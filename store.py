"""Base de datos SQLite de la app (data/app.db).

Tablas:
  users       usuarios del panel (contraseña con hash, nunca en texto plano)
  kv          ajustes y estado: facebook, settings, last_sync, last_sync_result
  sent_sales  cada venta procesada (cliente, monto, estado del envío a Meta)
  log         bitácora
"""
import csv
import io
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from werkzeug.security import check_password_hash, generate_password_hash

import config

MAX_LOG = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sent_sales (
    sale_id       TEXT PRIMARY KEY,
    lead_id       TEXT,
    client_name   TEXT,
    phone         TEXT,
    email         TEXT,
    amount        REAL NOT NULL DEFAULT 0,
    currency      TEXT,
    what_sold     TEXT,
    event_time    TEXT,
    status        TEXT NOT NULL,          -- enviado | omitido | error
    detail        TEXT,
    action_source TEXT,
    has_phone     INTEGER NOT NULL DEFAULT 0,
    has_ctwa      INTEGER NOT NULL DEFAULT 0,
    test          INTEGER NOT NULL DEFAULT 0,
    manual        INTEGER NOT NULL DEFAULT 0,
    attempts      INTEGER NOT NULL DEFAULT 1,
    processed_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sales_processed ON sent_sales(processed_at);
CREATE TABLE IF NOT EXISTS log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    level   TEXT NOT NULL,
    message TEXT NOT NULL,
    detail  TEXT
);
"""

_DEFAULTS = {
    "facebook": {},  # token_enc (cifrado), token_hint, dataset_id, dataset_name, waba_id, invalid
    "settings": {"test_event_code": "", "enabled": True},
    "last_sync": None,
    "last_sync_result": None,
}
_initialized = False


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    """Conexión con transacción; SQLite se encarga del bloqueo entre la web y la tarea diaria."""
    global _initialized
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if not _initialized:
            conn.executescript(SCHEMA)
            _initialized = True
        with conn:
            yield conn
    finally:
        conn.close()


# ---------- ajustes / estado ----------
def get(key):
    with connect() as db:
        row = db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    if row is None:
        return json.loads(json.dumps(_DEFAULTS.get(key)))
    return json.loads(row["value"])


def put(key, value):
    with connect() as db:
        db.execute("INSERT INTO kv(key, value) VALUES(?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                   (key, json.dumps(value, ensure_ascii=False)))


def patch(key, **changes):
    value = get(key) or {}
    value.update(changes)
    put(key, value)
    return value


# ---------- conexión con Meta (token cifrado) ----------
class TokenError(Exception):
    pass


def _fernet():
    """Llave de cifrado derivada de APP_SECRET_KEY (si esa clave cambia, hay que volver a pegar el token)."""
    if not config.APP_SECRET_KEY:
        raise TokenError("Falta APP_SECRET_KEY en .env; se necesita para cifrar el token")
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"ventas-meta/token/v1",
               info=b"meta-capi-token").derive(config.APP_SECRET_KEY.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def save_facebook(token, dataset_id, dataset_name="", waba_id=""):
    put("facebook", {
        "token_enc": _fernet().encrypt(token.encode()).decode(),
        "token_hint": f"…{token[-4:]}",
        "dataset_id": dataset_id,
        "dataset_name": dataset_name or dataset_id,
        "waba_id": waba_id,
        "connected_at": int(time.time()),
        "invalid": None,
    })


def facebook():
    """Datos de conexión sin el token (para mostrar en el panel)."""
    fb = get("facebook") or {}
    return {k: v for k, v in fb.items() if k != "token_enc"}


def facebook_token():
    fb = get("facebook") or {}
    if not fb.get("token_enc"):
        return None
    try:
        return _fernet().decrypt(fb["token_enc"].encode()).decode()
    except InvalidToken:
        raise TokenError("No se pudo descifrar el token (¿cambió APP_SECRET_KEY?). Vuelve a pegarlo.")


def fb_status(fb):
    """none = sin token; invalid = Meta lo rechazó; incomplete = falta el pixel; connected."""
    if not fb.get("token_hint") and not fb.get("token_enc"):
        return {"state": "none"}
    if fb.get("invalid"):
        return {"state": "invalid", "reason": fb["invalid"]}
    if not fb.get("dataset_id"):
        return {"state": "incomplete"}
    return {"state": "connected"}


# ---------- ventas / clientes enviados ----------
SALE_FIELDS = ("lead_id", "client_name", "phone", "email", "amount", "currency", "what_sold",
               "event_time", "status", "detail", "action_source", "has_phone", "has_ctwa",
               "test", "manual")


def done_sale_ids(ids):
    """De estas ventas, cuáles ya no hay que reenviar (todo menos las que dieron error)."""
    ids = [i for i in ids if i]
    if not ids:
        return set()
    with connect() as db:
        rows = db.execute(
            f"SELECT sale_id FROM sent_sales WHERE status != 'error' AND sale_id IN "
            f"({','.join('?' * len(ids))})", ids).fetchall()
    return {r["sale_id"] for r in rows}


def save_sale(sale_id, record):
    values = [record.get(f) for f in SALE_FIELDS]
    values = [int(v) if isinstance(v, bool) else v for v in values]
    cols = ", ".join(SALE_FIELDS)
    updates = ", ".join(f"{f} = excluded.{f}" for f in SALE_FIELDS)
    with connect() as db:
        db.execute(
            f"INSERT INTO sent_sales(sale_id, {cols}, processed_at) "
            f"VALUES(?, {','.join('?' * len(SALE_FIELDS))}, ?) "
            f"ON CONFLICT(sale_id) DO UPDATE SET {updates}, processed_at = excluded.processed_at, "
            f"attempts = sent_sales.attempts + 1",
            [sale_id, *values, now_iso()])


def list_sales(query="", status="", limit=500):
    sql = "SELECT * FROM sent_sales WHERE 1=1"
    args = []
    if query:
        sql += " AND (client_name LIKE ? OR phone LIKE ? OR email LIKE ? OR what_sold LIKE ?)"
        args += [f"%{query}%"] * 4
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY processed_at DESC LIMIT ?"
    args.append(limit)
    with connect() as db:
        return [dict(r) for r in db.execute(sql, args).fetchall()]


def sales_totals():
    with connect() as db:
        row = db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total, "
            "COUNT(DISTINCT COALESCE(NULLIF(phone, ''), lead_id, sale_id)) AS clients "
            "FROM sent_sales WHERE status = 'enviado' AND test = 0").fetchone()
    return dict(row)


def sales_csv(rows):
    out = io.StringIO()
    cols = ["processed_at", "event_time", "client_name", "phone", "email", "amount", "currency",
            "what_sold", "status", "detail", "test", "manual", "lead_id", "sale_id"]
    w = csv.DictWriter(out, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return "﻿" + out.getvalue()  # BOM para que Excel respete los acentos


# ---------- bitácora ----------
def log(level, message, detail=None):
    with connect() as db:
        db.execute("INSERT INTO log(at, level, message, detail) VALUES(?, ?, ?, ?)",
                   (now_iso(), level, message, detail))
        db.execute("DELETE FROM log WHERE id <= (SELECT MAX(id) FROM log) - ?", (MAX_LOG,))
    print(f"[{now_iso()}] {level.upper()}: {message}", flush=True)


def recent_logs(limit=40):
    with connect() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


# ---------- usuarios ----------
MIN_PASSWORD = 8


def ensure_admin():
    """Primera vez: crea el usuario con APP_USERNAME / APP_PASSWORD del .env.
    Después la contraseña vive en la base y se cambia desde el panel."""
    with connect() as db:
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return False
        if not config.APP_PASSWORD:
            return False
        db.execute("INSERT INTO users(username, password_hash, created_at, updated_at) "
                   "VALUES(?, ?, ?, ?)",
                   (config.APP_USERNAME, generate_password_hash(config.APP_PASSWORD),
                    now_iso(), now_iso()))
    log("info", f"Usuario '{config.APP_USERNAME}' creado desde .env")
    return True


def has_users():
    with connect() as db:
        return db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def check_login(username, password):
    with connect() as db:
        row = db.execute("SELECT password_hash FROM users WHERE username = ?",
                         (username,)).fetchone()
    if row is None:
        check_password_hash(generate_password_hash("x"), password)  # mismo tiempo de respuesta
        return False
    return check_password_hash(row["password_hash"], password)


def set_password(username, new_password):
    if len(new_password) < MIN_PASSWORD:
        raise ValueError(f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres")
    with connect() as db:
        cur = db.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE username = ?",
                         (generate_password_hash(new_password), now_iso(), username))
        if cur.rowcount != 1:
            raise ValueError("Usuario no encontrado")
    log("info", f"Contraseña cambiada para '{username}'")
