"""Textos del director «escena entera» (30-10), copiados TAL CUAL de las herramientas de
E:\\AutomatizacionYT\\herramientas con las que se aprobaron S003 y S006: el encargo
(director_escena_entera.INSTRUCCION), la cabecera de la orden del metodo a mano
(escena_nueva.ORDEN, LLENO, REGLAS_PROTA) y la comprobacion sin Claude
(revisar_escenas.revisar). Si se cambian alli, se copian aqui."""
import re

INSTRUCCION = """Eres el director de un video de historia animado, en estilo doodle, titulado
«{titulo}». El video ya esta cortado en planos, cada uno con su frase de narracion.
Para cada plano que se te pide escribes la ESCENA ENTERA que recibe el dibujante (un
generador de imagenes), igual que las escenas aprobadas de ejemplo que veras abajo.

=====================================================================
LO QUE EL DIBUJANTE YA RECIBE SIEMPRE (no lo repitas)
=====================================================================
Una cabecera fija: copia el estilo de la PRIMERA imagen de referencia (un plano aprobado,
el que tu eliges en "estilo"); las otras imagenes son las hojas de personaje (las que tu
pones en "hojas", en ese orden); cada cabeza es un circulo blanco sin orejas ni nariz; el
dibujo llena el cuadro de borde a borde, sin margenes de papel; nadie es una sombra o una
silueta oscura; se dibuja SOLO lo que dice la escena. Despues van tus "reglas" y tus
"letras", y al final tu "escena".

=====================================================================
LO QUE ESCRIBES PARA CADA PLANO
=====================================================================
- "escena" (en ingles, de 60 a 150 palabras), en este orden:
  1. EPOCA Y LUGAR PRIMERO: «Halloween night, present day, on the front porch of a
     suburban house.» / «England about five hundred years ago, at dusk in early November.»
     Si la frase nombra un lugar (una ciudad, una universidad, un pais), ese lugar se VE y
     da el contexto (por ejemplo, la universidad por la ventana).
  2. QUIEN SALE: cada persona con su hoja dicha asi: «the protagonist from the first
     character sheet», «the three children from the second character sheet: the ghost,
     the pirate and the dinosaur». Con su ropa, su pose y hacia donde mira, y su cara
     (sorpresa, duda, enfado...) segun la frase. La gente SIN hoja se describe entera:
     edad, pelo, ropa de su epoca y su pais.
  3. QUE HACEN: lo que dice la frase, de forma concreta y facil de dibujar. Lo que alguien
     lleva lo sujeta junto al cuerpo; lo grande se lleva abrazado.
  4. Luz y color, y que todo se vea claro.
- "letras": el UNICO texto del dibujo, entre comillas simples, con donde va y como
  («'TRICK OR TREAT!' inside one speech bubble coming from the children»), o "" si no
  lleva ninguno. Cualquier otro letrero, cartel o pagina, en la escena se pide «blank».
- "hojas": los ids de las hojas de personaje que salen, en orden: «the first character
  sheet» es la primera de la lista. El protagonista, si sale, va primero. Solo ids de la
  lista del REPARTO. Lista vacia si no sale nadie con hoja.
- "reglas": la frase del rizo y de la epoca, en ingles, con estos modelos:
  · sale el protagonista (primera hoja): «{prota}»
  · no sale, dia de hoy: «nobody has a spiral curl; nobody floats.»
  · no sale, otra epoca: «nobody has a spiral curl; nobody floats; nothing modern.» o, si
    la epoca es reciente, «...; nothing from after 1920.»
  · el protagonista sale dos veces (una en cada mitad): «the protagonist appears twice,
    once in each half, and he is the only one with a thin spiral curl; nobody floats.»
  · el protagonista sale con su hoja de otra epoca y no es la primera hoja: dilo, p. ej.
    «only the protagonist beggar from the second character sheet, in the right half, has
    a thin spiral curl; nobody else; nobody floats; nothing modern.»
- "estilo": el id del plano aprobado que sirve de referencia de ESTILO (solo estilo, no
  contenido), de la misma epoca y luz, de esta lista (entre corchetes, si lo lleva, su tipo
  y su luz; despues, su clase de plano y lo que sale en ella):
{estilos}
  OJO: el dibujante COPIA lo que ve en esa referencia (gente, sitio y luz), aunque se le diga
  que no. Elige la de la MISMA CLASE DE PLANO (pantalla dividida, calle llena de gente,
  puerta vista desde fuera...) y la misma luz, y que no tenga NADA que contradiga tu escena.
  Bien: S027 con S028 (las dos, pantallas divididas). Mal: S003 con S004 (ninos DENTRO del
  recibidor) salio con los ninos dentro y un fantasma repetido; S006 con S009 y con S139
  (gente en la acera) saco a los adultos a la acera.
- "tipo": uno de: cifra, comparacion, grupo_epoca, protagonista_hoy, escena_epoca, calle.
- "por_que": una frase corta EN CASTELLANO sencillo con la idea del plano.

=====================================================================
LAS REGLAS DE ENRIQUE (cada una salio mal en un video ya hecho)
=====================================================================
- LO LITERAL: la imagen ensena lo que dice la frase: quien, que hace y donde. Ni objetos
  inventados (paradas de autobus, gorros sobre globos, fechas tachadas) ni pantallas de
  ordenador si la frase no habla de ellas. Las ideas grandes se cuentan con una escena
  clara, nunca con objetos sueltos al azar.
- LA GENTE DE LA FRASE SALE entera, con la cara visible, haciendo lo que dice la frase.
  Una cifra de gente se ensena con esa gente. «you» / «your» = el protagonista con su ropa
  de hoy y sin disfraz; «tu en el pasado» = su hoja de esa epoca.
- EL RIZO EN ESPIRAL ES SOLO DEL PROTAGONISTA. Los demas hombres son calvos y se
  distinguen por la ropa, el gorro o el bigote; las mujeres llevan pelo y pestanas.
- UNA CIFRA PROTAGONISTA va ENORME, dominando el cuadro («'$13.5 BILLION' in huge bold
  hand-drawn letters across the top of the frame»), con la escena algo exagerada para
  acompanar el tono; nunca en un cartelito. PERO si el plano lleva CARTELA ENCIMA, esa
  cifra ya sale en pantalla: no la repitas en el dibujo.
- COMPARACION O PANTALLA DIVIDIDA (la frase dice «split screen», o compara hoy y antes):
  pantalla dividida de verdad, con una raya vertical y las dos mitades llenas, y SIEMPRE
  con sus cuadros de epoca en "letras" («'NOW' / '500 YEARS AGO'», «'TODAY' / 'BACK
  THEN'»), aunque la frase no diga la fecha. Las dos figuras con la misma pose, el mismo
  tamano, a la misma altura y mirando hacia el mismo lado.
- LETREROS: llevan texto solo si la frase nombra algo relevante que ese letrero diria (un
  lugar, una tienda, una fecha, un dato), corto y en ingles. NUNCA una palabra suelta o una
  accion del guion convertida en rotulo («THE DEAL», «THEY ASKED», «COPIED»).
- BOCADILLOS: solo con las palabras que un personaje de la escena DICE EN VOZ ALTA en ese
  momento («'TRICK OR TREAT!'» de los ninos). NUNCA la frase del narrador ni un trozo del
  guion puesto en boca de alguien, y nunca bocadillos vacios. Paso: «It's harmless fun.»
  del narrador salio como bocadillo de una vecina que no estaba en la frase.
- EL GIRO: si la frase da un giro («but...», «and yet...», «here's what nobody knows»), el
  dibujo ensena lo que viene DESPUES del giro, que es la idea del plano; lo de antes del
  giro suele ser el final del plano anterior. Paso: «It's harmless fun. But here's what
  almost nobody knows about the bowl in your hands» se dibujo con la vecina de la primera
  parte en vez de con el y el misterio de su bol.
- NADA FUERA DE LA EPOCA NI DE LA FIESTA: ropa, edificios y objetos del lugar y del ano de
  la frase; nada de guirnaldas que parezcan navidenas en Halloween ni numeros de
  calendario. El adulto de la casa no va disfrazado, y el que da los dulces esta DENTRO de
  su casa, en su puerta.
- CUANTOS Y DONDE: di cuantos hay de cada grupo y que cada uno sale UNA vez («exactly three
  children, one ghost, one pirate and one dinosaur, nobody else»), y quien esta DENTRO y
  quien FUERA («inside his open doorway», «outside on the porch boards»). En una escena con
  mucha gente, la gente va pequena y sin describirla una a una: dos o tres personas
  descritas en detalle se dibujan grandes y delante, y se comen la escena.
- LOS MAYORES se dibujan como los demas (cabeza redonda blanca, ojos de punto): la edad se ve
  en el pelo gris y la ropa, nunca con arrugas ni cara realista. Paso: «an elderly woman»
  salio con nariz y arrugas.
- NADA QUE PAREZCA DE NAVIDAD (abetos, acebo, estrellas, rojo y verde): los cuencos, latas y
  bolsas, lisos o con motivos de Halloween. Paso: una lata con un abeto.
- NADA DUPLICADO (un bol en la mano y otro en la mesa) y NADA ARQUITECTONICAMENTE RARO: cada
  casa es una casa separada, con su porche, sus escalones y su camino; nunca una fila de
  puertas bajo un solo tejado.
- LOS VECINOS: lee las frases de antes y de despues para saber de que va el tramo y donde
  estamos, pero este plano ensena SU frase: no adelantes lo que trae la siguiente.

=====================================================================
LAS {n_ejemplos} ESCENAS APROBADAS DE EJEMPLO (imitalas en forma, largura y detalle)
=====================================================================
{ejemplos}

=====================================================================
EL REPARTO (las hojas de personaje que existen; cada una viaja como imagen)
=====================================================================
{reparto}

=====================================================================
LOS PLANOS QUE TE TOCAN ({cuantos}, y los quiero TODOS)
=====================================================================
El «sitio del estudio» y el «reparto del estudio» son solo una PISTA de un catalogo
antiguo: usalos si encajan con la frase e ignoralos si no. Manda la frase.

{planos}

DEVUELVE ESTE JSON:

{{"planos": {{"<id>": {{"tipo": "...", "estilo": "S0..", "hojas": ["..."], "reglas": "...",
"letras": "...", "escena": "...", "por_que": "..."}}}}}}

Los {cuantos} planos, ninguno menos y ninguno de mas.
"""

ORDEN = """Draw a NEW picture in exactly the same drawing style as the FIRST reference image: copy only its style (line work, colours, shading, paper texture, the simple round-headed people), not its content or composition. {hojas}Every head is one round white circle with no ears and no nose; {reglas} {lleno} {texto}
Scene: {escena}"""

LLENO = """The picture is full-bleed: the drawing fills the whole frame edge to edge, with no paper border, no empty margins and no blank band at the top or the sides. Every person is drawn in full in this style, clearly lit, with a white round head and coloured clothes, never as a dark silhouette or shadow. Draw only what the scene below describes: no extra objects, signs or screens."""

REGLAS_PROTA = """only the protagonist from the first character sheet has a thin spiral curl, nobody else; nobody floats."""

HOJAS_PROTA = ('muneco', 'muneco_mendigo', 'muneco_1930s')
PROHIBIDAS = ("black field", "blank speech bubble", "empty speech bubble", "silhouette",
              "shadow figure", "in shadow", "computer", "laptop", "monitor", "terminal window",
              "garland", "calendar", "christmas", "holly", "fir tree", "pine tree", "wrinkle",
              "elderly")  # 29-10 S006: «elderly» salio con arrugas y nariz; lata con abeto
# Cifras de la frase: digitos o numeros en palabras con su magnitud
CIFRA = re.compile(r"\d|\b(?:hundred|thousand|million|billion|percent)\b", re.I)
REPETIDO = re.compile(r"\b(?:another|a second|second)\s+(\w+)", re.I)
NO_OBJETO = ("character", "sheet", "half", "reference", "image", "one", "boy", "girl", "child",
             "man", "woman", "neighbour", "neighbor", "professor")
NEGACION = re.compile(r"\b(?:no|nobody|never|not|without|nor)\b[^.;]{0,30}$")

def _comillas(texto):
    # el apostrofo DENTRO de una palabra (IT'S) no cierra la cita
    return re.findall(r"'((?:[^']|(?<=[A-Za-z])'(?=[A-Za-z])){1,60})'|\"([^\"]{1,60})\"",
                      texto or "")


def revisar(ficha, frase="", cartela="", reparto=None, sid="", estilos=None, pista=""):
    """-> lista de avisos en castellano sencillo (vacia = pasa)."""
    avisos = []
    # 29-10 (S003 con S004, S006 con S009): el dibujante copia el CONTENIDO de la referencia
    estilo = str(ficha.get("estilo") or "")
    if sid[1:].isdigit() and estilo[1:].isdigit() and abs(int(sid[1:]) - int(estilo[1:])) <= 2:
        avisos.append(f"la referencia de estilo {estilo} es un plano vecino: copiara su contenido")
    escena = str(ficha.get("escena") or "")
    letras = str(ficha.get("letras") or "")
    reglas = str(ficha.get("reglas") or "").lower()
    hojas = list(ficha.get("hojas") or [])
    bajo = escena.lower()
    palabras = len(escena.split())

    if not 60 <= palabras <= 160:
        avisos.append(f"la escena tiene {palabras} palabras (lo bueno: 60-150)")
    if reparto is not None:
        for h in hojas:
            if h not in reparto:
                avisos.append(f"la hoja «{h}» no existe en el reparto")
    if estilos is not None and ficha.get("estilo") not in estilos:
        avisos.append(f"referencia de estilo «{ficha.get('estilo')}» fuera de la lista")

    # el rizo
    con_prota = any(h in HOJAS_PROTA for h in hojas)
    if con_prota and "only" not in reglas and "the only one" not in reglas:
        avisos.append("sale el protagonista pero las reglas no dicen que SOLO el lleva el rizo")
    if not con_prota and "nobody has a spiral curl" not in reglas:
        avisos.append("no sale el protagonista y las reglas no dicen «nobody has a spiral curl»")
    if con_prota and hojas[0] not in HOJAS_PROTA and "first character sheet" in reglas:
        avisos.append("las reglas dan el rizo a la primera hoja, y la primera no es el protagonista")
    if "spiral" in bajo or re.search(r"\b(?:a|his|her|thin)\s+curl\b", bajo):
        avisos.append("la escena habla de un rizo: eso va solo en las reglas")

    # las letras: solo entre comillas, y solo en "letras"
    if _comillas(escena):
        avisos.append("la escena lleva texto entre comillas: las letras van solo en «letras»")
    if letras and not _comillas(letras):
        avisos.append("«letras» no lleva el texto entre comillas simples")
    if re.search(r"speech bubble", letras + " " + escena, re.I) and not _comillas(letras):
        avisos.append("hay un bocadillo sin palabras dentro")
    # 29-10 (S010): el bocadillo no puede ser la frase del narrador
    if re.search(r"bubble", letras, re.I):
        plano = re.sub(r"[^a-z0-9 ]", "", frase.lower().replace("’", "'").replace("'", ""))
        trozos = [t for t in re.split(r",|;|\band\b", letras) if "bubble" in t.lower()]
        for par in _comillas(" ".join(trozos)):
            dicho = re.sub(r"[^a-z0-9 ]", "", "".join(par).lower().replace("'", "")).strip()
            if dicho and dicho in plano:
                avisos.append(f"el bocadillo repite la frase del narrador («{''.join(par)}»)")
    for cosa in ("sign", "poster", "headline", "banner", "placard", "label", "page"):
        if re.search(rf"\b{cosa}s?\b", bajo) and "blank" not in bajo and not letras:
            avisos.append(f"sale un «{cosa}» sin letras y la escena no lo pide «blank»")
            break

    # pantalla dividida = dos mitades + cuadros de epoca
    dividida = "split screen" in bajo or ("left half" in bajo and "right half" in bajo)
    if "split screen" in frase.lower() and not dividida:
        avisos.append("la frase pide «split screen» y la escena no es una pantalla dividida")
    if dividida and len(_comillas(letras)) < 2:
        avisos.append("pantalla dividida sin sus dos cuadros de epoca en «letras»")

    # la cifra: enorme, salvo que ya vaya en la cartela
    if cartela:
        cifra_cartela = re.findall(r"\d[\d.,]*", cartela)
        if any(c in letras or c in escena for c in cifra_cartela):
            avisos.append(f"repite en el dibujo la cifra que ya sale en la cartela («{cartela}»)")
    elif CIFRA.search(frase) and not re.search(r"\d", letras) and ficha.get("tipo") == "cifra":
        avisos.append("plano de cifra sin la cifra en «letras»")
    if (not dividida and re.search(r"\d", letras)
            and "huge" not in letras.lower() and "large" not in letras.lower()):
        avisos.append("la cifra no va en letras ENORMES («huge bold hand-drawn letters»)")

    # cosas que Enrique odia
    for mala in PROHIBIDAS:
        for texto in (bajo, letras.lower()):
            for casa in re.finditer(re.escape(mala), texto):
                if not NEGACION.search(texto[:casa.start()]):   # «nobody in shadow» vale
                    avisos.append(f"lleva «{mala}»")
                    break
    for casa in REPETIDO.finditer(escena):
        if casa.group(1).lower() not in NO_OBJETO:
            avisos.append(f"posible objeto repetido: «{casa.group(0)}»")
    for casa in re.finditer(r"row of (?:doors|front doors)|under (?:one|a single) roof", bajo):
        if not NEGACION.search(bajo[:casa.start()]):   # «never a row of doors...» vale (30-10)
            avisos.append("fila de puertas bajo un solo tejado")
            break
    # 30-10 (Sonnet, S132): nada flota ni se apoya en la raya de una pantalla dividida
    for casa in re.finditer(r"on the dividing line|floating|floats|hovering|in mid-?air", bajo):
        if not NEGACION.search(bajo[:casa.start()]):
            avisos.append("algo flota o esta sobre la raya del medio")
            break
    # 30-10 (Sonnet, S006): las reglas de la calle que Opus ponia solo
    if ficha.get("tipo") == "calle":
        if "separate" not in bajo:
            avisos.append("calle sin decir «casas separadas» (each house separate)")
        if (re.search(r"homeowner|grown-up|adult", bajo)
                and not re.search(r"no costume|not in costume|everyday clothes|ordinary clothes",
                                  bajo)):
            avisos.append("calle sin decir que el adulto de la casa va sin disfraz")
    # 30-10 (Sonnet, S003): lo que el sitio pone en las manos del protagonista
    con_prota = any(h in HOJAS_PROTA for h in hojas)
    for cosa in ("bowl",):
        if con_prota and cosa in pista.lower() and cosa not in bajo:
            avisos.append(f"el sitio tiene «{cosa}» y la escena no lo pone")
    return avisos
