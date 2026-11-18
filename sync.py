"""Busca ventas ("Vendido") en Leadsales y las manda a Meta como eventos Purchase."""
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

import config
import meta
import store
from leadsales import LeadsalesClient, LeadsalesError, lead_id_of

_client = None
_sync_lock = threading.Lock()
OVERLAP = timedelta(minutes=30)
MAX_EVENT_AGE = 7 * 24 * 3600  # Meta rechaza eventos con más de 7 días


def client():
    global _client
    if _client is None:
        _client = LeadsalesClient()
    return _client


# ---------- extracción de datos del lead ----------
def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, obj


def _first(lead, key_regex, value_ok=lambda v: True):
    rx = re.compile(key_regex, re.I)
    for path, value in _walk(lead):
        last = path.split(".")[-1].split("[")[0]
        if rx.search(last) and value not in (None, "") and value_ok(value):
            return value
    return None


def _is_phone(v):
    return len(re.sub(r"\D", "", str(v))) >= 10


def extract_contact(lead):
    name = lead.get("name") or lead.get("fullName") or lead.get("full_name") or ""
    phone = _first(lead, r"^(phone|phoneNumber|phone_number|telefono|whatsapp|wa_?id|cellphone|mobile)$",
                   _is_phone)
    if not phone and re.fullmatch(r"\+?\d{10,15}", str(name).strip()):
        phone = name  # Leadsales usa el número como nombre cuando no hay perfil
        name = ""
    email = _first(lead, r"e-?mail", lambda v: "@" in str(v))
    ctwa = _first(lead, r"^ctwa_?clid$")
    first = lead.get("firstName") or lead.get("first_name")
    last = lead.get("lastName") or lead.get("last_name")
    if not first and name and not re.search(r"\d", name):
        parts = name.split()
        first = parts[0] if parts else ""
        last = " ".join(parts[1:]) if len(parts) > 1 else ""
    return {"phone": phone, "email": email, "first_name": first or "", "last_name": last or "",
            "ctwa_clid": ctwa, "name": lead.get("name") or name}


def _sold_results(summary):
    results = summary.get("results") if isinstance(summary, dict) else summary
    for r in results or []:
        kind = (r.get("type") or r.get("kind") or r.get("result") or "").lower()
        if kind != "sold":
            continue
        if r.get("deleted_at") or r.get("voided_at") or (r.get("status") or "").lower() in (
                "deleted", "voided", "reverted"):
            continue
        yield r


def _parse_ts(value):
    if not value:
        return None
    s = str(value).replace(" ", "T")
    if len(s) == 10:  # solo fecha
        s += "T12:00:00"
    if not re.search(r"(Z|[+-]\d{2}:?\d{2})$", s):
        s += "+00:00"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _event_time(sale):
    now = time.time()
    for key in ("created_at", "sale_date", "createdAt"):
        ts = _parse_ts(sale.get(key))
        if ts:
            return min(ts, now)
    return now


# ---------- envío ----------
def send_sale(lead, sale, contact, fb, settings):
    sale_id = sale.get("uuid") or sale.get("id")
    event_time = _event_time(sale)
    amount = sale.get("amount") or sale.get("value") or 0
    try:
        amount = float(str(amount).replace(",", ""))
    except ValueError:
        amount = 0.0
    lead_id = lead_id_of(lead)
    event = meta.build_purchase_event(
        event_id=f"leadsales-sale-{sale_id}", event_time=event_time, value=amount,
        currency=sale.get("currency"), contact=contact, content_name=sale.get("what_sold"),
        external_id=lead_id, order_id=sale_id)
    record = {
        "lead_id": lead_id, "client_name": contact.get("name") or contact.get("phone"),
        "phone": str(contact.get("phone") or ""), "email": contact.get("email") or "",
        "amount": amount,
        "currency": event["custom_data"]["currency"], "what_sold": sale.get("what_sold"),
        "event_time": datetime.fromtimestamp(event_time, timezone.utc).isoformat(timespec="seconds"),
        "has_phone": "ph" in event["user_data"], "has_ctwa": bool(contact.get("ctwa_clid")),
        "action_source": event["action_source"], "test": bool(settings.get("test_event_code")),
        "manual": bool(sale.get("manual")),
    }
    if time.time() - event_time > MAX_EVENT_AGE:
        record.update(status="omitido", detail="La venta tiene más de 7 días; Meta no la acepta")
    elif not (event["user_data"].get("ph") or event["user_data"].get("em")
              or event["user_data"].get("ctwa_clid")):
        record.update(status="omitido", detail="El lead no tiene teléfono ni email")
    else:
        try:
            resp = meta.send_events(fb["dataset_id"], fb["token"], [event],
                                    settings.get("test_event_code", ""))
            record.update(status="enviado", detail=f"events_received={resp.get('events_received')}",
                          fbtrace_id=resp.get("fbtrace_id"))
        except (meta.MetaError, requests.RequestException) as e:
            record.update(status="error", detail=str(e))
    store.save_sale(sale_id, record)
    store.log("info" if record["status"] == "enviado" else "warning",
              f"Venta {record['status']}: {record['client_name'] or 'sin nombre'} "
              f"${record['amount']:,.2f}", detail=record.get("detail"))
    return sale_id, record


def _fail(message):
    store.log("error", message)
    store.put("last_sync_result", {"at": store.now_iso(), "ok": False, "message": message})
    return {"ok": False, "message": message}


def run_sync(full_days=None):
    """Una pasada de sincronización. Devuelve un resumen."""
    if not _sync_lock.acquire(blocking=False):
        return {"ok": False, "message": "Ya hay una sincronización en curso"}
    try:
        return _run(full_days)
    finally:
        _sync_lock.release()


def _run(full_days):
    fb, settings, last_sync = store.get("facebook"), store.get("settings"), store.get("last_sync")
    status = store.fb_status(fb)
    if status["state"] == "none" or not fb.get("dataset_id"):
        return _fail("Primero conecta Facebook y elige el pixel/dataset")
    if status["state"] == "expired":
        return _fail(f"La conexión con Facebook venció (cada {config.FB_RECONNECT_DAYS} días). "
                     "Vuelve a conectar; las ventas pendientes se enviarán en la siguiente revisión.")

    started = datetime.now(timezone.utc)
    if full_days:
        since = started - timedelta(days=full_days)
    elif last_sync:
        since = datetime.fromisoformat(last_sync) - OVERLAP
    else:
        since = started - timedelta(days=config.SYNC_LOOKBACK_DAYS)
    since = max(since, started - timedelta(days=config.SYNC_MAX_CATCHUP_DAYS))
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    summary = {"leads": 0, "ventas": 0, "enviadas": 0, "errores": 0, "omitidas": 0}
    c = client()
    try:
        seen = set()
        for active in (True, False):
            for lead in c.iter_leads(since_iso, active=active):
                lid = lead_id_of(lead)
                if not lid or lid in seen:
                    continue
                seen.add(lid)
                summary["leads"] += 1
                sales = list(_sold_results(c.close_events(lid)))
                if not sales:
                    continue
                contact = extract_contact(lead)
                # los que dieron error se reintentan; el resto ya no se reenvía
                done = store.done_sale_ids([s.get("uuid") or s.get("id") for s in sales])
                for sale in sales:
                    summary["ventas"] += 1
                    sid = sale.get("uuid") or sale.get("id")
                    if not sid or sid in done:
                        continue
                    _, record = send_sale(lead, sale, contact, fb, settings)
                    key = {"enviado": "enviadas", "error": "errores"}.get(record["status"], "omitidas")
                    summary[key] += 1
    except (LeadsalesError, requests.RequestException) as e:
        return _fail(f"Error con Leadsales: {e}")

    msg = (f"{summary['leads']} leads revisados, {summary['ventas']} ventas encontradas, "
           f"{summary['enviadas']} enviadas a Meta, {summary['omitidas']} omitidas, "
           f"{summary['errores']} con error")

    store.put("last_sync", started.isoformat(timespec="seconds"))
    store.put("last_sync_result", {"at": store.now_iso(), "ok": True, "message": msg, **summary})
    store.log("info", f"Sincronización: {msg}")
    return {"ok": True, "message": msg, **summary}


def send_manual(phone, amount, currency, name="", what_sold=""):
    """Envía una venta capturada a mano (por si el vendedor no la marcó en Leadsales)."""
    fb, settings = store.get("facebook"), store.get("settings")
    if store.fb_status(fb)["state"] != "connected" or not fb.get("dataset_id"):
        raise meta.MetaError("Conecta Facebook (o vuelve a conectar) y elige el pixel/dataset")
    parts = (name or "").split()
    contact = {"phone": phone, "first_name": parts[0] if parts else "",
               "last_name": " ".join(parts[1:]), "name": name}
    sale = {"uuid": f"manual-{int(time.time() * 1000)}", "amount": amount, "currency": currency,
            "what_sold": what_sold, "manual": True}
    _, record = send_sale({"id": None}, sale, contact, fb, settings)
    return record
