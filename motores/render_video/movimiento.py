"""
Hyperframes y movimiento de camara determinista.

Idea: el plano se genera a 1536x1024 y se amplia x2 en local. El video sale a
1920x1080 recortando una ventana 16:9 DENTRO de ese hyperframe de 3072x2048. Un
zoom de 1.0 a 1.2 sigue leyendo pixeles reales del hyperframe en vez de estirar
los del plano, que es la diferencia entre un zoom limpio y uno emborronado.

El centro del zoom no se elige a ojo: sale de anclas.json, que el blockout
proyecta desde la misma geometria que guio la generacion de la imagen. Si la
escena dice zoom sobre 'buque', el encuadre acaba sobre el buque, sin adivinar.

    python movimiento.py --plan plan_timed.json --escenas ./assets/final --out movimiento.json
"""
import argparse
import json
import os
import sys

from PIL import Image

ASPECTO = 16 / 9


def cargar_anclas(blockout_dir, set_name, camara):
    ruta = os.path.join(blockout_dir, set_name or "", "anclas.json")
    if not set_name or not os.path.exists(ruta):
        return {}
    with open(ruta, "r", encoding="utf-8") as fh:
        return json.load(fh).get(camara, {})


def centro_de(escena, anclas):
    """Centro normalizado del zoom. Prioridad: ancla del blockout, centro
    explicito del plan, y si no hay nada, el centro geometrico."""
    zoom = escena.get("zoom") or {}
    nombre = zoom.get("ancla")
    if nombre:
        ancla = anclas.get(nombre)
        if ancla and ancla.get("visible"):
            return [ancla["x"], ancla["y"]], f"ancla:{nombre}"
    if zoom.get("centro"):
        return list(zoom["centro"]), "plan"
    return [0.5, 0.5], "por_defecto"


def ventana(centro, escala):
    """Rectangulo 16:9 normalizado sobre el hyperframe para una escala dada.

    La ventana se mantiene dentro de los limites: si el ancla esta cerca de un
    borde, se desplaza en lugar de salirse, porque un recorte fuera del lienzo
    sacaria banda negra.
    """
    ancho = 1.0 / escala
    alto = 1.0 / escala
    x = min(max(centro[0] - ancho / 2, 0.0), 1.0 - ancho)
    y = min(max(centro[1] - alto / 2, 0.0), 1.0 - alto)
    return [round(x, 5), round(y, 5), round(ancho, 5), round(alto, 5)]


#: EL MOVIMIENTO CRECE CON EL PLANO (fork, Fase 5). El zoom era el mismo 5 %
#: en cualquier plano (`segmentar.CIERRE`), pensado para planos de 2,5 a 6 s:
#: en uno de 8 s ese recorrido es tan lento que la imagen parece quieta, que es
#: justo lo que no se puede permitir un video largo, hecho a proposito de planos
#: largos para pagar menos imagenes. Por encima de PLANO_LARGO_S se mantiene la
#: VELOCIDAD del zoom de siempre (un 5 % en 3,5 s) hasta ZOOM_MAXIMO, y si el
#: centro no lo fija un ancla la camara ademas se desliza hacia un lado lo que
#: deje libre el encuadre: un paneo suave, alternando el lado plano a plano.
#: Los planos cortos, las cartelas y los que siguen a otro no cambian.
PLANO_LARGO_S = 5.0
VELOCIDAD_ZOOM = 0.0526 / 3.5
ZOOM_MAXIMO = 0.12
#: Que parte del margen libre recorre el paneo (el resto queda de aire).
DESLIZ = 0.6


def recorrido(escena, de, a, centro, origen_centro):
    """El zoom y el paneo de un plano segun lo que dura. -> (de, a, c_ini, c_fin)"""
    try:
        duracion = float(escena.get("t_out") or 0) - float(escena.get("t_in") or 0)
    except (TypeError, ValueError):
        duracion = 0.0
    if (duracion <= PLANO_LARGO_S or de == a or escena.get("cartela")
            or escena.get("sigue_a")):
        return de, a, centro, centro
    corto, largo = min(de, a), max(de, a)
    tramo = min(ZOOM_MAXIMO, max(largo - corto, VELOCIDAD_ZOOM * duracion))
    if a > de:
        a = round(de + tramo, 4)
    else:
        de = round(a + tramo, 4)
    if origen_centro != "por_defecto":
        return de, a, centro, centro        # un ancla manda donde mira la camara
    cifras = "".join(c for c in str(escena.get("id") or "") if c.isdigit())
    lado = 1 if int(cifras or 0) % 2 == 0 else -1
    margen = (1.0 - 1.0 / max(de, a)) / 2.0
    movido = [round(min(max(centro[0] + lado * DESLIZ * margen, 0.0), 1.0), 4),
              centro[1]]
    # el paneo va hacia el extremo mas cerrado del zoom: es donde hay margen
    if a > de:
        return de, a, centro, movido
    return de, a, movido, centro


def hyperframe(origen, destino, escala=2):
    """Amplia el plano. LANCZOS basta porque el arte es plano y de linea dura:
    no hay textura fina que reconstruir, solo bordes que mantener limpios."""
    img = Image.open(origen).convert("RGB")
    grande = img.resize((img.width * escala, img.height * escala), Image.LANCZOS)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    grande.save(destino, "PNG")
    return grande.size


def calcular(plan, dir_escenas, dir_hyper, blockout_dir, escala=2):
    movimientos = []
    for escena in plan["escenas"]:
        sid = escena["id"]
        origen = os.path.join(dir_escenas, f"{sid}.png")
        zoom = escena.get("zoom") or {}
        anclas = cargar_anclas(blockout_dir, escena.get("set"), escena.get("camara"))
        centro, fuente = centro_de(escena, anclas)

        de = float(zoom.get("de", 1.0))
        a = float(zoom.get("a", 1.0))
        de, a, centro_ini, centro_fin = recorrido(escena, de, a, centro, fuente)
        entrada = {
            "id": sid,
            "t_in": escena.get("t_in"),
            "t_out": escena.get("t_out"),
            "transicion": escena.get("transicion", "corte"),
            "centro": [round(c, 4) for c in centro],
            "origen_centro": fuente,
            "escala_ini": de,
            "escala_fin": a,
            "ventana_ini": ventana(centro_ini, de),
            "ventana_fin": ventana(centro_fin, a),
            "componente": escena.get("componente"),
        }

        if os.path.exists(origen):
            destino = os.path.join(dir_hyper, f"{sid}.png")
            ancho, alto = hyperframe(origen, destino, escala)
            entrada["hyperframe"] = os.path.relpath(destino, os.path.dirname(dir_hyper))
            entrada["hyperframe_px"] = [ancho, alto]
            # Con la ventana mas cerrada, cuantos pixeles reales quedan por
            # cada pixel de salida. Por debajo de 1.0 se estaria inventando detalle.
            estrecha = min(de, a) if min(de, a) > 0 else 1.0
            entrada["px_por_px_salida"] = round((ancho / max(de, a)) / 1920, 2)
        else:
            entrada["hyperframe"] = None

        movimientos.append(entrada)
    return movimientos


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--escenas", required=True)
    parser.add_argument("--hyper", required=True)
    parser.add_argument("--blockout", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--escala", type=int, default=2)
    args = parser.parse_args()

    with open(args.plan, "r", encoding="utf-8") as fh:
        plan = json.load(fh)

    movimientos = calcular(plan, args.escenas, args.hyper, args.blockout, args.escala)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"fps": plan.get("fps", 30),
                   "resolucion": plan.get("resolucion", [1920, 1080]),
                   "movimientos": movimientos}, fh, ensure_ascii=False, indent=2)

    for m in movimientos:
        marca = "OK " if m.get("hyperframe") else "-- "
        ratio = m.get("px_por_px_salida")
        aviso = "" if ratio is None or ratio >= 1.0 else f"  AVISO ratio {ratio}"
        print(f"  {marca}{m['id']}  zoom {m['escala_ini']}->{m['escala_fin']}  "
              f"centro {m['centro']} ({m['origen_centro']}){aviso}")
    print(f"[movimiento] {len(movimientos)} escenas -> {args.out}")


if __name__ == "__main__":
    main()
