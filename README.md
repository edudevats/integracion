# Ventas Leadsales → Meta (Conversions API)

Cuando un vendedor marca un lead como **Vendido** en Leadsales (con monto), esta app
manda a Meta un evento **Purchase** con el teléfono y el nombre del comprador cifrados (SHA-256).
Así Meta sabe quién compró y puede optimizar los anuncios hacia personas parecidas.

## Cómo funciona
- **Base de datos SQLite** en `data/app.db` (se crea sola): usuario del panel, conexión con Meta
  (token **cifrado**), ventas/clientes enviados y bitácora. Respáldala de vez en cuando.
- **Panel web** con usuario y contraseña propios. La primera vez el usuario se crea con
  `APP_USERNAME` / `APP_PASSWORD` del `.env`; después la contraseña se cambia en **Cambiar contraseña**
  (se guarda con hash en la base y el valor del `.env` ya no se usa).
- **Conexión con Meta sin dar contraseñas:** el dueño de la página genera un **token de la API de
  conversiones** en su Administrador de eventos y lo pega en el panel junto con el ID del pixel
  (el panel trae la guía paso a paso). Ese token solo sirve para enviar eventos a ese pixel,
  no da acceso a su Facebook, anuncios ni pagos, y no vence.
  - Se guarda cifrado (Fernet/AES) con una llave derivada de `APP_SECRET_KEY`; nunca se vuelve a mostrar.
  - Si Meta lo rechaza alguna vez, el panel muestra "Meta rechazó el token" y basta con pegar uno nuevo.
- **Clientes enviados**: lista de todos los clientes cuya venta se mandó a Facebook (nombre, teléfono,
  monto, qué compró, estado), con buscador, filtro por estado y descarga en CSV para Excel.
- **Una vez al día, a las 12:00 am (CDMX)**, la tarea `run_daily.py`:
  1. entra a Leadsales con las credenciales del `.env`;
  2. busca los leads con actividad desde la revisión anterior (`/leads/filter?lastActionAtFrom=…`);
  3. revisa sus cierres (`/leads/{id}/close-events`) y toma los de tipo `sold`;
  4. manda cada venta nueva a Meta con `event_id = leadsales-sale-{uuid}`, así nunca se cuenta dos veces.
- Si no hay token válido, la revisión no envía nada; al corregirlo, la siguiente revisión manda las
  ventas pendientes (Meta solo acepta ventas de los últimos 7 días).

> Leadsales no tiene API pública ni webhooks: se usan los mismos endpoints que su CRM web.
> Si Leadsales los cambia, habrá que ajustar `leadsales.py`.

## Datos de Meta que pega el dueño (resumen de la guía del panel)
1. Entra a **business.facebook.com/events_manager2** (Administrador de eventos) con el Facebook
   que administra los anuncios.
2. Elige el **pixel** que usan los anuncios en *Orígenes de datos*.
3. **ID del pixel**: el número debajo del nombre (o *Configuración* → "ID del conjunto de datos").
4. **Token**: *Configuración* → sección **API de conversiones** → **Generar token de acceso**.
   Se muestra una sola vez; cópialo completo.
5. *(Opcional)* **ID de WhatsApp Business**: *Configuración del negocio* → *Cuentas* →
   *Cuentas de WhatsApp* → Identificador. Mejora la atribución de anuncios de clic a WhatsApp.
6. Pégalos en el panel → **Conectar**. Para probar: pestaña **Probar eventos** → copia el código →
   pégalo en "Código de prueba" → **Sincronizar ahora**. Luego borra el código.

## Subir a PythonAnywhere
1. Sube la carpeta (sin `.venv` ni `data`) a `/home/TUUSUARIO/integracion`.
2. En una consola Bash:
   ```bash
   mkvirtualenv --python=python3.12 ventas-meta
   pip install -r ~/integracion/requirements.txt
   ```
3. Crea `~/integracion/.env` a partir de `.env.example` y llénalo (solo panel y Leadsales).
4. Pestaña **Web** → *Add a new web app* → *Manual configuration* (Python 3.12):
   - *Virtualenv*: `/home/TUUSUARIO/.virtualenvs/ventas-meta`
   - *WSGI configuration file*: pega el contenido de `wsgi.py` (si el proyecto no está en `/home/RedMedicaPro/integracion`, cambia `project_home`).
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
2. El dueño pega el token y el ID del pixel en **Conexión con Meta** (guía en el panel).
3. Pon un código de **Probar eventos** y pulsa *Sincronizar ahora*. Cuando veas el evento en Meta, borra el código.

## Para pruebas en tu computadora
```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python app.py          # http://localhost:5000
.\.venv\Scripts\python run_daily.py    # ejecuta la revisión diaria a mano
```

## Importante para el equipo de ventas
Solo se envían las ventas registradas con **Marcar resultado → Vendido** (con monto) en el chat de Leadsales.
Mover el lead de etapa no basta.
