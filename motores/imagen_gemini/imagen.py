"""
Motor de imagen: Gemini («Nano Banana») en Vertex AI, con la MISMA interfaz que
motores/imagen_openai -- generar, generar_lote, gasto, normalizar --, para que
los pasos no noten el cambio.

LO PAGA EL PROYECTO DE GOOGLE CLOUD, POR VERTEX, NUNCA POR AI STUDIO. La prueba
gratuita de Google Cloud no cubre la Gemini API de AI Studio: una API key de
alli se cobraria a la tarjeta. Por eso aqui no hay API key en ningun sitio: el
cliente es `genai.Client(vertexai=True, ...)` con las credenciales de gcloud
(ADC) o con el JSON de una cuenta de servicio, igual que motores/voz_google.

Modelos (comprobados contra Vertex el 02-10-2026 con models.get, ubicacion
`global`)
------------------------------------------------------------------------------
    gemini-3.1-flash-image   «Nano Banana 2»   planos, por defecto
    gemini-3-pro-image       «Nano Banana Pro» hojas de reparto (fijan la
                                               consistencia de todo el video)
    gemini-2.5-flash-image   «Nano Banana»     el mas barato; admite pocas
                                               referencias, no sirve para planos

La calidad del Estudio (low | medium | high) se traduce asi (CALIDADES):
    low     Nano Banana 2 a 1K   ~0,07 $ + referencias
    medium  Nano Banana 2 a 2K   ~0,10 $ + referencias
    high    Nano Banana Pro a 2K ~0,13 $ + referencias
y las hojas de reparto van SIEMPRE con el modelo de reparto (Pro), sea cual
sea la calidad: son pocas y de ellas depende que el personaje sea el mismo en
todo el video.

Tamano: el resto del Estudio espera exactamente lo que daba OpenAI (1536x1024
apaisado, 1024x1536 vertical, 1024x1024 cuadrado; ver p6_assets.TAMANO). Se
pide a Gemini la MISMA proporcion (3:2, 2:3, 1:1) y se ajusta al tamano exacto.

Las referencias NO se recortan aqui: el prompt de cada plano las nombra por su
posicion, y quitar una a ciegas haria que «la imagen 3» fuera otra. Si un
modelo admite menos de las que llegan, se dice (ValueError) en vez de adivinar.

Contrato: no importa nada de la aplicacion. Lee `secretos/claves.json` (bloque
"google": proyecto, ubicacion, cuenta_servicio, modelo_planos, modelo_reparto).
"""
import importlib.util
import io
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

PROVEEDOR = "vertex_gemini"
ALCANCE = "https://www.googleapis.com/auth/cloud-platform"

#: Modelos: cuantas imagenes de entrada admiten, que resoluciones y que cuestan
#: (USD por millon de tokens; la imagen de salida a 1K son 1.120 tokens en los
#: 3.x y 1.290 en el 2.5). Precios de la pagina de Vertex del 02-10-2026: los de
#: ENTRADA estan por confirmar con la factura de la primera tanda.
MODELOS = {
    "gemini-3.1-flash-image": {"nombre": "Nano Banana 2", "max_refs": 14,
                               "resoluciones": ("1K", "2K", "4K"),
                               "usd_entrada_m": 0.50, "usd_salida_m": 60.0},
    "gemini-3-pro-image": {"nombre": "Nano Banana Pro", "max_refs": 14,
                           "resoluciones": ("1K", "2K", "4K"),
                           "usd_entrada_m": 2.00, "usd_salida_m": 120.0},
    "gemini-2.5-flash-image": {"nombre": "Nano Banana", "max_refs": 3,
                               "resoluciones": (),
                               "usd_entrada_m": 0.30, "usd_salida_m": 30.0},
}
#: Tokens de una imagen de salida, por resolucion (lo que cobra Vertex).
TOKENS_SALIDA = {"1K": 1120, "2K": 1680, "4K": 2520}
TOKENS_SALIDA_PRO = {"1K": 1120, "2K": 1120, "4K": 2000}
TOKENS_SALIDA_25 = 1290

MODELO_PLANOS = "gemini-3.1-flash-image"
MODELO_REPARTO = "gemini-3-pro-image"
CALIDADES = {"low": (MODELO_PLANOS, "1K"), "medium": (MODELO_PLANOS, "2K"),
             "high": (MODELO_REPARTO, "2K")}

TAMANOS = {"apaisado": "1536x1024", "cuadrado": "1024x1024", "vertical": "1024x1536"}
ASPECTOS = {"apaisado": "3:2", "cuadrado": "1:1", "vertical": "2:3"}

#: Precio de referencia por imagen (solo la salida, a 1K), como el PRECIO de
#: imagen_openai: es lo que se ensena antes de generar. Lo que se anota sale de
#: los tokens que devuelve la API.
PRECIO = {"low": 0.067, "medium": 0.101, "high": 0.134}

#: Llamadas a la vez. Vertex reparte una cuota dinamica compartida; la prueba
#: gratuita no deja pedir mas, asi que se va despacio y se espera ante un 429.
CONCURRENCIA = 4
REINTENTOS = 6
ESPERA_MAXIMA_S = 120.0

CARPETA_SECRETOS = os.environ.get("ESTUDIO_SECRETOS") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "secretos")
RUTA_CLAVES = os.path.join(CARPETA_SECRETOS, "claves.json")

_gasto = {"usd": 0.0, "llamadas": 0}
_GASTO_LOCK = threading.Lock()
_CLIENTES = {}
_CLIENTES_LOCK = threading.Lock()
#: Freno comun: cuando una llamada recibe un 429, nadie sale hasta que pase.
_PAUSA_HASTA = [0.0]
_PAUSA_LOCK = threading.Lock()


class SinSaldo(RuntimeError):
    """El proyecto no puede pagar mas (credito agotado o facturacion parada)."""


class SinCredenciales(RuntimeError):
    """No hay forma de hablar con Vertex en esta maquina."""


# ----------------------------------------------------------- configuracion

def ficha_google():
    """El bloque "google" del almacen de claves, o {}. Nunca lanza."""
    try:
        with open(RUTA_CLAVES, "r", encoding="utf-8-sig") as fh:
            datos = json.load(fh)
    except (OSError, ValueError):
        return {}
    ficha = datos.get("google") if isinstance(datos, dict) else None
    return ficha if isinstance(ficha, dict) else {}


def modelo_para(quality="low", uso="plano"):
    """(modelo, resolucion) para esa calidad y ese uso (plano | reparto)."""
    ficha = ficha_google()
    modelo, resolucion = CALIDADES.get(str(quality or "low"), CALIDADES["low"])
    if uso == "reparto":
        modelo = str(ficha.get("modelo_reparto") or MODELO_REPARTO)
        # Pro cobra lo mismo a 1K que a 2K (1.120 tokens de salida, medido el
        # 02-10-2026): la hoja de la que depende todo el video, a 2K
        resolucion = "2K"
    elif ficha.get("modelo_planos") and quality != "high":
        modelo = str(ficha["modelo_planos"])
    if modelo not in MODELOS:
        raise ValueError(f"modelo de imagen de Vertex desconocido: {modelo!r}. "
                         f"Validos: {', '.join(MODELOS)}")
    return modelo, resolucion


def _cliente():
    """El cliente de Vertex (uno por proyecto y ubicacion). Nunca AI Studio."""
    try:
        from google import genai
    except ImportError as fallo:
        raise SinCredenciales("falta la libreria google-genai "
                              "(pip install -r requirements.txt)") from fallo
    ficha = ficha_google()
    ubicacion = str(ficha.get("ubicacion") or "global")
    ruta_json = str(ficha.get("cuenta_servicio") or "").strip()
    proyecto = (str(ficha.get("proyecto") or "").strip()
                or os.environ.get("ESTUDIO_GCP_PROYECTO")
                or os.environ.get("GOOGLE_CLOUD_PROJECT") or "")
    clave = (proyecto, ubicacion, ruta_json)
    with _CLIENTES_LOCK:
        if clave in _CLIENTES:
            return _CLIENTES[clave]
        credenciales = None
        try:
            import google.auth
            if ruta_json:
                from google.oauth2 import service_account
                credenciales = service_account.Credentials.from_service_account_file(
                    ruta_json, scopes=[ALCANCE])
                proyecto = proyecto or credenciales.project_id
            else:
                credenciales, por_defecto = google.auth.default(scopes=[ALCANCE])
                proyecto = (proyecto or getattr(credenciales, "quota_project_id", None)
                            or por_defecto)
        except Exception as fallo:                              # noqa: BLE001
            raise SinCredenciales(
                "no hay credenciales de Google Cloud: ejecuta `gcloud auth "
                "application-default login` (o apunta el JSON de una cuenta de "
                f"servicio en Configuracion). Detalle: {fallo}") from fallo
        if not proyecto:
            raise SinCredenciales("no se sabe que proyecto de Google Cloud usar: "
                                  "ponlo en Configuracion")
        cliente = genai.Client(vertexai=True, project=proyecto, location=ubicacion,
                               credentials=credenciales)
        _CLIENTES[clave] = cliente
        return cliente


# ---------------------------------------------------------------- imagenes

def _motor_hermano(carpeta, fichero):
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        carpeta, fichero)
    especificacion = importlib.util.spec_from_file_location(f"_{carpeta}_hermano", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


_OPENAI = []


def normalizar(ruta, cache_dir, lado_max=1024):
    """PNG RGBA a tamano razonable, con escritura atomica: la de imagen_openai.

    Es la misma operacion para los dos proveedores (preparar una referencia en
    local, sin red) y tiene detras un fallo de carreras ya resuelto: se usa la
    suya en vez de copiarla.
    """
    if not _OPENAI:
        _OPENAI.append(_motor_hermano("imagen_openai", "imagen.py"))
    return _OPENAI[0].normalizar(ruta, cache_dir, lado_max)


def _mime(ruta):
    extension = os.path.splitext(ruta)[1].lower()
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp"}.get(extension, "image/png")


def _a_png(datos, tamano):
    """Lo que devuelve Gemini, ajustado al tamano EXACTO que espera el Estudio."""
    ancho, alto = (int(x) for x in TAMANOS[tamano].split("x"))
    img = Image.open(io.BytesIO(datos)).convert("RGB")
    if img.size != (ancho, alto):
        # misma proporcion pedida: el ajuste es de escala, no recorta nada
        # salvo el pixel de redondeo
        escala = max(ancho / img.width, alto / img.height)
        img = img.resize((max(ancho, round(img.width * escala)),
                          max(alto, round(img.height * escala))), Image.LANCZOS)
        izquierda, arriba = (img.width - ancho) // 2, (img.height - alto) // 2
        img = img.crop((izquierda, arriba, izquierda + ancho, arriba + alto))
    salida = io.BytesIO()
    img.save(salida, "PNG")
    return salida.getvalue()


def coste_de(modelo, resolucion, tokens_entrada, tokens_salida=None):
    """USD de una imagen: tokens de entrada (prompt + referencias) y la salida."""
    ficha = MODELOS[modelo]
    if tokens_salida is None:
        if modelo == "gemini-2.5-flash-image":
            tokens_salida = TOKENS_SALIDA_25
        elif modelo == "gemini-3-pro-image":
            tokens_salida = TOKENS_SALIDA_PRO.get(resolucion, 1120)
        else:
            tokens_salida = TOKENS_SALIDA.get(resolucion, 1120)
    return (int(tokens_entrada or 0) * ficha["usd_entrada_m"]
            + int(tokens_salida or 0) * ficha["usd_salida_m"]) / 1e6


def _esperar_pausa():
    while True:
        with _PAUSA_LOCK:
            falta = _PAUSA_HASTA[0] - time.monotonic()
        if falta <= 0:
            return
        time.sleep(min(falta, 5.0))


def _pausar(segundos):
    with _PAUSA_LOCK:
        _PAUSA_HASTA[0] = max(_PAUSA_HASTA[0], time.monotonic() + segundos)


def simulado():
    """ESTUDIO_SIMULAR=1: no se sale a Vertex. Es lo que usan las pruebas."""
    valor = str(os.environ.get("ESTUDIO_SIMULAR", "")).strip().lower()
    return valor not in ("", "0", "false", "no")


def _simulada(prompt, referencias, quality, tamano, uso):
    """Una imagen gris del tamano exacto, sin red y sin coste."""
    ancho, alto = (int(x) for x in TAMANOS[tamano].split("x"))
    tono = 96 + (sum(map(ord, str(prompt))) % 96)
    salida = io.BytesIO()
    Image.new("RGB", (ancho, alto), (tono, tono, tono)).save(salida, "PNG")
    modelo, resolucion = CALIDADES.get(str(quality or "low"), CALIDADES["low"])
    if uso == "reparto":
        modelo = MODELO_REPARTO
    return salida.getvalue(), {"segundos": 0.0, "quality": quality,
                               "refs": len(referencias), "coste": 0.0,
                               "modelo": modelo, "resolucion": resolucion,
                               "tamano": TAMANOS[tamano], "proveedor": PROVEEDOR,
                               "uso": uso, "simulado": True,
                               "usage": {"input_tokens": 0, "output_tokens": 0,
                                         "total_tokens": 0}}


def _codigo(fallo):
    for atributo in ("code", "status_code"):
        valor = getattr(fallo, atributo, None)
        if isinstance(valor, int):
            return valor
    return None


def generar(prompt, referencias, *, quality="low", tamano="apaisado", api_key=None,
            reintentos=REINTENTOS, uso="plano"):
    """Una imagen. -> (png, meta), como imagen_openai.generar.

    `api_key` se acepta para tener la misma firma y NO se usa: aqui no hay API
    keys (ver la cabecera). `uso` = plano | reparto decide el modelo.
    """
    referencias = list(referencias or [])
    if simulado():
        return _simulada(prompt, referencias, quality, tamano, uso)
    from google.genai import errors, types

    faltan = [r for r in referencias if not os.path.exists(r)]
    if faltan:
        raise ValueError("estas imagenes de referencia no existen: "
                         + ", ".join(str(f) for f in faltan[:5]))
    if tamano not in TAMANOS:
        raise ValueError(f"tamano desconocido: {tamano!r}")
    modelo, resolucion = modelo_para(quality, uso)
    ficha = MODELOS[modelo]
    if len(referencias) > ficha["max_refs"]:
        raise ValueError(
            f"{ficha['nombre']} admite {ficha['max_refs']} imagenes de referencia y "
            f"este plano lleva {len(referencias)}. No se recortan aqui porque el "
            f"prompt las nombra por su posicion: usa otro modelo o menos referencias.")

    partes = []
    for ruta in referencias:
        with open(ruta, "rb") as fh:
            partes.append(types.Part.from_bytes(data=fh.read(), mime_type=_mime(ruta)))
    partes.append(prompt)
    configuracion_imagen = {"aspect_ratio": ASPECTOS[tamano]}
    if ficha["resoluciones"]:
        configuracion_imagen["image_size"] = resolucion
    configuracion = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(**configuracion_imagen),
        # sin herramientas: es una imagen, no una conversacion (y el SDK avisa
        # en cada llamada si esto se queda puesto)
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))

    ultimo = None
    for intento in range(reintentos + 1):
        _esperar_pausa()
        t0 = time.time()
        try:
            respuesta = _cliente().models.generate_content(
                model=modelo, contents=partes, config=configuracion)
        except errors.APIError as fallo:
            codigo = _codigo(fallo)
            ultimo = f"{codigo}: {str(fallo)[:300]}"
            texto = str(fallo).lower()
            if codigo == 403 and ("billing" in texto or "factur" in texto):
                raise SinSaldo(
                    "Google Cloud no deja cobrar en este proyecto (credito agotado o "
                    "facturacion parada). NO actives la cuenta de pago sin revisarlo: "
                    f"mira Facturacion en la consola. {ultimo}") from fallo
            if codigo in (429, 500, 502, 503, 504) and intento < reintentos:
                espera = min(ESPERA_MAXIMA_S, 2.0 ** (intento + 2) + random.uniform(0, 2))
                if codigo == 429:
                    _pausar(espera)       # el freno es de todas las llamadas
                print(f"[imagen-gemini] {codigo}; se espera {espera:.0f} s y se reintenta",
                      flush=True)
                time.sleep(espera if codigo != 429 else 0)
                continue
            raise RuntimeError(f"Vertex ({ficha['nombre']}) rechaza la imagen: {ultimo}") from fallo
        segundos = time.time() - t0

        datos = None
        for candidato in respuesta.candidates or []:
            for parte in ((candidato.content.parts if candidato.content else None) or []):
                if getattr(parte, "inline_data", None) and parte.inline_data.data:
                    datos = parte.inline_data.data
                    break
            if datos:
                break
        if not datos:
            motivo = ""
            if respuesta.candidates:
                motivo = str(getattr(respuesta.candidates[0], "finish_reason", "") or "")
            bloqueo = getattr(getattr(respuesta, "prompt_feedback", None), "block_reason", None)
            ultimo = f"no ha devuelto imagen ({motivo or bloqueo or 'sin motivo'})"
            if intento < min(reintentos, 1):
                continue                  # una vez: a veces sale a la segunda
            raise RuntimeError(f"Vertex ({ficha['nombre']}) {ultimo}")

        uso_tokens = respuesta.usage_metadata
        entrada = int(getattr(uso_tokens, "prompt_token_count", 0) or 0)
        salida = int(getattr(uso_tokens, "candidates_token_count", 0) or 0)
        usd = coste_de(modelo, resolucion, entrada, salida or None)
        png = _a_png(datos, tamano)
        with _GASTO_LOCK:
            _gasto["usd"] += usd
            _gasto["llamadas"] += 1
        return png, {"segundos": round(segundos, 1), "quality": quality,
                     "refs": len(referencias), "coste": round(usd, 6),
                     "modelo": modelo, "resolucion": resolucion,
                     "tamano": TAMANOS[tamano], "proveedor": PROVEEDOR, "uso": uso,
                     "usage": {"input_tokens": entrada, "output_tokens": salida,
                               "total_tokens": int(getattr(uso_tokens,
                                                           "total_token_count", 0) or 0)}}
    raise RuntimeError(f"Vertex no responde tras {reintentos} intentos: {ultimo}")


def generar_lote(trabajos, *, concurrencia=CONCURRENCIA):
    """Como imagen_openai.generar_lote: un fallo no tumba el lote."""
    def uno(trabajo):
        try:
            png, meta = generar(trabajo["prompt"], trabajo["referencias"],
                                quality=trabajo.get("quality", "low"),
                                tamano=trabajo.get("tamano", "apaisado"),
                                uso=trabajo.get("uso", "plano"))
        except Exception as exc:                                # noqa: BLE001
            return {"id": trabajo.get("id"), "error": str(exc)}
        os.makedirs(os.path.dirname(trabajo["destino"]), exist_ok=True)
        with open(trabajo["destino"], "wb") as fh:
            fh.write(png)
        return {"id": trabajo.get("id"), "destino": trabajo["destino"], **meta}

    with ThreadPoolExecutor(max_workers=max(1, int(concurrencia))) as pool:
        return list(pool.map(uno, trabajos))


def gasto():
    with _GASTO_LOCK:
        return dict(_gasto, usd=round(_gasto["usd"], 4))
