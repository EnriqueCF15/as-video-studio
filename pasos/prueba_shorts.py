"""Pruebas de pasos/shorts.py sin red ni ffmpeg (fork, 04-11-2026)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import shorts  # noqa: E402

FALLOS = []


def comprobar(nombre, condicion, detalle=""):
    print(("OK   " if condicion else "FALLA") + f" {nombre}" + (f" ({detalle})" if detalle and not condicion else ""))
    if not condicion:
        FALLOS.append(nombre)


def _escena(t_in, texto, paso=0.5):
    palabras = texto.split()
    marcas = [[t_in + i * paso, t_in + i * paso + paso * 0.8] for i in range(len(palabras))]
    return {"narracion": texto, "marcas": marcas, "t_in": t_in,
            "t_out": t_in + len(palabras) * paso}


PLAN = {"escenas": [
    _escena(0.0, "January. You wake up in the dark,"),
    _escena(3.0, "because a cow just sighed. It's your cow."),
    _escena(10.0, "She lives here? Yes."),
]}


def prueba_frases():
    frases = shorts.frases_del_plan(PLAN)
    comprobar("frases partidas por puntuacion", [f["texto"] for f in frases] == [
        "January.", "You wake up in the dark, because a cow just sighed.",
        "It's your cow.", "She lives here?", "Yes."], [f["texto"] for f in frases])
    comprobar("una frase cruza dos escenas", frases[1]["t0"] < 3.0 < frases[1]["t1"])
    comprobar("tiempos de palabra conservados", frases[0]["palabras"][0][1:] == [0.0, 0.4])
    return frases


def prueba_tramos(frases):
    tramo = shorts._tramos(frases, [[2, 3]])[0]
    comprobar("margen antes sin pisar la frase anterior",
              frases[0]["t1"] <= tramo["ini"] <= frases[1]["t0"])
    comprobar("margen despues sin pisar la siguiente",
              frases[2]["t1"] <= tramo["fin"] <= frases[3]["t0"])
    primero = shorts._tramos(frases, [[1, 1]])[0]
    comprobar("el primer tramo no baja de 0", primero["ini"] >= 0)


def prueba_problemas(frases):
    fallos = shorts._problemas(frases, [{"titulo_pantalla": "x", "momentos": [[1, 2]]}])
    comprobar("avisa de duracion corta", any("dura" in f for f in fallos), fallos)
    fallos = shorts._problemas(frases, [{"titulo_pantalla": "x", "momentos": [[3, 99]]}])
    comprobar("rechaza frase inexistente", any("no son dos numeros" in f for f in fallos), fallos)
    fallos = shorts._problemas(frases, [{"titulo_pantalla": "a", "momentos": [[1, 2]]},
                                        {"titulo_pantalla": "b", "momentos": [[2, 3]]}])
    comprobar("avisa de frase repetida entre shorts", any("ya esta en" in f for f in fallos), fallos)
    comprobar("lista vacia", shorts._problemas(frases, []) != [])


def prueba_grupos():
    palabras = [[p, i, i + 0.4] for i, p in enumerate(
        "Because something enormous just sighed, a few feet from your bed.".split())]
    grupos = [" ".join(p[0] for p in g) for g in shorts._grupos(palabras)]
    comprobar("grupos de subtitulo cortos",
              all(len(g.split()) <= shorts.PALABRAS_POR_GRUPO for g in grupos), grupos)
    comprobar("corta en la coma", "sighed," in [g.split()[-1] for g in grupos], grupos)


def prueba_ass():
    frases = shorts.frases_del_plan(PLAN)
    pieza = {"ini": 0.0, "fin": 2.0, "inicio_short": 0.0,
             "palabras": frases[0]["palabras"] + frases[1]["palabras"][:2]}
    texto = shorts._ass({"titulo_pantalla": "Why {cows}?"}, [pieza], 2.6)
    comprobar("ass con titulo en mayusculas y sin llaves", "WHY (COWS)?" in texto)
    comprobar("ass con palabra resaltada", "\\c&H00E5FF&" in texto)
    comprobar("tiempo ass", shorts._ass_tiempo(61.257) == "0:01:01.26", shorts._ass_tiempo(61.257))


if __name__ == "__main__":
    frases = prueba_frases()
    prueba_tramos(frases)
    prueba_problemas(frases)
    prueba_grupos()
    prueba_ass()
    print(f"\n{'TODO OK' if not FALLOS else f'{len(FALLOS)} FALLOS'}")
    sys.exit(1 if FALLOS else 0)
