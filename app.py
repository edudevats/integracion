"""Panel: login, conexión con Facebook, clientes enviados y envío de ventas de Leadsales a Meta."""
import hmac
import secrets
import time
from datetime import datetime
from functools import wraps

from flask import (Flask, Response, abort, flash, redirect, render_template, request, session,
                   url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

import config
import meta
import store
import sync

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # PythonAnywhere va detrás de un proxy
app.secret_key = config.APP_SECRET_KEY or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=config.SECURE_COOKIES,
                  PERMANENT_SESSION_LIFETIME=12 * 3600)

store.ensure_admin()


# ---------- seguridad ----------
def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        return fn(*a, **kw)
    return wrapper


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf", "")
        if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Token CSRF inválido; recarga la página")


app.jinja_env.globals["csrf_token"] = csrf_token


@app.template_filter("dt")
def fmt_dt(value):
    if not value:
        return "—"
    return str(value).replace("T", " ")[:16] + " UTC"


# ---------- login de la app ----------
_failed = {}


@app.route("/login", methods=["GET", "POST"])
def login():
    if not store.has_users():
        store.ensure_admin()
    no_users = not store.has_users()
    if request.method == "POST" and not no_users:
        ip = request.remote_addr or "?"
        tries = [t for t in _failed.get(ip, []) if time.time() - t < 900]
        if len(tries) >= 8:
            flash("Demasiados intentos. Espera 15 minutos.", "error")
            return render_template("login.html"), 429
        user = request.form.get("username", "").strip()
        if store.check_login(user, request.form.get("password", "")):
            session.clear()
            session.permanent = True
            session["user"] = user
            _failed.pop(ip, None)
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
        _failed[ip] = tries + [time.time()]
        flash("Usuario o contraseña incorrectos", "error")
    return render_template("login.html", missing=no_users)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current", "")
        new, confirm = request.form.get("new", ""), request.form.get("confirm", "")
        if not store.check_login(session["user"], current):
            flash("La contraseña actual no es correcta", "error")
        elif new != confirm:
            flash("La nueva contraseña y su confirmación no coinciden", "error")
        elif new == current:
            flash("La nueva contraseña debe ser distinta de la actual", "error")
        else:
            try:
                store.set_password(session["user"], new)
                flash("Contraseña actualizada", "ok")
                return redirect("/")
            except ValueError as e:
                flash(str(e), "error")
    return render_template("password.html", min_len=store.MIN_PASSWORD)


# ---------- panel ----------
@app.get("/")
@login_required
def dashboard():
    fb = store.get("facebook")
    return render_template(
        "dashboard.html", fb=fb, fbs=store.fb_status(fb), settings=store.get("settings"),
        last=store.get("last_sync_result"), sales=store.list_sales(limit=10),
        totals=store.sales_totals(), logs=store.recent_logs(30),
        datasets=session.get("datasets"), missing=config.missing_settings(),
        daily_label=config.DAILY_RUN_LABEL, reconnect_days=config.FB_RECONNECT_DAYS,
        waba=bool(config.WHATSAPP_BUSINESS_ACCOUNT_ID))


@app.get("/clientes")
@login_required
def clients():
    q = request.args.get("q", "").strip()
    status = request.args.get("estado", "")
    if status not in ("", "enviado", "omitido", "error"):
        status = ""
    rows = store.list_sales(q, status, limit=2000)
    if request.args.get("formato") == "csv":
        name = f"clientes-enviados-{datetime.now():%Y%m%d}.csv"
        return Response(store.sales_csv(rows), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
    return render_template("clients.html", rows=rows, q=q, status=status,
                           totals=store.sales_totals())


# ---------- Facebook ----------
@app.post("/facebook/connect")
@login_required
def fb_connect():
    if not config.FB_APP_ID or not config.FB_APP_SECRET:
        flash("Faltan FB_APP_ID / FB_APP_SECRET en .env", "error")
        return redirect("/")
    session["fb_state"] = secrets.token_urlsafe(24)
    return redirect(meta.login_url(session["fb_state"]))


@app.get("/facebook/callback")
@login_required
def fb_callback():
    if request.args.get("error"):
        flash(f"Facebook: {request.args.get('error_description') or request.args['error']}", "error")
        return redirect("/")
    state = request.args.get("state", "")
    if not state or not hmac.compare_digest(state, session.pop("fb_state", "")):
        abort(400, "Estado de OAuth inválido")
    try:
        info = meta.exchange_code(request.args.get("code", ""))
        datasets = meta.list_datasets(info["token"])
    except meta.MetaError as e:
        flash(str(e), "error")
        return redirect("/")
    prev = store.get("facebook")
    store.put("facebook", {**info, "dataset_id": prev.get("dataset_id"),
                           "dataset_name": prev.get("dataset_name")})
    session["datasets"] = datasets
    store.log("info", f"Facebook conectado como {info['user_name']}")
    flash(f"Conectado como {info['user_name']}. Revisa que el pixel/dataset sea el correcto.", "ok")
    return redirect("/")


@app.post("/facebook/datasets/refresh")
@login_required
def fb_refresh_datasets():
    fb = store.get("facebook")
    if store.fb_status(fb)["state"] != "connected":
        flash("Primero conecta Facebook", "error")
    else:
        session["datasets"] = meta.list_datasets(fb["token"])
        if not session["datasets"]:
            flash("No encontré píxeles con este usuario. Puedes escribir el ID a mano.", "error")
    return redirect("/")


@app.post("/facebook/dataset")
@login_required
def fb_dataset():
    ds_id = request.form.get("dataset_id", "").strip()
    if not ds_id.isdigit():
        flash("El ID del pixel/dataset debe ser numérico", "error")
        return redirect("/")
    names = {d["id"]: d["name"] for d in session.get("datasets") or []}
    store.patch("facebook", dataset_id=ds_id, dataset_name=names.get(ds_id, ds_id))
    store.log("info", f"Pixel/dataset seleccionado: {names.get(ds_id, ds_id)}")
    flash("Pixel/dataset guardado", "ok")
    return redirect("/")


@app.post("/facebook/disconnect")
@login_required
def fb_disconnect():
    store.put("facebook", {})
    session.pop("datasets", None)
    store.log("info", "Facebook desconectado")
    return redirect("/")


# ---------- ajustes y sincronización ----------
@app.post("/settings")
@login_required
def save_settings():
    code = request.form.get("test_event_code", "").strip()
    enabled = request.form.get("enabled") == "on"
    store.patch("settings", test_event_code=code, enabled=enabled)
    store.log("info", f"Ajustes guardados (envío diario {'activo' if enabled else 'pausado'}"
                      f"{', con código de prueba' if code else ''})")
    flash("Ajustes guardados", "ok")
    return redirect("/")


@app.post("/sync")
@login_required
def sync_now():
    days = request.form.get("days", "").strip()
    result = sync.run_sync(full_days=int(days) if days.isdigit() and 0 < int(days) <= 7 else None)
    flash(result["message"], "ok" if result["ok"] else "error")
    return redirect("/")


@app.post("/manual")
@login_required
def manual():
    try:
        amount = float(request.form.get("amount", "0").replace(",", ""))
        rec = sync.send_manual(request.form.get("phone", ""), amount,
                               request.form.get("currency") or config.DEFAULT_CURRENCY,
                               request.form.get("name", ""), request.form.get("what_sold", ""))
        flash(f"Venta manual: {rec['status']} — {rec.get('detail', '')}",
              "ok" if rec["status"] == "enviado" else "error")
    except (ValueError, meta.MetaError) as e:
        flash(str(e), "error")
    return redirect("/")


if __name__ == "__main__":
    missing = config.missing_settings()
    if missing:
        print("Faltan en .env:", ", ".join(missing))
    # Solo para pruebas locales. En PythonAnywhere la página la sirve su app web (WSGI)
    # y la revisión diaria es la tarea programada run_daily.py.
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
