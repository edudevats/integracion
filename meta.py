"""API de conversiones (CAPI) de Meta.

Se usa el token que el dueño genera en el Administrador de eventos
(Configuración del pixel > API de conversiones > Generar token de acceso).
Ese token solo permite enviar eventos a ese pixel y no vence.
"""
import hashlib
import re
import unicodedata

import requests

import config

GRAPH = "https://graph.facebook.com"
# Códigos de Meta que significan "el token ya no sirve o no tiene permiso"
AUTH_ERROR_CODES = {102, 190, 10, 200, 294}


class MetaError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code

    @property
    def is_auth(self):
        return self.code in AUTH_ERROR_CODES


def _graph_url(path):
    return f"{GRAPH}/{config.FB_GRAPH_VERSION}/{path.lstrip('/')}"


def _check(r):
    try:
        body = r.json()
    except ValueError:
        raise MetaError(f"Respuesta inválida de Meta ({r.status_code}): {r.text[:200]}")
    if r.status_code >= 400 or "error" in body:
        err = body.get("error", {})
        msg = err.get("error_user_msg") or err.get("message") or str(body)[:300]
        raise MetaError(f"Meta ({r.status_code}): {msg}", code=err.get("code"))
    return body


def verify_dataset(token, dataset_id):
    """Comprueba que el token pueda leer el pixel. Devuelve su nombre."""
    r = requests.get(_graph_url(dataset_id), params={"fields": "id,name"},
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    return _check(r).get("name") or dataset_id


def send_events(dataset_id, token, events, test_event_code=""):
    # El token va en el cuerpo (no en la URL) para que no quede en bitácoras de servidores
    payload = {"data": events, "access_token": token}
    if test_event_code:
        payload["test_event_code"] = test_event_code
    r = requests.post(_graph_url(f"{dataset_id}/events"), json=payload, timeout=30)
    return _check(r)


# ---------- normalización y hash (requisito de Meta) ----------
def sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm_name(s):
    s = _strip_accents((s or "").strip().lower())
    return re.sub(r"[^a-zñ]", "", s)


def phone_variants(raw, country=None):
    """Teléfono en formato E.164 sin '+', con variantes para México (52 / 521)."""
    country = (country or config.DEFAULT_COUNTRY).lower()
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) < 8:
        return []
    if country == "mx":
        if len(digits) == 10:
            local = digits
        elif digits.startswith("521") and len(digits) == 13:
            local = digits[3:]
        elif digits.startswith("52") and len(digits) == 12:
            local = digits[2:]
        else:
            return [digits]
        return [f"52{local}", f"521{local}"]
    return [digits]


def build_user_data(contact, external_id=None):
    """contact: dict con phone, email, first_name, last_name, ctwa_clid, country."""
    ud = {}
    phones = phone_variants(contact.get("phone"), contact.get("country"))
    if phones:
        ud["ph"] = [sha256(p) for p in phones]
    email = (contact.get("email") or "").strip().lower()
    if "@" in email:
        ud["em"] = [sha256(email)]
    fn, ln = norm_name(contact.get("first_name")), norm_name(contact.get("last_name"))
    if fn:
        ud["fn"] = [sha256(fn)]
    if ln:
        ud["ln"] = [sha256(ln)]
    country = (contact.get("country") or config.DEFAULT_COUNTRY).lower()
    if country:
        ud["country"] = [sha256(country)]
    if external_id:
        ud["external_id"] = [sha256(str(external_id))]
    return ud


def build_purchase_event(*, event_id, event_time, value, currency, contact, content_name=None,
                         external_id=None, order_id=None, waba_id=None):
    user_data = build_user_data(contact, external_id)
    event = {
        "event_name": "Purchase",
        "event_time": int(event_time),
        "event_id": str(event_id),
        "action_source": "system_generated",
        "user_data": user_data,
        "custom_data": {"currency": (currency or config.DEFAULT_CURRENCY).upper(),
                        "value": round(float(value or 0), 2)},
    }
    if content_name:
        event["custom_data"]["content_name"] = content_name[:200]
    if order_id:
        event["custom_data"]["order_id"] = str(order_id)
    ctwa = contact.get("ctwa_clid")
    if ctwa and waba_id:
        # Venta de un lead que llegó por anuncio "clic a WhatsApp": atribución directa al anuncio.
        event["action_source"] = "business_messaging"
        event["messaging_channel"] = "whatsapp"
        user_data["ctwa_clid"] = ctwa
        user_data["whatsapp_business_account_id"] = waba_id
    return event
