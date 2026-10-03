"""
Prueba del motor de imagen con Gemini en Vertex (motores/imagen_gemini) y de su
paso por el Estudio: modelos por calidad y por uso, tamanos exactos, coste por
tokens, reintentos, modo simulado, el selector de proveedor y el medidor.

NO SALE A LA RED NI PAGA UNA IMAGEN. Vertex se sustituye por un cliente de
mentira, y lo que pasa por p6_assets va con ESTUDIO_SIMULAR=1 (el motor
devuelve una imagen gris del tamano exacto sin llamar a nadie).

    python pasos/prueba_imagen_gemini.py
"""
import io
import os
import shutil
import sys
import tempfile
import types as tipos

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)

from PIL import Image  # noqa: E402

from nucleo import coste  # noqa: E402

import medios  # noqa: E402

FALLOS = []
HECHAS = [0]
BASE = tempfile.mkdtemp(prefix="estudio_imagen_gemini_")
os.environ["ESTUDIO_SECRETOS"] = os.path.join(BASE, "secretos")
os.environ["ESTUDIO_AJUSTES"] = os.path.join(BASE, "ajustes.json")
os.environ["ESTUDIO_ESTADISTICAS"] = os.path.join(BASE, "estadisticas.json")

gem = medios.motor("imagen_gemini/imagen.py")


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


def png(ancho, alto):
    salida = io.BytesIO()
    Image.new("RGB", (ancho, alto), (120, 90, 60)).save(salida, "PNG")
    return salida.getvalue()


def referencia(nombre="ref.png"):
    ruta = os.path.join(BASE, nombre)
    with open(ruta, "wb") as fh:
        fh.write(png(64, 64))
    return ruta


class _APIError(Exception):
    def __init__(self, code, mensaje="error"):
        super().__init__(mensaje)
        self.code = code


class _ClienteDoble:
    """Vertex de mentira: devuelve las respuestas que se le pongan, en orden."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.llamadas.append({"model": model, "contents": contents, "config": config})
        siguiente = self.respuestas.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente


def respuesta(datos, entrada=15680, salida=1120):
    parte = tipos.SimpleNamespace(inline_data=tipos.SimpleNamespace(data=datos))
    candidato = tipos.SimpleNamespace(content=tipos.SimpleNamespace(parts=[parte]),
                                      finish_reason="STOP")
    return tipos.SimpleNamespace(
        candidates=[candidato],
        usage_metadata=tipos.SimpleNamespace(prompt_token_count=entrada,
                                             candidates_token_count=salida,
                                             total_token_count=entrada + salida))


def sin_imagen():
    candidato = tipos.SimpleNamespace(content=tipos.SimpleNamespace(parts=[]),
                                      finish_reason="IMAGE_SAFETY")
    return tipos.SimpleNamespace(candidates=[candidato], usage_metadata=None,
                                 prompt_feedback=None)


def con_cliente(doble, funcion):
    """Corre `funcion` con el cliente de Vertex sustituido y sin simular."""
    real = gem._cliente
    simular = os.environ.pop("ESTUDIO_SIMULAR", None)
    gem._cliente = lambda: doble
    try:
        return funcion()
    finally:
        gem._cliente = real
        if simular is not None:
            os.environ["ESTUDIO_SIMULAR"] = simular


# ------------------------------------------------------------------ motor

def prueba_modelos():
    print("\n[1] que modelo y que resolucion para cada calidad y uso")
    igual(gem.modelo_para("low"), ("gemini-3.1-flash-image", "1K"), "low: Nano Banana 2 a 1K")
    igual(gem.modelo_para("medium"), ("gemini-3.1-flash-image", "2K"), "medium: Nano Banana 2 a 2K")
    igual(gem.modelo_para("high"), ("gemini-3.1-flash-image", "4K"), "high: Nano Banana 2 a 4K")
    igual(gem.modelo_para("low", "reparto"), ("gemini-3.1-flash-image", "2K"),
          "las hojas de reparto, con Nano Banana 2 a 2K sea cual sea la calidad "
          "(eleccion de Enrique tras la prueba real)")
    igual(gem.ASPECTOS, {"apaisado": "3:2", "cuadrado": "1:1", "vertical": "2:3"},
          "la MISMA proporcion que daba OpenAI (1536x1024)")
    usd = gem.coste_de("gemini-3.1-flash-image", "1K", 15680, 1120)
    comprobar(abs(usd - (0.0672 + 0.00784)) < 1e-6,
              f"coste de un plano con 14 referencias a 1K = salida + entrada ({usd:.4f} $)")
    comprobar(abs(gem.coste_de("gemini-3-pro-image", "2K", 0) - 0.1344) < 1e-6,
              "Pro a 2K: 1.120 tokens de salida a 120 $ el millon")


def prueba_tamanos():
    print("\n[2] el tamano exacto que espera el Estudio")
    for tamano, (ancho, alto), crudo in (("apaisado", (1536, 1024), (1264, 848)),
                                         ("vertical", (1024, 1536), (848, 1264)),
                                         ("cuadrado", (1024, 1024), (1024, 1024))):
        salida = Image.open(io.BytesIO(gem._a_png(png(*crudo), tamano)))
        igual(salida.size, (ancho, alto), f"{tamano}: {crudo} -> {ancho}x{alto}")


def prueba_generar():
    print("\n[3] generar contra un Vertex de mentira")
    ref = referencia()
    doble = _ClienteDoble([respuesta(png(1264, 848))])
    imagen, meta = con_cliente(doble, lambda: gem.generar("a cozy kitchen", [ref],
                                                          quality="low"))
    igual(Image.open(io.BytesIO(imagen)).size, (1536, 1024), "sale a 1536x1024")
    igual((meta["modelo"], meta["resolucion"], meta["proveedor"]),
          ("gemini-3.1-flash-image", "1K", "vertex_gemini"), "con su modelo y proveedor")
    igual(meta["usage"]["input_tokens"], 15680, "los tokens son los que dio la API")
    comprobar(abs(meta["coste"] - gem.coste_de("gemini-3.1-flash-image", "1K", 15680, 1120))
              < 1e-9, "y el coste sale de ellos")
    llamada = doble.llamadas[0]
    igual(llamada["model"], "gemini-3.1-flash-image", "se pide a Nano Banana 2")
    igual(llamada["contents"][-1], "a cozy kitchen",
          "las referencias van delante y el prompt al final, en su orden")
    igual(llamada["config"].image_config.aspect_ratio, "3:2", "con la proporcion 3:2")
    igual(llamada["config"].image_config.image_size, "1K", "y la resolucion pedida")

    # un 429 frena y se reintenta; la segunda sale
    gem.ESPERA_MAXIMA_S, maximo = 0.01, gem.ESPERA_MAXIMA_S
    try:
        doble = _ClienteDoble([_APIError(429, "RESOURCE_EXHAUSTED"),
                               respuesta(png(1264, 848))])
        original = gem.errors if hasattr(gem, "errors") else None
        from google.genai import errors
        clase_real = errors.APIError
        errors.APIError = _APIError
        try:
            _img, meta = con_cliente(doble, lambda: gem.generar("x", [ref]))
        finally:
            errors.APIError = clase_real
        igual(len(doble.llamadas), 2, "un 429 se espera y se reintenta")

        doble = _ClienteDoble([sin_imagen(), respuesta(png(1264, 848))])
        _img, meta = con_cliente(doble, lambda: gem.generar("x", [ref]))
        igual(len(doble.llamadas), 2, "una respuesta sin imagen se reintenta una vez")
        doble = _ClienteDoble([sin_imagen(), sin_imagen()])
        falla(lambda: con_cliente(doble, lambda: gem.generar("x", [ref])),
              "y si vuelve sin imagen, se dice", RuntimeError)
    finally:
        gem.ESPERA_MAXIMA_S = maximo
        gem._PAUSA_HASTA[0] = 0.0

    muchas = [referencia(f"r{i}.png") for i in range(15)]
    falla(lambda: con_cliente(_ClienteDoble([]), lambda: gem.generar("x", muchas)),
          "mas referencias de las que admite el modelo: no se recortan a ciegas", ValueError)
    falla(lambda: gem.generar("x", [os.path.join(BASE, "no_existe.png")]),
          "una referencia que no existe se dice antes de pagar", ValueError)


def prueba_simulado():
    print("\n[4] modo simulado: no sale a Vertex")
    os.environ["ESTUDIO_SIMULAR"] = "1"
    real = gem._cliente
    gem._cliente = lambda: (_ for _ in ()).throw(AssertionError("ha salido a Vertex"))
    try:
        imagen, meta = gem.generar("x", [referencia()], tamano="vertical", uso="reparto")
    finally:
        gem._cliente = real
    igual(Image.open(io.BytesIO(imagen)).size, (1024, 1536), "imagen del tamano exacto")
    igual((meta["coste"], meta["simulado"], meta["modelo"]),
          (0.0, True, "gemini-3.1-flash-image"), "sin coste, marcada y con el modelo que tocaria")


# ---------------------------------------------------------------- estudio

def prueba_estudio():
    print("\n[5] el Estudio: proveedor por Configuracion, p6 y el medidor")
    import ajustes
    import p6_assets
    igual(ajustes.proveedor_imagen(), "vertex_gemini", "por defecto, Gemini en Vertex")
    falla(lambda: ajustes.guardar({"proveedor_imagen": "aistudio"}),
          "un proveedor desconocido no se guarda", ValueError)
    igual(medios.motor_imagen("openai"), medios.motor("imagen_openai/imagen.py"),
          "openai sigue siendo su motor")
    igual(medios.motor_imagen(), gem, "y sin decir nada, el de Configuracion")
    igual(p6_assets.PARAMS_POR_DEFECTO["motor_imagen"], "openai",
          "el defecto del paso NO cambia: los proyectos viejos siguen como estaban")

    os.environ["ESTUDIO_SIMULAR"] = "1"
    banco = os.path.join(BASE, "banco")
    p = p6_assets._con_defectos({"motor_imagen": "vertex_gemini", "banco_imagenes": banco,
                                 "imagenes_previas": [], "calidad": "low"})
    ref = referencia()
    destino = os.path.join(BASE, "tmp", "escenas", "S001.png")
    salida = p6_assets._producir_imagen("S001", "a kitchen", [ref], destino, p)
    igual(salida["origen"], "generada", "p6 genera con Gemini")
    comprobar(os.path.exists(destino), "y deja el plano en su sitio")
    comprobar(any(n.endswith("_vertex_gemini.png") for n in os.listdir(banco)),
              "la cache de Gemini lleva su sufijo: no se mezcla con la de OpenAI")
    otra = p6_assets._producir_imagen("S001", "a kitchen", [ref], destino, p)
    igual(otra["origen"], "cache", "la segunda vez sale de la cache, sin pagar")

    total = coste.agregar([{"proveedor": "gemini_imagen", "usd": 0.075,
                            "cantidad": {"imagenes": 1}},
                           {"proveedor": "openai", "usd": 0.05, "cantidad": {"imagenes": 1}}])
    comprobar(abs(total["total_usd"] - 0.125) < 1e-9,
              "las imagenes de Gemini SI suman al total (se pagan en dolares)")
    igual(total["proveedores"]["gemini_imagen"]["etiqueta"], "Gemini (Vertex)", "con su nombre")
    comprobar(gem in coste.modulos_de_imagen(), "el medidor encuentra el motor de Gemini")


def main():
    try:
        prueba_modelos()
        prueba_tamanos()
        prueba_generar()
        prueba_simulado()
        prueba_estudio()
    finally:
        os.environ.pop("ESTUDIO_SIMULAR", None)
        shutil.rmtree(BASE, ignore_errors=True)
    print()
    if FALLOS:
        print(f"IMAGEN GEMINI: {len(FALLOS)} de {HECHAS[0]} comprobaciones fallan")
        for texto in FALLOS:
            print(f"  - {texto}")
        return 1
    print(f"IMAGEN GEMINI OK: {HECHAS[0]} comprobaciones pasan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
