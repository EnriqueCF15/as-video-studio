"""El RITMO DE CAMARA de un video: que planos se mueven y cuales no (fork, 26-10-2026).

QUE PROBLEMA RESUELVE
---------------------
Hasta el 26-10 todos los planos se movian: el plan alterna un zoom del 5 %
plano a plano (`segmentar.alternar_zoom`) y el motor lo estira hasta un 12 % con
paneo en todo plano de mas de 5 s (`movimiento.recorrido`). En el video de
historia eso fueron 174 de 218 planos con zoom y paneo, y dos cosas que Enrique
vio al publicarlo:

  - comparado con los canales que funcionan, sobra movimiento: ellos pasan de
    una imagen a otra y solo de vez en cuando hacen un zoom;
  - con el zoom entrado, el plano no ensena entero lo que tenia que ensenar (un
    grafico, un texto dibujado, la cara del protagonista).

Aqui se decide, plano a plano y sin gastar nada (ni Claude ni Google), con lo
que el plan ya sabe de cada uno: en que parte del guion cae, su encuadre, su
tono y lo que dura.

LOS TRES MODOS (param `camara` del paso callouts)
-------------------------------------------------
  variada   (por defecto) quieto casi siempre; se mueve donde lo pide lo que se
            cuenta. Las reglas, en `decidir`.
  siempre   como antes del 26-10: todo plano con su zoom y su paneo.
  quieta    ningun plano se mueve.

Y LOS CORTES SECOS. El corte seco (una imagen y la siguiente, sin fundido) se
retiro el 20-08 en el estudio original. Vuelve SOLO donde lo marca este modulo:
todo el gancho, y entre dos planos quietos del mismo sitio. Lo demas sigue con
sus transiciones de siempre.
"""

MODOS = ("variada", "siempre", "quieta")
POR_DEFECTO = "variada"

#: Lo que se mueve un plano en modo variada, como mucho: el 8 % y no el 12 %,
#: para que ni en el punto mas cerrado se coma el plano.
ZOOM_MAXIMO_VARIADA = 0.08

#: Por debajo de esto no se mueve nada: no da tiempo a verlo.
PLANO_CORTO_S = 5.0
#: Un plano largo de tono neutro se mueve cuando ya van estos quietos seguidos,
#: para que un tramo largo sin nada no se quede muerto.
QUIETOS_SEGUIDOS = 3
PLANO_LARGO_S = 6.0
#: Nunca mas de dos planos seguidos moviendose.
MOVIDOS_SEGUIDOS = 2
#: El gancho, como mucho: si el guion no marca secciones o la primera es muy
#: larga, el gancho son estos segundos.
GANCHO_MAXIMO_S = 60.0
GANCHO_SIN_SECCIONES_S = 30.0

#: Encuadres que ENSENAN algo que hay que ver entero.
CARTAS_INFORMATIVAS = {"diagrama", "pantalla"}
#: Primeros planos: ya van cerrados, un zoom encima los corta.
CARTAS_CERRADAS = {"detalle", "retrato"}
#: Planos abiertos: donde un paneo lento si luce.
CARTAS_ABIERTAS = {"general", "aereo", "eterea"}

#: Hacia donde va el zoom segun el tono: la tension empuja hacia dentro, lo
#: solemne y lo triste se alejan.
DIRECCION_POR_TONO = {"tenso": "in", "solemne": "out", "triste": "out"}


def modo_de(params):
    """El modo pedido, o el de por defecto si no se entiende. -> str"""
    modo = str((params or {}).get("camara") or POR_DEFECTO).strip().lower()
    return modo if modo in MODOS else POR_DEFECTO


def _duracion(escena):
    try:
        return float(escena.get("duracion") or
                     float(escena.get("t_out") or 0) - float(escena.get("t_in") or 0))
    except (TypeError, ValueError):
        return 0.0


def _bloque(escena):
    return ((escena.get("origen") or {}).get("bloque")) or None


def _sigue_a(escena):
    cartela = escena.get("cartela")
    return escena.get("sigue_a") or (
        cartela.get("sigue_a") if isinstance(cartela, dict) else None)


def ids_del_gancho(escenas, secciones=None):
    """Los planos del gancho: los de la primera seccion del guion. -> set

    `secciones` son los bloques del guion que abren seccion (`abre_seccion`). El
    gancho acaba donde empieza la SEGUNDA. Sin secciones, o si la primera dura
    mas de GANCHO_MAXIMO_S, el gancho se corta por tiempo.
    """
    abren = set(secciones or [])
    tope = GANCHO_MAXIMO_S if len(abren) > 1 else GANCHO_SIN_SECCIONES_S
    gancho = set()
    escenas = list(escenas or [])
    for indice, escena in enumerate(escenas):
        if indice and _abre_seccion(escenas, indice, abren) and not _sigue_a(escena):
            break
        if float(escena.get("t_in") or 0) >= tope:
            break
        gancho.add(escena.get("id"))
    return gancho


def _abre_seccion(escenas, indice, abren):
    """Si el plano es el primero de una seccion del guion."""
    bloque = _bloque(escenas[indice])
    if not bloque or bloque not in abren:
        return False
    return indice == 0 or _bloque(escenas[indice - 1]) != bloque


def decidir(escenas, modo=POR_DEFECTO, secciones=None):
    """Que hace la camara en cada plano. -> {id: decision}

    Cada decision es {"mov": quieto|zoom|paneo|plan, "dir": in|out|None,
    "seco": bool, "por_que": str}. «plan» es el movimiento de siempre (modo
    siempre). Determinista: el mismo plan da siempre lo mismo, y rehacer un
    plano suelto no le cambia la camara a otro.
    """
    escenas = list(escenas or [])
    modo = modo if modo in MODOS else POR_DEFECTO
    if modo == "siempre":
        return {e.get("id"): {"mov": "plan", "dir": None, "seco": False,
                              "por_que": "modo siempre: como antes"}
                for e in escenas}
    abren = set(secciones or [])
    gancho = ids_del_gancho(escenas, secciones)
    salida = {}
    quietos, movidos, ultima_dir = 0, 0, None
    for indice, escena in enumerate(escenas):
        sid = escena.get("id")
        padre = _sigue_a(escena)
        if padre and padre in salida:
            # la segunda mitad de un plano estirado hace lo mismo que la primera
            salida[sid] = dict(salida[padre], seco=False, por_que="sigue al anterior")
            continue
        mov, direccion, por_que = _movimiento(escena, indice, escenas, modo,
                                              gancho, abren, quietos, movidos)
        if mov != "quieto":
            direccion = (direccion or DIRECCION_POR_TONO.get(escena.get("tono"))
                         or ((escena.get("zoom") or {}).get("tipo")) or "in")
            if movidos and direccion == ultima_dir:
                # dos zooms seguidos hacia el mismo lado se leen como uno solo
                mov, direccion, por_que = "quieto", None, "el anterior ya iba hacia ese lado"
        if mov == "quieto":
            quietos, movidos, ultima_dir = quietos + 1, 0, None
        else:
            quietos, movidos, ultima_dir = 0, movidos + 1, direccion
        salida[sid] = {"mov": mov, "dir": direccion if mov != "quieto" else None,
                       "seco": _corte_seco(escenas, indice, salida, mov, gancho),
                       "por_que": por_que}
    return salida


def _movimiento(escena, indice, escenas, modo, gancho, abren, quietos, movidos):
    """El movimiento de UN plano en modo variada o quieta. -> (mov, dir, por_que)"""
    if modo == "quieta":
        return "quieto", None, "modo quieta"
    sid = escena.get("id")
    duracion = _duracion(escena)
    carta = escena.get("carta")
    if sid in gancho:
        return "quieto", None, "gancho: plano a plano"
    if escena.get("cartela"):
        return "quieto", None, "cartela: el texto se lee entero"
    if carta in CARTAS_INFORMATIVAS or escena.get("abstracta"):
        return "quieto", None, "ensena algo que se tiene que ver entero"
    if carta in CARTAS_CERRADAS:
        return "quieto", None, "primer plano: un zoom lo cortaria"
    if duracion < PLANO_CORTO_S:
        return "quieto", None, "plano corto"
    if movidos >= MOVIDOS_SEGUIDOS:
        return "quieto", None, "ya van dos moviendose"
    if _abre_seccion(escenas, indice, abren):
        return "paneo", "out", "abre una parte del guion"
    if carta in CARTAS_ABIERTAS:
        return "paneo", None, "plano abierto: paneo lento"
    if escena.get("tono") in DIRECCION_POR_TONO and duracion >= PLANO_LARGO_S:
        return "zoom", None, f"tono {escena.get('tono')}"
    if quietos >= QUIETOS_SEGUIDOS and duracion >= PLANO_LARGO_S:
        return "zoom", None, "plano largo tras varios quietos"
    return "quieto", None, "sin motivo para moverse"


def _corte_seco(escenas, indice, salida, mov, gancho):
    """Si este plano entra con corte seco, sin transicion."""
    if indice == 0:
        return False
    escena, anterior = escenas[indice], escenas[indice - 1]
    if escena.get("id") in gancho:
        return True
    previa = salida.get(anterior.get("id")) or {}
    return (mov == "quieto" and previa.get("mov") == "quieto"
            and escena.get("transicion") != "acento"
            and bool(escena.get("set")) and escena.get("set") == anterior.get("set"))


#: El extremo cerrado del zoom si el plan no trae ninguno (`segmentar.ESCALA_CERRADA`).
ESCALA_CERRADA = round(1.0 / 0.95, 4)


def escena_para_el_motor(escena, decision):
    """La escena con el zoom que pide su decision. -> (escena, ajustes del motor)

    No toca el plan: devuelve una copia. Los ajustes son los argumentos de
    `movimiento.calcular` (cuanto zoom como mucho y si hay paneo).
    """
    decision = decision or {}
    mov = decision.get("mov") or "plan"
    if mov == "plan":
        return escena, {}
    zoom = dict(escena.get("zoom") or {})
    copia = dict(escena)
    if mov == "quieto":
        copia["zoom"] = dict(zoom, tipo="quieto", de=1.0, a=1.0)
        return copia, {}
    cerrada = max(float(zoom.get("de") or 1.0), float(zoom.get("a") or 1.0))
    cerrada = cerrada if cerrada > 1.0 else ESCALA_CERRADA
    if decision.get("dir") == "out":
        copia["zoom"] = dict(zoom, tipo="out", de=cerrada, a=1.0)
    else:
        copia["zoom"] = dict(zoom, tipo="in", de=1.0, a=cerrada)
    return copia, {"zoom_maximo": ZOOM_MAXIMO_VARIADA, "paneo": mov == "paneo"}


def resumen(decisiones):
    """Cuantos planos de cada clase. -> str para los avisos del paso"""
    total = len(decisiones or {})
    if not total:
        return ""
    cuenta = {}
    for d in decisiones.values():
        cuenta[d["mov"]] = cuenta.get(d["mov"], 0) + 1
    secos = sum(1 for d in decisiones.values() if d.get("seco"))
    partes = [f"{cuenta[k]} {k}" for k in ("quieto", "zoom", "paneo", "plan") if cuenta.get(k)]
    return f"camara: {', '.join(partes)} de {total} planos; {secos} cortes secos"
