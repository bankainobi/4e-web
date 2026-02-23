# GHOST-SHELL — Deploy en Render.com

## Archivos necesarios

```
flask_app__5_.py      ← app principal
requirements.txt      ← dependencias
render.yaml           ← config de Render
static/               ← carpeta estática (logo, etc.)
```

---

## Pasos para subir

### 1. Crear repositorio en GitHub

```bash
git init
git add flask_app__5_.py requirements.txt render.yaml
git commit -m "Initial deploy"
git remote add origin https://github.com/TU_USUARIO/ghostshell.git
git push -u origin main
```

### 2. Conectar en Render

1. Ve a **render.com** → "New Web Service"
2. Conecta tu repositorio de GitHub
3. Render detectará el `render.yaml` automáticamente
4. Pulsa **Deploy**

### 3. Variables de entorno (ya configuradas en render.yaml)

- `SECRET_KEY` → se genera automáticamente (seguro)

---

## ⚠️ IMPORTANTE — Datos persistentes

Render en el **plan gratuito** usa un sistema de ficheros efímero.
Esto significa que `pages.json`, `users.json`, `chat.json` y las imágenes
subidas **se borran cada vez que el servicio se reinicia** (cada ~15 min de inactividad).

### Solución recomendada: usar un disco persistente

En Render puedes añadir un **Disk** (plan de pago, ~$0.25/GB/mes):

1. En tu servicio → "Disks" → "Add Disk"
2. Mount path: `/data`
3. En `flask_app__5_.py`, cambia las rutas de datos:

```python
DATA_DIR      = os.environ.get('DATA_DIR', '.')  # En Render: '/data'
PAGES_FILE    = os.path.join(DATA_DIR, 'pages.json')
EVENTS_FILE   = os.path.join(DATA_DIR, 'events.json')
AGENDA_FILE   = os.path.join(DATA_DIR, 'agenda.json')
CHAT_FILE     = os.path.join(DATA_DIR, 'chat.json')
CHAT_META     = os.path.join(DATA_DIR, 'chat_meta.json')
USERS_FILE    = os.path.join(DATA_DIR, 'users.json')
UPLOAD_FOLDER = os.path.join(DATA_DIR, 'chat_uploads')
```

Y añade en render.yaml:
```yaml
    envVars:
      - key: DATA_DIR
        value: /data
```

### Alternativa gratuita: Railway.app

Railway ofrece **volúmenes persistentes gratis** y es igual de fácil:
1. railway.app → "New Project" → "Deploy from GitHub"
2. Añade un volumen en `/data`
3. Mismos cambios de rutas que arriba

---

## SSE (chat en tiempo real)

El chat usa Server-Sent Events. Render los soporta bien con `gthread`.
El `render.yaml` ya está configurado con `--worker-class gthread` para esto.

Si usas un proxy inverso (Nginx/Cloudflare), asegúrate de:
- Desactivar buffering: `X-Accel-Buffering: no`
- Cloudflare: desactivar "Rocket Loader" para la ruta `/api/chat/stream`

---

## Comandos útiles en local

```bash
# Instalar dependencias
pip install -r requirements.txt

# Ejecutar en desarrollo
python flask_app__5_.py

# Ejecutar con gunicorn (como en producción)
gunicorn flask_app__5_:app --workers 2 --threads 4 --worker-class gthread --bind 0.0.0.0:5000
```
