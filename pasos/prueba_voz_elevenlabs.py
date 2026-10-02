"""
Prueba de la voz con ElevenLabs (motores/voz_elevenlabs) y de su paso por
p4_voz: marcas por caracter -> por palabra, troceado, creditos, contexto en las
costuras, regrabado y medidor.

NO SALE A LA RED NI GASTA UN CREDITO. La API se sustituye por dobles: el
motor por uno que devuelve tonos con su alineacion, y la suscripcion por un
contador en memoria.

    python pasos/prueba_voz_elevenlabs.py
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

from nucleo import coste  # noqa: E402
from nucleo.estado import Estado  # noqa: E402
from nucleo.proyecto import Proyecto  # noqa: E402

import comun  # noqa: E402
import marcas_tts  # noqa: E402
import p4_voz  # noqa: E402

FALLOS = []
HECHAS = [0]

ele = comun.cargar_motor("voz_elevenlabs", "voz.py")
VOZ = "AbCdEfGhIjKlMnOpQrSt"          # 20 letras: forma de id de ElevenLabs


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


def alineacion_de(texto, por_caracter=0.05):
    """La alineacion que devolveria la API: un caracter cada `por_caracter` s."""
    return {"characters": list(texto),
            "character_start_times_seconds": [i * por_caracter for i in range(len(texto))],
            "character_end_times_seconds": [(i + 1) * por_caracter for i in range(len(texto))]}


def tono(segundos):
    n = int(ele.SR * segundos)
    return b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 220 * i / ele.SR)))
                    for i in range(n))


# ------------------------------------------------------------------ motor

def prueba_marcas():
    print("\n[1] motor: de la alineacion por caracter a marcas por palabra")
    texto = "Save money, every week — it works!"
    marcas = ele.palabras_de_alineacion(texto, alineacion_de(texto))
    igual([m["w"] for m in marcas], ["Save", "money,", "every", "week—", "it", "works!"],
          "las palabras del texto con su puntuacion (y el guion largo pegado)")
    igual((marcas[0]["s"], marcas[0]["e"]), (0.0, 0.2), "cada palabra va de su primer "
          "caracter al ultimo")
    comprobar(all(marcas[i]["e"] <= marcas[i + 1]["s"] for i in range(len(marcas) - 1)),
              "en orden y sin solaparse")
    desplazadas = ele.palabras_de_alineacion(texto, alineacion_de(texto), desde=10.0)
    igual(desplazadas[0]["s"], 10.0, "y se pueden desplazar a su sitio en la pista")
    igual(ele.palabras_de_alineacion(texto, alineacion_de("otro texto distinto")), None,
          "si la alineacion no casa con el texto, None: no se adivina")
    igual(ele.palabras_de_alineacion("Hola.", {}), None, "sin alineacion, None")

    largo = " ".join(f"Sentence {i} is here." for i in range(100))
    trozos = ele.trocear(largo, 300)
    comprobar(len(trozos) > 1 and all(len(t) <= 300 for t in trozos),
              f"un texto largo se parte bajo el tope ({len(trozos)} trozos)")
    igual(" ".join(trozos), largo, "sin perder nada")
    igual(ele.creditos_estimados(1000, "eleven_flash_v2_5"), 500, "Flash: medio credito "
          "por caracter")
    igual(ele.creditos_estimados(1000, "eleven_multilingual_v2"), 1000, "Multilingual v2: uno")
    igual(ele.creditos_estimados(1000, "eleven_v4"), None, "v4 sin precio publicado: None")


def prueba_trozos():
    print("\n[2] motor: varias piezas, creditos y contexto en las costuras")
    llamadas, usados = [], [1000]
    original_s, original_sus = ele.sintetizar, ele.suscripcion

    def sintetizar(texto, voz, modelo, idioma, anterior, siguiente, velocidad):
        llamadas.append({"texto": texto, "anterior": anterior, "siguiente": siguiente})
        usados[0] += ele.creditos_estimados(len(texto), modelo)
        segundos = 0.05 * len(texto)
        return tono(segundos), segundos, ele.palabras_de_alineacion(texto, alineacion_de(texto))

    def suscripcion():
        return {"plan": "starter", "usados": usados[0], "limite": 30000,
                "restantes": 30000 - usados[0]}

    ele.sintetizar, ele.suscripcion = sintetizar, suscripcion
    try:
        wav, segundos, palabras, info = ele.sintetizar_trozos(
            [{"texto": "First part here.", "seccion": "SB001"},
             {"texto": "Second part now.", "seccion": "SB002"}],
            VOZ, "eleven_flash_v2_5", "en", concurrencia=1,
            antes="What came before.", despues="What comes after.")
        igual(info["peticiones"], 2, "una peticion por trozo")
        igual(llamadas[0]["anterior"], "What came before.",
              "la primera pieza lleva como contexto lo de antes")
        igual(llamadas[0]["siguiente"], "Second part now.", "y la pieza siguiente")
        igual(llamadas[1]["anterior"], "First part here.", "la segunda, la anterior")
        igual(llamadas[1]["siguiente"], "What comes after.", "y lo de despues")
        igual(info["creditos"], 16, "los creditos son los MEDIDOS (suscripcion antes y despues)")
        igual(info["creditos_restantes"], 30000 - 1016, "y se dice cuantos quedan")
        segunda = [p for p in palabras if p["w"] == "Second"][0]
        comprobar(abs(segunda["s"] - (0.05 * 16 + ele.PAUSA_ENTRE_SECCIONES_S)) < 0.01,
                  "las marcas de la segunda pieza se desplazan a su sitio en la pista")
        comprobar(abs(segundos - (len(wav) - 44) / (ele.SR * 2)) < 0.01,
                  "la duracion cuadra con el wav")

        usados[0] = 29990
        falla(lambda: ele.sintetizar_trozos([{"texto": "x" * 100}], VOZ, "eleven_flash_v2_5"),
              "sin creditos para toda la toma, no se graba ni una pieza", ele.SinCreditos)
    finally:
        ele.sintetizar, ele.suscripcion = original_s, original_sus


# ---------------------------------------------------------------- p4_voz

class _ElevenDoble:
    """ElevenLabs de mentira para p4_voz: tonos y su alineacion."""

    def __init__(self, real, sin_marcas=False):
        self._real = real
        self.llamadas = []
        self.sin_marcas = sin_marcas

    def __getattr__(self, nombre):
        return getattr(self._real, nombre)

    def sintetizar_trozos(self, trozos, voz, modelo, idioma=None, velocidad=None,
                          avisar=None, antes="", despues="", **_):
        self.llamadas.append({"trozos": [dict(t) for t in trozos], "antes": antes,
                              "despues": despues, "velocidad": velocidad})
        pista, palabras, reloj = [], [], 0.0
        for indice, trozo in enumerate(trozos):
            if indice:
                pista.append(self._real.silencio(0.6))
                reloj += 0.6
            segundos = 0.05 * len(trozo["texto"])
            pista.append(tono(segundos))
            palabras.extend(self._real.palabras_de_alineacion(
                trozo["texto"], alineacion_de(trozo["texto"]), desde=reloj))
            reloj += segundos
        pcm = b"".join(pista)
        info = {"peticiones": len(trozos), "caracteres": sum(len(t["texto"]) for t in trozos),
                "creditos": 7, "creditos_restantes": 1234}
        return (self._real.wav_desde_pcm(pcm), len(pcm) / (self._real.SR * 2),
                None if self.sin_marcas else palabras, info)


class _AlineadorDoble:
    def __init__(self):
        self.llamadas = 0

    def alinear(self, ruta, hablado, idioma="en", avisar=None, **_):
        self.llamadas += 1
        palabras = hablado.split()
        segundos = (os.path.getsize(ruta) - 44) / (ele.SR * 2)
        paso = segundos / max(1, len(palabras))
        return {"palabras": [{"w": w, "s": round(i * paso, 3), "e": round((i + 0.9) * paso, 3)}
                             for i, w in enumerate(palabras)],
                "cobertura": 1.0, "faltan": [], "sobran": 0, "dispositivo": "doble",
                "modelo": "doble", "segundos": 0.0}


_GUION = [
    {"id": "B01", "texto": "Most people never hear about this.", "abre_seccion": True},
    {"id": "B02", "texto": 'Your grandparents knew it.<break time="900ms"/>'},
    {"id": "B03", "texto": "Habit one: pay yourself first.", "abre_seccion": True},
    {"id": "B04", "texto": "Thanks for watching.", "abre_seccion": True},
]


def preparar_proyecto(base):
    proyecto = Proyecto.crear(base, "Prueba ElevenLabs")
    estado = Estado(proyecto)
    estado.set_params("ingesta", {"url": "https://ejemplo/1"})
    estado.completar("ingesta", {"frames": 0})
    estado.set_params("brief", {"idioma_salida": "en"})
    estado.completar("brief", {"ok": True})
    estado.set_params("guion", {"unidades": {b["id"]: {"texto": b["texto"]} for b in _GUION}})
    with open(os.path.join(proyecto.ruta_trabajo("guion"), "guion.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"titulo": "Prueba", "guion": _GUION}, fh, ensure_ascii=False)
    estado.completar("guion", {"guion": _GUION, "bloques": len(_GUION)})
    return proyecto


def prueba_params():
    print("\n[3] p4_voz: params con ElevenLabs")
    cfg = p4_voz.resolver_params({"proveedor": "elevenlabs", "idioma": "en"})
    igual((cfg["proveedor"], cfg["modelo"]), ("elevenlabs", "eleven_flash_v2_5"),
          "por defecto, Flash v2.5")
    igual(cfg["voz_id"], ele.VOCES["en"]["id"], "y la voz de la libreria por defecto")
    igual(p4_voz.resolver_params({"proveedor": "elevenlabs", "voz_id": VOZ})["voz_id"], VOZ,
          "una voz propia de ElevenLabs se respeta")
    for ajena in ("a892d232-f705-40d7-bc8d-e368b295ec2a", "Orus"):
        igual(p4_voz.resolver_params({"proveedor": "elevenlabs", "voz_id": ajena})["voz_id"],
              ele.VOCES["en"]["id"], f"la voz de otro proveedor ({ajena[:8]}) no se manda")
    igual(p4_voz.resolver_params({"proveedor": "elevenlabs", "velocidad": "slow"})["speed"],
          p4_voz._speed_sonic3("slow"), "la velocidad viaja como speed")
    falla(lambda: p4_voz.resolver_params({"proveedor": "elevenlabs", "modelo": "sonic-3"}),
          "un modelo que no es de ElevenLabs se rechaza antes de pagar", ValueError)
    cartesia = p4_voz.resolver_params({"voz_id": VOZ, "idioma": "en"})
    comprobar(cartesia["voz_id"] != VOZ,
              "volver a Cartesia con una voz de ElevenLabs puesta usa la de Cartesia")


def prueba_toma(base):
    print("\n[4] p4_voz: la toma y el regrabado con ElevenLabs")
    proyecto = preparar_proyecto(base)
    params = {"proveedor": "elevenlabs", "voz_id": VOZ, "idioma": "en", "hueco_minimo": 0.8}
    doble, alineador = _ElevenDoble(ele), _AlineadorDoble()
    real_e, real_a = p4_voz.motor_eleven, p4_voz.alineador
    simular = os.environ.get("ESTUDIO_SIMULAR")
    p4_voz.motor_eleven, p4_voz.alineador = doble, alineador
    os.environ["ESTUDIO_SIMULAR"] = "0"
    try:
        salidas = p4_voz.ejecutar(proyecto, params)
        trozos = doble.llamadas[0]["trozos"]
        igual(len(trozos), 3, "una pieza por seccion declarada")
        comprobar(all("<" not in t["texto"] and "[" not in t["texto"] for t in trozos),
                  "el texto va limpio, sin etiquetas")
        igual(len(salidas["palabras"]),
              sum(len(marcas_tts.limpiar(b["texto"]).split()) for b in _GUION),
              "una marca por palabra del guion")
        igual(alineador.llamadas, 0, "con marcas de la API no hace falta el alineador")
        igual(salidas["elevenlabs"]["creditos"], 7, "los creditos gastados quedan en la toma")

        with open(os.path.join(os.path.dirname(salidas["pista"]), p4_voz.NOMBRE_META),
                  "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        cfg = p4_voz.resolver_params(params)
        bloques = p4_voz.cargar_guion(proyecto, {"bloques": _GUION})
        p4_voz.regrabar_seccion(bloques, "SB002", cfg, meta, os.path.dirname(salidas["pista"]))
        ultima = doble.llamadas[-1]
        igual([t["texto"] for t in ultima["trozos"]], ["Habit one: pay yourself first."],
              "regrabar graba SOLO esa seccion")
        comprobar(ultima["antes"].endswith("Your grandparents knew it.")
                  and ultima["despues"].startswith("Thanks for watching."),
                  "con el texto de antes y de despues como contexto")

        sin = _ElevenDoble(ele, sin_marcas=True)
        p4_voz.motor_eleven = sin
        proyecto2 = preparar_proyecto(os.path.join(base, "dos"))
        salidas2 = p4_voz.ejecutar(proyecto2, params)
        igual(alineador.llamadas, 1, "si la API no da marcas, las pone el alineador local")
        comprobar(salidas2["palabras"] and "alineado" in salidas2["elevenlabs"],
                  "y queda apuntado que se alineo aqui")
    finally:
        p4_voz.motor_eleven, p4_voz.alineador = real_e, real_a
        if simular is None:
            os.environ.pop("ESTUDIO_SIMULAR", None)
        else:
            os.environ["ESTUDIO_SIMULAR"] = simular


def prueba_coste():
    print("\n[5] coste: ElevenLabs en creditos, sin dolares")
    informe = coste.instrumentar()
    comprobar("voz.sintesis_elevenlabs" in informe["enganchado"],
              "el medidor engancha la sintesis de ElevenLabs")
    total = coste.agregar([{"proveedor": "elevenlabs", "cantidad": {"caracteres": 1000,
                                                                    "creditos": 500}},
                           {"proveedor": "tts", "usd": 0.38, "cantidad": {"caracteres": 22000}}])
    eleven = total["proveedores"]["elevenlabs"]
    igual((eleven["usd"], eleven["suma_al_total"]), (None, False),
          "ElevenLabs no suma dolares al total (son creditos del plan)")
    igual(eleven["cantidad"]["creditos"], 500, "y cuenta los creditos")
    igual(total["total_usd"], 0.38, "el total sigue siendo solo lo que se paga en dolares")


def main():
    base = tempfile.mkdtemp(prefix="estudio_voz_eleven_")
    os.environ["ESTUDIO_ESTADISTICAS"] = os.path.join(base, "estadisticas.json")
    os.environ["ESTUDIO_SECRETOS"] = os.path.join(base, "secretos")
    print(f"proyectos de prueba en {base}")
    try:
        prueba_marcas()
        prueba_trozos()
        prueba_params()
        prueba_toma(base)
        prueba_coste()
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print()
    if FALLOS:
        print(f"VOZ ELEVENLABS: {len(FALLOS)} de {HECHAS[0]} comprobaciones fallan")
        for texto in FALLOS:
            print(f"  - {texto}")
        return 1
    print(f"VOZ ELEVENLABS OK: {HECHAS[0]} comprobaciones pasan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
