"""Musica de fondo y efectos: de donde salen, como se eligen y como se mezclan.

DE DONDE SALE EL AUDIO
----------------------
De dos sitios y de ninguno mas. No se inventan rutas ni se usa audio de origen
desconocido:

    musica    Jamendo API v3.0      https://api.jamendo.com/v3.0/tracks/
    efectos   Freesound API v2      https://freesound.org/apiv2/search/text/

Las claves viven en `secretos/.env` o en ESTUDIO_SECRETOS (JAMENDO_CLIENT_ID,
FREESOUND_API_KEY), NUNCA en el repo, igual que las de OpenAI.

EL BANCO, Y POR QUE UN VIDEO NO SUENA DISTINTO CADA VEZ
--------------------------------------------------------
Una busqueda en una API devuelve resultados distintos cada semana. Si el render
buscara, el mismo plan daria dos videos con distinta musica -- y este sistema
entero se apoya en lo contrario.

Asi que hay dos momentos separados:

  1. SURTIR (una persona, una vez): se busca, se escucha, se elige. Lo elegido
     se descarga al banco del canal (`banco/audio/`) y su ficha se guarda en los
     params del render. A partir de ahi es un dato.
  2. RENDERIZAR: no se sale a la red. Se coge del banco lo que dicen los params
     y se reparte por semilla, asi que el mismo plan suena igual siempre.

Es el mismo contrato que el catalogo visual, los rotulos y las cartelas: alguien
propone, una persona aprueba, y a partir de ahi es determinista.

QUE SUENA Y CUANDO
------------------
    transicion_suave   un aire corto en los encadenados de cierre de frase
    transicion_acento  un golpe en los acentos, cada pocos planos
    tecla              la maquina de escribir de una cartela, letra a letra
    retorno            el carro al terminar de escribirse una cartela

Los tiempos de la maquina de escribir NO se recalculan aqui: se piden a
`cartelas`, que los devuelve del MISMO codigo que dibuja las palabras. Una
segunda cuenta se desincronizaria del dibujo en cuanto alguien tocara el ritmo,
y un teclado que suena cuando no se escribe es peor que no ponerlo.

COMO SE MEZCLA
--------------
La pista de efectos se monta en numpy y no con un grafo de ffmpeg: son decenas
de eventos -- una cartela de seis palabras son treinta y tantas teclas -- y eso
en ffmpeg es un `adelay` y una entrada por evento. En numpy es sumar en un
buffer, es exacto y no tiene limite practico.

La mezcla final si es ffmpeg: `loudnorm` deja la musica en -23 LUFS y encima va
la subida del canal (`MUSICA_SUBIDA_DB`, +1,5 dB desde el 23-08),
`sidechaincompress` la agacha bajo la locucion (ducking) y los efectos entran a
pico -6 dBFS.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time

import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cartelas  # noqa: E402
import medios  # noqa: E402

def banco(*partes):
    """Donde viven los ficheros ya descargados, creando la carpeta si falta.

    Es del CANAL y no del video: un golpe de transicion que sirve para uno
    sirve para el siguiente, y bajarlo dos veces seria pedirle lo mismo a la
    API por no haber mirado en el cajon.
    """
    ruta = os.path.join(medios.BANCO, "audio", *partes)
    os.makedirs(os.path.dirname(ruta) if os.path.splitext(ruta)[1] else ruta,
                exist_ok=True)
    return ruta


#: Se puede mover con ESTUDIO_SECRETOS, igual que en `claves.py`: sin la
#: variable, la carpeta del propio programa.
RUTA_SECRETOS = os.path.join(
    os.environ.get("ESTUDIO_SECRETOS") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "secretos"),
    ".env")


def _claves():
    """Las claves del entorno o de secrets/.env. Nunca del repo."""
    salida = {}
    for nombre in ("JAMENDO_CLIENT_ID", "FREESOUND_API_KEY"):
        valor = os.environ.get(nombre)
        if valor and valor.strip():
            salida[nombre] = valor.strip()
    if len(salida) == 2 or not os.path.exists(RUTA_SECRETOS):
        return salida
    for linea in io.open(RUTA_SECRETOS, encoding="utf-8-sig"):
        m = re.match(r"^([A-Z_0-9]+)=(.*)$", linea.strip())
        if m and m.group(1) not in salida and m.group(2).strip():
            salida[m.group(1)] = m.group(2).strip()
    return {k: v for k, v in salida.items()
            if k in ("JAMENDO_CLIENT_ID", "FREESOUND_API_KEY")}


def hay_claves():
    """Si se puede salir a buscar. Renderizar NO lo necesita: eso lee del banco."""
    claves = _claves()
    return bool(claves.get("JAMENDO_CLIENT_ID")), bool(claves.get("FREESOUND_API_KEY"))


# ===========================================================================
# LICENCIAS, CARPETA PROPIA Y ORDEN DE FUENTES (fork, Fase 3)
#
# MONETIZAR PIDE LICENCIAS QUE LO PERMITAN. Jamendo solo excluia ND y dejaba
# pasar NC (no comercial), y Freesound no filtraba nada: un video monetizado
# con eso es un reclamo esperando. Con `audio_licencias_seguras` (defecto) de
# las dos APIs solo entran:
#
#     CC0      dominio publico, sin condiciones
#     CC BY    se puede usar para todo citando al autor (creditos.txt)
#
# y quedan fuera NC (no comercial), ND (sin obras derivadas: un video con
# musica de fondo lo es) y SA (obligaria a publicar el video con la misma
# licencia). Se clasifica la URL de la licencia que traen las dos APIs y no se
# pide filtrado al servidor: Jamendo devuelve CERO con los filtros duros
# combinados (ver `buscar_musica`).
#
# LA CARPETA PROPIA, `banco/audio/propio/{musica,efectos}/`, es lo que se baja
# a mano: la Biblioteca de audio de YouTube, Uppbeat (no tiene API), Pixabay,
# Mixkit... Cada fichero lleva al lado su ficha, `<fichero>.json`, que se
# rellena en Configuracion y no se adivina: titulo, autor, licencia, si pide
# credito y con que texto, el animo (musica) o el papel (efecto). Un fichero sin
# ficha NO se usa: no se sabe que licencia tiene.
# ===========================================================================

#: Las licencias que dejan monetizar sin pedir permiso.
LICENCIAS_SEGURAS = ("cc0", "cc-by")

#: De donde sale un fichero propio (campo `fuente` de su ficha).
ORIGENES_PROPIOS = ("youtube_audio_library", "uppbeat", "pixabay", "mixkit",
                    "comprada", "otra")

NOMBRES_ORIGEN = {
    "youtube_audio_library": "Biblioteca de audio de YouTube",
    "uppbeat": "Uppbeat", "pixabay": "Pixabay", "mixkit": "Mixkit",
    "comprada": "comprada", "otra": "otra", "jamendo": "Jamendo",
    "freesound": "Freesound",
}

#: Lo que se acepta en la carpeta propia. ffmpeg los abre todos.
EXT_AUDIO = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus")


def tipo_de_licencia(texto):
    """De la URL o el nombre de una licencia a su tipo. -> str

    'cc0', 'cc-by', 'cc-by-sa', 'cc-by-nc', 'cc-by-nd', 'cc-by-nc-sa',
    'cc-by-nc-nd', 'sampling+' o '' si no se reconoce. Acepta las URL de
    creativecommons.org (lo que devuelven Jamendo y Freesound) y los nombres
    largos («Attribution NonCommercial 4.0», «Creative Commons 0»).
    """
    t = str(texto or "").strip().lower()
    if not t:
        return ""
    if "sampling" in t:
        return "sampling+"
    if ("publicdomain/zero" in t or "creative commons 0" in t or t == "cc0"
            or t.startswith("cc0 ") or "cc0 1.0" in t):
        return "cc0"
    m = re.search(r"/licenses/([a-z-]+)/", t)
    if m:
        partes = set(m.group(1).split("-"))
    else:
        partes = set()
        if "attribution" in t or re.search(r"\bby\b", t):
            partes.add("by")
        if "noncommercial" in t or "non-commercial" in t or re.search(r"\bnc\b", t):
            partes.add("nc")
        if "noderiv" in t or "no deriv" in t or re.search(r"\bnd\b", t):
            partes.add("nd")
        if "sharealike" in t or "share alike" in t or re.search(r"\bsa\b", t):
            partes.add("sa")
    if "by" not in partes:
        return ""
    return "cc-" + "-".join(p for p in ("by", "nc", "nd", "sa") if p in partes)


def licencia_segura(texto):
    """Si se puede monetizar con esa licencia sin pedir permiso (CC0 o CC BY)."""
    return tipo_de_licencia(texto) in LICENCIAS_SEGURAS


def nombre_licencia(texto):
    """«CC BY 4.0», «CC0 1.0»... para los creditos. Lo que no se reconoce, tal cual."""
    tipo = tipo_de_licencia(texto)
    if not tipo:
        return str(texto or "").strip()
    version = re.search(r"/(\d\.\d)/?", str(texto or ""))
    nombre = "CC0" if tipo == "cc0" else "CC " + tipo[3:].upper()
    return f"{nombre} {version.group(1)}" if version else nombre


def _ajustes():
    """Los ajustes del estudio, o los de fabrica si no se pueden leer."""
    try:
        import ajustes                                       # noqa: PLC0415
        return ajustes.leer()
    except Exception:                                        # noqa: BLE001
        return {"audio_licencias_seguras": True,
                "audio_prioridad": "propia_primero",
                "uppbeat_suscripcion": False, "uppbeat_canal": ""}


def solo_seguras():
    """Si de Jamendo y Freesound solo entran CC0 y CC BY."""
    return bool(_ajustes().get("audio_licencias_seguras", True))


def fuentes_de(familia):
    """En que orden se buscan musica o efectos. -> ['propia', 'jamendo'] etc."""
    en_linea = "jamendo" if familia == "musica" else "freesound"
    prioridad = _ajustes().get("audio_prioridad") or "propia_primero"
    if prioridad == "solo_propia":
        return ["propia"]
    if prioridad == "en_linea_primero":
        return [en_linea, "propia"]
    return ["propia", en_linea]


def carpeta_propia(familia):
    """La carpeta donde se sueltan a mano la musica o los efectos propios."""
    if familia not in ("musica", "efectos"):
        raise ValueError(f"familia de audio desconocida: {familia!r}")
    return banco("propio", familia)


def _ruta_ficha(ruta):
    return ruta + ".json"


def _id_propio(ruta):
    """Identificador estable de un fichero propio: su nombre y su contenido.

    El contenido entra (un trozo de su huella) para que sustituir un fichero
    por otro con el mismo nombre no deje sonando la copia vieja del banco.
    """
    import hashlib                                           # noqa: PLC0415
    estado = os.stat(ruta)
    clave = (os.path.normcase(os.path.abspath(ruta)), estado.st_size, estado.st_mtime)
    if clave in _IDS_PROPIOS:
        return _IDS_PROPIOS[clave]
    base = re.sub(r"[^a-z0-9]+", "-",
                  os.path.splitext(os.path.basename(ruta))[0].lower()).strip("-")
    huella = hashlib.sha1()
    with open(ruta, "rb") as fh:
        for trozo in iter(lambda: fh.read(1 << 20), b""):
            huella.update(trozo)
    _IDS_PROPIOS[clave] = f"{(base or 'audio')[:40]}-{huella.hexdigest()[:8]}"
    return _IDS_PROPIOS[clave]


#: Huellas ya calculadas: {(ruta, tamano, fecha): id}. Un fichero que no cambia
#: no se vuelve a leer entero en cada busqueda.
_IDS_PROPIOS = {}


def catalogo_propio(familia):
    """Lo que hay en la carpeta propia, con su ficha o sin ella. -> [dict]

    Cada entrada: {archivo, id, ficha (o None), completa, falta}. `completa` es
    que se puede usar; `falta` dice que le falta a la ficha para poder usarse.
    """
    carpeta = carpeta_propia(familia)
    salida = []
    for nombre in sorted(os.listdir(carpeta)):
        ruta = os.path.join(carpeta, nombre)
        if not os.path.isfile(ruta) or os.path.splitext(nombre)[1].lower() not in EXT_AUDIO:
            continue
        # las copias de trabajo («cancion.48000.wav») no son canciones: las
        # dejaba aqui una version anterior de `_a_wav`
        if re.search(rf"\.{FRECUENCIA}\.wav$", nombre, re.IGNORECASE):
            continue
        ficha = None
        if os.path.exists(_ruta_ficha(ruta)):
            try:
                with open(_ruta_ficha(ruta), "r", encoding="utf-8-sig") as fh:
                    ficha = json.load(fh)
            except (OSError, ValueError):
                ficha = None
        falta = _falta_en_ficha(ficha, familia)
        salida.append({"archivo": nombre, "id": _id_propio(ruta),
                       "ficha": ficha, "completa": not falta, "falta": falta})
    return salida


def _falta_en_ficha(ficha, familia):
    """Lo que le falta a una ficha propia para poder usarse. -> [str]"""
    if not isinstance(ficha, dict):
        return ["la ficha entera"]
    falta = []
    if ficha.get("fuente") not in ORIGENES_PROPIOS:
        falta.append("de donde sale (fuente)")
    if not str(ficha.get("titulo") or "").strip():
        falta.append("el titulo")
    if not str(ficha.get("licencia") or "").strip():
        falta.append("la licencia")
    if familia == "musica" and ficha.get("animo") not in ANIMOS:
        falta.append("el animo")
    if familia == "efectos" and ficha.get("papel") not in PAPELES:
        falta.append("el papel (en que hueco suena)")
    return falta


def guardar_ficha_propia(familia, archivo, datos):
    """Escribe la ficha de un fichero propio, validada. -> ficha guardada"""
    carpeta = carpeta_propia(familia)
    nombre = os.path.basename(str(archivo or ""))
    ruta = os.path.join(carpeta, nombre)
    if not nombre or not os.path.isfile(ruta):
        raise ValueError(f"no hay ningun {nombre!r} en tu carpeta de {familia}")
    datos = datos if isinstance(datos, dict) else {}
    ficha = {
        "fuente": str(datos.get("fuente") or "").strip(),
        "titulo": " ".join(str(datos.get("titulo") or "").split())[:200],
        "artista": " ".join(str(datos.get("artista") or "").split())[:200],
        "licencia": " ".join(str(datos.get("licencia") or "").split())[:200],
        "atribucion_requerida": bool(datos.get("atribucion_requerida")),
        "texto_credito": str(datos.get("texto_credito") or "").strip()[:1000],
        "codigo_credito": " ".join(str(datos.get("codigo_credito") or "").split())[:200],
        "etiquetas": [str(x).strip()[:40] for x in (datos.get("etiquetas") or [])
                      if str(x).strip()][:12],
    }
    if familia == "musica":
        ficha["animo"] = str(datos.get("animo") or "").strip()
        try:
            # 0 es «no lo se»: es lo que manda un campo numerico vacio
            ficha["bpm"] = (int(datos["bpm"]) or None) if datos.get("bpm") not in (None, "") else None
        except (TypeError, ValueError):
            raise ValueError("bpm es un numero entero (o vacio)")
        # LOS TROZOS BUENOS: lo unico de la cancion que se usa. Se mide la
        # duracion aqui para no aceptar un trozo que se sale del fichero.
        try:
            duracion = float(medios.duracion_media(ruta) or 0.0)
        except Exception:                                    # noqa: BLE001
            duracion = 0.0
        ficha["duracion"] = round(duracion, 2)
        ficha["fragmentos"] = _limpiar_fragmentos(datos.get("fragmentos"), duracion)
    else:
        ficha["papel"] = str(datos.get("papel") or "").strip()
    if ficha["fuente"] not in ORIGENES_PROPIOS:
        raise ValueError(f"fuente {ficha['fuente']!r}: solo "
                         + ", ".join(ORIGENES_PROPIOS))
    falta = _falta_en_ficha(ficha, familia)
    if falta:
        raise ValueError("a la ficha le falta " + ", ".join(falta))
    if ficha["atribucion_requerida"] and not ficha["texto_credito"] \
            and ficha["fuente"] != "uppbeat":
        raise ValueError("si pide atribucion, escribe el texto del credito "
                         "(el que da la propia web al bajarla)")
    medios.escribir_texto(_ruta_ficha(ruta),
                          json.dumps(ficha, ensure_ascii=False, indent=1))
    return ficha


#: Lo minimo que puede durar un trozo bueno: menos no da para asentarse, y
#: encadenado con fundidos de tres segundos seria casi todo fundido.
MIN_FRAGMENTO_S = 8.0

#: Cuantos trozos se pueden marcar en una cancion.
MAX_FRAGMENTOS = 20

#: El fundido entre dos trozos dentro de un mismo tramo. Mas corto que el
#: cruce entre tramos (CRUCE_S): aqui no cambia el relato, solo se empalma.
CRUCE_PIEZA_S = 3.0


def _limpiar_fragmentos(lista, duracion=0.0):
    """Los trozos buenos de una cancion, validados y ordenados. -> [dict]

    Acepta [{desde, hasta}] o [[desde, hasta]] en segundos. Se ordenan y los
    que se pisan se juntan: dos trozos solapados son un trozo.
    """
    pares = []
    for trozo in lista or []:
        if isinstance(trozo, dict):
            desde, hasta = trozo.get("desde"), trozo.get("hasta")
        elif isinstance(trozo, (list, tuple)) and len(trozo) == 2:
            desde, hasta = trozo
        else:
            raise ValueError("cada trozo es {desde, hasta} en segundos")
        try:
            desde, hasta = float(desde), float(hasta)
        except (TypeError, ValueError):
            raise ValueError("desde y hasta son segundos (numeros)")
        if duracion:
            hasta = min(hasta, duracion)
        if desde < 0 or hasta - desde < MIN_FRAGMENTO_S:
            raise ValueError(f"cada trozo tiene que durar al menos "
                             f"{MIN_FRAGMENTO_S:.0f} segundos y caber en la cancion")
        pares.append([round(desde, 2), round(hasta, 2)])
    pares.sort()
    juntos = []
    for desde, hasta in pares:
        if juntos and desde <= juntos[-1][1]:
            juntos[-1][1] = max(juntos[-1][1], hasta)
        else:
            juntos.append([desde, hasta])
    if len(juntos) > MAX_FRAGMENTOS:
        raise ValueError(f"como mucho {MAX_FRAGMENTOS} trozos por cancion")
    return [{"desde": d, "hasta": h} for d, h in juntos]


def ficha_propia(entrada, familia):
    """De una entrada de `catalogo_propio` a una ficha como las de Jamendo."""
    f = entrada.get("ficha") or {}
    salida = {
        "fuente": "propia", "id": entrada["id"], "archivo": entrada["archivo"],
        "origen": f.get("fuente") or "otra",
        "titulo": f.get("titulo") or entrada["archivo"],
        "artista": f.get("artista") or "", "autor": f.get("artista") or "",
        "licencia": f.get("licencia") or "",
        "atribucion_requerida": bool(f.get("atribucion_requerida")),
        "texto_credito": f.get("texto_credito") or "",
        "codigo_credito": f.get("codigo_credito") or "",
        "etiquetas": list(f.get("etiquetas") or []),
        "duracion": 0.0, "descarga": "",
    }
    if familia == "musica":
        salida.update(animo=f.get("animo") or "", bpm=f.get("bpm"),
                      fragmentos=list(f.get("fragmentos") or []),
                      duracion=float(f.get("duracion") or 0.0))
    else:
        salida["papel"] = f.get("papel") or ""
    return salida


def propios(familia):
    """Las fichas propias que SE PUEDEN USAR (ficha completa). -> [ficha]"""
    return [ficha_propia(e, familia) for e in catalogo_propio(familia)
            if e["completa"]]


def hay_musica():
    """Si hay de donde sacar musica: carpeta propia o Jamendo, segun el orden."""
    for fuente in fuentes_de("musica"):
        if fuente == "propia" and propios("musica"):
            return True
        if fuente == "jamendo" and hay_claves()[0]:
            return True
    return False


def hay_efectos():
    """Si hay de donde sacar efectos: carpeta propia o Freesound, segun el orden."""
    for fuente in fuentes_de("efectos"):
        if fuente == "propia" and propios("efectos"):
            return True
        if fuente == "freesound" and hay_claves()[1]:
            return True
    return False


def falta_musica_texto():
    """Que hacer cuando no hay musica de donde sacar. Para errores y avisos."""
    return ("no hay música de donde sacar: suelta temas en tu carpeta "
            f"({carpeta_propia('musica')}) y rellena su ficha en Configuración, "
            "o pon la clave de Jamendo, o apaga la música de este vídeo")


# ===========================================================================
# LOS PAPELES DE EFECTO
#
# Un papel no es "un sonido": es un HUECO del montaje con lo que hay que pedirle
# a Freesound para llenarlo y como tiene que sonar cuando entre.
#
#   consultas   varias, y a proposito: pedir siempre lo mismo devuelve siempre
#               los mismos diez ficheros, y entonces el video suena repetitivo.
#   duracion    el filtro de busqueda. Un latigazo de tres segundos no es un
#               latigazo, es un fondo.
#   ganancia    cuanto entra respecto al pico. Una tecla no puede sonar como un
#               golpe de transicion aunque las dos esten normalizadas.
#
# AQUI VIVIA `adelanto`: cuanto ANTES del corte empezaba a sonar el fichero,
# 0,45 s los barridos y 0,55 s los golpes. La idea era buena --«un barrido que
# empieza en el corte llega tarde: lo que hace el efecto es anunciarlo»-- y la
# ejecucion estaba mal, porque colocaba el PRINCIPIO DEL FICHERO y lo que se oye
# no es el principio: es el golpe.
#
# Medido sobre los doce efectos de transicion de un video real, distancia desde
# el principio del fichero hasta que llega a -6 dB de su pico:
#
#     freesound_553521   0,05 s        freesound_648729   0,61 s
#     freesound_449645   0,07 s        freesound_810329   0,69 s
#     freesound_802465   0,16 s        freesound_511874   0,90 s
#
# O sea que con un adelanto fijo el mismo montaje sonaba **0,4 s pronto o 0,45 s
# tarde segun que fichero le tocara a ese corte por semilla**, y eso es lo que se
# oye como «no van sincronizados, y no sabria decir si siempre». No era un
# retraso: era una loteria de casi un segundo de recorrido.
#
# Lo que hay ahora es `golpe_de` + `ANCLA_TRANSICION`: se mide donde golpea CADA
# fichero y se coloca para que el golpe caiga siempre en el mismo sitio de la
# transicion que se ve. El adelanto deja de ser un numero escrito a ojo y pasa a
# ser una consecuencia.
# ===========================================================================

PAPELES = {
    "transicion_suave": {
        "nombre": "Aire de transición",
        "descripcion": "Un barrido corto y suave para los encadenados que cierran frase.",
        "consultas": ("soft whoosh transition", "airy swoosh", "subtle swish transition",
                      "gentle whoosh"),
        "duracion": (0.3, 2.5), "ganancia": 0.55, "cuantos": 6,
    },
    "transicion_acento": {
        "nombre": "Golpe de transición",
        "descripcion": "El impacto de los acentos, cada pocos planos. Se nota.",
        "consultas": ("cinematic whoosh impact", "transition hit braam",
                      "riser impact short", "whoosh boom transition"),
        "duracion": (0.3, 3.0), "ganancia": 0.8, "cuantos": 6,
    },
    "tecla": {
        "nombre": "Tecla de máquina de escribir",
        "descripcion": "Una pulsación seca. Suena una por letra mientras la cartela se escribe.",
        "consultas": ("typewriter single key press", "typewriter key click",
                      "mechanical keyboard single click"),
        "duracion": (0.05, 0.6), "ganancia": 0.32, "cuantos": 4,
    },
    "retorno": {
        "nombre": "Retorno de carro",
        "descripcion": "La campanita y el carro al terminar de escribirse una cartela.",
        "consultas": ("typewriter carriage return bell", "typewriter ding return"),
        "duracion": (0.3, 3.0), "ganancia": 0.5, "cuantos": 3,
    },
}

#: Ritmo maximo de la maquina de escribir, en teclas por segundo. Por encima de
#: esto deja de sonar a maquina y suena a ruido: una palabra corta que entra en
#: 0,09 s no puede llevar siete pulsaciones.
TECLAS_POR_SEGUNDO = 16.0

#: Y el tope de teclas por palabra, por lo mismo: una palabra larga no suena
#: mas rapido, suena la misma rafaga.
TECLAS_POR_PALABRA = 6

#: DONDE CAE EL GOLPE del efecto dentro de la transicion que se VE, en fraccion
#: de su duracion. La transicion se cuece en los primeros fotogramas del plano
#: que entra (`p8._cocer_transicion`), asi que ocupa [t_in, t_in + duracion]:
#:
#:     0.0   el golpe cae al empezar la mezcla visual
#:     0.5   cae cuando la mezcla va por la mitad  <- el ojo lee AQUI el cambio
#:     1.0   cae cuando la imagen nueva ya esta entera, o sea tarde
#:
#: Es el UNICO numero de esto, y es el que hay que mover si suena pronto o tarde.
#: Con 0,5 el sonido y la imagen dicen «ha pasado algo» en el mismo instante.
ANCLA_TRANSICION = 0.5

#: A cuanto por debajo del pico se considera que el efecto YA ESTA SONANDO. Con
#: el pico a secas (`argmax`) un barrido con la cola mas alta que la entrada se
#: colocaria por su final; con -6 dB se coge el momento en que entra de verdad,
#: que es lo que oye una persona.
GOLPE_UMBRAL_DB = -6.0

#: Objetivo de la musica, en LUFS, y pico de los efectos, en dBFS.
MUSICA_LUFS = -23.0
EFECTOS_PICO_DB = -6.0

FRECUENCIA = 48000

#: Lo medido de cada fichero, que no cambia: se abre una vez por render.
_GOLPES = {}


def golpe_de(ruta):
    """Cuanto tarda ese efecto en GOLPEAR desde el principio del fichero. -> s

    Un barrido no suena en su primer milisegundo: sube y pega. Medido sobre los
    doce efectos de transicion de un video real, ese retardo va de **0,05 s a
    0,90 s** segun el fichero -- casi un segundo de recorrido--, asi que colocar
    los efectos por su primera muestra es colocarlos en cualquier sitio.

    Se devuelve 0,0 cuando el fichero no esta o no se puede leer, y eso es lo
    correcto: si no esta, `pista_de_efectos` no lo mezcla y el numero da igual.
    Reventar aqui seria tumbar un render por un efecto que ni suena.
    """
    if not ruta:
        return 0.0
    clave = os.path.normcase(os.path.abspath(ruta))
    if clave in _GOLPES:
        return _GOLPES[clave]
    valor = 0.0
    try:
        muestras = _leer(_a_wav(ruta))
        env = np.abs(muestras).max(axis=1) if muestras.ndim > 1 \
            else np.abs(muestras)
        pico = float(env.max()) if len(env) else 0.0
        if pico > 0:
            umbral = pico * (10.0 ** (GOLPE_UMBRAL_DB / 20.0))
            arriba = np.nonzero(env >= umbral)[0]
            if len(arriba):
                valor = float(arriba[0]) / FRECUENCIA
    except Exception:                                       # noqa: BLE001
        valor = 0.0
    _GOLPES[clave] = valor
    return valor


# ------------------------------------------------------------------ Jamendo

ANIMOS = {
    "oscuro": "dark cinematic", "tension": "tense suspense",
    "epico": "epic cinematic", "sobrio": "documentary ambient",
    "esperanzador": "uplifting hopeful", "corporativo": "corporate clean",
    "melancolico": "melancholic piano", "misterioso": "mysterious ambient",
}


def _pedir_a_jamendo(params):
    respuesta = requests.get("https://api.jamendo.com/v3.0/tracks/",
                             params=params, timeout=45)
    respuesta.raise_for_status()
    datos = respuesta.json()
    cabecera = datos.get("headers") or {}
    if cabecera.get("status") != "success":
        raise RuntimeError(f"Jamendo: {cabecera.get('error_message') or cabecera}")
    return datos.get("results") or []


def buscar_musica(animo="sobrio", duracion_s=0, cuantas=12, velocidad="low",
                  instrumental=True, extra=""):
    """Temas de Jamendo que encajan con el tono del video.

    POR QUE ESTO ES UNA ESCALERA Y NO UNA CONSULTA
    ----------------------------------------------
    En Jamendo, `fuzzytags` NO se lleva bien con los filtros duros: combinarlo
    con `durationbetween`, con `audiodownload_allowed` o con `ccnd` devuelve
    CERO resultados casi siempre, y devuelve cero con `status: success` -- o
    sea, sin un solo indicio de que el problema sea el filtro. Medido el
    19-08-2026:

        fuzzytags=cinematic                             5 temas
        fuzzytags=cinematic + durationbetween=60_600    0
        fuzzytags=cinematic + audiodownload_allowed     0
        fuzzytags=cinematic + ccnd=false                0
        durationbetween=60_600 (sin fuzzytags)          5

    Asi que se pregunta de lo mas estricto a lo mas suelto y se para en cuanto
    hay material, y lo que de verdad hace falta -- que el tema se pueda
    descargar -- se comprueba AQUI mirando el campo `audiodownload`, que es lo
    que se va a usar. Un filtro del servidor que vacia la busqueda no protege
    de nada: deja la pantalla en blanco y parece que no hay musica.
    """
    claves = _claves()
    if not claves.get("JAMENDO_CLIENT_ID"):
        raise RuntimeError("falta JAMENDO_CLIENT_ID: ponla en Configuracion")
    etiquetas = ANIMOS.get(animo, animo)
    if extra:
        etiquetas = f"{etiquetas} {extra}".strip()
    tope = max(1, min(50, int(cuantas)))
    seguras = solo_seguras()
    # CON EL FILTRO DE LICENCIAS SE PIDEN MAS: en Jamendo casi todo es NC, y de
    # cada cien temas pasan unos pocos. 200 es el maximo que admite su API.
    comun = {"client_id": claves["JAMENDO_CLIENT_ID"], "format": "json",
             "limit": 200 if seguras else tope * 2,
             "include": "musicinfo licenses", "boost": "popularity_total"}
    if instrumental:
        comun["vocalinstrumental"] = "instrumental"
    minimo = int(max(60, min((duracion_s or 0) * 0.45, 420))) if duracion_s else 0

    escalera = [
        {"fuzzytags": etiquetas, "speed": velocidad,
         "audiodownload_allowed": "true", "ccnd": "false"},
        {"fuzzytags": etiquetas, "speed": velocidad},
        {"fuzzytags": etiquetas},
        # la ultima suelta las etiquetas y se queda con la primera palabra: mas
        # vale ofrecer algo del genero que una lista vacia
        {"fuzzytags": etiquetas.split()[0] if etiquetas.split() else "ambient",
         "speed": velocidad},
    ]
    vistos, salida = set(), []
    for extra_params in escalera:
        params = dict(comun)
        params.update(extra_params)
        for track in _pedir_a_jamendo(params):
            ficha = _ficha_musica(track)
            # lo unico innegociable: que se pueda bajar. Sin esto no hay tema.
            if not ficha["descarga"] or ficha["id"] in vistos:
                continue
            # y, con el filtro puesto, que se pueda MONETIZAR: CC0 o CC BY
            if seguras and not licencia_segura(ficha["licencia"]):
                vistos.add(ficha["id"])
                continue
            vistos.add(ficha["id"])
            salida.append(ficha)
        if len(salida) >= tope:
            break
    # los que cubren el video sin repetirse mucho, primero
    salida.sort(key=lambda f: (f["duracion"] < minimo, -f["duracion"]))
    return salida[:tope]


def _ficha_musica(track):
    info = track.get("musicinfo") or {}
    etiquetas = info.get("tags") or {}
    return {
        "fuente": "jamendo",
        "id": str(track.get("id")),
        "titulo": track.get("name") or "",
        "artista": track.get("artist_name") or "",
        "duracion": float(track.get("duration") or 0),
        "descarga": track.get("audiodownload") or "",
        "escucha": track.get("audio") or track.get("audiodownload") or "",
        "licencia": track.get("license_ccurl") or "",
        "generos": list(etiquetas.get("genres") or []),
        "instrumentos": list(etiquetas.get("instruments") or [])[:6],
        "vocal": info.get("vocalinstrumental") or "",
        "velocidad": info.get("speed") or "",
    }


# ------------------------------------------------- la banda sonora, sin manos
#
# NADIE ELIGE UNA CANCION. Un video de doce minutos con un solo tema en bucle
# suena a video de doce minutos con un tema en bucle, y ademas pedirle a alguien
# que escuche seis candidatos para decidir algo que el propio video ya dice es
# trabajo que no hay que hacer: el RITMO del montaje —cuantos planos por minuto,
# donde se acelera, donde se para— es exactamente la informacion con la que un
# montador elige la musica.
#
# Asi que el video se parte en TRAMOS, cada tramo pide su animo, y cada animo
# trae su tema. Luego se encadenan con un fundido largo y sale una CAMA: una
# sola pista continua que cambia de color cuando cambia el relato.
#
# La receta de encadenado y niveles viene del motor anterior (docs/08 §C), que la
# pago en varios videos: loudnorm a -23 en cada tramo ANTES de encadenar —o se
# oyen los saltos entre temas—, `acrossfade` de seis segundos, y relleno exacto
# al largo del master con fundido de entrada y de salida.

#: En cuantos tramos se parte el video. Un tema por cada ~2,5 minutos: menos es
#: un bucle, y mas es un popurri en el que ningun tema llega a asentarse.
SEGUNDOS_POR_TRAMO = 150.0
TRAMOS_MIN, TRAMOS_MAX = 2, 6

#: Cuanto dura el encadenado entre dos tramos. Seis segundos es lo que hace que
#: el cambio de tema se note como que la escena cambia y no como que ha entrado
#: otra cancion.
CRUCE_S = 6.0

#: Ganancia de la cama sobre su -23 LUFS, ya normalizada. Vive aqui y no en el
#: grafo de mezcla porque es el unico numero que hay que calibrar. En el motor anterior se
#: midio que +1 dB se pasa por arriba en los huecos sin voz y -6 la entierra.
CAMA_GANANCIA_DB = -1.0

#: CUANTA MUSICA, sobre su -23 LUFS ya normalizados. Se aplica a las DOS
#: entradas --la cama encadenada y el tema suelto-- y por eso vive aparte de
#: `CAMA_GANANCIA_DB`: ese numero describe donde queda la cama RESPECTO al tema
#: suelto (1 dB por debajo, porque ya viene encadenada), y moverlo haria que dos
#: videos del canal sonaran distinto segun llevaran uno o varios temas.
#:
#: HISTORIA DE ESTE NUMERO, porque ha ido en las dos direcciones:
#:
#:   23-08   +1,5 dB   el canal oyo el video largo montado y dijo que la cama se
#:                     quedaba corta bajo la locucion.
#:   24-08   -2,0 dB   el canal oyo el segundo video y dijo lo contrario: «la
#:                     musica en general esta un pelin fuerte, especialmente en
#:                     las pausas». No es que se contradiga: lo que habia
#:                     cambiado en medio no era el nivel, era CUANDO subia. Ver
#:                     el bloque del ducking justo debajo.
#:
#: Medido con el banco de pruebas del ducking, contra el nivel de la musica sin
#: agachar (0 dB de referencia):
#:
#:                      bajo la voz   en el remate de frase   en la pausa
#:   antes (23-08)        -47,2 dB         -35,5 dB            -23,7 dB
#:   ahora                -48,6 dB         -46,8 dB            -29,3 dB
#:
#: O sea: bajo la voz casi lo mismo (1,4 dB menos), en la pausa 5,6 dB menos, y
#: en el remate de frase --el fallo que se oia, la palabra que cierra la frase
#: tapada por la musica-- 11,3 dB menos.
MUSICA_SUBIDA_DB = -2.0

# ===========================================================================
# EL DUCKING, Y POR QUE LA LLAVE NO ES LA VOZ TAL CUAL
#
# Un compresor de cadena lateral agacha la musica en proporcion a lo que suba la
# LLAVE. Si la llave es la voz tal cual, la musica sigue la envolvente de la voz
# -- y la envolvente de una voz no es una meseta: cae al final de cada frase.
#
# Ese es el fallo que se oyo (24-08): «around one hundred sixty-five companies
# warned» -- el «warned» cierra la frase bajando el tono, la llave cae por
# debajo del umbral MIENTRAS la palabra todavia suena, la musica sube 11,7 dB y
# se lleva la ultima palabra por delante. Pasa en todas las frases y siempre en
# el mismo sitio: justo donde esta el dato.
#
# La cura no es agachar mas ni soltar mas tarde: es APLANAR LA LLAVE. Se sube
# 16 dB y se limita, asi que una silaba floja y una fuerte cruzan el umbral
# igual. Con eso el ducking deja de seguir la voz y pasa a ser binario: hay voz
# o no la hay. Es lo que hace cualquier emisora, y ademas tiene un efecto
# lateral muy util -- la profundidad pasa a ser CONSTANTE, asi que «cuanta
# musica hay bajo la voz» vuelve a ser UN solo numero (MUSICA_SUBIDA_DB) en vez
# de depender de como venga cada frase.
#
# Y la caida sube a 1,2 s por lo mismo: con 450 ms la musica volvia a subir
# entre frase y frase, o sea varias veces por plano.
# ===========================================================================

#: Cuanto se sube la llave antes de limitarla. Con 16 dB, una silaba a -24 dBFS
#: --el final flojo de una frase-- llega igual de arriba que el cuerpo.
DUCK_LLAVE_SUBIDA_DB = 16

#: Y donde se le corta la cabeza. Todo lo que pase de aqui vale lo mismo, que es
#: lo que hace la llave plana.
DUCK_LLAVE_TECHO = 0.7

#: El umbral, ya sobre la llave subida. Alto a proposito: con la llave 16 dB
#: arriba, un umbral bajo lo cruzaria hasta el ruido de sala entre frases.
DUCK_UMBRAL = 0.05

#: La proporcion. Con la llave plana esto ya no modula nada: fija la
#: PROFUNDIDAD, que sale ~19 dB.
DUCK_RATIO = 6

#: Ataque y caida, en ms. El ataque corto para que la musica no pise la primera
#: silaba; la caida larga para que no vuelva a subir entre dos frases de la
#: misma idea.
DUCK_ATAQUE_MS = 15
DUCK_CAIDA_MS = 1200

# ------------------------------------------------------------ EL MASTER
#
# A CUANTO SALE EL VIDEO, y son las cifras de la plataforma. Medido con
# `ebur128` sobre el MP4 del 28-08-2026 (PENDIENTE 42):
#
#     integrado   -17,1 LUFS      YouTube normaliza a -14
#     rango         3,6 LU
#     pico         -0,2 dBFS      lo habitual es dejar -1 dBTP
#
# Ni estaba roto ni recortaba, pero sonaba tres decibelios por debajo de la
# media de la plataforma y a la vez con los picos pegados a cero: los dos
# defectos a la vez, que es lo que pasa cuando nadie mide el conjunto.
#
# Y SE APLICA EN DOS PASADAS, que es la parte que importa. Un `loudnorm` de una
# sola pasada es un normalizador ADAPTATIVO: va corrigiendo sobre la marcha, y
# sobre una mezcla de voz con musica eso se oye como bombeo. Con la medida
# delante (`measured_*` + `linear=true`) lo unico que hace es una GANANCIA
# CONSTANTE: el video suena mas alto y la dinamica es exactamente la que se
# monto. Es la misma disciplina que la cama, que normaliza cada tramo al
# construirla en vez de medir la pista entera como si fuera una.
MASTER_LUFS = -14.0
MASTER_TP = -1.0
MASTER_LRA = 11.0


def filtro_master(medida=None):
    """El ultimo eslabon de la mezcla: el nivel de salida. -> str

    Sin `medida` sale el filtro de ANALISIS, que no toca nada y escribe la
    medida en JSON; con ella, el que aplica la ganancia constante.
    """
    base = (f"loudnorm=I={MASTER_LUFS:g}:TP={MASTER_TP:g}:LRA={MASTER_LRA:g}")
    if not medida:
        return base + ":print_format=json"
    return base + ":" + ":".join([
        f"measured_I={float(medida['input_i']):.2f}",
        f"measured_TP={float(medida['input_tp']):.2f}",
        f"measured_LRA={float(medida['input_lra']):.2f}",
        f"measured_thresh={float(medida['input_thresh']):.2f}",
        "linear=true", "print_format=summary"])


#: Las tres bandas con las que se juzga un tema SIN OIRLO. La de en medio es la
#: de la VOZ: un tema con muchos medios se pelea con el narrador por el mismo
#: sitio del espectro y no hay ducking que lo arregle.
BANDAS = {"graves": (20.0, 250.0), "medios": (250.0, 4000.0),
          "agudos": (4000.0, 16000.0)}


def arco_del_video(escenas, duracion_s=0.0):
    """En que tramos se parte el video y que animo pide cada uno, POR EL RITMO.

    QUE SE MIDE
    -----------
    La densidad de plano: cuantos cortes por segundo hay en ese tramo. Es la
    medida honesta del ritmo de un montaje —un tramo de planos de segundo y
    medio corre, uno de planos de cuatro respira— y sale del plan, que ya esta
    hecho, sin llamar a nadie y sin costar un centimo.

    COMO SE TRADUCE A ANIMO
    -----------------------
    Con la posicion, que es la otra mitad: el mismo ritmo no pide lo mismo al
    principio que al final. La apertura tiene que enganchar, el final tiene que
    cerrar, y el cuerpo acompaña sin estorbar. De ahi salen los animos y ademas
    la `velocidad` con la que se le pide el tema a Jamendo, que es literalmente
    el ritmo traducido a su vocabulario.

    Devuelve [{i, desde, hasta, fraccion, planos, densidad, animo, velocidad,
    por_que}]. Nada aleatorio: el mismo plan da el mismo arco siempre.
    """
    escenas = [e for e in (escenas or []) if e.get("t_in") is not None]
    if not escenas:
        return []
    origen = float(escenas[0].get("t_in") or 0.0)
    fin = float(escenas[-1].get("t_out") or escenas[-1].get("t_in") or 0.0)
    total = float(duracion_s or (fin - origen)) or 1.0
    cuantos = int(max(TRAMOS_MIN, min(TRAMOS_MAX, round(total / SEGUNDOS_POR_TRAMO))))

    tramos = []
    for i in range(cuantos):
        desde = total * i / cuantos
        hasta = total * (i + 1) / cuantos
        dentro = [e for e in escenas
                  if desde <= float(e.get("t_in") or 0.0) - origen < hasta]
        largo = max(0.001, hasta - desde)
        tramos.append({"i": i, "desde": desde, "hasta": hasta,
                       "fraccion": 1.0 / cuantos, "planos": len(dentro),
                       "densidad": len(dentro) / largo})

    # contra la densidad MEDIA del video, no contra la mediana de los tramos:
    # con tres tramos la mediana ES uno de ellos, asi que ese nunca podia salir
    # rapido y su explicacion decia «por debajo» de si mismo
    media = len(escenas) / max(0.001, total)
    for tramo in tramos:
        rapido = tramo["densidad"] > media
        primero, ultimo = tramo["i"] == 0, tramo["i"] == cuantos - 1
        if primero:
            tramo["animo"] = "tension" if rapido else "misterioso"
            tramo["por_que"] = ("abre y el montaje ya va rapido: engancha"
                                if rapido else "abre despacio: intriga")
        elif ultimo:
            tramo["animo"] = "epico" if rapido else "melancolico"
            tramo["por_que"] = ("cierra acelerando: remate"
                                if rapido else "cierra bajando: poso")
        else:
            tramo["animo"] = "tension" if rapido else "sobrio"
            tramo["por_que"] = (f"{tramo['planos']} planos, "
                                + ("por encima" if rapido else "por debajo")
                                + " del ritmo medio")
        # el ritmo, dicho en el vocabulario de Jamendo
        tramo["velocidad"] = ("high" if tramo["densidad"] > media * 1.25
                              else "low" if tramo["densidad"] < media * 0.8
                              else "medium")
    return tramos


def medir(ruta):
    """Como suena un tema, en numeros. Sin oirlo y sin llamar a nadie.

    Se mide la energia en tres bandas y se devuelve en dB relativos. Lo que
    importa de verdad es `medios`: es donde vive la voz, y un tema que la llena
    se pelea con el narrador por el mismo sitio. Un tema de graves gordos y
    medios flojos deja el hueco donde tiene que estar.

    Se hace en numpy y no con un grafo de ffmpeg porque el WAV ya esta
    decodificado para todo lo demas: tres FFT contra tres llamadas a un proceso.
    """
    # LO MEDIDO NO CAMBIA mientras el fichero no cambie: con la carpeta propia,
    # el mismo tema se puede mirar en cada tramo de un video de veinte minutos
    try:
        estado = os.stat(ruta)
        clave = (os.path.normcase(os.path.abspath(ruta)), estado.st_size,
                 estado.st_mtime)
    except OSError:
        clave = None
    if clave and clave in _MEDIDAS:
        return dict(_MEDIDAS[clave])
    muestras = _leer(_a_wav(ruta))
    if not len(muestras):
        return {"lufs": -99.0, "graves": -99.0, "medios": -99.0, "agudos": -99.0}
    ficha = _medir_muestras(muestras)
    if clave:
        _MEDIDAS[clave] = dict(ficha)
    return ficha


#: Lo medido de cada tema: {(ruta, tamano, fecha): ficha}.
_MEDIDAS = {}


def _medir_muestras(muestras):
    mono = muestras.mean(axis=1)
    # un trozo del centro: el arranque y el final de un tema suelen ser fundidos
    # y medir ahi dice mas del fundido que del tema
    centro = len(mono) // 2
    ancho = min(len(mono), FRECUENCIA * 30)
    trozo = mono[max(0, centro - ancho // 2):centro + ancho // 2]
    espectro = np.abs(np.fft.rfft(trozo * np.hanning(len(trozo)))) ** 2
    hercios = np.fft.rfftfreq(len(trozo), 1.0 / FRECUENCIA)
    total = float(espectro.sum()) or 1.0
    ficha = {}
    for nombre, (bajo, alto) in BANDAS.items():
        dentro = espectro[(hercios >= bajo) & (hercios < alto)].sum()
        ficha[nombre] = round(10.0 * np.log10(max(dentro / total, 1e-9)), 1)
    rms = float(np.sqrt(np.mean(np.square(trozo)))) or 1e-9
    ficha["lufs"] = round(20.0 * np.log10(rms), 1)
    return ficha


def puntuar(ficha_medida):
    """Cuanto le conviene este tema a una voz encima. Mas alto, mejor.

    Graves menos medios: premia lo que suena lleno abajo y deja libre la banda
    de la voz. Es la misma regla con la que se eligieron los temas de
    el motor anterior, medida en vez de escuchada.
    """
    m = ficha_medida or {}
    return float(m.get("graves", -99)) - float(m.get("medios", -99))


def ordenar_temas(candidatos, evitar=()):
    """Los candidatos, de mejor a peor para llevar voz encima. Sin oir nada.

    Cada uno se trae al banco y se mide (`medir`, `puntuar`); los que fallan se
    quedan fuera. `evitar` son los ids que ya han salido en otro tramo: van al
    FINAL de la lista y no fuera de ella, porque repetir un tema en un video
    largo es mejor que un tramo mudo.
    """
    evitar = {str(i) for i in (evitar or [])}
    medidos = []
    for ficha in candidatos or []:
        try:
            ruta = traer(ficha, "musica")
            medida = medir(ruta)
            # los propios se miden siempre (su ficha puede ser de un fichero
            # anterior con el mismo nombre); los de fuera, si no la traen
            if ficha.get("fuente") == "propia" or not float(ficha.get("duracion") or 0):
                ficha["duracion"] = round(float(medios.duracion_media(ruta) or 0), 2)
        except Exception as fallo:                     # un tema caido no corta
            ficha["error"] = str(fallo)[:120]
            continue
        ficha["medida"] = medida
        ficha["punto"] = round(puntuar(medida), 1)
        medidos.append(ficha)
    medidos.sort(key=lambda f: (str(f.get("id")) in evitar, -float(f.get("punto") or 0)))
    return medidos


def elegir_tema(candidatos, evitar=()):
    """El mejor candidato para llevar voz encima, o None. Ver `ordenar_temas`.

    Los de `evitar` no se eligen aqui: quien quiera repetir, que lo pida.
    """
    evitar = {str(i) for i in (evitar or [])}
    for ficha in ordenar_temas(candidatos, evitar):
        if str(ficha.get("id")) not in evitar:
            return ficha
    return None


# ------------------------------------------- los trozos buenos de una cancion
#
# DE UNA CANCION NO SE USA TODO. Las de la Biblioteca de audio de YouTube y
# parecidas tienen buenos momentos y otros que no: una intro de diez segundos
# de silencio, un final que cae, un golpe de bateria que se come la voz. Asi
# que lo que entra en la cama son TROZOS:
#
#   · los que hayas marcado en su ficha (`fragmentos`), si hay. Mandan siempre:
#     que momento es bueno lo decide quien lo escucha.
#   · si no hay ninguno, los que salen de mirar su energia (`partes_automaticas`):
#     fuera lo flojo (silencios, intros y colas muy bajas), fuera los picos que
#     pelean con la voz, y fuera los ultimos segundos, que son la caida del final.

#: Por debajo de esto respecto a lo tipico de la cancion, es silencio, intro o
#: cola: no vale de cama.
FLOJO_DB = -10.0

#: Por encima de esto respecto a lo tipico, es un pico que se come la voz.
PICO_DB = 6.0

#: Lo que se quita siempre del final: la caida con la que acaba la cancion.
COLA_FINAL_S = 3.0

_PARTES = {}


def partes_automaticas(ruta):
    """Los trozos usables de una cancion sin trozos marcados. -> [(desde, hasta)]

    Se mide la energia por segundos. Un segundo vale si no esta muy por debajo
    de lo tipico de la cancion (FLOJO_DB) ni muy por encima (PICO_DB); los
    ultimos COLA_FINAL_S nunca valen. Los tramos seguidos que valen y duran al
    menos MIN_FRAGMENTO_S son los trozos. Si no sale ninguno, la cancion entera
    menos su cola: mejor algo que nada.
    """
    try:
        estado = os.stat(ruta)
        clave = (os.path.normcase(os.path.abspath(ruta)), estado.st_size, estado.st_mtime)
    except OSError:
        clave = None
    if clave and clave in _PARTES:
        return list(_PARTES[clave])
    muestras = _leer(_a_wav(ruta))
    mono = muestras.mean(axis=1) if muestras.ndim > 1 else muestras
    total = len(mono) / float(FRECUENCIA)
    segundos = int(len(mono) // FRECUENCIA)
    if segundos < 2:
        return [(0.0, round(total, 2))] if total > 0 else []
    trozos = mono[:segundos * FRECUENCIA].reshape(segundos, FRECUENCIA)
    db = 20.0 * np.log10(np.sqrt(np.mean(np.square(trozos), axis=1)) + 1e-9)
    tipico = float(np.median(db))
    vale = (db >= tipico + FLOJO_DB) & (db <= tipico + PICO_DB)
    cola = int(np.ceil(COLA_FINAL_S))
    vale[max(0, segundos - cola):] = False
    partes, inicio = [], None
    for indice, bueno in enumerate(list(vale) + [False]):
        if bueno and inicio is None:
            inicio = indice
        elif not bueno and inicio is not None:
            if indice - inicio >= MIN_FRAGMENTO_S:
                partes.append((float(inicio), float(indice)))
            inicio = None
    if not partes:
        partes = [(0.0, round(max(1.0, total - COLA_FINAL_S), 2))]
    if clave:
        _PARTES[clave] = list(partes)
    return partes


def partes_utiles(ficha, ruta=None):
    """Los trozos de una cancion que se pueden usar. -> [(desde, hasta)]"""
    ruta = ruta or traer(ficha, "musica")
    # la del FICHERO, no la de la ficha: si cambias el fichero por otro con el
    # mismo nombre, la ficha guarda la duracion del de antes
    try:
        duracion = float(medios.duracion_media(ruta) or 0)
    except Exception:                                        # noqa: BLE001
        duracion = float(ficha.get("duracion") or 0)
    marcados = []
    for trozo in ficha.get("fragmentos") or []:
        try:
            desde = float(trozo["desde"])
            hasta = float(trozo["hasta"])
        except (KeyError, TypeError, ValueError):
            continue
        if duracion:
            hasta = min(hasta, duracion)
        if hasta - desde >= 1.0:
            marcados.append((desde, hasta))
    return marcados or partes_automaticas(ruta)


#: Lo de una cancion que viaja en cada trozo puesto en la cama.
_CAMPOS_PIEZA = ("fuente", "id", "titulo", "artista", "licencia", "archivo",
                 "origen", "atribucion_requerida", "texto_credito",
                 "codigo_credito", "duracion")


def llenar_tramo(largo, candidatos):
    """Trozos de canciones que cubren `largo` segundos. -> [pieza]

    Se recorren los candidatos en su orden y, de cada uno, sus trozos buenos,
    hasta cubrir el tramo contando lo que se comen los fundidos entre trozos.
    Si no alcanza, se VUELVE a empezar por el primero: un trozo repetido con
    fundido suena a cancion, un salto al principio del fichero suena a error.
    """
    piezas, cubierto = [], 0.0
    fuentes = []
    for ficha in candidatos:
        try:
            partes = partes_utiles(ficha)
        except Exception:                                    # noqa: BLE001
            continue
        if partes:
            fuentes.append((ficha, partes))
    if not fuentes:
        return []
    for _vuelta in range(50):
        for ficha, partes in fuentes:
            for desde, hasta in partes:
                aporta = (hasta - desde) - (CRUCE_PIEZA_S if piezas else 0.0)
                if aporta <= 0.5:
                    continue
                piezas.append({**{k: ficha.get(k) for k in _CAMPOS_PIEZA
                                  if ficha.get(k) not in (None, "")},
                               "desde": round(desde, 2), "hasta": round(hasta, 2)})
                cubierto += aporta
                if cubierto >= largo:
                    return piezas
    return piezas


def _candidatos_de(tramo, fijada=None):
    """Las canciones que pueden ir en un tramo, de la primera fuente que tenga.

    De la carpeta propia: la FIJADA a mano primero (si sigue en la carpeta),
    luego las del animo del tramo, y si ninguna es de ese animo, cualquiera de
    la carpeta (con la carpeta primero, lo de fuera solo entra si esta vacia).
    -> (lista, fijada_encontrada)
    """
    for fuente in fuentes_de("musica"):
        if fuente == "propia":
            lista = propios("musica")
            if not lista:
                continue
            fija = [f for f in lista if fijada and f.get("archivo") == fijada]
            resto = [f for f in lista if f not in fija]
            del_animo = [f for f in resto if f.get("animo") == tramo["animo"]
                         or tramo["animo"] in (f.get("etiquetas") or [])]
            return fija + (del_animo or resto), bool(fija)
        if fuente == "jamendo" and hay_claves()[0]:
            candidatos = buscar_musica(
                animo=tramo["animo"], cuantas=6,
                duracion_s=(tramo["hasta"] - tramo["desde"]),
                velocidad=tramo["velocidad"])
            if candidatos:
                return candidatos, False
    return [], False


def montar_banda(escenas, duracion_s, avisar=None, tramos=None, fijadas=None):
    """La banda sonora entera, decidida sola. Devuelve la ficha para params.

    NO deja fichero: deja escrito QUE trozos de que canciones van en cada
    tramo. La cama se construye al renderizar (`construir_cama`), sin salir a la
    red, desde los ficheros que esto ha dejado en el banco. Es el mismo contrato
    que todo lo demas: lo que se guarda es la decision, y el mismo plan suena
    igual siempre.

    UN TRAMO PUEDE LLEVAR VARIAS CANCIONES. Con canciones de uno a tres minutos
    y tramos de cuatro, repetir una sola desde el principio se oia como un
    salto; ahora el tramo se llena con los trozos buenos de las mejores
    canciones de su animo, empalmados con fundidos (`llenar_tramo`).

    `fijadas` es {indice de tramo: fichero de tu carpeta}: esa cancion va
    primero en ese tramo, sea del animo que sea. Es lo que se elige a mano en
    la pantalla del video.
    """
    avisar = avisar or (lambda *a, **k: None)
    fijadas = {str(k): str(v) for k, v in (fijadas or {}).items() if v}
    arco = tramos or arco_del_video(escenas, duracion_s)
    if not arco:
        raise RuntimeError("no hay planos con tiempos: no se puede leer el ritmo")
    if not hay_musica():
        raise RuntimeError(falta_musica_texto())
    total = float(duracion_s or 0) or float(arco[-1]["hasta"])
    puestos, usados, avisos = [], [], []
    for tramo in arco:
        avisar(0.1 + 0.8 * tramo["i"] / max(1, len(arco)),
               f"tramo {tramo['i'] + 1} de {len(arco)}: buscando algo "
               f"«{tramo['animo']}»")
        fijada = fijadas.get(str(tramo["i"]))
        candidatos, encontrada = _candidatos_de(tramo, fijada)
        if fijada and not encontrada:
            avisos.append(f"tramo {tramo['i'] + 1}: «{fijada}» ya no está en tu "
                          "carpeta (o no tiene ficha); se ha elegido otra")
        ordenados = ordenar_temas(candidatos, evitar=usados)
        if encontrada:
            # la fijada va primero aunque otra puntue mejor: la has elegido tu
            fija = [f for f in ordenados if f.get("archivo") == fijada]
            ordenados = fija + [f for f in ordenados if f not in fija]
        cola = CRUCE_S if tramo is not arco[-1] else 0.0
        largo = total * float(tramo.get("fraccion") or 0) + cola
        piezas = llenar_tramo(largo, ordenados)
        if not piezas:
            raise RuntimeError(f"tramo {tramo['i'] + 1}: no hay ningun tema "
                               f"«{tramo['animo']}» que se pueda usar ("
                               + falta_musica_texto() + ")")
        principal = next(f for f in ordenados if f.get("id") == piezas[0].get("id"))
        for pieza in piezas:
            if str(pieza.get("id")) not in usados:
                usados.append(str(pieza.get("id")))
        puestos.append({**{k: principal.get(k) for k in
                           ("fuente", "id", "titulo", "artista", "licencia",
                            "duracion", "medida", "punto")},
                        # los de la carpeta propia: con que se encuentra el
                        # fichero y que credito pide
                        **{k: principal.get(k) for k in
                           ("archivo", "origen", "atribucion_requerida",
                            "texto_credito", "codigo_credito")
                           if principal.get(k) not in (None, "")},
                        "piezas": piezas, "fijada": fijada if encontrada else "",
                        "animo": tramo["animo"], "velocidad": tramo["velocidad"],
                        "fraccion": tramo["fraccion"], "planos": tramo["planos"],
                        "desde": tramo["desde"], "hasta": tramo["hasta"],
                        "por_que": ("elegida a mano" if encontrada
                                    else tramo["por_que"])})
    return {"tramos": puestos, "ganancia_db": CAMA_GANANCIA_DB,
            "cruce_s": CRUCE_S, "modo": "cama", "avisos": avisos}


def _ffmpeg(*args):
    subprocess.run([medios.ffmpeg(), "-y", "-loglevel", "error", *args],
                   capture_output=True, text=True, timeout=600, check=True,
                   **medios.SIN_VENTANA)


def _tramo_de_piezas(piezas, quiere, destino, carpeta, faltan):
    """Los trozos de un tramo, empalmados con fundido, al largo exacto. -> bool"""
    hechos = []
    for pieza in piezas:
        origen = banco("musica", _nombre_de(pieza))
        if not os.path.exists(origen):
            nombre = pieza.get("titulo") or pieza.get("id")
            if nombre not in faltan:
                faltan.append(nombre)
            continue
        desde = float(pieza.get("desde") or 0.0)
        hasta = float(pieza.get("hasta") or 0.0)
        largo = hasta - desde
        if largo <= 0.5:
            continue
        salida = os.path.join(carpeta, f"{os.path.splitext(os.path.basename(destino))[0]}"
                                       f"_p{len(hechos)}.wav")
        # loudnorm en CADA trozo: dos canciones distintas no suenan igual de alto
        _ffmpeg("-ss", f"{desde:.3f}", "-t", f"{largo:.3f}", "-i", origen,
                "-af", f"loudnorm=I={MUSICA_LUFS:.0f}:TP=-2:LRA=11",
                "-ar", str(FRECUENCIA), "-ac", "2", salida)
        hechos.append((salida, largo))
    if not hechos:
        return False
    cadena, largo_cadena = hechos[0]
    for indice, (siguiente, largo) in enumerate(hechos[1:], start=1):
        fundido = max(0.1, min(CRUCE_PIEZA_S, 0.4 * largo_cadena, 0.4 * largo))
        junto = os.path.join(carpeta, f"{os.path.splitext(os.path.basename(destino))[0]}"
                                      f"_c{indice}.wav")
        _ffmpeg("-i", cadena, "-i", siguiente, "-filter_complex",
                f"[0:a][1:a]acrossfade=d={fundido:.2f}:c1=tri:c2=tri", junto)
        cadena, largo_cadena = junto, largo_cadena + largo - fundido
    # al largo EXACTO del tramo: se recorta, o se rellena si faltara algo
    _ffmpeg("-i", cadena, "-af", f"apad=whole_dur={quiere:.3f},atrim=0:{quiere:.3f}",
            "-ar", str(FRECUENCIA), "-ac", "2", destino)
    return True


def construir_cama(ficha, duracion_s, destino):
    """Encadena los temas de los tramos en UNA pista del largo del video.

    No sale a la red: coge del banco lo que dicen los params. Si falta algun
    fichero se sigue con los que haya —un video con tres cuartos de banda
    sonora es mejor que uno mudo— y se dice cuales faltaban.

    Los pasos son los del motor anterior (docs/08 §C) y cada uno esta por algo:
      · loudnorm a -23 en CADA tramo antes de encadenar, o se oye el salto de
        volumen entre un tema y el siguiente
      · los trozos buenos de cada tramo (`piezas`) empalmados con fundidos
        cortos; un tramo de antes, sin piezas, repite su tema con `-stream_loop`
      · `acrossfade` de seis segundos entre tramos, que es lo que hace que el
        cambio suene a montaje y no a lista de reproduccion
      · relleno y recorte al largo EXACTO, con fundido de entrada y de salida
    """
    tramos = [t for t in (ficha or {}).get("tramos") or [] if t.get("id")]
    if not tramos:
        return None, []
    cruce = float((ficha or {}).get("cruce_s") or CRUCE_S)
    total = float(duracion_s)
    carpeta = os.path.dirname(os.path.abspath(destino))
    os.makedirs(carpeta, exist_ok=True)

    trozos, faltan = [], []
    for tramo in tramos:
        # el ultimo no necesita cola de cruce: no se encadena con nada
        cola = cruce if tramo is not tramos[-1] else 0.0
        quiere = max(1.0, total * float(tramo.get("fraccion") or 0) + cola)
        trozo = os.path.join(carpeta, f"tramo{len(trozos)}.wav")
        if tramo.get("piezas"):
            if _tramo_de_piezas(tramo["piezas"], quiere, trozo, carpeta, faltan):
                trozos.append(trozo)
            continue
        origen = banco("musica", _nombre_de(tramo))
        if not os.path.exists(origen):
            faltan.append(tramo.get("titulo") or tramo.get("id"))
            continue
        corto = float(tramo.get("duracion") or 0) < quiere + 1
        orden = []
        if corto:
            orden += ["-stream_loop", "3"]
        orden += ["-i", origen, "-t", f"{quiere:.2f}",
                  "-af", f"loudnorm=I={MUSICA_LUFS:.0f}:TP=-2:LRA=11",
                  "-ar", str(FRECUENCIA), "-ac", "2", trozo]
        _ffmpeg(*orden)
        trozos.append(trozo)
    if not trozos:
        return None, faltan

    cadena = trozos[0]
    for indice in range(1, len(trozos)):
        siguiente = os.path.join(carpeta, f"cadena{indice}.wav")
        _ffmpeg("-i", cadena, "-i", trozos[indice], "-filter_complex",
                f"[0:a][1:a]acrossfade=d={cruce:g}:c1=tri:c2=tri", siguiente)
        cadena = siguiente
    salida = float(min(cruce, max(1.0, total * 0.05)))
    _ffmpeg("-i", cadena, "-af",
            f"apad=whole_dur={total + 1:.2f},atrim=0:{total:.3f},"
            f"afade=t=in:st=0:d=3,afade=t=out:st={max(0.0, total - salida):.2f}:d={salida:g}",
            "-ar", str(FRECUENCIA), "-ac", "2", destino)
    return destino, faltan


# ---------------------------------------------------------------- Freesound

def buscar_efectos(consulta, dur_min=0.2, dur_max=8.0, cuantos=15):
    """Efectos de Freesound para una consulta, ordenados por descargas.

    `ac_analysis` viene cuando el sonido lo tiene analizado y describe COMO
    suena (brillo, dureza, reverb). No se filtra por el -- muchos sonidos buenos
    no lo traen -- pero se devuelve para poder elegir mirando.
    """
    claves = _claves()
    if not claves.get("FREESOUND_API_KEY"):
        raise RuntimeError("falta FREESOUND_API_KEY: ponla en Configuracion")
    seguras = solo_seguras()
    tope = max(1, min(50, int(cuantos)))
    respuesta = requests.get(
        "https://freesound.org/apiv2/search/text/", timeout=45,
        headers={"Authorization": "Token " + claves["FREESOUND_API_KEY"]},
        params={"query": consulta,
                "filter": f"duration:[{dur_min} TO {dur_max}]",
                "fields": ("id,name,tags,duration,license,username,url,previews,"
                           "ac_analysis"),
                "sort": "downloads_desc",
                # con el filtro de licencias se piden mas: parte se queda fuera
                "page_size": min(150, tope * 3) if seguras else tope})
    if respuesta.status_code != 200:
        raise RuntimeError(f"Freesound {respuesta.status_code}: "
                           f"{respuesta.text[:200]}")
    fichas = [_ficha_efecto(s) for s in respuesta.json().get("results") or []]
    # SOLO LO MONETIZABLE: CC0 o CC BY. Fuera NC y el viejo Sampling+.
    if seguras:
        fichas = [f for f in fichas if licencia_segura(f.get("licencia"))]
    return fichas[:tope]


def _ficha_efecto(crudo):
    ac = crudo.get("ac_analysis") or {}
    previos = crudo.get("previews") or {}
    return {
        "fuente": "freesound",
        "id": str(crudo.get("id")),
        "titulo": crudo.get("name") or "",
        "autor": crudo.get("username") or "",
        "duracion": float(crudo.get("duration") or 0),
        "licencia": crudo.get("license") or "",
        "pagina": crudo.get("url") or "",
        # el preview de alta basta con el token; el WAV original pide OAuth2
        "descarga": previos.get("preview-hq-mp3") or "",
        "escucha": previos.get("preview-hq-mp3") or "",
        "etiquetas": list(crudo.get("tags") or [])[:8],
        # como SUENA, cuando Freesound lo tiene analizado. No se filtra por
        # ello -- muchos sonidos buenos no lo traen -- pero viaja para poder
        # elegir mirando.
        "brillo": ac.get("ac_brightness"),
        "dureza": ac.get("ac_hardness"),
        "reverb": ac.get("ac_reverb"),
    }


# ------------------------------------------------------------------- banco

def _nombre_de(ficha, extension=".mp3"):
    # lo propio conserva su extension: un WAV de la Biblioteca de YouTube no se
    # llama .mp3 en el banco
    if extension == ".mp3" and ficha.get("fuente") == "propia" and ficha.get("archivo"):
        extension = os.path.splitext(str(ficha["archivo"]))[1].lower() or ".mp3"
    return f"{ficha.get('fuente', 'x')}_{ficha.get('id', '0')}{extension}"


def clave_de(ficha):
    """El identificador de un efecto: de donde sale y su id alli.

    El mismo con el que se guarda en el banco, sin extension. Es lo que
    identifica un SONIDO, no un papel: un laser vetado como golpe de transicion
    tampoco puede volver como aire de transicion.
    """
    if isinstance(ficha, str):
        return ficha.strip()
    return _nombre_de(ficha or {}, "")


# ------------------------------------------------------------- los vetados
#
# UN EFECTO QUE NO GUSTA SE VETA, NO SE BORRA. Decision del 21-08-2026 despues
# de oir el primer video montado: «de forma recurrente suena un sonido que es
# como una especie de laser que no me gusta nada». El resto de la banda si
# gustaba, asi que lo que hay que quitar es UNO.
#
# Y borrar su fichero del banco no lo quita: la proxima vez que alguien pulse
# «buscar mas», Freesound lo devuelve --ordena por descargas, o sea que los
# mismos suenan siempre-- y se vuelve a bajar. Un veto es una decision, y una
# decision se guarda.
#
# ES DEL CANAL, como el banco y por el mismo motivo: un sonido que no gusta no
# gusta en el video siguiente tampoco. Por eso vive aqui al lado de los
# ficheros y no en los params de un proyecto -- que es la unica diferencia con
# `camara_vetada`, que si es de un plano concreto de un video concreto.
#
# Y muerde en los DOS sitios, que es lo que lo hace de verdad:
#     surtir()   no lo baja ni lo mete en el surtido    -> no vuelve
#     elegir()   no lo reparte aunque este en el surtido -> deja de sonar YA,
#                sin tener que volver a surtir ni pagar una busqueda

def _fichero_vetados():
    return os.path.join(banco("efectos"), "vetados.json")


def vetados():
    """Los efectos vetados del canal: {clave: ficha}."""
    ruta = _fichero_vetados()
    if not os.path.exists(ruta):
        return {}
    try:
        with open(ruta, "r", encoding="utf-8-sig") as fh:
            datos = json.load(fh)
    except (OSError, ValueError):
        return {}
    fuera = {}
    for ficha in (datos or {}).get("vetados") or []:
        if isinstance(ficha, dict) and ficha.get("clave"):
            fuera[str(ficha["clave"])] = ficha
    return fuera


def esta_vetado(ficha, lista=None):
    """Si este efecto esta vetado en el canal."""
    clave = clave_de(ficha)
    return bool(clave) and clave in (vetados() if lista is None else lista)


def _guardar_vetados(fichas):
    ruta = _fichero_vetados()
    temporal = ruta + ".parcial"
    with open(temporal, "w", encoding="utf-8") as fh:
        json.dump({"vetados": [fichas[k] for k in sorted(fichas)]}, fh,
                  ensure_ascii=False, indent=1)
    os.replace(temporal, ruta)
    return ruta


def vetar(ficha, papel=None, motivo=""):
    """Prohibe este efecto en TODO el canal. Devuelve la ficha del veto.

    No borra el fichero del banco a proposito: borrarlo lo unico que consigue es
    que la siguiente busqueda lo baje otra vez. Y guarda de que papel se vetaba
    y con que titulo, para que la lista se pueda leer meses despues.
    """
    clave = clave_de(ficha)
    if not clave or clave.endswith("_0") or clave.startswith("x_"):
        raise ValueError("ese efecto no trae fuente e id: no se puede vetar")
    lista = vetados()
    entrada = {
        "clave": clave,
        "titulo": (ficha.get("titulo") if isinstance(ficha, dict) else "") or "",
        "autor": (ficha.get("autor") if isinstance(ficha, dict) else "") or "",
        "papel": str(papel or (ficha.get("papel") if isinstance(ficha, dict) else "") or ""),
        "motivo": str(motivo or "")[:200],
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    lista[clave] = entrada
    _guardar_vetados(lista)
    return entrada


def desvetar(ficha):
    """Levanta el veto. -> si habia algo que levantar."""
    clave = clave_de(ficha)
    lista = vetados()
    if clave not in lista:
        return False
    lista.pop(clave)
    _guardar_vetados(lista)
    return True


#: Por encima de que frecuencia se cuenta el brillo de un efecto, en Hz. Cuatro
#: mil es donde deja de haber cuerpo y solo queda filo.
AGUDO_HZ = 4000.0


def _medida_de(ruta, fichero, clave, calcular):
    """Una medida cara de un efecto, cacheada por fichero y fecha. -> valor o None

    LAS DOS MEDIDAS DEL BANCO PASAN POR AQUI (`agudeza` y `nivel_de`) y las dos
    hacian lo mismo por su cuenta: leer un JSON del banco, comparar el sello del
    fichero, calcular si no cuadraba y volver a escribirlo. Veinte lineas
    identicas por medida, y la tercera que se anadiera habria sido otras veinte.

    Cada una vive en su fichero (`agudeza.json`, `nivel.json`) y guarda su valor
    bajo su propia `clave`: asi anadir una medida no invalida las que ya estan
    calculadas, que es lo que pasaria con un fichero comun.

    `calcular` recibe el mono en float y devuelve el numero, o None si el
    fichero no da para medirlo. Nada de esto levanta nunca: una medida que no
    sale devuelve None y quien llama decide -- la pantalla no se cae y el render
    no se corrige, que es lo correcto en los dos casos.
    """
    if not ruta or not os.path.exists(ruta):
        return None
    nombre = os.path.basename(ruta)
    sello = f"{os.path.getmtime(ruta):.0f}:{os.path.getsize(ruta)}"
    cache_ruta = banco("efectos", fichero)
    cache = {}
    if os.path.exists(cache_ruta):
        try:
            with open(cache_ruta, "r", encoding="utf-8-sig") as fh:
                cache = json.load(fh) or {}
        except (OSError, ValueError):
            cache = {}
    guardado = cache.get(nombre)
    if isinstance(guardado, dict) and guardado.get("sello") == sello:
        return guardado.get(clave)
    try:
        muestras = _leer(_a_wav(ruta))
    except Exception:                                            # noqa: BLE001
        return None                       # un efecto ilegible no tumba la pantalla
    mono = muestras.mean(axis=1) if muestras.ndim > 1 else muestras
    if len(mono) < 64:
        return None
    valor = calcular(mono)
    if valor is None:
        return None
    cache[nombre] = {"sello": sello, clave: valor}
    try:
        temporal = cache_ruta + ".parcial"
        with open(temporal, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=1)
        os.replace(temporal, cache_ruta)
    except OSError:
        pass                              # sin cache se recalcula, no se rompe
    return valor


def agudeza(ficha):
    """Cuanta energia del efecto esta por encima de 4 kHz, de 0 a 1. O None.

    PARA ENCONTRAR EL LASER. Identificar «ese sonido que suena como una especie
    de laser» entre doce efectos es escucharlos todos, y esta cifra los ordena:
    un laser es un barrido fino y agudo, y un aire de transicion es un soplo con
    cuerpo. Medido sobre el fichero del banco, que es el que va a sonar.

    NO DECIDE NADA, y eso es a proposito: cual molesta lo dice un oido, no un
    numero. Esto solo pone el candidato arriba del todo.

    Cacheado por fichero y fecha: son una FFT por efecto y esta llamada la hace
    la pantalla de Sonido cada vez que se abre.
    """
    def calcular(mono):
        espectro = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
        hz = np.fft.rfftfreq(len(mono), 1.0 / FRECUENCIA)
        total = float((espectro ** 2).sum())
        if total <= 0:
            return None
        return round(float((espectro[hz > AGUDO_HZ] ** 2).sum()) / total, 4)

    ruta = ficha if isinstance(ficha, str) else banco("efectos", _nombre_de(ficha))
    return _medida_de(ruta, "agudeza.json", "agudo", calcular)


#: ---------------------------------------------------------- CUANTO SUENA UNO
#:
#: EL PROBLEMA QUE ESTO ARREGLA. Los seis barridos de `transicion_suave` salen
#: de un banco de stock y cada uno viene masterizado como le dio la gana a quien
#: lo subio. Medido en `video_referencia` (08-09-2026), en el mismo papel y con la misma
#: ganancia de clase:
#:
#:     freesound_449645   -18,3 LUFS      <- se oye un golpe
#:     freesound_810329   -19,1
#:     freesound_701104   -19,5
#:     freesound_802465   -35,1
#:     freesound_742833   -37,7
#:     freesound_816261   -40,6 LUFS      <- casi no se oye
#:
#: **22,3 dB de diferencia entre dos sonidos que hacen lo mismo**: un factor de
#: diez largo en lo que oye una persona. Por eso unas transiciones pegaban un
#: golpe y otras pasaban en silencio. La `ganancia` de la clase no lo arregla
#: porque es la misma para los seis, y la normalizacion de la pista tampoco: esa
#: mira el PICO de la suma, o sea que el fichero mas caliente decide el volumen
#: de todos los demas.
#:
#: SE MIDE CON PONDERACION K (ITU-R BS.1770), que es la del LUFS, y no con RMS
#: ni con el pico:
#:
#:   · el pico miente con los transientes -- una tecla seca tiene el pico de un
#:     barrido de dos segundos y se oye diez veces menos;
#:   · el RMS de un fichero con cola larga y silencio sale bajo aunque el golpe
#:     sea fuerte;
#:   · la K pondera como el oido -- realza los agudos y quita peso a los graves
#:     --, asi que un golpe brillante mide MAS que uno grave del mismo nivel.
#:     Eso es exactamente lo que se quiere: es el brillante el que molesta.
#:
#: Y se mide el MAXIMO en ventanas de 400 ms (el «momentaneo» del LUFS), no la
#: media del fichero: lo que molesta es el momento mas fuerte, no el promedio.
EFECTOS_VENTANA_S = 0.4

#: Cuanto se le puede mover a un fichero, arriba o abajo. Un tope hace falta:
#: sin el, un fichero casi mudo se subiria 25 dB y con el subiria su ruido de
#: fondo, y uno reventado se bajaria hasta desaparecer. Con 12 dB la diferencia
#: de 22 dB del banco baja a unos 3, que es la que ya no se nota.
EFECTOS_CORRECCION_MAX_DB = 12.0

#: K-weighting de ITU-R BS.1770 a 48 kHz: pre-filtro de cabeza y paso alto RLB.
#: Se aplica por FFT --multiplicando por su respuesta-- y no muestra a muestra:
#: aqui solo interesa la ENERGIA, no la fase, y un bucle IIR en Python sobre
#: cuarenta ficheros de dos segundos son varios millones de vueltas.
_K_ETAPA1_B = (1.53512485958697, -2.69169618940638, 1.19839281085285)
_K_ETAPA1_A = (1.0, -1.69065929318241, 0.73248077421585)
_K_ETAPA2_B = (1.0, -2.0, 1.0)
_K_ETAPA2_A = (1.0, -1.99004745483398, 0.99007225036621)


def _respuesta_biquad(b, a, w):
    z = np.exp(-1j * w)
    return ((b[0] + b[1] * z + b[2] * z ** 2)
            / (a[0] + a[1] * z + a[2] * z ** 2))


def _k_ponderado(mono):
    """El audio pasado por el filtro K, por el dominio de la frecuencia."""
    n = len(mono)
    hz = np.fft.rfftfreq(n, 1.0 / FRECUENCIA)
    w = 2 * np.pi * hz / FRECUENCIA
    h = (_respuesta_biquad(_K_ETAPA1_B, _K_ETAPA1_A, w)
         * _respuesta_biquad(_K_ETAPA2_B, _K_ETAPA2_A, w))
    return np.fft.irfft(np.fft.rfft(mono) * h, n=n)


def nivel_de(ruta):
    """Lo fuerte que suena ese efecto, en LUFS momentaneo maximo. O None.

    Es la cifra con la que `pista_de_efectos` iguala los ficheros de un mismo
    papel. Ver el bloque de constantes de arriba: por que ponderacion K y por
    que el maximo y no la media.

    Cacheado en `nivel.json` (ver `_medida_de`): son dos FFT por efecto y esto
    corre en cada render. Devuelve None cuando el fichero no esta o no se puede
    leer, y ahi quien llama no corrige nada -- reventar un render de veinte
    minutos por no saber medir un wav seria mucho peor.
    """
    def calcular(mono):
        y = _k_ponderado(mono)
        ventana = max(1, int(EFECTOS_VENTANA_S * FRECUENCIA))
        if len(y) <= ventana:
            # UN FICHERO MAS CORTO QUE LA VENTANA SE MIDE ENTERO, no rellenado
            # con ceros: rellenar repartiria la energia de una tecla de 50 ms
            # sobre 400 y la dejaria 9 dB por debajo de lo que suena.
            energia = float(np.mean(y ** 2))
        else:
            # ventanas solapadas al 75 %, como el momentaneo del LUFS, por suma
            # acumulada: con un bucle son miles de rebanadas por fichero
            salto = max(1, ventana // 4)
            acumulado = np.concatenate(([0.0], np.cumsum(y.astype(np.float64) ** 2)))
            inicios = np.arange(0, len(y) - ventana + 1, salto)
            energia = float(np.max(
                (acumulado[inicios + ventana] - acumulado[inicios]) / ventana))
        return round(-0.691 + 10.0 * float(np.log10(max(energia, 1e-12))), 2)

    return _medida_de(ruta, "nivel.json", "lufs", calcular)


def traer(ficha, familia):
    """Descarga el fichero al banco si no esta, y devuelve su ruta.

    Idempotente: un fichero que ya esta no se vuelve a pedir. El banco es del
    canal, asi que el segundo video no baja nada.
    """
    destino = banco(familia, _nombre_de(ficha))
    if os.path.exists(destino) and os.path.getsize(destino) > 2000:
        return destino
    if ficha.get("fuente") == "propia":
        # DE TU CARPETA AL BANCO, sin red. Se copia (no se enlaza) para que el
        # render no dependa de que el fichero siga en la carpeta: el id lleva la
        # huella del contenido, asi que un fichero cambiado es otra copia.
        origen = os.path.join(carpeta_propia(familia),
                              os.path.basename(str(ficha.get("archivo") or "")))
        if not ficha.get("archivo") or not os.path.isfile(origen):
            raise RuntimeError(f"«{ficha.get('titulo') or ficha.get('id')}» ya no "
                               f"esta en tu carpeta de {familia}")
        temporal = destino + ".parcial"
        shutil.copyfile(origen, temporal)
        os.replace(temporal, destino)
        return destino
    url = ficha.get("descarga") or ""
    if not url:
        raise RuntimeError(f"{ficha.get('id')}: no trae URL de descarga")
    cabeceras = {}
    if ficha.get("fuente") == "freesound":
        claves = _claves()
        if claves.get("FREESOUND_API_KEY"):
            cabeceras["Authorization"] = "Token " + claves["FREESOUND_API_KEY"]
    respuesta = requests.get(url, timeout=180, headers=cabeceras, stream=True)
    respuesta.raise_for_status()
    temporal = destino + ".parcial"
    with open(temporal, "wb") as fh:
        for trozo in respuesta.iter_content(1 << 16):
            fh.write(trozo)
    os.replace(temporal, destino)
    return destino


def surtir(papel, cuantos=None, salteado=0):
    """Busca efectos para un papel y los deja descargados. Devuelve sus fichas.

    Recorre las VARIAS consultas del papel en vez de pedir siempre lo mismo:
    con una sola, Freesound devuelve los mismos diez ficheros a todo el mundo y
    todos los videos suenan igual.
    """
    ficha_papel = PAPELES.get(papel)
    if not ficha_papel:
        raise RuntimeError(f"papel de efecto desconocido: {papel}")
    cuantos = int(cuantos or ficha_papel["cuantos"])
    dur_min, dur_max = ficha_papel["duracion"]
    salida = []
    # LO VETADO NO VUELVE. Freesound ordena por descargas, o sea que devuelve
    # los mismos ficheros a todo el mundo y a la tercera busqueda vuelve a salir
    # el mismo laser. Borrarlo del banco no serviria de nada: se bajaria otra
    # vez. Ver «los vetados» arriba.
    fuera = vetados()
    # LAS FUENTES EN SU ORDEN (Configuracion): la carpeta propia y Freesound.
    # Sin clave de Freesound se sigue con lo propio, sin error: un papel sin
    # efectos se queda mudo, que es lo que pasa tambien si se vetan todos.
    for fuente in fuentes_de("efectos"):
        if fuente == "propia":
            for ficha in propios("efectos"):
                if ficha.get("papel") != papel or clave_de(ficha) in fuera:
                    continue
                try:
                    traer(ficha, "efectos")
                except Exception as fallo:            # un fichero caido no corta
                    ficha["error"] = str(fallo)[:120]
                    continue
                salida.append(ficha)
                if len(salida) >= cuantos:
                    return salida
        elif fuente == "freesound" and hay_claves()[1]:
            salida.extend(_surtir_de_freesound(papel, ficha_papel, cuantos - len(salida),
                                               salteado, fuera, dur_min, dur_max))
            if len(salida) >= cuantos:
                return salida[:cuantos]
    return salida


def _surtir_de_freesound(papel, ficha_papel, cuantos, salteado, fuera, dur_min,
                         dur_max):
    """Lo de `surtir` que sale a Freesound: varias consultas, sin vetados."""
    vistos, salida = set(), []
    if cuantos <= 0:
        return salida
    consultas = list(ficha_papel["consultas"])
    for indice in range(len(consultas)):
        consulta = consultas[(indice + int(salteado)) % len(consultas)]
        for ficha in buscar_efectos(consulta, dur_min, dur_max, cuantos * 2):
            if ficha["id"] in vistos or not ficha.get("descarga"):
                continue
            if clave_de(ficha) in fuera:
                vistos.add(ficha["id"])
                continue
            vistos.add(ficha["id"])
            try:
                traer(ficha, "efectos")
            except Exception as fallo:                # un sonido caido no corta
                ficha["error"] = str(fallo)[:120]
                continue
            ficha["papel"] = papel
            salida.append(ficha)
            if len(salida) >= cuantos:
                return salida
    return salida


def elegir(fichas, semilla, *clave, vetados_de=None):
    """Un elemento del surtido, elegido SIN azar de verdad.

    «Aleatorio» aqui significa «que no se repita», no «que cambie cada vez»: dos
    renders del mismo plan tienen que sonar igual, asi que la eleccion sale de
    la semilla del video y de la clave del hueco (el plano, el papel).

    Y SI EL QUE TOCA ESTA VETADO, SE PASA AL SIGUIENTE. No se filtra la lista
    antes de indexar, y la diferencia importa: filtrando, los indices de todos
    los demas se corren y vetar un laser cambiaria tambien que aire suena en
    cada uno de los otros cuarenta cortes. Avanzando, los huecos que no habian
    elegido el vetado suenan exactamente igual que antes. Vetar uno tiene que
    quitar uno.
    """
    fichas = [f for f in (fichas or []) if f]
    if not fichas:
        return None
    fuera = vetados() if vetados_de is None else vetados_de
    arranque = medios.desempatar(semilla, *clave) % len(fichas)
    for salto in range(len(fichas)):
        ficha = fichas[(arranque + salto) % len(fichas)]
        if not fuera or clave_de(ficha) not in fuera:
            return ficha
    return None                       # todas vetadas: ese hueco se queda mudo


# ------------------------------------------------------------------ eventos

def eventos(escenas, cortes, params, semilla=0):
    """Cuando suena cada cosa. Devuelve [{t, papel, ficha, ganancia}] ordenado.

    't' esta en el reloj DEL VIDEO ya montado, o sea contando desde el primer
    plano: el video empieza en `escenas[0]['t_in']` y el audio se recorta ahi
    (ver p8._concatenar), asi que todo lo de aqui va restado por ese desfase.
    """
    surtido = (params or {}).get("efectos") or {}
    if not surtido:
        return []
    origen = float((escenas or [{}])[0].get("t_in") or 0.0)
    # los vetados se leen UNA vez: `elegir` corre una vez por tecla de una
    # cartela, o sea decenas de veces por plano, y abrir el fichero en cada una
    # seria pagar el mismo disco cuatro mil veces por video
    prohibidos = vetados()
    fuera = []

    for escena in escenas or []:
        sid = escena.get("id")
        inicio = float(escena.get("t_in") or 0.0) - origen

        # 1. LA TRANSICION, colocada por su GOLPE y no por su primera muestra.
        #
        # El fichero empieza a sonar `golpe_de` segundos antes de pegar, y eso
        # va de 0,05 a 0,90 s segun cual toque: colocarlos todos por el
        # principio los reparte por medio segundo largo a un lado y a otro del
        # corte. Lo que se fija es DONDE CAE EL GOLPE --en el centro de la
        # mezcla visual, `ANCLA_TRANSICION`-- y el arranque del fichero sale de
        # restar. Asi el adelanto deja de ser un numero y pasa a ser el que cada
        # efecto necesita para llegar a tiempo.
        corte = (cortes or {}).get(sid) or {}
        if corte.get("tipo") and corte["tipo"] != "corte":
            papel = ("transicion_acento" if corte.get("ranura") == "acento"
                     else "transicion_suave")
            ficha = elegir(surtido.get(papel), semilla, sid, papel,
                           vetados_de=prohibidos)
            if ficha:
                diana = inicio + ANCLA_TRANSICION * float(corte.get("duracion") or 0.0)
                golpe = golpe_de(banco("efectos", _nombre_de(ficha)))
                fuera.append({"t": max(0.0, diana - golpe),
                              "papel": papel, "ficha": ficha,
                              "ganancia": PAPELES[papel]["ganancia"]})

        # 2. la maquina de escribir de una cartela, letra a letra
        if not cartelas.es_cartela(escena):
            continue
        # El plano de CONTINUACION de una cartela que dura dos no se escribe:
        # sigue puesta y quieta. Un teclado sonando ahi seria justo el fallo que
        # §26.4 evito -- oir escribir cuando no se escribe.
        escritura = escena.get("escritura") if isinstance(escena.get("escritura"), dict) else {}
        if escritura.get("continua") or (escena.get("cartela") or {}).get("sigue_a"):
            continue
        duracion = float(escena.get("duracion") or
                         (escena.get("t_out", 0) - escena.get("t_in", 0)) or 4.0)
        # Los MISMOS tiempos con los que la dibuja p7: si la cartela va
        # sincronizada con la voz y el teclado no, se teclea donde no hay letras.
        tiempos = list(escritura.get("tiempos") or [])
        if not tiempos:
            antes, despues = cartelas.vecinos_de(escenas, sid)
            tiempos = cartelas.tiempos_dichos(escena["cartela"], escena,
                                              duracion=duracion,
                                              antes=antes, despues=despues)
        palabras = cartelas.tiempos_de_escritura(escena["cartela"], duracion,
                                                 tiempos=tiempos)
        for orden, (cuando, palabra) in enumerate(palabras):
            siguiente = palabras[orden + 1][0] if orden + 1 < len(palabras) else \
                cuando + 0.28
            hueco = max(0.04, min(siguiente - cuando, 0.5))
            cuantas = max(1, min(TECLAS_POR_PALABRA, len(palabra.strip()),
                                 int(hueco * TECLAS_POR_SEGUNDO)))
            for golpe in range(cuantas):
                ficha = elegir(surtido.get("tecla"), semilla, sid, orden, golpe,
                               vetados_de=prohibidos)
                if not ficha:
                    break
                fuera.append({
                    "t": inicio + cuando + hueco * golpe / max(1, cuantas),
                    "papel": "tecla", "ficha": ficha,
                    # las pulsaciones no suenan todas igual de fuerte: un
                    # teclado con todas las teclas al mismo volumen suena a
                    # bucle, no a alguien escribiendo
                    "ganancia": PAPELES["tecla"]["ganancia"] * (
                        0.78 + 0.22 * ((medios.desempatar(semilla, sid, orden,
                                                          golpe, "vol") % 100) / 99.0)),
                })
        if palabras:
            ficha = elegir(surtido.get("retorno"), semilla, sid, "retorno",
                           vetados_de=prohibidos)
            if ficha:
                fuera.append({"t": inicio + palabras[-1][0] + 0.32,
                              "papel": "retorno", "ficha": ficha,
                              "ganancia": PAPELES["retorno"]["ganancia"]})
    return sorted(fuera, key=lambda e: e["t"])


# ------------------------------------------------------------------ mezcla

def _a_wav(origen, destino=None):
    """Decodifica a WAV 48k estereo. Se cachea al lado del fichero del banco.

    NUNCA AL LADO DE LO TUYO: la copia de trabajo de un fichero de tu carpeta
    propia va a `banco/audio/cache_wav`. Al lado del original aparecia en tu
    carpeta como si fuera otra cancion (y se volvia a convertir a si misma).
    """
    if not destino:
        propio = os.path.normcase(os.path.abspath(banco("propio")))
        absoluto = os.path.abspath(origen)
        if os.path.normcase(absoluto).startswith(propio + os.sep):
            relativo = os.path.relpath(absoluto, banco("propio"))
            nombre = re.sub(r"[^A-Za-z0-9._-]+", "_", relativo)
            destino = os.path.join(banco("cache_wav"), f"{nombre}.{FRECUENCIA}.wav")
        else:
            destino = os.path.splitext(origen)[0] + f".{FRECUENCIA}.wav"
    if os.path.exists(destino) and os.path.getmtime(destino) >= os.path.getmtime(origen):
        return destino
    proceso = subprocess.run(
        [medios.ffmpeg(), "-y", "-loglevel", "error", "-i", origen,
         "-ac", "2", "-ar", str(FRECUENCIA), "-c:a", "pcm_s16le", destino],
        capture_output=True, text=True, timeout=300, **medios.SIN_VENTANA)
    if proceso.returncode != 0 or not os.path.exists(destino):
        raise RuntimeError(f"no se pudo decodificar {os.path.basename(origen)}: "
                           f"{proceso.stderr[-200:]}")
    return destino


def _leer(ruta):
    """El WAV como float32 estereo en -1..1."""
    import wave                                        # noqa: PLC0415
    with wave.open(ruta, "rb") as fh:
        canales, ancho = fh.getnchannels(), fh.getsampwidth()
        crudo = fh.readframes(fh.getnframes())
    if ancho != 2:
        raise RuntimeError(f"{os.path.basename(ruta)}: se esperaba PCM de 16 bits")
    datos = np.frombuffer(crudo, dtype="<i2").astype(np.float32) / 32768.0
    if canales == 1:
        return np.stack([datos, datos], axis=1)
    return datos.reshape(-1, 2)[:, :2]


def _escribir(ruta, muestras):
    import wave                                        # noqa: PLC0415
    pcm = np.clip(muestras, -1.0, 1.0) * 32767.0
    with wave.open(ruta, "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(FRECUENCIA)
        fh.writeframes(pcm.astype("<i2").tobytes())
    return ruta


def igualar_por_papel(lista):
    """Cuanto hay que corregir cada fichero para que su papel suene parejo.

    -> {ruta: factor}   (1.0 = se queda como esta)

    EL PROBLEMA, con numeros: los seis barridos de `transicion_suave` de
    `video_referencia` iban de -18,3 a -40,6 LUFS, o sea **22,3 dB entre dos sonidos que
    hacen exactamente lo mismo**. Unas transiciones pegaban un golpe y otras
    pasaban de largo, y no habia nada en el sistema que lo corrigiera: la
    `ganancia` es de la CLASE --la misma para los seis-- y la normalizacion de
    la pista mira el PICO DE LA SUMA, o sea que el fichero mas caliente decide
    el volumen de todos los demas.

    LA REFERENCIA ES LA MEDIANA DE SU PAPEL, y no el mas bajo ni el mas alto:
    con la mediana la clase se queda donde estaba --donde se calibro su
    `ganancia` de oido-- y lo unico que desaparece es la diferencia. Bajarlos
    todos al mas flojo apagaria los efectos, y subirlos al mas fuerte los
    dispararia; las dos cosas son otra decision, y esa la toma el mando general
    (`efectos_db`), no esto.

    Y SE MIDE CON PONDERACION K (ver `nivel_de`), que es la que pesa como el
    oido: un golpe brillante mide mas que uno grave del mismo nivel, asi que el
    que baja mas es justo el que molesta. Uno grave y fuerte se queda casi como
    estaba, que es lo que se oye bien.

    Un fichero que no se puede medir se queda en 1.0: sin medida no se corrige.
    """
    por_papel = {}
    for evento in lista or []:
        ficha = evento.get("ficha") or {}
        ruta = banco("efectos", _nombre_de(ficha))
        if not os.path.exists(ruta):
            continue
        por_papel.setdefault(str(evento.get("papel") or ""), {}).setdefault(ruta, None)
    factores = {}
    for rutas in por_papel.values():
        niveles = {}
        for ruta in rutas:
            valor = nivel_de(ruta)
            if valor is not None:
                niveles[ruta] = float(valor)
        if len(niveles) < 2:
            continue                # con uno solo no hay con que compararlo
        referencia = float(np.median(list(niveles.values())))
        for ruta, valor in niveles.items():
            db = max(-EFECTOS_CORRECCION_MAX_DB,
                     min(EFECTOS_CORRECCION_MAX_DB, referencia - valor))
            factores[ruta] = 10.0 ** (db / 20.0)
    return factores


def pista_de_efectos(lista, duracion_s, destino, igualar=True):
    """Suma todos los efectos en un WAV del largo del video.

    En numpy y no con un grafo de ffmpeg: una cartela de seis palabras son ya
    treinta y tantas teclas, y en ffmpeg cada evento es una entrada mas y un
    `adelay` mas. Aqui es sumar en un buffer.

    `igualar` pone todos los ficheros de un mismo papel al mismo volumen antes
    de sumarlos (`igualar_por_papel`). Se puede apagar para comparar, y solo
    para eso: en un video de verdad va puesto siempre.
    """
    total = int(max(1, round(float(duracion_s) * FRECUENCIA)))
    bus = np.zeros((total, 2), dtype=np.float32)
    factores = igualar_por_papel(lista) if igualar else {}
    cache, usados = {}, 0
    for evento in lista or []:
        ficha = evento.get("ficha") or {}
        origen = banco("efectos", _nombre_de(ficha))
        if not os.path.exists(origen):
            continue                                   # no esta en el banco: se calla
        if origen not in cache:
            cache[origen] = _leer(_a_wav(origen))
        muestra = cache[origen]
        inicio = int(round(max(0.0, float(evento.get("t") or 0.0)) * FRECUENCIA))
        if inicio >= total:
            continue
        trozo = muestra[:total - inicio]
        # la correccion del FICHERO multiplica a la ganancia del EVENTO: la
        # primera iguala lo que el banco trae desigual y la segunda es la
        # intencion (la clase, y el toque aleatorio de cada tecla)
        ganancia = (float(evento.get("ganancia") or 1.0)
                    * factores.get(origen, 1.0))
        bus[inicio:inicio + len(trozo)] += trozo * ganancia
        usados += 1
    pico = float(np.max(np.abs(bus))) if usados else 0.0
    if pico > 0:
        # a pico -6 dBFS: los efectos acompañan, no compiten con la voz
        bus *= (10.0 ** (EFECTOS_PICO_DB / 20.0)) / pico
    _escribir(destino, bus)
    return destino, usados


def filtro_de_mezcla(con_musica, con_efectos, duracion_s, lufs=MUSICA_LUFS,
                     ya_normalizada=False, ajuste_db=0.0, master=False,
                     medida=None, efectos_db=0.0):
    """El grafo de ffmpeg que junta voz, musica agachada y efectos.

    Las tres pistas se recortan al MISMO largo antes de mezclarse: con
    `-stream_loop -1` la musica es infinita, y un `amix` con una entrada
    infinita no termina nunca.

    `ya_normalizada` es para la CAMA: cada tramo se normalizo por separado al
    montarla, que es lo que evita el salto de volumen al cambiar de tema. Volver
    a pasarle un `loudnorm` a la cama entera desharia justo eso —mediria una
    pista con cuatro colores como si fuera una— asi que ahi solo se ajusta la
    ganancia, que es el unico numero calibrado (`CAMA_GANANCIA_DB`).

    Con `master` el grafo acaba en el nivel de salida del video (ver
    `filtro_master`): sin `medida` es la pasada que MIDE y no escribe nada, y
    con ella la que aplica la ganancia. El limitador se queda delante de los dos
    y no se toca: lo que impide que la suma de tres pistas recorte no puede
    depender de que la medida haya salido bien.
    """
    partes = ["[1:a]aresample=%d,aformat=channel_layouts=stereo,"
              "apad,atrim=0:%.3f[voz]" % (FRECUENCIA, duracion_s)]
    entradas = ["[vozmix]"]
    if con_musica:
        partes.append("[voz]asplit=2[vozmix][vozllave]")
        # LA SUBIDA VA EN LAS DOS RAMAS. En la cama se suma a su ganancia
        # calibrada; en el tema suelto va como un `volume` DETRAS del loudnorm
        # y no moviendo su objetivo, porque `lufs` es tambien el objetivo con el
        # que se normalizo cada tramo de la cama al montarla (MUSICA_LUFS): si
        # se tocara ahi, la subida se aplicaria dos veces en un video con cama.
        # EL AJUSTE DEL VIDEO va con la subida del canal, en la misma suma. Es
        # el mando que mueve el repaso cuando alguien dice «la musica esta
        # alta»: un numero en dB, y remuxear. No toca ninguna imagen ni ningun
        # clip, asi que bajar la musica cuesta lo que tarda el mux.
        subida = MUSICA_SUBIDA_DB + float(ajuste_db or 0.0)
        nivel = (f"volume={CAMA_GANANCIA_DB + subida:g}dB"
                 if ya_normalizada
                 else f"loudnorm=I={lufs:.1f}:TP=-1.5:LRA=11,"
                      f"volume={subida:g}dB")
        partes.append("[2:a]aresample=%d,aformat=channel_layouts=stereo,"
                      "atrim=0:%.3f,%s[mus]"
                      % (FRECUENCIA, duracion_s, nivel))
        # EL DUCKING, con la llave APLANADA. Ver el bloque de constantes: con
        # la voz tal cual de llave, la musica subia 11,7 dB en el remate de cada
        # frase y se llevaba por delante la ultima palabra, que es donde suele
        # estar el dato.
        partes.append(f"[vozllave]volume={DUCK_LLAVE_SUBIDA_DB:g}dB,"
                      f"alimiter=limit={DUCK_LLAVE_TECHO:g}:attack=5:release=80"
                      f"[llave]")
        partes.append(f"[mus][llave]sidechaincompress=threshold={DUCK_UMBRAL:g}:"
                      f"ratio={DUCK_RATIO:g}:attack={DUCK_ATAQUE_MS:g}:"
                      f"release={DUCK_CAIDA_MS:g}:makeup=1[musduck]")
        entradas.append("[musduck]")
    else:
        partes.append("[voz]anull[vozmix]")
    if con_efectos:
        indice = 3 if con_musica else 2
        # CUANTOS EFECTOS, EN ESTE VIDEO. El gemelo de `ajuste_db` para la
        # musica: un numero en dB sobre la pista ya montada, o sea que moverlo
        # cuesta un remux y ni una imagen ni un clip. Va DETRAS del atrim y
        # delante del amix, que es donde la pista ya esta como va a sonar.
        #
        # Y NO en `pista_de_efectos`: si se aplicara al escribir el WAV, la
        # normalizacion a pico -6 dBFS que hay ahi se lo comeria entero.
        try:
            trim = float(efectos_db or 0.0)
        except (TypeError, ValueError):
            trim = 0.0
        partes.append("[%d:a]aresample=%d,aformat=channel_layouts=stereo,"
                      "atrim=0:%.3f%s[sfx]"
                      % (indice, FRECUENCIA, duracion_s,
                         (",volume=%g dB" % trim).replace(" ", "") if trim else ""))
        entradas.append("[sfx]")
    # `normalize=0` porque amix, por defecto, divide entre el numero de
    # entradas: la voz sonaria a un tercio por el hecho de haber puesto musica.
    # Y por eso mismo hace falta el limitador detras: las tres pistas se SUMAN,
    # y una voz que ya venia a -3 dBFS de pico mas la musica mas un golpe de
    # transicion se pasan de cero. Medido en la mezcla de prueba: pico -0,8
    # dBFS, o sea a dos decimas de recortar en cuanto un video venga mas
    # caliente. El limitador no se nota y evita el chasquido.
    partes.append("%samix=inputs=%d:duration=first:normalize=0[mezcla]"
                  % ("".join(entradas), len(entradas)))
    if master:
        partes.append("[mezcla]alimiter=limit=0.95:attack=5:release=50,"
                      + filtro_master(medida) + "[salida]")
    else:
        partes.append("[mezcla]alimiter=limit=0.95:attack=5:release=50[salida]")
    return ";".join(partes)


def describir(params):
    """Frase corta con lo que va a sonar, para la pantalla y para p8.describir."""
    p = params or {}
    if not p.get("sonido", True):
        return "sin música ni efectos"
    try:
        ajuste = float(p.get("musica_db") or 0.0)
    except (TypeError, ValueError):
        ajuste = 0.0
    trozos = []
    musica = p.get("musica") or {}
    tramos = musica.get("tramos") or []
    if tramos:
        # con un separador ASCII a proposito: esto acaba en la consola (avisos
        # de trabajo, resumenes de p8) y la de Windows es cp1252, donde una
        # flecha revienta con UnicodeEncodeError DESPUES de hacer el trabajo
        animos = " > ".join(t.get("animo") or "?" for t in tramos)
        trozos.append(f"{len(tramos)} temas encadenados ({animos}) a "
                      f"{MUSICA_LUFS + CAMA_GANANCIA_DB + MUSICA_SUBIDA_DB + ajuste:.1f} "
                      f"LUFS con ducking")
    elif musica.get("id"):
        trozos.append(f"«{musica.get('titulo') or musica['id']}» de "
                      f"{musica.get('artista') or '?'} a "
                      f"{MUSICA_LUFS + MUSICA_SUBIDA_DB + ajuste:.1f} LUFS con ducking")
    surtido = p.get("efectos") or {}
    cuantos = sum(len(v or []) for v in surtido.values())
    if cuantos:
        trozos.append(f"{cuantos} efectos en {len(surtido)} papeles")
    return " · ".join(trozos) if trozos else "sin música ni efectos"


# ------------------------------------------------------------ los creditos
#
# CREDITOS.TXT, JUNTO AL MP4 (fork, Fase 3). CC BY permite monetizar a cambio
# de citar al autor, y la Biblioteca de audio de YouTube o el plan gratis de
# Uppbeat piden su linea en la descripcion. Hacerlo a mano en cada video es
# como se acaba olvidando uno. Arriba va lo que hay que PEGAR; debajo, separado,
# todo lo que suena con su licencia, para tu registro.

_TEXTOS_CREDITOS = {
    "es": {"musica": "Música", "efectos": "Efectos de sonido",
           "nada": "No hace falta atribución.",
           "registro": "Todo lo que suena en este vídeo (para tu registro, NO lo pegues)",
           "por": "de", "si": "pide crédito", "no": "sin crédito"},
    "en": {"musica": "Music", "efectos": "Sound effects",
           "nada": "No attribution required.",
           "registro": "Everything heard in this video (for your records, do NOT paste)",
           "por": "by", "si": "credit required", "no": "no credit required"},
}


def _url_de(ficha):
    if ficha.get("fuente") == "jamendo" and ficha.get("id"):
        return f"https://www.jamendo.com/track/{ficha['id']}"
    return ficha.get("pagina") or ""


def _credito_de(ficha, textos, uppbeat_suscripcion):
    """(pide_credito, linea para pegar, aviso o '') de un tema o un efecto."""
    titulo = ficha.get("titulo") or ficha.get("id") or "?"
    autor = ficha.get("artista") or ficha.get("autor") or ""
    licencia = ficha.get("licencia") or ""
    if ficha.get("fuente") == "propia":
        origen = ficha.get("origen") or "otra"
        if origen == "uppbeat":
            if uppbeat_suscripcion:
                return False, "", ""
            texto = ficha.get("texto_credito") or ficha.get("codigo_credito") or ""
            if not texto:
                return True, f'"{titulo}" (Uppbeat) — FALTA EL CÓDIGO DE CRÉDITO', (
                    f"falta el código de crédito de Uppbeat de «{titulo}»: "
                    "con el plan gratis cada pista lo pide en la descripción")
            return True, texto, ""
        if not ficha.get("atribucion_requerida"):
            return False, "", ""
        texto = ficha.get("texto_credito") or (
            f'"{titulo}"' + (f" {textos['por']} {autor}" if autor else "")
            + f" — {licencia} ({NOMBRES_ORIGEN.get(origen, origen)})")
        return True, texto, ""
    tipo = tipo_de_licencia(licencia)
    if tipo == "cc0":
        return False, "", ""
    linea = (f'"{titulo}"' + (f" {textos['por']} {autor}" if autor else "")
             + f" ({NOMBRES_ORIGEN.get(ficha.get('fuente'), ficha.get('fuente') or '')})"
             + f" — {nombre_licencia(licencia)}"
             + (f" — {_url_de(ficha)}" if _url_de(ficha) else ""))
    aviso = "" if tipo in LICENCIAS_SEGURAS else (
        f"«{titulo}» tiene licencia {nombre_licencia(licencia) or 'desconocida'}: "
        "no es CC0 ni CC BY, revisa si se puede monetizar")
    return True, linea, aviso


def creditos(params, lista_eventos=None, idioma="en"):
    """El texto de creditos.txt y sus avisos. -> {texto, avisos, piezas}

    Mira lo que DE VERDAD suena: los temas de la musica y los efectos de
    `lista_eventos` (los que reparte `eventos`), no todo el surtido.
    """
    p = params or {}
    textos = _TEXTOS_CREDITOS.get(str(idioma or "en")[:2], _TEXTOS_CREDITOS["en"])
    suscrito = bool(_ajustes().get("uppbeat_suscripcion"))
    musica = p.get("musica") or {}
    temas = []
    for tramo in musica.get("tramos") or ([musica] if musica.get("id") else []):
        # un tramo puede llevar trozos de VARIAS canciones: se citan todas
        temas.extend(tramo.get("piezas") or [tramo])
    efectos, vistos = [], set()
    for evento in lista_eventos or []:
        ficha = evento.get("ficha") if isinstance(evento, dict) else None
        if isinstance(ficha, dict) and clave_de(ficha) not in vistos:
            vistos.add(clave_de(ficha))
            efectos.append(ficha)
    vistos_temas, unicos = set(), []
    for tema in temas:
        if clave_de(tema) not in vistos_temas:
            vistos_temas.add(clave_de(tema))
            unicos.append(tema)

    pegar, registro, avisos, piezas = [], [], [], []
    for familia, lista in (("musica", unicos), ("efectos", efectos)):
        lineas = []
        for ficha in lista:
            pide, linea, aviso = _credito_de(ficha, textos, suscrito)
            if aviso:
                avisos.append(aviso)
            if pide and linea:
                lineas.append(linea)
            origen = (NOMBRES_ORIGEN.get(ficha.get("origen"), ficha.get("origen"))
                      if ficha.get("fuente") == "propia"
                      else NOMBRES_ORIGEN.get(ficha.get("fuente"), ficha.get("fuente")))
            registro.append(
                f"- [{textos[familia]}] \"{ficha.get('titulo') or ficha.get('id')}\""
                + (f" {textos['por']} {ficha.get('artista') or ficha.get('autor')}"
                   if ficha.get("artista") or ficha.get("autor") else "")
                + f" · {origen} · {ficha.get('licencia') or '?'}"
                + f" · {textos['si'] if pide else textos['no']}"
                + (f" · {_url_de(ficha)}" if _url_de(ficha) else ""))
            piezas.append({"familia": familia, "titulo": ficha.get("titulo"),
                           "pide_credito": pide, "linea": linea})
        if lineas:
            pegar.append(f"{textos[familia]}:")
            pegar.extend(lineas)
            pegar.append("")
    if not pegar:
        pegar = [textos["nada"], ""]
    texto = "\n".join(pegar + ["-" * 60, textos["registro"] + ":"]
                      + (registro or ["-"])) + "\n"
    return {"texto": texto, "avisos": avisos, "piezas": piezas}
