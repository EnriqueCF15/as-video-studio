"""EL REVISOR DE UN PLANO: mira la imagen YA DIBUJADA antes de darla por buena.

POR QUE EXISTE
--------------
El corrector (`corrector.py`) trabaja ANTES de dibujar: lee la nota, elige las
hojas y escribe la escena. Pero nadie miraba lo que salia. Medido el 12-10-2026
en el repaso del video de historia: la imagen de «YOU / HIM» se dibujo cinco
veces con 8 casillas donde la nota pedia 10, los arcos del sol salieron con
numeros romanos repetidos y varias figuras con nariz y orejas que la guia del
estilo prohibe. Cada vuelta la descubria una persona mirando una hoja de
contactos, y cada vuelta era otra imagen pagada.

QUE HACE
--------
Un agente del CLI (`cli_claude`, el mismo motor que el corrector) abre con
`Read` el PNG recien generado --y, si es un retoque, la imagen de antes-- y lo
compara con la nota y con la guia escrita del estilo. Devuelve:

  - `ok`        si la imagen se puede dar por buena
  - `fallos`    lo que esta mal, en castellano y en una linea cada uno (es lo
                que se ensena a quien revisa)
  - `arreglo`   la instruccion en ingles para el generador, para UN reintento

Quien llama (`p6_assets._revisar_y_reintentar`) rehace la imagen UNA vez con el
arreglo al final del prompt y la vuelve a revisar. Si sigue mal, la imagen se
queda (ya esta pagada) y el fallo se dice en el resumen de la tanda: no se
reintenta en bucle, que es justo el gasto que esto existe para evitar.

LO QUE NO HACE
--------------
No genera ni toca el plan. No es un juez de gusto: solo suspende por defectos
claros que se verian en el video (lo que la nota pedia y no esta, textos mal
escritos, cuentas que no cuadran, lo que la guia del estilo prohibe, objetos de
otra epoca). Un revisor quisquilloso suspenderia todo y doblaria el gasto.

Y `claude_contesta` es la comprobacion de ANTES de empezar: si ni el corrector
ni el revisor pueden hablar con Claude, la tanda no dibuja nada (ver
`p6_assets._comprobar_claude`).
"""
import os
import time

try:
    from . import cli_claude, comun, salud_cli
except ImportError:  # ejecutado con la carpeta pasos directamente en sys.path
    import cli_claude
    import comun
    import salud_cli

FASE = "revisor_imagen"
MODELO_POR_DEFECTO = cli_claude.por_defecto_de(FASE)["modelo"]
ESFUERZO_POR_DEFECTO = cli_claude.por_defecto_de(FASE)["esfuerzo"]
TIEMPO_BASE_S = 240

#: Cuantos fallos se guardan y se ensenan por imagen: con mas de cinco, la
#: imagen esta mal entera y la lista solo alarga el resumen.
MAX_FALLOS = 5

HERRAMIENTAS_VETADAS = ("Bash", "Write", "Edit", "NotebookEdit", "Grep", "Glob",
                        "WebFetch", "WebSearch", "Task", "TodoWrite")
HERRAMIENTAS_PERMITIDAS = ("Read",)

SISTEMA = ("Eres el revisor de arte de un video de animacion narrada. Abre con "
           "Read la imagen que se te pide revisar antes de juzgar nada. Responde "
           "despues exclusivamente con el objeto JSON pedido, sin texto "
           "alrededor y sin vallas de markdown.")

INSTRUCCION = """Revisa la imagen recien dibujada del plano {sid} de «{titulo}».

LA IMAGEN A REVISAR (abrela con Read):
  {imagen}
{antes}
LA NOTA QUE SE PIDIO CUMPLIR (manda sobre la descripcion):
<<<
{nota}
>>>

LO QUE DICE LA VOZ EN ESE PLANO: {narracion}
PERSONAJES DEL REPARTO EN EL PLANO: {personajes}

LA GUIA ESCRITA DEL ESTILO (lo que prohibe, esta prohibido):
{estilo}
{especie}

QUE TIENES QUE COMPROBAR, por este orden:
1. La nota: esta hecho lo que pide? Si pide textos o rotulos, que esten y
   escritos EXACTAMENTE asi. Si pide cantidades (casillas, numeros, marcas,
   personas), cuentalas una a una: tienen que cuadrar.
2. La guia del estilo: lo que prohibe para caras, piel, pelo, rizo, manos y
   lineas. Ojo al rizo en espiral: solo lo lleva el protagonista y nunca mas
   de uno.
3. Textos: ningun texto que la nota no pida, ninguna palabra mal escrita ni
   letras inventadas sin sentido.
4. Epoca: nada moderno en una escena historica (relojes, lapices, ropa o
   muebles actuales...) salvo que la nota lo pida.
5. Fisica: nadie flota en el aire, nadie con miembros de mas.

SE ESTRICTO SOLO CON LO QUE SE VERIA EN EL VIDEO. Suspende unicamente por
defectos claros que un espectador notaria a tamano normal. No suspendas por
variaciones pequenas de trazo, de fondo o de composicion que la nota no pide.

QUE TIENES QUE DEVOLVER
Un objeto JSON con estas claves:
  "ok"       true si la imagen se puede dar por buena; false si tiene algun
             defecto claro de la lista.
  "fallos"   lista de frases cortas EN CASTELLANO, una por defecto, diciendo
             que esta mal y donde («la losa de la derecha tiene 8 casillas y
             la nota pide 10»). Vacia si "ok" es true.
  "arreglo"  UNA instruccion en INGLES para el generador de imagenes que
             corrija SOLO esos defectos y conserve todo lo demas («the stone
             slab must have exactly ten numbered boxes, 1 to 10; boxes 9 and
             10 red»). Vacia si "ok" es true.
"""


def _texto(valor, tope=1200):
    return " ".join(str(valor or "").split())[:tope]


def revisar(imagen, nota, escena, estilo_legible="", regla_especie="",
            titulo="", antes=None, cwd=None, ajuste=None, avisar=None):
    """Mira la imagen y dice si vale. -> {"ok", "fallos", "arreglo", ...} | {"error"}

    `imagen` es la ruta del PNG recien generado; `antes`, la de la imagen que se
    retocaba (si la hay), para poder comprobar que lo que la nota no toca sigue
    igual. Nunca lanza: un fallo del CLI vuelve como {"error": ...} y quien
    llama decide.
    """
    avisar = avisar or (lambda *a, **k: None)
    if not imagen or not os.path.exists(str(imagen)):
        return {"error": f"no existe la imagen a revisar: {imagen}"}
    ajuste = dict(ajuste or {})
    modelo = cli_claude.normalizar_modelo(ajuste.get("modelo"),
                                          defecto=MODELO_POR_DEFECTO)
    esfuerzo = cli_claude.normalizar_esfuerzo(ajuste.get("esfuerzo"),
                                              defecto=ESFUERZO_POR_DEFECTO)
    instruccion = INSTRUCCION.format(
        sid=escena.get("id"), titulo=_texto(titulo, 120) or "sin titulo",
        imagen=os.path.abspath(str(imagen)),
        antes=(f"LA IMAGEN DE ANTES DEL CAMBIO (abrela si la nota pide conservar "
               f"algo de ella):\n  {os.path.abspath(str(antes))}\n"
               if antes and os.path.exists(str(antes)) else ""),
        nota=_texto(nota, 2500) or "(sin nota: revisa solo estilo, textos, "
                                   "epoca y fisica)",
        narracion=_texto(escena.get("narracion"), 600) or "(sin narracion)",
        personajes=", ".join(escena.get("personajes") or []) or "(nadie del reparto)",
        estilo=estilo_legible or "  (sin guia escrita)",
        especie=(f"Regla de especie del estilo: {regla_especie}"
                 if regla_especie else ""))
    arranque = time.time()
    avisar(None, f"{escena.get('id')}: el revisor esta mirando la imagen")
    try:
        texto, _sobre = cli_claude.ejecutar(
            instruccion, modelo=modelo, esfuerzo=esfuerzo, cwd=cwd,
            tiempo_max_s=0, base_tiempo_s=TIEMPO_BASE_S, sistema=SISTEMA,
            herramientas_vetadas=HERRAMIENTAS_VETADAS,
            herramientas_permitidas=HERRAMIENTAS_PERMITIDAS,
            extra=["--no-session-persistence"],
            para=f"el revisor del plano {escena.get('id')}")
        datos = comun.extraer_json(texto, "la respuesta del revisor")
    except Exception as fallo:                                   # noqa: BLE001
        return {"error": f"{type(fallo).__name__}: {fallo}"[:300],
                "segundos": round(time.time() - arranque, 1)}
    return leer_veredicto(datos, modelo=modelo, esfuerzo=esfuerzo,
                          segundos=round(time.time() - arranque, 1))


def leer_veredicto(datos, modelo=None, esfuerzo=None, segundos=None):
    """La respuesta del agente, validada. -> dict con "ok" o con "error"

    Aparte de `revisar` para poder probarla sin el CLI. Un «ok» que no es un
    booleano o un suspenso sin decir por que no valen: un suspenso mudo no se
    puede ni reintentar ni ensenar.
    """
    base = {"modelo": modelo, "esfuerzo": esfuerzo, "segundos": segundos}
    if not isinstance(datos, dict) or not isinstance(datos.get("ok"), bool):
        return {**base, "error": "la respuesta del revisor no trae un 'ok' "
                                 "verdadero o falso"}
    fallos = [_texto(f, 300) for f in (datos.get("fallos") or [])
              if _texto(f, 300)][:MAX_FALLOS]
    arreglo = _texto(datos.get("arreglo"), 1500)
    if datos["ok"]:
        return {**base, "ok": True, "fallos": [], "arreglo": ""}
    if not fallos and not arreglo:
        return {**base, "error": "el revisor suspende la imagen sin decir por que"}
    return {**base, "ok": False, "fallos": fallos,
            "arreglo": arreglo or "Fix these defects: " + "; ".join(fallos)}


def claude_contesta(para="revisar las imagenes"):
    """¿Hay alguna cuenta del CLI que conteste ahora mismo? -> (bool, motivo)

    Una llamada minima por cuenta (`salud_cli.probar`: haiku, esfuerzo bajo),
    en el orden de la cadena, hasta que una conteste. Cuesta segundos y evita lo
    que paso el 12-10-2026: la sesion del CLI caducada, el corrector fallando en
    silencio en cada plano y las imagenes dibujandose igual, sin filtro.
    """
    if salud_cli.simulado():
        return True, "simulado"
    motivos = []
    for indice, cuenta in enumerate(cli_claude.cuentas()):
        ficha = salud_cli.probar(cuenta, para=para) or {}
        if ficha.get("estado") == "ok":
            return True, ""
        motivos.append(f"{cli_claude.nombre_de(cuenta, indice)}: "
                       f"{ficha.get('estado') or 'error'} "
                       f"{_texto(ficha.get('mensaje'), 160)}".strip())
    return False, "; ".join(motivos) or "no hay ninguna cuenta del CLI"
