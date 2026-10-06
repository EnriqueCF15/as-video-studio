"""
Lo que el Estudio le pide a Windows mientras trabaja solo (fork, Fase 4).

DOS COSAS Y NINGUNA AJUSTA NADA DEL SISTEMA:

  notificar(titulo, texto)   la burbuja de Windows de abajo a la derecha, con su
                             sonido. Un render de un video de veinte minutos
                             tarda horas: nadie mira la barra todo ese rato, y
                             sin esto la unica forma de saber que acabo era
                             volver a la pantalla a mirar.
  despierto()                mientras dura el bloque, Windows no suspende el
                             portatil por inactividad. Es la misma peticion que
                             hace un reproductor de video (SetThreadExecutionState)
                             y se retira sola al salir: no cambia el plan de
                             energia ni lo que pasa al cerrar la tapa.

Las dos NUNCA levantan: un aviso que no sale no puede tumbar un render que ha
salido bien. Fuera de Windows no hacen nada, y con ESTUDIO_SIN_AVISOS=1 (lo
ponen las pruebas) tampoco se ve ninguna burbuja.
"""
import base64
import contextlib
import os
import subprocess
from xml.sax.saxutils import escape

#: El remitente de la burbuja. Windows solo ensena las de una aplicacion que
#: conoce, y PowerShell viene registrado en todas las instalaciones.
REMITENTE = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _activos():
    return os.name == "nt" and str(os.environ.get("ESTUDIO_SIN_AVISOS", "")).strip() \
        not in ("1", "true", "si")


def _orden_powershell(titulo, texto):
    """El script que ensena la burbuja, en el -EncodedCommand de PowerShell.

    En base64 y con el texto escapado para XML y para la comilla simple: el
    nombre de un proyecto lo escribe quien sea y no puede convertirse en codigo.
    """
    xml = ("<toast><visual><binding template='ToastGeneric'>"
           f"<text>{escape(str(titulo))[:120]}</text>"
           f"<text>{escape(str(texto))[:300]}</text>"
           "</binding></visual>"
           "<audio src='ms-winsoundevent:Notification.Default'/></toast>")
    xml = xml.replace("'", "''")
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType = WindowsRuntime] > $null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
        " ContentType = WindowsRuntime] > $null\n"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        f"$x.LoadXml('{xml}')\n"
        "$t = [Windows.UI.Notifications.ToastNotification]::new($x)\n"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        f"'{REMITENTE}').Show($t)\n")
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def notificar(titulo, texto=""):
    """Ensena la burbuja de Windows. -> True si se pidio, False si no."""
    if not _activos():
        return False
    try:
        # powershell.exe y no pwsh: el 7 no trae las clases de Windows.UI
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-EncodedCommand",
             _orden_powershell(titulo, texto)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        return True
    except Exception:                                          # noqa: BLE001
        return False


@contextlib.contextmanager
def despierto():
    """Que el portatil no se suspenda mientras corre el bloque.

    La peticion es DEL HILO que la hace (por eso se pide y se retira aqui, en
    el mismo hilo del trabajo) y Windows la olvida sola si el hilo muere.
    """
    pedido = False
    if os.name == "nt":
        try:
            import ctypes                                      # noqa: PLC0415
            pedido = bool(ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
        except Exception:                                      # noqa: BLE001
            pedido = False
    try:
        yield pedido
    finally:
        if pedido:
            try:
                import ctypes                                  # noqa: PLC0415
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            except Exception:                                  # noqa: BLE001
                pass
