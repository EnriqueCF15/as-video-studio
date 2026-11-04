"""
Motor de voz: Google Cloud Text-to-Speech (Gemini TTS y Chirp 3 HD).

Lo paga el proyecto de Google Cloud de esta instalacion -- el de la prueba
gratuita mientras dure --, autenticado con las Application Default Credentials
de gcloud (o con el JSON de una cuenta de servicio). NUNCA con una API key de AI
Studio: la prueba gratuita no cubre AI Studio y eso se cobraria a la tarjeta.

Lo que Google NO da, y por que eso decide el diseno
---------------------------------------------------
1. **Marcas de tiempo por palabra.** Ni Gemini TTS ni Chirp 3 HD las devuelven.
   Las pone despues el alineador local (motores/alinear_voz), que es quien deja
   la salida con la forma de siempre: [{"w","s","e"}].
2. **Una toma de 25 minutos.** Cada peticion admite <= 4.000 bytes de texto y
   ~655 s de audio. Un guion largo va en TROZOS, y cada trozo es una generacion
   distinta: el tono y el volumen pueden variar de uno a otro. Por eso:
     - se trocea SOLO por secciones del guion y, si una no cabe, por final de
       frase -- nunca a mitad de frase;
     - cada trozo lleva la misma voz y modelo; solo cambia la instruccion de
       estilo, y solo si el trozo es de otro tramo (intro, cuerpo, cierre);
     - los trozos se IGUALAN de volumen y se unen con una pausa natural;
     - y se MIDE la costura: si dos trozos seguidos difieren mucho en volumen
       o en ritmo, se dice (`avisos`), que es lo unico que no se arregla solo.

Interfaz
--------
    sintetizar(texto, voz=, modelo=, idioma=, estilo=) -> (pcm, segundos)
    sintetizar_trozos(trozos, voz=, modelo=, idioma=, ...) -> (wav, segundos, info)
    wav_desde_pcm(pcm) -> wav ; SR ; MODELOS ; VOCES
    sintetizar_continuo / sintetizar_plan: como voz_cartesia, para el uso suelto.

Contrato: no importa nada de la aplicacion. Lee `secretos/claves.json` (bloque
"google": proyecto, ubicacion, cuenta de servicio) igual que voz_cartesia lee el
suyo.
"""
import argparse
import base64
import io
import json
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests

API = "https://texttospeech.googleapis.com/v1/text:synthesize"
ALCANCE = "https://www.googleapis.com/auth/cloud-platform"
SR = 44100

#: Modelos y su familia. La familia decide la forma de la peticion: Gemini lleva
#: `prompt` (estilo en lenguaje natural) y `modelName`; Chirp 3 HD no tiene
#: estilo y la voz va en el nombre ("en-US-Chirp3-HD-Orus").
MODELOS = {
    "gemini-2.5-flash-tts": "gemini",
    "gemini-2.5-pro-tts": "gemini",
    "gemini-2.5-flash-lite-preview-tts": "gemini",
    "gemini-3.1-flash-tts-preview": "gemini",
    "chirp3-hd": "chirp",
}
MODELO_POR_DEFECTO = "gemini-2.5-flash-tts"

#: Voz por defecto por idioma. Las de Gemini y Chirp 3 HD comparten nombres
#: (Orus, Charon, Kore...), asi que la misma voz vale para las dos familias.
VOCES = {
    "en": {"id": "Orus", "nombre": "Orus (masculina)"},
    "es": {"id": "Orus", "nombre": "Orus (masculina)"},
}

#: Voces masculinas y femeninas que publica Google para estas dos familias. Es
#: el catalogo: no hay endpoint que lo liste con su genero.
VOCES_MASCULINAS = ("Achird", "Algenib", "Algieba", "Alnilam", "Charon",
                    "Enceladus", "Fenrir", "Iapetus", "Orus", "Puck",
                    "Rasalgethi", "Sadachbia", "Sadaltager", "Schedar",
                    "Umbriel", "Zubenelgenubi")
VOCES_FEMENINAS = ("Achernar", "Aoede", "Autonoe", "Callirrhoe", "Despina",
                   "Erinome", "Gacrux", "Kore", "Laomedeia", "Leda", "Pulcherrima",
                   "Sulafat", "Vindemiatrix", "Zephyr")

#: Locale por idioma y familia. Gemini tiene es-ES en GA (es-419 en preview);
#: Chirp 3 HD tiene es-US.
LOCALES = {"gemini": {"en": "en-US", "es": "es-ES"},
           "chirp": {"en": "en-US", "es": "es-US"}}

#: Tope por peticion. La API admite 4.000 bytes de texto; se deja margen para
#: las etiquetas de pausa que se anaden y para no rozar el limite de audio.
MAX_BYTES = 3600

#: Modelos que se APAGAN a lo largo de una peticion larga: hay que pedirles el
#: texto en piezas cortas y parecidas. Medido el 09-10-2026 con Gemini 3.1 Flash
#: TTS: en una peticion de 1.900 caracteres la voz pierde brillo desde los 10 s
#: y cae ~5 dB pasado el minuto (Enrique: «se vuelve un susurro»); en piezas de
#: ~600 el nivel se quedo plano y cada pieza arranca con la energia del
#: principio. Cuesta lo mismo: Google cobra por caracter y por segundo de audio.
BYTES_PIEZA_POR_MODELO = {"gemini-3.1-flash-tts-preview": 700}

#: Quitasoplidos (ffmpeg afftdn) sobre cada pieza: la voz de Google trae un
#: soplido de -53/-57 dB debajo de todo, que se oia como estatica (09-10-2026,
#: comparado de oido por Enrique: «definitivamente mas limpio»).
FILTRO_SOPLIDO = "afftdn=nr=12:nf=-55:tn=1"

#: Pausa entre trozos de SECCIONES distintas (cambio de tema: se lee como
#: intencionada) y entre partes de una misma seccion partida por tamano.
PAUSA_ENTRE_SECCIONES_S = 0.6
PAUSA_DENTRO_S = 0.35

#: Nivel al que se igualan los trozos (dBFS medido sobre lo que suena, no sobre
#: los silencios) y lo maximo que se le corrige a uno: si hace falta mas, el
#: trozo esta mal y no se maquilla.
NIVEL_OBJETIVO_DB = -20.0
AJUSTE_MAXIMO_DB = 9.0

#: A partir de cuanto se AVISA de una costura: diferencia de volumen entre dos
#: trozos seguidos antes de igualar, y de ritmo (palabras/s).
AVISO_VOLUMEN_DB = 3.0
AVISO_RITMO = 0.18

#: Peticiones a la vez. Google cuenta cuota por minuto; tres no la rozan.
CONCURRENCIA = 3
REINTENTOS = 6

CARPETA_SECRETOS = os.environ.get("ESTUDIO_SECRETOS") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "secretos")
RUTA_CLAVES = os.path.join(CARPETA_SECRETOS, "claves.json")


class SinCredenciales(RuntimeError):
    """No hay forma de hablar con Google Cloud en esta maquina."""


# ----------------------------------------------------------- credenciales

def ficha_google():
    """El bloque "google" del almacen de claves, o {}. Nunca lanza."""
    try:
        with open(RUTA_CLAVES, "r", encoding="utf-8-sig") as fh:
            datos = json.load(fh)
    except (OSError, ValueError):
        return {}
    ficha = datos.get("google") if isinstance(datos, dict) else None
    return ficha if isinstance(ficha, dict) else {}


_CREDENCIALES = {"objeto": None, "proyecto": None}


def credenciales():
    """(token, proyecto) para la API. Renueva el token cuando caduca.

    Orden: el JSON de una cuenta de servicio si Configuracion lo apunta; si no,
    las Application Default Credentials (`gcloud auth application-default
    login`). El proyecto: el de Configuracion, el de la variable, o el
    `quota_project_id` de las ADC.
    """
    try:
        import google.auth
        import google.auth.transport.requests
        from google.oauth2 import service_account
    except ImportError as fallo:
        raise SinCredenciales("falta la libreria google-auth "
                              "(pip install -r requirements.txt)") from fallo
    ficha = ficha_google()
    objeto = _CREDENCIALES["objeto"]
    if objeto is None:
        ruta_json = str(ficha.get("cuenta_servicio") or "").strip()
        try:
            if ruta_json:
                objeto = service_account.Credentials.from_service_account_file(
                    ruta_json, scopes=[ALCANCE])
                por_defecto = objeto.project_id
            else:
                objeto, por_defecto = google.auth.default(scopes=[ALCANCE])
        except Exception as fallo:                              # noqa: BLE001
            raise SinCredenciales(
                "no hay credenciales de Google Cloud: ejecuta "
                "`gcloud auth application-default login` (o apunta el JSON de "
                f"una cuenta de servicio en Configuracion). Detalle: {fallo}") from fallo
        proyecto = (str(ficha.get("proyecto") or "").strip()
                    or os.environ.get("ESTUDIO_GCP_PROYECTO")
                    or os.environ.get("GOOGLE_CLOUD_PROJECT")
                    or getattr(objeto, "quota_project_id", None) or por_defecto)
        if not proyecto:
            raise SinCredenciales("no se sabe que proyecto de Google Cloud usar: "
                                  "ponlo en Configuracion")
        _CREDENCIALES.update(objeto=objeto, proyecto=proyecto)
    if not objeto.valid:
        objeto.refresh(google.auth.transport.requests.Request())
    return objeto.token, _CREDENCIALES["proyecto"]


# ---------------------------------------------------------------- audio

def wav_desde_pcm(pcm: bytes) -> bytes:
    """Cabecera WAV PCM s16le mono sobre PCM crudo (igual que voz_cartesia)."""
    cabecera = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    cabecera += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16)
    cabecera += b"data" + struct.pack("<I", len(pcm))
    return cabecera + pcm


def _pcm_de_wav(contenido):
    """PCM s16le mono a SR de lo que devuelve la API (un WAV con cabecera)."""
    with wave.open(io.BytesIO(contenido), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise RuntimeError("Google devolvio un audio que no es PCM 16 bits mono")
        if wav.getframerate() != SR:
            raise RuntimeError(f"Google devolvio {wav.getframerate()} Hz y se pidio {SR}")
        return wav.readframes(wav.getnframes())


def _muestras(pcm):
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def _a_pcm(muestras):
    return (np.clip(muestras, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def _energia_por_ventana(muestras, ventana_s=0.02):
    paso = max(1, int(SR * ventana_s))
    n = len(muestras) // paso
    if n == 0:
        return np.zeros(0)
    bloques = muestras[:n * paso].reshape(n, paso)
    return np.sqrt(np.mean(bloques ** 2, axis=1) + 1e-12)


def nivel_db(pcm):
    """Volumen de lo que SUENA (dBFS), sin contar los silencios. None si no hay voz."""
    energia = _energia_por_ventana(_muestras(pcm))
    if not len(energia):
        return None
    umbral = 10 ** (-45 / 20.0)
    voz = energia[energia > umbral]
    if not len(voz):
        return None
    return float(20 * np.log10(np.sqrt(np.mean(voz ** 2))))


def recortar_silencios(pcm, margen_s=0.08, umbral_db=-50.0):
    """Quita el silencio del principio y del final, dejando un margen."""
    muestras = _muestras(pcm)
    energia = _energia_por_ventana(muestras)
    umbral = 10 ** (umbral_db / 20.0)
    con_voz = np.nonzero(energia > umbral)[0]
    if not len(con_voz):
        return pcm
    paso = int(SR * 0.02)
    ini = max(0, con_voz[0] * paso - int(margen_s * SR))
    fin = min(len(muestras), (con_voz[-1] + 1) * paso + int(margen_s * SR))
    return _a_pcm(muestras[ini:fin])


def aplicar_ganancia(pcm, db):
    """Sube o baja el volumen, sin pasar de -1 dBFS de pico."""
    muestras = _muestras(pcm) * (10 ** (db / 20.0))
    pico = float(np.max(np.abs(muestras))) if len(muestras) else 0.0
    techo = 10 ** (-1 / 20.0)
    if pico > techo:
        muestras *= techo / pico
    return _a_pcm(muestras)


def silencio(segundos):
    return b"\x00\x00" * int(SR * segundos)


# --------------------------------------------------------------- texto

def trocear_por_bytes(texto, maximo=MAX_BYTES):
    """Parte un texto que no cabe en una peticion, por final de frase.

    Si una frase sola no cabe (no deberia pasar en un guion), se parte por
    comas y en ultimo caso por palabras. Nunca se devuelve un trozo vacio.
    """
    texto = " ".join(str(texto or "").split())
    if len(texto.encode("utf-8")) <= maximo:
        return [texto] if texto else []
    frases = re.split(r"(?<=[.!?…])\s+", texto)
    trozos, actual = [], ""
    for frase in frases:
        candidato = (actual + " " + frase).strip()
        if len(candidato.encode("utf-8")) <= maximo:
            actual = candidato
            continue
        if actual:
            trozos.append(actual)
        if len(frase.encode("utf-8")) <= maximo:
            actual = frase
            continue
        # una frase que no cabe sola: por comas, y si no, por palabras
        actual = ""
        for pieza in re.split(r"(?<=[,;:])\s+", frase):
            candidato = (actual + " " + pieza).strip()
            if len(candidato.encode("utf-8")) <= maximo:
                actual = candidato
                continue
            if actual:
                trozos.append(actual)
            actual = ""
            for palabra in pieza.split():
                candidato = (actual + " " + palabra).strip()
                if len(candidato.encode("utf-8")) > maximo and actual:
                    trozos.append(actual)
                    actual = palabra
                else:
                    actual = candidato
    if actual:
        trozos.append(actual)
    return trozos


def trocear_equilibrado(texto, maximo):
    """Como `trocear_por_bytes`, pero en piezas de tamano PARECIDO.

    Llenando hasta el tope la ultima pieza sale con una frase y media, y una
    pieza tan corta arranca «en frio» y se nota (09-10-2026: la union del 2:00
    fue la unica que Enrique oyo). Mismo numero de piezas, frases repartidas a
    partes iguales; si una frase no cabe sola, se queda el troceo de siempre.
    """
    base = trocear_por_bytes(texto, maximo)
    if len(base) < 2:
        return base
    frases = re.split(r"(?<=[.!?…])\s+", " ".join(str(texto or "").split()))
    tamanos = [len(f.encode("utf-8")) + 1 for f in frases]
    if max(tamanos) > maximo:
        return base
    objetivo = sum(tamanos) / float(len(base))
    piezas, actual, llevado = [], [], 0
    for frase, tamano in zip(frases, tamanos):
        if actual and llevado + tamano / 2.0 > objetivo and len(piezas) < len(base) - 1:
            piezas.append(" ".join(actual))
            actual, llevado = [], 0
        actual.append(frase)
        llevado += tamano
    piezas.append(" ".join(actual))
    if any(len(p.encode("utf-8")) > maximo for p in piezas):
        return base
    return piezas


def quitar_soplido(pcm):
    """El PCM de una pieza pasado por el quitasoplidos. -> (pcm, hecho)

    Sin ffmpeg (o con ESTUDIO_SIN_QUITASOPLIDO) devuelve el PCM tal cual: una
    voz con soplido es mejor que ninguna voz.
    """
    if not pcm or os.environ.get("ESTUDIO_SIN_QUITASOPLIDO"):
        return pcm, False
    ffmpeg = (os.environ.get("ESTUDIO_FFMPEG") or "").strip().strip('"') or shutil.which("ffmpeg")
    if not ffmpeg:
        return pcm, False
    formato = ["-f", "s16le", "-ar", str(SR), "-ac", "1"]
    # cola de silencio para que el filtro, que RETRASA la salida ~25 ms, no se
    # coma el final de la pieza al devolver el mismo largo
    cola = b"\x00\x00" * int(SR * 0.25)
    hecho = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", *formato, "-i", "pipe:0",
                            "-af", FILTRO_SOPLIDO, *formato, "pipe:1"],
                           input=pcm + cola, capture_output=True)
    if hecho.returncode != 0 or len(hecho.stdout) < len(pcm):
        return pcm, False
    entrada = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    salida = np.frombuffer(hecho.stdout[:len(hecho.stdout) & ~1], dtype="<i2")
    # las marcas de tiempo no se pueden mover: se mide el retraso y se descuenta
    retraso = _retraso(entrada, salida[:len(entrada)].astype(np.float64))
    if retraso < 0 or retraso > len(salida) - len(entrada):
        return pcm, False
    return salida[retraso:retraso + len(entrada)].astype("<i2").tobytes(), True


def _retraso(a, b, maximo=4096):
    """Cuantas muestras va `b` por detras de `a` (correlacion cruzada). -> int"""
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    c = np.fft.irfft(np.fft.rfft(b, n) * np.conj(np.fft.rfft(a, n)), n)
    ventana = np.concatenate((c[:maximo + 1], c[-maximo:]))
    k = int(np.argmax(ventana))
    return k if k <= maximo else k - len(ventana)


def _palabras(texto):
    return len(re.sub(r"\[[^\]]*\]", " ", texto).split())


# -------------------------------------------------------------- sintesis

def familia_de(modelo):
    familia = MODELOS.get(str(modelo or "").strip())
    if not familia:
        raise ValueError(f"modelo de Google desconocido: {modelo!r}. "
                         f"Validos: {', '.join(MODELOS)}")
    return familia


def cuerpo_peticion(texto, voz, modelo, idioma, estilo=None, locale=None,
                    velocidad=None):
    """El JSON de text:synthesize para una familia u otra."""
    familia = familia_de(modelo)
    locale = locale or LOCALES[familia].get(str(idioma or "en").lower(), "en-US")
    configuracion = {"audioEncoding": "LINEAR16", "sampleRateHertz": SR}
    if familia == "gemini":
        entrada = {"text": texto}
        if str(estilo or "").strip():
            entrada["prompt"] = " ".join(str(estilo).split())
        voz_json = {"languageCode": locale, "name": voz, "modelName": modelo}
    else:
        # Chirp 3 HD: sin estilo; las pausas van en `markup`
        entrada = {"markup": texto}
        voz_json = {"languageCode": locale, "name": f"{locale}-Chirp3-HD-{voz}"}
        if velocidad:
            configuracion["speakingRate"] = float(velocidad)
    return {"input": entrada, "voice": voz_json, "audioConfig": configuracion}


def _espera_de(respuesta, intento):
    cabecera = respuesta.headers.get("retry-after") if respuesta is not None else None
    if cabecera:
        try:
            return min(60.0, float(cabecera))
        except ValueError:
            pass
    return min(60.0, 2.0 ** intento + random.uniform(0, 1.0))


def sintetizar(texto, voz="Orus", modelo=MODELO_POR_DEFECTO, idioma="en",
               estilo=None, locale=None, velocidad=None):
    """Una peticion. -> (pcm s16le mono a SR, segundos)

    Reintenta lo que se arregla esperando (429, 500, 503, red); lo demas
    (400, 403) se dice tal cual: reintentar una peticion mal formada o sin
    permiso solo gasta tiempo.
    """
    if len(texto.encode("utf-8")) > 4000:
        raise ValueError("el trozo pasa de 4.000 bytes: trocealo antes")
    cuerpo = cuerpo_peticion(texto, voz, modelo, idioma, estilo, locale, velocidad)
    ultimo = None
    for intento in range(REINTENTOS):
        token, proyecto = credenciales()
        cabeceras = {"Authorization": f"Bearer {token}",
                     "x-goog-user-project": proyecto,
                     "Content-Type": "application/json"}
        respuesta = None
        try:
            respuesta = requests.post(API, headers=cabeceras, json=cuerpo,
                                      timeout=(30, 600))
        except requests.RequestException as fallo:
            ultimo = f"red: {fallo}"
        else:
            if respuesta.status_code == 200:
                contenido = base64.b64decode(respuesta.json()["audioContent"])
                pcm = _pcm_de_wav(contenido)
                return pcm, len(pcm) / (SR * 2)
            ultimo = f"HTTP {respuesta.status_code}: {respuesta.text[:300]}"
            if respuesta.status_code == 401:
                _CREDENCIALES["objeto"] = None      # token caducado: renovar
            elif respuesta.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"Google TTS: {ultimo}")
        time.sleep(_espera_de(respuesta, intento))
    raise RuntimeError(f"Google TTS no responde tras {REINTENTOS} intentos: {ultimo}")


def sintetizar_trozos(trozos, voz="Orus", modelo=MODELO_POR_DEFECTO, idioma="en",
                      locale=None, velocidad=None, avisar=None,
                      concurrencia=CONCURRENCIA):
    """Varios trozos en una sola pista igualada. -> (wav, segundos, info)

    trozos: [{"texto", "estilo", "seccion"}] en orden. "seccion" agrupa los
    que son de la misma seccion del guion (entre ellos, pausa corta; entre
    secciones, pausa de cambio de tema). Un trozo que no cabe en una peticion
    se parte por final de frase y sus partes llevan el mismo estilo.

    info: {"piezas": [...], "avisos": [...], "caracteres", "segundos_audio",
           "peticiones"}. Cada pieza dice de que trozo es, donde cae en la
    pista (t_in/t_out), su volumen original y la ganancia que se le aplico.
    """
    def sordo(valor, mensaje=""):
        return valor
    avisar = avisar if callable(avisar) else sordo

    maximo = BYTES_PIEZA_POR_MODELO.get(modelo)
    piezas = []
    for indice, trozo in enumerate(trozos):
        if isinstance(trozo, str):
            trozo = {"texto": trozo}
        partes = (trocear_equilibrado(trozo.get("texto"), maximo) if maximo
                  else trocear_por_bytes(trozo.get("texto")))
        for parte in partes:
            piezas.append({"trozo": indice, "texto": parte,
                           "estilo": trozo.get("estilo"),
                           "seccion": trozo.get("seccion", indice)})
    if not piezas:
        raise ValueError("no hay texto que sintetizar")

    hechas = [0]

    def una(pieza):
        pcm, _ = sintetizar(pieza["texto"], voz, modelo, idioma, pieza["estilo"],
                            locale, velocidad)
        pcm, pieza["sin_soplido"] = quitar_soplido(pcm)
        hechas[0] += 1
        avisar(hechas[0] / len(piezas), f"{hechas[0]} de {len(piezas)} trozos de voz")
        return recortar_silencios(pcm)

    with ThreadPoolExecutor(max_workers=max(1, int(concurrencia))) as pool:
        audios = list(pool.map(una, piezas))

    # Medir ANTES de igualar: es lo que dice si la costura se va a notar.
    for pieza, pcm in zip(piezas, audios):
        pieza["nivel_db"] = nivel_db(pcm)
        segundos = len(pcm) / (SR * 2)
        pieza["ritmo"] = round(_palabras(pieza["texto"]) / segundos, 3) if segundos else None

    avisos = []
    for anterior, siguiente in zip(piezas, piezas[1:]):
        if anterior["nivel_db"] is not None and siguiente["nivel_db"] is not None:
            salto = siguiente["nivel_db"] - anterior["nivel_db"]
            if abs(salto) >= AVISO_VOLUMEN_DB:
                avisos.append({"tipo": "volumen", "entre": [anterior["trozo"], siguiente["trozo"]],
                               "db": round(salto, 1)})
        if anterior["ritmo"] and siguiente["ritmo"]:
            cambio = siguiente["ritmo"] / anterior["ritmo"] - 1.0
            if abs(cambio) >= AVISO_RITMO:
                avisos.append({"tipo": "ritmo", "entre": [anterior["trozo"], siguiente["trozo"]],
                               "cambio": round(cambio, 3)})

    pista, reloj = [], 0.0
    for posicion, (pieza, pcm) in enumerate(zip(piezas, audios)):
        ganancia = 0.0
        if pieza["nivel_db"] is not None:
            ganancia = max(-AJUSTE_MAXIMO_DB,
                           min(AJUSTE_MAXIMO_DB, NIVEL_OBJETIVO_DB - pieza["nivel_db"]))
            pcm = aplicar_ganancia(pcm, ganancia)
        if posicion:
            mismo = piezas[posicion - 1]["seccion"] == pieza["seccion"]
            hueco = silencio(PAUSA_DENTRO_S if mismo else PAUSA_ENTRE_SECCIONES_S)
            pista.append(hueco)
            reloj += len(hueco) / (SR * 2)
        pieza["ganancia_db"] = round(ganancia, 2)
        pieza["t_in"] = round(reloj, 3)
        reloj += len(pcm) / (SR * 2)
        pieza["t_out"] = round(reloj, 3)
        if pieza["nivel_db"] is not None:
            pieza["nivel_db"] = round(pieza["nivel_db"], 2)
        pista.append(pcm)

    pcm_total = b"".join(pista)
    segundos = len(pcm_total) / (SR * 2)
    info = {"piezas": [{k: v for k, v in p.items() if k != "texto"} | {"caracteres": len(p["texto"])}
                       for p in piezas],
            "avisos": avisos,
            "caracteres": sum(len(p["texto"]) for p in piezas),
            "segundos_audio": round(segundos, 3),
            "peticiones": len(piezas),
            "modelo": modelo, "voz": voz}
    return wav_desde_pcm(pcm_total), segundos, info


# ----------------------------------------------- uso suelto (como voz_cartesia)

def _motor_hermano(carpeta, fichero):
    """Otro motor por ruta: el alineador y el reparto de palabras de Cartesia."""
    import importlib.util
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        carpeta, fichero)
    especificacion = importlib.util.spec_from_file_location(f"_{carpeta}", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def sintetizar_continuo(plan, out_dir, idioma="en", nombre="narracion.wav",
                        hueco_minimo=1.0, voz=None, modelo=MODELO_POR_DEFECTO,
                        estilo=None):
    """La misma salida que voz_cartesia.sintetizar_continuo: pista + audio_meta.json."""
    cartesia = _motor_hermano("voz_cartesia", "voz.py")
    alineador = _motor_hermano("alinear_voz", "alinear.py")
    voz = voz or VOCES.get(idioma, VOCES["en"])["id"]
    os.makedirs(out_dir, exist_ok=True)
    escenas = plan["escenas"]
    transcript = " ".join((e.get("narracion") or "").strip() for e in escenas
                          if (e.get("narracion") or "").strip())
    if not transcript:
        raise SystemExit("El plan no tiene narracion")
    wav, duracion, info = sintetizar_trozos([{"texto": transcript, "estilo": estilo}],
                                            voz=voz, modelo=modelo, idioma=idioma)
    destino = os.path.join(out_dir, nombre)
    with open(destino, "wb") as fh:
        fh.write(wav)
    palabras = alineador.alinear(destino, transcript, idioma)["palabras"]
    reparto = cartesia._repartir_palabras(escenas, palabras)
    if hueco_minimo > 0:
        wav, desplazamientos = cartesia.espaciar(wav, palabras, reparto, escenas,
                                                 hueco_minimo)
        for tramo in reparto.values():
            for p in tramo:
                p["s"] = cartesia.aplicar_desplazamiento(p["s"], desplazamientos)
                p["e"] = cartesia.aplicar_desplazamiento(p["e"], desplazamientos)
        if desplazamientos:
            duracion += desplazamientos[-1]["retardo"]
        with open(destino, "wb") as fh:
            fh.write(wav)
    meta = {"modo": "continuo", "modelo": modelo, "proveedor": "google",
            "voz": {"id": voz, "nombre": voz}, "idioma": idioma, "archivo": nombre,
            "duracion": round(duracion, 3), "transcript": transcript, "escenas": []}
    for escena in escenas:
        tramo = reparto.get(escena["id"], [])
        meta["escenas"].append({
            "id": escena["id"],
            "duracion": round(tramo[-1]["e"] - tramo[0]["s"], 3) if tramo else 0.0,
            "t_primera_palabra": round(tramo[0]["s"], 3) if tramo else None,
            "t_ultima_palabra": round(tramo[-1]["e"], 3) if tramo else None,
            "palabras": tramo})
    with open(os.path.join(out_dir, "audio_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    return meta


def sintetizar_plan(plan, out_dir, idioma="en", voz=None, modelo=MODELO_POR_DEFECTO,
                    estilo=None):
    """Un wav por escena, con marcas del alineador (como voz_cartesia)."""
    alineador = _motor_hermano("alinear_voz", "alinear.py")
    voz = voz or VOCES.get(idioma, VOCES["en"])["id"]
    os.makedirs(out_dir, exist_ok=True)
    meta = {"modelo": modelo, "proveedor": "google", "voz": {"id": voz, "nombre": voz},
            "idioma": idioma, "escenas": []}
    for escena in plan["escenas"]:
        texto = (escena.get("narracion") or "").strip()
        if not texto:
            meta["escenas"].append({"id": escena["id"], "archivo": None,
                                    "duracion": 0.0, "palabras": []})
            continue
        wav, duracion, _ = sintetizar_trozos([{"texto": texto, "estilo": estilo}],
                                             voz=voz, modelo=modelo, idioma=idioma)
        destino = os.path.join(out_dir, f"{escena['id']}.wav")
        with open(destino, "wb") as fh:
            fh.write(wav)
        meta["escenas"].append({"id": escena["id"], "archivo": os.path.basename(destino),
                                "duracion": round(duracion, 3),
                                "palabras": alineador.alinear(destino, texto, idioma)["palabras"]})
    with open(os.path.join(out_dir, "audio_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    return meta


def main():
    parser = argparse.ArgumentParser(description="Voz con Google Cloud TTS")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--idioma", default="en")
    parser.add_argument("--voz")
    parser.add_argument("--modelo", default=MODELO_POR_DEFECTO, choices=sorted(MODELOS))
    parser.add_argument("--estilo", help="instruccion de estilo (solo Gemini)")
    parser.add_argument("--modo", default="continuo", choices=["continuo", "por_escena"])
    parser.add_argument("--hueco", type=float, default=1.0)
    args = parser.parse_args()
    with open(args.plan, "r", encoding="utf-8") as fh:
        plan = json.load(fh)
    if args.modo == "continuo":
        sintetizar_continuo(plan, args.out, args.idioma, hueco_minimo=args.hueco,
                            voz=args.voz, modelo=args.modelo, estilo=args.estilo)
    else:
        sintetizar_plan(plan, args.out, args.idioma, voz=args.voz, modelo=args.modelo,
                        estilo=args.estilo)


if __name__ == "__main__":
    sys.exit(main())
