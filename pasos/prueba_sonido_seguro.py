"""
Prueba de la Fase 3 (fork): musica y efectos que se pueden monetizar.

Licencias (solo CC0 y CC BY de Jamendo y Freesound), la carpeta propia con sus
fichas, el orden de fuentes de Configuracion, Uppbeat y el creditos.txt.

NO SALE A LA RED. Jamendo y Freesound se sustituyen por respuestas de mentira,
los secretos y los ajustes van a una carpeta temporal, y los ficheros de audio
son tonos generados aqui.

    python pasos/prueba_sonido_seguro.py
"""
import os
import shutil
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)

BASE = tempfile.mkdtemp(prefix="estudio_sonido_seguro_")
os.environ["ESTUDIO_BANCO"] = os.path.join(BASE, "banco")
os.environ["ESTUDIO_SECRETOS"] = os.path.join(BASE, "secretos")
os.environ["ESTUDIO_AJUSTES"] = os.path.join(BASE, "ajustes.json")
for _clave in ("JAMENDO_CLIENT_ID", "FREESOUND_API_KEY"):
    os.environ.pop(_clave, None)

import numpy as np  # noqa: E402

import ajustes  # noqa: E402
import sonido  # noqa: E402

FALLOS = []
HECHAS = [0]


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
    print(f"  FALLO {texto} (no fallo)")
    FALLOS.append(texto)


def tono(ruta, segundos, hercios=220.0):
    """Un WAV de verdad: lo que se mide y se copia tiene que poder abrirse."""
    t = np.arange(int(sonido.FRECUENCIA * segundos)) / sonido.FRECUENCIA
    mono = 0.3 * np.sin(2 * np.pi * hercios * t)
    sonido._escribir(ruta, np.stack([mono, mono], axis=1).astype(np.float32))
    return ruta


# ---------------------------------------------------------------- licencias

def prueba_licencias():
    print("\n[1] las licencias: que se puede monetizar y que no")
    casos = [
        ("http://creativecommons.org/publicdomain/zero/1.0/", "cc0", True),
        ("https://creativecommons.org/licenses/by/4.0/", "cc-by", True),
        ("http://creativecommons.org/licenses/by/3.0/", "cc-by", True),
        ("http://creativecommons.org/licenses/by-nc/3.0/", "cc-by-nc", False),
        ("http://creativecommons.org/licenses/by-nc-sa/3.0/", "cc-by-nc-sa", False),
        ("http://creativecommons.org/licenses/by-nc-nd/3.0/", "cc-by-nc-nd", False),
        ("http://creativecommons.org/licenses/by-nd/4.0/", "cc-by-nd", False),
        ("https://creativecommons.org/licenses/by-sa/4.0/", "cc-by-sa", False),
        ("http://creativecommons.org/licenses/sampling+/1.0/", "sampling+", False),
        ("Attribution", "cc-by", True),
        ("Attribution NonCommercial 4.0", "cc-by-nc", False),
        ("Creative Commons 0", "cc0", True),
        ("", "", False),
        ("licencia rara", "", False),
    ]
    for texto, tipo, segura in casos:
        igual(sonido.tipo_de_licencia(texto), tipo, f"{texto or '(vacia)'} es {tipo or 'desconocida'}")
        igual(sonido.licencia_segura(texto), segura,
              f"{texto or '(vacia)'} {'SI' if segura else 'NO'} se puede monetizar")
    igual(sonido.nombre_licencia("https://creativecommons.org/licenses/by/4.0/"),
          "CC BY 4.0", "el nombre corto lleva su version")
    igual(sonido.nombre_licencia("http://creativecommons.org/publicdomain/zero/1.0/"),
          "CC0 1.0", "y el CC0 tambien")


# ---------------------------------------------------------------- ajustes

def prueba_ajustes():
    print("\n[2] los ajustes de audio y el orden de fuentes")
    a = ajustes.leer()
    igual(a["audio_licencias_seguras"], True, "por defecto, solo licencias seguras")
    igual(a["audio_prioridad"], "propia_primero", "y la carpeta propia primero")
    igual(a["uppbeat_suscripcion"], False, "sin suscripcion de Uppbeat")
    igual(sonido.fuentes_de("musica"), ["propia", "jamendo"], "musica: carpeta y luego Jamendo")
    igual(sonido.fuentes_de("efectos"), ["propia", "freesound"], "efectos: carpeta y luego Freesound")
    ajustes.guardar({"audio_prioridad": "solo_propia"})
    igual(sonido.fuentes_de("musica"), ["propia"], "«solo tu carpeta» no sale fuera")
    ajustes.guardar({"audio_prioridad": "en_linea_primero"})
    igual(sonido.fuentes_de("efectos"), ["freesound", "propia"], "y al reves si se pide")
    ajustes.guardar({"audio_prioridad": "propia_primero"})
    falla(lambda: ajustes.guardar({"audio_prioridad": "jamendo_solo"}),
          "una prioridad desconocida no se guarda", ValueError)
    falla(lambda: ajustes.guardar({"audio_licencias_seguras": "si"}),
          "las licencias seguras son verdadero o falso", ValueError)
    falla(lambda: ajustes.guardar({"uppbeat_canal": "x" * 300}),
          "el canal de Uppbeat es un texto corto", ValueError)
    igual(ajustes.guardar({"uppbeat_canal": "  https://youtube.com/@yo  "})["uppbeat_canal"],
          "https://youtube.com/@yo", "y se guarda limpio")


# ---------------------------------------------------------- la carpeta propia

def prueba_carpeta_propia():
    print("\n[3] la carpeta propia: un fichero sin ficha no se usa")
    musica = sonido.carpeta_propia("musica")
    efectos = sonido.carpeta_propia("efectos")
    comprobar(musica.endswith(os.path.join("audio", "propio", "musica")),
              "la carpeta de musica vive en banco/audio/propio/musica")
    tono(os.path.join(musica, "Calma Total.wav"), 6.0, 180.0)
    tono(os.path.join(musica, "sin ficha.wav"), 4.0, 300.0)
    tono(os.path.join(efectos, "whoosh.wav"), 0.8, 900.0)
    with open(os.path.join(musica, "notas.txt"), "w", encoding="utf-8") as fh:
        fh.write("no es audio")

    catalogo = sonido.catalogo_propio("musica")
    igual(sorted(e["archivo"] for e in catalogo), ["Calma Total.wav", "sin ficha.wav"],
          "el catalogo ve los audios y no el .txt")
    comprobar(all(not e["completa"] for e in catalogo), "sin ficha, ninguno se puede usar")
    igual(sonido.propios("musica"), [], "y propios() no devuelve nada")
    comprobar(not sonido.hay_musica(), "sin ficha ni Jamendo no hay musica")
    comprobar("carpeta" in sonido.falta_musica_texto(), "y el aviso dice donde ponerla")

    falla(lambda: sonido.guardar_ficha_propia("musica", "Calma Total.wav", {
        "fuente": "youtube_audio_library", "titulo": "Calma", "licencia": "YouTube Audio Library"}),
        "una ficha de musica sin animo se rechaza", ValueError)
    falla(lambda: sonido.guardar_ficha_propia("musica", "Calma Total.wav", {
        "fuente": "youtube_audio_library", "titulo": "Calma", "animo": "sobrio",
        "licencia": "CC BY 4.0", "atribucion_requerida": True}),
        "si pide atribucion sin texto de credito, se rechaza", ValueError)
    falla(lambda: sonido.guardar_ficha_propia("musica", "no-existe.wav", {}),
          "un fichero que no esta en la carpeta se rechaza", ValueError)
    falla(lambda: sonido.guardar_ficha_propia("musica", "Calma Total.wav", {
        "fuente": "spotify", "titulo": "x", "animo": "sobrio", "licencia": "x"}),
        "una fuente desconocida se rechaza", ValueError)
    ficha = sonido.guardar_ficha_propia("musica", "Calma Total.wav", {
        "fuente": "youtube_audio_library", "titulo": "Calma Total", "artista": "Autor YT",
        "licencia": "YouTube Audio Library", "animo": "sobrio", "bpm": 0})
    igual(ficha["bpm"], None, "un bpm vacio (0) se guarda como «no se sabe»")
    sonido.guardar_ficha_propia("efectos", "whoosh.wav", {
        "fuente": "mixkit", "titulo": "Whoosh", "licencia": "Mixkit License",
        "papel": "transicion_suave"})

    listos = sonido.propios("musica")
    igual(len(listos), 1, "con ficha, el tema ya se puede usar")
    tema = listos[0]
    igual((tema["fuente"], tema["origen"], tema["animo"]),
          ("propia", "youtube_audio_library", "sobrio"),
          "la ficha dice de donde sale y su animo")
    comprobar(tema["id"].startswith("calma-total-"), f"el id sale del nombre ({tema['id']})")
    igual(sonido.propios("musica")[0]["id"], tema["id"], "y es estable mientras no cambie")
    comprobar(sonido.hay_musica(), "ahora si hay musica")

    ruta = sonido.traer(tema, "musica")
    comprobar(os.path.exists(ruta) and ruta.endswith(".wav"),
              "traer copia el tema al banco con su extension")
    igual(os.path.basename(ruta), f"propia_{tema['id']}.wav", "con el nombre de siempre")

    tono(os.path.join(musica, "Calma Total.wav"), 20.0, 190.0)
    nuevo = sonido.propios("musica")[0]["id"]
    comprobar(nuevo != tema["id"], "si el fichero cambia, cambia el id (no suena la copia vieja)")

    print("\n[3b] los trozos buenos de una cancion")
    tono(os.path.join(musica, "Paseo.wav"), 25.0, 240.0)
    falla(lambda: sonido.guardar_ficha_propia("musica", "Paseo.wav", {
        "fuente": "pixabay", "titulo": "Paseo", "licencia": "Pixabay Content License",
        "animo": "sobrio", "fragmentos": [{"desde": 2, "hasta": 6}]}),
        "un trozo de menos de 8 s se rechaza", ValueError)
    falla(lambda: sonido.guardar_ficha_propia("musica", "Paseo.wav", {
        "fuente": "pixabay", "titulo": "Paseo", "licencia": "Pixabay Content License",
        "animo": "sobrio", "fragmentos": [{"desde": "a", "hasta": 9}]}),
        "un trozo que no son numeros se rechaza", ValueError)
    paseo = sonido.guardar_ficha_propia("musica", "Paseo.wav", {
        "fuente": "pixabay", "titulo": "Paseo", "licencia": "Pixabay Content License",
        "animo": "sobrio",
        "fragmentos": [[12, 99], {"desde": 3, "hasta": 13}]})
    igual(paseo["fragmentos"], [{"desde": 3.0, "hasta": 25.0}],
          "los trozos se ordenan, se juntan si se pisan y no pasan del final")
    comprobar(abs(paseo["duracion"] - 25.0) < 0.2, "y la duracion se mide al guardar")
    ficha_paseo = next(f for f in sonido.propios("musica") if f["archivo"] == "Paseo.wav")
    igual(sonido.partes_utiles(ficha_paseo), [(3.0, 25.0)],
          "con trozos marcados, se usan esos")
    # analizar un fichero DE TU CARPETA no deja copias de trabajo en ella
    sonido.partes_automaticas(os.path.join(musica, "Paseo.wav"))
    igual(sorted(n for n in os.listdir(musica) if n.endswith(".wav")),
          ["Calma Total.wav", "Paseo.wav", "sin ficha.wav"],
          "analizar tu cancion no deja un .48000.wav en tu carpeta")
    tono(os.path.join(musica, "vieja.48000.wav"), 1.0)
    comprobar("vieja.48000.wav" not in [e["archivo"] for e in sonido.catalogo_propio("musica")],
              "y una copia de trabajo vieja no sale como cancion")
    os.remove(os.path.join(musica, "vieja.48000.wav"))
    sonido.guardar_ficha_propia("musica", "Paseo.wav", {
        "fuente": "pixabay", "titulo": "Paseo", "licencia": "Pixabay Content License",
        "animo": "sobrio", "fragmentos": [{"desde": 5, "hasta": 17}]})

    # una cancion con sus partes malas: 4 s de silencio, 20 s buenos, un golpe
    # fuerte de 3 s, 12 s buenos y la caida del final
    f = sonido.FRECUENCIA
    t = np.arange(f) / f
    seg = 0.25 * np.sin(2 * np.pi * 330 * t)
    partes = ([np.zeros(f)] * 4 + [seg] * 20 + [seg * 4] * 3 + [seg] * 12
              + [seg * 0.02] * 4)
    mono = np.concatenate(partes)
    ruta_mala = os.path.join(BASE, "con_partes_malas.wav")
    sonido._escribir(ruta_mala, np.stack([mono, mono], axis=1).astype(np.float32))
    auto = sonido.partes_automaticas(ruta_mala)
    comprobar(len(auto) == 2, f"salen dos trozos: el golpe los separa ({auto})")
    if len(auto) == 2:
        comprobar(auto[0][0] >= 4.0, "el primero empieza despues del silencio")
        comprobar(auto[0][1] <= 24.0 and auto[1][0] >= 27.0, "y el golpe fuerte se salta")
        comprobar(auto[1][1] <= 39.0, "y el final que cae no se usa")


def prueba_banda_con_lo_propio():
    print("\n[4] la banda sonora con la carpeta propia, sin Jamendo")
    escenas = [{"id": f"S{i:03d}", "t_in": i * 4.0, "t_out": (i + 1) * 4.0} for i in range(40)]
    banda = sonido.montar_banda(escenas, 160.0)
    tramos = banda["tramos"]
    comprobar(len(tramos) == 2, f"un video de 160 s son 2 tramos ({len(tramos)})")
    comprobar(all(t["fuente"] == "propia" for t in tramos), "todos de la carpeta propia")
    for tramo in tramos:
        canciones = {p["archivo"] for p in tramo["piezas"]}
        igual(canciones, {"Calma Total.wav", "Paseo.wav"},
              f"tramo {tramos.index(tramo) + 1}: un tramo largo se llena con VARIAS canciones")
    paseo = [p for p in tramos[0]["piezas"] if p["archivo"] == "Paseo.wav"]
    comprobar(paseo and all((p["desde"], p["hasta"]) == (5.0, 17.0) for p in paseo),
              "de Paseo solo entra su trozo marcado (5-17 s)")
    calma = [p for p in tramos[0]["piezas"] if p["archivo"] == "Calma Total.wav"]
    comprobar(calma and all(p["hasta"] <= 17.0 for p in calma),
              "de Calma, sin trozos marcados, los automaticos (sin la caida del final)")
    largo_1 = 80.0 + sonido.CRUCE_S
    cubierto = sum(p["hasta"] - p["desde"] for p in tramos[0]["piezas"]) \
        - sonido.CRUCE_PIEZA_S * (len(tramos[0]["piezas"]) - 1)
    comprobar(cubierto >= largo_1, f"y cubren el tramo entero ({cubierto:.0f} de {largo_1:.0f} s)")
    igual(banda["avisos"], [], "sin avisos")

    fijada = sonido.montar_banda(escenas, 160.0, fijadas={"1": "Paseo.wav"})
    igual(fijada["tramos"][1]["piezas"][0]["archivo"], "Paseo.wav",
          "una cancion fijada a mano va primero en su tramo")
    igual(fijada["tramos"][1]["fijada"], "Paseo.wav", "y queda dicho que la elegiste tu")
    igual(fijada["tramos"][1]["por_que"], "elegida a mano", "con su porque")
    perdida = sonido.montar_banda(escenas, 160.0, fijadas={"0": "ya-no-esta.wav"})
    comprobar(perdida["avisos"] and "ya no está" in perdida["avisos"][0],
              "si la fijada ya no esta en la carpeta, se avisa y se elige otra")

    destino = os.path.join(BASE, "cama.wav")
    hecho, faltan = sonido.construir_cama(banda, 160.0, destino)
    comprobar(hecho and os.path.exists(destino) and not faltan,
              "la cama se construye desde el banco sin salir a la red")
    if hecho:
        duracion = len(sonido._leer(destino)) / sonido.FRECUENCIA
        comprobar(abs(duracion - 160.0) < 0.1, f"y dura lo que el video ({duracion:.1f} s)")

    print("\n[4b] los animos de cada parte del video, por Estilo")
    igual(sonido.animos_arco({}), sonido.ANIMOS_ARCO, "sin tabla del Estilo, la de siempre")
    falla(lambda: sonido.validar_animos_arco({"intro": {"lento": "sobrio"}}),
          "una parte inventada se rechaza", ValueError)
    falla(lambda: sonido.validar_animos_arco({"inicio": {"lento": "alegre"}}),
          "un animo inventado se rechaza", ValueError)
    falla(lambda: sonido.validar_animos_arco({"inicio": {"medio": "sobrio"}}),
          "un ritmo inventado se rechaza", ValueError)
    tabla = sonido.validar_animos_arco({"inicio": {"lento": "sobrio", "rapido": "sobrio"},
                                        "cierre": {"lento": "esperanzador"}})
    igual((tabla["inicio"]["rapido"], tabla["medio"]["lento"], tabla["cierre"]["lento"]),
          ("sobrio", "sobrio", "esperanzador"), "lo que no se dice se queda como siempre")
    arco = sonido.arco_del_video(escenas, 160.0, tabla)
    igual([t["animo"] for t in arco], ["sobrio", "esperanzador"],
          "el arranque ya no pide tension y el cierre pide esperanzador")
    con_tabla = sonido.montar_banda(escenas, 160.0, animos=tabla)
    igual([t["animo"] for t in con_tabla["tramos"]], ["sobrio", "esperanzador"],
          "y la banda sonora monta con esa tabla")

    hecho = sonido.creditos({"musica": banda}, [], idioma="en")
    comprobar("Calma Total" in hecho["texto"] and "Paseo" in hecho["texto"],
              "los creditos citan TODAS las canciones de un tramo, no solo la primera")

    print("\n[4c] la musica 'divertido' solo suena donde se pide")
    comprobar("divertido" in sonido.ANIMOS, "'divertido' es un animo que se puede elegir")
    risas = os.path.join(sonido.carpeta_propia("musica"), "Risas.wav")
    tono(risas, 20.0, 330.0)
    sonido.guardar_ficha_propia("musica", "Risas.wav", {
        "fuente": "otra", "titulo": "Risas", "artista": "Alguien",
        "licencia": "CC BY 4.0", "atribucion_requerida": True,
        "texto_credito": "Risas by Alguien - CC BY 4.0", "animo": "divertido"})
    try:
        sin_pedir = sonido.montar_banda(escenas, 160.0)
        comprobar(all(p["archivo"] != "Risas.wav"
                      for t in sin_pedir["tramos"] for p in t["piezas"]),
                  "de relleno no entra: ningun tramo serio la usa")
        arco_d = sonido.arco_del_video(escenas, 160.0)
        arco_d[0]["animo"] = "divertido"
        pedida = sonido.montar_banda(escenas, 160.0, tramos=arco_d)
        igual(pedida["tramos"][0]["piezas"][0]["archivo"], "Risas.wav",
              "un tramo 'divertido' la usa")
        comprobar(all(p["archivo"] != "Risas.wav" for p in pedida["tramos"][1]["piezas"]),
                  "y el tramo de al lado sigue sin ella")
        fijada_d = sonido.montar_banda(escenas, 160.0, fijadas={"1": "Risas.wav"})
        igual(fijada_d["tramos"][1]["piezas"][0]["archivo"], "Risas.wav",
              "fijada a mano entra en cualquier tramo")
    finally:
        for ruta in (risas, sonido._ruta_ficha(risas)):
            if os.path.exists(ruta):
                os.remove(ruta)


def prueba_arco_del_guion():
    print("\n[4d] la musica por lo que se cuenta: tonos, mapa y gancho")
    import mapa_musical  # noqa: PLC0415
    # 60 planos de 10 s: 0-100 s neutro, 100-400 s tenso, 400-430 alegre
    # (un golpe corto), 430-600 s solemne
    escenas = []
    for i in range(60):
        t = i * 10.0
        tono_plano = ("neutro" if t < 100 else "tenso" if t < 400
                      else "alegre" if t < 430 else "solemne")
        escenas.append({"id": f"S{i + 1:03d}", "t_in": t, "t_out": t + 10.0,
                        "narracion": f"frase {i + 1}", "tono": tono_plano})
    hay = {"misterioso": 3, "tension": 1, "sobrio": 2, "melancolico": 2,
           "esperanzador": 2, "divertido": 1}
    arco = sonido.arco_del_guion(escenas, 600.0, hay=hay)
    comprobar(arco[0]["gancho"] and not any(t["gancho"] for t in arco[1:]),
              "la primera seccion es el gancho, y solo ella")
    comprobar(abs(sum(t["fraccion"] for t in arco) - 1.0) < 1e-6
              and all(a["hasta"] == b["desde"] for a, b in zip(arco, arco[1:])),
              "las secciones cubren el video entero, sin huecos")
    comprobar(all(sonido.SECCION_MIN_TONOS_S <= t["hasta"] - t["desde"]
                  <= sonido.SECCION_MAX_S for t in arco),
              f"con los tonos, ninguna corta ni larga ({[round(t['hasta'] - t['desde']) for t in arco]})")
    animos = [t["animo"] for t in arco]
    comprobar("tension" not in animos and "misterioso" in animos,
              "«tenso» con una sola cancion de tension pide la siguiente (misterioso)")
    comprobar("melancolico" in animos, "«solemne» pide melancolico")
    comprobar("divertido" not in animos, "un plano alegre no es un chiste: no pide «divertido»")
    comprobar(all(t["desde_plano"] for t in arco), "cada seccion dice en que plano empieza")

    mapa = mapa_musical.leer_mapa({"secciones": [
        {"desde_plano": "S003", "animo": "misterioso", "porque": "el gancho"},
        {"desde_plano": "S020", "animo": "tension", "porque": "la huelga"},
        {"desde_plano": "S030", "animo": "divertido", "porque": "la excusa de la cerveza"},
        {"desde_plano": "S033", "animo": "tension", "porque": "sigue la huelga"},
        {"desde_plano": "S040", "animo": "oscuro", "porque": "sin canciones"},
        {"desde_plano": "S999", "animo": "sobrio", "porque": "no existe"},
        {"desde_plano": "S045", "animo": "esperanzador", "porque": "el cierre"}]},
        escenas, hay)
    igual(mapa["secciones"][0]["desde_plano"], "S001",
          "el mapa empieza siempre en el primer plano")
    igual(len(mapa["avisos"]), 2, "un animo sin canciones y un plano que no existe se avisan")
    arco_m = sonido.arco_del_guion(escenas, 600.0, mapa=mapa, hay=hay)
    igual([t["animo"] for t in arco_m][:4], ["misterioso", "tension", "divertido", "tension"],
          "con el mapa, sus animos en su orden")
    divertida = next(t for t in arco_m if t["animo"] == "divertido")
    comprobar(sonido.DIVERTIDO_MIN_S <= divertida["hasta"] - divertida["desde"]
              <= sonido.DIVERTIDO_MAX_S, "el momento divertido se queda corto (10-45 s)")
    igual(divertida["por_que"], "la excusa de la cerveza", "con el porque de Claude")
    largo_d = mapa_musical.leer_mapa({"secciones": [
        {"desde_plano": "S001", "animo": "misterioso", "porque": "a"},
        {"desde_plano": "S020", "animo": "divertido", "porque": "demasiado largo"}]},
        escenas, hay)
    arco_l = sonido.arco_del_guion(escenas, 600.0, mapa=largo_d, hay=hay)
    larga = next(t for t in arco_l if t["animo"] == "divertido")
    comprobar(larga["hasta"] - larga["desde"] <= sonido.DIVERTIDO_MAX_S + 10,
              "un «divertido» de minutos se recorta y vuelve lo de antes")
    falla(lambda: mapa_musical.leer_mapa({"secciones": [
        {"desde_plano": "S999", "animo": "misterioso"}]}, escenas, hay),
        "un mapa sin ninguna seccion usable se rechaza (y se usan los tonos)", ValueError)
    texto = mapa_musical.instruccion(escenas, 600.0, hay, "Prueba")
    comprobar("S001 0:00 frase 1" in texto and "- divertido:" in texto
              and "- oscuro" not in texto,
              "a Claude le llega la narracion con su minuto y solo los animos con canciones")

    print("\n[4f] canciones vetadas en un video y animos sin canciones")
    # 19-10: la unica de «tension» (Go Down Swinging) se veto; el mapa la pedia
    hay_sin_tension = {"misterioso": 3, "sobrio": 2, "esperanzador": 2, "divertido": 1}
    arco_v = sonido.arco_del_guion(escenas, 600.0, mapa=mapa, hay=hay_sin_tension)
    comprobar("tension" not in [t["animo"] for t in arco_v]
              and "misterioso" in [t["animo"] for t in arco_v],
              "una seccion de un animo sin canciones pide el mas parecido (tension -> misterioso)")
    planos_v = [{"id": f"S{i + 1:03d}", "t_in": i * 4.0, "t_out": (i + 1) * 4.0}
                for i in range(40)]
    banda_v = sonido.montar_banda(planos_v, 160.0, vetadas=["Paseo.wav"])
    comprobar(all(p["archivo"] != "Paseo.wav"
                  for t in banda_v["tramos"] for p in t["piezas"]),
              "una cancion vetada no suena en ningun tramo")
    fijada_v = sonido.montar_banda(planos_v, 160.0, fijadas={"0": "Paseo.wav"},
                                   vetadas=["Paseo.wav"])
    comprobar(all(p["archivo"] != "Paseo.wav"
                  for t in fijada_v["tramos"] for p in t["piezas"]),
              "ni aunque este fijada")
    comprobar("sobrio" not in sonido.animos_con_canciones(["Calma Total.wav", "Paseo.wav"])
              and sonido.animos_con_canciones().get("sobrio") == 2,
              "las vetadas no cuentan para los animos con canciones")

    print("\n[4e] el gancho: primero las canciones que entran a tope")
    carpeta = sonido.carpeta_propia("musica")
    golpe, susurro = os.path.join(carpeta, "Golpe.wav"), os.path.join(carpeta, "Susurro.wav")
    tono(golpe, 30.0, 200.0)
    t = np.arange(int(sonido.FRECUENCIA * 30.0)) / sonido.FRECUENCIA
    mono = 0.3 * np.sin(2 * np.pi * 260.0 * t) * np.clip(t / 8.0, 0.02, 1.0)
    sonido._escribir(susurro, np.stack([mono, mono], axis=1).astype(np.float32))
    for nombre in ("Golpe.wav", "Susurro.wav"):
        sonido.guardar_ficha_propia("musica", nombre, {
            "fuente": "otra", "titulo": nombre[:-4], "artista": "Alguien",
            "licencia": "CC0", "animo": "misterioso"})
    try:
        fichas = {f["archivo"]: f for f in sonido.propios("musica")}
        comprobar(sonido.fuerza_de_arranque(fichas["Golpe.wav"]) > sonido.GANCHO_MIN_DB
                  > sonido.fuerza_de_arranque(fichas["Susurro.wav"]),
                  "se mide: Golpe entra a tope y Susurro en un susurro")
        # la carpeta del canal es de MP3: el banco los guarda tal cual y se
        # miden sobre su WAV (la primera version leia el MP3 como WAV, fallaba
        # en silencio y el gancho no ordenaba nada)
        golpe_mp3 = os.path.join(carpeta, "GolpeMp3.mp3")
        sonido._ffmpeg("-i", golpe, golpe_mp3)
        sonido.guardar_ficha_propia("musica", "GolpeMp3.mp3", {
            "fuente": "otra", "titulo": "GolpeMp3", "artista": "Alguien",
            "licencia": "CC0", "animo": "sobrio"})
        ficha_mp3 = next(f for f in sonido.propios("musica") if f["archivo"] == "GolpeMp3.mp3")
        comprobar(sonido.fuerza_de_arranque(ficha_mp3) > sonido.GANCHO_MIN_DB,
                  "y con un MP3 tambien se mide")
        planos = [{"id": f"S{i + 1:03d}", "t_in": i * 4.0, "t_out": (i + 1) * 4.0,
                   "tono": "neutro"} for i in range(40)]
        todo_misterio = {"lento": "misterioso", "rapido": "misterioso"}
        tramos = sonido.arco_del_guion(planos, 160.0, hay={"misterioso": 2, "sobrio": 2},
                                       animos={"inicio": todo_misterio, "medio": todo_misterio,
                                               "cierre": todo_misterio})
        banda = sonido.montar_banda(planos, 160.0, tramos=tramos)
        igual(banda["tramos"][0]["piezas"][0]["archivo"], "Golpe.wav",
              "el gancho arranca con la que entra a tope")
        comprobar(banda["tramos"][0].get("gancho"), "y queda dicho que es el gancho")
    finally:
        for ruta in (golpe, susurro, os.path.join(carpeta, "GolpeMp3.mp3")):
            for fichero in (ruta, sonido._ruta_ficha(ruta)):
                if os.path.exists(fichero):
                    os.remove(fichero)


def prueba_efectos_propios():
    print("\n[5] los efectos: lo propio sin clave de Freesound")
    lista = sonido.surtir("transicion_suave")
    igual([f["titulo"] for f in lista], ["Whoosh"], "el efecto propio entra en su papel")
    igual(sonido.surtir("tecla"), [], "un papel sin efectos propios ni Freesound queda vacio, sin error")
    comprobar(sonido.hay_efectos(), "hay efectos (los propios)")


# -------------------------------------------------- Jamendo y Freesound

def prueba_filtros_en_linea():
    print("\n[6] Jamendo y Freesound: solo entra lo monetizable")
    os.environ["JAMENDO_CLIENT_ID"] = "id-de-mentira"
    os.environ["FREESOUND_API_KEY"] = "clave-de-mentira"
    pistas = [
        {"id": 1, "name": "Libre", "artist_name": "A", "duration": 200,
         "audiodownload": "http://x/1.mp3",
         "license_ccurl": "https://creativecommons.org/licenses/by/4.0/"},
        {"id": 2, "name": "No comercial", "artist_name": "B", "duration": 200,
         "audiodownload": "http://x/2.mp3",
         "license_ccurl": "http://creativecommons.org/licenses/by-nc-sa/3.0/"},
        {"id": 3, "name": "Dominio publico", "artist_name": "C", "duration": 200,
         "audiodownload": "http://x/3.mp3",
         "license_ccurl": "http://creativecommons.org/publicdomain/zero/1.0/"},
    ]
    pedidos = []
    original = sonido._pedir_a_jamendo

    def jamendo_falso(params):
        pedidos.append(params)
        return list(pistas)

    sonido._pedir_a_jamendo = jamendo_falso
    try:
        temas = sonido.buscar_musica("sobrio", cuantas=5)
        igual(sorted(t["titulo"] for t in temas), ["Dominio publico", "Libre"],
              "de Jamendo solo pasan CC BY y CC0")
        igual(pedidos[0]["limit"], 200, "y se piden 200 para que quede algo tras filtrar")
        ajustes.guardar({"audio_licencias_seguras": False})
        temas = sonido.buscar_musica("sobrio", cuantas=5)
        comprobar("No comercial" in [t["titulo"] for t in temas],
                  "con el filtro apagado, entra tambien lo NC (bajo tu responsabilidad)")
        ajustes.guardar({"audio_licencias_seguras": True})
    finally:
        sonido._pedir_a_jamendo = original

    class Respuesta:
        status_code = 200

        def json(self):
            return {"results": [
                {"id": 10, "name": "golpe cc0", "duration": 1.0, "username": "u1",
                 "license": "http://creativecommons.org/publicdomain/zero/1.0/",
                 "url": "https://freesound.org/s/10/",
                 "previews": {"preview-hq-mp3": "http://x/10.mp3"}},
                {"id": 11, "name": "golpe nc", "duration": 1.0, "username": "u2",
                 "license": "http://creativecommons.org/licenses/by-nc/3.0/",
                 "url": "https://freesound.org/s/11/",
                 "previews": {"preview-hq-mp3": "http://x/11.mp3"}},
                {"id": 12, "name": "golpe sampling", "duration": 1.0, "username": "u3",
                 "license": "http://creativecommons.org/licenses/sampling+/1.0/",
                 "url": "https://freesound.org/s/12/",
                 "previews": {"preview-hq-mp3": "http://x/12.mp3"}},
            ]}

    original_get = sonido.requests.get
    sonido.requests.get = lambda *a, **k: Respuesta()
    try:
        efectos = sonido.buscar_efectos("whoosh", cuantos=5)
        igual([e["titulo"] for e in efectos], ["golpe cc0"],
              "de Freesound fuera lo NC y el viejo Sampling+")
    finally:
        sonido.requests.get = original_get
        os.environ.pop("JAMENDO_CLIENT_ID", None)
        os.environ.pop("FREESOUND_API_KEY", None)


# ------------------------------------------------------------- creditos

def prueba_creditos():
    print("\n[7] creditos.txt: lo que hay que pegar y tu registro")
    params = {"musica": {"tramos": [
        {"fuente": "propia", "id": "calma-1", "titulo": "Calma Total", "artista": "Autor YT",
         "origen": "youtube_audio_library", "licencia": "YouTube Audio Library"},
        {"fuente": "jamendo", "id": "777", "titulo": "Sunrise", "artista": "Ana",
         "licencia": "https://creativecommons.org/licenses/by/4.0/"},
        {"fuente": "jamendo", "id": "777", "titulo": "Sunrise", "artista": "Ana",
         "licencia": "https://creativecommons.org/licenses/by/4.0/"},
    ]}}
    eventos = [
        {"t": 1.0, "ficha": {"fuente": "freesound", "id": "10", "titulo": "golpe cc0",
                             "autor": "u1", "pagina": "https://freesound.org/s/10/",
                             "licencia": "http://creativecommons.org/publicdomain/zero/1.0/"}},
        {"t": 2.0, "ficha": {"fuente": "freesound", "id": "20", "titulo": "aire",
                             "autor": "u2", "pagina": "https://freesound.org/s/20/",
                             "licencia": "https://creativecommons.org/licenses/by/4.0/"}},
        {"t": 3.0, "ficha": {"fuente": "freesound", "id": "20", "titulo": "aire",
                             "autor": "u2", "pagina": "https://freesound.org/s/20/",
                             "licencia": "https://creativecommons.org/licenses/by/4.0/"}},
    ]
    hecho = sonido.creditos(params, eventos, idioma="en")
    texto = hecho["texto"]
    pegar, _, registro = texto.partition("-" * 60)
    comprobar('"Sunrise" by Ana (Jamendo) — CC BY 4.0 — https://www.jamendo.com/track/777' in pegar,
              "el tema CC BY de Jamendo va con autor, licencia y enlace")
    igual(pegar.count("Sunrise"), 1, "un tema repetido en dos tramos se cita una vez")
    comprobar('"aire" by u2 (Freesound) — CC BY 4.0 — https://freesound.org/s/20/' in pegar,
              "el efecto CC BY tambien")
    comprobar("golpe cc0" not in pegar and "Calma Total" not in pegar,
              "lo CC0 y lo de YouTube sin atribucion no se pega")
    comprobar("golpe cc0" in registro and "Calma Total" in registro,
              "pero sale en tu registro, debajo")
    comprobar(pegar.startswith("Music:"), "en ingles para un canal en ingles")
    igual(hecho["avisos"], [], "sin avisos")

    sin_codigo = {"musica": {"tramos": [
        {"fuente": "propia", "id": "u-1", "titulo": "Beat", "origen": "uppbeat",
         "licencia": "Uppbeat"}]}}
    hecho = sonido.creditos(sin_codigo, [], idioma="es")
    comprobar(any("Uppbeat" in a for a in hecho["avisos"]),
              "Uppbeat sin suscripcion y sin codigo: avisa")
    comprobar(hecho["texto"].startswith("Música:"), "y en espanol para un canal en espanol")
    ajustes.guardar({"uppbeat_suscripcion": True})
    hecho = sonido.creditos(sin_codigo, [], idioma="es")
    igual(hecho["avisos"], [], "con suscripcion, Uppbeat no pide credito")
    comprobar(hecho["texto"].startswith("No hace falta atribución."),
              "y no hay nada que pegar")
    ajustes.guardar({"uppbeat_suscripcion": False})

    nc = {"musica": {"tramos": [{"fuente": "jamendo", "id": "9", "titulo": "Raro",
                                  "licencia": "http://creativecommons.org/licenses/by-nc/3.0/"}]}}
    comprobar(any("no es CC0 ni CC BY" in a for a in sonido.creditos(nc, [])["avisos"]),
              "una licencia NC colada (filtro apagado) se avisa en los creditos")


def main():
    try:
        prueba_licencias()
        prueba_ajustes()
        prueba_carpeta_propia()
        prueba_banda_con_lo_propio()
        prueba_arco_del_guion()
        prueba_efectos_propios()
        prueba_filtros_en_linea()
        prueba_creditos()
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    print()
    if FALLOS:
        print(f"SONIDO SEGURO: {len(FALLOS)} de {HECHAS[0]} comprobaciones fallan")
        for texto in FALLOS:
            print("  -", texto)
        sys.exit(1)
    print(f"SONIDO SEGURO OK: {HECHAS[0]} comprobaciones pasan")


if __name__ == "__main__":
    main()
