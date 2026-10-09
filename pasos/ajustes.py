"""Los AJUSTES del estudio: lo poco que se decide UNA vez y vale para todos.

QUE ES UN AJUSTE Y QUE NO
-------------------------
Un ajuste es una decision que no pertenece a ningun video en concreto: la
calidad con la que se piden las imagenes es la misma manana que hoy, y
preguntarla en cada video seria preguntar quince veces lo mismo. Lo que SI
pertenece a un video --su estilo, su voz, su duracion-- vive en sus params o en
su preset, y no aqui.

Y SOLO ES UN VALOR POR DEFECTO. Cambiar un ajuste NO toca ningun video ya
hecho, a proposito: `calidad` entra en la firma de cada imagen
(`p6_assets`, `firma = medios.huella({... "calidad": p["calidad"] ...})`), asi
que un defecto que se leyera al EJECUTAR dejaria obsoletas de golpe las
imagenes de todos los proyectos que nunca lo fijaron -- y regenerarlas cuesta
dinero de verdad. Por eso el valor se escribe en los params del proyecto CUANDO
SE CREA (ver `crear_proyecto` en app.py) y ahi se queda: lo viejo sigue con lo
que tenia y el modo editor lo puede cambiar video a video como siempre.

LO QUE CUESTA UNA IMAGEN, DE VERDAD
-----------------------------------
La tabla de precios de OpenAI habla de la imagen DEVUELTA, y con eso solo se
entiende la mitad de la factura: a cada plano se le adjuntan las referencias de
estilo, las de reparto y las de continuidad, y ESO se paga como tokens de
entrada. Medido sobre las 1.439 imagenes del historico (`coste_global.jsonl`),
la entrada es la mediana de abajo y sale casi tan cara como la imagen en `low`.

La consecuencia es la que importa al elegir: subir de `low` a `medium` parece
multiplicar por 6,8 y multiplica por 1,7, porque la parte que se multiplica es
la pequena. Sin esta cuenta delante, la pantalla asustaria con un numero falso.
"""
import os

try:
    from nucleo import coste as COSTE
except ImportError:                                   # corriendo desde pasos/
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from nucleo import coste as COSTE
from nucleo.proyecto import escribir_json, leer_json

RAIZ_ESTUDIO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Redirigible igual que el resto: en el servidor cada cuenta tiene el suyo.
RUTA = os.environ.get("ESTUDIO_AJUSTES") or os.path.join(RAIZ_ESTUDIO,
                                                         "ajustes.json")

#: Las calidades que acepta el motor de imagen, de mas barata a mas cara.
CALIDADES = ("low", "medium", "high")

#: El tamano con el que se generan los planos, y por tanto con el que hay que
#: mirar la tabla de precios. Es el de `p6_assets` para 16:9.
TAMANO = "1536x1024"

#: TOKENS DE ENTRADA POR IMAGEN. La MEDIANA de las 1.439 imagenes del historico
#: --p10 3.287, p90 6.741--, no una estimacion. Se usa la mediana y no el p90
#: porque esto es lo que cuesta una imagen tipica; el p90 ensenaria de mas casi
#: siempre. Si cambia cuantas referencias se adjuntan, este numero se vuelve a
#: medir: sale de `coste_global.jsonl`, campo tokens.entrada de las operaciones
#: 'imagen'.
TOKENS_ENTRADA_POR_IMAGEN = 5114

#: Quien dibuja las imagenes (fork). Gemini en Vertex lo paga el credito de
#: Google Cloud; OpenAI queda como opcion. Como la calidad, se escribe en el
#: vídeo al CREARLO (param `motor_imagen` de assets) y no se lee al generar.
PROVEEDORES_IMAGEN = ("vertex_gemini", "openai")

#: De donde sale el audio (fork, Fase 3), en orden. «propia» es la carpeta
#: `banco/audio/propio/` (Biblioteca de audio de YouTube, Uppbeat, Pixabay...);
#: «en linea» es Jamendo para la musica y Freesound para los efectos.
PRIORIDADES_AUDIO = ("propia_primero", "solo_propia", "en_linea_primero")

POR_DEFECTO = {
    "calidad_imagen": "low",
    "proveedor_imagen": "vertex_gemini",
    # MUSICA Y EFECTOS QUE SE PUEDEN MONETIZAR. Con esto puesto, de Jamendo y
    # Freesound solo entran CC0 y CC BY: nada no comercial (NC), sin derivadas
    # (ND) ni «compartir igual» (SA). Quitarlo es bajo tu responsabilidad.
    "audio_licencias_seguras": True,
    "audio_prioridad": "propia_primero",
    # Uppbeat: con suscripcion, sus pistas no piden credito en la descripcion
    # (el canal va en su lista blanca); con el plan gratis, cada pista pide su
    # codigo de credito.
    "uppbeat_suscripcion": False,
    "uppbeat_canal": "",
    # Si ya se ha pasado por la guia de inicio (las tarjetas que piden las
    # claves al entrar por primera vez). Vive aqui y no en el navegador
    # porque es de la instalacion, no de la pantalla: desde el movil no hay
    # que volver a verla.
    "onboarding_visto": False,
}


def leer():
    """Los ajustes guardados, con los que faltan puestos por defecto."""
    guardado = leer_json(RUTA, {}) or {}
    salida = dict(POR_DEFECTO)
    for clave, valor in guardado.items():
        if clave in POR_DEFECTO:
            salida[clave] = valor
    if salida.get("calidad_imagen") not in CALIDADES:
        salida["calidad_imagen"] = POR_DEFECTO["calidad_imagen"]
    if salida.get("proveedor_imagen") not in PROVEEDORES_IMAGEN:
        salida["proveedor_imagen"] = POR_DEFECTO["proveedor_imagen"]
    if salida.get("audio_prioridad") not in PRIORIDADES_AUDIO:
        salida["audio_prioridad"] = POR_DEFECTO["audio_prioridad"]
    for clave in ("onboarding_visto", "audio_licencias_seguras",
                  "uppbeat_suscripcion"):
        salida[clave] = bool(salida.get(clave))
    salida["uppbeat_canal"] = str(salida.get("uppbeat_canal") or "")[:200]
    return salida


def guardar(cambios):
    """Mezcla `cambios` sobre lo que hay y devuelve los ajustes resultantes.

    Se valida aqui y no en la pantalla: un ajuste con un valor que el motor no
    entiende no da error al guardarlo, lo da meses despues al generar.
    """
    if not isinstance(cambios, dict):
        raise ValueError("los ajustes se cambian con un objeto")
    actual = leer()
    for clave, valor in cambios.items():
        if clave not in POR_DEFECTO:
            raise ValueError(f"ajuste desconocido: {clave!r}")
        if clave == "calidad_imagen" and valor not in CALIDADES:
            raise ValueError(
                f"calidad {valor!r}: solo {', '.join(CALIDADES)}")
        if clave in ("onboarding_visto", "audio_licencias_seguras",
                     "uppbeat_suscripcion") and not isinstance(valor, bool):
            raise ValueError(f"{clave} es verdadero o falso")
        if clave == "audio_prioridad" and valor not in PRIORIDADES_AUDIO:
            raise ValueError(f"prioridad de audio {valor!r}: solo "
                             f"{', '.join(PRIORIDADES_AUDIO)}")
        if clave == "uppbeat_canal":
            if not isinstance(valor, str) or len(valor) > 200:
                raise ValueError("uppbeat_canal es un texto corto (el enlace "
                                 "o el nombre del canal)")
            valor = valor.strip()
        if clave == "proveedor_imagen" and valor not in PROVEEDORES_IMAGEN:
            raise ValueError(f"proveedor de imagen {valor!r}: solo "
                             f"{', '.join(PROVEEDORES_IMAGEN)}")
        actual[clave] = valor
    escribir_json(RUTA, actual)
    return actual


def calidad_imagen():
    """La calidad con la que arranca un proyecto nuevo."""
    return leer()["calidad_imagen"]


def proveedor_imagen():
    """Quien dibuja las imagenes de un proyecto nuevo (y el moodboard)."""
    return leer()["proveedor_imagen"]


#: Gemini en Vertex (fork): la imagen devuelta segun calidad (Nano Banana 2 a
#: 1K / 2K / 4K, 1.120 / 1.680 / 2.520 tokens a 60 $ el millon) y lo adjuntado.
#: Las referencias son ESTIMADAS hasta medir una tanda real: ~10 imagenes de
#: 1.120 tokens mas el prompt, a 0,50 $ el millon de tokens de entrada.
USD_IMAGEN_GEMINI = {"low": 0.0672, "medium": 0.1008, "high": 0.1512}
TOKENS_ENTRADA_GEMINI = 12000
USD_TOKEN_ENTRADA_GEMINI = 0.50 / 1e6


def cobro_imagen_gemini():
    """«flex» o «estandar»: como se pagan las imagenes de Vertex. -> str

    Lo MISMO que lee el motor (`imagen_gemini.cobro`): la variable de entorno
    manda y si no el bloque google de las claves. Por defecto, Flex.
    """
    valor = os.environ.get("ESTUDIO_COBRO_IMAGEN")
    if not valor:
        try:
            from . import claves                                # noqa: PLC0415
        except ImportError:
            import claves                                       # noqa: PLC0415
        try:
            valor = (claves.leer().get("google") or {}).get("cobro_imagen")
        except Exception:                                       # noqa: BLE001
            valor = ""
    valor = str(valor or "flex").strip().lower()
    return valor if valor in ("flex", "estandar") else "flex"


def coste_por_imagen(calidad, tamano=TAMANO, proveedor=None):
    """Lo que cuesta UNA imagen a esa calidad: la devuelta MAS lo adjuntado.

    Devuelve las dos mitades por separado porque el reparto es justo lo que hay
    que ensenar: sin el, la comparacion entre calidades es de la parte pequena.
    Con el proveedor de Configuracion si no se dice otro.
    """
    if (proveedor or proveedor_imagen()) == "vertex_gemini":
        # CON FLEX, LA MITAD DE LAS DOS PARTES (fork, 08-10-2026): Flex PayGo
        # descuenta el 50 % de la entrada y de la salida
        cobro = cobro_imagen_gemini()
        factor = 0.5 if cobro == "flex" else 1.0
        devuelta = USD_IMAGEN_GEMINI.get(calidad, USD_IMAGEN_GEMINI["low"]) * factor
        entrada = TOKENS_ENTRADA_GEMINI * USD_TOKEN_ENTRADA_GEMINI * factor
        return {"calidad": calidad, "usd_imagen": round(devuelta, 4),
                "usd_referencias": round(entrada, 4),
                "usd_total": round(devuelta + entrada, 4),
                "tokens_entrada": TOKENS_ENTRADA_GEMINI, "proveedor": "vertex_gemini",
                "cobro": cobro}
    tokens = COSTE.tarifa_tokens() or {}
    por_token_entrada = float(tokens.get("entrada_imagen") or 0.0)
    entrada = TOKENS_ENTRADA_POR_IMAGEN * por_token_entrada
    devuelta = float(COSTE.tarifa_imagen(tamano, calidad) or 0.0)
    return {
        "calidad": calidad,
        "usd_imagen": round(devuelta, 4),
        "usd_referencias": round(entrada, 4),
        "usd_total": round(devuelta + entrada, 4),
        "tokens_entrada": TOKENS_ENTRADA_POR_IMAGEN,
    }


def tabla_de_costes(tamano=TAMANO, proveedor=None):
    """Las tres calidades con su coste real y su multiplicador contra la base.

    `veces_total` es lo que de verdad se multiplica la factura y `veces_imagen`
    lo que parece si solo se mira la tabla de OpenAI. Se sirven LOS DOS: la
    diferencia entre 1,7 y 6,8 es la razon de ser de esta pantalla.
    """
    filas = [coste_por_imagen(c, tamano, proveedor) for c in CALIDADES]
    base = filas[0]
    for fila in filas:
        fila["veces_total"] = (round(fila["usd_total"] / base["usd_total"], 1)
                               if base["usd_total"] else 0.0)
        fila["veces_imagen"] = (round(fila["usd_imagen"] / base["usd_imagen"], 1)
                                if base["usd_imagen"] else 0.0)
    return filas
