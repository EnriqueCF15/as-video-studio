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

    tono(os.path.join(musica, "Calma Total.wav"), 6.0, 190.0)
    nuevo = sonido.propios("musica")[0]["id"]
    comprobar(nuevo != tema["id"], "si el fichero cambia, cambia el id (no suena la copia vieja)")


def prueba_banda_con_lo_propio():
    print("\n[4] la banda sonora con la carpeta propia, sin Jamendo")
    escenas = [{"id": f"S{i:03d}", "t_in": i * 4.0, "t_out": (i + 1) * 4.0} for i in range(40)]
    banda = sonido.montar_banda(escenas, 160.0)
    tramos = banda["tramos"]
    comprobar(len(tramos) >= 1, f"monta {len(tramos)} tramo(s)")
    comprobar(all(t["fuente"] == "propia" for t in tramos), "todos de la carpeta propia")
    comprobar(all(t.get("archivo") == "Calma Total.wav" for t in tramos),
              "con un solo tema propio, se REPITE en vez de dejar tramos mudos")
    comprobar(all(float(t.get("duracion") or 0) > 5 for t in tramos),
              "la duracion del propio se mide (la cama la necesita)")
    destino = os.path.join(BASE, "cama.wav")
    hecho, faltan = sonido.construir_cama(banda, 20.0, destino)
    comprobar(hecho and os.path.exists(destino) and not faltan,
              "la cama se construye desde el banco sin salir a la red")


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
