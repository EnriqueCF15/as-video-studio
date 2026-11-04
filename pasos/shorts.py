"""
Shorts verticales a partir del video ya montado (fork, 04-11-2026).

Lo que hace, en dos tiempos:

1. ELEGIR (Claude, una llamada barata): se le da el guion partido en FRASES,
   con su minuto y lo que dura cada una, y devuelve 3 shorts. Cada short es una
   MEZCLA DE 2-4 MOMENTOS (tramos de frases seguidas) de distintas partes del
   video que juntos duran 30-40 s: el primero es el gancho y el ultimo cierra.
   Tambien da el titulo que va arriba en pantalla, el titulo de YouTube y la
   descripcion. Decision de Enrique (04-11): mezcla de momentos, 3 por video.

2. MONTAR (ffmpeg, 0 $): para cada short
     - IMAGEN: los trozos del MP4 horizontal, cortados en seco y pegados; el
       plano ENTERO en el centro y arriba/abajo el mismo plano ampliado y
       difuminado (decision de Enrique: no se recorta el dibujo).
     - VOZ: la narracion original de esos mismos trozos, con un fundido de
       milisegundos en cada union para que no chasquee.
     - MUSICA: un tramo SEGUIDO de la cama del video (la de la version activa
       del render), mezclada igual que en el render. Seguida a proposito: si se
       cortara con la voz, la musica saltaria en cada union.
     - TEXTO: el titulo arriba y subtitulos grandes de 1-3 palabras abajo, con
       la palabra que suena en amarillo (quemados: en los shorts casi todo el
       mundo los ve sin sonido).

Los tiempos salen del plan de assets: cada escena trae las `marcas` (inicio y
fin de cada palabra), asi que las frases se cortan por la silaba exacta y los
subtitulos van palabra a palabra. El reloj del MP4 es el del plan menos el
`t_in` de la primera escena (el mismo desfase que usa el render).

Salida: <proyecto>/shorts/vN/ con short1.mp4, short2.mp4, ... y shorts.json
(lo elegido, para poder volver a montar sin preguntar a Claude).

Uso suelto (sin el estudio):
    python pasos/shorts.py <carpeta_proyecto> [--cuantos 3] [--remontar vN]
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cli_claude  # noqa: E402
import comun  # noqa: E402
import medios  # noqa: E402
import p6_assets  # noqa: E402
import p8_render  # noqa: E402
import sonido  # noqa: E402

PASO = "shorts"
#: Elegir momentos es leer un guion y escoger: no necesita Opus. Sonnet alto
#: gasta mucho menos del plan Pro compartido (regla de Enrique: ahorrar Claude).
MODELO = "sonnet"
ESFUERZO = "high"

CUANTOS = 3
DURACION_MIN = 30.0
DURACION_MAX = 40.0
#: Lo que se acepta sin volver a preguntar (las uniones y los margenes mueven
#: un par de segundos lo que Claude suma).
TOLERANCIA_MIN = 26.0
TOLERANCIA_MAX = 46.0
MOMENTOS_MAX = 4

ANCHO, ALTO = 1080, 1920
#: El plano horizontal a lo ancho: 1080 x 608, centrado.
ALTO_PLANO = 608
Y_PLANO = (ALTO - ALTO_PLANO) // 2

#: Aire alrededor de cada trozo de voz: antes de la primera silaba y despues
#: de la ultima, sin comerse la frase de al lado.
MARGEN_ANTES = 0.12
MARGEN_DESPUES = 0.30
FUNDIDO_VOZ = 0.03
FUNDIDO_MUSICA_ENTRADA = 0.4
FUNDIDO_MUSICA_SALIDA = 1.2
#: El final del short: un respiro sobre la ultima imagen antes de cortar.
COLA_S = 0.6

FUENTE = "Arial Black"
FICHERO_FUENTE = "ariblk.ttf"
PALABRAS_POR_GRUPO = 3
LETRAS_POR_GRUPO = 18

FIN_DE_FRASE = re.compile(r"[.!?…]['\"”’)\]]*$")


# ------------------------------------------------------------------ frases

def frases_del_plan(plan):
    """El guion partido en frases con el tiempo de cada palabra. -> list

    Cada frase: {"n", "texto", "t0", "t1", "palabras": [[palabra, ini, fin]]}
    en el reloj del PLAN. Una frase puede cruzar dos escenas: se parte por la
    puntuacion, no por los planos.
    """
    frases, actual = [], []

    def cerrar():
        if actual:
            frases.append({
                "n": len(frases) + 1,
                "texto": " ".join(p[0] for p in actual),
                "t0": actual[0][1], "t1": actual[-1][2],
                "palabras": list(actual)})
            actual.clear()

    for escena in plan.get("escenas") or []:
        palabras = str(escena.get("narracion") or "").split()
        marcas = escena.get("marcas") or []
        if not palabras:
            continue
        if len(marcas) != len(palabras):
            # sin marcas fiables: se reparte el tramo de la escena a partes
            # iguales (solo afecta a los subtitulos de esa escena)
            t_in = float(escena.get("t_in") or 0)
            t_out = float(escena.get("t_out") or t_in)
            paso = (t_out - t_in) / len(palabras)
            marcas = [[t_in + i * paso, t_in + (i + 1) * paso]
                      for i in range(len(palabras))]
        for palabra, (ini, fin) in zip(palabras, marcas):
            actual.append([palabra, float(ini), float(fin)])
            if FIN_DE_FRASE.search(palabra):
                cerrar()
    cerrar()
    return frases


def _mmss(segundos):
    segundos = max(0, int(segundos))
    return f"{segundos // 60}:{segundos % 60:02d}"


# ------------------------------------------------------------------ elegir

INSTRUCCION = """Eres el editor de shorts de un canal de YouTube de historia
narrada (videos largos de ~20 min, voz en off sobre ilustraciones).

Del video «{titulo}» hay que sacar {cuantos} YouTube Shorts verticales. Cada
short es una MEZCLA de 2 a {maximo} MOMENTOS del video. Un momento es un tramo de
FRASES SEGUIDAS (de la frase A a la frase B, ambas incluidas). Los momentos se
pegan uno detras de otro con un corte seco, en el orden que tu digas.

REGLAS DE CADA SHORT
- Duracion total (la suma de lo que duran sus frases): ENTRE {minimo:.0f} Y
  {maximo_s:.0f} SEGUNDOS. Te doy lo que dura cada frase: suma bien.
- El PRIMER momento es el gancho: lo mas sorprendente, raro o intrigante, que
  haga que nadie deslice (una pregunta, un dato que choca, una imagen fuerte).
- El ULTIMO momento cierra: una respuesta, un remate o una frase redonda. Que
  no acabe a medias.
- Que se entienda SOLO, sin haber visto el video: evita frases que dependan de
  algo que no esta en el short («as we saw», «this», «he» sin saber quien...).
- Que al pegarlos suene natural, como si fuera un solo relato.
- Cada short cuenta una idea distinta: no repitas frases entre shorts.
- Los momentos de un short pueden venir de partes muy distintas del video.

ADEMAS, para cada short:
- "titulo_pantalla": el texto que va fijo ARRIBA del video vertical. En el
  idioma del video, maximo 7 palabras, gancho claro (p. ej. una pregunta).
- "titulo_youtube": titulo para subirlo, maximo 90 caracteres, idioma del
  video, que acabe en #shorts.
- "descripcion": 1-2 frases en el idioma del video + 3-5 hashtags.
- "porque": UNA frase EN ESPAÑOL para el dueño del canal: por que este short
  engancha.

LAS FRASES DEL VIDEO ([numero] minuto | segundos que ocupa | texto):
{frases}

FORMA DE LA RESPUESTA (JSON):
{{"shorts": [
  {{"titulo_pantalla": "...", "titulo_youtube": "... #shorts",
    "descripcion": "...", "porque": "...",
    "momentos": [[desde, hasta], [desde, hasta]]}}
]}}
"desde" y "hasta" son NUMEROS de frase (los de los corchetes), desde <= hasta.
"""


def _lista_de_frases(frases):
    """Una linea por frase. Lo que dura cada una INCLUYE la pausa hasta la
    siguiente: es lo que ocupa dentro de un tramo, y asi la suma de Claude
    cuadra con lo que mide el montaje."""
    lineas = []
    for i, f in enumerate(frases):
        siguiente = frases[i + 1]["t0"] if i + 1 < len(frases) else f["t1"] + 0.3
        dura = max(f["t1"] - f["t0"], siguiente - f["t0"])
        lineas.append(f"[{f['n']}] {_mmss(f['t0'])} | {dura:.1f}s | {f['texto']}")
    return "\n".join(lineas)


def _tramos(frases, momentos):
    """Los momentos de Claude como tramos de tiempo, con su margen. -> list

    Cada tramo: {"desde", "hasta", "ini", "fin"} (ini/fin en reloj del plan).
    El margen no se mete en la frase vecina: se queda a mitad del hueco.
    """
    por_n = {f["n"]: f for f in frases}
    tramos = []
    for desde, hasta in momentos:
        primera, ultima = por_n[desde], por_n[hasta]
        ini = primera["t0"] - MARGEN_ANTES
        anterior = por_n.get(desde - 1)
        if anterior:
            ini = max(ini, (anterior["t1"] + primera["t0"]) / 2)
        fin = ultima["t1"] + MARGEN_DESPUES
        siguiente = por_n.get(hasta + 1)
        if siguiente:
            fin = min(fin, (ultima["t1"] + siguiente["t0"]) / 2)
        tramos.append({"desde": desde, "hasta": hasta,
                       "ini": max(0.0, ini), "fin": fin})
    return tramos


def _problemas(frases, shorts):
    """Lo que falla en lo que ha devuelto Claude, en frases para volver a pedir."""
    if not isinstance(shorts, list) or not shorts:
        return ["no hay ninguna entrada en \"shorts\""]
    validos = {f["n"] for f in frases}
    fallos, usadas = [], {}
    for i, short in enumerate(shorts, 1):
        momentos = short.get("momentos") if isinstance(short, dict) else None
        if not isinstance(momentos, list) or not momentos:
            fallos.append(f"short {i}: no tiene momentos")
            continue
        if len(momentos) > MOMENTOS_MAX:
            fallos.append(f"short {i}: tiene {len(momentos)} momentos (maximo {MOMENTOS_MAX})")
        bien = True
        for m in momentos:
            if (not isinstance(m, list) or len(m) != 2
                    or not all(isinstance(x, int) for x in m)
                    or m[0] not in validos or m[1] not in validos or m[0] > m[1]):
                fallos.append(f"short {i}: el momento {m!r} no son dos numeros de frase validos")
                bien = False
        if not bien:
            continue
        for desde, hasta in momentos:
            for n in range(desde, hasta + 1):
                if n in usadas and usadas[n] != i:
                    fallos.append(f"short {i}: la frase {n} ya esta en el short {usadas[n]}")
                usadas.setdefault(n, i)
        dura = sum(t["fin"] - t["ini"] for t in _tramos(frases, momentos))
        if not TOLERANCIA_MIN <= dura <= TOLERANCIA_MAX:
            fallos.append(f"short {i}: dura {dura:.1f} s y tiene que durar entre "
                          f"{DURACION_MIN:.0f} y {DURACION_MAX:.0f} s")
        if not str(short.get("titulo_pantalla") or "").strip():
            fallos.append(f"short {i}: falta titulo_pantalla")
    return fallos


def elegir(proyecto, frases, cuantos=CUANTOS, avance=None):
    """Pide a Claude los shorts y los valida (una vuelta de correccion). -> list"""
    config = medios.leer_json(proyecto.ruta("proyecto.json"), {}) or {}
    titulo = config.get("nombre") or config.get("titulo") or os.path.basename(proyecto.raiz)
    instruccion = INSTRUCCION.format(
        titulo=titulo, cuantos=cuantos, maximo=MOMENTOS_MAX,
        minimo=DURACION_MIN, maximo_s=DURACION_MAX,
        frases=_lista_de_frases(frases))
    datos = cli_claude.escribiendo(
        instruccion, modelo=MODELO, esfuerzo=ESFUERZO, base_tiempo_s=300,
        avance=avance, para="los shorts", que="los shorts")
    shorts = (datos or {}).get("shorts")
    fallos = _problemas(frases, shorts)
    if fallos:
        correccion = (instruccion + "\n\nYA LO INTENTASTE Y ESTO ESTABA MAL:\n- "
                      + "\n- ".join(fallos)
                      + "\n\nTu respuesta anterior:\n"
                      + json.dumps(datos, ensure_ascii=False)
                      + "\n\nDevuelve los shorts completos otra vez, corregidos.")
        datos = cli_claude.escribiendo(
            correccion, modelo=MODELO, esfuerzo=ESFUERZO, base_tiempo_s=300,
            avance=avance, para="los shorts", que="los shorts")
        shorts = (datos or {}).get("shorts")
        fallos = _problemas(frases, shorts)
    if not isinstance(shorts, list):
        raise RuntimeError("Claude no ha devuelto shorts legibles")
    # Lo que sigue mal tras la segunda vuelta: se quitan los shorts rotos y se
    # monta el resto (mejor 2 buenos que ninguno). La duracion fuera de rango
    # NO tumba un short: se monta y se avisa.
    buenos = []
    for i, short in enumerate(shorts, 1):
        propios = [f for f in fallos if f.startswith(f"short {i}:")]
        rotos = [f for f in propios if "dura " not in f and "ya esta en" not in f]
        if rotos:
            continue
        short["avisos"] = propios
        buenos.append(short)
    if not buenos:
        raise RuntimeError("ningun short valido: " + "; ".join(fallos[:5]))
    return buenos[:cuantos]


# ------------------------------------------------------------------ montar

def _correr(orden, cwd=None, que="ffmpeg"):
    proceso = subprocess.run(orden, capture_output=True, text=True, cwd=cwd,
                             timeout=1800, **medios.SIN_VENTANA)
    if proceso.returncode != 0:
        raise RuntimeError(f"{que} fallo: {(proceso.stderr or '')[-600:]}")
    return proceso


def _ass_tiempo(segundos):
    segundos = max(0.0, segundos)
    centesimas = int(round(segundos * 100))
    h, resto = divmod(centesimas, 360000)
    m, resto = divmod(resto, 6000)
    s, c = divmod(resto, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _ass_texto(texto):
    return (str(texto).replace("\\", "\\\\").replace("{", "(").replace("}", ")")
            .replace("\n", " "))


def _grupos(palabras):
    """Las palabras de un tramo en grupos cortos para el subtitulo."""
    grupos, actual = [], []
    for palabra in palabras:
        letras = sum(len(p[0]) + 1 for p in actual) + len(palabra[0])
        if actual and (len(actual) >= PALABRAS_POR_GRUPO or letras > LETRAS_POR_GRUPO):
            grupos.append(actual)
            actual = []
        actual.append(palabra)
        if re.search(r"[,.;:!?…]['\"”’)]*$", palabra[0]):
            grupos.append(actual)
            actual = []
    if actual:
        grupos.append(actual)
    return grupos


def _ass(short, piezas, duracion):
    """Titulo arriba + subtitulos palabra a palabra abajo (formato ASS)."""
    cab = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {ANCHO}
PlayResY: {ALTO}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Titulo,{FUENTE},76,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,6,4,5,70,70,0,1
Style: Sub,{FUENTE},74,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,7,4,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lineas = []
    titulo = _ass_texto(short.get("titulo_pantalla") or "").upper()
    # el titulo, centrado en la franja de arriba (entre la interfaz de YouTube
    # y el plano)
    lineas.append(f"Dialogue: 1,{_ass_tiempo(0)},{_ass_tiempo(duracion)},Titulo,,0,0,0,,"
                  f"{{\\pos({ANCHO // 2},{Y_PLANO // 2 + 40})\\fad(250,0)}}{titulo}")
    y_sub = Y_PLANO + ALTO_PLANO + 170
    for pieza in piezas:
        desplaza = pieza["inicio_short"] - pieza["ini"]
        grupos = _grupos(pieza["palabras"])
        for g, grupo in enumerate(grupos):
            fin_grupo = (grupos[g + 1][0][1] if g + 1 < len(grupos)
                         else pieza["fin"])
            for w, palabra in enumerate(grupo):
                ini = palabra[1]
                fin = grupo[w + 1][1] if w + 1 < len(grupo) else fin_grupo
                if w == 0 and g == 0:
                    ini = max(pieza["ini"], ini)
                texto = " ".join(
                    ("{\\c&H00E5FF&}" + _ass_texto(p[0]).upper() + "{\\c&HFFFFFF&}")
                    if k == w else _ass_texto(p[0]).upper()
                    for k, p in enumerate(grupo))
                a = max(pieza["inicio_short"], ini + desplaza)
                b = min(pieza["inicio_short"] + pieza["fin"] - pieza["ini"], fin + desplaza)
                if b - a < 0.02:
                    continue
                lineas.append(f"Dialogue: 0,{_ass_tiempo(a)},{_ass_tiempo(b)},Sub,,0,0,0,,"
                              f"{{\\pos({ANCHO // 2},{y_sub})}}{texto}")
    return cab + "\n".join(lineas) + "\n"


def _al_fotograma(t, fps):
    return round(t * fps) / fps


def montar_short(short, frases, video, voz, cama, desfase, fps, carpeta, nombre,
                 musica_db=0.0):
    """Monta un short vertical en <carpeta>/<nombre>.mp4. -> dict"""
    trabajo = os.path.join(carpeta, f"_{nombre}")
    os.makedirs(trabajo, exist_ok=True)
    por_n = {f["n"]: f for f in frases}
    piezas, inicio = [], 0.0
    for tramo in _tramos(frases, short["momentos"]):
        # al fotograma: la imagen se corta por fotogramas y la voz por
        # muestras; sin esto cada union desfasaria unos milisegundos
        ini = _al_fotograma(tramo["ini"] - desfase, fps) + desfase
        fin = _al_fotograma(tramo["fin"] - desfase, fps) + desfase
        palabras = [p for n in range(tramo["desde"], tramo["hasta"] + 1)
                    for p in por_n[n]["palabras"]]
        piezas.append({"ini": ini, "fin": fin, "inicio_short": inicio,
                       "palabras": palabras, "desde": tramo["desde"],
                       "hasta": tramo["hasta"]})
        inicio += fin - ini
    duracion_voz = inicio
    duracion = duracion_voz + COLA_S

    # ---- voz: los trozos de la narracion, con fundidos cortos en las uniones
    orden = [medios.ffmpeg(), "-y", "-loglevel", "error"]
    filtros, etiquetas = [], []
    for i, pieza in enumerate(piezas):
        largo = pieza["fin"] - pieza["ini"]
        orden += ["-ss", f"{pieza['ini']:.3f}", "-t", f"{largo:.3f}", "-i", voz]
        filtros.append(
            f"[{i}:a]aresample={sonido.FRECUENCIA},aformat=channel_layouts=stereo,"
            f"apad,atrim=0:{largo:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={FUNDIDO_VOZ},"
            f"afade=t=out:st={max(0.0, largo - FUNDIDO_VOZ * 2):.3f}:d={FUNDIDO_VOZ * 2}[v{i}]")
        etiquetas.append(f"[v{i}]")
    filtros.append("".join(etiquetas) + f"concat=n={len(piezas)}:v=0:a=1,"
                   f"apad,atrim=0:{duracion:.3f}[voz]")
    ruta_voz = os.path.join(trabajo, "voz.wav")
    _correr(orden + ["-filter_complex", ";".join(filtros), "-map", "[voz]",
                     ruta_voz], que="ffmpeg (voz)")

    # ---- musica: un tramo SEGUIDO de la cama, desde donde empieza el short
    ruta_musica = None
    if cama and os.path.exists(cama):
        ruta_musica = os.path.join(trabajo, "musica.wav")
        desde = max(0.0, piezas[0]["ini"] - desfase)
        _correr([medios.ffmpeg(), "-y", "-loglevel", "error",
                 "-stream_loop", "-1", "-ss", f"{desde:.3f}", "-i", cama,
                 "-t", f"{duracion:.3f}", "-af",
                 f"afade=t=in:d={FUNDIDO_MUSICA_ENTRADA},"
                 f"afade=t=out:st={max(0.0, duracion - FUNDIDO_MUSICA_SALIDA):.3f}"
                 f":d={FUNDIDO_MUSICA_SALIDA}",
                 ruta_musica], que="ffmpeg (musica)")

    # ---- mezcla: el mismo grafo que el render (voz + cama agachada + master)
    ruta_mezcla = os.path.join(trabajo, "mezcla.wav")
    base = [medios.ffmpeg(), "-y", "-loglevel", "error",
            "-f", "lavfi", "-t", f"{duracion:.3f}",
            "-i", f"anullsrc=r={sonido.FRECUENCIA}:cl=stereo", "-i", ruta_voz]
    if ruta_musica:
        base += ["-i", ruta_musica]

        def grafo(medida=None):
            return sonido.filtro_de_mezcla(
                True, False, duracion, sonido.MUSICA_LUFS, ya_normalizada=True,
                ajuste_db=musica_db, master=True, medida=medida)
        medida = p8_render._medir_mezcla(
            base + ["-filter_complex", grafo(), "-map", "[salida]", "-f", "null", "-"])
        _correr(base + ["-filter_complex", grafo(medida), "-map", "[salida]",
                        ruta_mezcla], que="ffmpeg (mezcla)")
    else:
        medida = p8_render._medir_mezcla(
            base + ["-af", sonido.filtro_master(), "-map", "1:a", "-f", "null", "-"])
        _correr(base + ["-map", "1:a", "-af", sonido.filtro_master(medida),
                        ruta_mezcla], que="ffmpeg (mezcla)")

    # ---- textos
    with open(os.path.join(trabajo, "textos.ass"), "w", encoding="utf-8") as fh:
        fh.write(_ass(short, piezas, duracion))
    fuente = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", FICHERO_FUENTE)
    if os.path.exists(fuente):
        shutil.copy2(fuente, os.path.join(trabajo, FICHERO_FUENTE))

    # ---- imagen: trozos del MP4 + plano entero sobre fondo difuminado
    orden = [medios.ffmpeg(), "-y", "-loglevel", "error"]
    filtros, etiquetas = [], []
    for i, pieza in enumerate(piezas):
        largo = pieza["fin"] - pieza["ini"]
        orden += ["-ss", f"{pieza['ini'] - desfase:.3f}", "-t", f"{largo:.3f}",
                  "-i", video]
        filtros.append(f"[{i}:v]fps={fps},setpts=PTS-STARTPTS,"
                       f"trim=0:{largo:.3f},setsar=1[c{i}]")
        etiquetas.append(f"[c{i}]")
    n = len(piezas)
    orden += ["-i", ruta_mezcla]
    filtros.append("".join(etiquetas) + f"concat=n={n}:v=1:a=0,"
                   f"tpad=stop_mode=clone:stop_duration={COLA_S:.3f}[seguido]")
    filtros.append("[seguido]split=2[a][b]")
    filtros.append(f"[a]scale=-2:{ALTO},crop={ANCHO}:{ALTO},gblur=sigma=28,"
                   f"eq=brightness=-0.12:saturation=0.85[fondo]")
    filtros.append(f"[b]scale={ANCHO}:{ALTO_PLANO}[plano]")
    filtros.append(f"[fondo][plano]overlay=0:{Y_PLANO},"
                   f"ass=textos.ass:fontsdir=.,format=yuv420p[salida]")
    destino = os.path.join(carpeta, f"{nombre}.mp4")
    temporal = os.path.join(trabajo, "short.mp4")
    _correr(orden + ["-filter_complex", ";".join(filtros),
                     "-map", "[salida]", "-map", f"{n}:a",
                     "-r", str(fps), "-c:v", "libx264", "-preset", "medium",
                     "-crf", "19", "-c:a", "aac", "-b:a", "192k",
                     "-t", f"{duracion:.3f}", "-movflags", "+faststart",
                     os.path.abspath(temporal)],
            cwd=trabajo, que="ffmpeg (video)")
    medios.reemplazar(temporal, destino)
    shutil.rmtree(trabajo, ignore_errors=True)
    return {"mp4": destino, "duracion": round(duracion, 2),
            "piezas": [{"desde": p["desde"], "hasta": p["hasta"],
                        "minuto": _mmss(p["ini"] - desfase),
                        "segundos": round(p["fin"] - p["ini"], 2)} for p in piezas],
            "con_musica": bool(ruta_musica)}


# ------------------------------------------------------------------ juntar

def _fuentes(proyecto, estado=None):
    """El MP4, la voz, la cama y el plan del video montado. -> dict"""
    if proyecto.version_activa("render") is None:
        raise RuntimeError("todavia no hay video montado: monta el video antes de hacer shorts")
    carpeta = proyecto.ruta_paso("render")
    video = os.path.join(carpeta, "video.mp4")
    if not os.path.exists(video):
        raise RuntimeError(f"no encuentro el video montado en {carpeta}")
    plan = p6_assets.plan_actual(proyecto, "assets", estado=estado)
    if not plan.get("escenas"):
        raise RuntimeError("no encuentro el plan de planos del video")
    voz = medios.salida_de(proyecto, "voz", claves=("wav", "audio", "narracion"),
                           patrones=(r"narracion\.wav", r".*\.wav"), estado=estado)
    if not voz or not os.path.exists(voz):
        raise RuntimeError("no encuentro la voz del video")
    cama = os.path.join(carpeta, "cama.wav")
    fps = int(round(float(plan.get("fps") or 25)))
    try:
        fps = int(round(eval_fps(video))) or fps
    except Exception:                                          # noqa: BLE001
        pass
    estado_render = {}
    if estado is not None:
        try:
            estado_render = estado.params("render") or {}
        except Exception:                                      # noqa: BLE001
            estado_render = {}
    return {"video": video, "voz": voz, "plan": plan,
            "cama": cama if os.path.exists(cama) else None,
            "desfase": float(plan["escenas"][0].get("t_in") or 0.0), "fps": fps,
            "musica_db": float(estado_render.get("musica_db") or 0.0),
            "version_render": proyecto.version_activa("render")}


def eval_fps(video):
    """Los fotogramas por segundo REALES del MP4 (el plan puede decir otros)."""
    proceso = subprocess.run(
        [medios.ffprobe(), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", video],
        capture_output=True, text=True, timeout=60, **medios.SIN_VENTANA)
    texto = (proceso.stdout or "").strip().splitlines()[0].strip(" ,")
    num, _, den = texto.partition("/")
    return float(num) / float(den or 1)


def _carpeta_nueva(proyecto):
    raiz = proyecto.ruta("shorts")
    os.makedirs(raiz, exist_ok=True)
    numeros = [int(m.group(1)) for m in
               (re.fullmatch(r"v(\d+)", x) for x in os.listdir(raiz)) if m]
    destino = os.path.join(raiz, f"v{max(numeros, default=0) + 1}")
    os.makedirs(destino)
    return destino


def listar(proyecto):
    """Las tandas de shorts ya hechas, la ultima primero. -> list"""
    raiz = proyecto.ruta("shorts")
    if not os.path.isdir(raiz):
        return []
    tandas = []
    for nombre in os.listdir(raiz):
        if not re.fullmatch(r"v\d+", nombre):
            continue
        datos = medios.leer_json(os.path.join(raiz, nombre, "shorts.json"), None)
        if isinstance(datos, dict):
            datos["version"] = nombre
            tandas.append(datos)
    tandas.sort(key=lambda d: int(d["version"][1:]), reverse=True)
    return tandas


def ejecutar(proyecto, cuantos=CUANTOS, avisar=None, remontar=None, estado=None,
             avance=None):
    """Elige (o reutiliza) los momentos y monta los shorts. -> dict

    `remontar` = "vN": vuelve a montar lo elegido en esa tanda SIN preguntar a
    Claude (p. ej. tras volver a montar el video o cambiar la musica).
    """
    def aviso(valor, mensaje):
        if avisar:
            avisar(valor, mensaje)
        else:
            print(f"[shorts] {int(valor * 100):3d}% {mensaje}", flush=True)

    fuentes = _fuentes(proyecto, estado)
    frases = frases_del_plan(fuentes["plan"])
    if remontar:
        previa = medios.leer_json(proyecto.ruta("shorts", remontar, "shorts.json"), None)
        if not isinstance(previa, dict) or not previa.get("shorts"):
            raise RuntimeError(f"no encuentro la tanda de shorts {remontar}")
        elegidos = [{k: v for k, v in s.items() if k not in ("mp4", "ruta", "duracion",
                                                              "piezas", "con_musica")}
                    for s in previa["shorts"]]
        aviso(0.05, f"vuelvo a montar los {len(elegidos)} shorts de {remontar}")
    else:
        aviso(0.03, f"Claude ({MODELO}) está eligiendo los mejores momentos "
                    f"de {len(frases)} frases…")
        elegidos = elegir(proyecto, frases, cuantos, avance=avance)

    carpeta = _carpeta_nueva(proyecto)
    hechos = []
    for i, short in enumerate(elegidos, 1):
        aviso(0.15 + 0.8 * (i - 1) / len(elegidos),
              f"montando el short {i} de {len(elegidos)}: «{short.get('titulo_pantalla', '')}»")
        nombre = f"short{i}"
        resultado = montar_short(short, frases, fuentes["video"], fuentes["voz"],
                                 fuentes["cama"], fuentes["desfase"], fuentes["fps"],
                                 carpeta, nombre, musica_db=fuentes["musica_db"])
        short = dict(short)
        short.update({k: v for k, v in resultado.items() if k != "mp4"})
        short["ruta"] = os.path.relpath(resultado["mp4"], proyecto.raiz).replace("\\", "/")
        short["textos"] = [por["texto"] for por in frases
                           for m in short["momentos"] if m[0] <= por["n"] <= m[1]]
        hechos.append(short)
    datos = {"fecha": time.strftime("%Y-%m-%d %H:%M"), "modelo": MODELO,
             "render": f"v{fuentes['version_render']}", "remontado_de": remontar,
             "carpeta": carpeta, "shorts": hechos}
    comun.escribir_json(os.path.join(carpeta, "shorts.json"), datos)
    # una ficha legible para subirlos (titulo, descripcion) junto a los MP4
    with open(os.path.join(carpeta, "para_subir.txt"), "w", encoding="utf-8") as fh:
        for i, s in enumerate(hechos, 1):
            fh.write(f"=== short{i}.mp4 ({s['duracion']:.0f} s) ===\n"
                     f"Título: {s.get('titulo_youtube', '')}\n\n"
                     f"Descripción:\n{s.get('descripcion', '')}\n\n"
                     f"Por qué: {s.get('porque', '')}\n\n")
    aviso(1.0, f"{len(hechos)} shorts listos en {carpeta}")
    datos["version"] = os.path.basename(carpeta)
    return datos


def _main(argv=None):
    import argparse                                           # noqa: PLC0415
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from nucleo.proyecto import Proyecto                       # noqa: PLC0415
    parser = argparse.ArgumentParser(description="Shorts verticales del video montado")
    parser.add_argument("proyecto")
    parser.add_argument("--cuantos", type=int, default=CUANTOS)
    parser.add_argument("--remontar", default=None)
    parser.add_argument("--elegidos", default=None,
                        help="JSON con shorts ya elegidos (para probar sin Claude)")
    args = parser.parse_args(argv)
    proyecto = Proyecto(args.proyecto)
    if args.elegidos:
        fuentes = _fuentes(proyecto)
        frases = frases_del_plan(fuentes["plan"])
        elegidos = medios.leer_json(args.elegidos, {}).get("shorts") or []
        print("problemas:", _problemas(frases, elegidos))
        carpeta = _carpeta_nueva(proyecto)
        for i, short in enumerate(elegidos, 1):
            print(montar_short(short, frases, fuentes["video"], fuentes["voz"],
                               fuentes["cama"], fuentes["desfase"], fuentes["fps"],
                               carpeta, f"short{i}", musica_db=fuentes["musica_db"]))
        return
    print(json.dumps(ejecutar(proyecto, args.cuantos, remontar=args.remontar),
                     ensure_ascii=False, indent=1)[:3000])


if __name__ == "__main__":
    _main()
