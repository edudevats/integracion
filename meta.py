"""Facebook Login + Conversions API (CAPI) de Meta."""
import hashlib
import re
import time
import unicodedata
from urllib.parse import urlencode

import requests

import config

GRAPH = "https://graph.facebook.com"
SCOPES = ["ads_management", "business_management", "ads_read"]


class MetaError(Exception):
    pass


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
        raise MetaError(f"Meta ({r.status_code}): {msg}")
    return body


# ---------- Facebook Login (OAuth) ----------
def login_url(state):
    q = {
        "client_id": config.FB_APP_ID,
        "redirect_uri": config.FB_REDIRECT_URI,
        "state": state,
        "scope": ",".join(SCOPES),
        "response_type": "code",
    }
    return f"https://www.facebook.com/{config.FB_GRAPH_VERSION}/dialog/oauth?{urlencode(q)}"


def exchange_code(code):
    """code -> token de usuario de larga duración (~60 días)."""
    short = _check(requests.get(_graph_url("oauth/access_token"), params={
        "client_id": config.FB_APP_ID,
        "client_secret": config.FB_APP_SECRET,
        "redirect_uri": config.FB_REDIRECT_URI,
        "code": code,
    }, timeout=30))
    long = _check(requests.get(_graph_url("oauth/access_token"), params={
        "grant_type": "fb_exchange_token",
        "client_id": config.FB_APP_ID,
        "client_secret": config.FB_APP_SECRET,
        "fb_exchange_token": short["access_token"],
    }, timeout=30))
    expires_in = int(long.get("expires_in") or 60 * 24 * 3600)
    me = _check(requests.get(_graph_url("me"), params={
        "fields": "id,name", "access_token": long["access_token"]}, timeout=30))
    return {
        "token": long["access_token"],
        "connected_at": int(time.time()),
        "expires_at": int(time.time()) + expires_in,
        "user_id": me.get("id"),
        "user_name": me.get("name"),
    }


def list_datasets(token):
    """Píxeles / datasets a los que tiene acceso el usuario (vía cuentas publicitarias y negocios)."""
    found = {}

    def add(px, origin):
        if px and px.get("id") and px["id"] not in found:
            found[px["id"]] = {"id": px["id"], "name": px.get("name") or px["id"], "origin": origin}

    try:
        body = _check(requests.get(_graph_url("me/adaccounts"), params={
            "fields": "name,adspixels{id,name}", "limit": 100, "access_token": token}, timeout=30))
        for acc in body.get("data", []):
            for px in (acc.get("adspixels") or {}).get("data", []):
                add(px, f"Cuenta publicitaria: {acc.get('name')}")
    except MetaError:
        pass
    try:
        body = _check(requests.get(_graph_url("me/businesses"), params={
            "fields": "name,owned_pixels{id,name}", "limit": 100, "access_token": token}, timeout=30))
        for biz in body.get("data", []):
            for px in (biz.get("owned_pixels") or {}).get("data", []):
                add(px, f"Negocio: {biz.get('name')}")
    except MetaError:
        pass
    return sorted(found.values(), key=lambda p: p["name"].lower())


def send_events(dataset_id, token, events, test_event_code=""):
    payload = {"data": events}
    if test_event_code:
        payload["test_event_code"] = test_event_code
    r = requests.post(_graph_url(f"{dataset_id}/events"), params={"access_token": token},
                      json=payload, timeout=30)
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
                         external_id=None, order_id=None):
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
    if ctwa and config.WHATSAPP_BUSINESS_ACCOUNT_ID:
        # Venta de un lead que llegó por anuncio "clic a WhatsApp": atribución directa al anuncio.
        event["action_source"] = "business_messaging"
        event["messaging_channel"] = "whatsapp"
        user_data["ctwa_clid"] = ctwa
        user_data["whatsapp_business_account_id"] = config.WHATSAPP_BUSINESS_ACCOUNT_ID
    return event
