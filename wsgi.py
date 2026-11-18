# WSGI para PythonAnywhere.
# Pestaña "Web" -> "WSGI configuration file": borra todo su contenido y pega este archivo.
import logging
import os
import sys
import time

from dotenv import load_dotenv

# Zona horaria de Ciudad de México
os.environ["TZ"] = "America/Mexico_City"
try:
    time.tzset()
except AttributeError:
    # time.tzset() solo existe en Unix
    pass

# Carpeta del proyecto en PythonAnywhere
project_home = "/home/RedMedicaPro/integracion"
if project_home not in sys.path:
    sys.path.insert(0, project_home)
os.chdir(project_home)

logging.basicConfig()
logger = logging.getLogger(__name__)

# Cargar .env SOLO desde la carpeta del proyecto, antes de importar la app.
# override=False: las variables que ya existan en el entorno tienen prioridad.
env_path = os.path.join(project_home, ".env")
if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path, override=False, verbose=False)
else:
    logger.warning(f"No se encontró el archivo .env en: {env_path}")

import config  # noqa: E402

# Verificar variables obligatorias (solo se registran los nombres, nunca los valores)
missing = config.missing_settings()
if missing:
    logger.error(
        "CRÍTICO: faltan variables en .env: %s. Revisado: %s",
        ", ".join(missing),
        env_path,
    )

from app import app  # noqa: E402

# PythonAnywhere espera que la variable se llame 'application'
application = app

if __name__ == "__main__":
    # Solo para pruebas: python wsgi.py. En producción lo sirve PythonAnywhere.
    app.run()
