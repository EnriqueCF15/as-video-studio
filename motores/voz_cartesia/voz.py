"""
Motor de voz: Cartesia sonic-3 con marcas de tiempo por palabra.

Se usa la ruta SSE en lugar de /tts/bytes porque devuelve word timestamps, y son
esos timestamps los que permiten sincronizar la animacion con la narracion sin
adivinar. Si SSE falla se cae a /tts/bytes, que da audio pero sin marcas: en ese
caso el consumidor tendra que repartir el tiempo proporcionalmente.

Uso:
    python voz.py --plan plan.json --out ./audio [--idioma es]

Espera un JSON con una lista "escenas", cada una con "id" y "narracion".
Escribe <out>/<id>.wav y <out>/audio_meta.json.
"""
import argparse
import base64
import json
import math
import os
import re
import struct
import sys

import requests

API_SSE = "https://api.cartesia.ai/tts/sse"
API_BYTES = "https://api.cartesia.ai/tts/bytes"
API_VERSION = "2025-04-16"
MODELO = "sonic-3"
SR = 44100

VOCES = {
    "es": {"id": "b042270c-d46f-4d4f-8fb0-7dd7c5fe5615", "nombre": "Hector - Tour Leader"},
    "en": {"id": "a892d232-f705-40d7-bc8d-e368b295ec2a", "nombre": "Harlan - Vintage Tone"},
}

#: La carpeta de secretos se puede mover con ESTUDIO_SECRETOS: en el servidor
#: cada cuenta tiene la suya. Sin la variable, el valor es el de siempre.
CARPETA_SECRETOS = os.environ.get("ESTUDIO_SECRETOS") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "secretos")
RUTAS_ENV = [
    os.path.join(CARPETA_SECRETOS, ".env"),
]

#: El almacen que escribe la pantalla de configuracion del Estudio
#: (pasos/claves.py). Se lee por CONTRATO -- una ruta y una forma --, nunca
#: importando codigo del Estudio: este motor tambien lo usan otras cosas.
RUTA_CLAVES = os.path.join(CARPETA_SECRETOS, "claves.json")


def _del_almacen():
    if not os.path.exists(RUTA_CLAVES):
        return ""
    try:
        with open(RUTA_CLAVES, "r", encoding="utf-8-sig") as fh:
            datos = json.load(fh)
    except (OSError, ValueError):
        return ""
    ficha = datos.get("cartesia") if isinstance(datos, dict) else None
    if isinstance(ficha, dict):
        return str(ficha.get("clave") or "").strip()
    return ""


def cargar_api_key():
    """La clave nunca se pasa por linea de comandos: se lee del entorno, del
    almacen de claves del Estudio, o de los .env conocidos."""
    clave = os.environ.get("CARTESIA_API_KEY")
    if clave:
        return clave.strip()
    clave = _del_almacen()
    if clave:
        return clave
    for ruta in RUTAS_ENV:
        if not os.path.exists(ruta):
            continue
        # utf-8-sig y no utf-8: el .env de secrets empieza con BOM, asi que si
        # alguien pone CARTESIA_API_KEY en su primera linea el regex no
        # enganchaba y el fallo salia como "no encuentro la clave", que manda a
        # buscar al sitio equivocado.
        with open(ruta, "r", encoding="utf-8-sig") as fh:
            for linea in fh:
                m = re.match(r"^CARTESIA_API_KEY=(.*)$", linea.strip())
                if m:
                    return m.group(1).strip()
    raise SystemExit(
        "No encuentro CARTESIA_API_KEY. Ponla en la pantalla de Configuracion "
        f"(se guarda en {RUTA_CLAVES}), o en el entorno.")


def wav_desde_pcm(pcm: bytes) -> bytes:
    """Cabecera WAV PCM s16le mono sobre PCM crudo."""
    cabecera = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    cabecera += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16)
    cabecera += b"data" + struct.pack("<I", len(pcm))
    return cabecera + pcm


def _cuerpo(transcript, voz_id, idioma, timestamps):
    cuerpo = {
        "model_id": MODELO,
        "transcript": transcript,
        "voice": {"mode": "id", "id": voz_id},
        "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": SR},
        "language": idioma,
    }
    if timestamps:
        cuerpo["add_timestamps"] = True
    return cuerpo


def tts_sse(api_key, voz_id, idioma, transcript):
    cabeceras = {
        "X-API-Key": api_key,
        "Cartesia-Version": API_VERSION,
        "Content-Type": "application/json",
    }
    respuesta = requests.post(API_SSE, headers=cabeceras,
                              json=_cuerpo(transcript, voz_id, idioma, True),
                              stream=True, timeout=180)
    if respuesta.status_code != 200:
        raise RuntimeError(f"SSE HTTP {respuesta.status_code}: {respuesta.text[:200]}")

    trozos, palabras = [], []
    for linea in respuesta.iter_lines(decode_unicode=True):
        if not linea or not linea.startswith("data:"):
            continue
        try:
            evento = json.loads(linea[5:].strip())
        except json.JSONDecodeError:
            continue
        if evento.get("data"):
            trozos.append(base64.b64decode(evento["data"]))
        wt = evento.get("word_timestamps")
        if wt and wt.get("words"):
            for w, ini, fin in zip(wt["words"], wt["start"], wt["end"]):
                palabras.append({"w": w, "s": round(ini, 3), "e": round(fin, 3)})

    pcm = b"".join(trozos)
    if not pcm:
        raise RuntimeError("SSE sin audio")
    return wav_desde_pcm(pcm), len(pcm) / (SR * 2), palabras


def tts_bytes(api_key, voz_id, idioma, transcript):
    cabeceras = {
        "X-API-Key": api_key,
        "Cartesia-Version": API_VERSION,
        "Content-Type": "application/json",
    }
    respuesta = requests.post(API_BYTES, headers=cabeceras,
                              json=_cuerpo(transcript, voz_id, idioma, False),
                              timeout=180)
    if respuesta.status_code != 200:
        raise RuntimeError(f"bytes HTTP {respuesta.status_code}: {respuesta.text[:200]}")
    pcm = respuesta.content
    return wav_desde_pcm(pcm), len(pcm) / (SR * 2), None


def _normalizar(palabra):
    return re.sub(r"[^\wáéíóúüñ]", "", palabra.lower())


def _repartir_palabras(escenas, palabras):
    """Asigna a cada escena el tramo de palabras que le corresponde.

    Se recorre la lista devuelta por Cartesia en orden y se van consumiendo las
    palabras de cada escena. Se compara normalizado porque el modelo puede
    devolver la puntuacion pegada o separada, y un desajuste de un token
    desplazaria todos los cortes siguientes.
    """
    reparto = {}
    i = 0
    for escena in escenas:
        esperadas = [_normalizar(p) for p in (escena.get("narracion") or "").split()]
        esperadas = [p for p in esperadas if p]
        tramo = []
        for esperada in esperadas:
            # Ventana de tolerancia por si el modelo parte o une un token
            j = i
            while j < min(i + 3, len(palabras)):
                if _normalizar(palabras[j]["w"]) == esperada:
                    break
                j += 1
            if j < min(i + 3, len(palabras)):
                tramo.extend(palabras[i:j + 1])
                i = j + 1
            elif i < len(palabras):
                tramo.append(palabras[i])
                i += 1
        reparto[escena["id"]] = tramo
    # Lo que sobre se cuelga de la ultima escena con texto
    if i < len(palabras) and reparto:
        ultimo = [e["id"] for e in escenas if (e.get("narracion") or "").strip()]
        if ultimo:
            reparto[ultimo[-1]].extend(palabras[i:])
    return reparto


def _pcm_de_wav(wav: bytes) -> bytes:
    """Extrae el PCM saltando la cabecera de 44 bytes que escribimos nosotros."""
    return wav[44:]


#: Milisegundos de fundido al pegar el relleno. Sin esto, el salto de amplitud
#: en el empalme suena como un chasquido, que es peor que el problema original.
FUNDIDO_MS = 12


#: Ventana con la que se mide la energia dentro de una pausa (segundos).
VENTANA_SALA_S = 0.01

#: Lo que se repite para ensanchar una pausa tiene que ser RUIDO DE SALA. Si la
#: muestra elegida suena mas que el suelo de la toma por encima de este margen,
#: lo que lleva dentro es el final de la palabra --la cola de una consonante, una
#: respiracion-- y repetirlo suena a disco rayado: «break-k-k-k». Paso con la voz
#: de Google el 09-10-2026: 45 de 113 pausas de un video de 23 min lo tenian,
#: porque la muestra salia del CENTRO de la pausa y el alineador da por acabada
#: la palabra antes de que se apague. Factor de amplitud: 3,16 = 10 dB.
MARGEN_SALA = 3.16

#: Por debajo de esto (-60 dBFS) nada se oye: se acepta aunque pase del margen.
SILENCIO_SALA = 33

#: Y por encima de esto (-40 dBFS) nunca es ruido de sala, sea cual sea el suelo.
TECHO_SALA = 328

#: Si el trozo mas callado de la toma no pasa de esto (-78 dBFS), la voz calla
#: con CEROS DIGITALES --Gemini lo hace asi-- y el relleno es silencio puro: no
#: hay ruido de sala que imitar. Repetir un trocito casi mudo de esa toma sonaba
#: a ESTATICA (09-10-2026: semillas de 20 ms a -60 dB repetidas cientos de veces
#: son un zumbido), y Enrique pidio las uniones «como la del 1:30», que era
#: silencio puro.
SILENCIO_DIGITAL = 4

#: Una semilla de ruido de sala mas corta que esto, repetida, deja de sonar a
#: sala y suena a zumbido (su periodo se oye como un tono).
SEMILLA_MIN_S = 0.1

#: La voz «se ha callado» por debajo de esto (-50 dBFS), o de 12 dB sobre el
#: suelo de la toma si es mas alto. Una respiracion ronda los -35/-50 dB.
CALLADO = 103

#: Un silencio mas corto que esto no es la pausa entre dos frases sino el cierre
#: de una consonante («p», «t», «k» callan 50-100 ms). Si la union no tiene un
#: silencio asi de largo, NO se mete aire: el 09-10-2026 el alineador dio
#: «people worked» por dichas medio segundo antes de tiempo, el relleno cayo en
#: el cierre de la «p» y se oia «p- people».
PAUSA_MIN_S = 0.12


def _muestras(pcm):
    """Las muestras de 16 bits del PCM sin copiarlo (memoryview). -> secuencia de int"""
    if len(pcm) % 2:
        pcm = pcm[:-1]               # solo si sobra un byte: copiar 120 MB por pausa no
    if sys.byteorder == "little":
        return memoryview(pcm).cast("h")
    import array
    muestras = array.array("h", pcm)
    muestras.byteswap()
    return muestras


def _tramo_quieto(muestras, desde_seg, hasta_seg, largo_seg):
    """El tramo de `largo_seg` con MENOS energia dentro de [desde, hasta].

    -> (inicio, fin, rms) en muestras, o None si no cabe ni una ventana.
    """
    paso = max(1, int(SR * VENTANA_SALA_S))
    a = max(0, int(desde_seg * SR))
    b = min(len(muestras), int(hasta_seg * SR))
    n = max(1, int(round(largo_seg / VENTANA_SALA_S)))
    energias = [sum(v * v for v in muestras[i:i + paso])
                for i in range(a, b - paso + 1, paso)]
    if len(energias) < n:
        return None
    suma = sum(energias[:n])
    mejor, donde = suma, 0
    for i in range(n, len(energias)):
        suma += energias[i] - energias[i - n]
        if suma < mejor:
            mejor, donde = suma, i - n + 1
    inicio = a + donde * paso
    return inicio, inicio + n * paso, math.sqrt(mejor / float(n * paso))


def _semilla_de_sala(muestras, desde_seg, hasta_seg):
    """El trozo mas callado de una pausa, para repetirlo. -> (inicio, fin, rms) o None

    Hasta 200 ms y como mucho la mitad de la pausa: una muestra larga en una
    pausa corta acaba cogiendo la cola de la palabra aunque se elija la ventana
    mas quieta.
    """
    ancho = max(0.0, hasta_seg - desde_seg)
    if ancho < SEMILLA_MIN_S:
        return None                          # no hay pausa de la que sacar nada
    return _tramo_quieto(muestras, desde_seg, hasta_seg,
                         max(SEMILLA_MIN_S, min(0.2, ancho * 0.5)))


def _punto_de_corte(muestras, desde_seg, hasta_seg, callado=CALLADO):
    """Donde se mete el relleno: justo cuando la voz se calla. -> segundos o None

    Se busca el SILENCIO SEGUIDO MAS LARGO de la union (ventanas de 10 ms por
    debajo de `callado`), medido en el sonido y no en las marcas del alineador,
    que a veces se adelantan medio segundo. El aire entra al principio de ese
    silencio: la frase se apaga, llega el aire, y la respiracion --que va al
    final de la pausa-- queda entera y pegada a la frase siguiente. Antes iba al
    centro de la pausa, o a su ventana mas callada, y partia respiraciones y
    palabras. None si no hay un silencio de PAUSA_MIN_S: mejor una union corta
    que aire en mitad de una palabra.
    """
    paso = max(1, int(SR * VENTANA_SALA_S))
    a = max(0, int(desde_seg * SR))
    b = min(len(muestras), int(hasta_seg * SR))
    umbral = callado * callado * paso
    mejor, mejor_ini, actual, actual_ini = 0, a, 0, a
    for i in range(a, b - paso + 1, paso):
        if sum(v * v for v in muestras[i:i + paso]) <= umbral:
            if actual == 0:
                actual_ini = i
            actual += 1
            if actual > mejor:
                mejor, mejor_ini = actual, actual_ini
        else:
            actual = 0
    largo = mejor * paso / float(SR)
    if largo < PAUSA_MIN_S:
        return None
    return mejor_ini / float(SR) + min(0.03, largo / 3.0)


def _relleno_de_sala(pcm, desde_seg, hasta_seg, duracion, reserva=None, muestras=None):
    """Ruido de sala para rellenar un hueco, sacado de la PROPIA pausa.

    Antes se insertaban ceros. Una toma continua tiene su suelo de ruido, y
    cortarlo a cero durante mas de un segundo se oye como un corte: el fondo
    desaparece de golpe y vuelve. Con 88 bloques y 1,2 s de hueco eran 62 s de
    vacio digital en un video de 15 minutos, uno en cada frontera de bloque.

    La mejor muestra de ruido de sala es la de la pausa que se esta ensanchando,
    asi que se toma de ahi (su trozo mas callado) y se repite. Se alterna con su
    reverso para que la repeticion no cree un patron audible. Si ese trozo suena
    demasiado (ver MARGEN_SALA) se usa `reserva` --(inicio, fin, rms) en
    muestras: el trozo mas callado de toda la toma--, y si tampoco vale, ceros.
    Si la toma calla con ceros digitales (SILENCIO_DIGITAL), ceros siempre.
    """
    bytes_por_seg = SR * 2
    necesarios = int(duracion * bytes_por_seg) & ~1
    if necesarios <= 0:
        return b""
    muestras = muestras if muestras is not None else _muestras(pcm)
    propia = _semilla_de_sala(muestras, desde_seg, hasta_seg)
    # CEROS solo si ESTA pausa calla con ceros. Mirando la toma entera bastaba
    # una pausa muda para rellenar todas con ceros, y en una voz con soplido el
    # fondo se cortaba y volvia: «un silencio chiquito y se retoma, y ahi se nota
    # la estatica» (Enrique, 09-10-2026). `reserva` ya llega sin las mudas.
    if propia and propia[2] <= SILENCIO_DIGITAL:
        return b"\x00" * necesarios
    piso = reserva[2] if reserva else (propia[2] if propia else 0.0)
    aceptable = min(TECHO_SALA, max(piso * MARGEN_SALA, SILENCIO_SALA))
    elegida = None
    for candidata in (propia, reserva):
        if (candidata and candidata[2] <= aceptable
                and candidata[1] - candidata[0] >= int(SEMILLA_MIN_S * SR)):
            elegida = candidata
            break
    if elegida is None:
        return b"\x00" * necesarios
    semilla = pcm[elegida[0] * 2:elegida[1] * 2]

    reverso = semilla[::-1]
    # el reverso de un buffer de bytes invierte tambien los dos bytes de cada
    # muestra: se rehace por pares para que siga siendo audio y no ruido blanco
    reverso = b"".join(reverso[i:i + 2][::-1] for i in range(0, len(reverso) - 1, 2))
    trozos, largo, vuelta = [], 0, 0
    while largo < necesarios:
        pieza = semilla if vuelta % 2 == 0 else reverso
        trozos.append(pieza)
        largo += len(pieza)
        vuelta += 1
    relleno = bytearray(b"".join(trozos)[:necesarios])

    # fundido de entrada y de salida sobre el relleno
    muestras = FUNDIDO_MS * SR // 1000
    for i in range(min(muestras, len(relleno) // 2)):
        factor = i / muestras
        for pos in (i * 2, len(relleno) - 2 - i * 2):
            valor = int.from_bytes(relleno[pos:pos + 2], "little", signed=True)
            relleno[pos:pos + 2] = int(valor * factor).to_bytes(2, "little", signed=True)
    return bytes(relleno)


def espaciar(wav, palabras, reparto, escenas, hueco_minimo=1.0):
    """Ensancha los silencios ENTRE escenas sin re-sintetizar nada.

    Una lectura continua encadena las frases con pausas cortas, y algunos planos
    se quedan por debajo de dos segundos. Trocear la sintesis lo arreglaria pero
    devolveria el problema de origen: cada frase arrancando en frio. Aqui se
    conserva la toma tal cual y solo se estira el silencio en los cortes, asi que
    la prosodia dentro de cada frase queda intacta.

    Lo que se inserta es RUIDO DE SALA de la propia pausa (o silencio puro si la
    voz calla con ceros digitales, como Gemini): ver _relleno_de_sala. Entra
    donde la voz se calla de verdad, y no entra si la union no tiene un silencio
    claro: ver _punto_de_corte.

    Devuelve (wav_nuevo, desplazamientos) donde desplazamientos es el retardo
    acumulado a aplicar a cada marca segun el instante en que caiga.
    """
    pcm = _pcm_de_wav(wav)
    bytes_por_seg = SR * 2
    muestras = _muestras(pcm)

    # Uniones que necesitan aire: (silencio a insertar, fin de la frase, inicio de la siguiente)
    uniones = []
    con_voz = [e for e in escenas if reparto.get(e["id"])]
    for anterior, siguiente in zip(con_voz, con_voz[1:]):
        fin = reparto[anterior["id"]][-1]["e"]
        inicio = reparto[siguiente["id"]][0]["s"]
        falta = hueco_minimo - (inicio - fin)
        if falta > 0.01:
            uniones.append((falta, fin, inicio))

    if not uniones:
        return wav, []

    # el trozo mas callado de TODAS las pausas: el suelo de la toma, y la muestra
    # de reserva para las pausas cuyo trozo mas callado aun lleva voz
    # La reserva, sin las pausas mudas: el fondo de una voz con soplido es su
    # soplido. Pero «callado» se mide con TODAS: si la unica pausa con fondo
    # fuera una respiracion, el umbral subiria hasta ella y le meteria aire.
    semillas = [s for s in (_semilla_de_sala(muestras, d, h) for _, d, h in uniones) if s]
    con_fondo = [s for s in semillas if s[2] > SILENCIO_DIGITAL]
    reserva = min(con_fondo, key=lambda s: s[2]) if con_fondo else None
    callado = max(CALLADO, min((s[2] for s in semillas), default=0.0) * 4.0)

    # Cortes: (instante original, silencio a insertar, pausa natural). Una union
    # sin silencio claro se queda como esta (ver _punto_de_corte).
    cortes = []
    for falta, fin, inicio in uniones:
        instante = _punto_de_corte(muestras, fin, inicio, callado)
        if instante is not None:
            cortes.append((instante, falta, fin, inicio))
    if not cortes:
        return wav, []

    trozos = []
    anterior_byte = 0
    acumulado = 0.0
    desplazamientos = []
    for instante, silencio, desde, hasta in cortes:
        corte_byte = int(instante * bytes_por_seg) & ~1        # alineado a muestra
        trozos.append(pcm[anterior_byte:corte_byte])
        trozos.append(_relleno_de_sala(pcm, desde, hasta, silencio, reserva, muestras))
        anterior_byte = corte_byte
        acumulado += silencio
        desplazamientos.append({"desde": instante, "retardo": round(acumulado, 3)})
    trozos.append(pcm[anterior_byte:])

    return wav_desde_pcm(b"".join(trozos)), desplazamientos


def aplicar_desplazamiento(instante, desplazamientos):
    retardo = 0.0
    for d in desplazamientos:
        if instante >= d["desde"]:
            retardo = d["retardo"]
    return round(instante + retardo, 3)


def sintetizar_continuo(plan, out_dir, idioma="es", nombre="narracion.wav",
                        hueco_minimo=1.0):
    """UNA sola toma para todo el video.

    Sintetizar frase a frase suena artificial: cada linea arranca en frio, sin
    continuidad de entonacion ni de energia entre planos. Con una toma unica la
    prosodia fluye, y los cortes de escena se deducen despues de las marcas de
    palabra, que son exactas.
    """
    api_key = cargar_api_key()
    voz = VOCES[idioma]
    os.makedirs(out_dir, exist_ok=True)

    escenas = plan["escenas"]
    partes = [(e.get("narracion") or "").strip() for e in escenas]
    transcript = " ".join(p for p in partes if p)
    if not transcript:
        raise SystemExit("El plan no tiene narracion")

    print(f"[voz] toma unica: {len(transcript)} caracteres, "
          f"{len(transcript.split())} palabras")
    wav, duracion, palabras = tts_sse(api_key, voz["id"], idioma, transcript)
    reparto = _repartir_palabras(escenas, palabras or [])

    if hueco_minimo > 0:
        wav, desplazamientos = espaciar(wav, palabras, reparto, escenas,
                                        hueco_minimo)
        if desplazamientos:
            for tramo in reparto.values():
                for p in tramo:
                    p["s"] = aplicar_desplazamiento(p["s"], desplazamientos)
                    p["e"] = aplicar_desplazamiento(p["e"], desplazamientos)
            anadido = desplazamientos[-1]["retardo"]
            duracion += anadido
            print(f"[voz] silencio insertado en {len(desplazamientos)} cortes "
                  f"(+{anadido:.2f}s) para que ningun plano baje de {hueco_minimo}s "
                  f"de aire")

    destino = os.path.join(out_dir, nombre)
    with open(destino, "wb") as fh:
        fh.write(wav)
    meta = {
        "modo": "continuo",
        "modelo": MODELO, "api": API_VERSION, "voz": voz, "idioma": idioma,
        "archivo": nombre, "duracion": round(duracion, 3),
        "transcript": transcript,
        "escenas": [],
    }
    for escena in escenas:
        tramo = reparto.get(escena["id"], [])
        meta["escenas"].append({
            "id": escena["id"],
            "duracion": round(tramo[-1]["e"] - tramo[0]["s"], 3) if tramo else 0.0,
            "t_primera_palabra": round(tramo[0]["s"], 3) if tramo else None,
            "t_ultima_palabra": round(tramo[-1]["e"], 3) if tramo else None,
            "palabras": tramo,
        })
        n = len(tramo)
        marca = (f"{tramo[0]['s']:6.2f} -> {tramo[-1]['e']:6.2f}" if tramo
                 else "   sin narracion")
        print(f"  {escena['id']}: {marca}  {n:3d} palabras")

    ruta_meta = os.path.join(out_dir, "audio_meta.json")
    with open(ruta_meta, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    print(f"[voz] toma unica de {duracion:.2f}s -> {destino}")
    return meta


def sintetizar_plan(plan, out_dir, idioma="es"):
    api_key = cargar_api_key()
    voz = VOCES[idioma]
    os.makedirs(out_dir, exist_ok=True)

    meta = {"modelo": MODELO, "api": API_VERSION, "voz": voz, "idioma": idioma, "escenas": []}
    for escena in plan["escenas"]:
        texto = (escena.get("narracion") or "").strip()
        if not texto:
            meta["escenas"].append({"id": escena["id"], "archivo": None,
                                    "duracion": 0.0, "palabras": []})
            print(f"  {escena['id']}: sin narracion, se omite")
            continue
        try:
            wav, duracion, palabras = tts_sse(api_key, voz["id"], idioma, texto)
        except Exception as exc:
            print(f"  {escena['id']}: SSE fallo ({exc}) -> fallback bytes")
            wav, duracion, palabras = tts_bytes(api_key, voz["id"], idioma, texto)

        destino = os.path.join(out_dir, f"{escena['id']}.wav")
        with open(destino, "wb") as fh:
            fh.write(wav)
        meta["escenas"].append({
            "id": escena["id"],
            "archivo": os.path.basename(destino),
            "duracion": round(duracion, 3),
            "palabras": palabras or [],
        })
        n = len(palabras) if palabras else 0
        print(f"  {escena['id']}: {duracion:6.2f}s  {n:3d} palabras con marca")

    ruta_meta = os.path.join(out_dir, "audio_meta.json")
    with open(ruta_meta, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    total = sum(e["duracion"] for e in meta["escenas"])
    print(f"[voz] total narracion {total:.1f}s en {len(meta['escenas'])} escenas -> {ruta_meta}")
    return meta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--idioma", default="es", choices=sorted(VOCES))
    parser.add_argument("--modo", default="continuo",
                        choices=["continuo", "por_escena"],
                        help="continuo: una sola toma (recomendado). "
                             "por_escena: un wav por escena, suena artificial")
    parser.add_argument("--hueco", type=float, default=1.0,
                        help="silencio minimo entre escenas, en segundos")
    args = parser.parse_args()

    with open(args.plan, "r", encoding="utf-8") as fh:
        plan = json.load(fh)
    if args.modo == "continuo":
        sintetizar_continuo(plan, args.out, args.idioma,
                            hueco_minimo=args.hueco)
    else:
        sintetizar_plan(plan, args.out, args.idioma)


if __name__ == "__main__":
    main()
