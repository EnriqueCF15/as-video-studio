"""El director «escena entera» con la biblioteca de escenas (30-10), SOLO para el Estilo que
lleva `orden: "corta"` y `biblioteca` (el de historia). Los demas Estilos no pasan por aqui.

Es el metodo con el que se aprobaron S003 y S006 del video 2 (herramientas de
E:\\AutomatizacionYT\\herramientas: director_escena_entera, revisar_escenas,
piloto_escena_entera). Para cada plano el director escribe la ESCENA ENTERA (tipo, referencia
de estilo de la biblioteca, hojas, letras, reglas y escena), imitando los ejemplos que eligio
Enrique. La leccion del 29-10: Nano Banana copia gente, sitio y LUZ de la referencia, asi que
la referencia es UNA, de la misma clase de plano y la misma luz.

La ficha viaja como la `direccion` del plano, en una linea que Enrique puede leer y tocar:
  [[tipo=calle|ref=S141|hojas=muneco,ninos_puerta]] LETRAS: ... REGLAS: ... ESCENA: ...
  POR QUE: ...
y p6_assets, si la reconoce, dibuja con la orden del metodo a mano (`orden`) y solo esa
referencia mas las hojas (`referencias`).

Modelo por defecto: Sonnet high («Sonnet con revision», eleccion de Enrique del 30-10 tras
8 planos dibujados); Opus xhigh se elige en el ajuste de la fase.
"""
import json
import os
import re

import medios
from escena_entera_textos import INSTRUCCION, LLENO, ORDEN, REGLAS_PROTA, revisar

POR_DEFECTO = {"modelo": "sonnet", "esfuerzo": "high"}
VECINOS = 2
_LINEA = re.compile(r"^\[\[tipo=(?P<tipo>[\w-]*)\|ref=(?P<ref>[\w-]*)\|hojas=(?P<hojas>[\w,-]*)\]\]"
                    r"\s*LETRAS:(?P<letras>.*?)\s*REGLAS:(?P<reglas>.*?)\s*ESCENA:(?P<escena>.*?)"
                    r"(?:\s*POR QUE:(?P<por_que>.*))?$", re.S)


def carpeta(estilo):
    """La carpeta de la biblioteca de este Estilo, o None."""
    if not isinstance(estilo, dict) or str(estilo.get("orden") or "") != "corta":
        return None
    nombre = str(estilo.get("biblioteca") or "").strip()
    if not nombre:
        return None
    ruta = nombre if os.path.isabs(nombre) else os.path.join(medios.BANCO, nombre)
    return ruta if os.path.isfile(os.path.join(ruta, "biblioteca.json")) else None


def activa(estilo):
    """Si este Estilo dirige con la escena entera. -> bool"""
    return carpeta(estilo) is not None


def cargar(estilo):
    """-> {"ejemplos": {sid: {contexto, ficha}}, "referencias": {sid: {descripcion, imagen}}}"""
    ruta = carpeta(estilo)
    if not ruta:
        return {"ejemplos": {}, "referencias": {}}
    with open(os.path.join(ruta, "biblioteca.json"), encoding="utf-8") as fh:
        return json.load(fh)


def frases_alrededor(escenas, indice):
    """Las frases de antes y de despues, marcadas, y la del plano en medio."""
    lineas = []
    for j in range(max(0, indice - VECINOS), min(len(escenas), indice + VECINOS + 1)):
        e = escenas[j]
        marca = "    SE DICE AQUI" if j == indice else ("    antes" if j < indice else "    despues")
        lineas.append(f"{marca} [{e['id']}]: {str(e.get('narracion') or '').strip()}")
    return "\n".join(lineas)


def cartela_encima(escena):
    ficha = escena.get("cartela")
    if not isinstance(ficha, dict) or ficha.get("fondo") != "imagen":
        return ""
    datos = ficha.get("datos") if isinstance(ficha.get("datos"), dict) else {}
    return " · ".join(str(v).strip() for k, v in datos.items()
                      if k != "icono" and isinstance(v, (str, int, float)) and str(v).strip())


def _pista_sitio(escena, catalogo):
    sitio = escena.get("set") or ""
    sets = (catalogo or {}).get("sets") or {}
    return sitio, str((sets.get(sitio) or {}).get("descripcion") or "").strip()


def encargo(escenas, ids, catalogo, titulo, biblioteca):
    """El encargo entero para los planos `ids` (todos de una vez). -> str"""
    pos = {e["id"]: i for i, e in enumerate(escenas)}
    ejemplos = [f"[EJEMPLO de otro video, {sid}]\n{ej.get('contexto', '')}\nSALIDA: "
                + json.dumps({sid: ej.get("ficha") or {}}, ensure_ascii=False, indent=1)
                for sid, ej in (biblioteca.get("ejemplos") or {}).items()]
    reparto = (catalogo or {}).get("reparto") or {}
    lineas_reparto = [f"  [{quien}] {f.get('nombre', '')} ({f.get('papel', '')}): "
                      f"{str(f.get('descripcion') or '').strip()}"
                      for quien, f in reparto.items() if isinstance(f, dict)]
    planos = []
    for sid in ids:
        e = escenas[pos[sid]]
        sitio, pista = _pista_sitio(e, catalogo)
        cabeza = (f"[{sid}] sitio del estudio (pista): {sitio or 'ninguno'}"
                  + (f" = {pista}" if pista else "")
                  + f" · reparto del estudio (pista): "
                    f"{', '.join(e.get('personajes') or []) or 'nadie'}")
        encima = cartela_encima(e)
        if encima:
            cabeza += f" · CARTELA ENCIMA (no la repitas en el dibujo): «{encima}»"
        planos.append(cabeza + "\n" + frases_alrededor(escenas, pos[sid]))
    refs = biblioteca.get("referencias") or {}
    return INSTRUCCION.format(
        titulo=titulo or "sin titulo", prota=REGLAS_PROTA,
        estilos="\n".join(f"  · {k}: {v.get('descripcion', '')}" for k, v in refs.items()),
        ejemplos="\n\n".join(ejemplos), n_ejemplos=len(ejemplos),
        reparto="\n".join(lineas_reparto), cuantos=len(ids), planos="\n\n".join(planos))


def linea(ficha):
    """La ficha del director como la `direccion` de un plano. -> str"""
    hojas = ",".join(str(h).strip() for h in ficha.get("hojas") or [] if str(h).strip())
    texto = (f"[[tipo={ficha.get('tipo') or ''}|ref={ficha.get('estilo') or ''}|hojas={hojas}]] "
             f"LETRAS: {ficha.get('letras') or ''} REGLAS: {ficha.get('reglas') or ''} "
             f"ESCENA: {ficha.get('escena') or ''}")
    if ficha.get("por_que"):
        texto += f" POR QUE: {ficha['por_que']}"
    return " ".join(texto.split())


def ficha(direccion):
    """La ficha de una `direccion` de escena entera (texto o escena), o None."""
    if isinstance(direccion, dict):
        direccion = direccion.get("direccion")
    casa = _LINEA.match(" ".join(str(direccion or "").split()))
    if not casa:
        return None
    return {"tipo": casa["tipo"], "estilo": casa["ref"],
            "hojas": [h for h in casa["hojas"].split(",") if h],
            "letras": casa["letras"].strip(), "reglas": casa["reglas"].strip(),
            "escena": casa["escena"].strip(), "por_que": (casa["por_que"] or "").strip()}


def orden(ficha_, feedback=""):
    """La orden al generador, montada EXACTAMENTE como la del metodo a mano. -> str"""
    hojas = ("The other reference images are character sheets: draw those people exactly as "
             "they appear there. ") if ficha_.get("hojas") else ""
    letras = str(ficha_.get("letras") or "").strip().rstrip(".")
    texto = (f"The only lettering allowed is {letras}." if letras
             else "No text, letters or numbers anywhere.")
    salida = ORDEN.format(hojas=hojas, texto=texto, escena=ficha_.get("escena", ""),
                          reglas=ficha_.get("reglas", ""), lleno=LLENO)
    if feedback:
        salida += (" Correction requested for this picture, apply it: "
                   + " ".join(str(feedback).split()))
    return salida


def referencias(ficha_, estilo, dir_reparto, normalizar):
    """La referencia de estilo elegida + las hojas, en el orden de la ficha. -> [ref]"""
    salida = []
    ruta = carpeta(estilo)
    ref = ((cargar(estilo).get("referencias") or {}).get(ficha_.get("estilo")) or {})
    imagen = os.path.join(ruta, ref.get("imagen") or "") if ruta and ref else ""
    if imagen and os.path.isfile(imagen):
        salida.append({"papel": "estilo", "ruta": normalizar(imagen)})
    for quien in ficha_.get("hojas") or []:
        hoja = os.path.join(dir_reparto, f"{quien}.png")
        if os.path.exists(hoja):
            salida.append({"papel": "reparto", "nombre": quien, "ruta": normalizar(hoja)})
    return salida


def leer_respuesta(crudo, ids, biblioteca, reparto):
    """Valida lo que devuelve el director. -> ({sid: ficha}, avisos)"""
    fichas, avisos = {}, []
    planos = crudo.get("planos") if isinstance(crudo, dict) else None
    if not isinstance(planos, dict):
        return fichas, ["la respuesta no trae un objeto de planos"]
    refs = biblioteca.get("referencias") or {}
    for sid, f in planos.items():
        sid = str(sid).strip().upper()
        if sid not in ids:
            continue
        if not isinstance(f, dict) or not str(f.get("escena") or "").strip():
            avisos.append(f"{sid}: sin escena")
            continue
        if f.get("estilo") not in refs:
            avisos.append(f"{sid}: referencia «{f.get('estilo')}» fuera de la biblioteca")
        f["hojas"] = [h for h in (f.get("hojas") or []) if not reparto or h in reparto]
        fichas[sid] = f
    faltan = [sid for sid in ids if sid not in fichas]
    if faltan:
        avisos.append(f"{len(faltan)} plano(s) se han quedado sin direccion: "
                      + ", ".join(faltan[:8]))
    return fichas, avisos


#: El repaso de sentido (30-10, eleccion de Enrique): la comprobacion de arriba no entiende
#: la frase. Opus LEE las escenas de Sonnet y solo marca las que no la cuentan.
REPASO_POR_DEFECTO = {"modelo": "opus", "esfuerzo": "high"}
REPASO = """Eres el revisor de un video de historia animado titulado «{titulo}». Otro director
ya escribio la escena que se dibujara en cada plano. Tu trabajo es SOLO avisar de las que no
cuentan lo que dice su frase, antes de pagar las imagenes. No reescribas nada.

Marca un plano SOLO si pasa alguna de estas cosas:
- La escena no ensena lo que dice la frase marcada «SE DICE AQUI»: otra persona, otra accion,
  otro sitio u otra epoca; o el sentido al reves (p. ej. la frase dice que hoy los padres
  temen a los adultos de la puerta y la escena asusta al que da los dulces).
- La frase da un giro («but», «and yet», «the fear flipped»...) y la escena se queda con lo de
  antes del giro.
- Se inventa algo que la frase no nombra y que le cambia el sentido (un quiosco, un cartel,
  bocadillos vacios, objetos sueltos).
- Algo no tiene sentido fisico: flota, esta sobre la raya de una pantalla dividida, sale
  repetido, una casa rara.
Si la escena cuenta la frase aunque no sea como tu la harias, NO la marques.

Para cada marcado, una frase corta en castellano sencillo que diga que falla y como
arreglarlo («S132: hoy los que tienen miedo son los padres de la acera, no el protagonista»).

LOS PLANOS:

{planos}

DEVUELVE ESTE JSON (vacio si todo esta bien):

{{"marcados": {{"<id>": "<que falla y como arreglarlo>"}}}}
"""


def encargo_repaso(fichas, escenas, titulo):
    """El encargo del repaso de sentido: frase, vecinas y escena de cada plano. -> str"""
    pos = {e["id"]: i for i, e in enumerate(escenas)}
    planos = [f"[{sid}]\n{frases_alrededor(escenas, pos[sid])}\n    ESCENA: "
              f"{f.get('escena', '')}\n    LETRAS: {f.get('letras') or 'ninguna'}"
              for sid, f in fichas.items() if sid in pos]
    return REPASO.format(titulo=titulo or "sin titulo", planos="\n\n".join(planos))


def leer_repaso(crudo, ids):
    """-> {sid: motivo} de los planos que el repaso marca."""
    marcados = crudo.get("marcados") if isinstance(crudo, dict) else None
    return {str(sid).strip().upper(): " ".join(str(m).split())
            for sid, m in (marcados or {}).items()
            if str(sid).strip().upper() in ids and str(m).strip()}


def comprobar(fichas, escenas, catalogo, biblioteca):
    """La comprobacion sin Claude de cada escena, ANTES de pagar imagenes. -> [aviso]"""
    por_id = {e["id"]: e for e in escenas}
    reparto = (catalogo or {}).get("reparto") or {}
    refs = biblioteca.get("referencias") or {}
    avisos = []
    for sid, f in fichas.items():
        e = por_id.get(sid) or {}
        _, pista = _pista_sitio(e, catalogo)
        for aviso in revisar(f, str(e.get("narracion") or ""), cartela_encima(e),
                             reparto or None, "", estilos=refs, pista=pista):
            avisos.append(f"{sid}: {aviso}")
    return avisos
