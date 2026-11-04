"""El MAPA MUSICAL: que animo de musica va en cada momento, por LO QUE SE CUENTA.

(fork, 19-10-2026) La banda sonora se decidia solo por el ritmo del montaje
(`sonido.arco_del_video`): seis trozos iguales y un animo por cuantos cortes
tiene cada uno. No sabia que a las 3:34 se cuenta una anecdota graciosa ni que
el arranque tiene que enganchar. Esto le pasa a Claude la narracion plano a
plano, con su minuto, y le pide secciones: desde que plano, con que animo (solo
de los que hay canciones en la carpeta) y por que.

UNA LLAMADA POR VIDEO, sin herramientas (es leer texto y decidir), y el
resultado se guarda en los params del render (`mapa_musical`): volver a montar
la banda no vuelve a llamar a nadie. Las secciones van ancladas a PLANOS y no a
segundos, para que sigan en su sitio si la voz cambia de duracion.

Si Claude no contesta o contesta algo que no vale, quien llama usa los tonos de
la direccion (`sonido.arco_del_guion` sin mapa): nunca se queda sin musica.
"""
import time

try:
    from . import cli_claude, comun, sonido
except ImportError:  # ejecutado con la carpeta pasos directamente en sys.path
    import cli_claude
    import comun
    import sonido

FASE = "mapa_musical"
MODELO_POR_DEFECTO = cli_claude.por_defecto_de(FASE)["modelo"]
ESFUERZO_POR_DEFECTO = cli_claude.por_defecto_de(FASE)["esfuerzo"]
TIEMPO_BASE_S = 300

#: Lo que quiere decir cada animo, para que Claude elija con el mismo
#: vocabulario que la ficha de cada cancion (web/app.js, ANIMOS_EXPLICADOS).
ANIMOS_EXPLICADOS = {
    "sobrio": "tranquilo, de fondo",
    "tension": "con pulso, algo de urgencia",
    "misterioso": "intriga, ambiente, curiosidad",
    "melancolico": "reflexivo, suave",
    "epico": "crece y remata",
    "esperanzador": "optimista, luminoso",
    "corporativo": "limpio, de empresa",
    "oscuro": "grave, sombrio",
    "divertido": "gracioso, de comedia",
}

SISTEMA = ("Eres el musico de un video documental narrado. Decides que animo de "
           "musica acompana cada momento segun lo que cuenta la narracion. "
           "Responde exclusivamente con el objeto JSON pedido, sin texto alrededor "
           "y sin vallas de markdown.")

INSTRUCCION = """Decide la musica del video «{titulo}» ({minutos} minutos).

LA NARRACION, plano a plano (id del plano, minuto en que empieza, lo que se dice):
{narracion}

LOS ANIMOS QUE PUEDES USAR (solo estos: son los que tienen canciones):
{animos}

COMO SE DECIDE:
1. Parte el video en secciones por LO QUE SE CUENTA (una epoca, un caso, una
   tension que crece, una revelacion, el cierre). Cada seccion empieza en un plano.
2. La PRIMERA seccion es el gancho: tiene que despertar curiosidad desde el
   primer segundo.
3. Cada seccion dura al menos 60 segundos y no mas de unos 4 minutos: entre
   {pocas} y {muchas} secciones en total. Un cambio de musica cada poco suena a
   trozos sueltos.
{divertido}5. La ultima seccion cierra el video.
6. No repitas el mismo animo en mas de dos secciones seguidas salvo que el texto
   lo pida de verdad.

Responde SOLO con este JSON (el "porque" en espanol, una frase corta que diga
que se cuenta ahi):
{{"secciones": [{{"desde_plano": "S001", "animo": "misterioso", "porque": "..."}}]}}"""

DIVERTIDO = """4. «divertido» SOLO para momentos de verdad graciosos o curiosos (una anecdota,
   un blooper, una excusa absurda): de 10 a 45 segundos, como mucho {n} en todo
   el video. Despues vuelve el animo que habia. Si no hay ninguno, no lo uses.
"""
SIN_DIVERTIDO = "4. (No hay musica divertida en la biblioteca: no la uses.)\n"


def _mmss(segundos):
    segundos = max(0, int(segundos))
    return f"{segundos // 60}:{segundos % 60:02d}"


def instruccion(escenas, duracion_s, hay, titulo=""):
    """El texto que se le pasa a Claude. Aparte para poder probarlo sin el CLI."""
    escenas = [e for e in escenas if e.get("t_in") is not None]
    origen = float(escenas[0]["t_in"]) if escenas else 0.0
    total = float(duracion_s or 0) or (
        float(escenas[-1].get("t_out") or 0) - origen if escenas else 0.0)
    lineas = [f"{e['id']} {_mmss(float(e['t_in']) - origen)} "
              + " ".join(str(e.get("narracion") or "").split())
              for e in escenas]
    validos = [a for a in sorted(hay) if a in sonido.ANIMOS]
    animos = "\n".join(f"- {a}: {ANIMOS_EXPLICADOS.get(a, a)}" for a in validos)
    pocas = max(2, round(total / 240))
    muchas = max(pocas + 1, round(total / 90))
    divertido = (DIVERTIDO.format(n=max(1, round(total / 400)))
                 if "divertido" in validos else SIN_DIVERTIDO)
    return INSTRUCCION.format(titulo=titulo or "sin titulo",
                              minutos=max(1, round(total / 60)),
                              narracion="\n".join(lineas), animos=animos,
                              pocas=pocas, muchas=muchas, divertido=divertido)


def leer_mapa(datos, escenas, hay=None):
    """La respuesta de Claude, validada. -> {"secciones": [...], "avisos": [...]}

    Lanza ValueError si no trae ninguna seccion usable. Lo que no vale se quita
    con su aviso (un plano que no existe, un animo sin canciones) y la seccion
    de antes se alarga hasta la siguiente: mejor una seccion mas larga que un
    hueco. La primera empieza siempre en el primer plano.
    """
    if not isinstance(datos, dict) or not isinstance(datos.get("secciones"), list):
        raise ValueError("el mapa musical no trae una lista de 'secciones'")
    ids = [e["id"] for e in escenas if e.get("t_in") is not None]
    posicion = {sid: i for i, sid in enumerate(ids)}
    validas, avisos = [], []
    for seccion in datos["secciones"]:
        if not isinstance(seccion, dict):
            continue
        plano = str(seccion.get("desde_plano") or "").strip().upper()
        animo = str(seccion.get("animo") or "").strip().lower()
        if plano not in posicion:
            avisos.append(f"el plano {plano or '(vacío)'} no existe: se quita esa sección")
            continue
        if animo not in sonido.ANIMOS or (hay is not None and animo not in hay):
            avisos.append(f"{plano}: no hay canciones «{animo}»: sigue lo de antes")
            continue
        validas.append({"desde_plano": plano, "animo": animo,
                        "porque": " ".join(str(seccion.get("porque") or "").split())[:200]})
    if not validas:
        raise ValueError("el mapa musical no trae ninguna sección que se pueda usar")
    validas.sort(key=lambda s: posicion[s["desde_plano"]])
    unicas = []
    for seccion in validas:
        if unicas and unicas[-1]["desde_plano"] == seccion["desde_plano"]:
            continue
        unicas.append(seccion)
    unicas[0]["desde_plano"] = ids[0]
    return {"secciones": unicas, "avisos": avisos}


def proponer(escenas, duracion_s, hay, titulo="", ajuste=None, cwd=None, avisar=None):
    """Pide el mapa a Claude. -> mapa validado, o {"error": ...}. Nunca lanza."""
    avisar = avisar or (lambda *a, **k: None)
    ajuste = dict(ajuste or {})
    modelo = cli_claude.normalizar_modelo(ajuste.get("modelo"), defecto=MODELO_POR_DEFECTO)
    esfuerzo = cli_claude.normalizar_esfuerzo(ajuste.get("esfuerzo"),
                                              defecto=ESFUERZO_POR_DEFECTO)
    arranque = time.time()
    avisar(None, "Claude está leyendo la narración para decidir la música")
    try:
        texto, _sobre = cli_claude.ejecutar(
            instruccion(escenas, duracion_s, hay, titulo), modelo=modelo,
            esfuerzo=esfuerzo, cwd=cwd, tiempo_max_s=0, base_tiempo_s=TIEMPO_BASE_S,
            sistema=SISTEMA, extra=["--no-session-persistence"],
            para="el mapa musical")
        mapa = leer_mapa(comun.extraer_json(texto, "el mapa musical"), escenas, hay)
    except Exception as fallo:                                   # noqa: BLE001
        return {"error": f"{type(fallo).__name__}: {fallo}"[:300],
                "segundos": round(time.time() - arranque, 1)}
    mapa.update({"fuente": "claude", "modelo": modelo, "esfuerzo": esfuerzo,
                 "segundos": round(time.time() - arranque, 1),
                 "fecha": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return mapa
