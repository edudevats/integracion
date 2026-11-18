# Copia este contenido al archivo WSGI de tu app web en PythonAnywhere
# (pestaña "Web" -> "WSGI configuration file") y cambia la ruta del proyecto.
import sys

PROJECT_DIR = "/home/TUUSUARIO/integracion"
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from app import app as application  # noqa: E402
