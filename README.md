# Ventas Leadsales → Meta (Conversions API)

Cuando un vendedor marca un lead como **Vendido** en Leadsales (con monto), esta app
manda a Meta un evento **Purchase** con el teléfono y el nombre del comprador cifrados (SHA-256).
Así Meta sabe quién compró y puede optimizar los anuncios hacia personas parecidas.

## Cómo funciona
- **Base de datos SQLite** en `data/app.db` (se crea sola): usuario del panel, conexión de Facebook,
  ventas/clientes enviados y bitácora. Respáldala de vez en cuando.
- **Panel web** con usuario y contraseña propios. La primera vez el usuario se crea con
  `APP_USERNAME` / `APP_PASSWORD` del `.env`; después la contraseña se cambia en **Cambiar contraseña**
  (se guarda cifrada en la base y el valor del `.env` ya no se usa).
- **Clientes enviados**: lista de todos los clientes cuya venta se mandó a Facebook (nombre, teléfono,
  monto, qué compró, estado), con buscador, filtro por estado y descarga en CSV para Excel.
- Botón **Conectar con Facebook**. El dueño de la página inicia sesión en la pantalla de Facebook
  (la app nunca ve su contraseña) y autoriza. La app guarda el permiso y muestra "Conectado".
  La conexión dura **60 días**; después aparece "Desconectado" y hay que pulsar
  "Volver a conectar". El panel avisa 7 días antes.
- **Una vez al día, a las 12:00 am (CDMX)**, la tarea `run_daily.py`:
  1. entra a Leadsales con las credenciales del `.env`;
  2. busca los leads con actividad desde la revisión anterior (`/leads/filter?lastActionAtFrom=…`);
  3. revisa sus cierres (`/leads/{id}/close-events`) y toma los de tipo `sold`;
  4. manda cada venta nueva a Meta con `event_id = leadsales-sale-{uuid}`, así nunca se cuenta dos veces.
- Si Facebook está desconectado, la revisión no envía nada. Al reconectar, la siguiente revisión
  manda las ventas pendientes (Meta solo acepta ventas de los últimos 7 días).

> Leadsales no tiene API pública ni webhooks: se usan los mismos endpoints que su CRM web.
> Si Leadsales los cambia, habrá que ajustar `leadsales.py`.

## App de Meta (una sola vez)
1. https://developers.facebook.com/apps → Crear app → tipo **Business**.
2. Agrega el producto **Facebook Login** y en "URIs de redirección de OAuth válidos" pon
   `https://TUUSUARIO.pythonanywhere.com/facebook/callback`.
3. Copia App ID y App Secret al `.env` (`FB_APP_ID`, `FB_APP_SECRET`).
4. Permisos que pide la app: `ads_management`, `ads_read`, `business_management`.
   Mientras la app esté en modo desarrollo, el dueño debe tener un rol en la app (administrador o tester).

## Subir a PythonAnywhere
1. Sube la carpeta (sin `.venv` ni `data`) a `/home/TUUSUARIO/integracion`.
2. En una consola Bash:
   ```bash
   mkvirtualenv --python=python3.12 ventas-meta
   pip install -r ~/integracion/requirements.txt
   ```
3. Crea `~/integracion/.env` a partir de `.env.example` y llénalo
   (`FB_REDIRECT_URI=https://TUUSUARIO.pythonanywhere.com/facebook/callback`).
4. Pestaña **Web** → *Add a new web app* → *Manual configuration* (Python 3.12):
   - *Virtualenv*: `/home/TUUSUARIO/.virtualenvs/ventas-meta`
   - *WSGI configuration file*: pega el contenido de `pythonanywhere_wsgi.py` (cambia `TUUSUARIO`).
   - Activa **Force HTTPS** y pulsa **Reload**.
5. Pestaña **Tasks** → tarea **diaria** a las **06:00 UTC** (= 12:00 am CDMX):
   ```
   /home/TUUSUARIO/.virtualenvs/ventas-meta/bin/python /home/TUUSUARIO/integracion/run_daily.py
   ```
6. Prueba el login de Leadsales desde una consola:
   ```bash
   cd ~/integracion && ~/.virtualenvs/ventas-meta/bin/python discover.py
   ```

## Primer uso
1. Entra al panel con `APP_USERNAME` / `APP_PASSWORD` y cámbiala en **Cambiar contraseña**.
   Si se olvida, restablécela desde una consola de PythonAnywhere:
   `cd ~/integracion && ~/.virtualenvs/ventas-meta/bin/python reset_password.py`
2. **Conectar con Facebook** (lo hace el dueño) → elige el pixel/dataset.
3. Pon un código de **Probar eventos** (Administrador de eventos) y pulsa *Sincronizar ahora*.
   Cuando veas los eventos llegar a Meta, borra el código.

## Para pruebas en tu computadora
```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python app.py          # http://localhost:5000
.\.venv\Scripts\python run_daily.py    # ejecuta la revisión diaria a mano
```
(Usa `FB_REDIRECT_URI=http://localhost:5000/facebook/callback` y regístrala también en la App de Meta.)

## Importante para el equipo de ventas
Solo se envían las ventas registradas con **Marcar resultado → Vendido** (con monto) en el chat de Leadsales.
Mover el lead de etapa no basta.
