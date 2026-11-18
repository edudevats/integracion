"""Cliente de la API web de Leadsales (la misma que usa crm.leadsales.services).

Leadsales no tiene API pública documentada; esto replica las llamadas del CRM web:
  - Login con Firebase Auth (proyecto leadsales-app) -> idToken (Bearer).
  - GET  /workspaces
  - GET  /workspace/{ws}/leads/filter          (listado de leads, paginado por cursor)
  - GET  /workspace/{ws}/leads/{lead}/close-events   (ventas/pérdidas registradas)
"""
import threading
import time

import requests

import config

IDENTITY_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
REFRESH_URL = "https://securetoken.googleapis.com/v1/token"
BROWSER_HEADERS = {
    "Origin": "https://crm.leadsales.services",
    "Referer": "https://crm.leadsales.services/",
    "X-Leadsales-Client": "web",
    "Accept": "application/json",
}


class LeadsalesError(Exception):
    pass


class LeadsalesClient:
    def __init__(self, email=None, password=None, workspace_id=None):
        self.email = email or config.LEADSALES_EMAIL
        self.password = password or config.LEADSALES_PASSWORD
        self.workspace_id = workspace_id or config.LEADSALES_WORKSPACE_ID
        self.http = requests.Session()
        self.http.headers.update(BROWSER_HEADERS)
        self._id_token = None
        self._refresh_token = None
        self._expires_at = 0
        self._lock = threading.Lock()

    # ---------- autenticación ----------
    def _firebase(self, url, payload, form=False):
        params = {"key": config.LEADSALES_FIREBASE_API_KEY}
        if form:
            r = self.http.post(url, params=params, data=payload, timeout=30)
        else:
            r = self.http.post(url, params=params, json=payload, timeout=30)
        if r.status_code != 200:
            try:
                msg = r.json().get("error", {}).get("message", r.text[:200])
            except ValueError:
                msg = r.text[:200]
            raise LeadsalesError(f"Login de Leadsales falló ({r.status_code}): {msg}")
        return r.json()

    def login(self):
        if not self.email or not self.password:
            raise LeadsalesError("Faltan LEADSALES_EMAIL / LEADSALES_PASSWORD en .env")
        data = self._firebase(
            IDENTITY_URL,
            {"email": self.email, "password": self.password, "returnSecureToken": True},
        )
        self._set_tokens(data["idToken"], data["refreshToken"], data.get("expiresIn", 3600))

    def _refresh(self):
        data = self._firebase(
            REFRESH_URL,
            {"grant_type": "refresh_token", "refresh_token": self._refresh_token},
            form=True,
        )
        self._set_tokens(data["id_token"], data["refresh_token"], data.get("expires_in", 3600))

    def _set_tokens(self, id_token, refresh_token, expires_in):
        self._id_token = id_token
        self._refresh_token = refresh_token
        self._expires_at = time.time() + int(expires_in) - 120

    def token(self):
        with self._lock:
            if self._id_token and time.time() < self._expires_at:
                return self._id_token
            if self._refresh_token:
                try:
                    self._refresh()
                    return self._id_token
                except LeadsalesError:
                    pass
            self.login()
            return self._id_token

    # ---------- HTTP ----------
    def get(self, path, params=None, _retry=True):
        url = config.LEADSALES_API_BASE + path
        r = self.http.get(
            url, params=params, headers={"Authorization": f"Bearer {self.token()}"}, timeout=60
        )
        if r.status_code == 401 and _retry:
            with self._lock:
                self._id_token = None
                self._expires_at = 0
            return self.get(path, params, _retry=False)
        if r.status_code == 429 and _retry:
            time.sleep(5)
            return self.get(path, params, _retry=False)
        if r.status_code >= 400:
            raise LeadsalesError(f"GET {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    @staticmethod
    def unwrap(body):
        """Las respuestas suelen venir como {success, data}; devuelve data si existe."""
        if isinstance(body, dict) and "data" in body and body.get("data") is not None:
            return body["data"]
        return body

    # ---------- recursos ----------
    def workspaces(self):
        return self.unwrap(self.get("/workspaces"))

    def resolve_workspace(self):
        if self.workspace_id:
            return self.workspace_id
        ws = self.workspaces()
        items = ws if isinstance(ws, list) else (ws.get("workspaces") or ws.get("items") or [])
        if not items:
            raise LeadsalesError("La cuenta de Leadsales no tiene workspaces")
        first = items[0]
        self.workspace_id = first.get("id") or first.get("uuid") or first.get("workspace_id")
        return self.workspace_id

    def iter_leads(self, last_action_from_iso, last_action_to_iso=None, active=None, page_size=50,
                   max_pages=200):
        """Leads con actividad desde la fecha dada (filtro lastActionAt del CRM)."""
        ws = self.resolve_workspace()
        params = {
            "paginationMode": "cursor",
            "pageSize": page_size,
            "lastActionAtFrom": last_action_from_iso,
        }
        if last_action_to_iso:
            params["lastActionAtTo"] = last_action_to_iso
        if active is not None:
            params["active"] = "true" if active else "false"
        cursor = None
        for _ in range(max_pages):
            if cursor:
                params["cursor"] = cursor
            body = self.get(f"/workspace/{ws}/leads/filter", params)
            data = self.unwrap(body)
            leads = []
            if isinstance(data, dict):
                leads = data.get("leads") or data.get("items") or data.get("rows") or []
            elif isinstance(data, list):
                leads = data
            for lead in leads:
                yield lead
            cursor = None
            for src in (data, body):
                if isinstance(src, dict):
                    cursor = src.get("nextCursor") or src.get("next_cursor") or cursor
                    pag = src.get("pagination") or {}
                    cursor = cursor or pag.get("nextCursor") or pag.get("next_cursor")
            if not cursor or not leads:
                break

    def close_events(self, lead_id):
        ws = self.resolve_workspace()
        return self.unwrap(self.get(f"/workspace/{ws}/leads/{lead_id}/close-events"))


def lead_id_of(lead):
    return lead.get("id") or lead.get("uuid") or lead.get("leadId") or lead.get("lead_id")
