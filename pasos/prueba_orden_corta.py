r"""
Prueba de los planos «como el piloto» (fork, 28-10-2026).

El 27-10 el repaso del video 2 encontro 23 planos malos en los tres primeros
minutos, y la causa estaba en el estudio: la carta de plano se sorteaba sin
mirar la frase, la orden media unos 19.000 caracteres con la escena al final y
la guia pedia papel vacio. Cuatro planos rehechos con una orden corta salieron
bien. Lo que se protege aqui:

  1. LA ORDEN CORTA     solo si el Estilo la pide; escena delante, regla LLENO,
                        sin la guia entera, sin notas internas en castellano,
                        sin etiquetas del director, letras solo entrecomilladas
  2. PANTALLA DIVIDIDA  si la frase la pide, ninguna orden la prohibe
  3. LA CARTA DIRIGIDA  «(PLANO: medio)» en la linea del director manda sobre
                        el sorteo si el Estilo la deja, y no llega al generador
  4. EL DIRECTOR        recibe la lista de cartas y la cartela que va encima, y
                        recortar una linea larga no le quita el tono ni el plano
  5. EL PRESET          las cartas y la orden viajan con el Estilo

NINGUNA PAGA NADA: solo arma textos.

    python pasos\prueba_orden_corta.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import direccion  # noqa: E402
import p6_assets  # noqa: E402
import presets_canal  # noqa: E402

FALLOS = []
COMPROBACIONES = 0


def igual(visto, esperado, que):
    global COMPROBACIONES
    COMPROBACIONES += 1
    if visto != esperado:
        FALLOS.append(f"{que}: esperaba {esperado!r}, vino {visto!r}")


def cierto(condicion, que):
    global COMPROBACIONES
    COMPROBACIONES += 1
    if not condicion:
        FALLOS.append(que)


def titulo(texto):
    print(f"\n--- {texto}")


HISTORIA = ["conjunto", "medio", "retrato", "detalle", "perfil"]
GUIA = {"guia": "LA GUIA LARGA " * 200,
        "personajes": "Only the PROTAGONIST wears one thin spiral curl.",
        "esencial": "Every person is the same simple doodle figure."}
CORTA = {"orden": "corta", "cartas": HISTORIA, "guia": GUIA}
REFS = ([{"papel": "estilo", "ruta": "x"}] * 5
        + [{"papel": "reparto", "nombre": "muneco", "ruta": "y"}])
ESCENA = {
    "id": "S007", "personajes": ["muneco"],
    "narracion": "This year, they're expected to spend thirteen and a half "
                 "billion dollars on Halloween.",
    "luz": "early evening, warm orange light",
    "prompt": ("A suburban street., with the character 'muneco', neutral faces. "
               "A huge heap of Halloween shopping fills the frame. (TONO: neutro). "
               "This shot accompanies the narration line: \"This year, they're "
               "expected to spend thirteen and a half billion dollars on "
               "Halloween.\". SHOT TYPE, and this is not optional: a medium "
               "shot at eye level.")}


def corta(escena=None, estilo=None, **kw):
    return p6_assets._prompt_completo(escena or ESCENA, REFS, estilo or CORTA,
                                      idioma="en", **kw)


titulo("1 · la orden corta")
texto = corta()
cierto(len(texto) < 2500, f"la orden corta es corta ({len(texto)} caracteres)")
cierto(texto.startswith("Draw one new illustration"), "empieza por el encargo")
cierto("reference images 1 to 5" in texto, "cita las referencias de estilo")
escena_en = texto.find("Scene:")
cierto(0 < escena_en < texto.find(p6_assets.LLENO),
       "la escena va antes que las reglas")
cierto(escena_en < texto.find("Every person is the same"),
       "la escena va antes que lo esencial del estilo")
cierto(p6_assets.LLENO in texto, "lleva la regla LLENO")
cierto("never as a dark" in texto and "never greyed out" in texto,
       "LLENO prohibe siluetas y secundarios en gris")
cierto("LA GUIA LARGA" not in texto, "no mete la guia entera")
cierto("Every person is the same simple doodle figure." in texto,
       "mete lo esencial del Estilo")
for etiqueta in ("(TONO", "(PLANO", "SHOT TYPE", "not optional"):
    cierto(etiqueta not in texto, f"sin «{etiqueta}» en la orden corta")
cierto("Framing: a medium shot at eye level" in texto, "el encuadre va como Framing")
cierto(".," not in texto and ".." not in texto, "sin puntuacion doble")
for nota in ("Todo prompt", "aqui no lo leia", "La sonrisa", "regla hoja-reparto"):
    cierto(nota not in texto, f"sin notas internas en castellano («{nota}»)")
cierto("scene description below" not in texto,
       "la hoja no dice «below»: la escena va arriba")
cierto("No text, letters or numbers anywhere" in texto,
       "sin comillas propias no hay letras (la frase narrada no cuenta)")
con_letras = dict(ESCENA, prompt=ESCENA["prompt"]
                  + ' Huge letters read "$13.5 BILLION".')
cierto("The only lettering allowed" in corta(con_letras),
       "con comillas se permiten solo esas letras")
cierto("in English" in corta(con_letras), "en el idioma del video")
nota = corta(feedback="the bags float")
cierto(nota.endswith("No watermarks.") and "the bags float" in nota[-400:],
       "la nota del revisor va al final")
sin_esencial = corta(estilo={"orden": "corta", "guia": dict(GUIA, esencial="")})
cierto("Only the PROTAGONIST wears" in sin_esencial,
       "sin 'esencial', cae a la linea de personajes")
largo = p6_assets._prompt_completo(ESCENA, REFS, {"guia": GUIA}, idioma="en")
cierto(largo.startswith("Draw a single illustration")
       and "LA GUIA LARGA" in largo,
       "un Estilo sin 'orden' sigue con la orden de siempre")
igual(p6_assets.orden_corta({"orden": "larga"}), False, "solo «corta» la activa")

titulo("2 · pantalla dividida")
partida = dict(ESCENA, narracion="Split screen. On the left, you, at your door.")
cierto(p6_assets.pide_pantalla_dividida(partida), "se ve en la frase")
cierto(p6_assets.pide_pantalla_dividida({"direccion": "split-screen: left..."}),
       "y en la direccion")
cierto(not p6_assets.pide_pantalla_dividida(ESCENA), "y no se inventa")
cierto(p6_assets.PANTALLA_DIVIDIDA in corta(partida),
       "la orden corta la pide de verdad")
cierto(p6_assets.PANTALLA_DIVIDIDA not in corta(), "y solo cuando toca")
LAMINA = [{"papel": "lamina", "ruta": "x", "cuantas": 5}]
cierto("a split screen," not in p6_assets._prompt_completo(partida, LAMINA, {}),
       "la orden larga ya no la prohibe cuando la frase la pide")
cierto("a split screen," in p6_assets._prompt_completo(ESCENA, LAMINA, {}),
       "y la sigue prohibiendo cuando no")

titulo("3 · la carta dirigida")
igual(p6_assets.carta_dirigida("He waits. (TONO: neutro) (PLANO: Retrato)",
                               HISTORIA), "retrato", "lee la carta del director")
igual(p6_assets.carta_dirigida("(PLANO: silueta)", HISTORIA), "",
      "una carta que el Estilo no deja no vale")
igual(p6_assets.carta_dirigida("sin etiqueta", HISTORIA), "", "sin etiqueta, nada")
igual(p6_assets._con_direccion("Base.", {"direccion": "He waits. (PLANO: medio)"}),
      p6_assets._con_direccion("Base.", {"direccion": "He waits."}),
      "la carta no llega al generador")
escenas = [{"id": f"S{n:03d}"} for n in range(1, 30)]
p = {"estilo": {"cartas": HISTORIA},
     "unidades": {"escena:S005": {"direccion": "x (TONO: neutro) (PLANO: detalle)"},
                  "escena:S006": {"carta": "perfil",
                                  "direccion": "x (PLANO: retrato)"}}}
p6_assets._asignar_cartas(escenas, p)
igual(escenas[4]["carta"], "detalle", "la del director manda sobre el sorteo")
igual(escenas[5]["carta"], "perfil", "la de la persona manda sobre la del director")
cierto(all(e["carta"] in HISTORIA for e in escenas),
       "solo salen las cartas del Estilo")
cierto(all(e["encuadre"] for e in escenas), "y cada una trae su encuadre")
informe = p6_assets._informe_cartas(escenas, HISTORIA)
igual([c["id"] for c in informe["escalera"]], HISTORIA,
      "la pantalla ofrece solo las del Estilo")

titulo("4 · el director")
igual(direccion.cartas_legibles(None), "", "sin lista no hay bloque de cartas")
bloque = direccion.cartas_legibles(HISTORIA)
cierto("(PLANO: medio)" in bloque and all(f"[{c}]" in bloque for c in HISTORIA),
       "el bloque lista las cartas y como devolverla")
planos = direccion._planos_legibles(
    [{"id": "S006", "carta": "medio", "encuadre": "a medium shot",
      "narracion": "Sixty-one percent.",
      "cartela": {"fondo": "imagen", "datos": {"cifra": "61%", "icono": "x",
                                               "label": "of Americans"}}}],
    permitidas=HISTORIA)
cierto("plano sorteado: medio" in planos, "con lista, el plano dice el sorteado")
cierto("«61% · of Americans»" in planos, "y la cartela que va encima")
cierto("encuadre (no lo contradigas)"
       in direccion._planos_legibles([{"id": "S1", "encuadre": "a medium shot"}]),
       "sin lista, como siempre")
for clave in ("LO QUE SE VE ES LO QUE SE DICE", "SILUETAS", "split screen:",
              "CARTELA ENCIMA", "blank"):
    cierto(clave in direccion.INSTRUCCION, f"el encargo dice «{clave}»")
relleno = " ".join(["word"] * 80)
linea, aviso = direccion.limpiar_linea(f"{relleno} (TONO: triste) (PLANO: medio)")
cierto(aviso and linea.endswith("(TONO: triste) (PLANO: medio)"),
       "recortar una linea larga no le quita el tono ni el plano")
cierto("{cartas}" in direccion.INSTRUCCION, "el encargo tiene el hueco de cartas")

titulo("5 · el preset")
cambios = presets_canal.cambios_para(
    {"tipo": "estilo", "datos": {"guia": GUIA, "cartas": HISTORIA,
                                 "orden": "corta"}},
    {"assets": {"estilo": {"prompt": "a mano"}}})
estilo = cambios["assets"]["estilo"]
igual((estilo.get("cartas"), estilo.get("orden"), estilo.get("prompt")),
      (HISTORIA, "corta", "a mano"),
      "aplicar el Estilo pone cartas y orden sin borrar lo que habia")
guardado = presets_canal.datos_de_params(
    {"assets": {"estilo": {"guia": GUIA, "cartas": HISTORIA, "orden": "corta"}}})
igual((guardado["estilo"].get("cartas"), guardado["estilo"].get("orden")),
      (HISTORIA, "corta"), "guardar el Estilo se las lleva")

print()
if FALLOS:
    print(f"FALLAN {len(FALLOS)} de {COMPROBACIONES} comprobaciones:")
    for f in FALLOS:
        print("   ", f)
    sys.exit(1)
print(f"ORDEN CORTA OK: {COMPROBACIONES} comprobaciones pasan")
