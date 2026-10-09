r"""
LAS IDEAS DE IMAGEN DEL AUTOR: lo que quien escribio el guion quiere VER.

De donde sale (fork, 08-10-2026)
--------------------------------
Un guion escrito a mano suele traer, junto a la narracion, notas de imagen:
«[VISUAL: pantalla partida, a la izquierda tu en la oficina...]». El estudio
solo se quedaba con lo que se locuta --los bloques del guion son {id, texto}--,
asi que esas notas se perdian y cada plano lo imaginaba `direccion` leyendo
solo la frase. Con esto, quien decide que se ve (`direccion`) y quien decide
quien sale y donde (`catalogo_visual`) las tienen delante.

Por que es un FICHERO y no un param
-----------------------------------
Todo param de un paso entra en su firma, y la firma de `assets` es la de cada
plano: guardar aqui las ideas como param haria que retocar una coma dejara
obsoletos los doscientos planos, y el siguiente «Generar» los volveria a pagar.
Las ideas no dibujan nada por si mismas: las LEEN dos agentes cuando corren, y
lo que esos agentes deciden SI se guarda por plano, con su firma. Es el mismo
trato que la `peticion` que se le pasa al catalogo: una entrada del agente, no
del paso. Cambiarlas no rehace nada sola; vale para la proxima vez que se
dirija.
"""
import os

#: Donde viven, dentro de la carpeta del proyecto.
NOMBRE = "ideas_visuales.txt"

#: Tope de lo que se guarda. Un guion de 25 minutos con sus notas de imagen son
#: unos 35.000 caracteres; esto deja margen sin dejar que un pegado accidental
#: de un libro entero acabe dentro de dos prompts.
TOPE_CARACTERES = 80000


def ruta(proyecto):
    return os.path.join(proyecto.raiz, NOMBRE)


def limpiar(texto):
    """Saltos de linea normalizados, sin espacios colgando. -> str"""
    lineas = str(texto or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(l.rstrip() for l in lineas).strip()


def leer(proyecto):
    """Las ideas de este video, o "" si no tiene. -> str"""
    try:
        with open(ruta(proyecto), encoding="utf-8") as fh:
            return limpiar(fh.read())
    except OSError:
        return ""


def guardar(proyecto, texto):
    """Las guarda (o las quita, si llegan vacias). -> str guardado

    Demasiado largo es un error y no un recorte: cortar por la mitad las notas
    de alguien dejaria la segunda mitad del video sin ellas y sin decirlo.
    """
    limpio = limpiar(texto)
    if len(limpio) > TOPE_CARACTERES:
        raise ValueError(f"las ideas de imagen ocupan {len(limpio)} caracteres "
                         f"y el tope son {TOPE_CARACTERES}")
    destino = ruta(proyecto)
    if not limpio:
        try:
            os.remove(destino)
        except OSError:
            pass
        return ""
    temporal = destino + ".tmp"
    with open(temporal, "w", encoding="utf-8") as fh:
        fh.write(limpio + "\n")
    os.replace(temporal, destino)
    return limpio
