"""
Motor de voz: ElevenLabs, con marcas de tiempo POR CARACTER convertidas a
marcas por palabra con la forma de siempre: [{"w","s","e"}].

La voz premium del Estudio (Google es la de diario): mismas secciones que los
demas proveedores, y cada peticion lleva el texto de antes y el de despues
(`previous_text` / `next_text`) para que la entonacion no arranque en frio en
cada costura -- la «una sola toma» del autor, hecha con lo que da esta API.

Lo que hay que saber de la API, medido y documentado
----------------------------------------------------
- Endpoint `with-timestamps`: devuelve el audio en base64 y la alineacion por
  caracter del texto ENVIADO (`alignment`). Se usa esa y no
  `normalized_alignment`, que es del texto ya normalizado («$5» -> «five
  dollars») y no casa con las palabras del guion.
- Formato: MP3 a 44,1 kHz. El PCM/WAV a 44,1 kHz es solo del plan Pro; el
  plan Starter recibe MP3 y aqui se decodifica con ffmpeg a PCM 16 bits.
- Se cobra en CREDITOS del plan, no en dolares. Cada respuesta trae la
  cabecera `character-cost` con lo que costo: eso es lo que se anota. La
  suscripcion NO sirve para medir: su contador tarda en moverse (medido: 0
  creditos justo despues de una toma de 64).
- Sin etiquetas: el texto va limpio. Las pausas entre bloques las pone el
  Estudio despues (motor.espaciar), igual que con los demas.

Interfaz
--------
    sintetizar(texto, voz, modelo, ...) -> (pcm, segundos, marcas o None, creditos)
    sintetizar_trozos(trozos, voz, modelo, ...) -> (wav, segundos, palabras o None, info)
      (None: la alineacion no casaba con el texto; las pone el alineador local)
    listar_voces() -> [ficha] ; suscripcion() -> dict ; cargar_api_key()
    wav_desde_pcm ; SR ; MODELOS ; VOCES
    sintetizar_continuo / sintetizar_plan: como voz_cartesia, para el uso suelto.

Contrato: no importa nada de la aplicacion. La clave sale del entorno
(ELEVENLABS_API_KEY), del almacen de Configuracion (`secretos/claves.json`,
bloque "elevenlabs") o del .env de secretos.
"""
import argparse
import base64
import json
import os
import re
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

API = "https://api.elevenlabs.io"
SR = 44100
FORMATO = "mp3_44100_128"

#: Modelos: creditos por caracter y tope de caracteres por peticion.
#:
#: LO QUE SE ANOTA NO SALE DE AQUI: cada respuesta trae la cabecera
#: `character-cost` con lo que costo de verdad, y eso es lo que se mide. Esta
#: tabla solo sirve para ESTIMAR antes de grabar (y no empezar una toma que no
#: cabe en el saldo). Flash/Turbo: 0,2 MEDIDO el 02-10-2026 (64 creditos por
#: 322 caracteres; la documentacion decia 0,5). Multilingual, v3 y v4: sin
#: medir; 1 por caracter es el techo de la web y aqui hace de tope prudente.
MODELOS = {
    "eleven_flash_v2_5": {"creditos": 0.2, "max": 40000, "costuras": True},
    "eleven_turbo_v2_5": {"creditos": 0.2, "max": 40000, "costuras": True},
    "eleven_multilingual_v2": {"creditos": 1.0, "max": 10000, "costuras": True},
    "eleven_v3": {"creditos": 1.0, "max": 5000, "costuras": False},
    "eleven_v4": {"creditos": 1.0, "max": 10000, "costuras": False},
}
MODELO_POR_DEFECTO = "eleven_flash_v2_5"

#: Voces premade de la libreria publica (sirven en cualquier plan). La del canal
#: se elige de la lista de la cuenta, clonadas incluidas.
VOCES = {
    "en": {"id": "pNInz6obpgDQGcFmaJgB", "nombre": "Adam"},
    "es": {"id": "pNInz6obpgDQGcFmaJgB", "nombre": "Adam"},
}

#: Cuanto texto vecino se manda como contexto en cada costura.
CONTEXTO_CARACTERES = 400

#: Pausa entre secciones (cambio de tema) y entre partes de una misma seccion.
PAUSA_ENTRE_SECCIONES_S = 0.6
PAUSA_DENTRO_S = 0.35

#: Peticiones a la vez. El plan Starter admite 3 concurrentes; con 2 sobra hueco
#: para una escucha mientras se graba.
CONCURRENCIA = 2
REINTENTOS = 5

CARPETA_SECRETOS = os.environ.get("ESTUDIO_SECRETOS") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "secretos")
RUTA_CLAVES = os.path.join(CARPETA_SECRETOS, "claves.json")
RUTA_ENV = os.path.join(CARPETA_SECRETOS, ".env")


class SinCreditos(RuntimeError):
    """No quedan creditos para lo que se pide. Reintentar no lo arregla."""


# ----------------------------------------------------------------- clave

def cargar_api_key():
    clave = os.environ.get("ELEVENLABS_API_KEY")
    if clave:
        return clave.strip()
    try:
        with open(RUTA_CLAVES, "r", encoding="utf-8-sig") as fh:
            datos = json.load(fh)
        ficha = datos.get("elevenlabs") if isinstance(datos, dict) else None
        if isinstance(ficha, dict) and str(ficha.get("clave") or "").strip():
            return str(ficha["clave"]).strip()
    except (OSError, ValueError):
        pass
    if os.path.exists(RUTA_ENV):
        with open(RUTA_ENV, "r", encoding="utf-8-sig") as fh:
            for linea in fh:
                encaje = re.match(r"^ELEVENLABS_API_KEY=(.*)$", linea.strip())
                if encaje:
                    return encaje.group(1).strip()
    raise SystemExit("No encuentro la clave de ElevenLabs. Ponla en la pantalla de "
                     f"Configuracion (se guarda en {RUTA_CLAVES}).")


def _cabeceras(clave=None):
    return {"xi-api-key": clave or cargar_api_key(), "Content-Type": "application/json"}


def _get(ruta, params=None):
    respuesta = requests.get(f"{API}{ruta}", headers=_cabeceras(), params=params, timeout=60)
    if respuesta.status_code != 200:
        raise RuntimeError(f"ElevenLabs {ruta} HTTP {respuesta.status_code}: "
                           f"{respuesta.text[:300]}")
    return respuesta.json()


def suscripcion():
    """Los creditos del plan: usados, limite, restantes y cuando se renuevan."""
    datos = _get("/v1/user/subscription")
    usados = int(datos.get("character_count") or 0)
    limite = int(datos.get("character_limit") or 0)
    return {"plan": datos.get("tier"), "usados": usados, "limite": limite,
            "restantes": max(0, limite - usados),
            "renovacion_unix": datos.get("next_character_count_reset_unix"),
            "estado": datos.get("status")}


def creditos_estimados(caracteres, modelo):
    """Creditos que costaria ese texto, o None si el modelo no publica precio."""
    precio = (MODELOS.get(modelo) or {}).get("creditos")
    return None if precio is None else int(round(caracteres * precio))


def listar_voces():
    """Las voces de la cuenta (las propias y clonadas primero) y las premade."""
    fichas, token = [], None
    for _ in range(20):
        params = {"page_size": 100}
        if token:
            params["next_page_token"] = token
        datos = _get("/v2/voices", params)
        for cruda in datos.get("voices") or []:
            etiquetas = cruda.get("labels") or {}
            categoria = cruda.get("category") or ""
            fichas.append({
                "id": cruda.get("voice_id"), "nombre": cruda.get("name") or cruda.get("voice_id"),
                "descripcion": " · ".join(str(v) for v in (etiquetas.get("description"),
                                                           etiquetas.get("accent"),
                                                           etiquetas.get("age")) if v),
                "idioma": str(etiquetas.get("language") or "").lower()[:2],
                "genero": etiquetas.get("gender") or "", "pais": "",
                "categoria": categoria,
                "publica": categoria in ("premade", "professional_public"),
                "proveedor": "elevenlabs"})
        if not datos.get("has_more"):
            break
        token = datos.get("next_page_token")
        if not token:
            break
    fichas.sort(key=lambda f: (f["publica"], f["nombre"].lower()))
    return fichas


# ---------------------------------------------------------------- audio

def wav_desde_pcm(pcm: bytes) -> bytes:
    cabecera = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    cabecera += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16)
    cabecera += b"data" + struct.pack("<I", len(pcm))
    return cabecera + pcm


def _ffmpeg():
    return os.environ.get("ESTUDIO_FFMPEG") or "ffmpeg"


def mp3_a_pcm(mp3):
    """MP3 -> PCM s16le mono a SR, con ffmpeg (el plan Starter no da PCM a 44,1)."""
    extra = {}
    if os.name == "nt":
        extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    hecho = subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error",
                            "-i", "pipe:0", "-f", "s16le", "-acodec", "pcm_s16le",
                            "-ac", "1", "-ar", str(SR), "pipe:1"],
                           input=mp3, capture_output=True, timeout=300, **extra)
    if hecho.returncode != 0 or not hecho.stdout:
        raise RuntimeError(f"ffmpeg no ha podido decodificar el MP3: "
                           f"{hecho.stderr.decode('utf-8', 'replace')[:300]}")
    return hecho.stdout


def silencio(segundos):
    return b"\x00\x00" * int(SR * segundos)


# --------------------------------------------------------------- marcas

def _hay_letra(token):
    return bool(re.sub(r"[^\w]", "", token).replace("_", ""))


def palabras_de_alineacion(texto, alineacion, desde=0.0):
    """Alineacion por caracter -> [{"w","s","e"}] con las palabras del TEXTO.

    Cada palabra (separada por espacios) va del inicio de su primer caracter al
    final del ultimo. Un token que es solo puntuacion se pega al anterior: es
    lo que hacen el reparto y el alineador, y lo que mira segmentar para saber
    donde se puede cortar un plano.
    """
    caracteres = (alineacion or {}).get("characters") or []
    inicios = (alineacion or {}).get("character_start_times_seconds") or []
    finales = (alineacion or {}).get("character_end_times_seconds") or []
    if "".join(caracteres).strip() != texto.strip():
        # La API alinea el texto enviado tal cual; si no casa, no se adivina:
        # None, y quien llama pone las marcas con el alineador local.
        return None
    palabras, actual = [], None
    for caracter, ini, fin in zip(caracteres, inicios, finales):
        if caracter.isspace():
            if actual:
                palabras.append(actual)
                actual = None
            continue
        if actual is None:
            actual = {"w": "", "s": float(ini), "e": float(fin)}
        actual["w"] += caracter
        actual["e"] = float(fin)
    if actual:
        palabras.append(actual)
    salida = []
    for palabra in palabras:
        if not _hay_letra(palabra["w"]) and salida:
            salida[-1]["w"] += palabra["w"]
            salida[-1]["e"] = max(salida[-1]["e"], palabra["e"])
            continue
        salida.append(palabra)
    return [{"w": p["w"], "s": round(p["s"] + desde, 3), "e": round(p["e"] + desde, 3)}
            for p in salida]


# -------------------------------------------------------------- sintesis

def trocear(texto, maximo):
    """Parte por final de frase un texto que no cabe en una peticion."""
    texto = " ".join(str(texto or "").split())
    if len(texto) <= maximo:
        return [texto] if texto else []
    trozos, actual = [], ""
    for frase in re.split(r"(?<=[.!?…])\s+", texto):
        candidato = (actual + " " + frase).strip()
        if len(candidato) <= maximo or not actual:
            actual = candidato
        else:
            trozos.append(actual)
            actual = frase
    if actual:
        trozos.append(actual)
    return trozos


def _espera(respuesta, intento):
    cabecera = respuesta.headers.get("retry-after") if respuesta is not None else None
    try:
        return min(60.0, float(cabecera)) if cabecera else min(30.0, 2.0 ** intento)
    except ValueError:
        return min(30.0, 2.0 ** intento)


def sintetizar(texto, voz, modelo=MODELO_POR_DEFECTO, idioma=None, anterior="",
               siguiente="", velocidad=None):
    """Una peticion. -> (pcm, segundos, marcas relativas o None, creditos o None)

    Los creditos son los de la cabecera `character-cost`: lo que costo DE
    VERDAD esta peticion.
    """
    ficha = MODELOS.get(modelo)
    if not ficha:
        raise ValueError(f"modelo de ElevenLabs desconocido: {modelo!r}")
    if len(texto) > ficha["max"]:
        raise ValueError(f"el trozo pasa de {ficha['max']} caracteres: trocealo antes")
    cuerpo = {"text": texto, "model_id": modelo}
    if idioma and modelo in ("eleven_flash_v2_5", "eleven_turbo_v2_5"):
        cuerpo["language_code"] = idioma
    if ficha["costuras"]:
        if anterior:
            cuerpo["previous_text"] = anterior[-CONTEXTO_CARACTERES:]
        if siguiente:
            cuerpo["next_text"] = siguiente[:CONTEXTO_CARACTERES]
    if velocidad:
        cuerpo["voice_settings"] = {"speed": max(0.7, min(1.2, float(velocidad)))}
    url = f"{API}/v1/text-to-speech/{voz}/with-timestamps"
    ultimo = None
    for intento in range(REINTENTOS):
        respuesta = None
        try:
            respuesta = requests.post(url, headers=_cabeceras(), json=cuerpo,
                                      params={"output_format": FORMATO}, timeout=(30, 600))
        except requests.RequestException as fallo:
            ultimo = f"red: {fallo}"
        else:
            if respuesta.status_code == 200:
                datos = respuesta.json()
                pcm = mp3_a_pcm(base64.b64decode(datos["audio_base64"]))
                try:
                    creditos = int(respuesta.headers.get("character-cost"))
                except (TypeError, ValueError):
                    creditos = None
                return (pcm, len(pcm) / (SR * 2),
                        palabras_de_alineacion(texto, datos.get("alignment")), creditos)
            ultimo = f"HTTP {respuesta.status_code}: {respuesta.text[:300]}"
            if "quota_exceeded" in respuesta.text:
                raise SinCreditos(f"ElevenLabs: no quedan creditos ({ultimo})")
            if respuesta.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"ElevenLabs: {ultimo}")
        time.sleep(_espera(respuesta, intento))
    raise RuntimeError(f"ElevenLabs no responde tras {REINTENTOS} intentos: {ultimo}")


def sintetizar_trozos(trozos, voz, modelo=MODELO_POR_DEFECTO, idioma=None,
                      velocidad=None, avisar=None, concurrencia=CONCURRENCIA,
                      comprobar_creditos=True, antes="", despues=""):
    """Varios trozos en una pista, con sus marcas. -> (wav, segundos, palabras, info)

    trozos: [{"texto", "seccion"}] en orden. Antes de pagar nada se mira que
    queden creditos para todo (cortarse a mitad de video deja pagada la
    mitad); lo gastado DE VERDAD es la suma de la cabecera de cada peticion.

    `antes` / `despues`: el texto que rodea a estos trozos en el video, cuando
    se graba solo una parte (regrabar una seccion). Va como contexto de la
    primera y la ultima pieza; no se locuta ni se cobra como sintesis.
    """
    def sordo(valor, mensaje=""):
        return valor
    avisar = avisar if callable(avisar) else sordo
    maximo = MODELOS[modelo]["max"]
    piezas = []
    for indice, trozo in enumerate(trozos):
        if isinstance(trozo, str):
            trozo = {"texto": trozo}
        for parte in trocear(trozo.get("texto"), maximo):
            piezas.append({"trozo": indice, "texto": parte,
                           "seccion": trozo.get("seccion", indice)})
    if not piezas:
        raise ValueError("no hay texto que sintetizar")
    caracteres = sum(len(p["texto"]) for p in piezas)

    saldo = None
    if comprobar_creditos:
        saldo = suscripcion()
        necesarios = creditos_estimados(caracteres, modelo)
        if necesarios is not None and necesarios > saldo["restantes"]:
            raise SinCreditos(f"esta toma necesita ~{necesarios} creditos de ElevenLabs y "
                              f"quedan {saldo['restantes']} este mes")

    hechas = [0]

    def una(posicion):
        pieza = piezas[posicion]
        anterior = piezas[posicion - 1]["texto"] if posicion else antes
        siguiente = piezas[posicion + 1]["texto"] if posicion + 1 < len(piezas) else despues
        resultado = sintetizar(pieza["texto"], voz, modelo, idioma, anterior, siguiente,
                               velocidad)
        hechas[0] += 1
        avisar(hechas[0] / len(piezas), f"{hechas[0]} de {len(piezas)} trozos de voz")
        return resultado

    with ThreadPoolExecutor(max_workers=max(1, int(concurrencia))) as pool:
        resultados = list(pool.map(una, range(len(piezas))))

    pista, palabras, reloj = [], [], 0.0
    costes = [r[3] for r in resultados]
    for posicion, (pieza, (pcm, segundos, marcas, _c)) in enumerate(zip(piezas, resultados)):
        if posicion:
            mismo = piezas[posicion - 1]["seccion"] == pieza["seccion"]
            hueco = silencio(PAUSA_DENTRO_S if mismo else PAUSA_ENTRE_SECCIONES_S)
            pista.append(hueco)
            reloj += len(hueco) / (SR * 2)
        pieza["t_in"] = round(reloj, 3)
        if marcas is None or palabras is None:
            palabras = None                 # una pieza sin marcas: alinea quien llama
        else:
            palabras.extend({"w": m["w"], "s": round(m["s"] + reloj, 3),
                             "e": round(m["e"] + reloj, 3)} for m in marcas)
        reloj += segundos
        pieza["t_out"] = round(reloj, 3)
        pista.append(pcm)

    info = {"piezas": [{k: v for k, v in p.items() if k != "texto"} | {"caracteres": len(p["texto"])}
                       for p in piezas],
            "caracteres": caracteres, "peticiones": len(piezas),
            "creditos_estimados": creditos_estimados(caracteres, modelo),
            "modelo": modelo, "voz": voz}
    # lo medido: la suma de las cabeceras. Si alguna no vino, None y quien anota
    # usa la estimacion (marcada como tal).
    info["creditos"] = sum(costes) if all(c is not None for c in costes) else None
    if saldo is not None and info["creditos"] is not None:
        info["creditos_restantes"] = max(0, saldo["restantes"] - info["creditos"])
    pcm_total = b"".join(pista)
    return wav_desde_pcm(pcm_total), len(pcm_total) / (SR * 2), palabras, info


# ----------------------------------------------- uso suelto (como voz_cartesia)

def _motor_hermano(carpeta, fichero):
    import importlib.util
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        carpeta, fichero)
    especificacion = importlib.util.spec_from_file_location(f"_{carpeta}", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def sintetizar_continuo(plan, out_dir, idioma="en", nombre="narracion.wav",
                        hueco_minimo=1.0, voz=None, modelo=MODELO_POR_DEFECTO):
    """La misma salida que voz_cartesia.sintetizar_continuo: pista + audio_meta.json."""
    cartesia = _motor_hermano("voz_cartesia", "voz.py")
    voz = voz or VOCES.get(idioma, VOCES["en"])["id"]
    os.makedirs(out_dir, exist_ok=True)
    escenas = plan["escenas"]
    trozos = [{"texto": (e.get("narracion") or "").strip(), "seccion": e["id"]}
              for e in escenas if (e.get("narracion") or "").strip()]
    if not trozos:
        raise SystemExit("El plan no tiene narracion")
    wav, duracion, palabras, info = sintetizar_trozos(trozos, voz, modelo, idioma)
    reparto = cartesia._repartir_palabras(escenas, palabras)
    if hueco_minimo > 0:
        wav, desplazamientos = cartesia.espaciar(wav, palabras, reparto, escenas, hueco_minimo)
        for tramo in reparto.values():
            for p in tramo:
                p["s"] = cartesia.aplicar_desplazamiento(p["s"], desplazamientos)
                p["e"] = cartesia.aplicar_desplazamiento(p["e"], desplazamientos)
        if desplazamientos:
            duracion += desplazamientos[-1]["retardo"]
    destino = os.path.join(out_dir, nombre)
    with open(destino, "wb") as fh:
        fh.write(wav)
    meta = {"modo": "continuo", "modelo": modelo, "proveedor": "elevenlabs",
            "voz": {"id": voz, "nombre": voz}, "idioma": idioma, "archivo": nombre,
            "duracion": round(duracion, 3), "creditos": info.get("creditos"),
            "transcript": " ".join(t["texto"] for t in trozos), "escenas": []}
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


def sintetizar_plan(plan, out_dir, idioma="en", voz=None, modelo=MODELO_POR_DEFECTO):
    """Un wav por escena, con sus marcas (como voz_cartesia)."""
    voz = voz or VOCES.get(idioma, VOCES["en"])["id"]
    os.makedirs(out_dir, exist_ok=True)
    meta = {"modelo": modelo, "proveedor": "elevenlabs", "voz": {"id": voz, "nombre": voz},
            "idioma": idioma, "escenas": []}
    for escena in plan["escenas"]:
        texto = (escena.get("narracion") or "").strip()
        if not texto:
            meta["escenas"].append({"id": escena["id"], "archivo": None,
                                    "duracion": 0.0, "palabras": []})
            continue
        pcm, duracion, marcas, _creditos = sintetizar(texto, voz, modelo, idioma)
        destino = os.path.join(out_dir, f"{escena['id']}.wav")
        with open(destino, "wb") as fh:
            fh.write(wav_desde_pcm(pcm))
        meta["escenas"].append({"id": escena["id"], "archivo": os.path.basename(destino),
                                "duracion": round(duracion, 3), "palabras": marcas})
    with open(os.path.join(out_dir, "audio_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    return meta


def main():
    parser = argparse.ArgumentParser(description="Voz con ElevenLabs")
    parser.add_argument("--plan")
    parser.add_argument("--out")
    parser.add_argument("--idioma", default="en")
    parser.add_argument("--voz")
    parser.add_argument("--modelo", default=MODELO_POR_DEFECTO, choices=sorted(MODELOS))
    parser.add_argument("--modo", default="continuo", choices=["continuo", "por_escena"])
    parser.add_argument("--hueco", type=float, default=1.0)
    parser.add_argument("--creditos", action="store_true", help="dice los creditos y sale")
    args = parser.parse_args()
    if args.creditos:
        print(json.dumps(suscripcion(), indent=2))
        return
    if not (args.plan and args.out):
        parser.error("hace falta --plan y --out (o --creditos)")
    with open(args.plan, "r", encoding="utf-8") as fh:
        plan = json.load(fh)
    if args.modo == "continuo":
        sintetizar_continuo(plan, args.out, args.idioma, hueco_minimo=args.hueco,
                            voz=args.voz, modelo=args.modelo)
    else:
        sintetizar_plan(plan, args.out, args.idioma, voz=args.voz, modelo=args.modelo)


if __name__ == "__main__":
    sys.exit(main())
