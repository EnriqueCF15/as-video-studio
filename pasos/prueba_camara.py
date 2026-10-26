r"""
Prueba del ritmo de camara y de los subtitulos fuera del video (fork, 26-10-2026).

  1. VARIADA          el gancho quieto y a cortes secos; graficos, primeros
                      planos y cartelas quietos; nunca tres seguidos moviendose
  2. LOS OTROS MODOS  siempre = como antes; quieta = nada se mueve
  3. EL MOTOR         un plano quieto no tiene recorrido; el variado no pasa del
                      8 % ni hace paneo fuera de los planos abiertos
  4. LOS CORTES       la ranura «seco» sale como corte; la vieja «corte» sigue
                      siendo suave; el montaje la aplica desde movimiento.json
  5. SUBTITULOS       auto = fuera en horizontal y dentro en vertical; fuera, la
                      capa va vacia pero las fichas siguen (son el .srt)
  6. EL .SRT          ordenado, sin solapes y con su formato

    python pasos\prueba_camara.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camara  # noqa: E402
import medios  # noqa: E402
import p7_callouts as p7  # noqa: E402
import p8_render as p8  # noqa: E402
import subtitulos  # noqa: E402
import transiciones  # noqa: E402

movimiento = medios.motor("render_video/movimiento.py")

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


def plano(n, bloque, dur, carta="hombro", tono="neutro", sitio="oficina", **extra):
    t_in = 7.0 * (n - 1)
    return dict({"id": f"S{n:03d}", "t_in": t_in, "t_out": t_in + dur, "duracion": dur,
                 "origen": {"bloque": bloque}, "carta": carta, "tono": tono,
                 "set": sitio, "transicion": "suave",
                 "zoom": {"tipo": "in" if n % 2 else "out", "de": 1.0, "a": 1.0526}},
                **extra)


# un gancho de tres planos (B001-B002), y luego una seccion (B003) larga
ESCENAS = [
    plano(1, "B001", 6.0, "detalle"),
    plano(2, "B001", 6.5, "general"),
    plano(3, "B002", 7.0, "pantalla", "tenso"),
    plano(4, "B003", 7.5, "general", sitio="fabrica"),
    plano(5, "B003", 7.0, "diagrama", sitio="fabrica"),
    plano(6, "B003", 7.0, "retrato", sitio="fabrica"),
    plano(7, "B003", 4.0, "hombro", sitio="fabrica"),
    plano(8, "B003", 8.0, "suelo", sitio="fabrica"),
    plano(9, "B003", 8.0, "aereo", "tenso", sitio="calle"),
    plano(10, "B003", 8.0, "general", "solemne", sitio="calle"),
    plano(11, "B003", 8.0, "general", "tenso", sitio="calle"),
    plano(12, "B003", 7.0, "hombro", sitio="calle", cartela={"plantilla": "pregunta"}),
]
SECCIONES = ["B001", "B003"]

titulo("1 · variada")
d = camara.decidir(ESCENAS, "variada", SECCIONES)
igual(camara.ids_del_gancho(ESCENAS, SECCIONES), {"S001", "S002", "S003"},
      "el gancho acaba donde empieza la segunda seccion")
for sid in ("S001", "S002", "S003"):
    igual(d[sid]["mov"], "quieto", f"{sid}: el gancho va quieto")
igual([d[s]["seco"] for s in ("S001", "S002", "S003")], [False, True, True],
      "el gancho va a cortes secos (el primero no tiene de donde venir)")
igual(d["S004"]["mov"], "paneo", "el plano que abre una seccion se mueve")
igual(d["S005"]["mov"], "quieto", "un diagrama va quieto")
igual(d["S006"]["mov"], "quieto", "un primer plano va quieto")
igual(d["S007"]["mov"], "quieto", "un plano corto va quieto")
igual(d["S012"]["mov"], "quieto", "una cartela va quieta")
igual(d["S006"]["seco"], True, "dos quietos del mismo sitio: corte seco")
igual(d["S004"]["seco"], False, "un plano que se mueve no entra en seco")
seguidos, peor = 0, 0
for e in ESCENAS:
    seguidos = seguidos + 1 if d[e["id"]]["mov"] != "quieto" else 0
    peor = max(peor, seguidos)
cierto(peor <= camara.MOVIDOS_SEGUIDOS, f"nunca mas de dos seguidos moviendose ({peor})")
igual(camara.decidir(ESCENAS, "variada", SECCIONES), d, "es determinista")
igual(camara.ids_del_gancho(ESCENAS, []), {"S001", "S002", "S003", "S004", "S005"},
      "sin secciones el gancho son los primeros 30 s")

titulo("2 · los otros modos")
siempre = camara.decidir(ESCENAS, "siempre", SECCIONES)
cierto(all(x["mov"] == "plan" and not x["seco"] for x in siempre.values()),
       "siempre: el movimiento y las transiciones de antes")
quieta = camara.decidir(ESCENAS, "quieta", SECCIONES)
cierto(all(x["mov"] == "quieto" for x in quieta.values()), "quieta: nada se mueve")
igual(camara.modo_de({"camara": "loca"}), "variada", "un modo raro cae al de por defecto")
igual(camara.modo_de({}), "variada", "sin modo, variada")

titulo("3 · el motor")
esc, ajustes = camara.escena_para_el_motor(ESCENAS[4], d["S005"])
igual((esc["zoom"]["de"], esc["zoom"]["a"], ajustes), (1.0, 1.0, {}),
      "un plano quieto llega al motor sin zoom")
igual(ESCENAS[4]["zoom"]["a"], 1.0526, "y el plan no se toca")
de, a, c0, c1 = movimiento.recorrido(esc, 1.0, 1.0, [0.5, 0.5], "por_defecto")
igual((de, a, c0, c1), (1.0, 1.0, [0.5, 0.5], [0.5, 0.5]), "quieto: sin recorrido")
largo = dict(ESCENAS[7], t_out=ESCENAS[7]["t_in"] + 12.0)
de, a, c0, c1 = movimiento.recorrido(largo, 1.0, 1.0526, [0.5, 0.5], "por_defecto",
                                     camara.ZOOM_MAXIMO_VARIADA, False)
cierto(a - de <= camara.ZOOM_MAXIMO_VARIADA + 1e-9, f"variado: zoom de 8 % como mucho ({a - de})")
igual(c0, c1, "sin paneo, el centro no se mueve")
de, a, c0, c1 = movimiento.recorrido(largo, 1.0, 1.0526, [0.5, 0.5], "por_defecto")
igual(round(a - de, 4), movimiento.ZOOM_MAXIMO, "siempre: el 12 % de antes")
cierto(c0 != c1, "siempre: con paneo")
esc, ajustes = camara.escena_para_el_motor(ESCENAS[3], d["S004"])
igual(ajustes.get("paneo"), True, "un paneo pide paneo al motor")
igual(camara.escena_para_el_motor(ESCENAS[3], siempre["S004"])[0], ESCENAS[3],
      "modo siempre: la escena tal cual")

titulo("4 · los cortes")
cortes = transiciones.resolver([dict(ESCENAS[0]), dict(ESCENAS[1], transicion="seco"),
                                dict(ESCENAS[2], transicion="corte")])
igual(cortes["S002"]["tipo"], "corte", "la ranura seco sale como corte")
igual(cortes["S002"]["duracion"], 0.0, "y sin duracion")
cierto(cortes["S003"]["tipo"] != "corte", "la ranura vieja 'corte' sigue siendo suave")
montaje = p8._con_cortes_de_camara(ESCENAS, {"camara": d})
igual([e["transicion"] for e in montaje[:3]], ["suave", "seco", "seco"],
      "el montaje pone en seco lo que marca la camara")
igual(ESCENAS[1]["transicion"], "suave", "sin tocar el plan")
igual(p8._con_cortes_de_camara(ESCENAS, {}), ESCENAS, "un movimiento.json viejo: nada cambia")

titulo("5 · subtitulos")
igual(p7.subtitulos_quemados({}, (1920, 1080)), False, "auto en horizontal: fuera")
igual(p7.subtitulos_quemados({}, (1080, 1920)), True, "auto en vertical: dentro")
igual(p7.subtitulos_quemados({"subtitulos": "si"}, (1920, 1080)), True, "si: dentro")
igual(p7.subtitulos_quemados({"subtitulos": "no"}, (1080, 1920)), False, "no: fuera")
con_voz = {"id": "S050", "t_in": 10.0, "t_out": 13.0,
           "narracion": "Eight hours a day.",
           "marcas": [[10.1, 10.4], [10.5, 10.9], [11.0, 11.2], [11.3, 11.8]]}
p = p7._con_defectos({"subtitulos": "no"})
svg, fichas = p7.capa_fija_de(con_voz, p)
cierto("<text" not in svg, "fuera: la capa quieta va vacia")
cierto(fichas and fichas[0]["tipo"] == "subtitulo", "pero las fichas siguen (son el .srt)")
svg, fichas = p7.capa_fija_de(con_voz, p7._con_defectos({"subtitulos": "si"}))
cierto("<text" in svg, "si: el subtitulo va dibujado")

titulo("6 · el .srt")
texto = subtitulos.srt([(3.0, 4.0, "second"), (0.0, 3.5, "first  line"), (5.0, 5.1, "x")])
lineas = texto.split("\n")
igual(lineas[:3], ["1", "00:00:00,000 --> 00:00:03,000", "first line"],
      "ordenado, sin pisar al siguiente y con los espacios limpios")
igual(lineas[5], "00:00:03,000 --> 00:00:04,000", "el segundo, tal cual")
igual(lineas[9], "00:00:05,000 --> 00:00:05,500", "uno muy corto dura al menos medio segundo")
igual(subtitulos.srt([]), "", "sin subtitulos, vacio")

print()
if FALLOS:
    print(f"FALLAN {len(FALLOS)} de {COMPROBACIONES} comprobaciones:")
    for f in FALLOS:
        print("   ", f)
    sys.exit(1)
print(f"CAMARA OK: {COMPROBACIONES} comprobaciones pasan")
