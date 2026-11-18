import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _get(name, default=""):
    return (os.getenv(name) or default).strip()


APP_USERNAME = _get("APP_USERNAME", "admin")
APP_PASSWORD = _get("APP_PASSWORD")
APP_SECRET_KEY = _get("APP_SECRET_KEY")

LEADSALES_EMAIL = _get("LEADSALES_EMAIL")
LEADSALES_PASSWORD = _get("LEADSALES_PASSWORD")
LEADSALES_WORKSPACE_ID = _get("LEADSALES_WORKSPACE_ID")
# Clave pública de Firebase que usa el CRM web de Leadsales (viene en su JS público).
LEADSALES_FIREBASE_API_KEY = _get(
    "LEADSALES_FIREBASE_API_KEY", "AIzaSyBK1pfNO6QAyXB0B_B77SoislelNZhln9Q"
)
LEADSALES_API_BASE = _get("LEADSALES_API_BASE", "https://api.leadsales.services/v2/web")

FB_APP_ID = _get("FB_APP_ID")
FB_APP_SECRET = _get("FB_APP_SECRET")
FB_REDIRECT_URI = _get("FB_REDIRECT_URI", "http://localhost:5000/facebook/callback")
FB_GRAPH_VERSION = _get("FB_GRAPH_VERSION", "v24.0")
WHATSAPP_BUSINESS_ACCOUNT_ID = _get("WHATSAPP_BUSINESS_ACCOUNT_ID")

# La conexión con Facebook se pide de nuevo cada N días (el token de Meta dura ~60).
FB_RECONNECT_DAYS = max(1, int(_get("FB_RECONNECT_DAYS", "60")))
# Primera revisión: cuántos días hacia atrás buscar ventas.
SYNC_LOOKBACK_DAYS = max(1, int(_get("SYNC_LOOKBACK_DAYS", "3")))
# Nunca buscar más atrás de esto (Meta solo acepta ventas de los últimos 7 días).
SYNC_MAX_CATCHUP_DAYS = 8
DAILY_RUN_LABEL = _get("DAILY_RUN_LABEL", "12:00 am (hora de CDMX)")
DEFAULT_COUNTRY = _get("DEFAULT_COUNTRY", "mx").lower()
DEFAULT_CURRENCY = _get("DEFAULT_CURRENCY", "MXN").upper()

DATA_DIR = BASE_DIR / "data"
DB_FILE = DATA_DIR / "app.db"
SECURE_COOKIES = FB_REDIRECT_URI.startswith("https://")


def missing_settings():
    """Variables obligatorias que faltan en .env (solo nombres, nunca valores)."""
    required = {
        "APP_SECRET_KEY": APP_SECRET_KEY,
        "LEADSALES_EMAIL": LEADSALES_EMAIL,
        "LEADSALES_PASSWORD": LEADSALES_PASSWORD,
        "FB_APP_ID": FB_APP_ID,
        "FB_APP_SECRET": FB_APP_SECRET,
    }
    return [k for k, v in required.items() if not v]
