"""
Prueba de la voz con Google (motores/voz_google), del alineador local
(motores/alinear_voz) y de su paso por p4_voz: tramos, estilos y regrabado.

NO SALE A LA RED NI CARGA TORCH. Google se sustituye por un doble que devuelve
tonos (con el volumen que se le pida, para probar la igualacion y los avisos) y
el alineador por uno que reparte las palabras a lo largo de la pista. Lo que se
prueba es todo lo que va ALREDEDOR de esas dos llamadas: el emparejamiento de
palabras, el troceado, la forma de la peticion, que cada seccion lleve el estilo
de su tramo y que regrabar respete el suyo.

    python pasos/prueba_voz_google.py
"""
import json
import math
import os
import shutil
import struct
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)

from nucleo.estado import Estado  # noqa: E402
from nucleo.proyecto import Proyecto  # noqa: E402

import comun  # noqa: E402
import marcas_tts  # noqa: E402
import p4_voz  # noqa: E402

FALLOS = []
HECHAS = [0]

alin = comun.cargar_motor("alinear_voz", "alinear.py")
goo = comun.cargar_motor("voz_google", "voz.py")

INTRO = ("Speak like you're revealing something most people have never been "
         "told. Intrigued, slightly urgent tone.")
CUERPO = "Speak like a friendly, smart neighbor explaining money tricks over coffee."
CIERRE = "Warm and grateful, slower, like saying goodbye to a friend."


def comprobar(condicion, texto):
    HECHAS[0] += 1
    if condicion:
        print(f"  ok   {texto}")
    else:
        print(f"  FALLO {texto}")
        FALLOS.append(texto)


def igual(obtenido, esperado, texto):
    comprobar(obtenido == esperado,
              texto if obtenido == esperado
              else f"{texto}  [obtenido={obtenido!r} esperado={esperado!r}]")


def falla(funcion, texto, tipo=Exception):
    HECHAS[0] += 1
    try:
        funcion()
    except tipo:
        print(f"  ok   {texto}")
        return
    except Exception as otro:                                   # noqa: BLE001
        print(f"  FALLO {texto}  [excepcion inesperada: {otro!r}]")
        FALLOS.append(texto)
        return
    print(f"  FALLO {texto}  [no fallo]")
    FALLOS.append(texto)


def tono(segundos, db=-20.0, frecuencia=220.0):
    """PCM s16le mono a goo.SR: un seno con el nivel RMS pedido."""
    amplitud = (10 ** (db / 20.0)) * math.sqrt(2)
    n = int(goo.SR * segundos)
    return b"".join(struct.pack("<h", int(32767 * amplitud *
                                          math.sin(2 * math.pi * frecuencia * i / goo.SR)))
                    for i in range(n))


# ------------------------------------------------------------- alineador

def prueba_emparejar():
    print("\n[1] alineador: emparejar el guion con lo oido")
    guion = alin.tokens_del_guion("Save money, every week. It works!")
    igual(guion, ["Save", "money,", "every", "week.", "It", "works!"],
          "los tokens del guion conservan la puntuacion")
    igual(alin.tokens_del_guion("First — then"), ["First—", "then"],
          "un token que es solo puntuacion se pega al anterior")

    oidas = [{"w": "save", "s": 0.0, "e": 0.3}, {"w": "money", "s": 0.3, "e": 0.7},
             {"w": "every", "s": 0.8, "e": 1.0}, {"w": "week", "s": 1.0, "e": 1.4},
             {"w": "it", "s": 1.8, "e": 1.9}, {"w": "works", "s": 1.9, "e": 2.3}]
    r = alin.emparejar(guion, oidas, 2.5)
    igual([p["w"] for p in r["palabras"]], guion,
          "las palabras que salen son las DEL GUION (segmentar mira su puntuacion)")
    igual(r["cobertura"], 1.0, "todo coincide: cobertura completa")
    igual((r["palabras"][1]["s"], r["palabras"][1]["e"]), (0.3, 0.7),
          "una palabra que coincide se queda el tiempo oido")

    # un numero: escrito con letras, oido en cifras
    guion = alin.tokens_del_guion("You save one thousand dollars a year.")
    oidas = [{"w": "you", "s": 0.0, "e": 0.2}, {"w": "save", "s": 0.2, "e": 0.5},
             {"w": "$1,000", "s": None, "e": None},
             {"w": "a", "s": 1.6, "e": 1.7}, {"w": "year", "s": 1.7, "e": 2.0}]
    r = alin.emparejar(guion, oidas, 2.2)
    palabras = {p["w"]: p for p in r["palabras"]}
    comprobar(0.5 <= palabras["one"]["s"] < palabras["thousand"]["s"]
              < palabras["dollars"]["s"] <= 1.6,
              "un numero oido en cifras reparte su hueco entre las palabras escritas")
    comprobar(r["cobertura"] == 1.0 and not r["faltan"],
              "y como lo oido son cifras, cuenta como dicho (no es una falsa alarma)")
    r = alin.emparejar(alin.tokens_del_guion("They bought a red car."),
                       [{"w": w, "s": i * 0.3, "e": i * 0.3 + 0.2} for i, w in
                        enumerate(["they", "bought", "a", "blue", "car"])], 2.0)
    igual(r["faltan"], ["red"], "pero una palabra CAMBIADA si cuenta como no oida")

    # cada palabra del guion, a la frase de Whisper donde se dijo
    frases = [{"start": 0.0, "end": 2.0, "text": "You save $1,000 a year."},
              {"start": 2.5, "end": 4.0, "text": "That is 10% more."}]
    guion = alin.tokens_del_guion("You save one thousand dollars a year. "
                                  "That is ten percent more.")
    reparto, cobertura = alin.frases_del_guion(guion, frases)
    igual(reparto, [["You", "save", "one", "thousand", "dollars", "a", "year."],
                    ["That", "is", "ten", "percent", "more."]],
          "las palabras del guion se reparten por frases (numeros con letras incluidos)")
    igual(cobertura["cobertura"], 1.0, "con cifras de por medio, cobertura completa")

    # el TTS se salta una palabra
    guion = alin.tokens_del_guion("This is a very simple trick.")
    oidas = [{"w": w, "s": i * 0.3, "e": i * 0.3 + 0.25}
             for i, w in enumerate(["this", "is", "a", "simple", "trick"])]
    r = alin.emparejar(guion, oidas, 2.0)
    marcas = r["palabras"]
    igual(len(marcas), 6, "una palabra no oida sigue teniendo marca")
    comprobar(marcas[2]["e"] <= marcas[3]["s"] <= marcas[4]["s"],
              "y se interpola entre sus vecinas")
    igual(r["faltan"], ["very"], "y se dice cual falta")
    comprobar(all(marcas[i]["e"] <= marcas[i + 1]["s"] + 1e-6
                  for i in range(len(marcas) - 1)),
              "las marcas van en orden y no se solapan")

    # el TTS se inventa palabras
    guion = alin.tokens_del_guion("Spend less.")
    oidas = [{"w": "spend", "s": 0.0, "e": 0.3}, {"w": "um", "s": 0.3, "e": 0.5},
             {"w": "less", "s": 0.5, "e": 0.8}]
    r = alin.emparejar(guion, oidas, 1.0)
    igual((r["cobertura"], r["sobran"]), (1.0, 1),
          "lo que se oye y no esta en el guion se cuenta aparte, no lo desplaza")


# ----------------------------------------------------------- motor google

def prueba_motor_google():
    print("\n[2] motor de Google: troceado, peticion y pausas")
    largo = " ".join(f"Sentence number {i} is about saving money." for i in range(200))
    trozos = goo.trocear_por_bytes(largo, 1000)
    comprobar(len(trozos) > 1, f"un texto largo se parte ({len(trozos)} trozos)")
    comprobar(all(len(t.encode("utf-8")) <= 1000 for t in trozos),
              "ningun trozo pasa del tope de bytes")
    comprobar(all(t.endswith(".") for t in trozos),
              "y todos acaban en final de frase")
    igual(" ".join(trozos), largo, "sin perder ni una palabra")
    igual(goo.trocear_por_bytes("Short one."), ["Short one."], "lo corto no se toca")

    cuerpo = goo.cuerpo_peticion("Hello there.", "Orus", "gemini-2.5-flash-tts",
                                 "en", estilo="  warm   and calm ")
    igual(cuerpo["voice"], {"languageCode": "en-US", "name": "Orus",
                            "modelName": "gemini-2.5-flash-tts"},
          "Gemini: la voz va por nombre y el modelo en modelName")
    igual(cuerpo["input"], {"text": "Hello there.", "prompt": "warm and calm"},
          "Gemini: el estilo va en prompt, sin espacios de mas")
    igual(cuerpo["audioConfig"]["sampleRateHertz"], 44100,
          "se pide a 44,1 kHz, que es lo que espera el resto de la toma")
    sin_estilo = goo.cuerpo_peticion("Hi.", "Orus", "gemini-2.5-flash-tts", "en")
    comprobar("prompt" not in sin_estilo["input"], "sin estilo no se manda prompt vacio")
    chirp = goo.cuerpo_peticion("Hi [pause] there.", "Orus", "chirp3-hd", "en",
                                estilo="ignored")
    igual(chirp["voice"], {"languageCode": "en-US", "name": "en-US-Chirp3-HD-Orus"},
          "Chirp 3 HD: la voz va dentro del nombre")
    igual(chirp["input"], {"markup": "Hi [pause] there."},
          "Chirp 3 HD: sin estilo y con las pausas en markup")
    falla(lambda: goo.cuerpo_peticion("x", "Orus", "sonic-3", "en"),
          "un modelo que no es de Google no se manda", ValueError)

    igual(marcas_tts.para_google('Wait.<break time="300ms"/>Now.<break time="900ms"/>'
                                 'Then.<break time="2s"/>End.'),
          "Wait. Now. Then. End.",
          "con Gemini no viaja ninguna etiqueta: con ellas sonaba sobreactuado")
    igual(marcas_tts.para_google('Wait.<break time="300ms"/>Now.<break time="900ms"/>'
                                 'Then.<break time="2s"/>End.', "chirp"),
          "Wait. [pause short] Now. [pause] Then. [pause long] End.",
          "con Chirp 3 HD las pausas pasan a sus etiquetas por su duracion")
    igual(marcas_tts.para_google('<speed ratio="0.9"/>Slow <spell>IRA</spell> now.'
                                 '<emotion value="calm"/>'),
          "Slow IRA now.", "lo que Google no entiende se quita, el texto se queda")


def prueba_igualar():
    print("\n[3] motor de Google: igualar volumen y avisar de la costura")
    original = goo.sintetizar
    niveles = iter([-20.0, -28.0, -27.5])

    def doble(texto, voz, modelo, idioma, estilo=None, locale=None, velocidad=None):
        pcm = goo.silencio(0.3) + tono(len(texto.split()) * 0.3, next(niveles)) + goo.silencio(0.4)
        return pcm, len(pcm) / (goo.SR * 2)

    goo.sintetizar = doble
    try:
        wav, segundos, info = goo.sintetizar_trozos(
            [{"texto": "One two three four.", "seccion": "SB001", "estilo": INTRO},
             {"texto": "Five six seven eight.", "seccion": "SB002", "estilo": CUERPO},
             {"texto": "Nine ten eleven twelve.", "seccion": "SB002", "estilo": CUERPO}],
            concurrencia=1)
    finally:
        goo.sintetizar = original
    piezas = info["piezas"]
    igual(len(piezas), 3, "una pieza por trozo")
    igual(info["peticiones"], 3, "y una peticion por pieza")
    comprobar(abs(piezas[1]["ganancia_db"] - 8.0) < 0.3,
              f"al trozo flojo se le sube lo que le falta ({piezas[1]['ganancia_db']} dB)")
    finales = [p["nivel_db"] + p["ganancia_db"] for p in piezas]
    comprobar(max(finales) - min(finales) < 0.5,
              f"tras igualar, todas suenan al mismo volumen ({[round(f, 1) for f in finales]})")
    comprobar(any(a["tipo"] == "volumen" and a["entre"] == [0, 1] for a in info["avisos"]),
              "un salto de 8 dB entre dos trozos se AVISA aunque se iguale")
    comprobar(not any(a["entre"] == [1, 2] and a["tipo"] == "volumen" for a in info["avisos"]),
              "medio dB no es un salto")
    hueco_secciones = piezas[1]["t_in"] - piezas[0]["t_out"]
    hueco_dentro = piezas[2]["t_in"] - piezas[1]["t_out"]
    comprobar(abs(hueco_secciones - goo.PAUSA_ENTRE_SECCIONES_S) < 0.01,
              "entre secciones, la pausa de cambio de tema")
    comprobar(abs(hueco_dentro - goo.PAUSA_DENTRO_S) < 0.01,
              "dentro de una seccion, la pausa corta")
    comprobar(piezas[0]["t_in"] < 0.1, "el silencio inicial se recorta")
    comprobar(abs(segundos - (len(wav) - 44) / (goo.SR * 2)) < 0.01,
              "la duracion cuadra con el wav")


# ---------------------------------------------------------------- p4_voz

class _GoogleDoble:
    """Google de mentira: tonos, y apunta que estilo llevo cada trozo."""

    def __init__(self, real):
        self._real = real
        self.llamadas = []

    def __getattr__(self, nombre):
        return getattr(self._real, nombre)

    def sintetizar_trozos(self, trozos, voz, modelo, idioma, avisar=None, **_):
        self.llamadas.append([dict(t) for t in trozos])
        pista, piezas, reloj = [], [], 0.0
        for indice, trozo in enumerate(trozos):
            if indice:
                pista.append(self._real.silencio(0.6))
                reloj += 0.6
            audio = tono(0.35 * len(trozo["texto"].split()))
            piezas.append({"trozo": indice, "t_in": reloj})
            reloj += len(audio) / (self._real.SR * 2)
            piezas[-1]["t_out"] = reloj
            pista.append(audio)
        pcm = b"".join(pista)
        return (self._real.wav_desde_pcm(pcm), len(pcm) / (self._real.SR * 2),
                {"piezas": piezas, "avisos": [], "peticiones": len(trozos),
                 "caracteres": sum(len(t["texto"]) for t in trozos)})


class _AlineadorDoble:
    def __init__(self):
        self.llamadas = []

    def alinear(self, ruta, hablado, idioma="en", avisar=None, **_):
        self.llamadas.append(hablado)
        segundos = (os.path.getsize(ruta) - 44) / (goo.SR * 2)
        palabras = alin.tokens_del_guion(hablado)
        paso = segundos / max(1, len(palabras))
        return {"palabras": [{"w": w, "s": round(i * paso, 3), "e": round((i + 0.9) * paso, 3)}
                             for i, w in enumerate(palabras)],
                "cobertura": 1.0, "faltan": [], "sobran": 0,
                "dispositivo": "doble", "modelo": "doble", "segundos": 0.0}


def _guion_largo():
    """Gancho de cinco bloques largos (mas de un minuto), cuerpo y cierre."""
    frase = ("Most people never hear about this simple money habit from the past. "
             "Your grandparents used it every single week without thinking twice. "
             "And it quietly built more wealth than any fancy app ever could.")
    bloques = []
    for i in range(1, 6):
        bloques.append({"id": f"B{i:02d}", "texto": frase, "abre_seccion": i == 1})
    for i in range(6, 12):
        bloques.append({"id": f"B{i:02d}", "texto": "Here is how it works in practice.",
                        "abre_seccion": i == 6})
    bloques.append({"id": "B12", "texto": "Thanks for watching, see you next time.",
                    "abre_seccion": True})
    return bloques


def preparar_proyecto(base, bloques):
    proyecto = Proyecto.crear(base, "Prueba Google")
    estado = Estado(proyecto)
    estado.set_params("ingesta", {"url": "https://ejemplo/1"})
    estado.completar("ingesta", {"frames": 0})
    estado.set_params("brief", {"idioma_salida": "en"})
    estado.completar("brief", {"ok": True})
    estado.set_params("guion", {"unidades": {b["id"]: {"texto": b["texto"]} for b in bloques}})
    carpeta = proyecto.ruta_trabajo("guion")
    with open(os.path.join(carpeta, "guion.json"), "w", encoding="utf-8") as fh:
        json.dump({"titulo": "Prueba", "guion": bloques}, fh, ensure_ascii=False)
    estado.completar("guion", {"guion": bloques, "bloques": len(bloques)})
    return proyecto, estado


def prueba_params():
    print("\n[4] p4_voz: params con Google")
    sin = p4_voz.resolver_params({"idioma": "en"})
    comprobar("proveedor" not in sin and sin["modelo"] == p4_voz.MODELO_POR_DEFECTO,
              "sin proveedor, Cartesia como siempre (los proyectos viejos no cambian)")
    cfg = p4_voz.resolver_params({"proveedor": "google", "idioma": "en",
                                  "estilos": {"intro": INTRO, "cuerpo": CUERPO,
                                              "otro": "x"}})
    igual((cfg["proveedor"], cfg["modelo"], cfg["voz_id"], cfg["familia"]),
          ("google", "gemini-2.5-flash-tts", "Orus", "gemini"),
          "con Google: Gemini 2.5 Flash TTS y Orus por defecto")
    igual(sorted(cfg["estilos"]), ["cuerpo", "intro"], "solo viajan los tramos conocidos")
    cartesia_id = "a892d232-f705-40d7-bc8d-e368b295ec2a"
    igual(p4_voz.resolver_params({"proveedor": "google", "voz_id": cartesia_id,
                                  "idioma": "en"})["voz_id"], "Orus",
          "un id de Cartesia heredado de un preset no se manda a Google")
    falla(lambda: p4_voz.resolver_params({"proveedor": "google", "modelo": "sonic-3"}),
          "un modelo de Cartesia con Google se rechaza ANTES de pagar", ValueError)
    falla(lambda: p4_voz.resolver_params({"proveedor": "google", "voz_id": "Pepito"}),
          "una voz que Google no tiene se rechaza", ValueError)
    falla(lambda: p4_voz.resolver_params({"proveedor": "polly"}),
          "un proveedor desconocido se rechaza", ValueError)
    igual(p4_voz.estilo_de("cierre", {"estilos": {"cuerpo": CUERPO}}), CUERPO,
          "sin estilo de cierre, el cierre usa el del cuerpo")
    igual(p4_voz.estilo_de("intro", {"estilos": {}}), None, "sin estilos, ninguno")


def prueba_tramos():
    print("\n[5] p4_voz: secciones y tramos")
    bloques = p4_voz.normalizar_bloques({"guion": _guion_largo()})
    comprobar(bloques[0].get("abre_seccion") and not bloques[1].get("abre_seccion"),
              "la marca de seccion del guion llega al paso de voz")
    secciones = p4_voz.agrupar_secciones(bloques)
    igual([s["bloques"][0] for s in secciones], ["B01", "B06", "B12"],
          "se corta por las secciones DECLARADAS por el guion")
    cfg = {"estilos": {"intro": INTRO, "cuerpo": CUERPO, "cierre": CIERRE}}
    partidas = p4_voz.separar_intro(secciones, bloques, cfg)
    primera = sum(p4_voz._segundos_estimados(b["texto"], cfg)
                  for b in bloques if b["id"] in partidas[0]["bloques"])
    comprobar(len(partidas[0]["bloques"]) < len(secciones[0]["bloques"]),
              "un gancho de mas de un minuto se acorta")
    comprobar(20 <= primera <= p4_voz.INTRO_MAXIMO_S,
              f"y se queda cerca del objetivo ({primera:.0f} s)")
    igual([s["id"] for s in partidas], [f"SB{i:03d}" for i in range(1, len(partidas) + 1)],
          "las secciones se renumeran seguidas")
    igual(sum(len(s["bloques"]) for s in partidas), len(bloques), "sin perder bloques")
    p4_voz.asignar_tramos(partidas, cfg)
    igual([s["tramo"] for s in partidas],
          ["intro"] + ["cuerpo"] * (len(partidas) - 2) + ["cierre"],
          "intro la primera, cierre la ultima, cuerpo el resto")
    sin_cierre = p4_voz.asignar_tramos([dict(s) for s in partidas],
                                       {"estilos": {"intro": INTRO, "cuerpo": CUERPO}})
    igual(sin_cierre[-1]["tramo"], "cuerpo", "sin estilo de cierre la ultima es cuerpo")
    a_mano = p4_voz.asignar_tramos([dict(s) for s in partidas],
                                   dict(cfg, tramos={"SB002": "intro"}))
    igual(a_mano[1]["tramo"], "intro", "lo fijado a mano manda")
    igual(p4_voz.separar_intro(secciones, bloques, {"estilos": {"cuerpo": CUERPO}}),
          secciones, "sin estilo de intro no se corta nada")


def prueba_toma_y_regrabado(base):
    print("\n[6] p4_voz: la toma con Google y el regrabado de una seccion")
    proyecto, estado = preparar_proyecto(base, _GUION)
    params = {"proveedor": "google", "idioma": "en", "hueco_minimo": 0.8,
              "estilos": {"intro": INTRO, "cuerpo": CUERPO, "cierre": CIERRE}}
    doble_google, doble_alineador = _GoogleDoble(goo), _AlineadorDoble()
    real_google, real_alineador = p4_voz.motor_google, p4_voz.alineador
    simular = os.environ.get("ESTUDIO_SIMULAR")
    p4_voz.motor_google, p4_voz.alineador = doble_google, doble_alineador
    os.environ["ESTUDIO_SIMULAR"] = "0"
    try:
        salidas = p4_voz.ejecutar(proyecto, params)
        trozos = doble_google.llamadas[0]
        estilos = [t["estilo"] for t in trozos]
        igual(estilos[0], INTRO, "la primera seccion va con el estilo de intro")
        igual(estilos[-1], CIERRE, "la ultima, con el de cierre")
        comprobar(all(e == CUERPO for e in estilos[1:-1]), "el resto, con el del cuerpo")
        igual(len(doble_google.llamadas), 1, "toda la toma en una sola tanda")
        comprobar(all("[" not in w["w"] for w in salidas["palabras"]),
                  "las etiquetas de pausa no acaban como palabras")
        igual(doble_alineador.llamadas[0].split(),
              " ".join(marcas_tts.limpiar(b["texto"]) for b in _GUION).split(),
              "el alineador recibe lo que se OYE, sin etiquetas")
        igual([s.get("tramo") for s in salidas["secciones"]][0], "intro",
              "las secciones guardan su tramo")
        comprobar(all(b["t_in"] is not None for b in salidas["bloques"]),
                  "cada bloque recibe su tramo de tiempo")
        igual(len(salidas["palabras"]),
              sum(len(marcas_tts.limpiar(b["texto"]).split()) for b in _GUION),
              "una marca por palabra del guion")
        comprobar(salidas["google"]["alineado"]["cobertura"] == 1.0,
                  "y queda apuntado como se alineo")

        # regrabar la intro: con SU estilo, y conservando el tramo
        with open(os.path.join(os.path.dirname(salidas["pista"]), p4_voz.NOMBRE_META),
                  "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        cfg = p4_voz.resolver_params(params)
        bloques = p4_voz.cargar_guion(proyecto, {"bloques": _GUION})
        nuevas, _ = p4_voz.regrabar_seccion(bloques, "SB001", cfg, meta,
                                            os.path.dirname(salidas["pista"]))
        igual(doble_google.llamadas[-1][0]["estilo"], INTRO,
              "regrabar la intro usa el estilo de intro")
        ultima = meta["secciones"][-1]["id"]
        p4_voz.regrabar_seccion(bloques, ultima, cfg, nuevas,
                                os.path.dirname(salidas["pista"]))
        igual(doble_google.llamadas[-1][0]["estilo"], CIERRE,
              "regrabar el cierre usa el de cierre")
        igual([s.get("tramo") for s in nuevas["secciones"]],
              [s.get("tramo") for s in salidas["secciones"]],
              "tras regrabar, cada seccion sigue en su tramo")
    finally:
        p4_voz.motor_google, p4_voz.alineador = real_google, real_alineador
        if simular is None:
            os.environ.pop("ESTUDIO_SIMULAR", None)
        else:
            os.environ["ESTUDIO_SIMULAR"] = simular


def prueba_simulado(base):
    print("\n[7] p4_voz: Google en modo simulado no llama a nadie")
    proyecto, _ = preparar_proyecto(base, _GUION)
    doble_google = _GoogleDoble(goo)
    real = p4_voz.motor_google
    simular = os.environ.get("ESTUDIO_SIMULAR")
    p4_voz.motor_google = doble_google
    os.environ["ESTUDIO_SIMULAR"] = "1"
    try:
        salidas = p4_voz.ejecutar(proyecto, {"proveedor": "google", "idioma": "en",
                                             "estilos": {"intro": INTRO, "cuerpo": CUERPO}})
    finally:
        p4_voz.motor_google = real
        if simular is None:
            os.environ.pop("ESTUDIO_SIMULAR", None)
        else:
            os.environ["ESTUDIO_SIMULAR"] = simular
    igual(doble_google.llamadas, [], "no se pide nada a Google")
    comprobar(salidas["palabras"] and salidas["secciones"][0].get("tramo") == "intro",
              "y aun asi sale la toma con sus tramos")


def prueba_fuga_de_estilo():
    print("\n[9] la voz que lee en voz alta su instruccion de estilo")
    frases = [{"start": 0.0, "end": 6.9, "text": "Intrigued, slightly urgent, quick but "
                                                 "clear pace, not salesy."},
              {"start": 6.9, "end": 10.0, "text": "Your grandparents never read a blog."}]
    guion = alin.tokens_del_guion("Your grandparents never read a blog.")
    reparto, cobertura = alin.frases_del_guion(guion, frases)
    igual(reparto[0], [], "la frase colada no se queda ninguna palabra del guion")
    de_mas = cobertura["de_mas"]
    comprobar(len(de_mas) == 1 and de_mas[0]["s"] == 0.0 and de_mas[0]["e"] == 6.9
              and "salesy" in de_mas[0]["texto"],
              "y el alineador dice QUE se dijo de mas y DONDE")

    trozos = [{"texto": "Your grandparents never read a blog.", "estilo": INTRO, "seccion": "SB001"},
              {"texto": "Habit one.", "estilo": CUERPO, "seccion": "SB002"}]
    info = {"piezas": [{"trozo": 0, "t_in": 0.0, "t_out": 10.0},
                       {"trozo": 1, "t_in": 10.6, "t_out": 12.0}]}
    igual(p4_voz.fugas_de_estilo({"de_mas": [{"texto": "Intrigued, slightly urgent tone, "
                                                       "quick but clear pace", "s": 0.0}]},
                                 info, trozos), {0: "Intrigued, slightly urgent tone, quick "
                                                    "but clear pace"},
          "unas palabras de mas que son la instruccion de la seccion: es una fuga")
    igual(p4_voz.fugas_de_estilo({"de_mas": [{"texto": "um so you know what", "s": 0.0}]},
                                 info, trozos), {},
          "unas palabras de mas cualquiera no son una fuga")

    # y se regraba sola esa seccion, una vez, y la segunda toma sale limpia
    class Alineador:
        def __init__(self):
            self.vez = 0

        def alinear(self, ruta, hablado, idioma="en", avisar=None, **_):
            self.vez += 1
            de_mas = ([{"texto": "Intrigued slightly urgent tone quick clear pace",
                        "palabras": 7, "s": 0.1, "e": 3.0}] if self.vez == 1 else [])
            return {"palabras": [{"w": w, "s": i * 0.3, "e": i * 0.3 + 0.2}
                                 for i, w in enumerate(hablado.split())],
                    "cobertura": 1.0, "faltan": [], "sobran": len(de_mas), "de_mas": de_mas}

    doble_google, doble_alin = _GoogleDoble(goo), Alineador()
    real_google, real_alin = p4_voz.motor_google, p4_voz.alineador
    simular = os.environ.get("ESTUDIO_SIMULAR")
    p4_voz.motor_google, p4_voz.alineador = doble_google, doble_alin
    os.environ["ESTUDIO_SIMULAR"] = "0"
    try:
        cfg = p4_voz.resolver_params({"proveedor": "google", "idioma": "en",
                                      "estilos": {"intro": INTRO, "cuerpo": CUERPO}})
        hablados = ["Your grandparents never read a blog.", "Habit one."]
        wav, segundos, alineado, info = p4_voz._google_verificado(
            trozos, hablados, cfg, lambda f, m="": None)
    finally:
        p4_voz.motor_google, p4_voz.alineador = real_google, real_alin
        if simular is None:
            os.environ.pop("ESTUDIO_SIMULAR", None)
        else:
            os.environ["ESTUDIO_SIMULAR"] = simular
    igual(len(doble_google.llamadas), 2, "una toma entera y un regrabado")
    igual([t["seccion"] for t in doble_google.llamadas[1]], ["SB001"],
          "el regrabado es SOLO la seccion que leyo su instruccion")
    igual(doble_google.llamadas[1][0]["estilo"], INTRO, "y con su mismo estilo")
    igual([(r["seccion"], r["motivo"]) for r in info.get("regrabados") or []],
          [("SB001", "fuga")], "queda apuntado que se regrabo y por que")
    comprobar("sin_arreglar" not in info, "y como la segunda salio limpia, no queda nada mal")
    igual(doble_alin.vez, 2, "se vuelve a alinear despues de regrabar")
    comprobar(abs(segundos - (len(wav) - 44) / (goo.SR * 2)) < 0.01,
              "la pista cosida cuadra con su duracion")
    comprobar(any("se regrabó sola" in a for a in p4_voz._avisos_google(info, [
        {"id": "SB001", "tramo": "intro"}, {"id": "SB002", "tramo": "cuerpo"}])),
              "y se le dice a quien escucha")

    print("\n[10] la voz que se salta texto")
    hablados = ["Your grandparents never read a finance blog. No apps, no charts. And "
                "somehow they saved anyway.", "Habit one: pay yourself first, every month."]
    igual(p4_voz.omisiones_por_seccion({"faltan_idx": list(range(0, 10))}, hablados), {0: 10},
          "diez palabras que faltan en la primera seccion: se regraba esa")
    igual(p4_voz.omisiones_por_seccion({"faltan_idx": [16, 17]}, hablados), {},
          "dos palabras (una contraccion, una cifra dicha distinta) no son una omision")
    # la primera seccion tiene 16 palabras: la segunda empieza en el indice 16
    igual(p4_voz.omisiones_por_seccion({"faltan_idx": list(range(16, 23))}, hablados), {1: 7},
          "y se sabe en QUE seccion faltan")
    igual(p4_voz.omisiones_por_seccion({}, hablados), {}, "sin nada que falte, nada")


def prueba_coste():
    print("\n[8] coste: lo que se paga a Google sale en pantalla")
    from nucleo import coste
    usd, tokens = coste.coste_google_tts("gemini-2.5-flash-tts", 22000, 1500)
    comprobar(0.37 <= usd <= 0.39,
              f"25 min con Gemini 2.5 Flash TTS ~ 0,38 $ (sale {usd:.4f})")
    igual(tokens["salida"], 37500, "25 tokens de audio por segundo")
    usd_estilo, _ = coste.coste_google_tts("gemini-2.5-flash-tts", 22000, 1500,
                                           caracteres_estilo=4000)
    comprobar(usd_estilo > usd, "la instruccion de estilo tambien se paga")
    igual(round(coste.coste_google_tts("chirp3-hd", 1000, 60)[0], 6), 0.03,
          "Chirp 3 HD va por caracter")
    igual(coste.coste_google_tts("gemini-2.5-flash-lite-preview-tts", 1000, 60),
          (None, None), "un modelo sin tarifa sale sin importe, no con uno inventado")
    informe = coste.instrumentar()
    comprobar("voz.sintesis_google" in informe["enganchado"],
              "el medidor engancha la sintesis de Google")


_GUION = [
    {"id": "B01", "texto": "Most people have never been told this secret.", "abre_seccion": True},
    {"id": "B02", "texto": 'Your grandparents knew it.<break time="900ms"/>'},
    {"id": "B03", "texto": "Habit one: pay yourself first.", "abre_seccion": True},
    {"id": "B04", "texto": "Put ten percent aside the day you get paid."},
    {"id": "B05", "texto": "Habit two: buy used.", "abre_seccion": True},
    {"id": "B06", "texto": "Thanks for watching, see you soon.", "abre_seccion": True},
]


def main():
    base = tempfile.mkdtemp(prefix="estudio_voz_google_")
    os.environ["ESTUDIO_ESTADISTICAS"] = os.path.join(base, "estadisticas.json")
    os.environ["ESTUDIO_SECRETOS"] = os.path.join(base, "secretos")
    print(f"proyectos de prueba en {base}")
    try:
        prueba_emparejar()
        prueba_motor_google()
        prueba_igualar()
        prueba_params()
        prueba_tramos()
        prueba_toma_y_regrabado(base)
        prueba_simulado(base)
        prueba_fuga_de_estilo()
        prueba_coste()
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print()
    if FALLOS:
        print(f"VOZ GOOGLE: {len(FALLOS)} de {HECHAS[0]} comprobaciones fallan")
        for texto in FALLOS:
            print(f"  - {texto}")
        return 1
    print(f"VOZ GOOGLE OK: {HECHAS[0]} comprobaciones pasan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
