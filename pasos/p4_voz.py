"""
Paso 4 del Estudio: VOZ.

Sintetiza el guion COMPLETO en una sola toma con Cartesia y devuelve la pista
mas las marcas de palabra.

Por que una sola toma
---------------------
Sintetizar frase a frase suena a robot leyendo una lista: cada frase arranca en
frio, sin memoria de la anterior, y en el montaje se oyen los empalmes. Con una
toma unica la entonacion fluye de un bloque al siguiente y los cortes de escena
se deducen despues de las marcas de palabra, que son exactas. El aire entre
bloques se consigue insertando silencio en la pista ya grabada (motor.espaciar),
nunca troceando la sintesis.

Controles de estilo
-------------------
OJO: cada modelo quiere una forma distinta y la que no toca se ignora SIN dar
error. sonic-3 y sonic-3.5 quieren `generation_config`; sonic-2 y sonic-turbo,
`__experimental_controls`. Ver el bloque de mediciones junto a MODELOS_NUEVOS.

Para los modelos viejos se replica la llamada SSE de voz.py anadiendo
__experimental_controls dentro del objeto voice:

    "voice": {"mode": "id", "id": "...", "__experimental_controls": {
        "speed": "slow",                       # o un numero -1.0 .. 1.0
        "emotion": ["curiosity:high", "sadness:low"]}}

Verificado contra la API real: cualquier emocion o nivel fuera del vocabulario
devuelve HTTP 400 ("invalid emotion" / "invalid emotion level"), asi que se
valida antes de gastar creditos. voz.py NO se toca: se importa y se reutilizan
cargar_api_key, wav_desde_pcm, espaciar y el reparto tolerante de palabras.

Modo simulado
-------------
Con ESTUDIO_SIMULAR=1 no se llama a Cartesia: se devuelve un wav de silencio con
marcas de palabra estimadas. Sirve para probar el paso, la UI y todo lo que va
aguas abajo sin gastar un credito.

API publica
-----------
    ejecutar(proyecto, params, avisar) -> salidas
    previsualizar(proyecto, params, segundos=20) -> ruta wav
    listar_voces(idioma=None, refrescar=False, solo_nativas=False) -> [ficha]

Params del paso
---------------
    preset          id de presets_voz; rellena lo que no se fije a mano
    voz_id          id de voz de Cartesia (por defecto, la del preset)
    modelo          sonic-3.5 | sonic-3 | sonic-turbo | sonic-2
    velocidad       slowest..fastest o un numero -1.0 .. 1.0
    emociones       ["curiosity:high", "sadness:low", ...]
    idioma          es | en | ...
    hueco_minimo    segundos de silencio minimo entre bloques
    bloques         guion inline; si falta, se lee del paso guion

Salidas
-------
    pista       ruta del wav (en trabajo/ hasta que se llame a completar; despues,
                ruta_paso("voz") + salidas["archivo"], que resuelve ruta_pista)
    duracion    segundos
    palabras    [{"w","s","e"}] de toda la toma, ya con los silencios aplicados
    controles   configuracion resuelta, incluido el bloque enviado a Cartesia
    bloques     [{"id","texto","t_in","t_out","palabras"}]
"""
import base64
import copy
import hashlib
import json
import os
import re
import sys
import time

import requests

try:
    from . import cadencia, cli_claude, comun, estadisticas, marcas_tts, presets_voz
except ImportError:  # ejecutado con la carpeta pasos directamente en sys.path
    import cli_claude
    import comun
    import cadencia
    import estadisticas
    import marcas_tts
    import presets_voz

PASO = "voz"
RAIZ_ESTUDIO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_CACHE = os.path.join(RAIZ_ESTUDIO, "cache")
RUTA_CACHE_VOCES = os.path.join(RUTA_CACHE, "voces_cartesia.json")
DIAS_CACHE_VOCES = 7

API_VOCES = "https://api.cartesia.ai/voices/"

EMOCIONES = ("anger", "curiosity", "positivity", "sadness", "surprise")
NIVELES = ("lowest", "low", "high", "highest")
VELOCIDADES = ("slowest", "slow", "normal", "fast", "fastest")
MODELOS = ("sonic-3.5", "sonic-3", "sonic-turbo", "sonic-2")

MODELO_POR_DEFECTO = "sonic-3.5"
IDIOMA_POR_DEFECTO = "es"

#: Quien pone la voz. SIN el param, Cartesia: es lo que tenian todos los
#: proyectos de antes y su firma no puede moverse por esto. Los videos nuevos lo
#: reciben escrito desde el Estilo del canal (o Configuracion) al crearse.
PROVEEDORES = ("cartesia", "google", "elevenlabs")
PROVEEDOR_POR_DEFECTO = "cartesia"

#: Los tramos del video, cada uno con su instruccion de estilo (solo Gemini).
#: La voz es la MISMA en todos: lo unico que cambia es como habla.
TRAMOS = ("intro", "cuerpo", "cierre")
#: El gancho: la primera seccion del guion. Si sale mas larga que INTRO_MAXIMO_S
#: se parte en la frontera de bloque mas cercana a INTRO_OBJETIVO_S, para que el
#: estilo de intro no se coma un minuto de video.
INTRO_OBJETIVO_S = 35.0
INTRO_MAXIMO_S = 60.0
HUECO_POR_DEFECTO = 1.0
NOMBRE_PISTA = "narracion.wav"
NOMBRE_META = "audio_meta.json"
#: La toma sin el aire entre bloques y sus marcas: para cambiar las pausas sin
#: volver a pagar la voz.
NOMBRE_TOMA_CRUDA = "narracion_cruda.wav"
NOMBRE_MARCAS_CRUDAS = "toma_cruda.json"

# CUANTO DURA LO ESCRITO: vive en `cadencia` y aqui solo se re-exporta.
#
# Estas cuatro cifras y el estimador que las usa estuvieron aqui, y solo servian
# para el modo simulado. El 24-08 hizo falta la misma cuenta ANTES de escribir
# nada -- para decir cuantas palabras caben en la duracion pedida-- y copiarla al
# brief habria dado dos tablas de lo mismo. Se mudo entera, y de paso se calibro
# contra la unica toma real medida: daba un 27,6 % de mas, o sea que el simulado
# ensayaba sobre una linea de tiempo un cuarto mas larga que la de verdad.
PAUSA_FRASE = cadencia.PAUSA_FRASE
PAUSA_COMA = cadencia.PAUSA_COMA
FACTOR_BREAK = cadencia.FACTOR_BREAK
FACTOR_VELOCIDAD = cadencia.FACTOR_VELOCIDAD

CLAVES_LISTA_BLOQUES = ("bloques", "escenas", "segmentos", "partes", "guion")
CLAVES_TEXTO = ("texto", "narracion", "texto_narracion", "contenido", "text")
FICHEROS_GUION = ("guion.json", "bloques.json", "guion_revisado.json",
                  "plan.json", "salidas.json")


class _MotorVoz:
    """El motor de voz, pedido de nuevo en CADA acceso a un atributo.

    Guardarlo en una variable de modulo -- que es lo que habia aqui -- deja este
    fichero con la copia que se cargo al arrancar el servicio, y entonces la
    recarga por mtime de `comun.cargar_motor` no llega: se arregla voz.py, comun
    lo recarga, y p4_voz sigue llamando a la copia vieja durante todo el dia. Con
    esto el atributo se resuelve contra el modulo que este cargado AHORA, y las
    llamadas de mas abajo (motor.espaciar, motor.VOCES, motor.wav_desde_pcm) se
    quedan escritas igual que estaban.

    Sirve igual para los otros motores de la voz (Google, el alineador): cada
    uno es un `_MotorVoz` con su carpeta.
    """

    def __init__(self, carpeta="voz_cartesia", fichero="voz.py"):
        self._carpeta, self._fichero = carpeta, fichero

    def __getattr__(self, nombre):
        return getattr(comun.cargar_motor(self._carpeta, self._fichero), nombre)


motor = _MotorVoz()
#: Google Cloud TTS (Gemini TTS / Chirp 3 HD) y el alineador local que le pone
#: las marcas de palabra que Google no da.
motor_google = _MotorVoz("voz_google", "voz.py")
alineador = _MotorVoz("alinear_voz", "alinear.py")
#: ElevenLabs: la voz premium. Trae sus propias marcas (por caracter).
motor_eleven = _MotorVoz("voz_elevenlabs", "voz.py")

#: Un id de voz de ElevenLabs: 20 letras y numeros, sin guiones (los de
#: Cartesia son UUID, con guiones).
_ID_ELEVENLABS = re.compile(r"^[A-Za-z0-9]{20}$")
#: La frecuencia de muestreo de la API. Se resuelve UNA vez a proposito: no
#: cambia entre versiones del motor y se usa en aritmetica en todo el fichero.
SR = motor.SR


# --------------------------------------------------------------------- utiles

def simulado():
    """True si hay que trabajar sin llamar a Cartesia (ESTUDIO_SIMULAR=1)."""
    valor = str(os.environ.get("ESTUDIO_SIMULAR", "")).strip().lower()
    return valor not in ("", "0", "false", "no")


def _avisador(avisar):
    """Normaliza el callback de progreso para poder llamarlo siempre."""
    if callable(avisar):
        return avisar

    def sordo(valor, mensaje=""):
        return valor
    return sordo


def _texto_de(registro):
    for clave in CLAVES_TEXTO:
        valor = registro.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return ""


# ------------------------------------------------------------------ controles

def normalizar_emociones(emociones):
    """Valida y ordena las etiquetas de emocion que acepta Cartesia."""
    if emociones is None:
        return []
    if isinstance(emociones, str):
        emociones = [emociones]
    limpias = []
    for cruda in emociones:
        etiqueta = str(cruda).strip().lower()
        if not etiqueta:
            continue
        nombre, _, nivel = etiqueta.partition(":")
        if nombre not in EMOCIONES:
            raise ValueError(
                f"emocion no soportada por Cartesia: {nombre!r}. "
                f"Validas: {', '.join(EMOCIONES)}")
        if nivel and nivel not in NIVELES:
            raise ValueError(
                f"nivel de emocion no soportado: {nivel!r} (en {etiqueta!r}). "
                f"Validos: {', '.join(NIVELES)} o sin nivel")
        etiqueta = nombre if not nivel else f"{nombre}:{nivel}"
        if etiqueta not in limpias:
            limpias.append(etiqueta)
    return limpias


def normalizar_velocidad(velocidad):
    """Devuelve un preset valido, un numero en -1..1, o None si no se toca."""
    if velocidad is None or velocidad == "":
        return None
    if isinstance(velocidad, bool):
        raise ValueError("velocidad no puede ser un booleano")
    if isinstance(velocidad, (int, float)):
        numero = float(velocidad)
        if not -1.0 <= numero <= 1.0:
            raise ValueError(f"velocidad numerica fuera de rango: {numero} "
                             f"(el rango de Cartesia es -1.0 .. 1.0)")
        return numero
    texto = str(velocidad).strip().lower()
    if texto in VELOCIDADES:
        return texto
    try:
        return normalizar_velocidad(float(texto))
    except ValueError:
        pass
    raise ValueError(f"velocidad no soportada: {velocidad!r}. "
                     f"Validas: {', '.join(VELOCIDADES)} o un numero -1.0 .. 1.0")


def construir_controles(velocidad=None, emociones=None):
    """Bloque __experimental_controls listo para meter dentro de voice."""
    controles = {}
    limpia = normalizar_velocidad(velocidad)
    if limpia is not None:
        controles["speed"] = limpia
    etiquetas = normalizar_emociones(emociones)
    if etiquetas:
        controles["emotion"] = etiquetas
    return controles


# --------------------------------------------- controles en sonic-3 y sonic-3.5
#
# LOS CONTROLES VIEJOS NO HACEN NADA EN SONIC-3.5, Y NO DAN ERROR.
#
# `__experimental_controls` es de la era sonic-2. En sonic-3.5 -- que es el
# modelo por defecto del Estudio -- se ignora EN SILENCIO: ni HTTP 400 ni aviso.
# Medido con el mismo texto y la misma voz:
#
#     sonic-3.5   sin controles                     6,32 s
#                 __experimental_controls slowest   6,56 s   (nada)
#                 __experimental_controls fastest   6,72 s   (nada)
#                 generation_config speed 0.7       7,84 s   (SI)
#
#     sonic-2     __experimental_controls fastest   5,62 s   (SI)
#
# O sea que todos los estilos de locucion han sido decorado durante todo este
# tiempo: elegir «susurrada, lentisima» sonaba exactamente igual que «urgente,
# rapida». Cada modelo recibe ahora la forma que entiende.

#: Modelos que quieren generation_config en vez de __experimental_controls.
MODELOS_NUEVOS = ("sonic-3", "sonic-3.5")

#: Rango de velocidad de sonic-3/3.5. Fuera de el se ignora, otra vez en silencio.
SPEED_MIN, SPEED_MAX = 0.6, 1.5

#: Los presets de siempre, traducidos al multiplicador que entiende sonic-3.
#: VIENE DE `cadencia`, que es donde vive la unica tabla de velocidad: aqui
#: estaba escrita a mano y alli habia otra --el factor de TIEMPO-- que decia lo
#: mismo del reves y no coincidia (0,72 de tiempo son 1,39x de velocidad, no
#: 1,30x). O sea que lo que se le pedia a Cartesia y lo que se estimaba que iba
#: a durar no eran el mismo mando.
SPEED_DE_PRESET = dict(cadencia.VELOCIDAD_API)

#: Nuestro vocabulario de emociones -> el de sonic-3. Los niveles (`:high`) no
#: existen alli, asi que se pierden: lo que viaja es QUE emocion, no cuanta.
EMOCION_A_SONIC3 = {"anger": "angry", "curiosity": "curious",
                    "positivity": "happy", "sadness": "sad",
                    "surprise": "surprised"}


def _speed_sonic3(velocidad):
    """Velocidad nuestra -> multiplicador 0.6..1.5, o None si no se toca."""
    limpia = normalizar_velocidad(velocidad)
    if limpia is None:
        return None
    if isinstance(limpia, str):
        return SPEED_DE_PRESET.get(limpia)
    # el mando numerico va de -1 (lento) a +1 (rapido); aqui 1.0 es lo normal
    return round(max(SPEED_MIN, min(SPEED_MAX, 1.0 + 0.35 * float(limpia))), 3)


def construir_generation_config(velocidad=None, emociones=None):
    """Bloque generation_config para sonic-3 y sonic-3.5."""
    cfg = {}
    speed = _speed_sonic3(velocidad)
    if speed is not None and abs(speed - 1.0) > 1e-9:
        cfg["speed"] = speed
    nombres = []
    for etiqueta in normalizar_emociones(emociones):
        nombre = EMOCION_A_SONIC3.get(etiqueta.partition(":")[0])
        if nombre and nombre not in nombres:
            nombres.append(nombre)
    if nombres:
        # UNA cadena, no una lista. La documentacion dice «un valor o una lista»
        # y la API contesta: «cannot unmarshal array into GenerationConfig.
        # Emotion of type string». Asi que sonic-3 admite una sola emocion: se
        # manda la primera y las demas se pierden, que es mejor que un HTTP 400
        # a mitad de una toma de dos minutos ya pagada.
        cfg["emotion"] = nombres[0]
    return cfg


def usa_generation_config(modelo):
    return str(modelo or "").strip() in MODELOS_NUEVOS


def resolver_params(params):
    """Params del paso -> configuracion completa y validada.

    Un preset rellena lo que el usuario no haya fijado a mano, nunca al reves:
    tocar la velocidad en la UI con un preset puesto tiene que ganar.
    """
    crudos = dict(params or {})
    nombre_preset = crudos.get("preset") or ""
    base = {}
    if nombre_preset:
        base = presets_voz.preset(nombre_preset)

    def elegir(clave, por_defecto):
        valor = crudos.get(clave)
        if valor is None or valor == "":
            valor = base.get(clave)
        if valor is None or valor == "":
            return por_defecto
        return valor

    proveedor = str(crudos.get("proveedor") or PROVEEDOR_POR_DEFECTO).strip().lower()
    if proveedor not in PROVEEDORES:
        raise ValueError(f"proveedor de voz desconocido: {proveedor!r}. "
                         f"Validos: {', '.join(PROVEEDORES)}")
    idioma = str(elegir("idioma", IDIOMA_POR_DEFECTO)).strip().lower()
    if proveedor == "google":
        return _resolver_google(crudos, base, idioma, nombre_preset)
    if proveedor == "elevenlabs":
        return _resolver_elevenlabs(crudos, base, idioma, nombre_preset)

    modelo = str(elegir("modelo", MODELO_POR_DEFECTO)).strip()
    if modelo not in MODELOS:
        raise ValueError(f"modelo de voz desconocido: {modelo!r}. "
                         f"Validos: {', '.join(MODELOS)}")

    emociones_crudas = crudos.get("emociones")
    if emociones_crudas is None:
        emociones_crudas = base.get("emociones", [])
    emociones = normalizar_emociones(emociones_crudas)
    velocidad = normalizar_velocidad(elegir("velocidad", None))

    voz_id = str(elegir("voz_id", "")).strip()
    if (voz_id in motor_google.VOCES_MASCULINAS + motor_google.VOCES_FEMENINAS
            or _ID_ELEVENLABS.match(voz_id)):
        # una voz de Google o de ElevenLabs que se quedo puesta al volver el
        # estilo a Cartesia: mandarsela seria un 400, asi que va la de defecto
        voz_id = ""
    if not voz_id:
        sugeridas = base.get("voces_sugeridas") or []
        if sugeridas:
            voz_id = str(sugeridas[0])
        else:
            voz_id = motor.VOCES.get(idioma, motor.VOCES["es"])["id"]

    try:
        hueco = float(elegir("hueco_minimo", HUECO_POR_DEFECTO))
    except (TypeError, ValueError):
        raise ValueError(f"hueco_minimo debe ser un numero de segundos, "
                         f"llego {crudos.get('hueco_minimo')!r}")
    if hueco < 0:
        raise ValueError("hueco_minimo no puede ser negativo")

    return {
        "preset": nombre_preset or None,
        "modelo": modelo,
        "voz_id": voz_id,
        "idioma": idioma,
        "velocidad": velocidad,
        "emociones": emociones,
        "hueco_minimo": round(hueco, 3),
        "experimental_controls": construir_controles(velocidad, emociones),
        "generation_config": construir_generation_config(velocidad, emociones),
        "controles_por": ("generation_config" if usa_generation_config(modelo)
                          else "experimental_controls"),
    }


def normalizar_estilos(estilos):
    """{tramo: instruccion} con solo los tramos conocidos y con texto. -> dict"""
    if not isinstance(estilos, dict):
        return {}
    limpios = {}
    for tramo in TRAMOS:
        texto = " ".join(str(estilos.get(tramo) or "").split())
        if texto:
            if len(texto.encode("utf-8")) > 3000:
                raise ValueError(f"la instruccion de estilo «{tramo}» es demasiado "
                                 f"larga (maximo ~3.000 caracteres)")
            limpios[tramo] = texto
    return limpios


def _resolver_google(crudos, base, idioma, nombre_preset):
    """La configuracion de una toma con Google. Ver `resolver_params`.

    Del preset de voz (los de Cartesia) solo se hereda el aire entre bloques: su
    modelo y sus voces son de Cartesia, y mandarlos a Google seria un 400.
    """
    modelo = str(crudos.get("modelo") or motor_google.MODELO_POR_DEFECTO).strip()
    if modelo not in motor_google.MODELOS:
        raise ValueError(f"modelo de Google desconocido: {modelo!r}. "
                         f"Validos: {', '.join(motor_google.MODELOS)}")
    voz_id = str(crudos.get("voz_id") or "").strip()
    if not voz_id or re.fullmatch(r"[0-9a-f-]{36}", voz_id):
        # vacio, o un id de Cartesia que llego de un preset viejo
        voz_id = motor_google.VOCES.get(idioma, motor_google.VOCES["en"])["id"]
    conocidas = motor_google.VOCES_MASCULINAS + motor_google.VOCES_FEMENINAS
    if voz_id not in conocidas:
        raise ValueError(f"voz de Google desconocida: {voz_id!r}")
    hueco_crudo = crudos.get("hueco_minimo")
    if hueco_crudo in (None, ""):
        hueco_crudo = base.get("hueco_minimo", HUECO_POR_DEFECTO)
    try:
        hueco = float(hueco_crudo)
    except (TypeError, ValueError):
        raise ValueError(f"hueco_minimo debe ser un numero de segundos, "
                         f"llego {hueco_crudo!r}")
    if hueco < 0:
        raise ValueError("hueco_minimo no puede ser negativo")
    tramos = crudos.get("tramos") if isinstance(crudos.get("tramos"), dict) else {}
    tramos = {str(s): str(t) for s, t in tramos.items() if t in TRAMOS}
    return {
        "proveedor": "google",
        "preset": nombre_preset or None,
        "modelo": modelo,
        "familia": motor_google.MODELOS[modelo],
        "voz_id": voz_id,
        "idioma": idioma,
        "velocidad": None,
        "emociones": [],
        "hueco_minimo": round(hueco, 3),
        "estilos": normalizar_estilos(crudos.get("estilos")),
        "tramos": tramos,
        "experimental_controls": {},
        "generation_config": {},
        "controles_por": "estilo" if motor_google.MODELOS[modelo] == "gemini" else "ninguno",
    }


def _resolver_elevenlabs(crudos, base, idioma, nombre_preset):
    """La configuracion de una toma con ElevenLabs. Ver `resolver_params`.

    Del preset de voz (los de Cartesia) se heredan el aire y la velocidad, que
    ElevenLabs entiende como `speed` (0,7 a 1,2). Sus emociones y su modelo son
    de Cartesia y no viajan. Sin estilos por tramo: ElevenLabs no lee
    instrucciones en lenguaje natural.
    """
    modelo = str(crudos.get("modelo") or motor_eleven.MODELO_POR_DEFECTO).strip()
    if modelo not in motor_eleven.MODELOS:
        raise ValueError(f"modelo de ElevenLabs desconocido: {modelo!r}. "
                         f"Validos: {', '.join(motor_eleven.MODELOS)}")
    voz_id = str(crudos.get("voz_id") or "").strip()
    if not _ID_ELEVENLABS.match(voz_id):
        # vacio, o la voz de otro proveedor que llego de un preset
        voz_id = motor_eleven.VOCES.get(idioma, motor_eleven.VOCES["en"])["id"]
    velocidad = normalizar_velocidad(crudos.get("velocidad") or base.get("velocidad"))
    hueco_crudo = crudos.get("hueco_minimo")
    if hueco_crudo in (None, ""):
        hueco_crudo = base.get("hueco_minimo", HUECO_POR_DEFECTO)
    try:
        hueco = float(hueco_crudo)
    except (TypeError, ValueError):
        raise ValueError(f"hueco_minimo debe ser un numero de segundos, "
                         f"llego {hueco_crudo!r}")
    if hueco < 0:
        raise ValueError("hueco_minimo no puede ser negativo")
    return {
        "proveedor": "elevenlabs",
        "preset": nombre_preset or None,
        "modelo": modelo,
        "voz_id": voz_id,
        "idioma": idioma,
        "velocidad": velocidad,
        "speed": _speed_sonic3(velocidad),
        "emociones": [],
        "hueco_minimo": round(hueco, 3),
        "estilos": {},
        "tramos": {},
        "experimental_controls": {},
        "generation_config": {},
        "controles_por": "speed",
    }


def estilo_de(tramo, cfg):
    """La instruccion de estilo de un tramo, o None. Sin la suya, la del cuerpo."""
    estilos = (cfg or {}).get("estilos") or {}
    return estilos.get(tramo) or estilos.get("cuerpo") or None


def _factor_velocidad(velocidad):
    return cadencia.factor_velocidad(velocidad)


# ---------------------------------------------------------------------- guion

_ID_UNIDAD = re.compile(r"^[A-Za-z]{1,3}\d{1,4}$")


def _es_mapa_de_unidades(crudo):
    """True si el dict es {id_unidad: bloque} y no un documento cualquiera.

    Sin esta comprobacion, un json con {"titulo": "..."} se colaria como un
    guion de un bloque llamado "titulo".
    """
    if not all(isinstance(clave, str) and _ID_UNIDAD.match(clave) for clave in crudo):
        return False
    return all(isinstance(valor, str) and valor.strip()
               or isinstance(valor, dict) and _texto_de(valor)
               for valor in crudo.values())


def normalizar_bloques(crudo):
    """Cualquier forma razonable de guion -> [{"id","texto"}] en orden."""
    lista = None
    if isinstance(crudo, dict):
        for clave in CLAVES_LISTA_BLOQUES:
            valor = crudo.get(clave)
            if isinstance(valor, list):
                lista = valor
                break
        if lista is None and crudo and _es_mapa_de_unidades(crudo):
            # {"S01": {...}} o {"S01": "texto"}: mapa de unidades del paso guion
            lista = []
            for clave in sorted(crudo):
                valor = crudo[clave]
                if isinstance(valor, dict):
                    lista.append(dict(valor, id=valor.get("id", clave)))
                else:
                    lista.append({"id": clave, "texto": valor})
    elif isinstance(crudo, list):
        lista = crudo
    elif isinstance(crudo, str):
        trozos = [t.strip() for t in re.split(r"\n\s*\n", crudo) if t.strip()]
        lista = [{"id": f"B{i:02d}", "texto": t} for i, t in enumerate(trozos, 1)]

    if not isinstance(lista, list):
        return []

    bloques = []
    for posicion, elemento in enumerate(lista, 1):
        if isinstance(elemento, str):
            texto = elemento.strip()
            identificador = f"B{posicion:02d}"
        elif isinstance(elemento, dict):
            texto = _texto_de(elemento)
            identificador = str(elemento.get("id") or elemento.get("bloque_id")
                                or f"B{posicion:02d}")
        else:
            continue
        if not texto:
            continue
        ficha = {"id": identificador, "texto": texto}
        # La marca de seccion que pone el redactor (regla 11 de p3_guion) tiene
        # que llegar a `agrupar_secciones`, que corta por ella. Se perdia aqui,
        # y las secciones salian de las pausas o cada seis bloques en vez de en
        # los cambios de tema -- y con Google, el cambio de estilo de la intro al
        # cuerpo caia donde no acababa el gancho.
        if isinstance(elemento, dict) and elemento.get("abre_seccion"):
            ficha["abre_seccion"] = True
        bloques.append(ficha)
    return bloques


def _estado_de(proyecto):
    """Estado del proyecto, o None si el nucleo no esta disponible."""
    if RAIZ_ESTUDIO not in sys.path:
        sys.path.insert(0, RAIZ_ESTUDIO)
    try:
        from nucleo.estado import Estado
    except ImportError:
        return None
    try:
        return Estado(proyecto)
    except Exception:
        return None


def ediciones_a_mano(proyecto, bloques):
    """Los bloques con las correcciones escritas a mano puestas encima.

    LO QUE ARREGLA. `params.guion.bloques` es el cajon donde la pantalla guarda
    lo que una persona reescribe de un bloque -- y donde escribe tambien
    `regrabar_seccion` cuando el microcambio le toca el texto. Ese cajon lo
    aplicaba SOLO `p3_guion`, al redactar; o sea que corregir una frase a mano y
    darle a grabar locutaba la frase VIEJA, y la unica forma de que llegara al
    audio era volver a pedirle el guion entero al modelo: una llamada completa,
    y encima reescribiendo los otros cuarenta bloques que estaban bien.

    Se aplica aqui, que es el otro sitio que lee el guion. Los dos ponen lo
    mismo encima de lo mismo, asi que no hay dos versiones de nada: si despues
    se vuelve a redactar, `p3_guion` deja exactamente este texto.

    Un id que ya no existe se ignora en silencio: es una edicion de un guion
    anterior, no un error de nadie.
    """
    estado = _estado_de(proyecto)
    if estado is None:
        return bloques
    try:
        ediciones = (estado.params("guion") or {}).get("bloques") or {}
    except Exception:                                        # noqa: BLE001
        return bloques
    if not isinstance(ediciones, dict) or not ediciones:
        return bloques
    salida = []
    for bloque in bloques:
        ficha = ediciones.get(bloque.get("id"))
        texto = ficha.get("texto") if isinstance(ficha, dict) else ficha
        if isinstance(texto, str) and texto.strip():
            bloque = dict(bloque, texto=" ".join(texto.split()))
        salida.append(bloque)
    return salida


def cargar_guion(proyecto, params=None):
    """Bloques de guion sobre los que hay que sintetizar.

    Se busca en tres sitios, de mas concreto a mas general: lo que venga en los
    params (util para pruebas y para reescrituras del paso 5), la salida en
    disco de la version activa del paso guion, y por ultimo los params del paso
    guion en estado.json, donde viven las unidades editadas desde la UI.

    Y sobre lo que salga se aplican las EDICIONES A MANO (ver
    `ediciones_a_mano`). Menos sobre lo que llegue por `params`: ahi lo que hay
    es un guion explicito -- una prueba, o la reescritura del paso 5 -- y
    pisarlo con una edicion guardada seria contestar otra cosa a lo que se pide.
    """
    crudos = dict(params or {})
    for clave in ("bloques", "guion"):
        bloques = normalizar_bloques(crudos.get(clave))
        if bloques:
            return bloques

    ruta_suelta = crudos.get("guion_ruta")
    if ruta_suelta and os.path.exists(ruta_suelta):
        with open(ruta_suelta, "r", encoding="utf-8") as fh:
            bloques = normalizar_bloques(json.load(fh))
        if bloques:
            return bloques

    # camino normal: guion.json de la version activa del paso 3
    documento = comun.leer_salida(proyecto, "guion", "guion.json", obligatorio=False)
    bloques = normalizar_bloques(documento)
    if bloques:
        return ediciones_a_mano(proyecto, bloques)

    carpeta = proyecto.ruta_paso("guion")
    if os.path.isdir(carpeta):
        candidatos = [os.path.join(carpeta, n) for n in FICHEROS_GUION]
        candidatos += sorted(os.path.join(carpeta, n) for n in os.listdir(carpeta)
                             if n.lower().endswith(".json"))
        for ruta in candidatos:
            if not os.path.isfile(ruta):
                continue
            try:
                with open(ruta, "r", encoding="utf-8") as fh:
                    bloques = normalizar_bloques(json.load(fh))
            except (ValueError, OSError):
                continue
            if bloques:
                return ediciones_a_mano(proyecto, bloques)

    estado = _estado_de(proyecto)
    if estado is not None:
        bloques = normalizar_bloques(estado.params("guion").get("unidades"))
        if bloques:
            return bloques

    raise RuntimeError(
        "no encuentro el guion: ni en los params, ni en "
        f"{proyecto.ruta_paso('guion')}, ni en las unidades del paso guion. "
        "Ejecuta el paso guion antes que el de voz.")


def idioma_de_salida(proyecto):
    """Idioma principal del brief: aquel en el que esta escrito el guion.

    Se lee del brief ya ejecutado y, si todavia no ha corrido, de sus params.
    Devuelve "" si no hay nada que preguntar.
    """
    brief = comun.leer_salida(proyecto, "brief", "brief.json", obligatorio=False) or {}
    codigo = str(brief.get("idioma_salida") or "").strip().lower()
    if codigo:
        return codigo

    estado = _estado_de(proyecto)
    if estado is None:
        return ""
    params = estado.params("brief") or {}
    codigo = str(params.get("idioma_salida") or "").strip().lower()
    if codigo:
        return codigo
    # la clave vieja del eje de idiomas: se lee, no se escribe
    lista = params.get("idiomas_salida")
    if isinstance(lista, list) and lista:
        return str(lista[0] or "").strip().lower()
    return ""


def formato_de_salida(proyecto):
    """El formato del video (horizontal | vertical), por el mismo camino que el
    idioma: del brief ya ejecutado y, si no ha corrido, de sus params. Un
    proyecto de antes de que existiera es horizontal."""
    brief = comun.leer_salida(proyecto, "brief", "brief.json", obligatorio=False) or {}
    if brief.get("formato"):
        return comun.normalizar_formato(brief.get("formato"))
    estado = _estado_de(proyecto)
    if estado is None:
        return comun.FORMATO_POR_DEFECTO
    return comun.normalizar_formato((estado.params("brief") or {}).get("formato"))


def params_con_idioma(proyecto, params):
    """Params del paso con el idioma resuelto cuando no se ha fijado a mano.

    El mando del paso manda: una voz inglesa leyendo espanol es una decision
    legitima y se respeta. Vacio significa "el del video", y ahi hay que ir a
    buscarlo en vez de caer en IDIOMA_POR_DEFECTO, porque ese 'es' de fabrica
    sobre un guion en ingles NO falla: lo locuta con acento espanol y no lo dice
    nadie. Y de paso, el catalogo de voces se pedia en el idioma equivocado.
    """
    crudos = dict(params or {})
    if str(crudos.get("idioma") or "").strip():
        return crudos
    heredado = idioma_de_salida(proyecto)
    if heredado:
        crudos["idioma"] = heredado
    return crudos


# ------------------------------------------------------------------- sintesis

def _duracion_palabra(palabra, factor=1.0):
    """Cuanto tarda en leerse una palabra, estimado por su longitud."""
    return cadencia.duracion_palabra(palabra, factor)


def _estimar_marcas(texto, factor=1.0, desde=0.0):
    """Marcas de palabra plausibles sin sintetizar nada (modo simulado)."""
    return cadencia.marcas_de(texto, factor, desde)


def _toma_simulada(texto, cfg):
    """Silencio del largo estimado, con marcas de palabra coherentes."""
    factor = _factor_velocidad(cfg.get("velocidad"))
    palabras, duracion = _estimar_marcas(texto, factor)
    duracion = max(duracion + 0.4, 0.5)
    muestras = int(duracion * SR) * 2
    return motor.wav_desde_pcm(b"\x00" * muestras), duracion, palabras


# ------------------------------------------------------- secciones de la toma
#
# POR QUE SE PARTE, Y POR QUE NO SE OYE
#
# La voz se graba en UNA toma continua a proposito: frase a
# frase, cada linea arranca en frio y el empalme se oye. Pero una toma unica
# tiene un problema: si el TTS tartamudea en el minuto ocho, hay que volver a
# pagar y regrabar los quince minutos enteros.
#
# Cartesia tiene la respuesta y no hace falta apano: los CONTEXTOS. Se manda el
# guion en varias entradas con el mismo context_id y `continue: true`, y la
# prosodia se mantiene entre ellas -- «puedes mandar un transcript en varias
# partes y recibir voz sin costuras», dice su documentacion--. Medido ademas:
#
#   - las marcas de palabra siguen llegando enteras (12 de 12 en la prueba), y
#   - sus tiempos son CONTINUOS entre secciones, no se reinician.
#
# Eso ultimo es lo que hace que esto no toque nada aguas abajo: la salida es
# identica en forma a la de siempre -- una lista de palabras con tiempos
# absolutos -- asi que assets, callouts y el montaje reciben exactamente lo
# mismo que recibian. Las secciones son una propiedad de COMO se graba, no del
# dato.
#
# DONDE SE CORTA
#
# El contexto expira un segundo despues del ultimo audio, asi que no se puede
# volver a el para regrabar la seccion 4: hay que abrir uno nuevo con esa
# seccion sola, y ahi SI hay dos costuras. Por eso se corta donde el relato ya
# cambia de tema y hay una pausa larga escrita: es donde un cambio de entonacion
# se lee como intencionado y no como un empalme.

#: Silencio a partir del cual un bloque abre seccion. Es el <break> que el
#: redactor pone en un cambio de capitulo o de tema, no el de suspense.
UMBRAL_SECCION_MS = 700

#: Tope de secciones. Mas cortes es mas sitios donde se puede oir un empalme el
#: dia que se regrabe uno, y menos contexto para la prosodia dentro de cada uno.
MAX_SECCIONES = 10

#: Bloques a partir de los cuales una seccion se subdivide sola. La seccion es
#: la unidad de REGRABADO: una que se trague medio video obliga a pagar medio
#: video por un tartamudeo, que es justo lo que las secciones vienen a evitar.
#: Paso un guion de 12 bloques con una sola seccion declarada, y regrabar una
#: frase costaba la toma entera.
MAX_BLOQUES_SECCION = 6


def _subdividir_largas(vivos, cortes, umbral_ms, tope=MAX_BLOQUES_SECCION):
    """Corta por dentro las secciones que salen demasiado largas.

    Preferencia por las fronteras con pausa escrita (la costura de un regrabado
    cae donde el propio guion ya paraba); sin pausas, la frontera de bloque mas
    centrada -- los bloques acaban en fin de frase, asi que el sitio es digno.
    """
    por_id = {b["id"]: i for i, b in enumerate(vivos)}
    resultado = set(cortes)
    inicios = sorted({0} | {por_id[c] for c in cortes if c in por_id})
    for desde, hasta in zip(inicios, inicios[1:] + [len(vivos)]):
        largo = hasta - desde
        if largo <= tope:
            continue
        trozos = -(-largo // tope)          # techo: trozos minimos para caber
        internos = list(range(desde + 1, hasta))
        con_pausa = [i for i in internos
                     if marcas_tts.silencio_final(vivos[i - 1]["texto"]) >= umbral_ms]
        candidatos = con_pausa or internos
        for k in range(1, trozos):
            ideal = desde + round(k * largo / trozos)
            resultado.add(vivos[min(candidatos, key=lambda i: abs(i - ideal))]["id"])
    return resultado


def agrupar_secciones(bloques, umbral_ms=UMBRAL_SECCION_MS, maximo=MAX_SECCIONES):
    """Reparte los bloques en secciones, cortando tras una pausa larga.

    Devuelve [{"id": "SB001", "bloques": [ids]}]. Un guion corto sin cambios de
    tema sale en una o dos secciones; uno largo nunca deja una seccion que no
    se pueda regrabar sin pagar medio video.
    """
    vivos = [b for b in bloques if str(b.get("texto") or "").strip()]
    if not vivos:
        return []

    # Manda lo que DECLARA el guion. Las secciones se cortan por TEMA, y el tema
    # solo lo sabe quien escribio el texto: en un guion real los cambios de
    # asunto no llevan pausa escrita -- se pasa de quien robo los datos a que se
    # llevaron sin ningun silencio de por medio-- asi que deducirlas de los
    # <break> daba dos secciones para doce bloques: la primera sola y el resto
    # en bruto. La pausa larga se queda solo como respaldo para un guion antiguo
    # que todavia no traiga las marcas.
    declarados = {b["id"] for b in vivos if b.get("abre_seccion")}
    if declarados:
        cortes = set(declarados)
    else:
        cortes = set()
        for anterior, siguiente in zip(vivos, vivos[1:]):
            if marcas_tts.silencio_final(anterior["texto"]) >= umbral_ms:
                cortes.add(siguiente["id"])

    # Red de seguridad: si el guion declaro pocas secciones (o ninguna), las
    # que salgan kilometricas se parten solas. La declaracion sigue mandando en
    # DONDE cortar; esto solo garantiza que la unidad de regrabado exista.
    cortes = _subdividir_largas(vivos, cortes, umbral_ms)

    # Con demasiados cortes se conservan primero los DECLARADOS (cambio de tema
    # de verdad) y despues los de la pausa mas larga: son los sitios donde menos
    # se nota un empalme.
    if len(cortes) + 1 > maximo:
        orden = sorted(
            ((1 if b["id"] in declarados else 0,
              marcas_tts.silencio_final(a["texto"]), b["id"])
             for a, b in zip(vivos, vivos[1:]) if b["id"] in cortes),
            reverse=True)
        cortes = {bid for _d, _ms, bid in orden[:max(0, maximo - 1)]}

    secciones, actual = [], None
    for bloque in vivos:
        if actual is None or bloque["id"] in cortes:
            actual = {"id": f"SB{len(secciones) + 1:03d}", "bloques": []}
            secciones.append(actual)
        actual["bloques"].append(bloque["id"])
    return secciones


# ------------------------------------------------- tramos y toma con Google
#
# Google graba cada seccion en una peticion aparte (ver motores/voz_google), y
# eso da justo lo que hace falta para los ESTILOS POR TRAMO: cada seccion lleva
# la instruccion de su tramo, y como las secciones se cortan donde el relato
# cambia de asunto, el cambio de estilo cae en un limite natural y nunca a mitad
# de frase. La voz es la misma en todas.

def _segundos_estimados(texto, cfg):
    _, duracion = _estimar_marcas(marcas_tts.limpiar(texto),
                                  _factor_velocidad((cfg or {}).get("velocidad")))
    return duracion


def separar_intro(secciones, bloques, cfg):
    """Si la primera seccion es larga, el gancho se queda en su propia seccion.

    Se corta en la frontera de BLOQUE cuya duracion estimada acumulada quede mas
    cerca de INTRO_OBJETIVO_S (los bloques acaban en final de frase). Solo hace
    falta si la intro tiene un estilo distinto del cuerpo: si no, cortar es
    poner una costura de mas sin ganar nada.
    """
    estilos = (cfg or {}).get("estilos") or {}
    if not secciones or not estilos.get("intro") or estilos.get("intro") == estilos.get("cuerpo"):
        return secciones
    por_id = {b["id"]: b for b in bloques}
    primera = secciones[0]
    # `intro_hasta`: el ULTIMO bloque del gancho, dicho a mano. La cuenta por
    # segundos estimados es una apuesta: en el video de las 8 horas cortaba una
    # frase antes del final del gancho («...nine years old.», 09-10-2026).
    hasta = str((cfg or {}).get("intro_hasta") or "").strip()
    if hasta in primera["bloques"][:-1]:
        mejor = primera["bloques"].index(hasta) + 1
    else:
        duraciones = [_segundos_estimados(por_id[b]["texto"], cfg) for b in primera["bloques"]]
        if sum(duraciones) <= INTRO_MAXIMO_S or len(duraciones) < 2:
            return secciones
        acumulado, mejor, distancia = 0.0, 1, None
        for corte in range(1, len(duraciones)):
            acumulado += duraciones[corte - 1]
            if distancia is None or abs(acumulado - INTRO_OBJETIVO_S) < distancia:
                mejor, distancia = corte, abs(acumulado - INTRO_OBJETIVO_S)
    nuevas = [{"bloques": primera["bloques"][:mejor]},
              {"bloques": primera["bloques"][mejor:]}] + [
        {"bloques": s["bloques"]} for s in secciones[1:]]
    for indice, seccion in enumerate(nuevas, 1):
        seccion["id"] = f"SB{indice:03d}"
    return nuevas


def asignar_tramos(secciones, cfg):
    """Pone a cada seccion su tramo: intro la primera, cierre la ultima (solo si
    hay estilo de cierre), cuerpo el resto. Lo fijado a mano (`tramos`, por id
    de seccion) manda."""
    estilos = (cfg or {}).get("estilos") or {}
    manual = (cfg or {}).get("tramos") or {}
    total = len(secciones)
    for indice, seccion in enumerate(secciones):
        tramo = "cuerpo"
        if indice == 0 and total > 1:
            tramo = "intro"
        elif indice == total - 1 and total > 2 and estilos.get("cierre"):
            tramo = "cierre"
        seccion["tramo"] = manual.get(seccion["id"], tramo)
    return secciones


def _sintesis_google(trozos, cfg, progreso):
    """La llamada que se PAGA: los trozos a Google, en una pista igualada.

    -> (wav, segundos, info). Esta aparte para que el medidor de coste la
    envuelva a ella sola (nucleo/coste.py), se pida desde donde se pida.
    """
    return comun.llamar_motor(
        motor_google.sintetizar_trozos, trozos, voz=cfg["voz_id"],
        modelo=cfg["modelo"], idioma=cfg["idioma"], avisar=progreso)


def _alinear(wav, hablado, cfg, progreso):
    """Marcas de palabra de una pista ya grabada, con el alineador local."""
    import tempfile
    carpeta = tempfile.mkdtemp(prefix="voz_alinear_")
    ruta = os.path.join(carpeta, "toma.wav")
    try:
        with open(ruta, "wb") as fh:
            fh.write(wav)
        return alineador.alinear(ruta, hablado, cfg.get("idioma") or "en",
                                 avisar=progreso)
    finally:
        try:
            os.remove(ruta)
            os.rmdir(carpeta)
        except OSError:
            pass


def _resumen_alineado(alineado):
    return {"cobertura": alineado.get("cobertura"),
            "faltan": list(alineado.get("faltan") or [])[:25],
            "sobran": alineado.get("sobran"),
            "de_mas": list(alineado.get("de_mas") or [])[:10],
            "dispositivo": alineado.get("dispositivo"),
            "modelo": alineado.get("modelo"),
            "segundos": alineado.get("segundos")}


# ------------------------------------------- la voz que lee su propia instruccion
#
# GEMINI A VECES LEE EN VOZ ALTA LA INSTRUCCION DE ESTILO. Medido el 02-10-2026
# en la primera toma real: antes del guion dijo «Intrigued, slightly urgent,
# quick but clear pace, energetic but credible, not salesy.» -- la mitad de la
# instruccion de la intro--, y la misma instruccion no se habia colado en la
# prueba A/B del dia anterior. Es aleatorio, asi que no se arregla escribiendo
# de otra forma la instruccion (que ademas es la que sono bien): se MIRA.
#
# El alineador dice lo que se oyo y no esta en el guion (`de_mas`, con donde
# sono). Si un tramo de eso se parece a la instruccion de la seccion en la que
# cae, esa seccion se regraba sola y se cose en su sitio; despues se vuelve a
# alinear. Unos centimos por regrabado, contra un video publicado que empieza
# leyendo sus propias acotaciones.

#: Cuantas veces se regraba una seccion que ha leido su instruccion.
REINTENTOS_FUGA = 2

#: Hasta cuanto texto se junta en UNA toma de Google (secciones seguidas con el
#: mismo estilo). Su tope son 4.000 bytes; con 3.500 queda margen y salen unos
#: tres o cuatro minutos de voz por toma.
BYTES_TOMA_UNICA = 3500
#: Palabras de mas a partir de las cuales se mira si son la instruccion, y que
#: parte de ellas tiene que estar en la instruccion para darlo por hecho.
FUGA_MIN_PALABRAS = 3
FUGA_PARECIDO = 0.5


def _norm(palabra):
    return re.sub(r"[^\w]", "", str(palabra).lower()).replace("_", "")


def fugas_de_estilo(alineado, info, trozos):
    """Secciones donde la voz leyo su instruccion de estilo. -> {indice: texto}"""
    piezas = (info or {}).get("piezas") or []
    fugas = {}
    for tramo in (alineado or {}).get("de_mas") or []:
        dichas = {_norm(w) for w in str(tramo.get("texto") or "").split()} - {""}
        if len(dichas) < FUGA_MIN_PALABRAS:
            continue
        indice = next((p["trozo"] for p in piezas
                       if p["t_in"] - 0.5 <= float(tramo.get("s") or 0) <= p["t_out"] + 0.5),
                      None)
        if indice is None or indice >= len(trozos):
            continue
        estilo = {_norm(w) for w in str(trozos[indice].get("estilo") or "").split()} - {""}
        if estilo and len(dichas & estilo) / len(dichas) >= FUGA_PARECIDO:
            fugas[indice] = str(tramo.get("texto") or "")
    return fugas


#: Y LA OTRA CARA: LA VOZ QUE SE SALTA TEXTO. Medido el 02-10-2026: en una de
#: tres tomas de la misma intro, Gemini se salto el gancho entero («Your
#: grandparents never read a finance blog. No apps, no spreadsheets, no
#: charts.») y siguio como si nada. En un video de 25 min eso son unas palabras
#: entre miles: la cobertura GLOBAL ni se mueve. Por eso se mira por seccion: a
#: partir de cuantas palabras que faltan, y de que parte de la seccion, se
#: regraba. Por debajo es ruido normal (una contraccion, «do not» oido como
#: «don't»).
OMISION_MIN_PALABRAS = 4
OMISION_PARTE = 0.12


def omisiones_por_seccion(alineado, hablados):
    """Secciones a las que les falta texto de verdad. -> {indice: palabras que faltan}

    `hablados` es lo que se locuta de cada seccion, en orden: con eso se sabe a
    que seccion pertenece cada palabra del guion que el alineador no oyo
    (`faltan_idx`, indices sobre las palabras con letras de todo el texto).
    """
    faltan = set((alineado or {}).get("faltan_idx") or [])
    salida, inicio = {}, 0
    for indice, texto in enumerate(hablados):
        cuantas = sum(1 for palabra in str(texto or "").split() if _norm(palabra))
        dentro = sum(1 for k in range(inicio, inicio + cuantas) if k in faltan)
        if cuantas and dentro >= OMISION_MIN_PALABRAS and dentro / cuantas >= OMISION_PARTE:
            salida[indice] = dentro
        inicio += cuantas
    return salida


def _sustituir_trozo(wav, info, indice, wav_nuevo, info_nuevo):
    """La pista con el trozo `indice` cambiado por otra grabacion. -> (wav, segundos)

    Corta de donde empieza su primera pieza a donde acaba la ultima (con las
    pausas internas) y pone la nueva en su sitio; las piezas de despues se
    corren lo que cambie la duracion, para que el siguiente vistazo sepa donde
    cae cada una.
    """
    sr = motor_google.SR
    piezas = info["piezas"]
    mias = [p for p in piezas if p["trozo"] == indice]
    desde, hasta = mias[0]["t_in"], mias[-1]["t_out"]
    pcm, nuevo = wav[44:], wav_nuevo[44:]
    ini, fin = int(round(desde * sr)) * 2, int(round(hasta * sr)) * 2
    pista = pcm[:ini] + nuevo + pcm[fin:]
    delta = len(nuevo) / (sr * 2) - (hasta - desde)
    otras = []
    for pieza in piezas:
        if pieza["trozo"] == indice:
            continue
        if pieza["t_in"] >= hasta - 1e-6:
            pieza = dict(pieza, t_in=round(pieza["t_in"] + delta, 3),
                         t_out=round(pieza["t_out"] + delta, 3))
        otras.append(pieza)
    for pieza in (info_nuevo or {}).get("piezas") or []:
        otras.append(dict(pieza, trozo=indice, t_in=round(pieza["t_in"] + desde, 3),
                          t_out=round(pieza["t_out"] + desde, 3)))
    info["piezas"] = sorted(otras, key=lambda p: p["t_in"])
    return motor_google.wav_desde_pcm(pista), len(pista) / (sr * 2)


def _id_de_seccion(trozos, trozo_de, indice):
    """El id de la seccion `indice` (de `hablados`), mirando dentro de su toma."""
    toma = trozo_de[indice]
    ids = trozos[toma].get("secciones")
    if ids:
        posicion = indice - trozo_de.index(toma)
        if 0 <= posicion < len(ids):
            return ids[posicion]
    return trozos[toma].get("seccion", indice)


def _secciones_mal(alineado, info, trozos, hablados, trozo_de=None):
    """Lo que hay que regrabar: {toma: (motivo, dato, [secciones])} -- la fuga manda.

    Las omisiones se miden POR SECCION aunque varias vayan en la misma toma
    (`trozo_de[i]` = la toma de la seccion i): medidas sobre una toma larga, un
    gancho de veinte palabras saltado se diluia por debajo del umbral. Se
    regraba la TOMA entera --partirla cambiaria la entonacion, que es por lo que
    se junto-- pero se dice que secciones fallaron: el aviso nombraba siempre la
    primera de la toma, aunque la mala fuera otra (revision de la Fase 4).
    """
    trozo_de = trozo_de or list(range(len(hablados)))
    malas = {}
    for i, n in omisiones_por_seccion(alineado, hablados).items():
        toma = trozo_de[i]
        _m, palabras, cuales = malas.get(toma, ("omision", 0, []))
        malas[toma] = ("omision", palabras + n, cuales + [_id_de_seccion(trozos, trozo_de, i)])
    malas.update({i: ("fuga", t, [trozos[i].get("seccion", i)])
                  for i, t in fugas_de_estilo(alineado, info, trozos).items()})
    return malas


def _google_verificado(trozos, hablados, cfg, progreso, trozo_de=None):
    """Graba con Google, alinea y regraba las secciones que leyeron su
    instruccion o se saltaron texto. -> (wav, segundos, alineado, info)

    `hablados`: lo que se locuta de cada SECCION, en orden; `trozo_de[i]` es la
    toma (trozo) que lleva la seccion i (sin el, una toma por seccion).
    """
    hablado = " ".join(hablados)
    wav, duracion, info = _sintesis_google(trozos, cfg, lambda f, m="": progreso(0.5 * f, m))
    alineado = _alinear(wav, hablado, cfg, lambda f, m="": progreso(0.5 + 0.4 * f, m))
    regrabados = []
    for intento in range(REINTENTOS_FUGA):
        malas = _secciones_mal(alineado, info, trozos, hablados, trozo_de)
        if not malas:
            break
        for indice, (motivo, dato, cuales) in malas.items():
            seccion = ", ".join(str(c) for c in cuales)
            juntas = len(trozos[indice].get("secciones") or [None])
            regraba = ("se regraba esa sección" if juntas <= 1 else
                       f"se regraba su toma entera ({juntas} secciones juntas, para que "
                       f"no cambie la entonación)")
            progreso(0.9, f"AVISO: la voz leyó en voz alta su instrucción de estilo en "
                          f"{seccion} («{str(dato)[:70]}»): {regraba}"
                     if motivo == "fuga" else
                     f"AVISO: la voz se saltó {dato} palabras en {seccion}: {regraba}")
            wav_nuevo, _d, info_nuevo = _sintesis_google([trozos[indice]], cfg,
                                                         lambda f, m="": None)
            wav, duracion = _sustituir_trozo(wav, info, indice, wav_nuevo, info_nuevo)
            regrabados.append({"seccion": seccion, "motivo": motivo,
                               "detalle": dato, "intento": intento + 1})
        alineado = _alinear(wav, hablado, cfg, lambda f, m="": progreso(0.92, m))
    if regrabados:
        info["regrabados"] = regrabados
    restantes = _secciones_mal(alineado, info, trozos, hablados, trozo_de)
    if restantes:
        info["sin_arreglar"] = [{"seccion": ", ".join(str(c) for c in cuales),
                                 "motivo": m, "detalle": d}
                                for i, (m, d, cuales) in restantes.items()]
    return wav, duracion, alineado, info


def _toma_google(secciones, bloques, cfg, progreso):
    """Toda la toma con Google. -> (wav, segundos, palabras, info)

    Una peticion por seccion (o varias si no cabe), cada una con el estilo de
    su tramo; despues el alineador pone las marcas sobre la pista entera, asi
    que los tiempos salen ABSOLUTOS y continuos como los de Cartesia.
    """
    familia = motor_google.familia_de(cfg["modelo"])
    por_id = {b["id"]: b for b in bloques}
    trozos, trozo_de = [], []
    for seccion in secciones:
        texto = " ".join(marcas_tts.para_google(por_id[b]["texto"], familia)
                         for b in seccion["bloques"])
        estilo = estilo_de(seccion.get("tramo"), cfg) if familia == "gemini" else None
        previo = trozos[-1] if trozos else None
        # UNA SOLA TOMA para las secciones seguidas con el MISMO estilo, mientras
        # quepan: grabadas por separado, cada una sale con su propia entonacion
        # (la prueba A/B del 06-10: la toma unica sonaba natural y la de una
        # peticion por bloque, sobreactuada al principio).
        if (previo is not None and previo["estilo"] == estilo
                and len(f"{previo['texto']} {texto}".encode("utf-8")) <= BYTES_TOMA_UNICA):
            previo["texto"] = f"{previo['texto']} {texto}"
            previo["secciones"].append(seccion["id"])
        else:
            trozos.append({"texto": texto, "seccion": seccion["id"],
                           "secciones": [seccion["id"]], "estilo": estilo})
        trozo_de.append(len(trozos) - 1)
    hablados = [" ".join(marcas_tts.limpiar(por_id[b]["texto"]) for b in s["bloques"])
                for s in secciones]
    hablado = " ".join(hablados)
    if simulado():
        wav, duracion, palabras = _toma_simulada(hablado, cfg)
        return wav, duracion, palabras, {"simulado": True, "peticiones": len(trozos)}

    wav, duracion, alineado, info = _google_verificado(trozos, hablados, cfg, progreso,
                                                       trozo_de=trozo_de)
    info["alineado"] = _resumen_alineado(alineado)
    return wav, duracion, alineado["palabras"], info


def _sintesis_elevenlabs(trozos, cfg, progreso, antes="", despues=""):
    """La llamada que se PAGA a ElevenLabs (en creditos del plan).

    -> (wav, segundos, palabras o None, info). Aparte, como la de Google, para
    que el medidor la envuelva a ella sola (nucleo/coste.py).
    """
    return comun.llamar_motor(
        motor_eleven.sintetizar_trozos, trozos, voz=cfg["voz_id"], modelo=cfg["modelo"],
        idioma=cfg.get("idioma"), velocidad=cfg.get("speed"), avisar=progreso,
        antes=antes, despues=despues)


def _toma_elevenlabs(secciones, bloques, cfg, progreso, antes="", despues=""):
    """Toda la toma (o una seccion) con ElevenLabs. -> (wav, segundos, palabras, info)

    Las marcas vienen de la propia API, por caracter. Si alguna pieza vuelve
    sin ellas, el alineador local las pone sobre la pista entera: es el
    respaldo, no el camino normal.
    """
    por_id = {b["id"]: b for b in bloques}
    trozos = [{"texto": " ".join(marcas_tts.limpiar(por_id[b]["texto"])
                                 for b in seccion["bloques"]),
               "seccion": seccion["id"]} for seccion in secciones]
    hablado = " ".join(t["texto"] for t in trozos)
    if simulado():
        wav, duracion, palabras = _toma_simulada(hablado, cfg)
        return wav, duracion, palabras, {"simulado": True, "peticiones": len(trozos)}
    wav, duracion, palabras, info = _sintesis_elevenlabs(
        trozos, cfg, lambda f, m="": progreso(0.85 * f, m), antes, despues)
    if palabras is None:
        alineado = _alinear(wav, hablado, cfg, lambda f, m="": progreso(0.85 + 0.15 * f, m))
        palabras = alineado["palabras"]
        info["alineado"] = _resumen_alineado(alineado)
    return wav, duracion, palabras, info


def _avisos_google(info, secciones):
    """Lo que hay que decirle a quien escucha, en sus palabras. -> [str]"""
    nombres = {i: s for i, s in enumerate(secciones)}
    avisos = []
    for aviso in (info or {}).get("avisos") or []:
        a, b = (nombres.get(i, {}) for i in aviso["entre"])
        donde = (f"entre {a.get('id')} ({a.get('tramo')}) y {b.get('id')} "
                 f"({b.get('tramo')})")
        if aviso["tipo"] == "volumen":
            avisos.append(f"salto de volumen de {aviso['db']:+.1f} dB {donde}: ya "
                          f"se ha igualado, pero escucha esa costura")
        else:
            avisos.append(f"cambio de ritmo del {aviso['cambio'] * 100:+.0f} % {donde}")
    for regrabado in (info or {}).get("regrabados") or []:
        avisos.append(f"{regrabado['seccion']} se regrabó sola: la voz había leído en voz "
                      f"alta su instrucción de estilo («{str(regrabado['detalle'])[:60]}»)"
                      if regrabado["motivo"] == "fuga" else
                      f"{regrabado['seccion']} se regrabó sola: la voz se había saltado "
                      f"{regrabado['detalle']} palabras")
    for mala in (info or {}).get("sin_arreglar") or []:
        avisos.append(f"¡OJO! {mala['seccion']} sigue saliendo mal tras {REINTENTOS_FUGA} "
                      f"intentos ({'lee su instrucción de estilo' if mala['motivo'] == 'fuga' else 'se salta texto'}): "
                      f"regrábala en la revisión de audio")
    alineado = (info or {}).get("alineado") or {}
    if alineado.get("cobertura") is not None and alineado["cobertura"] < 0.9:
        avisos.append(f"solo el {alineado['cobertura'] * 100:.0f} % del guion se oye "
                      f"tal cual: la voz puede haberse saltado o cambiado algo "
                      f"({' '.join(alineado.get('faltan') or [])[:120]})")
    return avisos


def _toma_por_contexto(trozos, cfg, progreso):
    """Una toma en varias entradas del MISMO contexto. (wav, dur, palabras).

    Suena como la toma unica porque la prosodia se mantiene entre entradas; lo
    que se gana es saber donde empieza y acaba cada seccion dentro del audio.
    """
    import websocket  # websocket-client; solo hace falta por este camino

    api_key = comun.llamar_motor(motor.cargar_api_key)
    url = (f"wss://api.cartesia.ai/tts/websocket?api_key={api_key}"
           f"&cartesia_version={motor.API_VERSION}")
    voz = {"mode": "id", "id": cfg["voz_id"]}
    if not usa_generation_config(cfg["modelo"]) and cfg.get("experimental_controls"):
        voz["__experimental_controls"] = copy.deepcopy(cfg["experimental_controls"])
    base = {"model_id": cfg["modelo"], "voice": voz,
            "output_format": {"container": "raw", "encoding": "pcm_s16le",
                              "sample_rate": SR},
            "language": cfg["idioma"], "add_timestamps": True}
    if usa_generation_config(cfg["modelo"]) and cfg.get("generation_config"):
        base["generation_config"] = copy.deepcopy(cfg["generation_config"])

    contexto = f"estudio_{hashlib.sha256(str(trozos).encode()).hexdigest()[:12]}"
    previstas = max(1, sum(len(t.split()) for t in trozos))
    conexion = websocket.create_connection(url, timeout=600)
    try:
        for indice, texto in enumerate(trozos):
            conexion.send(json.dumps({**base, "context_id": contexto,
                                      "transcript": texto,
                                      "continue": indice < len(trozos) - 1}))
        pcm, palabras = [], []
        ultimo_aviso = 0.0
        while True:
            # Un solo 'done' para TODO el contexto, no uno por entrada: esperar
            # uno por seccion cuelga la conexion hasta el timeout.
            evento = json.loads(conexion.recv())
            if evento.get("error"):
                raise RuntimeError(f"Cartesia WS: {evento['error']}")
            if evento.get("data"):
                pcm.append(base64.b64decode(evento["data"]))
            marcas = evento.get("word_timestamps") or {}
            for palabra, ini, fin in zip(marcas.get("words") or [],
                                         marcas.get("start") or [],
                                         marcas.get("end") or []):
                hablada = marcas_tts.texto_de_marca(palabra)
                if hablada is None:
                    continue
                palabras.append({"w": hablada, "s": round(ini, 3),
                                 "e": round(fin, 3)})
            if time.time() - ultimo_aviso > 0.5:
                ultimo_aviso = time.time()
                progreso(min(0.99, len(palabras) / previstas),
                         f"{len(palabras)} de ~{previstas} palabras")
            if evento.get("type") == "done":
                break
    finally:
        try:
            conexion.close()
        except Exception:  # noqa: BLE001
            pass

    crudo = b"".join(pcm)
    if not crudo:
        raise RuntimeError("Cartesia devolvio la toma sin audio")
    if not palabras:
        raise RuntimeError("Cartesia devolvio audio sin marcas de palabra: "
                           "sin marcas no se puede sincronizar el montaje")
    return motor.wav_desde_pcm(crudo), len(crudo) / (SR * 2), palabras


def _toma_real(texto, cfg, progreso):
    """Llamada SSE a Cartesia con __experimental_controls."""
    # cargar_api_key aborta con SystemExit, que dentro de un hilo del gestor de
    # trabajos no lo recoge nadie
    api_key = comun.llamar_motor(motor.cargar_api_key)
    cabeceras = {
        "X-API-Key": api_key,
        "Cartesia-Version": motor.API_VERSION,
        "Content-Type": "application/json",
    }
    voz = {"mode": "id", "id": cfg["voz_id"]}
    nuevo = usa_generation_config(cfg["modelo"])
    if not nuevo and cfg.get("experimental_controls"):
        voz["__experimental_controls"] = copy.deepcopy(cfg["experimental_controls"])
    cuerpo = {
        "model_id": cfg["modelo"],
        "transcript": texto,
        "voice": voz,
        "output_format": {"container": "raw", "encoding": "pcm_s16le",
                          "sample_rate": SR},
        "language": cfg["idioma"],
        "add_timestamps": True,
    }
    # sonic-3/3.5 ignoran __experimental_controls sin decir nada; ver el bloque
    # de arriba con las mediciones
    if nuevo and cfg.get("generation_config"):
        cuerpo["generation_config"] = copy.deepcopy(cfg["generation_config"])

    previstas = max(1, len(texto.split()))
    respuesta = requests.post(motor.API_SSE, headers=cabeceras, json=cuerpo,
                              stream=True, timeout=(30, 600))
    try:
        if respuesta.status_code != 200:
            raise RuntimeError(f"Cartesia SSE HTTP {respuesta.status_code}: "
                               f"{respuesta.text[:300]}")
        trozos, palabras = [], []
        ultimo_aviso = 0.0
        for linea in respuesta.iter_lines(decode_unicode=True):
            if not linea or not linea.startswith("data:"):
                continue
            try:
                evento = json.loads(linea[5:].strip())
            except json.JSONDecodeError:
                continue
            if evento.get("error"):
                raise RuntimeError(f"Cartesia SSE: {evento['error']}")
            if evento.get("data"):
                trozos.append(base64.b64decode(evento["data"]))
            marcas = evento.get("word_timestamps") or {}
            if marcas.get("words"):
                for palabra, ini, fin in zip(marcas["words"], marcas["start"],
                                             marcas["end"]):
                    # Cartesia deja fuera de las marcas casi todas las
                    # etiquetas, pero NO <spell>: vuelve como el token entero
                    # '<spell>ABC</spell>'. Se le quita la etiqueta y se queda
                    # la palabra, porque el reparto la espera (limpiar() ya la
                    # dejo como 'ABC'). Tirarla correria una palabra todos los
                    # cortes de plano del resto del video.
                    hablada = marcas_tts.texto_de_marca(palabra)
                    if hablada is None:
                        continue
                    palabras.append({"w": hablada, "s": round(ini, 3),
                                     "e": round(fin, 3)})
            # avisar en cada evento saturaria al gestor de trabajos: la toma de
            # un guion largo son miles de eventos
            if time.time() - ultimo_aviso > 0.5:
                ultimo_aviso = time.time()
                progreso(min(0.99, len(palabras) / previstas),
                         f"{len(palabras)} de ~{previstas} palabras")
    finally:
        respuesta.close()

    pcm = b"".join(trozos)
    if not pcm:
        raise RuntimeError("Cartesia devolvio la toma sin audio")
    if not palabras:
        raise RuntimeError("Cartesia devolvio audio sin marcas de palabra: "
                           "sin marcas no se puede sincronizar el montaje")
    return motor.wav_desde_pcm(pcm), len(pcm) / (SR * 2), palabras


def sintetizar_toma(texto, cfg, progreso=None):
    """Una toma continua de un texto -> (wav, duracion, palabras)."""
    if not texto.strip():
        raise ValueError("no hay texto que sintetizar")
    avisa = _avisador(progreso)
    if simulado():
        avisa(0.5, "simulando toma")
        return _toma_simulada(texto, cfg)
    if cfg.get("proveedor") == "google":
        return _toma_google_suelta(texto, cfg, avisa)
    if cfg.get("proveedor") == "elevenlabs":
        bloque = [{"id": "PREV", "texto": texto}]
        wav, duracion, palabras, _info = _toma_elevenlabs(
            [{"id": "SB001", "bloques": ["PREV"]}], bloque, cfg, avisa)
        return wav, duracion, palabras
    return _toma_real(texto, cfg, avisa)


def _toma_google_suelta(texto, cfg, avisa):
    """Un texto corto con Google, para ESCUCHAR la voz (previsualizar).

    Va con el estilo de la intro, que es lo primero que suena del video. Las
    marcas son estimadas y estiradas a la duracion real: para escuchar no hace
    falta arrancar el alineador.
    """
    familia = motor_google.familia_de(cfg["modelo"])
    wav, duracion, _info = _sintesis_google(
        [{"texto": marcas_tts.para_google(texto, familia),
          "estilo": estilo_de("intro", cfg) if familia == "gemini" else None}],
        cfg, avisa)
    palabras, estimada = _estimar_marcas(marcas_tts.limpiar(texto))
    escala = duracion / estimada if estimada else 1.0
    for palabra in palabras:
        palabra["s"] = round(palabra["s"] * escala, 3)
        palabra["e"] = round(palabra["e"] * escala, 3)
    return wav, duracion, palabras


def _reparto(bloques, palabras):
    """Reparte las palabras de la toma entre los bloques.

    Se reutiliza el emparejador tolerante del motor: compara normalizado y con
    ventana, porque el modelo puede unir o partir un token y un desajuste de uno
    desplazaria todos los cortes siguientes.

    Y por eso mismo el texto que se le da va SIN anotaciones de voz. El
    emparejador compara lo que el guion dice con lo que Cartesia devolvio, y
    Cartesia devuelve solo palabras habladas: las etiquetas no salen en las
    marcas (medido). Dejarlas aqui meteria tokens que no existen en la respuesta
    y cada uno se comeria una palabra real, desplazando de ahi en adelante todos
    los cortes de plano del video.
    """
    escenas = [{"id": b["id"], "narracion": marcas_tts.limpiar(b["texto"])}
               for b in bloques]
    return escenas, motor._repartir_palabras(escenas, palabras or [])


def sintetizar_bloques(bloques, destino, cfg, avisar=None,
                       nombre=NOMBRE_PISTA, extra_meta=None, proyecto_id=None):
    """Sintetiza los bloques en una toma unica y deja pista y meta en destino.

    Devuelve el dict de salidas del paso (pista, duracion, palabras, controles,
    bloques con t_in/t_out).
    """
    arranque = time.time()
    avisa = _avisador(avisar)
    bloques = [b for b in bloques if b.get("texto", "").strip()]
    if not bloques:
        raise RuntimeError("el guion no tiene ni un bloque con texto")
    os.makedirs(destino, exist_ok=True)

    # El transcript se manda ANOTADO: las etiquetas SSML son lo unico que le
    # dice a Cartesia donde para el relato y donde cambia de marcha. Se sanea
    # antes porque una etiqueta que Cartesia no reconoce la LOCUTA en voz alta
    # (medido), y eso no se arregla en el montaje.
    avisos_marcas = []
    anotados = [dict(b, texto=marcas_tts.sanear(b["texto"], avisos_marcas))
                for b in bloques]
    anotados = [b for b in anotados if b["texto"].strip()]
    if not anotados:
        raise RuntimeError("el guion no tiene ni un bloque con texto que locutar")
    texto = " ".join(b["texto"].strip() for b in anotados)

    # Y todo lo demas trabaja sobre lo que se OYE. La cuenta de palabras, la
    # revision de tildes y el reparto de marcas cuentan la narracion, no el
    # marcado: '<break time="900ms"/>' no es una palabra ni le falta una tilde.
    hablado = " ".join(marcas_tts.limpiar(b["texto"]) for b in anotados)
    avisa(0.05, f"toma unica: {len(anotados)} bloques, {len(hablado.split())} palabras"
                + (f", {sum(marcas_tts.resumen(anotados).values())} anotaciones de voz"
                   if marcas_tts.hay_marcas(texto) else ""))
    for problema in dict.fromkeys(avisos_marcas):
        avisa(0.05, f"anotacion corregida: {problema}")

    # Antes de pagar el TTS: si el guion viene sin tildes, la toma entera se
    # pronuncia mal y no hay montaje que lo arregle. No se aborta -- puede ser
    # un idioma sin tildes o un texto corto -- pero se dice ANTES, que es
    # cuando todavia se puede parar y corregir el guion.
    # Lo mismo que con las tildes y por lo mismo: una cifra la pronuncia el
    # sintetizador como el decida, y eso no se arregla en el montaje.
    cifras = comun.revisar_cifras(hablado)
    if cifras["hay_cifras"]:
        avisa(0.06, "AVISO: el guion trae cifras o simbolos sin escribir con "
                    "letras y esto se locuta; " + ", ".join(cifras["ejemplos"]))

    ortografia = comun.revisar_tildes(hablado, cfg.get("idioma") or "es")
    if ortografia["sin_tildes"]:
        avisa(0.06, "AVISO: el guion viene sin tildes y esto se locuta; "
                    + (", ".join(ortografia["ejemplos"])
                       or f"solo el {ortografia['proporcion'] * 100:.1f} % de "
                          f"las palabras lleva diacritico"))

    def progreso(fraccion, mensaje=""):
        return avisa(0.05 + 0.8 * max(0.0, min(1.0, fraccion)), mensaje)

    # La toma va en SECCIONES dentro de un mismo contexto: suena igual que de
    # una vez -- la prosodia se mantiene entre entradas y los tiempos siguen
    # siendo continuos -- y ademas queda escrito donde empieza y acaba cada una,
    # que es lo que permite regrabar solo la que salga mal.
    secciones = agrupar_secciones(anotados)
    por_bloque = {b["id"]: b["texto"].strip() for b in anotados}
    trozos = [" ".join(por_bloque[bid] for bid in sec["bloques"])
              for sec in secciones]
    wav = duracion = palabras = None
    info_google = info_eleven = None
    if cfg.get("proveedor") == "google":
        # Una peticion por seccion con el estilo de su tramo, y el alineador
        # local para las marcas. Sin caida a otro camino: si Google falla, el
        # fallo se dice; grabar con otra voz a escondidas seria peor.
        secciones = asignar_tramos(separar_intro(secciones, anotados, cfg), cfg)
        avisa(0.05, f"{len(secciones)} secciones con Google "
                    f"({cfg['modelo']}, voz {cfg['voz_id']})")
        wav, duracion, palabras, info_google = _toma_google(
            secciones, anotados, cfg, progreso)
        for aviso in _avisos_google(info_google, secciones):
            avisa(0.86, f"AVISO: {aviso}")
    elif cfg.get("proveedor") == "elevenlabs":
        # una peticion por seccion, cada una con el texto vecino como contexto;
        # las marcas vienen de la API. Sin caida a otro proveedor, como Google.
        avisa(0.05, f"{len(secciones)} secciones con ElevenLabs ({cfg['modelo']})")
        wav, duracion, palabras, info_eleven = _toma_elevenlabs(
            secciones, anotados, cfg, progreso)
    elif len(trozos) > 1 and not simulado():
        avisa(0.05, f"{len(trozos)} secciones en un solo contexto")
        try:
            wav, duracion, palabras = _toma_por_contexto(trozos, cfg, progreso)
        except Exception as fallo:  # noqa: BLE001
            # Que el camino nuevo falle no puede dejar sin voz al video: se cae
            # al de siempre, que es el mismo texto de una sola vez.
            avisa(0.06, f"el contexto no ha ido ({fallo}); se graba de una vez")
            wav = None
    if wav is None:
        wav, duracion, palabras = sintetizar_toma(texto, cfg, progreso)
        secciones = [{"id": "SB001", "bloques": [b["id"] for b in anotados]}]
    avisa(0.88, "repartiendo palabras entre bloques")
    escenas, reparto = _reparto(anotados, palabras)

    hueco = float(cfg.get("hueco_minimo") or 0.0)
    silencio_anadido = 0.0
    # LA TOMA CRUDA, tal cual la devolvio la voz y antes del aire entre bloques:
    # con ella, cambiar el largo de las pausas no obliga a volver a pagar la voz
    # (09-10-2026: un video de 23 min se regrabo dos veces por las pausas). Se
    # escribe YA porque `espaciar` mueve las marcas del reparto en su sitio.
    if hueco > 0 and len(anotados) > 1:
        os.makedirs(destino, exist_ok=True)
        with open(os.path.join(destino, NOMBRE_TOMA_CRUDA), "wb") as fh:
            fh.write(wav)
        comun.escribir_json(os.path.join(destino, NOMBRE_MARCAS_CRUDAS), {
            "hueco_minimo": hueco, "palabras": palabras,
            "reparto": {e["id"]: reparto.get(e["id"]) or [] for e in escenas}})
    if hueco > 0 and len(anotados) > 1:
        wav, desplazamientos = motor.espaciar(wav, palabras, reparto, escenas, hueco)
        if desplazamientos:
            for tramo in reparto.values():
                for palabra in tramo:
                    palabra["s"] = motor.aplicar_desplazamiento(palabra["s"], desplazamientos)
                    palabra["e"] = motor.aplicar_desplazamiento(palabra["e"], desplazamientos)
            silencio_anadido = desplazamientos[-1]["retardo"]
            duracion += silencio_anadido
            avisa(0.9, f"+{silencio_anadido:.2f}s de aire entre bloques")

    destino_wav = os.path.join(destino, nombre)
    with open(destino_wav, "wb") as fh:
        fh.write(wav)

    fichas, ordenadas = [], []
    for bloque in anotados:
        tramo = reparto.get(bloque["id"], [])
        ordenadas.extend(tramo)
        fichas.append({
            "id": bloque["id"],
            # el texto tal y como se MANDO, anotaciones incluidas: es lo que
            # permite que la revision de audio compruebe si esta toma sigue
            # valiendo sin volver a grabarla
            "texto": bloque["texto"],
            "narracion": marcas_tts.limpiar(bloque["texto"]),
            "t_in": round(tramo[0]["s"], 3) if tramo else None,
            "t_out": round(tramo[-1]["e"], 3) if tramo else None,
            "palabras": len(tramo),
        })

    salidas = {
        "pista": destino_wav,
        "archivo": nombre,
        "duracion": round(duracion, 3),
        "palabras": ordenadas,
        "controles": copy.deepcopy(cfg),
        "bloques": fichas,
        "simulado": simulado(),
        "silencio_anadido": round(silencio_anadido, 3),
        "meta": NOMBRE_META,
        "secciones": _fichas_de_seccion(secciones, fichas),
        "marcas_tts": marcas_tts.resumen(anotados),
        "avisos_marcas": list(dict.fromkeys(avisos_marcas)),
        "resumen": (f"{len(anotados)} bloques, {round(duracion, 1)}s, "
                    f"{cfg['modelo']} {cfg.get('preset') or 'sin preset'}"),
    }
    if info_google is not None:
        salidas["google"] = dict(info_google, avisos_texto=_avisos_google(
            info_google, secciones))
    if info_eleven is not None:
        salidas["elevenlabs"] = info_eleven
    if extra_meta:
        salidas.update(extra_meta)

    meta = dict(salidas)
    meta["transcript"] = texto
    comun.escribir_json(os.path.join(destino, NOMBRE_META), meta)

    # LA CADENCIA QUE HA SALIDO DE VERDAD, al historico. Es lo que hace que la
    # proxima estimacion de duracion deje de ser una tabla escrita: `cadencia`
    # solo la usa cuando hay tomas suficientes de la misma voz y velocidad, y no
    # cuenta las simuladas -- que salen del propio estimador, asi que meterlas
    # seria medirse a uno mismo. Este paso no anotaba NADA hasta el 24-08.
    medida = cadencia.medida_de_toma(len(ordenadas), duracion, silencio_anadido)
    estadisticas.anotar(
        PASO, max(0.01, time.time() - arranque), tamano=len(ordenadas),
        proyecto=proyecto_id, idioma=cfg.get("idioma"),
        detalle={"cadencia_palabras_s": medida,
                 "velocidad": cadencia.nombre_velocidad(cfg.get("velocidad")),
                 "hueco_minimo": cfg.get("hueco_minimo"),
                 "modelo_voz": cfg.get("modelo"), "voz_id": cfg.get("voz_id"),
                 "bloques": len(anotados), "duracion_s": round(duracion, 2),
                 "silencio_anadido_s": round(silencio_anadido, 3),
                 "simulado": simulado()})

    avisa(0.98, f"toma de {duracion:.1f}s lista")
    return salidas


# ------------------------------------------------------------------ paso 4

def ejecutar(proyecto, params, avisar=None):
    """Sintetiza el guion entero y deja la pista en la carpeta de trabajo.

    La pista se escribe en pasos/voz/trabajo/; al llamar a estado.completar la
    carpeta entera pasa a ser pasos/voz/v<N>/, asi que la ruta definitiva es
    ruta_paso("voz") + salidas["archivo"].
    """
    avisa = _avisador(avisar)
    avisa(0.01, "leyendo guion")
    bloques = cargar_guion(proyecto, params)
    cfg = resolver_params(params_con_idioma(proyecto, params))
    destino = comun.preparar_trabajo(proyecto, PASO)

    salidas = sintetizar_bloques(bloques, destino, cfg, avisar=avisa,
                                 proyecto_id=getattr(proyecto, "id", None))
    comun.escribir_json(os.path.join(destino, "guion_locutado.json"),
                        {"bloques": bloques})
    avisa(1.0, salidas["resumen"])
    return salidas


def _fichas_de_seccion(secciones, fichas):
    """Cada seccion con el tramo de audio que ocupa, sacado de sus bloques.

    Son datos DE MAS: nada aguas abajo los mira. El montaje sigue leyendo la
    lista de palabras y los bloques, que no cambian. Esto es para la revision:
    poder oir una seccion y pedir que se regrabe solo ella.
    """
    por_id = {f["id"]: f for f in fichas}
    salida = []
    for seccion in secciones or []:
        dentro = [por_id[b] for b in seccion["bloques"] if b in por_id]
        tiempos = [f for f in dentro if f.get("t_in") is not None]
        ficha = {
            "id": seccion["id"],
            "bloques": list(seccion["bloques"]),
            "t_in": tiempos[0]["t_in"] if tiempos else None,
            "t_out": tiempos[-1]["t_out"] if tiempos else None,
        }
        # el tramo (intro/cuerpo/cierre) solo existe con Google: es lo que hace
        # que regrabar la seccion use el estilo que le toca
        if seccion.get("tramo"):
            ficha["tramo"] = seccion["tramo"]
        salida.append(ficha)
    return salida


def ruta_pista(proyecto, salidas, paso_id="voz"):
    """Ruta real del wav, este el paso en trabajo/ o ya versionado."""
    nombre = (salidas or {}).get("archivo") or NOMBRE_PISTA
    candidatas = [
        (salidas or {}).get("pista"),
        os.path.join(proyecto.ruta_paso(paso_id), nombre),
        os.path.join(proyecto.ruta_trabajo(paso_id, crear=False), nombre),
    ]
    for ruta in candidatas:
        if ruta and os.path.exists(ruta):
            return ruta
    raise FileNotFoundError(f"no encuentro la pista de {paso_id}")


# ----------------------------------------------------------- previsualizacion

def recortar_texto(bloques, segundos, velocidad=None):
    """Trozo inicial del guion que dura aproximadamente 'segundos' al leerlo.

    Se corta en final de frase si hay uno cerca: una previsualizacion que acaba
    a media palabra no sirve para juzgar la voz.
    """
    factor = _factor_velocidad(velocidad)
    tope = max(2.0, float(segundos))
    tomadas, reloj = [], 0.0
    for bloque in bloques:
        # Sin anotaciones: una previsualizacion cortada por la mitad puede
        # dejar abierto un <speed> y el resto sonaria lento sin motivo. Lo que
        # se juzga aqui es la VOZ, no el marcado.
        for palabra in marcas_tts.limpiar(bloque["texto"]).split():
            duracion = _duracion_palabra(palabra, factor)
            if reloj + duracion > tope and tomadas:
                break
            tomadas.append(palabra)
            reloj += duracion
        if reloj >= tope:
            break
    if not tomadas:
        return ""
    for posicion in range(len(tomadas) - 1, max(0, len(tomadas) - 12) - 1, -1):
        if tomadas[posicion].endswith((".", "!", "?")):
            return " ".join(tomadas[:posicion + 1])
    texto = " ".join(tomadas)
    return texto if texto.endswith((".", "!", "?")) else texto + "."


def previsualizar(proyecto, params, segundos=20):
    """Sintetiza solo los primeros ~N segundos del guion para escuchar la voz.

    El resultado se cachea por configuracion: volver a pulsar 'escuchar' con los
    mismos mandos no vuelve a pagar la sintesis.
    """
    bloques = cargar_guion(proyecto, params)
    cfg = resolver_params(params_con_idioma(proyecto, params))
    texto = recortar_texto(bloques, segundos, cfg.get("velocidad"))
    if not texto:
        raise RuntimeError("el guion no tiene texto que previsualizar")

    firma = hashlib.sha256(json.dumps(
        [texto, cfg["modelo"], cfg["voz_id"], cfg["idioma"],
         cfg["experimental_controls"], simulado()]
        # con Google, el estilo de la intro cambia lo que suena; sin Google la
        # lista se queda como era y los wav ya cacheados siguen valiendo
        + ([cfg.get("proveedor"), estilo_de("intro", cfg)]
           if cfg.get("proveedor") == "google" else [])
        # con ElevenLabs suena distinto segun la velocidad (speed)
        + ([cfg.get("proveedor"), cfg.get("speed")]
           if cfg.get("proveedor") == "elevenlabs" else []),
        sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    carpeta = proyecto.ruta("previsualizaciones")
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, f"voz_{firma}.wav")
    if os.path.exists(ruta):
        return ruta

    wav, duracion, palabras = sintetizar_toma(texto, cfg)
    with open(ruta, "wb") as fh:
        fh.write(wav)
    with open(ruta[:-4] + ".json", "w", encoding="utf-8") as fh:
        json.dump({"texto": texto, "duracion": round(duracion, 3),
                   "palabras": palabras, "controles": cfg,
                   "simulado": simulado()},
                  fh, ensure_ascii=False, indent=2)
    return ruta


# ------------------------------------------------------------ catalogo de voces

VOCES_BASE = [
    {"id": presets_voz.VOZ_HECTOR, "nombre": "Hector - Tour Leader", "idioma": "es"},
    {"id": presets_voz.VOZ_LUIS, "nombre": "Luis - News Caster", "idioma": "es"},
    {"id": presets_voz.VOZ_GONZALO, "nombre": "Gonzalo - Grounded Storyteller",
     "idioma": "es"},
    {"id": motor.VOCES["en"]["id"], "nombre": motor.VOCES["en"]["nombre"],
     "idioma": "en"},
]


def _ficha_voz(cruda):
    locales, nativos = [], []
    for entrada in (cruda.get("locales") or []):
        if not isinstance(entrada, dict) or not entrada.get("locale"):
            continue
        locales.append(entrada["locale"])
        if entrada.get("is_native"):
            nativos.append(entrada["locale"])
    return {
        "id": cruda.get("id"),
        "nombre": cruda.get("name") or cruda.get("id"),
        "descripcion": (cruda.get("description") or "").strip(),
        "idioma": cruda.get("language") or "",
        "genero": cruda.get("gender") or "",
        "pais": cruda.get("country") or "",
        "locales": locales,
        "locales_nativos": nativos,
        "pro": bool(cruda.get("is_pro")),
        "publica": bool(cruda.get("is_public", True)),
    }


def _descargar_voces():
    api_key = motor.cargar_api_key()
    cabeceras = {"X-API-Key": api_key, "Cartesia-Version": motor.API_VERSION}
    fichas, cursor = [], None
    vistas = set()
    # el endpoint pagina de 100 en 100; el catalogo publico pasa de 600 voces
    for _ in range(40):
        url = f"{API_VOCES}?limit=100"
        if cursor:
            url += f"&starting_after={cursor}"
        respuesta = requests.get(url, headers=cabeceras, timeout=60)
        if respuesta.status_code != 200:
            raise RuntimeError(f"Cartesia /voices HTTP {respuesta.status_code}: "
                               f"{respuesta.text[:200]}")
        datos = respuesta.json()
        for cruda in datos.get("data", []):
            ficha = _ficha_voz(cruda)
            if ficha["id"] and ficha["id"] not in vistas:
                vistas.add(ficha["id"])
                fichas.append(ficha)
        if not datos.get("has_more"):
            break
        cursor = datos.get("next_page")
        if not cursor:
            break
    return fichas


def _nativa_en(ficha, clave):
    if (ficha.get("idioma") or "").lower() == clave:
        return True
    return any(str(l).lower().startswith(clave + "-")
               for l in ficha.get("locales_nativos", []))


def _filtrar_idioma(fichas, idioma, solo_nativas=False):
    """Voces que pueden narrar en ese idioma, las nativas primero.

    Sonic es multilingue: una voz etiquetada en ingles puede leer espanol, pero
    con acento. Se marcan con "nativa" y se ordenan detras en vez de esconderlas,
    que a veces ese acento es justo lo que se busca.
    """
    if not idioma:
        for ficha in fichas:
            ficha["nativa"] = True
        return fichas
    clave = str(idioma).strip().lower()
    elegidas = []
    for ficha in fichas:
        # UNA VOZ PROPIA (clonada en la cuenta) SIEMPRE ENTRA. Llega sin
        # `locales`, asi que el filtro de nativas la tiraba: la voz que el
        # canal se ha clonado era justo la que no salia en la lista
        # (02-09-2026). Cuenta como nativa de su idioma, o de cualquiera si
        # no declara ninguno.
        propia = not ficha.get("publica", True)
        if propia:
            declarado = (ficha.get("idioma") or "").lower()
            if declarado and declarado != clave:
                continue
            ficha["nativa"] = True
            elegidas.append(ficha)
            continue
        nativa = _nativa_en(ficha, clave)
        habla = nativa or any(str(l).lower().startswith(clave + "-")
                              for l in ficha.get("locales", []))
        if not habla or (solo_nativas and not nativa):
            continue
        ficha["nativa"] = nativa
        elegidas.append(ficha)
    # las propias delante: son pocas y son las que se buscan
    elegidas.sort(key=lambda f: (f.get("publica", True), not f["nativa"], f["nombre"]))
    return elegidas


def _huella_clave():
    """Una huella de la clave de Cartesia en uso, o "" si no hay. -> str

    LA HUELLA, NUNCA LA CLAVE: esto acaba escrito en un fichero de cache que no
    es un secreto. Doce caracteres de un sha256 bastan para saber si la clave es
    OTRA, que es lo unico que hay que saber.
    """
    try:
        clave = motor.cargar_api_key()
    except BaseException:                                       # noqa: BLE001
        # cargar_api_key levanta SystemExit cuando no hay clave configurada, y
        # aqui eso no es un fallo: es "no se puede saber", y entonces la huella
        # simplemente no opina sobre el cache.
        return ""
    return hashlib.sha256(str(clave).encode("utf-8")).hexdigest()[:12]


def _hay_clave():
    """Si esta maquina puede hablar con Cartesia. -> bool

    Se pregunta al MOTOR, que es quien sabe de donde puede salir la clave (el
    entorno, el almacen que escribe Configuracion, un .env), y se traga su
    SystemExit: esta escrito como CLI y aborta cuando no la encuentra.
    """
    try:
        return bool(comun.cargar_motor("voz_cartesia", "voz.py").cargar_api_key())
    except BaseException:                                     # noqa: BLE001
        return False


# --------------------------------------------------------------- prueba A/B
#
# EL MISMO TEXTO PARA TODAS LAS VOCES, y con dos secciones: un gancho y el
# arranque del cuerpo. Asi se oye cada voz con los dos estilos y, sobre todo, la
# COSTURA entre ellos, que es lo que no se puede juzgar con doce segundos.
# ~140 palabras, unos 70 s: lo bastante para cansarse de una voz, lo bastante
# poco para que comparar cuatro cueste centimos.

TEXTOS_AB = {
    "en": [
        {"id": "AB01", "abre_seccion": True, "texto":
            "Most people have never been told this, but the families who quietly "
            "built real wealth during the Great Depression followed a handful of "
            "simple rules. Not secret investments. Not lucky breaks. Just small "
            "habits, repeated every single week."},
        {"id": "AB02", "texto": "And almost nobody talks about them today. So let's fix that."},
        {"id": "AB03", "abre_seccion": True, "texto":
            "Habit number one: they paid themselves first. Before rent, before "
            "groceries, before anything else, a small part of every paycheck went "
            "straight into a jar, a tin, or a savings account."},
        {"id": "AB04", "texto":
            "It didn't matter if it was ten percent or just a few dollars. What "
            "mattered was that it happened automatically, every time, without a "
            "second thought."},
        {"id": "AB05", "texto":
            "Today you can do the same thing in about five minutes. Set up an "
            "automatic transfer for the day after payday, and you'll never even "
            "miss the money."},
    ],
    "es": [
        {"id": "AB01", "abre_seccion": True, "texto":
            "Casi nadie te lo ha contado, pero las familias que construyeron un "
            "patrimonio de verdad durante la Gran Depresión seguían unas pocas "
            "reglas muy sencillas. Ni inversiones secretas ni golpes de suerte. "
            "Solo pequeños hábitos, repetidos cada semana."},
        {"id": "AB02", "texto": "Y hoy casi nadie habla de ellos. Vamos a arreglarlo."},
        {"id": "AB03", "abre_seccion": True, "texto":
            "Hábito número uno: se pagaban primero a sí mismos. Antes del alquiler, "
            "antes de la compra, antes de cualquier otra cosa, una parte de cada "
            "sueldo iba directa a un frasco, a una lata o a una cuenta de ahorro."},
        {"id": "AB04", "texto":
            "Daba igual que fuera el diez por ciento o unas pocas monedas. Lo que "
            "importaba es que pasara solo, cada vez, sin pensarlo."},
        {"id": "AB05", "texto":
            "Hoy puedes hacer lo mismo en cinco minutos. Programa una transferencia "
            "automática para el día después de cobrar y ni siquiera echarás de "
            "menos ese dinero."},
    ],
}


def bloques_ab(idioma):
    """El pasaje de la prueba A/B en ese idioma (ingles si no lo hay). -> [bloques]"""
    return copy.deepcopy(TEXTOS_AB.get(str(idioma or "en").lower(), TEXTOS_AB["en"]))


def listar_voces_google(idioma=None):
    """Las voces de Google (Gemini TTS / Chirp 3 HD), con la misma forma de
    ficha que las de Cartesia para que la pantalla las pinte igual.

    Es una lista fija: son las que publica Google para estas dos familias, y
    hablan todos los idiomas del modelo (el acento lo pone el idioma, no la voz).
    """
    codigo = str(idioma or "en").strip().lower() or "en"
    fichas = []
    for genero, nombres in (("masculina", motor_google.VOCES_MASCULINAS),
                            ("femenina", motor_google.VOCES_FEMENINAS)):
        for nombre in nombres:
            fichas.append({"id": nombre, "nombre": nombre, "descripcion": f"voz {genero}",
                           "idioma": codigo, "genero": genero, "pais": "",
                           "locales": [], "locales_nativos": [], "pro": False,
                           "publica": True, "nativa": True, "proveedor": "google"})
    # Orus la primera (la del canal), despues las masculinas y luego las femeninas
    fichas.sort(key=lambda f: (f["nombre"] != "Orus", f["genero"] != "masculina",
                               f["nombre"]))
    return fichas


RUTA_CACHE_VOCES_ELEVEN = os.path.join(RUTA_CACHE, "voces_elevenlabs.json")


def _huella_clave_eleven():
    """Huella (nunca la clave) de la clave de ElevenLabs en uso, o ""."""
    try:
        clave = motor_eleven.cargar_api_key()
    except BaseException:                                       # noqa: BLE001
        return ""
    return hashlib.sha256(str(clave).encode("utf-8")).hexdigest()[:12]


def listar_voces_elevenlabs(idioma=None, refrescar=False):
    """Las voces de la cuenta de ElevenLabs (las propias primero), cacheadas.

    Con la misma regla que el catalogo de Cartesia: el cache sabe con que clave
    se bajo, y sin clave (o en simulado) no se pide nada y se devuelve lo que
    haya, o la voz por defecto. Las voces de ElevenLabs hablan todos los idiomas
    del modelo, asi que no se filtran por idioma.
    """
    cache = None
    if os.path.exists(RUTA_CACHE_VOCES_ELEVEN):
        try:
            with open(RUTA_CACHE_VOCES_ELEVEN, "r", encoding="utf-8") as fh:
                cache = json.load(fh)
        except ValueError:
            cache = None
    huella = _huella_clave_eleven()
    lista = cache.get("voces") if isinstance(cache, dict) else None
    fresca = bool(lista) and (time.time() - float(cache.get("epoch", 0))
                              < DIAS_CACHE_VOCES * 86400) and cache.get("clave") == huella
    if not refrescar and fresca:
        return copy.deepcopy(lista)
    if simulado() or not huella:
        if lista:
            return copy.deepcopy(lista)
        defecto = motor_eleven.VOCES.get(str(idioma or "en"), motor_eleven.VOCES["en"])
        return [{"id": defecto["id"], "nombre": defecto["nombre"], "descripcion": "voz de la libreria",
                 "idioma": "", "genero": "", "pais": "", "publica": True, "nativa": True,
                 "proveedor": "elevenlabs"}]
    try:
        fichas = comun.llamar_motor(motor_eleven.listar_voces)
    except Exception:                                           # noqa: BLE001
        if lista:
            return copy.deepcopy(lista)
        raise
    for ficha in fichas:
        ficha["nativa"] = True
    os.makedirs(RUTA_CACHE, exist_ok=True)
    comun.escribir_json(RUTA_CACHE_VOCES_ELEVEN, {
        "epoch": time.time(), "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "clave": huella, "voces": fichas})
    return copy.deepcopy(fichas)


def listar_voces(idioma=None, refrescar=False, solo_nativas=False):
    """Catalogo de voces de Cartesia, cacheado en disco.

    El catalogo cambia poco y son varias paginas de red: se guarda en
    cache/voces_cartesia.json y se refresca solo si caduca o si se pide.

    EL CACHE SABE CON QUE CLAVE SE BAJO (06-09-2026), y sin eso el dia que se
    cambia de cuenta de Cartesia la pantalla miente durante una semana: se puso
    la clave de la cuenta donde vive la voz clonada del canal, y el desplegable
    siguio diciendo «no hay voces clonadas en esta cuenta» porque el catalogo
    cacheado --934 voces bajadas con la clave anterior-- todavia estaba dentro
    de sus siete dias. No fallaba nada y no habia nada que mirar: por eso se
    tarda en encontrarlo.

    Se guarda la HUELLA de la clave, no la clave. Un cache de antes de esta
    fecha no la lleva, asi que se refresca una vez y ya la tiene.
    """
    cache = None
    if os.path.exists(RUTA_CACHE_VOCES):
        try:
            with open(RUTA_CACHE_VOCES, "r", encoding="utf-8") as fh:
                cache = json.load(fh)
        except ValueError:
            cache = None

    huella = _huella_clave()
    fresca = False
    if isinstance(cache, dict) and cache.get("voces"):
        edad = time.time() - float(cache.get("epoch", 0))
        fresca = edad < DIAS_CACHE_VOCES * 86400
        # Si no se puede saber que clave hay (no hay ninguna configurada), la
        # huella no opina: lo cacheado es mejor que nada.
        if huella and cache.get("clave") != huella:
            fresca = False

    if not refrescar and fresca:
        return _filtrar_idioma(copy.deepcopy(cache["voces"]), idioma, solo_nativas)

    if simulado():
        # sin red en modo simulado: lo que haya cacheado, y si no el minimo util
        fichas = cache["voces"] if isinstance(cache, dict) and cache.get("voces") \
            else copy.deepcopy(VOCES_BASE)
        return _filtrar_idioma(copy.deepcopy(fichas), idioma)

    # SIN CLAVE NO SE PIDE NADA, y se devuelve el minimo util en vez de fallar.
    # Es el estado de una instalacion recien puesta: nadie ha entrado todavia en
    # Configuracion. `voz.cargar_api_key` esta escrito como CLI y aborta con
    # SystemExit, que NO es un `Exception`, asi que se colaba por debajo del
    # try de abajo, subia entera por la ruta de la API y salia como un 500 sin
    # texto -- con la pantalla diciendo «cargando tus voces...» para siempre.
    if not _hay_clave():
        fichas = (cache["voces"] if isinstance(cache, dict) and cache.get("voces")
                  else copy.deepcopy(VOCES_BASE))
        return _filtrar_idioma(copy.deepcopy(fichas), idioma)

    try:
        fichas = _descargar_voces()
    except BaseException:                                     # noqa: BLE001
        if isinstance(cache, dict) and cache.get("voces"):
            return _filtrar_idioma(copy.deepcopy(cache["voces"]), idioma, solo_nativas)
        raise

    os.makedirs(RUTA_CACHE, exist_ok=True)
    with open(RUTA_CACHE_VOCES, "w", encoding="utf-8") as fh:
        json.dump({"epoch": time.time(), "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "clave": huella, "voces": fichas}, fh, ensure_ascii=False,
                  indent=2)
    return _filtrar_idioma(copy.deepcopy(fichas), idioma, solo_nativas)


if __name__ == "__main__":
    import argparse

    sys.path.insert(0, RAIZ_ESTUDIO)
    from nucleo.proyecto import Proyecto  # noqa: E402

    parser = argparse.ArgumentParser(description="Paso 4: voz")
    parser.add_argument("--proyecto", help="carpeta del proyecto")
    parser.add_argument("--preset", default=presets_voz.POR_DEFECTO)
    parser.add_argument("--voz")
    parser.add_argument("--previsualizar", type=float, default=0.0,
                        help="sintetiza solo N segundos")
    parser.add_argument("--voces", action="store_true",
                        help="lista el catalogo de voces y sale")
    parser.add_argument("--idioma", default="es")
    parser.add_argument("--refrescar", action="store_true")
    args = parser.parse_args()

    if args.voces:
        for ficha in listar_voces(args.idioma, args.refrescar):
            print(f"{ficha['id']}  {ficha['nombre']}  [{ficha['idioma']}] "
                  f"{ficha['descripcion'][:60]}")
        raise SystemExit(0)

    if not args.proyecto:
        raise SystemExit("hace falta --proyecto (o --voces)")
    proyecto_cli = Proyecto(args.proyecto)
    params_cli = {"preset": args.preset, "idioma": args.idioma}
    if args.voz:
        params_cli["voz_id"] = args.voz

    def traza(valor, mensaje=""):
        if mensaje:
            print(f"[{valor * 100:5.1f}%] {mensaje}")
        return valor

    if args.previsualizar:
        print(previsualizar(proyecto_cli, params_cli, args.previsualizar))
    else:
        print(json.dumps({k: v for k, v in ejecutar(proyecto_cli, params_cli, traza).items()
                          if k != "palabras"}, ensure_ascii=False, indent=2))


# ------------------------------------------------------ regrabar una seccion
#
# El problema que resuelve: el TTS tartamudea en un sitio y hoy la unica salida
# es volver a pagar y regrabar el video entero. Con las secciones se regraba
# solo la que falla.
#
# Y va con MICROCAMBIO, que es la mitad que no da ninguna API: repetir la misma
# entrada palabra por palabra puede volver a tartamudear, porque es la misma
# entrada. Reescribir una frase diciendo lo mismo cambia la entrada y esquiva el
# fallo. Lo que se reescriba viaja al guion, que es gratis: es la misma
# informacion.

#: POR QUE se reescribe, que no siempre es lo mismo y cambia lo que hay que
#: hacer. Con la toma ya grabada, el motivo es que la voz se traba y hay que
#: DECIR LO MISMO con otras palabras; sin toma, el motivo es que quien escribe
#: quiere otra cosa, y ahi cambiar lo que dice es justo lo que se pide.
MOTIVO_TTS = ("El motivo no es que esten mal: es que el sintetizador de voz ha "
              "fallado al locutarlos (se traba, repite una silaba) y repetir la "
              "misma entrada palabra por palabra volveria a fallar. Lo que hace "
              "falta es que el texto DIGA LO MISMO con otras palabras en la zona "
              "del problema. Mismo significado, longitud parecida.")
MOTIVO_EDITOR = ("El motivo es que quien escribe el video quiere otra cosa en "
                 "esa parte. Haz lo que pide y nada mas: los demas bloques del "
                 "guion no los ves, asi que lo que este bloque dice tiene que "
                 "seguir encajando con lo que va antes y despues.")

INSTRUCCION_MICROCAMBIO = """\
Eres el guionista de un documental narrado en off.

Reescribe estos bloques del guion CAMBIANDO LO MINIMO. {motivo}

BLOQUES (id y texto):
{bloques}

LO QUE PIDE QUIEN REVISA:
{peticion}

Reglas:
- Devuelve los MISMOS ids, todos, en el mismo orden.
- Cambia solo lo que haga falta para lo que se pide, y solo donde haga falta.
  El resto, palabra por palabra igual.
- Mismos hechos, mismas cifras y mismos nombres propios.
- Conserva las anotaciones de voz (<break/>, <speed/>, <spell>) y su sitio.
- TODO CON LETRAS: ni un digito ni un simbolo.
- Responde SOLO con este JSON: {{"bloques": [{{"id": "B00X", "texto": "..."}}]}}
"""


def _pcm_de(wav):
    return wav[44:]


def reescribir_bloques(bloques, ids, peticion, ajuste=None, cwd=None,
                       motivo=None):
    """Reescribe esos bloques por prompt, cambiando lo minimo. -> [bloques]

    ESTA APARTE DE `regrabar_seccion` porque se pide en DOS momentos distintos y
    solo uno de ellos tiene audio: antes de grabar, «cambia esta frase» es
    reescribir y ya; con la toma hecha, es reescribir Y regrabar. Un solo
    cuerpo y dos llamadas -- con dos cuerpos, el prompt de uno se queda viejo.
    """
    dentro = set(ids or [])
    objetivo = [b for b in bloques if b.get("id") in dentro]
    if not objetivo or not str(peticion or "").strip():
        return list(bloques)
    instruccion = INSTRUCCION_MICROCAMBIO.format(
        motivo=motivo or MOTIVO_TTS,
        bloques="\n".join(f'{b["id"]}: {b["texto"]}' for b in objetivo),
        peticion=" ".join(str(peticion).split()))
    por_fase = cli_claude.por_defecto_de("revision_audio")
    ajuste = ajuste or {"modelo": por_fase["modelo"],
                        "esfuerzo": por_fase["esfuerzo"]}
    texto, _sobre = cli_claude.ejecutar(
        instruccion, modelo=ajuste["modelo"], esfuerzo=ajuste["esfuerzo"],
        cwd=cwd, base_tiempo_s=300,
        sistema="Responde solo con el objeto JSON pedido.",
        herramientas_vetadas=("Bash", "Read", "Write", "Edit", "Glob",
                              "Grep", "WebFetch", "WebSearch", "Task"),
        extra=["--no-session-persistence"], para="el microcambio")
    crudo = comun.extraer_json(texto, "el microcambio")
    por_id = {str(b.get("id") or "").upper():
              marcas_tts.sanear(" ".join(str(b.get("texto") or "").split()))
              for b in (crudo.get("bloques") or []) if b.get("texto")}
    salida = []
    for bloque in bloques:
        ficha = dict(bloque)
        if ficha.get("id") in dentro and por_id.get(ficha.get("id")):
            ficha["texto"] = por_id[ficha["id"]]
        salida.append(ficha)
    return salida


def regrabar_seccion(bloques, seccion_id, cfg, meta, destino, peticion="",
                     ajuste=None, avisar=None, cwd=None):
    """Vuelve a grabar UNA seccion y la cose en la toma que ya existe.

    Devuelve (salidas, bloques_nuevos). Las salidas tienen la misma forma de
    siempre -- lista de palabras con tiempos absolutos -- porque lo que hay
    aguas abajo no puede enterarse de que esto existe.
    """
    avisa = _avisador(avisar)
    secciones = {s["id"]: s for s in (meta.get("secciones") or [])}
    if seccion_id not in secciones:
        raise RuntimeError(f"la toma no tiene ninguna seccion {seccion_id!r}")
    seccion = secciones[seccion_id]
    dentro = set(seccion["bloques"])
    if seccion.get("t_in") is None:
        raise RuntimeError(f"la seccion {seccion_id} no tiene audio que sustituir")

    nuevos = [dict(b) for b in bloques]
    # 1. el microcambio, si se ha pedido
    if str(peticion or "").strip():
        avisa(0.05, "reescribiendo el texto de la seccion")
        nuevos = reescribir_bloques(nuevos, dentro, peticion, ajuste=ajuste,
                                    cwd=cwd, motivo=MOTIVO_TTS)

    # 2. se graba SOLO esa seccion, en su propio contexto
    trozo = " ".join(b["texto"].strip() for b in nuevos if b["id"] in dentro)
    avisa(0.35, f"grabando {seccion_id} ({len(trozo.split())} palabras)")

    def progreso(fraccion, mensaje=""):
        return avisa(0.35 + 0.4 * max(0.0, min(1.0, fraccion)), mensaje)

    if simulado():
        wav_nuevo, dur_nueva, marcas_nuevas = _toma_simulada(trozo, cfg)
    elif cfg.get("proveedor") == "google":
        # con el estilo de SU tramo: una intro regrabada sigue sonando a intro.
        # El motor la deja al mismo nivel que el resto de la toma (todas las
        # piezas se igualan al mismo objetivo), asi que la costura no salta.
        tramo = seccion.get("tramo") or "cuerpo"
        familia = motor_google.familia_de(cfg["modelo"])
        con_estilo = [{"texto": " ".join(marcas_tts.para_google(b["texto"], familia)
                                         for b in nuevos if b["id"] in dentro),
                       "seccion": seccion_id,
                       "estilo": estilo_de(tramo, cfg) if familia == "gemini" else None}]
        hablado = " ".join(marcas_tts.limpiar(b["texto"]) for b in nuevos
                           if b["id"] in dentro)
        wav_nuevo, dur_nueva, alineado, info_regrabado = _google_verificado(
            con_estilo, [hablado], cfg, progreso)
        marcas_nuevas = alineado["palabras"]
        for mala in info_regrabado.get("sin_arreglar") or []:
            avisa(0.78, f"AVISO: {mala['seccion']} sigue saliendo mal "
                        f"({'lee su instrucción de estilo' if mala['motivo'] == 'fuga' else 'se salta texto'}): "
                        f"vuelve a regrabarla")
    elif cfg.get("proveedor") == "elevenlabs":
        # sola, pero con el texto de alrededor como contexto: la entonacion
        # de la costura sabe que viene de algo y que sigue algo
        orden = [b["id"] for b in nuevos]
        primero, ultimo = orden.index(seccion["bloques"][0]), orden.index(seccion["bloques"][-1])
        antes = " ".join(marcas_tts.limpiar(b["texto"]) for b in nuevos[:primero])
        despues = " ".join(marcas_tts.limpiar(b["texto"]) for b in nuevos[ultimo + 1:])
        wav_nuevo, dur_nueva, marcas_nuevas, _info = _toma_elevenlabs(
            [{"id": seccion_id, "bloques": [b["id"] for b in nuevos if b["id"] in dentro]}],
            nuevos, cfg, progreso, antes=antes, despues=despues)
    else:
        wav_nuevo, dur_nueva, marcas_nuevas = _toma_por_contexto(
            [trozo], cfg, progreso)

    # 3. se cose: lo de antes + lo nuevo + lo de despues
    avisa(0.8, "cosiendo la toma")
    origen = os.path.join(destino, meta.get("archivo") or NOMBRE_PISTA)
    with open(origen, "rb") as fh:
        pcm = _pcm_de(fh.read())
    bytes_seg = SR * 2
    corte_ini = int(seccion["t_in"] * bytes_seg) & ~1
    corte_fin = int(seccion["t_out"] * bytes_seg) & ~1
    pcm_nuevo = _pcm_de(wav_nuevo)
    wav = motor.wav_desde_pcm(pcm[:corte_ini] + pcm_nuevo + pcm[corte_fin:])

    # 4. las marcas: las de antes, las nuevas desplazadas, y las de despues
    #    corridas por la diferencia de duracion. Tiempos ABSOLUTOS, como siempre.
    desde, hasta = seccion["t_in"], seccion["t_out"]
    delta = (len(pcm_nuevo) / bytes_seg) - (hasta - desde)
    palabras = []
    for p in meta.get("palabras") or []:
        if p["e"] <= desde + 1e-6:
            palabras.append(dict(p))
    for p in marcas_nuevas:
        palabras.append({"w": p["w"], "s": round(p["s"] + desde, 3),
                         "e": round(p["e"] + desde, 3)})
    for p in meta.get("palabras") or []:
        if p["s"] >= hasta - 1e-6:
            palabras.append({"w": p["w"], "s": round(p["s"] + delta, 3),
                             "e": round(p["e"] + delta, 3)})

    escenas, reparto = _reparto(nuevos, palabras)
    fichas, ordenadas = [], []
    for bloque in nuevos:
        tramo = reparto.get(bloque["id"], [])
        ordenadas.extend(tramo)
        fichas.append({"id": bloque["id"], "texto": bloque["texto"],
                       "narracion": marcas_tts.limpiar(bloque["texto"]),
                       "t_in": round(tramo[0]["s"], 3) if tramo else None,
                       "t_out": round(tramo[-1]["e"], 3) if tramo else None,
                       "palabras": len(tramo)})

    nombre = meta.get("archivo") or NOMBRE_PISTA
    with open(os.path.join(destino, nombre), "wb") as fh:
        fh.write(wav)
    salidas = dict(meta)
    salidas.update({
        "pista": os.path.join(destino, nombre), "archivo": nombre,
        "duracion": round(len(_pcm_de(wav)) / bytes_seg, 3),
        "palabras": ordenadas, "bloques": fichas,
        "secciones": _fichas_de_seccion(
            [{"id": s["id"], "bloques": s["bloques"], "tramo": s.get("tramo")}
             for s in (meta.get("secciones") or [])], fichas),
        "resumen": f"{seccion_id} regrabada ({len(trozo.split())} palabras)",
    })
    salidas.pop("transcript", None)
    meta_nueva = dict(salidas)
    meta_nueva["transcript"] = " ".join(b["texto"].strip() for b in nuevos)
    comun.escribir_json(os.path.join(destino, NOMBRE_META), meta_nueva)
    avisa(1.0, salidas["resumen"])
    return salidas, nuevos
