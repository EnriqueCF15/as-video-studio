"""Prueba del FILTRO DE IMAGENES: el revisor, el bloqueo sin Claude y la nota vigente.

Lo que se comprueba es lo que pidio el canal el 12-10-2026: que no se rehaga
ninguna imagen sin el filtro (corrector + revisor) funcionando, que el revisor
rehaga UNA sola vez lo que suspende, y que al generador le llegue solo la nota
que manda ahora. Ninguna comprobacion llama al CLI ni paga una imagen: el CLI y
el motor se sustituyen por dobles.

    python pasos/prueba_revisor.py
"""
import json
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import cli_claude                                           # noqa: E402
import p6_assets                                            # noqa: E402
import revisor                                              # noqa: E402
import salud_cli                                            # noqa: E402

_ok = 0
_fallos = []


def ok(condicion, mensaje):
    global _ok
    if condicion:
        _ok += 1
        return True
    _fallos.append(mensaje)
    print(f"      FALLO: {mensaje}")
    return False


def igual(obtenido, esperado, mensaje):
    return ok(obtenido == esperado,
              f"{mensaje} (esperaba {esperado!r}, llego {obtenido!r})")


def seccion(titulo):
    print(f"\n  {titulo}")


class Parche:
    """Sustituye atributos de un modulo y los devuelve a su sitio al salir."""

    def __init__(self, modulo, **cambios):
        self.modulo, self.cambios, self.antes = modulo, cambios, {}

    def __enter__(self):
        for nombre, valor in self.cambios.items():
            self.antes[nombre] = getattr(self.modulo, nombre)
            setattr(self.modulo, nombre, valor)
        return self

    def __exit__(self, *_):
        for nombre, valor in self.antes.items():
            setattr(self.modulo, nombre, valor)


def _png_temporal():
    fd, ruta = tempfile.mkstemp(suffix=".png")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
    return ruta


def prueba_veredicto():
    seccion("el veredicto del revisor se valida")
    bueno = revisor.leer_veredicto({"ok": True, "fallos": ["sobra"], "arreglo": "x"})
    ok(bueno.get("ok") is True and bueno["fallos"] == [] and bueno["arreglo"] == "",
       "un aprobado no arrastra fallos ni arreglo")
    malo = revisor.leer_veredicto({"ok": False, "fallos": ["8 casillas, pide 10"],
                                   "arreglo": "ten boxes"})
    ok(malo.get("ok") is False and malo["fallos"] == ["8 casillas, pide 10"]
       and malo["arreglo"] == "ten boxes", "un suspenso trae sus fallos y su arreglo")
    sin_arreglo = revisor.leer_veredicto({"ok": False, "fallos": ["nariz"]})
    ok("nariz" in sin_arreglo.get("arreglo", ""),
       "sin arreglo escrito, el arreglo sale de los fallos")
    ok("error" in revisor.leer_veredicto({"ok": False}),
       "un suspenso mudo es un error, no un suspenso")
    ok("error" in revisor.leer_veredicto({"ok": "si"}),
       "un ok que no es booleano es un error")
    ok("error" in revisor.leer_veredicto(["ok"]), "una lista no es un veredicto")
    muchos = revisor.leer_veredicto({"ok": False, "fallos": [str(i) for i in range(9)]})
    igual(len(muchos["fallos"]), revisor.MAX_FALLOS, "los fallos se recortan")


def prueba_revisar():
    seccion("revisar llama al CLI con la imagen y nunca lanza")
    escena = {"id": "S042", "narracion": "two out of ten", "personajes": ["tu"]}
    ok("error" in revisor.revisar("no_existe.png", "nota", escena),
       "sin imagen no se llama a nadie")
    png = _png_temporal()
    vistas = {}

    def ejecutar_bien(instruccion, **kw):
        vistas["instruccion"] = instruccion
        vistas["permitidas"] = kw.get("herramientas_permitidas")
        return json.dumps({"ok": False, "fallos": ["8 casillas"],
                           "arreglo": "ten boxes"}), {}

    try:
        with Parche(cli_claude, ejecutar=ejecutar_bien):
            veredicto = revisor.revisar(png, "TEN boxes", escena, "  no nose")
        ok(veredicto.get("ok") is False and veredicto["fallos"] == ["8 casillas"],
           "el suspenso del CLI llega tal cual")
        ok(os.path.abspath(png) in vistas["instruccion"],
           "la instruccion nombra la imagen a abrir")
        ok("TEN boxes" in vistas["instruccion"] and "no nose" in vistas["instruccion"],
           "la instruccion lleva la nota y la guia del estilo")
        igual(tuple(vistas["permitidas"] or ()), ("Read",), "solo puede leer")

        def ejecutar_mal(instruccion, **kw):
            raise RuntimeError("Failed to authenticate: OAuth session expired")

        with Parche(cli_claude, ejecutar=ejecutar_mal):
            veredicto = revisor.revisar(png, "nota", escena)
        ok("OAuth" in veredicto.get("error", ""), "un fallo del CLI vuelve como error")
    finally:
        os.remove(png)


def prueba_claude_contesta():
    seccion("antes de dibujar se pregunta si alguna cuenta contesta")
    cuentas = [{"etiqueta": "la mia"}, {"etiqueta": "la otra"}]
    respuestas = {"la mia": {"estado": "sesion", "mensaje": "OAuth expired"},
                  "la otra": {"estado": "ok"}}
    with Parche(salud_cli, simulado=lambda: False,
                probar=lambda c, para="": respuestas[c["etiqueta"]]), \
            Parche(cli_claude, cuentas=lambda: cuentas):
        contesta, _ = revisor.claude_contesta()
        ok(contesta, "basta con que conteste una cuenta de la cadena")
        respuestas["la otra"] = {"estado": "cupo", "mensaje": "hasta el lunes"}
        contesta, motivo = revisor.claude_contesta()
        ok(not contesta and "OAuth expired" in motivo and "hasta el lunes" in motivo,
           "si no contesta ninguna se dice por que, cuenta a cuenta")


def prueba_nota_vigente():
    seccion("al generador solo le llega la ultima nota")
    historial = [{"id": "F001", "texto": "three rows of TEN"},
                 {"id": "F002", "texto": "fix the slab"},
                 {"id": "F005", "texto": "two rows of five"}]
    igual(p6_assets._nota_vigente(historial), "two rows of five",
          "de un historial manda la ultima")
    igual(p6_assets._nota_vigente("  a mano  "), "a mano", "una cadena vale tal cual")
    igual(p6_assets._nota_vigente(None), "", "sin nota no hay nada")
    ok("three rows" in p6_assets._texto_feedback(historial),
       "_texto_feedback sigue uniendo el historial (lo usan las hojas)")


def prueba_modos():
    seccion("los interruptores del filtro")
    igual(p6_assets._revisor_modo({}), "notas", "el revisor mira las notas por defecto")
    igual(p6_assets._revisor_modo({"revisor": "raro"}), "notas", "un valor raro es el defecto")
    ok(p6_assets._toca_revisar({}, "nota", True), "con nota y pedida se revisa")
    ok(not p6_assets._toca_revisar({}, "", True), "sin nota no se revisa por defecto")
    ok(not p6_assets._toca_revisar({}, "nota", False), "lo que no se ha pedido no se revisa")
    ok(p6_assets._toca_revisar({"revisor": "todo"}, "", True), "«todo» revisa toda imagen")
    ok(not p6_assets._toca_revisar({"revisor": "no"}, "nota", True), "«no» lo apaga")
    revisa = cli_claude.por_defecto_de("revisor_imagen")
    igual((revisa["modelo"], revisa["esfuerzo"]), ("sonnet", "medium"),
          "el revisor va en sonnet: no sube al suelo del canal (12-10)")
    corrige = cli_claude.por_defecto_de("corrector_imagen")
    igual((corrige["modelo"], corrige["esfuerzo"]), ("opus", "high"),
          "el corrector va en opus alto, no xhigh")
    dirige = cli_claude.por_defecto_de("direccion")
    igual((dirige["modelo"], dirige["esfuerzo"]),
          (cli_claude.MODELO_POR_DEFECTO, cli_claude.ESFUERZO_POR_DEFECTO),
          "el resto de fases sigue con el suelo del canal")
    ok(p6_assets._corrector_obligatorio({}), "por defecto el corrector es obligatorio")
    ok(not p6_assets._corrector_obligatorio({"corrector": "si_puede"}),
       "«si_puede» vuelve a lo de antes")


def prueba_bloqueo():
    seccion("sin Claude no se dibuja nada")
    escenas = [{"id": "S001"}, {"id": "S002"}]
    con_nota = {"unidades": {"escena:S002": {"feedback": [{"texto": "arregla"}]}}}
    ok(not p6_assets._necesita_claude(escenas, con_nota, ["escena:S001"], False),
       "si lo pedido no lleva nota, no hace falta Claude")
    ok(p6_assets._necesita_claude(escenas, con_nota, ["escena:S002"], False),
       "si lo pedido lleva nota, hace falta Claude")
    ok(not p6_assets._necesita_claude(
        escenas, dict(con_nota, corrector="no", revisor="no"), ["escena:S002"], False),
       "con corrector y revisor apagados no hace falta")
    ok(p6_assets._necesita_claude(escenas, {"revisor": "todo"}, None, True),
       "con «todo» y una tanda forzada hace falta")
    llamadas = []
    with Parche(revisor, claude_contesta=lambda para="": (llamadas.append(1)
                                                          or (False, "sesion caducada"))):
        try:
            p6_assets._comprobar_claude(escenas, con_nota, ["escena:S002"], False)
            ok(False, "sin Claude tenia que pararse")
        except RuntimeError as fallo:
            ok("sesion caducada" in str(fallo) and "No se ha dibujado nada" in str(fallo),
               "se para diciendo por que y que no se ha gastado")
        p6_assets._comprobar_claude(escenas, con_nota, ["escena:S001"], False)
        igual(len(llamadas), 1, "si no hace falta Claude ni se le pregunta")


def prueba_reintento():
    seccion("el revisor rehace UNA vez lo que suspende")
    escena = {"id": "S042", "narracion": "x", "personajes": []}
    referencias = [{"papel": "estilo", "ruta": "lamina.png"},
                   {"papel": "rechazada", "ruta": "antes.png"}]
    producidas = []

    def producir(sid, prompt, refs, destino, p, rehacer=False, tamano=None):
        producidas.append({"prompt": prompt, "rehacer": rehacer, "refs": refs})
        return {"origen": "generada", "firma": "f2", "coste": 0.036}

    def con_veredictos(*veredictos):
        cola = list(veredictos)
        vistos = []

        def revisar(imagen, nota, esc, estilo, especie, **kw):
            vistos.append(kw.get("antes"))
            return cola.pop(0)
        return revisar, vistos

    meta ={"origen": "generada", "firma": "f1", "coste": 0.036,
            "corrector": {"porque": "x"}}
    suspenso = {"ok": False, "fallos": ["8 casillas"], "arreglo": "ten boxes"}
    with Parche(p6_assets, _producir_imagen=producir, guia_escrita=lambda e: ["no nose"],
                regla_de_especie=lambda e: ""):
        revisar, vistos = con_veredictos(suspenso, {"ok": True, "fallos": [], "arreglo": ""})
        with Parche(revisor, revisar=revisar):
            prompt, nueva = p6_assets._revisar_y_reintentar(
                "S042", escena, "S042.png", "PROMPT", referencias, {"estilo": {}},
                {}, None, "nota", meta, "apaisado")
        igual(len(producidas), 1, "un suspenso paga UNA imagen mas")
        ok(producidas[0]["rehacer"] and "ten boxes" in producidas[0]["prompt"]
           and producidas[0]["prompt"].startswith("PROMPT"),
           "el reintento salta la cache y lleva el arreglo al final")
        ok(prompt == producidas[0]["prompt"], "se devuelve el prompt del reintento")
        ok(nueva["revisor"]["ok"] and nueva["revisor"]["reintento"],
           "la ficha dice que se rehizo y que ahora vale")
        igual(nueva["coste"], 0.072, "el coste suma las dos imagenes")
        ok(nueva.get("corrector") == {"porque": "x"}, "la ficha del corrector se conserva")
        igual(vistos, ["antes.png", "antes.png"], "el revisor ve la imagen de antes")

        producidas.clear()
        revisar, _ = con_veredictos(suspenso, dict(suspenso, fallos=["sigue con 8"]))
        with Parche(revisor, revisar=revisar):
            _, nueva = p6_assets._revisar_y_reintentar(
                "S042", escena, "S042.png", "PROMPT", referencias, {"estilo": {}},
                {}, None, "nota", meta, "apaisado")
        igual(len(producidas), 1, "si sigue mal NO se insiste: una sola vez")
        ok(not nueva["revisor"]["ok"] and nueva["revisor"]["fallos"] == ["sigue con 8"],
           "y se queda con los fallos del ultimo intento")

        producidas.clear()
        for veredicto, que in (({"ok": True, "fallos": [], "arreglo": ""}, "un aprobado"),
                               ({"error": "OAuth"}, "un error del revisor")):
            revisar, _ = con_veredictos(veredicto)
            with Parche(revisor, revisar=revisar):
                prompt, nueva = p6_assets._revisar_y_reintentar(
                    "S042", escena, "S042.png", "PROMPT", referencias, {"estilo": {}},
                    {}, None, "nota", meta, "apaisado")
            ok(not producidas and prompt == "PROMPT" and nueva["coste"] == 0.036,
               f"{que} no paga nada mas")
        ok(nueva["revisor"]["error"] == "OAuth", "el error del revisor queda apuntado")


def prueba_resumen():
    seccion("lo que dijo el revisor sale en el resumen de la tanda")
    resultados = {
        "escena:S001": {"origen": "generada", "revisor": {"ok": True, "reintento": False}},
        "escena:S002": {"origen": "generada", "revisor": {"ok": True, "reintento": True}},
        "escena:S003": {"origen": "generada",
                        "revisor": {"ok": False, "reintento": True, "fallos": ["nariz"]}},
        "escena:S004": {"origen": "generada", "revisor": {"error": "OAuth"}},
        "escena:S005": {"origen": "conservado", "revisor": {"ok": False}},
        "escena:S006": {"origen": "generada"},
    }
    revision = p6_assets.resumen_revision(resultados)
    igual((revision["revisadas"], revision["bien"], revision["rehechas"]), (3, 2, 2),
          "cuenta solo lo generado ahora")
    igual([m["id"] for m in revision["siguen_mal"]], ["S003"], "lo que sigue mal, con su id")
    igual([m["id"] for m in revision["sin_revisar"]], ["S004"], "lo que no se pudo revisar")
    frase = p6_assets.frase_revision(revision)
    ok("2 de 3 bien" in frase and "S003" in frase and "S004" in frase,
       "la frase lo dice todo en una linea")
    igual(p6_assets.frase_revision(p6_assets.resumen_revision({})), "",
          "sin revisar nada no se dice nada")


def main():
    print("PRUEBA DEL FILTRO DE IMAGENES")
    prueba_veredicto()
    prueba_revisar()
    prueba_claude_contesta()
    prueba_nota_vigente()
    prueba_modos()
    prueba_bloqueo()
    prueba_reintento()
    prueba_resumen()

    print("\n" + "=" * 70)
    if _fallos:
        print(f"FALLAN {len(_fallos)} de {_ok + len(_fallos)} comprobaciones:")
        for fallo in _fallos:
            print(f"  - {fallo}")
        return 1
    print(f"PRUEBA REVISOR OK: {_ok} comprobaciones pasan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
