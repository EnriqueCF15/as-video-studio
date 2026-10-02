"""
Alineador local: marcas de tiempo por palabra a partir de un WAV y su texto.

Lo necesitan los proveedores de voz que NO devuelven marcas (Google: Gemini TTS
y Chirp 3 HD; Kokoro), y sirve de verificacion de los que si. Coste: cero, corre
en esta maquina (la GPU si hay CUDA, la CPU si no).

Devuelve el MISMO formato que daba Cartesia, [{"w", "s", "e"}] en segundos, con
una diferencia que importa: "w" es la palabra DEL GUION, con su puntuacion
pegada, y no la que oyo el reconocedor. `guion/segmentar.tipo_de_corte` mira el
ultimo caracter de "w" para decidir donde se puede cortar un plano; con las
palabras de Whisper («one thousand» donde el guion dice «$1,000», o sin la coma)
los cortes caerian en otro sitio.

Como se hace
------------
1. faster-whisper transcribe el audio: frases con tiempos aproximados.
2. WhisperX alinea esas frases con wav2vec2: cada palabra OIDA con su tiempo
   preciso. Por frases y no de golpe: wav2vec2 sobre minutos de audio no cabe en
   4 GB de VRAM.
3. Se emparejan las palabras del guion con las oidas (difflib). Lo que coincide
   se queda los tiempos tal cual; un tramo que suena distinto de lo escrito (un
   numero, una contraccion) reparte el tiempo de lo oido entre lo escrito, por
   longitud; y una palabra que no se oyo se interpola entre sus vecinas.

La COBERTURA es la parte del guion que se oyo tal cual. Un TTS generativo puede
saltarse o inventarse una frase y no lo dice nadie: aqui sale como cobertura
baja y con la lista de lo que falta.

Por que en un proceso aparte
----------------------------
torch y los dos modelos son ~2 GB de RAM y lo que haga falta de VRAM, y el
servicio vive horas en una maquina con 16 GB al 93 %. El proceso hijo los carga,
alinea y muere, y con el se va todo. Ademas aisla un fallo de CUDA: si la GPU no
arranca, el hijo lo dice y se reintenta en CPU.

Contrato: este motor no importa nada de la aplicacion. Los modelos se descargan
a ESTUDIO_MODELOS (HF_HOME y TORCH_HOME), que arrancar.ps1 pone en E:.

Uso suelto:
    python alinear.py --wav toma.wav --texto guion.txt --idioma en --out marcas.json
"""
import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

#: Modelo de faster-whisper. Para alinear un texto que ya se conoce no hace falta
#: uno grande: solo tiene que oir las frases, el tiempo fino lo da wav2vec2.
#: «small» cabe holgado en 4 GB junto al de alineacion.
MODELO_POR_DEFECTO = {"en": "small.en"}
MODELO_GENERICO = "small"

#: Lo que se le da al proceso hijo antes de darlo por colgado. Una toma de 25 min
#: tarda ~2 min en la RTX 3050 y ~10 en CPU.
TIEMPO_MAXIMO_S = 45 * 60

#: Prefijo de las lineas de progreso que escribe el hijo por stdout.
PREFIJO_PROGRESO = "@@progreso "

#: Sin ventana negra en Windows por cada alineado (igual que medios.SIN_VENTANA,
#: que no se importa: contrato de motor).
SIN_VENTANA = {}
if os.name == "nt":
    SIN_VENTANA = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


def carpeta_modelos():
    """Donde se descargan los modelos. Fuera de C: si lo dice ESTUDIO_MODELOS."""
    return os.environ.get("ESTUDIO_MODELOS") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "modelos")


def modelo_para(idioma, modelo=None):
    return (modelo or os.environ.get("ESTUDIO_ALINEADOR_MODELO")
            or MODELO_POR_DEFECTO.get(str(idioma or "").lower(), MODELO_GENERICO))


# ------------------------------------------------------------- emparejamiento
#
# Puro Python y sin torch: es lo que se prueba en seco.

def normalizar(palabra):
    """Forma de comparar: minusculas y sin puntuacion (tildes y enes se quedan).

    El apostrofo tambien se va, a los dos lados: «don't» y «dont» son la misma
    palabra para quien escucha.
    """
    return re.sub(r"[^\w]", "", str(palabra).lower()).replace("_", "")


def tokens_del_guion(texto):
    """Las palabras del guion tal como se escribieron, puntuacion incluida.

    Un token que es SOLO puntuacion (un guion largo suelto, unas comillas) no se
    puede oir: se pega al anterior, porque lo que aporta es donde se corta.
    """
    salida = []
    for crudo in str(texto or "").split():
        if normalizar(crudo):
            salida.append(crudo)
        elif salida:
            salida[-1] += crudo
    return salida


def _rellenar_huecos(marcas, inicio=0.0, fin=None):
    """Da tiempo a las entradas que no lo tienen, interpolando entre vecinas.

    `marcas` es una lista de dicts con "s"/"e" que pueden ser None. Las que no
    tienen se reparten a partes iguales el hueco entre la anterior y la
    siguiente que si tienen.
    """
    n = len(marcas)
    i = 0
    while i < n:
        if marcas[i].get("s") is not None and marcas[i].get("e") is not None:
            i += 1
            continue
        j = i
        while j < n and (marcas[j].get("s") is None or marcas[j].get("e") is None):
            j += 1
        desde = marcas[i - 1]["e"] if i > 0 else inicio
        hasta = marcas[j]["s"] if j < n else (fin if fin is not None else desde)
        hasta = max(hasta, desde)
        paso = (hasta - desde) / (j - i)
        for k in range(i, j):
            marcas[k]["s"] = desde + paso * (k - i)
            marcas[k]["e"] = desde + paso * (k - i + 1)
        i = j
    return marcas


def emparejar(guion, oidas, duracion=None):
    """Palabras del guion con los tiempos de las oidas.

    guion: [str] tokens del guion (ver tokens_del_guion).
    oidas: [{"w","s","e"}] lo que oyo el reconocedor, en orden; "s"/"e" pueden
           faltar en alguna (wav2vec2 no alinea digitos ni simbolos).
    Devuelve {"palabras": [{"w","s","e"}], "cobertura": 0..1,
              "faltan": [str], "sobran": int}.
    """
    oidas = [dict(o) for o in oidas if normalizar(o.get("w"))]
    oidas = _rellenar_huecos(oidas, 0.0, duracion)
    a = [normalizar(t) for t in guion]
    b = [normalizar(o["w"]) for o in oidas]
    marcas = [{"w": t, "s": None, "e": None} for t in guion]
    iguales, faltan, sobran = 0, [], 0

    comparador = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for op, i1, i2, j1, j2 in comparador.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                marcas[i1 + k]["s"] = oidas[j1 + k]["s"]
                marcas[i1 + k]["e"] = oidas[j1 + k]["e"]
            iguales += i2 - i1
        elif op == "replace":
            # Lo escrito y lo oido son el mismo tramo dicho de otra forma: se
            # reparte el tiempo de lo oido entre lo escrito, por longitud.
            desde, hasta = oidas[j1]["s"], oidas[j2 - 1]["e"]
            pesos = [max(1, len(a[k])) for k in range(i1, i2)]
            total = float(sum(pesos))
            reloj = desde
            for k, peso in zip(range(i1, i2), pesos):
                largo = (hasta - desde) * peso / total
                marcas[k]["s"], marcas[k]["e"] = reloj, reloj + largo
                reloj += largo
            # Si lo oido son CIFRAS, es el guion dicho tal cual y escrito por
            # Whisper en numeros («one thousand dollars» -> «$1,000»): no es
            # algo que la voz se haya saltado, y contarlo daria falsas alarmas
            # en cualquier video de dinero.
            if all(re.search(r"\d", str(o["w"])) for o in oidas[j1:j2]):
                iguales += i2 - i1
            else:
                faltan.extend(guion[i1:i2])
                sobran += j2 - j1
        elif op == "delete":
            faltan.extend(guion[i1:i2])      # se quedan sin tiempo: se interpolan
        elif op == "insert":
            sobran += j2 - j1

    _rellenar_huecos(marcas, 0.0, duracion)
    # Monotonas y sin solaparse: el montaje da por hecho que el tiempo avanza.
    reloj = 0.0
    for m in marcas:
        s = max(float(m["s"]), reloj)
        e = max(float(m["e"]), s)
        m["s"], m["e"] = round(s, 3), round(e, 3)
        reloj = e
    return {"palabras": marcas,
            "cobertura": round(iguales / len(guion), 4) if guion else 1.0,
            "faltan": faltan, "sobran": sobran}


def frases_del_guion(guion, frases):
    """Reparte las palabras del guion entre las frases que oyo Whisper.

    frases: [{"start","end","text"}]. Devuelve ([[tokens] por frase],
    {"cobertura","faltan","sobran"}). Se reutiliza `emparejar` en el espacio
    de los INDICES: cada palabra oida «dura» de j a j+1, asi que el instante
    que le toca a una palabra del guion dice de que palabra oida -- y de que
    frase -- sale.
    """
    oidas, frase_de = [], []
    for indice, frase in enumerate(frases):
        for palabra in str(frase.get("text") or "").split():
            if normalizar(palabra):
                oidas.append({"w": palabra, "s": float(len(oidas)),
                              "e": float(len(oidas)) + 1.0})
                frase_de.append(indice)
    reparto = [[] for _ in frases]
    if not oidas:
        if frases:
            reparto[0] = list(guion)
        return reparto, {"cobertura": 0.0 if guion else 1.0,
                         "faltan": list(guion), "sobran": 0}
    casado = emparejar(guion, oidas, float(len(oidas)))
    for token, marca in zip(guion, casado["palabras"]):
        j = min(len(frase_de) - 1, int((marca["s"] + marca["e"]) / 2.0))
        reparto[frase_de[j]].append(token)
    return reparto, {k: casado[k] for k in ("cobertura", "faltan", "sobran")}


# ---------------------------------------------------------------- proceso hijo

def _preparar_entorno():
    """Modelos a ESTUDIO_MODELOS, y las DLL de CUDA de torch a la vista."""
    raiz = carpeta_modelos()
    os.makedirs(raiz, exist_ok=True)
    os.environ.setdefault("HF_HOME", os.path.join(raiz, "huggingface"))
    os.environ.setdefault("TORCH_HOME", os.path.join(raiz, "torch"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _avisar_hijo(fraccion, mensaje):
    print(f"{PREFIJO_PROGRESO}{fraccion:.3f} {mensaje}", flush=True)


def _cargar_whisper(modelo, dispositivo):
    """(WhisperModel, dispositivo, compute_type), cayendo a CPU si CUDA no va."""
    import torch
    if os.name == "nt":
        # ctranslate2 busca cuBLAS y cuDNN en el PATH; torch ya las trae.
        lib = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.isdir(lib):
            os.add_dll_directory(lib)
            os.environ["PATH"] = lib + os.pathsep + os.environ.get("PATH", "")
    from faster_whisper import WhisperModel

    quiere_gpu = dispositivo in (None, "", "auto", "cuda") and torch.cuda.is_available()
    if quiere_gpu:
        for tipo in ("float16", "int8_float16"):
            try:
                return WhisperModel(modelo, device="cuda", compute_type=tipo), "cuda", tipo
            except Exception as fallo:                          # noqa: BLE001
                _avisar_hijo(0.05, f"CUDA con {tipo} no va ({fallo}); probando otra")
    return WhisperModel(modelo, device="cpu", compute_type="int8"), "cpu", "int8"


def trabajar(entrada):
    """Lo que hace el proceso hijo. -> dict de salida (ver `alinear`)."""
    _preparar_entorno()
    arranque = time.time()
    idioma = str(entrada.get("idioma") or "en").lower()
    modelo = modelo_para(idioma, entrada.get("modelo"))

    _avisar_hijo(0.02, "cargando el reconocedor")
    import whisperx
    whisper, dispositivo, tipo = _cargar_whisper(modelo, entrada.get("dispositivo"))
    audio = whisperx.load_audio(entrada["wav"])
    duracion = len(audio) / 16000.0

    _avisar_hijo(0.10, f"escuchando {duracion / 60:.1f} min en {dispositivo}")
    trozos, info = whisper.transcribe(
        audio, language=idioma, beam_size=1, vad_filter=False,
        condition_on_previous_text=False)
    frases = []
    for frase in trozos:
        frases.append({"start": frase.start, "end": frase.end, "text": frase.text})
        _avisar_hijo(0.10 + 0.5 * min(1.0, frase.end / max(duracion, 0.1)),
                     f"escuchado {frase.end / 60:.1f} de {duracion / 60:.1f} min")
    del whisper

    # A cada frase de Whisper, las palabras DEL GUION que caen en ella. Y de
    # paso, la cobertura: cuanto del guion se oyo tal cual.
    guion = tokens_del_guion(entrada.get("texto"))
    reparto, cobertura = frases_del_guion(guion, frases)

    _avisar_hijo(0.62, "afinando cada palabra")
    # wav2vec2 alinea EL TEXTO DEL GUION, no lo que transcribio Whisper. Whisper
    # escribe «$1,000» donde se dijo «one thousand dollars», y una cifra no la
    # sabe alinear nadie: se quedaba sin tiempo y sus vecinas se lo comian. El
    # guion la trae con letras (regla del redactor), asi que se alinea entera.
    segmentos = [{"start": f["start"], "end": f["end"], "text": " ".join(p)}
                 for f, p in zip(frases, reparto) if p]
    alineador, metadatos = whisperx.load_align_model(language_code=idioma,
                                                     device=dispositivo)
    resultado = whisperx.align(segmentos, alineador, metadatos, audio, dispositivo,
                               return_char_alignments=False)
    oidas = [{"w": p.get("word", ""), "s": p.get("start"), "e": p.get("end")}
             for p in resultado.get("word_segments") or []]

    _avisar_hijo(0.92, "emparejando con el guion")
    salida = emparejar(guion, oidas, duracion)
    salida.update(cobertura)
    salida.update({"duracion": round(duracion, 3), "oidas": len(oidas),
                   "dispositivo": dispositivo, "compute_type": tipo,
                   "modelo": modelo, "segundos": round(time.time() - arranque, 1)})
    return salida


# --------------------------------------------------------------- API publica

def _python():
    """El interprete con el que correr el hijo: este mismo, o ESTUDIO_PYTHON."""
    return os.environ.get("ESTUDIO_PYTHON") or sys.executable


def disponible():
    """Si en esta instalacion esta el alineador. -> (bool, motivo)

    Se pregunta a un hijo y no se importa aqui: importar torch en el servicio
    para saber si existe ya es cargar lo que se quiere tener fuera.
    """
    try:
        hecho = subprocess.run(
            [_python(), "-c", "import whisperx, faster_whisper, torch; "
                              "print(torch.cuda.is_available())"],
            capture_output=True, text=True, timeout=180, **SIN_VENTANA)
    except Exception as fallo:                                   # noqa: BLE001
        return False, str(fallo)
    if hecho.returncode != 0:
        return False, ("falta el alineador: instala requirements-alineador.txt "
                       f"({(hecho.stderr or '').strip().splitlines()[-1:]})")
    return True, "GPU" if hecho.stdout.strip() == "True" else "CPU"


def alinear(wav, texto, idioma="en", modelo=None, dispositivo=None, avisar=None):
    """Marcas por palabra de `texto` dentro de `wav`. -> dict

    {"palabras": [{"w","s","e"}], "cobertura", "faltan", "sobran", "duracion",
     "oidas", "dispositivo", "compute_type", "modelo", "segundos"}

    `texto` es lo que se LOCUTA (sin anotaciones de voz). `avisar(fraccion,
    mensaje)` recibe el progreso del hijo, si se da.
    """
    if not str(texto or "").strip():
        raise ValueError("no hay texto que alinear")
    if not os.path.exists(wav):
        raise FileNotFoundError(wav)
    carpeta = tempfile.mkdtemp(prefix="alinear_")
    ruta_entrada = os.path.join(carpeta, "entrada.json")
    ruta_salida = os.path.join(carpeta, "salida.json")
    with open(ruta_entrada, "w", encoding="utf-8") as fh:
        json.dump({"wav": os.path.abspath(wav), "texto": texto, "idioma": idioma,
                   "modelo": modelo, "dispositivo": dispositivo}, fh,
                  ensure_ascii=False)

    entorno = dict(os.environ)
    entorno["PYTHONIOENCODING"] = "utf-8"
    proceso = subprocess.Popen(
        [_python(), os.path.abspath(__file__), "--entrada", ruta_entrada,
         "--salida", ruta_salida],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", env=entorno, **SIN_VENTANA)
    cola = []
    limite = time.time() + TIEMPO_MAXIMO_S
    try:
        for linea in proceso.stdout:
            linea = linea.rstrip()
            if linea.startswith(PREFIJO_PROGRESO):
                fraccion, _, mensaje = linea[len(PREFIJO_PROGRESO):].partition(" ")
                if callable(avisar):
                    avisar(float(fraccion), mensaje)
            elif linea:
                cola = (cola + [linea])[-20:]
            if time.time() > limite:
                proceso.kill()
                raise RuntimeError("el alineador no ha terminado a tiempo")
        proceso.wait(timeout=60)
    finally:
        if proceso.poll() is None:
            proceso.kill()
    if proceso.returncode != 0 or not os.path.exists(ruta_salida):
        raise RuntimeError("el alineador ha fallado: " + " | ".join(cola[-6:]))
    with open(ruta_salida, "r", encoding="utf-8") as fh:
        salida = json.load(fh)
    try:
        os.remove(ruta_entrada)
        os.remove(ruta_salida)
        os.rmdir(carpeta)
    except OSError:
        pass
    return salida


def main():
    parser = argparse.ArgumentParser(description="Alinea un WAV con su texto")
    parser.add_argument("--entrada", help="(uso interno) json de entrada")
    parser.add_argument("--salida", help="(uso interno) json de salida")
    parser.add_argument("--wav")
    parser.add_argument("--texto", help="fichero de texto con lo locutado")
    parser.add_argument("--idioma", default="en")
    parser.add_argument("--modelo")
    parser.add_argument("--dispositivo", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--out", help="donde escribir las marcas (json)")
    args = parser.parse_args()

    if args.entrada:                      # modo hijo
        with open(args.entrada, "r", encoding="utf-8") as fh:
            entrada = json.load(fh)
        salida = trabajar(entrada)
        with open(args.salida, "w", encoding="utf-8") as fh:
            json.dump(salida, fh, ensure_ascii=False)
        return

    if not (args.wav and args.texto):
        parser.error("hace falta --wav y --texto (o --entrada/--salida)")
    with open(args.texto, "r", encoding="utf-8") as fh:
        texto = fh.read()
    salida = alinear(args.wav, texto, args.idioma, args.modelo, args.dispositivo,
                     avisar=lambda f, m: print(f"[{f * 100:5.1f}%] {m}"))
    print(f"cobertura {salida['cobertura'] * 100:.1f} %  "
          f"({len(salida['palabras'])} palabras, {salida['dispositivo']}, "
          f"{salida['segundos']} s)")
    if salida["faltan"]:
        print("no se oyeron tal cual:", " ".join(salida["faltan"][:30]))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(salida, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
