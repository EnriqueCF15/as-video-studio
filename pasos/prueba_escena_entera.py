r"""
Prueba del director «escena entera» con la biblioteca de escenas (30-10-2026).

Enrique aprobo S003 y S006 del video 2 dibujados con la escena entera que escribe el
director, UNA referencia de la biblioteca (misma clase de plano y misma luz) y la orden del
metodo a mano. Lo que se protege aqui:

  1. SOLO EL ESTILO DE HISTORIA   sin `orden: corta` y `biblioteca`, nada cambia
  2. LA LINEA                     la ficha va y vuelve por la `direccion` sin perder nada,
                                  y el limite de palabras no la recorta
  3. LA ORDEN                     la misma que dibujo la imagen aprobada (piloto r3)
  4. LAS REFERENCIAS              la de la biblioteca primero, luego las hojas, nada mas
  5. EL ENCARGO                   lleva ejemplos, referencias, reparto y los planos pedidos
  6. LA COMPROBACION              pasa lo de Opus y caza lo que fallo Sonnet (30-10)

NINGUNA PAGA NADA: solo arma textos.

    python pasos\prueba_escena_entera.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import direccion  # noqa: E402
import escena_entera  # noqa: E402
import medios  # noqa: E402
import p6_assets  # noqa: E402
from PIL import Image  # noqa: E402

FALLOS = []
COMPROBACIONES = 0


def igual(visto, esperado, que):
    global COMPROBACIONES
    COMPROBACIONES += 1
    if visto != esperado:
        FALLOS.append(f"{que}: esperaba {esperado!r}, vino {visto!r}")


def cierto(condicion, que):
    igual(bool(condicion), True, que)


# una biblioteca de prueba en un banco temporal
tmp = tempfile.mkdtemp(prefix="prueba_escena_entera_")
medios.BANCO = tmp
os.makedirs(os.path.join(tmp, "bib"))
Image.new("RGB", (32, 18)).save(os.path.join(tmp, "bib", "S135.png"))
FICHA_S003 = {
    "tipo": "protagonista_hoy", "estilo": "S135", "hojas": ["muneco", "ninos_puerta"],
    "reglas": "only the protagonist from the first character sheet has a thin spiral curl, "
              "nobody else; nobody floats.",
    "letras": "'TRICK OR TREAT!' inside one single speech bubble coming from the three "
              "children on the porch",
    "escena": "Halloween night, present day, the protagonist's open front door seen from "
              "outside on the porch, the drawing filling the whole frame. The protagonist "
              "from the first character sheet, in his own grey V-neck jumper and black "
              "trousers, no costume, stands inside his open doorway on the strip of hall "
              "carpet, holding the round carmine bowl of wrapped sweets against his chest, "
              "eyebrows raised and mouth open in surprise. Outside on the porch boards stand "
              "exactly three children from the second character sheet, nobody else: the white "
              "bedsheet ghost, the pirate with the eyepatch and cardboard cutlass, and the "
              "green dinosaur, side by side facing him, each thrusting one open brown paper bag "
              "toward the bowl. A carved pumpkin glows beside the step. Warm porch light, deep "
              "blue night sky, everything clearly visible.",
    "por_que": "Lo literal de la frase."}
import json  # noqa: E402
with open(os.path.join(tmp, "bib", "biblioteca.json"), "w", encoding="utf-8") as fh:
    json.dump({"ejemplos": {"S086": {"contexto": "    SE DICE AQUI [S086]: frase",
                                     "ficha": FICHA_S003}},
               "referencias": {"S135": {"descripcion": "[protagonista_hoy / noche] porche",
                                        "imagen": "S135.png"}}}, fh)
HISTORIA = {"orden": "corta", "biblioteca": "bib"}

# 1. SOLO EL ESTILO DE HISTORIA
cierto(escena_entera.activa(HISTORIA), "con orden corta y biblioteca, activa")
cierto(not escena_entera.activa({"biblioteca": "bib"}), "sin orden corta, no")
cierto(not escena_entera.activa({"orden": "corta"}), "sin biblioteca, no")
cierto(not escena_entera.activa({"orden": "corta", "biblioteca": "no_existe"}),
       "con una biblioteca que no esta en disco, no")
cierto(not escena_entera.activa(None), "sin estilo, no")

# 2. LA LINEA
linea = escena_entera.linea(FICHA_S003)
igual(escena_entera.ficha(linea), FICHA_S003, "la ficha va y vuelve por la direccion")
igual(escena_entera.ficha({"direccion": linea}), FICHA_S003, "tambien desde la escena")
igual(escena_entera.ficha("a man at a door (TONO: neutro)"), None,
      "una direccion de las de siempre no es una ficha")
igual(direccion.limpiar_linea(linea), (linea, ""), "el limite de palabras no la recorta")

# 3. LA ORDEN: la que dibujo S003_r3.png (aprobado por Enrique el 30-10)
orden = escena_entera.orden(FICHA_S003)
r3 = r"E:\AutomatizacionYT\revision\video2\piloto_estudio3\S003_r3.txt"
if os.path.exists(r3):
    igual(orden, open(r3, encoding="utf-8").read().split("\nrefs: ")[0],
          "la orden es la del piloto aprobado")
cierto("TRICK OR TREAT!" in orden and FICHA_S003["escena"] in orden, "lleva letras y escena")
cierto("Correction requested" in escena_entera.orden(FICHA_S003, "make it bigger"),
       "la nota del repaso entra al final")
escena = {"id": "S003", "direccion": linea}
igual(p6_assets._prompt_completo(escena, [], {"orden": "corta", "biblioteca": "bib"}), orden,
      "p6_assets dibuja con la orden del metodo a mano")
cierto(p6_assets._prompt_completo(escena, [], {"orden": "corta"}) != orden,
       "sin biblioteca, la orden corta de siempre")

# 4. LAS REFERENCIAS
reparto = os.path.join(tmp, "reparto")
os.makedirs(reparto)
for h in ("muneco", "ninos_puerta", "otro"):
    Image.new("RGB", (8, 8)).save(os.path.join(reparto, f"{h}.png"))
refs = escena_entera.referencias(FICHA_S003, HISTORIA, reparto, lambda r: r)
igual([(r["papel"], os.path.basename(r["ruta"])) for r in refs],
      [("estilo", "S135.png"), ("reparto", "muneco.png"), ("reparto", "ninos_puerta.png")],
      "una referencia de la biblioteca y las hojas pedidas, en orden")
dirs = {"reparto": reparto, "escenas": os.path.join(tmp, "escenas")}
os.makedirs(dirs["escenas"])
PLAN = {"escenas": [{"id": "S003", "direccion": linea, "personajes": ["otro"]}]}
refs = p6_assets._referencias_escena(PLAN["escenas"][0], PLAN, dirs,
                                     {"estilo": HISTORIA}, os.path.join(tmp, "cache"), {})
igual([r["papel"] for r in refs], ["estilo", "reparto", "reparto"],
      "p6_assets manda solo esas (ni el reparto del catalogo ni continuidad)")

# 5. EL ENCARGO
ESCENAS = [{"id": "S002", "narracion": "Three strangers in masks."},
           {"id": "S003", "narracion": "They hold out their bags and say three words.",
            "set": "porche", "personajes": ["muneco"]},
           {"id": "S004", "narracion": "And you pay them."}]
CATALOGO = {"sets": {"porche": {"descripcion": "a porch with a round carmine bowl"}},
            "reparto": {"muneco": {"nombre": "tu", "papel": "protagonista",
                                   "descripcion": "round head"},
                        "ninos_puerta": {"nombre": "ninos", "papel": "ninos"}}}
texto = escena_entera.encargo(ESCENAS, ["S003"], CATALOGO, "Titulo",
                              escena_entera.cargar(HISTORIA))
for trozo in ("[S003] sitio del estudio (pista): porche", "SE DICE AQUI [S003]",
              "[EJEMPLO de otro video, S086]", "S135: [protagonista_hoy / noche]",
              "[muneco] tu (protagonista)", "Titulo"):
    cierto(trozo in texto, f"el encargo lleva «{trozo}»")
cierto("{" not in texto.split("DEVUELVE ESTE JSON")[0].replace('{\n', ''), "sin huecos sin rellenar")

# 6. LA COMPROBACION
bib = escena_entera.cargar(HISTORIA)
igual(escena_entera.comprobar({"S003": FICHA_S003}, ESCENAS, CATALOGO, bib), [],
      "la escena de Opus aprobada pasa")
sin_bol = dict(FICHA_S003, escena=FICHA_S003["escena"].replace("bowl", "door"))
cierto(any("bowl" in a for a in escena_entera.comprobar({"S003": sin_bol}, ESCENAS,
                                                         CATALOGO, bib)),
       "Sonnet sin el bol del sitio: aviso")
flota = dict(FICHA_S003, escena=FICHA_S003["escena"] + " The bowl sits on the dividing line.")
cierto(any("flota" in a for a in escena_entera.comprobar({"S003": flota}, ESCENAS,
                                                         CATALOGO, bib)),
       "Sonnet con el bol en la raya (S132): aviso")
niebla = dict(FICHA_S003, escena=FICHA_S003["escena"] + " Wisps of mist curl around.")
igual(escena_entera.comprobar({"S003": niebla}, ESCENAS, CATALOGO, bib), [],
      "la niebla que se riza no es un rizo (S046)")
cuadros = dict(FICHA_S003, letras="'TODAY' in a box in the left half, 'BACK THEN' in the "
               "right half, and 'TRICK OR TREAT!' in one speech bubble from the child")
igual([a for a in escena_entera.comprobar(
    {"S003": cuadros}, [dict(ESCENAS[1], narracion="Today, a kid says three words. Back "
                             "then, you had to put on a show.")], CATALOGO, bib)
       if "bocadillo" in a], [], "los cuadros de epoca no son el bocadillo (S041)")
cierto(any("fuera de la biblioteca" in a for a in escena_entera.leer_respuesta(
    {"planos": {"S003": dict(FICHA_S003, estilo="S999")}}, {"S003"}, bib, {})[1]),
    "una referencia que no esta en la biblioteca: aviso")

# 7. EL REPASO DE SENTIDO (Opus lee y marca; aqui sin llamarlo)
texto = escena_entera.encargo_repaso({"S003": FICHA_S003}, ESCENAS, "Titulo")
cierto("SE DICE AQUI [S003]" in texto and FICHA_S003["escena"] in texto and "{" not in
       texto.split("DEVUELVE ESTE JSON")[0], "el repaso lee frase, vecinas y escena")
igual(escena_entera.leer_repaso({"marcados": {"s003": " al reves ", "S999": "x", "S004": ""}},
                                {"S003", "S004"}), {"S003": "al reves"},
      "solo los marcados de los planos pedidos y con motivo")
igual(escena_entera.leer_repaso("roto", {"S003"}), {}, "una respuesta rota no marca nada")

shutil.rmtree(tmp, ignore_errors=True)
print()
if FALLOS:
    print(f"FALLAN {len(FALLOS)} de {COMPROBACIONES} comprobaciones:")
    for f in FALLOS:
        print("   ", f)
    sys.exit(1)
print(f"ESCENA ENTERA OK: {COMPROBACIONES} comprobaciones pasan")
