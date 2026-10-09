"""Prueba del reparto de la corrección del estilo gráfico.

Lo que vigila, y es UNA cosa: que cada retoque del vocabulario escriba en un
cajón que alguien lee de verdad. La trampa estaba servida antes de escribirlo —
los tres `SETS_DISENO` declaraban una clave `opacidad` que no leía nadie — y es
el fallo mudo de este repo: se enruta a algo que existe, se corre, el vídeo sale
idéntico y la corrección queda marcada como aplicada.

Corre EN SECO por defecto. Con `--con-cli` llama al modelo de verdad con las
frases que motivaron esto, que es lo único que comprueba que el prompt reparte
como se le pide.

    C:\\IA\\venvs\\cartoon\\Scripts\\python.exe C:\\IA\\estudio\\pasos\\prueba_enrutar_estilo.py
"""
import inspect
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import enrutar_estilo as er                                  # noqa: E402
import moodboard                                             # noqa: E402
import p7_callouts                                           # noqa: E402
import presets_light as light                                # noqa: E402


def _fuente(nombre):
    """El texto de un fichero del repo, para mirar quién lee un cajón."""
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return io.open(os.path.join(raiz, nombre), encoding="utf-8").read()

FALLOS = []


def ok(condicion, texto):
    print(("  ok   " if condicion else "  FALLO ") + texto)
    if not condicion:
        FALLOS.append(texto)


def igual(obtenido, esperado, texto):
    ok(obtenido == esperado,
       texto if obtenido == esperado
       else f"{texto}  [obtenido={obtenido!r} esperado={esperado!r}]")


def seccion(titulo):
    print(f"\n[{titulo}]")


def prueba_vocabulario():
    seccion("1] cada retoque existe de verdad al otro lado")
    for rid, ficha in er.RETOQUES.items():
        desconocidas = [t for t in ficha["tareas"] if t not in light.TAREAS_POR_ID]
        ok(not desconocidas,
           f"«{rid}» solo pide tareas que existen{'' if not desconocidas else f' -- {desconocidas}'}")
        ok(ficha["que_es"] and ficha["imagenes"] >= 0,
           f"«{rid}» dice qué es y cuánto cuesta")
    ok("todo" in er.RETOQUES and "sin_mando" in er.RETOQUES,
       "hay una salida para «no lo sé» y otra para «eso no se puede tocar»: "
       "sin ellas, el motor solo puede elegir mal")
    igual(er.RETOQUES[er.RESPALDO]["tareas"],
          light.PARTES["estilo"]["tareas"],
          "y el respaldo es EXACTAMENTE lo que se hacía antes: caer en él no "
          "es una regresión, es no haber ahorrado esta vez")


def prueba_los_cajones_se_leen():
    seccion("2] los params que escribe los LEE alguien")
    # EL FALLO QUE ESTO EVITA: los tres SETS_DISENO declaraban una clave
    # `opacidad` que no leia nadie. Un enrutador que la viera habria escrito
    # ahi, el video habria salido igual y el cambio habria quedado aplicado.
    ok(all("opacidad" not in ficha for ficha in p7_callouts.SETS_DISENO.values()),
       "la clave muerta `opacidad` de los sets ya no está: era una trampa")

    caja = er.params_de([{"retoque": "caja_subtitulo", "valor": 0.2,
                          "imagenes": 0}])
    igual(caja, {"callouts": {"subtitulo_caja": 0.2}},
          "caja_subtitulo escribe callouts.subtitulo_caja")
    igual(p7_callouts.opacidad_de_caja(caja["callouts"]), 0.2,
          "y ese param lo LEE `opacidad_de_caja`, que es quien pinta el <rect>")

    tam = er.params_de([{"retoque": "tamano_subtitulo", "valor": "grande",
                         "imagenes": 0}])
    igual(tam, {"callouts": {"subtitulo_tam": "grande"}},
          "tamano_subtitulo escribe callouts.subtitulo_tam")
    ok(p7_callouts.tamano_subtitulo(tam["callouts"])
       > p7_callouts.tamano_subtitulo({}),
       "y «grande» de verdad sale más grande que el normal")

    set_g = er.params_de([{"retoque": "set_grafismo", "valor": "editorial",
                           "imagenes": 0}])
    igual(set_g, {"callouts": {"diseno": "editorial"}},
          "set_grafismo escribe callouts.diseno")
    igual(p7_callouts.diseno_de(set_g["callouts"], {})[0], "editorial",
          "y `diseno_de` lo respeta por encima de lo que diga la guía")

    igual(er.params_de([{"retoque": "dibujo", "imagenes": 6}]), {},
          "«dibujo» no escribe params: lo suyo lo decide la tarea que corre")

    for rid, valores in er.VALORES.items():
        ok(all(v for v in valores), f"«{rid}» tiene su lista de valores válidos")
    ok(set(er.VALORES["set_grafismo"]) == set(p7_callouts.SETS_DISENO),
       "y la del grafismo sale de SETS_DISENO, no de una copia a mano")


def prueba_tareas():
    seccion("3] qué se corre con cada decisión")
    igual(er.tareas_de([{"retoque": "caja_subtitulo", "valor": 0.2}]),
          ("grafismo", "muestra"),
          "un retoque de texto en pantalla NO rehace la guía ni las láminas")
    ok("referencias" not in er.tareas_de([{"retoque": "set_grafismo",
                                           "valor": "editorial"}]),
       "ni el cambio de familia: cero imágenes")
    igual(er.tareas_de([{"retoque": "dibujo"}]),
          ("guia", "referencias", "grafismo", "muestra"),
          "y el dibujo sí las rehace, en el orden de las dependencias")
    igual(er.tareas_de([]), light.PARTES["estilo"]["tareas"],
          "sin retoques, el respaldo: lo de siempre")
    igual(er.tareas_de([{"retoque": "sin_mando"}]), (),
          "y «no tiene mando» no corre NADA: es una respuesta, no un cambio")

    # `muestra` entra siempre que entre algo: es lo unico que rasteriza la
    # cartela y el subtitulo con el mismo codigo que el video, o sea lo unico
    # donde el cambio SE VE en la tarjeta.
    for rid in ("caja_subtitulo", "tamano_subtitulo", "set_grafismo", "dibujo"):
        ok("muestra" in er.tareas_de([{"retoque": rid, "valor": "normal"}]),
           f"«{rid}» recompone las muestras: sin eso la tarjeta seguiría "
           f"enseñando lo anterior y no habría forma de ver que pasó algo")

    dos = er.tareas_de([{"retoque": "caja_subtitulo", "valor": 0.2},
                        {"retoque": "tamano_subtitulo", "valor": "grande"}])
    igual(dos, ("grafismo", "muestra"), "dos retoques baratos no duplican tareas")


def prueba_lo_que_devuelve_el_modelo():
    seccion("4] lo que devuelve el modelo se limpia antes de correr")
    # UN RETOQUE INVENTADO NO SE APLICA A MEDIAS: se cae al respaldo entero. Es
    # la misma regla del repaso, y el prompt le dice la CONSECUENCIA.
    retoques, avisos = er._limpiar({"retoques": [{"retoque": "opacidad"}]})
    igual(retoques, [], "un retoque inventado se descarta")
    ok(avisos and "no existe" in avisos[0], f"y se dice: {avisos}")
    igual(er.tareas_de(retoques), light.PARTES["estilo"]["tareas"],
          "y se cae al respaldo, que es rehacer el estilo entero")

    retoques, avisos = er._limpiar(
        {"retoques": [{"retoque": "set_grafismo", "valor": "chulo"}]})
    igual(retoques, [], "un valor que no está en la lista, igual")
    ok(avisos and "chulo" in avisos[0], f"y se dice cuál era: {avisos}")

    retoques, _ = er._limpiar(
        {"retoques": [{"retoque": "caja_subtitulo", "valor": "0,3"}]})
    igual(retoques[0]["valor"], 0.3, "una coma decimal se entiende")
    retoques, _ = er._limpiar(
        {"retoques": [{"retoque": "caja_subtitulo", "valor": 5}]})
    igual(retoques[0]["valor"], 1.0,
          "y un número fuera de rango se recorta, no tumba el render")
    retoques, avisos = er._limpiar(
        {"retoques": [{"retoque": "caja_subtitulo", "valor": "mucho"}]})
    igual(retoques, [], "pero uno que no es número se descarta")

    igual(er._limpiar({}), ([], []), "una respuesta vacía no revienta")
    igual(er._limpiar({"retoques": "no es una lista"}), ([], []),
          "ni una con la forma cambiada")


def prueba_la_tupla_vacia_es_una_respuesta():
    seccion("5 bis] «no tiene mando» no puede caerse al respaldo")
    # EL FALLO QUE ARREGLA, cazado probandolo en vivo: `tareas` salia de
    #     (reparto or {}).get("tareas") or light.PARTES[parte]["tareas"]
    # y una tupla VACIA es falsa, asi que la respuesta «esto no tiene mando»
    # caia al respaldo: se lanzaban las cuatro tareas y las seis imagenes para
    # una frase que el motor acababa de decir que no sabia aplicar. Justo lo
    # contrario de lo que este reparto existe para hacer.
    igual(er.tareas_de([{"retoque": "sin_mando"}]), (),
          "el reparto devuelve una tupla vacía, no None")

    fuente = _fuente("app.py")
    ok("elif reparto is not None:" in fuente,
       "y quien lo usa distingue «no hubo reparto» de «el reparto dice que no "
       "hay nada que hacer»: con un `or` son lo mismo")
    ok('(reparto or {}).get("tareas") or light.PARTES' not in fuente,
       "el `or` que las confundía ya no está")
    ok("sin_cambios" in fuente,
       "y hay una salida por la que decirlo sin lanzar ningún trabajo")


def prueba_una_sola_referencia():
    seccion("6] corregir UNA de las seis y no las seis")
    # EL CASO QUE LO DESTAPO, escrito mirando las muestras: «que de las 6
    # referencias en UNA haya monos variados... pero SOLO para una referencia,
    # el resto mantenlas». El unico destino que tocaba lo dibujado era `dibujo`,
    # que reescribe la guia y redibuja LAS SEIS: la unica forma de arreglar la
    # que no gustaba era tirar las cinco que si.
    ok(er.UNA_LAMINA in er.RETOQUES,
       "existe un retoque para una sola referencia")
    ficha = er.RETOQUES[er.UNA_LAMINA]
    igual(ficha["imagenes"], 1, "y cuesta UNA imagen, no seis")
    ok("guia" not in ficha["tareas"],
       "y NO reescribe la guía: corregir un dibujo suelto no puede mover el "
       "estilo escrito del que cuelgan los otros cinco")

    igual(er.tareas_de([{"retoque": er.UNA_LAMINA, "eje": "cuerpos",
                         "peticion": "gente variada"}]),
          ("referencias", "muestra"),
          "se redibuja y se recompone la tarjeta, y nada más")

    igual(set(er.EJES), set(moodboard.EJES),
          "los ejes salen de `moodboard` y no de una copia a mano: un eje "
          "inventado aquí sería una corrección escrita para una lámina que no "
          "existe")

    suelta = [{"retoque": er.UNA_LAMINA, "eje": "cuerpos",
               "peticion": "gente vestida de formas distintas", "imagenes": 1}]
    igual(er.laminas_de(suelta),
          {"cuerpos": "gente vestida de formas distintas"},
          "la petición viaja con SU eje")
    igual(er.params_de(suelta), {},
          "y no escribe ningún param: va por invocación, no es lo que el "
          "estilo es")

    dos = suelta + [{"retoque": er.UNA_LAMINA, "eje": "interior",
                     "peticion": "menos muebles", "imagenes": 1}]
    igual(sorted(er.laminas_de(dos)), ["cuerpos", "interior"],
          "dos láminas nombradas son dos correcciones")
    igual(er.imagenes_de(dos), 2,
          "y son dos imágenes: con un `max` se habría anunciado una")

    # LA MEZCLA ES EL FALLO CARO: `dibujo` ya redibuja las seis, asi que
    # entregar ademas una lamina suelta dejaria cinco con el estilo viejo y una
    # con el nuevo -- y la tarea habria corrido, o sea cambio dado por bueno.
    mezcla = suelta + [{"retoque": "dibujo", "imagenes": 6}]
    igual(er.laminas_de(mezcla), {},
          "mezclada con «dibujo», la lámina suelta no recorta nada: vacío son "
          "las seis")
    igual(er.imagenes_de(mezcla), 6, "y el precio que se dice son seis")


def prueba_el_idioma_llega_a_la_lamina():
    seccion("7b] el idioma del canal LLEGA a las láminas de un estilo descrito")
    # `dibujar_desde_guia` recibia el idioma y no se lo pasaba al prompt: la
    # regla de rotulos dice que el ingles de las instrucciones no decide el
    # idioma, y sin decir cual un canal en ingles saco «CABEZA / MANO / PIE».
    import tempfile                                           # noqa: PLC0415
    import medios                                             # noqa: PLC0415
    vistos = []

    class Falso:
        @staticmethod
        def generar(prompt, referencias, **_kw):
            vistos.append(prompt)
            return b"png", {"coste": 0.0}

    original = medios.motor_imagen
    medios.motor_imagen = lambda *a, **k: Falso
    try:
        with tempfile.TemporaryDirectory() as carpeta:
            moodboard.dibujar_desde_guia(
                {"guia": {"guia": "Flat ink drawing, warm palette."}}, carpeta,
                ejes=["diagrama"], idioma="en")
    finally:
        medios.motor_imagen = original
    ok(len(vistos) == 1 and "The language of this production is English" in vistos[0],
       "el prompt de la lámina dice el idioma del canal")


def prueba_las_imagenes_del_autor_llegan_a_la_lamina():
    seccion("7c] las imágenes que subiste LLEGAN al dibujo de las láminas")
    # Hasta el 08-10-2026 un estilo descrito con imagenes las usaba solo para
    # que Claude escribiera la guia: las laminas se dibujaban con ese texto y
    # nada mas. Ahora van tambien al generador, sueltas.
    import tempfile                                           # noqa: PLC0415
    import medios                                             # noqa: PLC0415
    vistos = []
    normalizadas = []

    class Falso:
        @staticmethod
        def generar(prompt, referencias, **_kw):
            vistos.append((prompt, list(referencias)))
            return b"png", {"coste": 0.0}

        @staticmethod
        def normalizar(ruta, cache, lado_max=1024):
            normalizadas.append(cache)
            return ruta

    original = medios.motor_imagen
    medios.motor_imagen = lambda *a, **k: Falso
    try:
        with tempfile.TemporaryDirectory() as carpeta:
            suyas = []
            for nombre in ("a.jpg", "b.jpg"):
                ruta = os.path.join(carpeta, nombre)
                with open(ruta, "wb") as fh:
                    fh.write(b"x")
                suyas.append(ruta)
            destino = os.path.join(carpeta, "estilo", "dibujadas")
            moodboard.dibujar_desde_guia(
                {"guia": {"guia": "Flat ink drawing, warm palette."}}, destino,
                ejes=["cara", "objeto"], idioma="en",
                referencias=suyas + [os.path.join(carpeta, "no_existe.png")])
            con = list(vistos)
            vistos.clear()
            moodboard.dibujar_desde_guia(
                {"guia": {"guia": "Flat ink drawing, warm palette."}}, destino,
                ejes=["cara"], idioma="en")
            sin = list(vistos)
            cache_fuera = all(os.path.dirname(c) == os.path.join(carpeta, "estilo")
                              for c in normalizadas)
            dentro = sorted(os.listdir(destino))
    finally:
        medios.motor_imagen = original
    ok(len(con) == 2 and all(refs == suyas for _p, refs in con),
       "las dos láminas reciben tus dos imágenes (y la que no existe se salta)")
    ok(all("The 2 reference images are example illustrations" in p
           for p, _r in con),
       "y el prompt dice que son ejemplos del autor a los que parecerse")
    ok(all("colour swatches" in p and "character sheet" in p for p, _r in con),
       "y que NO copie la maqueta: ni hoja de personaje ni muestras de color")
    ok(all("only description of how this production is drawn" not in p
           for p, _r in con),
       "y ya no dice que la guía escrita sea lo único que describe el dibujo")
    ok(len(sin) == 1 and sin[0][1] == []
       and "only description of how this production is drawn" in sin[0][0],
       "sin imágenes, la lámina se dibuja como siempre: sin adjuntos")
    ok(cache_fuera and dentro == ["cara.png", "objeto.png"],
       "las copias preparadas van JUNTO a las láminas, no dentro: "
       "la carpeta de dibujadas solo tiene láminas")
    ok(moodboard.prompt_de_dibujo("x", {"guia": {}}, de_autor=1).count(
        "The reference image is an example illustration") == 1,
       "con una sola imagen la frase va en singular")

    # CON TOPE: se pueden subir 24 y el generador admite menos. Pasarse no
    # recorta: el motor se niega y se pierden las seis laminas.
    for tope_motor, esperadas in ((None, moodboard.MAX_REFERENCIAS_AUTOR), (3, 3)):
        vistos.clear()
        if tope_motor:
            Falso.max_referencias = staticmethod(lambda *_a, t=tope_motor: t)
        medios.motor_imagen = lambda *a, **k: Falso
        try:
            with tempfile.TemporaryDirectory() as carpeta:
                muchas = []
                for indice in range(10):
                    ruta = os.path.join(carpeta, f"{indice}.jpg")
                    with open(ruta, "wb") as fh:
                        fh.write(b"x")
                    muchas.append(ruta)
                moodboard.dibujar_desde_guia(
                    {"guia": {"guia": "Flat ink."}},
                    os.path.join(carpeta, "dibujadas"), ejes=["cara"],
                    referencias=muchas)
        finally:
            medios.motor_imagen = original
            if "max_referencias" in Falso.__dict__:
                del Falso.max_referencias
        ok(bool(vistos) and len(vistos[0][1]) == esperadas,
           f"con 10 imágenes viajan {esperadas}: "
           + ("el motor dice su tope y se respeta" if tope_motor
              else "el tope de la casa"))

    fuente = _fuente("app.py")
    # UNA VOZ DE GOOGLE ELEGIDA A MANO NO SE BUSCA EN CARTESIA (08-10-2026):
    # crear «Historia EN – doodle» con voz_id «Orus» murio en la tarea de voz.
    tarea_voz = fuente[fuente.index("def _correr_light_voz"):]
    tarea_voz = tarea_voz[:tarea_voz.index("\ndef ")]
    ok(tarea_voz.index("google.VOCES_MASCULINAS")
       < tarea_voz.index("voz_descrita.proponer("),
       "la tarea de voz del estilo mira ANTES si la voz elegida es de Google")
    ok('"proveedor": "google"' in tarea_voz and "return dict(cambios" in tarea_voz,
       "y entonces la escribe directa, sin pasar por el catálogo de Cartesia")
    ok("referencias=_aportadas_del_taller(ctx)" in fuente,
       "y app.py le pasa las imágenes que subiste con la descripción")


def prueba_la_lamina_llega_al_prompt():
    seccion("7] la corrección de una lámina LLEGA al dibujo")
    # UN CAJON ES UNA PROMESA. Aqui el cajon no es un param: son los argumentos
    # `ejes` y `peticiones` de los DOS caminos que dibujan laminas. Si uno de
    # los dos no los tuviera, ese estilo aceptaria la correccion, pagaria la
    # imagen y devolveria la misma lamina generica, con el cambio aplicado.
    for nombre in ("generar", "dibujar_desde_guia"):
        firma = inspect.signature(getattr(moodboard, nombre)).parameters
        ok("ejes" in firma and "peticiones" in firma,
           f"`moodboard.{nombre}` sabe dibujar unos ejes con su corrección")

    prompt = moodboard.prompt_de_eje("cuerpos", {"guia": {}},
                                     peticion="ropa variada, no todos de traje")
    ok("ropa variada" in prompt,
       "y con vídeo la petición entra en el prompt de ESA lámina")
    prompt = moodboard.prompt_de_dibujo("lo que sea", {"guia": {}},
                                        peticion="ropa variada")
    ok("ropa variada" in prompt, "y sin vídeo también")

    fuente = _fuente("app.py")
    ok('encargo.get("laminas")' in fuente,
       "app.py lee el cajón que escribe el reparto")
    ok("ejes=pedidos, peticiones=peticiones" in fuente,
       "y se lo pasa a los dos caminos que dibujan")
    ok('encargo["laminas"] = dict(reparto.get("laminas") or {})' in fuente,
       "y quien reparte lo mete en el encargo, que es de ESTA pasada")
    # LO QUE SE ELIGE HAY QUE MONTARLO: sin video, las laminas SON la lista de
    # referencias del estilo. Escribir ahi solo la redibujada dejaria un estilo
    # de una referencia, sin error y sin aviso.
    ok("previas + [r for r in hecho" in fuente,
       "y redibujar una sola no borra de la lista las otras cinco")

    # Y EL ORDEN QUE SE LE ENSENA AL MODELO ES EL QUE SE VE, no uno inventado:
    # el moodboard va por orden alfabetico de eje y las laminas de un estilo
    # descrito van en el orden de EJES, que no es el mismo.
    ok("def _laminas_por_eje" in fuente,
       "las láminas se leen con su eje y en el orden en que se ven")
    ok("_laminas_por_eje(ctx)" in fuente.split("def _estado_del_estilo")[1][:2500],
       "y el estado que ve el reparto las lleva numeradas: sin eso «la "
       "segunda» sería una adivinanza")
    ok(list(moodboard.EJES) != sorted(moodboard.EJES),
       "los dos órdenes de verdad son distintos, que es por lo que el número "
       "no puede estar escrito en el prompt")


def prueba_lo_que_devuelve_el_modelo_para_una_lamina():
    seccion("8] un eje o una petición que no valen no se aplican a medias")
    retoques, avisos = er._limpiar({"retoques": [
        {"retoque": er.UNA_LAMINA, "eje": "la de los monos",
         "peticion": "más variados"}]})
    igual(retoques, [], "un eje inventado se descarta")
    ok(avisos and "referencias" in avisos[0], f"y se dice: {avisos}")
    igual(er.tareas_de(retoques), light.PARTES["estilo"]["tareas"],
          "y se cae al respaldo, que es lo que se hacía antes")

    retoques, avisos = er._limpiar({"retoques": [
        {"retoque": er.UNA_LAMINA, "eje": "cuerpos", "peticion": "   "}]})
    igual(retoques, [], "y una lámina sin decir qué cambiarle, también")
    ok(avisos and "qué cambiarle" in avisos[0], f"y se dice por qué: {avisos}")

    retoques, _ = er._limpiar({"retoques": [
        {"retoque": er.UNA_LAMINA, "eje": "  CUERPOS ",
         "peticion": "  ropa   variada  "}]})
    igual(retoques[0]["eje"], "cuerpos", "el eje se normaliza")
    igual(retoques[0]["peticion"], "ropa variada", "y la petición también")


def prueba_sin_frase():
    seccion("5] sin frase no se llama a nadie")
    r = er.repartir("")
    igual(r["tareas"], light.PARTES["estilo"]["tareas"],
          "sin nada escrito se rehace el estilo entero, como antes")
    igual(r["segundos"], 0.0, "y no se paga ninguna llamada por saberlo")
    igual(r["params"], {}, "ni se escribe ningún param")


def prueba_con_cli():
    seccion("9] contra el modelo de verdad")
    estado = ("grafismo: dibujo · subtítulo: tamaño normal, caja al 0.55 · "
              "trazo: grueso y uniforme, relleno plano\n\n"
              "las referencias dibujadas, numeradas en el orden en que se ven "
              "en pantalla:\n"
              "  1. cara — Una cara de cerca\n"
              "  2. cuerpos — Varias personas de cuerpo entero\n"
              "  3. diagrama — Un diagrama sencillo\n"
              "  4. exterior — Un exterior general\n"
              "  5. interior — Un interior general\n"
              "  6. objeto — Un objeto de cerca")
    casos = [
        ("quiero retocar solo la opacidad del rectangulo de fondo de los "
         "subtitulos, que tapa demasiado", "caja_subtitulo", 0, ""),
        ("el subtitulo se lee mal, hazlo mas grande", "tamano_subtitulo", 0, ""),
        ("el trazo es demasiado fino y las caras salen raras", "dibujo", 6, ""),
        # LA FRASE QUE LO DESTAPO, tal y como se escribio en la pantalla
        ("Haz que de las 6 refencias en una haya monos variados vesitods de "
         "formas distintas (rico, pobre, joven, viejo, atleta, granjero, "
         "empresario etc...), que no vayan todos con traje y corbata, que haya "
         "variedad, pero solo para una referencia, el resto de referencias "
         "manten las que ya estan", er.UNA_LAMINA, 1, "cuerpos"),
        # SENALADA POR SU SITIO: el numero sale del estado, no del vocabulario
        ("solo la quinta, que tenga menos muebles", er.UNA_LAMINA, 1,
         "interior"),
        # SENALADA POR LO QUE SE VE EN ELLA
        ("en la de la calle quiero mas edificios y menos cielo",
         er.UNA_LAMINA, 1, "exterior"),
        # Y EL SIMETRICO, que es el error caro: esto vale para las seis
        ("los colores son demasiado apagados en todo", "dibujo", 6, ""),
    ]
    for frase, esperado, imagenes, eje in casos:
        r = er.repartir(frase, estado=estado)
        elegidos = [e["retoque"] for e in r["retoques"]]
        ok(esperado in elegidos,
           f"«{frase[:44]}…» -> {elegidos or 'respaldo'} (esperaba {esperado})")
        if esperado not in elegidos:
            continue
        igual(er.imagenes_de(r["retoques"]), imagenes,
              f"    y cuesta {imagenes} imagen(es)")
        if eje:
            igual(sorted(r["laminas"]), [eje],
                  f"    y es la lámina «{eje}» y solo esa")
            ok(str(r["laminas"].get(eje) or "").strip(),
               f"    con algo escrito que cambiarle: "
               f"«{str(r['laminas'].get(eje) or '')[:70]}»")


def main():
    prueba_vocabulario()
    prueba_los_cajones_se_leen()
    prueba_tareas()
    prueba_lo_que_devuelve_el_modelo()
    prueba_la_tupla_vacia_es_una_respuesta()
    prueba_una_sola_referencia()
    prueba_la_lamina_llega_al_prompt()
    prueba_el_idioma_llega_a_la_lamina()
    prueba_las_imagenes_del_autor_llegan_a_la_lamina()
    prueba_lo_que_devuelve_el_modelo_para_una_lamina()
    prueba_sin_frase()
    if "--con-cli" in sys.argv:
        prueba_con_cli()
    else:
        print("\n  (en seco: con --con-cli se llama al modelo de verdad)")

    print()
    if FALLOS:
        print(f"FALLARON {len(FALLOS)} comprobaciones:")
        for texto in FALLOS:
            print(f"  - {texto}")
        return 1
    print("REPARTO DEL ESTILO OK: todas las comprobaciones pasan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
