import os, json, calendar, datetime, uuid, re, queue, threading, time
from flask import Flask, render_template_string, request, redirect, url_for, session, Response, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from functools import wraps
from werkzeug.security import check_password_hash, generate_password_hash

# --- CONFIGURACIÓN ---
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'cyberpunk_secreto_7034oguh_clave')
app.config['SESSION_COOKIE_SAMESITE']     = 'Lax'
app.config['SESSION_COOKIE_SECURE']       = False  # True en produccion con HTTPS
app.config['PERMANENT_SESSION_LIFETIME']  = datetime.timedelta(days=5)
app.config['SEND_FILE_MAX_AGE_DEFAULT']   = 86400   # cache estaticos 1 dia
app.config['TEMPLATES_AUTO_RELOAD']       = False   # no recargar plantillas en prod
app.jinja_env.auto_reload                 = False
app.jinja_env.cache_size                  = 400     # cachear hasta 400 plantillas compiladas

# Cache de render_template_string: evita recompilar Jinja2 en cada request
from jinja2 import Environment
_tpl_cache: dict = {}

def render_cached(template_str: str, **kwargs):
    """Como render_template_string pero cachea el objeto Template compilado."""
    if template_str not in _tpl_cache:
        _tpl_cache[template_str] = app.jinja_env.from_string(template_str)
    return _tpl_cache[template_str].render(**kwargs)

# --- ADMIN HARDCODED ---
ADMIN_USER      = 'ogmhabas'
ADMIN_PASS_HASH = 'scrypt:32768:8:1$Y2Jetdw7JfJ9Q4ql$e2306faecea53adcfecb39bcf990fabba330526e35665bea8072e8efada137e1b55731bf0a8c4766cdaed8f2d72e40832797980845f0758304ad0b5c49ea75c0'

# --- USUARIOS FILE (reemplaza PRIVATE_USERS hardcoded) ---
USERS_FILE = 'users.json'

def load_users():
    """Carga users.json → {username: {hash, created_at, banned}}"""
    if not os.path.exists(USERS_FILE): return {}
    try:
        with open(USERS_FILE,'r',encoding='utf-8') as f: return json.load(f)
    except: return {}

def save_users(users):
    with open(USERS_FILE,'w',encoding='utf-8') as f: json.dump(users, f, indent=2, ensure_ascii=False)

def get_user(username):
    return load_users().get(username)

def register_user(username, password):
    """Registra si no existe. Devuelve (ok, msg)."""
    if not username or not password: return False, 'Rellena todos los campos'
    if len(username) < 2: return False, 'Usuario demasiado corto'
    if len(password) < 6: return False, 'Mínimo 6 caracteres'
    if not re.match(r'^[\w\sáéíóúÁÉÍÓÚñÑ.\-]{2,30}$', username):
        return False, 'Usuario: solo letras, números, espacios y puntos'
    users = load_users()
    # Case-insensitive duplicate check
    if any(u.lower() == username.lower() for u in users):
        return False, 'Ese nombre ya está en uso'
    users[username] = {
        'hash': generate_password_hash(password),
        'created_at': datetime.datetime.utcnow().isoformat(),
        'banned': False,
        'message': None,  # mensaje admin en tiempo real
    }
    save_users(users)
    return True, 'ok'

# SSE: cola de mensajes por usuario  {username: Queue}
_user_sse_queues: dict = {}

def get_sse_queue(username):
    if username not in _user_sse_queues:
        _user_sse_queues[username] = queue.Queue(maxsize=20)
    return _user_sse_queues[username]

def push_message(username, text):
    """Envía mensaje SSE a un usuario online."""
    q = get_sse_queue(username)
    try: q.put_nowait({'type':'msg','text': text})
    except queue.Full: pass
    # Persiste también en users.json
    users = load_users()
    if username in users: users[username]['message'] = text; save_users(users)

def kick_user(username):
    """Fuerza re-login baneando temporalmente."""
    q = get_sse_queue(username)
    try: q.put_nowait({'type':'kick'})
    except queue.Full: pass

PAGES_FILE = 'pages.json'
EVENTS_FILE = 'events.json'
AGENDA_FILE = 'agenda.json'

# --- CONFIGURACIÓN VISUAL ---
THEME_COLORS = {
    'Drácula Cian': 'bg-cyan-600 border-cyan-500 text-white',
    'Drácula Rosa': 'bg-pink-600 border-pink-500 text-white',
    'Drácula Verde': 'bg-emerald-600 border-emerald-500 text-white',
    'Drácula Amarillo': 'bg-yellow-500 border-yellow-400 text-black',
    'Drácula Púrpura': 'bg-purple-600 border-purple-500 text-white',
}

SUBJECT_ICONS = {
    'Lengua': ['fa-solid fa-book-open', 'fa-solid fa-feather-pointed', 'fa-solid fa-comments'],
    'Mates': ['fa-solid fa-calculator', 'fa-solid fa-square-root-variable', 'fa-solid fa-chart-line'],
    'Historia': ['fa-solid fa-landmark', 'fa-solid fa-scroll', 'fa-solid fa-clock-rotate-left'],
    'Inglés': ['fa-solid fa-globe', 'fa-solid fa-headset', 'fa-solid fa-spell-check'],
    'Biología': ['fa-solid fa-dna', 'fa-solid fa-seedling', 'fa-solid fa-microscope'],
    'Física y Química': ['fa-solid fa-atom', 'fa-solid fa-flask', 'fa-solid fa-temperature-three-quarters'],
}

# --- PERSISTENCIA ---
def load_json(filename):
    if not os.path.exists(filename): return []
    try:
        with open(filename, 'r', encoding='utf-8') as f: return json.load(f)
    except json.JSONDecodeError: return []

def save_json(filename, data):
    with open(filename, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)

def load_pages(): return load_json(PAGES_FILE)
def save_pages(pages): save_json(PAGES_FILE, pages)
def load_events(): return load_json(EVENTS_FILE)
def save_events(events): save_json(EVENTS_FILE, events)
def load_agenda(): return load_json(AGENDA_FILE)
def save_agenda(notes): save_json(AGENDA_FILE, notes)

# --- AFTER REQUEST: cabeceras de rendimiento ---
@app.after_request
def add_perf_headers(response):
    if response.content_type and 'text/html' in response.content_type:
        response.headers['Cache-Control'] = 'no-store'
    return response

# --- DECORADORES ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('logged_in') != True:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def private_required(f):
    """Zona privada: necesita sesión de alumno vigente (≤5 días)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = session.get('private_user')
        if not user:
            return redirect(url_for('auth_wall'))
        u = get_user(user)
        if not u or u.get('banned'):
            session.clear(); return redirect(url_for('auth_wall', reason='banned'))
        login_ts = session.get('login_ts', 0)
        if (datetime.datetime.utcnow().timestamp() - login_ts) > 5 * 86400:
            session.clear(); return redirect(url_for('auth_wall', reason='expired'))
        return f(*args, **kwargs)
    return decorated_function

# --- PLANTILLA BASE (BLINDADA CONTRA DARK MODE FORZADO) ---
BASE_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es" class="scroll-smooth">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <meta name="color-scheme" content="dark">
    <meta name="darkreader-lock">

    <title>{{ title }} | GHOST-SHELL</title>

    <link rel="icon" type="image/png" href="{{ url_for('static', filename='logo.svg') }}">

    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css" />
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;800&display=swap" rel="stylesheet">

    <!-- Aplicar posición de polybar ANTES del primer paint para evitar salto visual -->
    <script>
        window.GS_REASON = {{ gs_reason|default('')|tojson }};
        (function() {
            try {
                var s = JSON.parse(localStorage.getItem('ghostshell_settings') || '{}');
                var pos = s.polybarPos || 'top';
                document.documentElement.setAttribute('data-polybar-pos', pos);
                // Brightness applied after DOM ready via #brightness-wrapper
                var style = document.createElement('style');
                style.id = 'no-transition-init';
                style.textContent = '#polybar-aside { transition: none !important; }';
                document.head.appendChild(style);
            } catch(e) {}
        })();
    </script>
    <style>
        :root {
            --bg-deep: #12131a;
            --bg-card: #1c1e2a;
            --glass-border: rgba(255, 255, 255, 0.14);
            color-scheme: dark; /* Fuerza al navegador a entender que es oscuro */
        }

        /* Evita ajustes de color forzados por el sistema */
        html, body {
            font-family: 'Inter', sans-serif;
            background-color: var(--bg-deep) !important;
            background-image: radial-gradient(circle at 50% 0%, #252840 0%, var(--bg-deep) 70%) !important;
            color: #F0F0F0 !important;
            forced-color-adjust: none;
        }

        /* --- ANIMACIONES --- */
        @keyframes fadeInUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .animate-enter { animation: fadeInUp 0.4s ease-out forwards; opacity: 0; will-change: transform, opacity; }
        .delay-100 { animation-delay: 0.1s; }
        .delay-200 { animation-delay: 0.2s; }

        /* --- GLASS & UI --- */
        .glass-panel { background: rgba(26, 28, 40, 0.85); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); border: 1px solid var(--glass-border); }
        .fast-glass { background: rgba(28, 30, 44, 0.82); border: 1px solid rgba(255, 255, 255, 0.13); }
        .input-liquid { background: rgba(255, 255, 255, 0.06); border: 1px solid var(--glass-border); color: white; transition: border-color 0.2s, background-color 0.2s; }
        .input-liquid:focus { outline: none; border-color: rgba(189, 147, 249, 0.65); background: rgba(255, 255, 255, 0.1); }
        .card-dynamic { transition: transform 0.2s cubic-bezier(0.2, 0, 0, 1), box-shadow 0.2s; transform: translateZ(0); }
        .card-dynamic:hover { transform: translateY(-6px); box-shadow: 0 20px 40px -5px rgba(0,0,0, 0.6); }
        .btn-glow { background: linear-gradient(135deg, #a855f7 0%, #7c3aed 100%); position: relative; overflow: hidden; transition: transform 0.2s; }
        .btn-glow:active { transform: scale(0.98); }

        /* --- SCROLLBAR --- */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 10px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.2); }

        .custom-scrollbar::-webkit-scrollbar { width: 4px; }

        /* --- POLYBAR TOOLTIPS --- */
        .poly-tooltip {
            position: absolute;
            background: rgba(0,0,0,0.85); border: 1px solid rgba(255,255,255,0.12); padding: 4px 10px;
            border-radius: 6px; font-size: 0.7rem; opacity: 0; pointer-events: none;
            transition: opacity 0.2s ease, transform 0.2s ease;
            white-space: nowrap; z-index: 200;
            /* default position (top bar): below the icon */
            top: 100%; left: 50%; right: auto; bottom: auto;
            transform: translateX(-50%) translateY(8px);
        }
        /* Default hover (top/bottom use this, pos-specific rules override) */
        .group:hover .poly-tooltip { opacity: 1; }

        /* --- SHORTCUT KEY HINT --- */
        .key-hint {
            display: inline-block; padding: 0 4px; border-radius: 4px; background: rgba(255,255,255,0.1);
            border: 1px solid rgba(255,255,255,0.2); font-family: monospace; font-size: 0.6rem; color: #a855f7; margin-left: 4px;
        }

        /* --- CALENDAR SPECIFIC --- */
        .calendar-cell { min-height: 120px; transition: background-color 0.2s; }
        .calendar-cell:hover { background-color: rgba(255,255,255,0.03); }
        .event-pill { font-size: 0.65rem; padding: 2px 6px; border-radius: 4px; margin-bottom: 2px; cursor: pointer; transition: filter 0.2s; border-left-width: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .event-pill:hover { filter: brightness(1.2); }

        .type-examen { background: rgba(239, 68, 68, 0.22); border-color: #ef4444; color: #fecaca; }
        .type-tarea { background: rgba(234, 179, 8, 0.22); border-color: #eab308; color: #fef08a; }
        .type-nota { background: rgba(59, 130, 246, 0.22); border-color: #3b82f6; color: #bfdbfe; }

        /* Brightness wrapper — solo el contenido, no los modales ni la polybar */
        #brightness-wrapper { transition: filter 0.3s ease; }
        /* Brightness protection: canvas/img/video get counter-filter via JS */

        /* --- HIGH CONTRAST --- */
        body.high-contrast { --glass-border: rgba(255,255,255,0.35); }
        body.high-contrast .glass-panel,
        body.high-contrast .fast-glass {
            background: rgba(8,8,16,0.98) !important;
            border-color: rgba(255,255,255,0.3) !important;
        }
        body.high-contrast #polybar-aside {
            background: rgba(5,5,12,0.99) !important;
            border-color: rgba(255,255,255,0.35) !important;
        }
        body.high-contrast .text-gray-400 { color: #d1d5db !important; }
        body.high-contrast .text-gray-500 { color: #9ca3af !important; }
        body.high-contrast .text-gray-600 { color: #6b7280 !important; }
        body.high-contrast .event-pill    { border-left-width: 4px !important; font-weight: 700 !important; }
        body.high-contrast .type-examen   { background: rgba(239,68,68,0.45) !important; color: #fef2f2 !important; }
        body.high-contrast .type-tarea    { background: rgba(234,179,8,0.45) !important; color: #fefce8 !important; }
        body.high-contrast .type-nota     { background: rgba(59,130,246,0.45) !important; color: #eff6ff !important; }
        body.high-contrast input, body.high-contrast textarea {
            border-color: rgba(255,255,255,0.25) !important;
            background: rgba(255,255,255,0.08) !important;
        }

        /* toggle switch */
        .toggle-switch { position:relative; width:44px; height:24px; flex-shrink:0; cursor:pointer; }
        .toggle-switch input { opacity:0; width:0; height:0; position:absolute; }
        .toggle-track {
            position:absolute; inset:0; border-radius:9999px;
            background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.15);
            transition: background 0.25s, border-color 0.25s;
        }
        .toggle-track::after {
            content:''; position:absolute; left:3px; top:50%; transform:translateY(-50%);
            width:16px; height:16px; border-radius:50%;
            background:#6b7280; transition: left 0.25s, background 0.25s;
        }
        .toggle-switch input:checked + .toggle-track { background:rgba(168,85,247,0.4); border-color:rgba(168,85,247,0.7); }
        .toggle-switch input:checked + .toggle-track::after { left:23px; background:#a855f7; box-shadow:0 0 8px rgba(168,85,247,0.8); }

        /* shortcuts editor */
        .shortcut-input {
            background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1);
            border-radius: 6px; color: white; font-family: monospace; font-size: 0.75rem;
            padding: 4px 8px; width: 90px; text-align: center; outline: none;
            transition: border-color 0.2s, background 0.2s;
        }
        .shortcut-input:focus { border-color: rgba(168,85,247,0.6); background: rgba(168,85,247,0.08); }
        .shortcut-input.recording { border-color: rgba(239,68,68,0.8); background: rgba(239,68,68,0.1); animation: pulse-rec 1s infinite; }
        @keyframes pulse-rec { 0%,100%{box-shadow:0 0 0 0 rgba(239,68,68,0.4)} 50%{box-shadow:0 0 0 4px rgba(239,68,68,0)} }


        /* Posición inicial via data-attr (evita flash al cargar) — espejo de las clases pos-* */
        html[data-polybar-pos="top"]    #polybar-aside { top:1.5rem;bottom:auto;left:50%;right:auto;transform:translateX(-50%);flex-direction:row;align-items:center;width:auto;height:4rem;padding:.5rem 1.5rem; }
        html[data-polybar-pos="bottom"] #polybar-aside { bottom:1.5rem;top:auto;left:50%;right:auto;transform:translateX(-50%);flex-direction:row;align-items:center;width:auto;height:4rem;padding:.5rem 1.5rem; }
        html[data-polybar-pos="left"]   #polybar-aside { left:1.5rem;right:auto;top:50%;bottom:auto;transform:translateY(-50%);flex-direction:column;align-items:center;justify-content:center;width:3.5rem;height:auto;padding:1rem .375rem;gap:0;min-width:unset; }
        html[data-polybar-pos="right"]  #polybar-aside { right:1.5rem;left:auto;top:50%;bottom:auto;transform:translateY(-50%);flex-direction:column;align-items:center;justify-content:center;width:3.5rem;height:auto;padding:1rem .375rem;gap:0;min-width:unset; }

        /* Secciones internas según data-attr */
        html[data-polybar-pos="top"] #polybar-aside .poly-nav,
        html[data-polybar-pos="bottom"] #polybar-aside .poly-nav { flex-direction:row;gap:1rem;align-items:center; }
        html[data-polybar-pos="left"] #polybar-aside .poly-nav,
        html[data-polybar-pos="right"] #polybar-aside .poly-nav { flex-direction:column;gap:.25rem;align-items:center; }

        html[data-polybar-pos="top"] #polybar-aside .poly-sep,
        html[data-polybar-pos="bottom"] #polybar-aside .poly-sep { display:block; }
        html[data-polybar-pos="left"] #polybar-aside .poly-sep,
        html[data-polybar-pos="right"] #polybar-aside .poly-sep { display:none; }

        html[data-polybar-pos="top"] #polybar-aside .poly-ghost-section,
        html[data-polybar-pos="bottom"] #polybar-aside .poly-ghost-section { padding-right:1rem;border-right:1px solid rgba(255,255,255,.1);border-bottom:none;padding-bottom:0; }
        html[data-polybar-pos="left"] #polybar-aside .poly-ghost-section,
        html[data-polybar-pos="right"] #polybar-aside .poly-ghost-section { border-right:none;border-bottom:1px solid rgba(255,255,255,.12);padding-right:0;padding-bottom:.5rem;margin-bottom:.5rem; }

        html[data-polybar-pos="top"] #polybar-aside .poly-actions-sep,
        html[data-polybar-pos="bottom"] #polybar-aside .poly-actions-sep { flex-direction:row;align-items:center;gap:.75rem;border-left:1px solid rgba(255,255,255,.1);border-top:none;padding-left:1.5rem;padding-top:0; }
        html[data-polybar-pos="left"] #polybar-aside .poly-actions-sep,
        html[data-polybar-pos="right"] #polybar-aside .poly-actions-sep { flex-direction:column;align-items:center;gap:.25rem;border-left:none;border-top:1px solid rgba(255,255,255,.12);padding-left:0;padding-top:.5rem;margin-top:.5rem; }

        /* Brillo inicial */
        html[data-brightness] body::after {
            content:''; position:fixed; inset:0; pointer-events:none; z-index:9999;
            background: rgba(0,0,0, calc((100 - var(--bri,100)) / 100 * 0.65));
        }

        /* Padding del wrapper según posición */
        html[data-polybar-pos="top"]    #main-wrapper { padding-top:8rem; }
        html[data-polybar-pos="bottom"] #main-wrapper { padding-top:2rem;padding-bottom:6rem; }
        html[data-polybar-pos="left"]   #main-wrapper { padding-left:6rem;padding-top:2rem; }
        html[data-polybar-pos="right"]  #main-wrapper { padding-right:6rem;padding-top:2rem; }
        #brightness-layer {
            position: fixed; inset: 0; pointer-events: none; z-index: 9999;
            background: transparent;
            transition: background 0.3s ease;
        }

        /* --- POLYBAR POSITIONS --- */
        #polybar-aside {
            transition: top 0.4s cubic-bezier(0.4,0,0.2,1),
                        bottom 0.4s cubic-bezier(0.4,0,0.2,1),
                        left 0.4s cubic-bezier(0.4,0,0.2,1),
                        right 0.4s cubic-bezier(0.4,0,0.2,1),
                        transform 0.4s cubic-bezier(0.4,0,0.2,1),
                        width 0.4s cubic-bezier(0.4,0,0.2,1),
                        height 0.4s cubic-bezier(0.4,0,0.2,1);
        }

        /* ---- TOP (default) ---- */
        #polybar-aside.pos-top {
            top: 1.5rem; bottom: auto; left: 50%; right: auto;
            transform: translateX(-50%);
            flex-direction: row; align-items: center;
            width: auto; height: 4rem;
            padding: 0.5rem 1.5rem;
        }
        #polybar-aside.pos-top .poly-nav    { flex-direction: row; gap: 1rem; align-items: center; }
        #polybar-aside.pos-top .poly-sep    { display: block; width: 1px; height: 2rem; background: rgba(255,255,255,0.2); }
        #polybar-aside.pos-top .poly-ghost-section { padding-right: 1rem; border-right: 1px solid rgba(255,255,255,0.1); border-bottom: none; padding-bottom: 0; }
        #polybar-aside.pos-top .poly-actions-sep   { flex-direction: row; align-items: center; gap: 0.75rem; border-left: 1px solid rgba(255,255,255,0.1); border-top: none; padding-left: 1.5rem; padding-top: 0; }
        /* tooltips: below */
        #polybar-aside.pos-top .poly-tooltip { top: 100%; bottom: auto; left: 50%; right: auto; transform: translateX(-50%) translateY(8px); }
        #polybar-aside.pos-top .group:hover .poly-tooltip { transform: translateX(-50%) translateY(14px); opacity: 1; }

        /* ---- BOTTOM ---- */
        #polybar-aside.pos-bottom {
            bottom: 1.5rem; top: auto; left: 50%; right: auto;
            transform: translateX(-50%);
            flex-direction: row; align-items: center;
            width: auto; height: 4rem;
            padding: 0.5rem 1.5rem;
        }
        #polybar-aside.pos-bottom .poly-nav    { flex-direction: row; gap: 1rem; align-items: center; }
        #polybar-aside.pos-bottom .poly-sep    { display: block; width: 1px; height: 2rem; background: rgba(255,255,255,0.2); }
        #polybar-aside.pos-bottom .poly-ghost-section { padding-right: 1rem; border-right: 1px solid rgba(255,255,255,0.1); border-bottom: none; padding-bottom: 0; }
        #polybar-aside.pos-bottom .poly-actions-sep   { flex-direction: row; align-items: center; gap: 0.75rem; border-left: 1px solid rgba(255,255,255,0.1); border-top: none; padding-left: 1.5rem; padding-top: 0; }
        /* tooltips: above */
        #polybar-aside.pos-bottom .poly-tooltip { bottom: calc(100% + 8px); top: auto; left: 50%; right: auto; transform: translateX(-50%); }
        #polybar-aside.pos-bottom .group:hover .poly-tooltip { transform: translateX(-50%) translateY(-4px); opacity: 1; }

        /* ---- LEFT ---- */
        #polybar-aside.pos-left {
            left: 1.5rem; right: auto; top: 50%; bottom: auto;
            transform: translateY(-50%);
            flex-direction: column; align-items: center; justify-content: center;
            width: 3.5rem; height: auto;
            padding: 1rem 0.375rem;
            gap: 0; min-width: unset;
        }
        #polybar-aside.pos-left .poly-nav    { flex-direction: column; gap: 0.25rem; align-items: center; }
        #polybar-aside.pos-left .poly-sep    { display: none; }
        #polybar-aside.pos-left .poly-ghost-section { border-right: none; border-bottom: 1px solid rgba(255,255,255,0.12); padding-right: 0; padding-bottom: 0.5rem; margin-bottom: 0.5rem; }
        #polybar-aside.pos-left .poly-actions-sep   { flex-direction: column; align-items: center; gap: 0.25rem; border-left: none; border-top: 1px solid rgba(255,255,255,0.12); padding-left: 0; padding-top: 0.5rem; margin-top: 0.5rem; }
        /* tooltips: to the RIGHT of icon */
        #polybar-aside.pos-left .poly-tooltip {
            top: 50%; bottom: auto;
            left: calc(100% + 12px); right: auto;
            transform: translateY(-50%);
        }
        #polybar-aside.pos-left .group:hover .poly-tooltip {
            opacity: 1;
            transform: translateY(-50%) translateX(4px);
        }

        /* ---- RIGHT ---- */
        #polybar-aside.pos-right {
            right: 1.5rem; left: auto; top: 50%; bottom: auto;
            transform: translateY(-50%);
            flex-direction: column; align-items: center; justify-content: center;
            width: 3.5rem; height: auto;
            padding: 1rem 0.375rem;
            gap: 0; min-width: unset;
        }
        #polybar-aside.pos-right .poly-nav    { flex-direction: column; gap: 0.25rem; align-items: center; }
        #polybar-aside.pos-right .poly-sep    { display: none; }
        #polybar-aside.pos-right .poly-ghost-section { border-right: none; border-bottom: 1px solid rgba(255,255,255,0.12); padding-right: 0; padding-bottom: 0.5rem; margin-bottom: 0.5rem; }
        #polybar-aside.pos-right .poly-actions-sep   { flex-direction: column; align-items: center; gap: 0.25rem; border-left: none; border-top: 1px solid rgba(255,255,255,0.12); padding-left: 0; padding-top: 0.5rem; margin-top: 0.5rem; }
        /* tooltips: to the LEFT of icon */
        #polybar-aside.pos-right .poly-tooltip {
            top: 50%; bottom: auto;
            right: calc(100% + 12px); left: auto;
            transform: translateY(-50%);
        }
        #polybar-aside.pos-right .group:hover .poly-tooltip {
            opacity: 1;
            transform: translateY(-50%) translateX(-4px);
        }

        /* shared section base */
        .poly-ghost-section, .poly-nav, .poly-actions-sep { display: flex; align-items: center; }
        body.poly-top    #main-wrapper { padding-top: 8rem; padding-left: 1rem; padding-right: 1rem; }
        body.poly-bottom #main-wrapper { padding-top: 2rem; padding-bottom: 6rem; }
        body.poly-left   #main-wrapper { padding-left: 6rem; padding-top: 2rem; }
        body.poly-right  #main-wrapper { padding-right: 6rem; padding-top: 2rem; }
        /* --- HIGH CONTRAST --- */
        html.hc #main-body { background-color: #000 !important; background-image: none !important; }
        html.hc .glass-panel, html.hc .fast-glass {
            background: #0d0d0d !important; border-color: #ffffff !important; backdrop-filter: none !important;
        }
        html.hc #polybar-aside { background: #000 !important; border: 2px solid #fff !important; }
        html.hc .text-gray-400, html.hc .text-gray-500, html.hc .text-gray-600 { color: #bbb !important; }
        html.hc input, html.hc textarea { background: #111 !important; border-color: #fff !important; }
        html.hc .type-examen { background: rgba(239,68,68,0.45) !important; }
        html.hc .type-tarea  { background: rgba(234,179,8,0.45) !important; }
        html.hc .type-nota   { background: rgba(59,130,246,0.45) !important; }
        html.hc a[href], html.hc button { outline-offset: 2px; }

        /* ══════════════════════════════════════════
           GHOST-SHELL CONTROLS — LIQUID GLASS
           ══════════════════════════════════════════ */

        /* ── LIQUID GLASS TOGGLE ────────────────── */
        .gs-toggle {
            position: relative; display: inline-flex; align-items: center;
            width: 3.4rem; height: 1.8rem;
            cursor: pointer; flex-shrink: 0; user-select: none;
        }
        .gs-toggle-rail {
            position: absolute; inset: 0; border-radius: 999px;
            background: rgba(255,255,255,0.06);
            backdrop-filter: blur(8px);
            border: 1px solid rgba(255,255,255,0.14);
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.1), inset 0 -1px 0 rgba(0,0,0,0.2);
            transition: all 0.3s ease;
            overflow: hidden;
        }
        /* glass inner shine */
        .gs-toggle-rail::before {
            content: ''; position: absolute;
            top: 0; left: 0; right: 0; height: 50%;
            background: linear-gradient(180deg, rgba(255,255,255,0.12) 0%, transparent 100%);
            border-radius: 999px 999px 0 0;
        }
        .gs-toggle.on .gs-toggle-rail {
            background: rgba(168,85,247,0.18);
            border-color: rgba(168,85,247,0.5);
            box-shadow: 0 0 16px rgba(168,85,247,0.3),
                        inset 0 1px 0 rgba(255,255,255,0.15),
                        inset 0 0 12px rgba(168,85,247,0.12);
        }
        /* orb */
        .gs-toggle-orb {
            position: absolute; left: 3px; top: 50%; transform: translateY(-50%);
            width: 1.3rem; height: 1.3rem; border-radius: 50%;
            background: linear-gradient(145deg, rgba(255,255,255,0.9) 0%, rgba(200,200,220,0.75) 100%);
            border: 1px solid rgba(255,255,255,0.5);
            box-shadow: 0 2px 6px rgba(0,0,0,0.35), 0 1px 0 rgba(255,255,255,0.4) inset;
            transition: transform 0.35s cubic-bezier(0.34,1.5,0.64,1), background 0.3s, box-shadow 0.3s, border-color 0.3s;
            z-index: 1;
        }
        /* orb highlight */
        .gs-toggle-orb::before {
            content: ''; position: absolute;
            top: 15%; left: 18%; width: 40%; height: 35%;
            background: rgba(255,255,255,0.6); border-radius: 50%;
            filter: blur(1px);
        }
        .gs-toggle.on .gs-toggle-orb {
            transform: translateX(1.6rem) translateY(-50%);
            background: linear-gradient(145deg, #c084fc 0%, #7c3aed 100%);
            border-color: rgba(216,180,254,0.6);
            box-shadow: 0 2px 8px rgba(0,0,0,0.3),
                        0 0 12px rgba(168,85,247,0.7),
                        0 0 24px rgba(168,85,247,0.3),
                        inset 0 1px 0 rgba(255,255,255,0.35);
        }

        /* ── LIQUID GLASS SLIDER ─────────────────── */
        .cyber-slider-wrap { position: relative; width: 100%; user-select: none; }
        .cyber-track {
            position: relative; height: 1.8rem; border-radius: 14px;
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(4px);
            border: 1px solid rgba(255,255,255,0.1);
            box-shadow: inset 0 2px 4px rgba(0,0,0,0.25), inset 0 1px 0 rgba(255,255,255,0.06);
            cursor: ew-resize; overflow: hidden;
        }
        .cyber-fill {
            position: absolute; top: 0; left: 0; bottom: 0; border-radius: 14px 0 0 14px;
            background: linear-gradient(90deg, rgba(99,102,241,0.55) 0%, rgba(168,85,247,0.85) 100%);
            pointer-events: none;
        }
        /* glass shine on fill */
        .cyber-fill::before {
            content: ''; position: absolute; top: 0; left: 0; right: 0; height: 50%;
            background: linear-gradient(180deg, rgba(255,255,255,0.18) 0%, transparent 100%);
            border-radius: 14px 0 0 0;
        }
        .cyber-thumb {
            position: absolute; top: 10%; bottom: 10%;
            width: 3px; border-radius: 999px; pointer-events: none;
            background: rgba(255,255,255,0.95);
            box-shadow: 0 0 6px rgba(255,255,255,0.8), 0 0 16px rgba(168,85,247,0.7);
            transform: translateX(-50%);
        }
        /* floating tooltip */
        .cyber-tooltip {
            position: absolute; bottom: calc(100% + 8px);
            background: rgba(10,10,20,0.85);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(168,85,247,0.4);
            color: #e9d5ff; font-size: 0.65rem; font-weight: 700;
            font-family: monospace; padding: 3px 8px; border-radius: 8px;
            white-space: nowrap; pointer-events: none;
            transform: translateX(-50%);
            opacity: 0; transition: opacity 0.12s;
            box-shadow: 0 4px 16px rgba(0,0,0,0.4), 0 0 8px rgba(168,85,247,0.15);
        }
        .cyber-tooltip::after {
            content:''; position:absolute; top:100%; left:50%; transform:translateX(-50%);
            border:5px solid transparent; border-top-color: rgba(168,85,247,0.4);
        }
        .cyber-slider-wrap:hover .cyber-tooltip,
        .cyber-slider-wrap.dragging .cyber-tooltip { opacity: 1; }
        .cyber-labels {
            display:flex; justify-content:space-between;
            font-size:0.52rem; color:rgba(255,255,255,0.22);
            font-family:monospace; margin-top:0.35rem; padding:0 3px;
            letter-spacing:0.05em;
        }

        /* ── SHORTCUT KEY CHIP ───────────────────── */
        .shortcut-key {
            display:inline-flex; align-items:center; justify-content:center;
            min-width:3.5rem; padding:0.28rem 0.65rem;
            background: rgba(255,255,255,0.06);
            backdrop-filter: blur(8px);
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 10px;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.1), 0 2px 4px rgba(0,0,0,0.2);
            font-family:monospace; font-size:0.7rem;
            color:#c4b5fd; cursor:pointer; transition:all 0.18s;
            white-space:nowrap; user-select:none;
        }
        .shortcut-key:hover {
            border-color:rgba(168,85,247,0.5); background:rgba(168,85,247,0.12);
            color:#e9d5ff; box-shadow: 0 0 12px rgba(168,85,247,0.2), inset 0 1px 0 rgba(255,255,255,0.15);
        }
        .shortcut-key.capturing {
            border-color:#a855f7; background:rgba(168,85,247,0.18); color:#fff;
            box-shadow: 0 0 0 3px rgba(168,85,247,0.15), 0 0 16px rgba(168,85,247,0.25);
            animation: sc-pulse 1s ease-in-out infinite;
        }
        @keyframes sc-pulse {
            0%,100% { box-shadow: 0 0 0 3px rgba(168,85,247,0.15), 0 0 16px rgba(168,85,247,0.25); }
            50%      { box-shadow: 0 0 0 6px rgba(168,85,247,0), 0 0 24px rgba(168,85,247,0.1); }
        }

        /* ── POS BUTTON ── */
        .pos-btn.active {
            background:rgba(168,85,247,0.2); border-color:rgba(168,85,247,0.6) !important;
            color:#e9d5ff !important;
            box-shadow: 0 0 12px rgba(168,85,247,0.25), inset 0 1px 0 rgba(255,255,255,0.1);
        }

        /* ── MOD DROPDOWN ── */
        .mod-dropdown { position:relative; }
        .mod-btn {
            display:flex; align-items:center; gap:0.45rem; padding:0.3rem 0.65rem;
            background: rgba(255,255,255,0.06);
            backdrop-filter: blur(8px);
            border: 1px solid rgba(255,255,255,0.12); border-radius: 10px;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.1);
            font-size:0.68rem; font-family:monospace; color:#c4b5fd;
            cursor:pointer; transition:all 0.18s; white-space:nowrap; user-select:none;
            min-width:4.5rem; justify-content:space-between;
        }
        .mod-btn:hover { border-color:rgba(168,85,247,0.5); background:rgba(168,85,247,0.12); }
        .mod-btn i { font-size:0.5rem; opacity:0.5; transition:transform 0.2s; }
        .mod-btn.open i { transform:rotate(180deg); }
        .mod-menu {
            position:absolute; bottom:calc(100% + 8px); left:0; z-index:400;
            background: rgba(12,12,22,0.92);
            backdrop-filter: blur(20px);
            border: 1px solid rgba(168,85,247,0.22); border-radius: 14px; overflow:hidden;
            box-shadow: 0 -16px 40px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.04),
                        inset 0 1px 0 rgba(255,255,255,0.06);
            min-width:190px;
            opacity:0; transform:translateY(6px) scale(0.97); pointer-events:none;
            transition:opacity 0.18s, transform 0.18s;
        }
        .mod-menu.open { opacity:1; transform:translateY(0) scale(1); pointer-events:auto; }
        .mod-option {
            display:flex; align-items:center; gap:0.7rem; padding:0.6rem 1rem;
            font-size:0.7rem; color:#6b7280; cursor:pointer;
            transition:background 0.1s, color 0.1s;
            border-bottom:1px solid rgba(255,255,255,0.04);
        }
        .mod-option:last-child { border-bottom:none; }
        .mod-option:hover { background:rgba(168,85,247,0.12); color:#f3f4f6; }
        .mod-option.selected { color:#e9d5ff; background:rgba(168,85,247,0.08); }
        .mod-badge {
            display:inline-flex; align-items:center; justify-content:center;
            background:rgba(255,255,255,0.07); border:1px solid rgba(255,255,255,0.1);
            border-radius:7px; padding:2px 8px; font-size:0.64rem; font-family:monospace;
            color:#9ca3af; min-width:2.8rem; text-align:center;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.08);
        }
        .mod-option.selected .mod-badge {
            background:rgba(168,85,247,0.22); border-color:rgba(168,85,247,0.45); color:#e9d5ff;
        }

        /* ── AUTH WALL ────────────────────────────── */
        .auth-tab-active {
            color: #e9d5ff;
            background: rgba(168,85,247,0.12);
            border-bottom: 2px solid rgba(168,85,247,0.7);
        }
        #pw-strength-bar.weak   { background: #ef4444; }
        #pw-strength-bar.medium { background: #f59e0b; }
        #pw-strength-bar.strong { background: #10b981; }
    </style>
</head>
<body class="min-h-screen flex flex-col" id="main-body">

    <aside id="polybar-aside" class="pos-top fixed z-50 hidden lg:flex glass-panel rounded-2xl border border-white/10 shadow-[0_0_30px_rgba(0,0,0,0.5)]">

        <div class="poly-ghost-section" style="display:flex; align-items:center;">
             <a href="{{ url_for('index') }}" class="group w-10 h-10 flex items-center justify-center rounded-2xl transition-all duration-300 relative
                {% if request.endpoint == 'index' %}
                    text-purple-400 bg-white/5 border border-purple-500/30 shadow-[0_0_15px_rgba(168,85,247,0.5)]
                {% else %}
                    text-gray-400 hover:text-purple-400 hover:bg-white/5 border border-transparent
                {% endif %}">
                <i class="fa-solid fa-ghost"></i> <span class="poly-tooltip">Inicio <span class="key-hint">AltGr+1</span></span>
            </a>
        </div>

        <div class="poly-sep h-8 w-[1px] bg-white/20 rounded-full"></div>

        <div class="poly-nav" style="display:flex;">

            <a href="{{ url_for('calendar_view') }}" class="group relative w-10 h-10 flex items-center justify-center rounded-2xl transition-all
                {% if request.endpoint == 'calendar_view' %}
                    text-yellow-400 bg-white/5 border border-yellow-500/30 shadow-[0_0_15px_rgba(250,204,21,0.5)]
                {% else %}
                    text-gray-400 hover:text-yellow-400 hover:bg-white/5 border border-transparent
                {% endif %}">
                <i class="fa-solid fa-calendar-days"></i> <span class="poly-tooltip text-yellow-400">Calendario <span class="key-hint">AltGr+2</span></span>
            </a>

            <a href="{{ url_for('agenda') }}" class="group relative w-10 h-10 flex items-center justify-center rounded-2xl transition-all
                {% if request.endpoint == 'agenda' %}
                    text-indigo-300 bg-white/5 border border-indigo-400/30 shadow-[0_0_15px_rgba(129,140,248,0.5)]
                {% else %}
                    text-gray-400 hover:text-indigo-300 hover:bg-white/5 border border-transparent
                {% endif %}">
                <i class="fa-solid fa-book-bookmark"></i> <span class="poly-tooltip text-indigo-300">Agenda <span class="key-hint">AltGr+3</span></span>
            </a>

            <a href="{{ url_for('horario') }}" class="group relative w-10 h-10 flex items-center justify-center rounded-2xl transition-all
                {% if request.endpoint == 'horario' %}
                    text-orange-400 bg-white/5 border border-orange-500/30 shadow-[0_0_15px_rgba(251,146,60,0.5)]
                {% else %}
                    text-gray-400 hover:text-orange-400 hover:bg-white/5 border border-transparent
                {% endif %}">
                <i class="fa-solid fa-table-cells"></i> <span class="poly-tooltip text-orange-400">Horario <span class="key-hint">AltGr+4</span></span>
            </a>

            <a href="{{ url_for('chat') }}" class="group relative w-10 h-10 flex items-center justify-center rounded-2xl transition-all
                {% if request.endpoint == 'chat' %}
                    text-cyan-400 bg-white/5 border border-cyan-500/30 shadow-[0_0_15px_rgba(34,211,238,0.5)]
                {% else %}
                    text-gray-400 hover:text-cyan-400 hover:bg-white/5 border border-transparent
                {% endif %}">
                <i class="fa-solid fa-comments"></i> <span class="poly-tooltip text-cyan-400">Chat grupal</span>
            </a>

            <a href="{{ url_for('private_zone') }}" class="group relative w-10 h-10 flex items-center justify-center rounded-2xl transition-all
                {% if request.endpoint == 'private_zone' or request.endpoint == 'private_login' %}
                    text-pink-400 bg-white/5 border border-pink-500/30 shadow-[0_0_15px_rgba(236,72,153,0.5)]
                {% else %}
                    text-gray-400 hover:text-pink-400 hover:bg-white/5 border border-transparent
                {% endif %}">
                {% if session.get('private_user') %}<span class="absolute top-2 right-2 w-2 h-2 bg-pink-500 rounded-full animate-pulse"></span>{% endif %}
                <i class="fa-solid fa-lock"></i> <span class="poly-tooltip text-pink-400">Privado <span class="key-hint">AltGr+5</span></span>
            </a>

            <a href="{{ url_for('admin') }}" class="group relative w-10 h-10 flex items-center justify-center rounded-2xl transition-all
                {% if 'admin' in request.endpoint %}
                    text-green-400 bg-white/5 border border-green-500/30 shadow-[0_0_15px_rgba(74,222,128,0.5)]
                {% else %}
                    text-gray-400 hover:text-green-400 hover:bg-white/5 border border-transparent
                {% endif %}">
                 {% if session.get('logged_in') %}<span class="absolute top-2 right-2 w-2 h-2 bg-green-500 rounded-full animate-pulse"></span>{% endif %}
                <i class="fa-solid fa-terminal"></i> <span class="poly-tooltip text-green-400">Admin <span class="key-hint">AltGr+6</span></span>
            </a>
        </div>

        <div class="poly-actions-sep" style="display:flex;">
            <button onclick="toggleShortcuts()" class="group w-10 h-10 flex items-center justify-center rounded-2xl text-gray-600 hover:text-white transition-all relative">
                <i class="fa-solid fa-keyboard"></i> <span class="poly-tooltip">Atajos <span class="key-hint">?</span></span>
            </button>
            <button onclick="toggleSettings()" class="group w-10 h-10 flex items-center justify-center rounded-2xl text-gray-600 hover:text-purple-400 transition-all relative" id="settingsBtn">
                <i class="fa-solid fa-gear"></i> <span class="poly-tooltip text-purple-400">Configuración</span>
            </button>
            {% if session.get('logged_in') or session.get('private_user') %}
            <a href="{{ url_for('logout') }}" class="group w-10 h-10 flex items-center justify-center rounded-2xl bg-red-500/10 border border-red-500/20 text-red-400 hover:bg-red-500 hover:text-white hover:shadow-[0_0_15px_rgba(239,68,68,0.5)] transition-all duration-300 relative">
                <i class="fa-solid fa-power-off"></i> <span class="poly-tooltip text-red-400">Salir</span>
            </a>
            {% endif %}
        </div>
    </aside>

    <div id="brightness-wrapper">
    <div id="main-wrapper" class="max-w-7xl mx-auto px-4 w-full py-8 relative z-10 pt-32 transition-all duration-300">

        <nav class="flex flex-wrap justify-between items-center py-4 mb-8 border-b border-white/5 animate-enter lg:hidden">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 bg-purple-500/10 rounded-xl flex items-center justify-center border border-purple-500/20">
                    <i class="fa-solid fa-ghost text-purple-400 text-xl"></i>
                </div>
                <h1 class="text-2xl font-bold tracking-tight text-white">GHOST<span class="text-purple-400">SHELL</span></h1>
            </div>
            <div class="flex items-center gap-4 text-sm font-medium">
                 <a href="{{ url_for('index') }}" class="text-gray-400"><i class="fa-solid fa-house"></i></a>
                 <a href="{{ url_for('calendar_view') }}" class="text-gray-400"><i class="fa-solid fa-calendar-days"></i></a>
            </div>
        </nav>

        <main id="main-content">
            {% block content %}{% endblock %}
        </main>

        <footer class="mt-16 pt-8 border-t border-white/5 text-center text-xs text-gray-500 animate-enter delay-200">
            <p class="opacity-50">SYSTEM READY. GHOST-SHELL V3.6 // ANTI-DARK-READER ENABLED</p>
        </footer>
    </div>

    </div><!-- /brightness-wrapper -->

    <!-- SETTINGS MODAL -->
    <div id="settingsModal" class="hidden fixed inset-0 z-[110] flex items-center justify-center p-4">
        <!-- backdrop -->
        <div class="absolute inset-0 bg-black/80 backdrop-blur-md" onclick="toggleSettings()"></div>

        <div class="relative glass-panel w-full max-w-lg rounded-2xl border border-purple-500/30 shadow-[0_0_60px_rgba(168,85,247,0.25)] overflow-hidden animate-enter">

            <!-- Header -->
            <div class="flex items-center justify-between px-6 py-4 border-b border-white/10 bg-purple-500/5">
                <div class="flex items-center gap-3">
                    <div class="w-8 h-8 rounded-lg bg-purple-500/20 border border-purple-500/30 flex items-center justify-center">
                        <i class="fa-solid fa-gear text-purple-400 text-sm"></i>
                    </div>
                    <h3 class="text-lg font-bold text-white tracking-wide">CONFIGURACIÓN</h3>
                </div>
                <button onclick="toggleSettings()" class="text-gray-500 hover:text-white transition w-8 h-8 flex items-center justify-center rounded-lg hover:bg-white/10">
                    <i class="fa-solid fa-xmark"></i>
                </button>
            </div>

            <div class="p-6 space-y-6 max-h-[70vh] overflow-y-auto custom-scrollbar">

                <!-- POSICIÓN DE POLYBAR -->
                <div>
                    <div class="flex items-center gap-2 mb-3">
                        <i class="fa-solid fa-arrows-up-down-left-right text-purple-400 text-xs"></i>
                        <span class="text-xs font-bold text-gray-400 uppercase tracking-widest">Posición de la Polybar</span>
                    </div>
                    <div class="relative w-full aspect-video max-w-xs mx-auto bg-white/5 border border-white/10 rounded-xl overflow-hidden p-2">
                        <div class="w-full h-full rounded-lg bg-black/30 border border-white/5 relative flex items-center justify-center">
                            <span class="text-[0.6rem] text-gray-700 font-mono uppercase tracking-widest select-none">PANTALLA</span>
                            <button data-pos="top" onclick="setPolybarPos('top')" class="pos-btn absolute top-2 left-1/2 -translate-x-1/2 w-20 h-5 rounded-md border border-white/15 bg-white/5 text-[0.55rem] font-bold text-gray-400 uppercase tracking-widest transition-all hover:border-purple-500/60 hover:text-purple-300 flex items-center justify-center gap-1"><i class="fa-solid fa-minus text-[0.4rem]"></i> Arriba</button>
                            <button data-pos="bottom" onclick="setPolybarPos('bottom')" class="pos-btn absolute bottom-2 left-1/2 -translate-x-1/2 w-20 h-5 rounded-md border border-white/15 bg-white/5 text-[0.55rem] font-bold text-gray-400 uppercase tracking-widest transition-all hover:border-purple-500/60 hover:text-purple-300 flex items-center justify-center gap-1"><i class="fa-solid fa-minus text-[0.4rem]"></i> Abajo</button>
                            <button data-pos="left" onclick="setPolybarPos('left')" class="pos-btn absolute left-2 top-1/2 -translate-y-1/2 w-5 h-20 rounded-md border border-white/15 bg-white/5 text-[0.5rem] font-bold text-gray-400 uppercase tracking-widest transition-all hover:border-purple-500/60 hover:text-purple-300 flex items-center justify-center"><span style="writing-mode:vertical-rl;transform:rotate(180deg)">Izq</span></button>
                            <button data-pos="right" onclick="setPolybarPos('right')" class="pos-btn absolute right-2 top-1/2 -translate-y-1/2 w-5 h-20 rounded-md border border-white/15 bg-white/5 text-[0.5rem] font-bold text-gray-400 uppercase tracking-widest transition-all hover:border-purple-500/60 hover:text-purple-300 flex items-center justify-center"><span style="writing-mode:vertical-rl">Der</span></button>
                        </div>
                    </div>
                </div>

                <div class="h-px bg-white/8"></div>

                <!-- BRILLO -->
                <div>
                    <div class="flex items-center justify-between mb-3">
                        <div class="flex items-center gap-2">
                            <i class="fa-solid fa-sun text-yellow-400 text-xs"></i>
                            <span class="text-xs font-bold text-gray-400 uppercase tracking-widest">Brillo de la interfaz</span>
                        </div>
                        <span id="brightness-value" class="text-xs font-mono text-purple-300 bg-purple-500/10 px-2 py-0.5 rounded border border-purple-500/20" style="min-width:3.5rem;text-align:center">50%</span>
                    </div>
                    <div class="cyber-slider-wrap" id="cyber-wrap">
                        <div class="cyber-tooltip" id="cyber-tooltip">50%</div>
                        <div class="cyber-track" id="cyber-track">
                            <div class="cyber-fill" id="cyber-fill" style="width:50%"></div>
                            <div class="cyber-thumb" id="cyber-thumb" style="left:50%"></div>
                        </div>
                        <div class="cyber-labels">
                            <span>◀ Oscuro</span>
                            <span>50% = normal</span>
                            <span>Brillante ▶</span>
                        </div>
                    </div>
                </div>

                <div class="h-px bg-white/8"></div>

                <!-- ALTO CONTRASTE -->
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-3">
                        <i class="fa-solid fa-circle-half-stroke text-xs" style="color:#fff"></i>
                        <div>
                            <p class="text-sm font-bold text-white leading-tight">Alto contraste</p>
                            <p class="text-[0.65rem] text-gray-500 mt-0.5">Fondos sólidos, bordes nítidos, sin transparencias</p>
                        </div>
                    </div>
                    <div class="gs-toggle" id="hc-toggle" onclick="toggleHighContrast()"><div class="gs-toggle-rail"></div><div class="gs-toggle-orb"></div></div>
                </div>

                <div class="h-px bg-white/8"></div>

                <!-- ATAJOS PERSONALIZADOS -->
                <div>
                    <div class="flex items-center justify-between mb-2">
                        <div class="flex items-center gap-3">
                            <i class="fa-solid fa-keyboard text-purple-400 text-xs"></i>
                            <div>
                                <p class="text-sm font-bold text-white leading-tight">Atajos personalizados</p>
                                <p class="text-[0.65rem] text-gray-500 mt-0.5">Asigna tus propias teclas de navegación</p>
                            </div>
                        </div>
                        <div class="gs-toggle" id="custom-sc-toggle" onclick="toggleCustomShortcuts()"><div class="gs-toggle-rail"></div><div class="gs-toggle-orb"></div></div>
                    </div>

                    <div id="shortcut-editor" class="mt-3 space-y-1.5 transition-all duration-200" style="opacity:0.4;pointer-events:none">
                        <p class="text-[0.6rem] text-gray-600 mb-2 flex items-center gap-1.5">
                            <i class="fa-solid fa-circle-info text-purple-500/60"></i>
                            Haz clic en una tecla y pulsa la nueva. Modificador: <span class="font-mono bg-white/10 px-1 rounded">AltGr</span> por defecto.
                        </p>
                        <div id="shortcut-rows" class="space-y-1.5"><!-- JS renders rows --></div>
                        <button onclick="resetShortcuts()" class="mt-2 text-[0.65rem] text-gray-600 hover:text-gray-400 transition flex items-center gap-1.5">
                            <i class="fa-solid fa-rotate-left text-[0.6rem]"></i> Restaurar por defecto
                        </button>
                    </div>
                </div>

            </div>

            <!-- Footer -->
            <div class="px-6 py-3 border-t border-white/5 bg-black/20 flex justify-between items-center">
                <button onclick="resetSettings()" class="text-xs text-gray-600 hover:text-gray-400 transition flex items-center gap-1.5">
                    <i class="fa-solid fa-rotate-left text-[0.65rem]"></i> Restablecer todo
                </button>
                <span class="text-[0.6rem] font-mono text-gray-700">Guardado automáticamente</span>
            </div>
        </div>
    </div>

    <!-- ══════════════════════════════════════════════════════
         AUTH WALL MODAL — primera visita / sesión caducada
         ══════════════════════════════════════════════════════ -->
    <div id="authWallModal" class="hidden fixed inset-0 z-[200] flex items-center justify-center p-4" style="background:rgba(0,0,0,0.92);backdrop-filter:blur(18px)">
        <div class="w-full max-w-sm relative">
            <div class="absolute -top-20 -left-20 w-64 h-64 rounded-full bg-purple-600/20 blur-[80px] pointer-events-none"></div>
            <div class="absolute -bottom-20 -right-20 w-64 h-64 rounded-full bg-pink-600/15 blur-[80px] pointer-events-none"></div>
            <div class="glass-panel rounded-2xl border border-white/10 shadow-[0_0_60px_rgba(168,85,247,0.2)] overflow-hidden relative z-10">
                <div class="bg-purple-500/8 border-b border-white/8 px-6 py-4 flex items-center gap-3">
                    <div class="w-8 h-8 rounded-xl bg-purple-500/20 border border-purple-500/30 flex items-center justify-center">
                        <i class="fa-solid fa-ghost text-purple-400 text-sm"></i>
                    </div>
                    <div>
                        <h2 class="text-white font-bold text-sm tracking-wide">GHOST-SHELL</h2>
                        <p class="text-gray-500 text-[0.6rem] font-mono uppercase" id="auth-subtitle">Sistema de identificación</p>
                    </div>
                </div>
                <div class="flex border-b border-white/8">
                    <button id="tab-register" onclick="switchAuthTab('register')" class="flex-1 py-2.5 text-xs font-bold tracking-wider transition-all auth-tab-active"><i class="fa-solid fa-user-plus mr-1.5 text-[0.65rem]"></i>REGISTRARSE</button>
                    <button id="tab-login" onclick="switchAuthTab('login')" class="flex-1 py-2.5 text-xs font-bold tracking-wider transition-all text-gray-600 hover:text-gray-400"><i class="fa-solid fa-right-to-bracket mr-1.5 text-[0.65rem]"></i>ENTRAR</button>
                </div>
                <div class="p-6">
                    <div id="auth-notice" class="hidden mb-4 p-3 rounded-xl border text-xs text-center"></div>
                    <div id="auth-error" class="hidden mb-4 p-3 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-xs text-center"></div>
                    <div id="auth-success" class="hidden mb-4 p-3 bg-green-500/10 border border-green-500/20 rounded-xl text-green-400 text-xs text-center"></div>
                    <form id="auth-form" class="space-y-4" onsubmit="submitAuth(event)">
                        <div>
                            <label class="block text-[0.65rem] font-bold text-gray-500 uppercase tracking-widest mb-1.5">Nombre de usuario</label>
                            <input id="auth-username" type="text" autocomplete="username" class="w-full px-4 py-2.5 rounded-xl input-liquid text-sm" placeholder="Tu nombre" required>
                        </div>
                        <div>
                            <label class="block text-[0.65rem] font-bold text-gray-500 uppercase tracking-widest mb-1.5">Contraseña</label>
                            <div class="relative">
                                <input id="auth-password" type="password" autocomplete="current-password" class="w-full px-4 py-2.5 pr-10 rounded-xl input-liquid text-sm" placeholder="••••••••" required>
                                <button type="button" onclick="toggleAuthPw()" class="absolute right-3 top-1/2 -translate-y-1/2 text-gray-600 hover:text-gray-400"><i class="fa-solid fa-eye text-xs" id="pw-eye"></i></button>
                            </div>
                        </div>
                        <div id="pw-strength-wrap" class="space-y-1">
                            <div class="h-1 bg-white/8 rounded-full overflow-hidden"><div id="pw-strength-bar" class="h-full rounded-full transition-all duration-300" style="width:0%"></div></div>
                            <p id="pw-strength-label" class="text-[0.55rem] text-gray-600 font-mono"></p>
                        </div>
                        <button type="submit" id="auth-submit-btn" class="w-full btn-glow text-white font-bold py-3 rounded-xl tracking-wider text-sm mt-2">REGISTRARSE</button>
                    </form>
                    <p class="text-center text-[0.58rem] text-gray-700 mt-4">Sesión activa durante <span class="text-gray-500">5 días</span>. Datos cifrados con hash seguro.</p>
                </div>
            </div>
        </div>
    </div>

    <!-- SSE live message toast -->
    <div id="sse-toast" class="hidden fixed bottom-6 left-1/2 -translate-x-1/2 z-[300] max-w-sm w-full px-4">
        <div class="glass-panel border border-purple-500/40 rounded-2xl p-4 shadow-[0_0_30px_rgba(168,85,247,0.3)] flex items-start gap-3">
            <div class="w-8 h-8 rounded-xl bg-purple-500/20 border border-purple-500/30 flex items-center justify-center flex-shrink-0 mt-0.5">
                <i class="fa-solid fa-satellite-dish text-purple-400 text-xs animate-pulse"></i>
            </div>
            <div class="flex-1 min-w-0">
                <p class="text-[0.6rem] font-bold text-purple-300 uppercase tracking-widest mb-0.5">Mensaje del sistema</p>
                <p id="sse-toast-text" class="text-sm text-white"></p>
            </div>
            <button onclick="document.getElementById('sse-toast').classList.add('hidden')" class="text-gray-600 hover:text-white ml-1"><i class="fa-solid fa-xmark text-xs"></i></button>
        </div>
    </div>

    <div id="shortcutsModal" class="hidden fixed inset-0 z-[100] bg-black/90 backdrop-blur-md flex items-center justify-center p-4 animate-enter">
        <div class="glass-panel max-w-2xl w-full p-8 rounded-2xl border border-purple-500/30 shadow-[0_0_50px_rgba(168,85,247,0.2)]">
            <div class="flex justify-between items-center mb-6 border-b border-white/10 pb-4">
                <h3 class="text-2xl font-bold text-white tracking-wide"><i class="fa-solid fa-keyboard text-purple-400 mr-2"></i> ATAJOS TÁCTICOS</h3>
                <span class="text-xs font-mono text-gray-500">PRESIONA <span class="bg-white/10 px-1 rounded text-white">ESC</span> PARA CERRAR</span>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-8">
                <div>
                    <h4 class="text-sm font-bold text-purple-400 uppercase mb-3">Navegación (AltGr)</h4>
                    <ul class="space-y-2 text-sm text-gray-300">
                        <li class="flex justify-between"><span>Dashboard</span> <span class="font-mono bg-white/5 px-2 rounded text-white">AltGr + 1</span></li>
                        <li class="flex justify-between"><span>Calendario</span> <span class="font-mono bg-white/5 px-2 rounded text-white">AltGr + 2</span></li>
                        <li class="flex justify-between"><span>Agenda</span> <span class="font-mono bg-white/5 px-2 rounded text-white">AltGr + 3</span></li>
                        <li class="flex justify-between"><span>Horario</span> <span class="font-mono bg-white/5 px-2 rounded text-white">AltGr + 4</span></li>
                        <li class="flex justify-between"><span>Zona Privada</span> <span class="font-mono bg-white/5 px-2 rounded text-white">AltGr + 5</span></li>
                        <li class="flex justify-between"><span>Admin</span> <span class="font-mono bg-white/5 px-2 rounded text-white">AltGr + 6</span></li>
                    </ul>
                </div>
                <div>
                    <h4 class="text-sm font-bold text-yellow-400 uppercase mb-3">Acciones</h4>
                    <ul class="space-y-2 text-sm text-gray-300">
                        <li class="flex justify-between"><span>Buscar (Inicio)</span> <span class="font-mono bg-white/5 px-2 rounded text-white">/</span></li>
                        <li class="flex justify-between"><span>Cerrar / Atrás</span> <span class="font-mono bg-white/5 px-2 rounded text-white">ESC</span></li>
                        <li class="flex justify-between"><span>Enviar Formulario</span> <span class="font-mono bg-white/5 px-2 rounded text-white">Ctrl + Enter</span></li>
                        <li class="flex justify-between"><span>Ver Ayuda</span> <span class="font-mono bg-white/5 px-2 rounded text-white">?</span></li>
                    </ul>
                </div>
            </div>
        </div>
    </div>

    <script>
        document.addEventListener('keydown', (e) => {
            const activeTag = document.activeElement.tagName;
            const isTyping = (activeTag === 'INPUT' || activeTag === 'TEXTAREA');

            if (e.key === 'Escape') {
                document.getElementById('shortcutsModal').classList.add('hidden');
                document.getElementById('settingsModal').classList.add('hidden');
                if(typeof closeAddModal === 'function') closeAddModal();
                if(document.getElementById('viewModal')) document.getElementById('viewModal').classList.add('hidden');
                if(document.getElementById('user-modal')) document.getElementById('user-modal').classList.add('hidden');
                if(document.getElementById('noteModal')) document.getElementById('noteModal').classList.add('hidden');
                document.activeElement.blur();
                return;
            }

            if (e.ctrlKey && e.key === 'Enter') {
                const form = document.querySelector('form');
                if(form) form.submit();
                return;
            }

            // --- NAVEGACIÓN (usa custom shortcuts si están activados) ---
            if (isTyping) return;
            const _navShortcuts = getShortcuts();
            for (const sc of _navShortcuts) {
                let modOk = false;
                if (sc.mod === 'AltGraph')  modOk = e.getModifierState('AltGraph');
                else if (sc.mod === 'Control') modOk = e.ctrlKey && !e.altKey;
                else if (sc.mod === 'Alt')     modOk = e.altKey && !e.ctrlKey && !e.getModifierState('AltGraph');
                else if (sc.mod === 'Shift')   modOk = e.shiftKey && !e.ctrlKey && !e.altKey;
                else if (sc.mod === 'Meta')    modOk = e.metaKey;
                else if (sc.mod === 'none')    modOk = !e.ctrlKey && !e.altKey && !e.metaKey && !e.shiftKey && !e.getModifierState('AltGraph');
                if (modOk && e.code === sc.code) {
                    e.preventDefault();
                    window.location.href = sc.url;
                    return;
                }
            }

            if (e.key === '?') toggleShortcuts();

            if (window.location.pathname === '/' || window.location.pathname === '/index') {
                if (e.key === '/') {
                    e.preventDefault();
                    document.getElementById('searchInput')?.focus();
                }
            }

            if (window.location.pathname.includes('/calendar')) {
                if (e.key === 'ArrowLeft') document.getElementById('btn-prev-month')?.click();
                if (e.key === 'ArrowRight') document.getElementById('btn-next-month')?.click();
                if (e.key === 'n' || e.key === 'N') {
                    e.preventDefault();
                    if(typeof openAddModal === 'function') openAddModal();
                }
            }
        });

        function toggleShortcuts() {
            const m = document.getElementById('shortcutsModal');
            m.classList.toggle('hidden');
        }

        // =============================================
        // SETTINGS SYSTEM
        // =============================================
        const SETTINGS_KEY = 'ghostshell_settings';
        function loadSettings() { try { return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {}; } catch(e) { return {}; } }
        function saveSettings(s) { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); }

        // ═══════════════════════════════════════
        // CYBER BRIGHTNESS SLIDER
        // ═══════════════════════════════════════
        let _briDragging = false;

        function _cyberPct(e) {
            const track = document.getElementById('cyber-track');
            if (!track) return 50;
            const r = track.getBoundingClientRect();
            return Math.max(0, Math.min(100, Math.round((e.clientX - r.left) / r.width * 100)));
        }

        function _cyberApply(val, save) {
            const fill    = document.getElementById('cyber-fill');
            const thumb   = document.getElementById('cyber-thumb');
            const tooltip = document.getElementById('cyber-tooltip');
            const disp    = document.getElementById('brightness-value');
            const pStr = val + '%';
            if (fill)    { fill.style.width = pStr; }
            if (thumb)   { thumb.style.left = pStr; }
            if (tooltip) { tooltip.textContent = pStr; tooltip.style.left = pStr; }
            if (disp)    { disp.textContent = pStr; }
            // Live filter
            const fv = val <= 50 ? 0.25 + (val/50)*0.75 : 1.0 + ((val-50)/50)*0.35; // max 1.35
            const bw = document.getElementById('brightness-wrapper');
            if (bw) bw.style.filter = `brightness(${fv.toFixed(3)})`;
            // Counter-filter visual elements so they don't blow out
            const inv = (1 / fv).toFixed(3);
            bw && bw.querySelectorAll('canvas, img, iframe, video').forEach(el => {
                el.style.filter = `brightness(${inv})`;
            });
            if (save) { const s = loadSettings(); s.brightness = val; saveSettings(s); }
        }

        function initCyberSlider(initVal) {
            const track = document.getElementById('cyber-track');
            const wrap  = document.getElementById('cyber-wrap');
            if (!track) return;
            _cyberApply(initVal, false);

            track.addEventListener('mousedown', e => {
                _briDragging = true; wrap.classList.add('dragging');
                _cyberApply(_cyberPct(e), false); e.preventDefault();
            });
            document.addEventListener('mousemove', e => {
                if (!_briDragging) return;
                _cyberApply(_cyberPct(e), false); // real-time, no save
            });
            document.addEventListener('mouseup', e => {
                if (!_briDragging) return;
                _briDragging = false; wrap.classList.remove('dragging');
                _cyberApply(_cyberPct(e), true); // save on release
            });
            track.addEventListener('touchstart', e => {
                _briDragging = true; wrap.classList.add('dragging');
                _cyberApply(_cyberPct(e.touches[0]), false); e.preventDefault();
            }, {passive:false});
            document.addEventListener('touchmove', e => {
                if (_briDragging) _cyberApply(_cyberPct(e.touches[0]), false);
            }, {passive:false});
            document.addEventListener('touchend', e => {
                if (!_briDragging) return;
                _briDragging = false; wrap.classList.remove('dragging');
                _cyberApply(_cyberPct(e.changedTouches[0]), true);
            });
        }

        function setBrightness(val) {
            val = Math.max(0, Math.min(100, parseInt(val)));
            _cyberApply(val, true);
        }

        // ═══════════════════════════════════════
        // TOGGLE sync
        // ═══════════════════════════════════════
        function syncToggle(id, on) {
            const el = document.getElementById(id);
            if (!el) return;
            on ? el.classList.add('on') : el.classList.remove('on');
        }

        // ═══════════════════════════════════════
        // MODIFIER DROPDOWN
        // ═══════════════════════════════════════
        const MOD_OPTIONS = [
            { value:'AltGraph', label:'AltGr',  desc:'tecla AltGr derecha' },
            { value:'Control',  label:'Ctrl',   desc:'Control izq o der' },
            { value:'Alt',      label:'Alt',    desc:'Alt izquierdo' },
            { value:'Shift',    label:'Shift',  desc:'Mayúsculas' },
            { value:'Meta',     label:'Super',  desc:'Windows / Cmd' },
            { value:'none',     label:'—',      desc:'sin modificador' },
        ];
        let _openModDropdown = null;

        function buildModDropdown(container, currentMod, onSelect) {
            container.innerHTML = '';
            container.className = 'mod-dropdown';
            const mo = MOD_OPTIONS.find(m => m.value === currentMod) || MOD_OPTIONS[0];
            const btn = document.createElement('div');
            btn.className = 'mod-btn';
            btn.innerHTML = `<span class="mod-badge">${mo.label}</span><i class="fa-solid fa-chevron-down"></i>`;
            const menu = document.createElement('div');
            menu.className = 'mod-menu';
            MOD_OPTIONS.forEach(opt => {
                const item = document.createElement('div');
                item.className = 'mod-option' + (opt.value === currentMod ? ' selected' : '');
                item.innerHTML = `<span class="mod-badge">${opt.label}</span><span style="font-size:0.62rem;opacity:0.7">${opt.desc}</span>`;
                item.addEventListener('click', e => {
                    e.stopPropagation();
                    onSelect(opt.value);
                    btn.innerHTML = `<span class="mod-badge">${opt.label}</span><i class="fa-solid fa-chevron-down"></i>`;
                    menu.querySelectorAll('.mod-option').forEach(el => el.classList.remove('selected'));
                    item.classList.add('selected');
                    closeModMenus();
                });
                menu.appendChild(item);
            });
            btn.addEventListener('click', e => {
                e.stopPropagation();
                const isOpen = menu.classList.contains('open');
                closeModMenus();
                if (!isOpen) { menu.classList.add('open'); btn.classList.add('open'); _openModDropdown = {menu,btn}; }
            });
            container.appendChild(btn);
            container.appendChild(menu);
        }
        function closeModMenus() {
            document.querySelectorAll('.mod-menu.open').forEach(m => m.classList.remove('open'));
            document.querySelectorAll('.mod-btn.open').forEach(b => b.classList.remove('open'));
            _openModDropdown = null;
        }
        document.addEventListener('click', () => closeModMenus());

        // ═══════════════════════════════════════
        // APPLY ALL SETTINGS
        // ═══════════════════════════════════════
        function applySettings(s) {
            const aside = document.getElementById('polybar-aside');
            const body  = document.getElementById('main-body');
            const wrapper = document.getElementById('main-wrapper');
            const pos = s.polybarPos || 'top';
            document.documentElement.setAttribute('data-polybar-pos', pos);
            ['pos-top','pos-bottom','pos-left','pos-right'].forEach(cl => aside.classList.remove(cl));
            aside.classList.add('pos-' + pos);
            ['poly-top','poly-bottom','poly-left','poly-right'].forEach(cl => body.classList.remove(cl));
            body.classList.add('poly-' + pos);
            wrapper.style.cssText = '';
            if (pos==='top')    wrapper.style.paddingTop = '8rem';
            if (pos==='bottom') { wrapper.style.paddingTop='2rem'; wrapper.style.paddingBottom='6rem'; }
            if (pos==='left')   { wrapper.style.paddingLeft='6rem'; wrapper.style.paddingTop='2rem'; }
            if (pos==='right')  { wrapper.style.paddingRight='6rem'; wrapper.style.paddingTop='2rem'; }
            document.querySelectorAll('.pos-btn').forEach(b => b.classList.toggle('active', b.getAttribute('data-pos')===pos));

            const bri = s.brightness !== undefined ? s.brightness : 50;
            _cyberApply(bri, false);
            initCyberSlider(bri);

            document.documentElement.classList.toggle('hc', !!s.highContrast);
            syncToggle('hc-toggle', !!s.highContrast);

            syncToggle('custom-sc-toggle', !!s.customShortcutsEnabled);
            const editor = document.getElementById('shortcut-editor');
            if (editor) {
                editor.style.opacity = s.customShortcutsEnabled ? '1' : '0.4';
                editor.style.pointerEvents = s.customShortcutsEnabled ? 'auto' : 'none';
            }
        }

        function setPolybarPos(pos) { const s=loadSettings(); s.polybarPos=pos; saveSettings(s); applySettings(s); }
        function toggleHighContrast() { const s=loadSettings(); s.highContrast=!s.highContrast; saveSettings(s); applySettings(s); }
        function toggleCustomShortcuts() {
            const s=loadSettings(); s.customShortcutsEnabled=!s.customShortcutsEnabled;
            saveSettings(s); applySettings(s);
            if (s.customShortcutsEnabled) buildShortcutEditor();
        }
        function resetSettings() { localStorage.removeItem(SETTINGS_KEY); applySettings({}); buildShortcutEditor(); }

        // ═══════════════════════════════════════
        // CUSTOM SHORTCUTS
        // ═══════════════════════════════════════
        const DEFAULT_SHORTCUTS = [
            { label:'Dashboard',    code:'Digit1', mod:'AltGraph', url:"{{ url_for('index') }}" },
            { label:'Calendario',   code:'Digit2', mod:'AltGraph', url:"{{ url_for('calendar_view') }}" },
            { label:'Agenda',       code:'Digit3', mod:'AltGraph', url:"{{ url_for('agenda') }}" },
            { label:'Horario',      code:'Digit4', mod:'AltGraph', url:"{{ url_for('horario') }}" },
            { label:'Zona Privada', code:'Digit5', mod:'AltGraph', url:"{{ url_for('private_zone') }}" },
            { label:'Admin',        code:'Digit6', mod:'AltGraph', url:"{{ url_for('admin') }}" },
        ];
        function getShortcuts() {
            const s = loadSettings();
            // Only return custom shortcuts if the feature is enabled
            if (s.customShortcutsEnabled && s.customShortcuts) return s.customShortcuts;
            return DEFAULT_SHORTCUTS.map(d => ({...d}));
        }
        function saveCustomShortcuts(sc) { const s=loadSettings(); s.customShortcuts=sc; saveSettings(s); }
        function resetShortcuts() { const s=loadSettings(); delete s.customShortcuts; saveSettings(s); buildShortcutEditor(); }
        function codeToLabel(code) {
            if (code.startsWith('Digit')) return code.replace('Digit','');
            if (code.startsWith('Key'))   return code.replace('Key','');
            const map={Space:'Spc',Enter:'↵',Tab:'Tab',Backspace:'⌫',ArrowUp:'↑',ArrowDown:'↓',ArrowLeft:'←',ArrowRight:'→'};
            return map[code] || code;
        }

        let capturingIdx = null;
        let _scCaptureHandler = null;

        function buildShortcutEditor() {
            const rows = document.getElementById('shortcut-rows');
            if (!rows) return;
            // Always use the full default list for the editor UI (show all 6)
            const s = loadSettings();
            const shortcuts = s.customShortcuts ? s.customShortcuts : DEFAULT_SHORTCUTS.map(d=>({...d}));
            rows.innerHTML = '';
            if (_scCaptureHandler) { document.removeEventListener('keydown', _scCaptureHandler, true); _scCaptureHandler=null; }
            capturingIdx = null;

            shortcuts.forEach((sc, i) => {
                const row = document.createElement('div');
                row.className = 'flex items-center gap-2 py-1.5 px-2 rounded-lg hover:bg-white/5 transition-colors';

                const lbl = document.createElement('span');
                lbl.className = 'text-sm text-gray-300 flex-1';
                lbl.textContent = sc.label;
                row.appendChild(lbl);

                const modWrap = document.createElement('div');
                buildModDropdown(modWrap, sc.mod, newMod => {
                    shortcuts[i].mod = newMod; saveCustomShortcuts(shortcuts);
                });
                row.appendChild(modWrap);

                const plus = document.createElement('span');
                plus.className = 'text-xs text-gray-700 select-none px-0.5';
                plus.textContent = '+';
                row.appendChild(plus);

                const keyBtn = document.createElement('div');
                keyBtn.className = 'shortcut-key';
                keyBtn.id = `sc-key-${i}`;
                keyBtn.dataset.idx = i;
                keyBtn.textContent = codeToLabel(sc.code);
                keyBtn.addEventListener('click', () => {
                    if (capturingIdx !== null) {
                        const prev = document.getElementById(`sc-key-${capturingIdx}`);
                        if (prev) { prev.classList.remove('capturing'); prev.textContent = codeToLabel(shortcuts[capturingIdx].code); }
                    }
                    capturingIdx = i;
                    keyBtn.classList.add('capturing');
                    keyBtn.textContent = '⌨';
                });
                row.appendChild(keyBtn);
                rows.appendChild(row);
            });

            const resetBtn = document.createElement('button');
            resetBtn.className = 'mt-2 w-full text-[0.65rem] text-gray-600 hover:text-gray-400 transition flex items-center justify-center gap-1.5 py-1';
            resetBtn.innerHTML = '<i class="fa-solid fa-rotate-left text-[0.6rem]"></i> Restaurar por defecto';
            resetBtn.onclick = resetShortcuts;
            rows.appendChild(resetBtn);

            _scCaptureHandler = function(e) {
                if (capturingIdx === null) return;
                e.preventDefault(); e.stopPropagation();
                if (e.key === 'Escape') {
                    const btn = document.getElementById(`sc-key-${capturingIdx}`);
                    if (btn) { btn.classList.remove('capturing'); btn.textContent = codeToLabel(shortcuts[capturingIdx].code); }
                    capturingIdx = null; return;
                }
                if (['Control','Alt','Shift','Meta','AltGraph'].includes(e.key)) return;
                shortcuts[capturingIdx].code = e.code;
                const btn = document.getElementById(`sc-key-${capturingIdx}`);
                if (btn) { btn.classList.remove('capturing'); btn.textContent = codeToLabel(e.code); }
                saveCustomShortcuts(shortcuts);
                capturingIdx = null;
            };
            document.addEventListener('keydown', _scCaptureHandler, true);
        }

        function toggleSettings() {
            const m = document.getElementById('settingsModal');
            m.classList.toggle('hidden');
            if (!m.classList.contains('hidden')) {
                applySettings(loadSettings());
                buildShortcutEditor();
            }
        }

        document.addEventListener('DOMContentLoaded', () => {
            applySettings(loadSettings());
            requestAnimationFrame(() => requestAnimationFrame(() => {
                const s = document.getElementById('no-transition-init');
                if (s) s.remove();
            }));
            initAuthWall();
            initSSE();
        });

        // ═══════════════════════════════════════════════════
        // AUTH WALL — registro / login modal de primera visita
        // ═══════════════════════════════════════════════════
        const AUTH_COOKIE = 'gs_auth_v1';
        const SESSION_DAYS = 5;
        let _authTab = 'register';

        function getAuthCookie() {
            const m = document.cookie.match(/(?:^|; )gs_auth_v1=([^;]*)/);
            try { return m ? JSON.parse(decodeURIComponent(m[1])) : null; } catch { return null; }
        }
        function setAuthCookie(data) {
            const exp = new Date(Date.now() + SESSION_DAYS * 864e5).toUTCString();
            document.cookie = `gs_auth_v1=${encodeURIComponent(JSON.stringify(data))}; expires=${exp}; path=/; SameSite=Lax`;
        }
        function clearAuthCookie() {
            document.cookie = 'gs_auth_v1=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/';
        }

        function initAuthWall() {
            const modal = document.getElementById('authWallModal');
            if (!modal) return;

            // Check URL params for reason (expired, banned)
            const reason = (window.GS_REASON || new URLSearchParams(window.location.search).get('gs_reason') || '');

            const cookie = getAuthCookie();
            const now    = Date.now();

            if (reason === 'expired') {
                clearAuthCookie();
                showAuthWall('Tu sesión de 5 días ha caducado. Por favor vuelve a identificarte.', 'warning');
                switchAuthTab('login');
                return;
            }
            if (reason === 'banned') {
                clearAuthCookie();
                showAuthWall('Tu acceso ha sido revocado por el administrador.', 'error');
                return;
            }

            if (!cookie || !cookie.username || !cookie.ts) {
                // Primera visita — mostrar registro
                showAuthWall(null, null);
                switchAuthTab('register');
                return;
            }

            // Cookie válida: renovar sesión server-side silenciosamente
            const elapsed = (now - cookie.ts) / 864e5;
            if (elapsed > SESSION_DAYS) {
                clearAuthCookie();
                showAuthWall('Tu sesión ha caducado. Vuelve a entrar.', 'warning');
                switchAuthTab('login');
            }
            // else: cookie válida, no mostrar modal
        }

        function showAuthWall(notice, type) {
            const modal = document.getElementById('authWallModal');
            modal.classList.remove('hidden');
            document.body.style.overflow = 'hidden';
            if (notice) {
                const el = document.getElementById('auth-notice');
                el.className = `mb-4 p-3 rounded-xl border text-xs text-center ${
                    type === 'error'   ? 'bg-red-500/10 border-red-500/20 text-red-400' :
                    type === 'warning' ? 'bg-yellow-500/10 border-yellow-500/20 text-yellow-400' :
                                        'bg-blue-500/10 border-blue-500/20 text-blue-400'
                }`;
                el.textContent = notice;
                el.classList.remove('hidden');
            }
        }
        function hideAuthWall() {
            document.getElementById('authWallModal').classList.add('hidden');
            document.body.style.overflow = '';
        }

        function switchAuthTab(tab) {
            _authTab = tab;
            const isReg = tab === 'register';
            document.getElementById('tab-register').className =
                `flex-1 py-2.5 text-xs font-bold tracking-wider transition-all ${isReg ? 'auth-tab-active' : 'text-gray-600 hover:text-gray-400'}`;
            document.getElementById('tab-login').className =
                `flex-1 py-2.5 text-xs font-bold tracking-wider transition-all ${!isReg ? 'auth-tab-active' : 'text-gray-600 hover:text-gray-400'}`;
            document.getElementById('pw-strength-wrap').style.display = isReg ? '' : 'none';
            document.getElementById('auth-submit-btn').textContent = isReg ? 'REGISTRARSE' : 'ENTRAR';
            document.getElementById('auth-subtitle').textContent = isReg ? 'Crea tu cuenta de alumno' : 'Acceso con cuenta existente';
            document.getElementById('auth-error').classList.add('hidden');
            document.getElementById('auth-success').classList.add('hidden');
        }

        function toggleAuthPw() {
            const inp = document.getElementById('auth-password');
            const eye = document.getElementById('pw-eye');
            inp.type = inp.type === 'password' ? 'text' : 'password';
            eye.className = inp.type === 'password' ? 'fa-solid fa-eye text-xs' : 'fa-solid fa-eye-slash text-xs';
        }

        // Password strength
        document.addEventListener('DOMContentLoaded', () => {
            const pw = document.getElementById('auth-password');
            if (!pw) return;
            pw.addEventListener('input', () => {
                if (_authTab !== 'register') return;
                const v = pw.value;
                const bar = document.getElementById('pw-strength-bar');
                const lbl = document.getElementById('pw-strength-label');
                let score = 0;
                if (v.length >= 6) score++;
                if (v.length >= 10) score++;
                if (/[A-Z]/.test(v)) score++;
                if (/[0-9]/.test(v)) score++;
                if (/[^A-Za-z0-9]/.test(v)) score++;
                const levels = ['','weak','weak','medium','strong','strong'];
                const labels = ['','Muy débil','Débil','Media','Fuerte','Muy fuerte'];
                bar.style.width = (score * 20) + '%';
                bar.className = 'h-full rounded-full transition-all duration-300 ' + (levels[score]||'');
                lbl.textContent = labels[score] || '';
            });
        });

        async function submitAuth(e) {
            e.preventDefault();
            const username = document.getElementById('auth-username').value.trim();
            const password = document.getElementById('auth-password').value;
            const btn = document.getElementById('auth-submit-btn');
            const errEl = document.getElementById('auth-error');
            const okEl  = document.getElementById('auth-success');
            errEl.classList.add('hidden');
            okEl.classList.add('hidden');
            btn.disabled = true;
            btn.textContent = '...';

            const endpoint = _authTab === 'register' ? '/api/register' : '/api/login';
            try {
                const res  = await fetch(endpoint, {
                    method: 'POST',
                    headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({username, password})
                });
                const data = await res.json();
                if (data.ok) {
                    setAuthCookie({username, ts: Date.now()});
                    okEl.textContent = _authTab === 'register'
                        ? `¡Bienvenido, ${username}! Tu cuenta ha sido creada.`
                        : `Bienvenido de nuevo, ${username}.`;
                    okEl.classList.remove('hidden');
                    setTimeout(() => hideAuthWall(), 1200);
                } else {
                    errEl.textContent = data.error || 'Error desconocido';
                    errEl.classList.remove('hidden');
                }
            } catch {
                errEl.textContent = 'Error de conexión';
                errEl.classList.remove('hidden');
            }
            btn.disabled = false;
            btn.textContent = _authTab === 'register' ? 'REGISTRARSE' : 'ENTRAR';
        }

        // ═══════════════════════════════════════════════════
        // SSE — mensajes en tiempo real del admin
        // ═══════════════════════════════════════════════════
        function initSSE() {
            const cookie = getAuthCookie();
            if (!cookie || !cookie.username) return;
            const es = new EventSource(`/api/sse/${encodeURIComponent(cookie.username)}`);
            es.onmessage = ev => {
                const d = JSON.parse(ev.data);
                if (d.type === 'msg') {
                    document.getElementById('sse-toast-text').textContent = d.text;
                    document.getElementById('sse-toast').classList.remove('hidden');
                } else if (d.type === 'kick') {
                    clearAuthCookie();
                    location.href = '/?gs_reason=banned';
                } else if (d.type === 'ping') { /* keepalive */ }
            };
            es.onerror = () => {};
        }

        function initDropdown(containerId, inputId, labelId, arrowId, menuId, itemClass) {
            const container = document.getElementById(containerId);
            if(!container) return;

            const btn = container.querySelector('button');
            const menu = document.getElementById(menuId);
            const input = document.getElementById(inputId);
            const label = document.getElementById(labelId);
            const arrow = document.getElementById(arrowId);
            const items = container.querySelectorAll(itemClass);

            btn.addEventListener('click', (e) => {
                e.preventDefault(); e.stopPropagation();
                toggleMenu();
            });

            function toggleMenu() {
                const isHidden = menu.classList.contains('hidden');
                if(isHidden) {
                    menu.classList.remove('hidden');
                    requestAnimationFrame(() => {
                        menu.animate([{ opacity: 0, transform: 'translateY(-10px)' }, { opacity: 1, transform: 'translateY(0)' }], { duration: 150, easing: 'ease-out' });
                    });
                    arrow.style.transform = 'rotate(180deg)';
                } else { closeMenu(); }
            }

            function closeMenu() {
                menu.classList.add('hidden');
                arrow.style.transform = 'rotate(0deg)';
            }

            document.addEventListener('click', (e) => { if (!container.contains(e.target)) closeMenu(); });

            items.forEach(item => {
                item.addEventListener('click', () => {
                    const val = item.getAttribute('data-value');
                    const text = item.querySelector('span').innerText;
                    input.value = val;
                    label.innerText = text === 'all' ? 'Todas' : text;
                    if(val !== 'all' && val !== '') label.classList.add('text-purple-400');
                    else label.classList.remove('text-purple-400');
                    closeMenu();
                    const event = new Event('change');
                    input.dispatchEvent(event);
                });
            });
        }
    </script>
</body>
</html>
"""

# --- CALENDAR TEMPLATE ---
CALENDAR_TEMPLATE = BASE_HTML_TEMPLATE.replace('{% block content %}{% endblock %}', """
{% block content %}
<div class="animate-enter">

    <div class="flex flex-col md:flex-row items-center justify-between mb-8 gap-4">
        <div class="flex items-center gap-4">
            <div class="w-12 h-12 rounded-xl bg-yellow-500/10 border border-yellow-500/20 flex items-center justify-center shadow-[0_0_15px_rgba(234,179,8,0.2)]">
                <i class="fa-solid fa-calendar-days text-yellow-400 text-xl"></i>
            </div>
            <div>
                <h2 class="text-3xl font-bold text-white tracking-wide uppercase">{{ month_name }} <span class="text-gray-500">{{ year }}</span></h2>
                <div class="flex items-center gap-3 text-xs font-mono text-gray-400 mt-1">
                    <a id="btn-prev-month" href="{{ url_for('calendar_view', year=prev_year, month=prev_month) }}" class="hover:text-white transition flex items-center gap-1"><i class="fa-solid fa-chevron-left"></i> PREV</a>
                    <span class="w-[1px] h-3 bg-gray-700"></span>
                    <a id="btn-next-month" href="{{ url_for('calendar_view', year=next_year, month=next_month) }}" class="hover:text-white transition flex items-center gap-1">NEXT <i class="fa-solid fa-chevron-right"></i></a>
                    <span class="w-[1px] h-3 bg-gray-700"></span>
                    <a href="{{ url_for('calendar_view') }}" class="text-yellow-400 hover:text-yellow-300">HOY</a>
                </div>
            </div>
        </div>

        <button onclick="openAddModal()" class="group px-6 py-3 rounded-xl bg-gradient-to-r from-purple-600 to-blue-600 text-white font-bold text-sm shadow-lg hover:shadow-purple-500/40 hover:scale-105 transition-all flex items-center gap-2">
            <i class="fa-solid fa-plus group-hover:rotate-90 transition-transform"></i> AÑADIR EVENTO <span class="bg-black/20 text-[0.6rem] px-1.5 py-0.5 rounded border border-white/10 ml-2 group-hover:bg-white/10 transition">N</span>
        </button>
    </div>

    <div class="glass-panel rounded-2xl overflow-hidden border border-white/10 shadow-2xl">
        <div class="grid grid-cols-7 bg-black/20 border-b border-white/5 text-center py-3">
            {% for day in ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'] %}
            <div class="text-xs font-bold text-gray-500 uppercase tracking-wider">{{ day }}</div>
            {% endfor %}
        </div>
        <div class="grid grid-cols-7 auto-rows-fr bg-black/10">
            {% for week in month_days %}
                {% for day, events in week %}
                    <div class="calendar-cell p-2 border-r border-b border-white/5 relative group {% if day == 0 %}bg-black/20{% endif %}">
                        {% if day != 0 %}
                            <span class="absolute top-2 right-2 text-xs font-mono {% if day == current_day and month == current_month and year == current_year %}text-yellow-400 font-bold bg-yellow-400/10 px-1.5 rounded{% else %}text-gray-600 group-hover:text-gray-400{% endif %}">{{ day }}</span>
                            <div class="mt-6 flex flex-col gap-1">
                                {% for event in events %}
                                    <div onclick='openViewModal({{ event | tojson }})' class="event-pill type-{{ event.type }} truncate shadow-sm hover:shadow-md animate-enter">
                                        {% if event.type == 'examen' %}<i class="fa-solid fa-triangle-exclamation mr-1"></i>
                                        {% elif event.type == 'tarea' %}<i class="fa-solid fa-list-check mr-1"></i>
                                        {% else %}<i class="fa-solid fa-sticky-note mr-1"></i>{% endif %}
                                        {{ event.title }}
                                    </div>
                                {% endfor %}
                            </div>
                        {% endif %}
                    </div>
                {% endfor %}
            {% endfor %}
        </div>
    </div>
</div>

<div id="addModal" class="hidden fixed inset-0 z-[100] bg-black/80 backdrop-blur-sm flex items-center justify-center p-4 animate-enter">
    <div class="glass-panel w-full max-w-md p-0 rounded-2xl border border-white/10 shadow-[0_0_50px_rgba(0,0,0,0.6)] overflow-hidden">
        <div class="bg-gradient-to-r from-gray-900 to-gray-800 p-4 border-b border-white/5 flex justify-between items-center">
            <h3 class="text-white font-bold flex items-center gap-2"><i class="fa-solid fa-layer-group text-purple-400"></i> NUEVA ENTRADA</h3>
            <button onclick="closeAddModal()" class="text-gray-500 hover:text-white"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <form method="POST" action="{{ url_for('add_event') }}" class="p-6 space-y-4">
            <div class="grid grid-cols-3 gap-2 mb-4">
                <label class="cursor-pointer">
                    <input type="radio" name="type" value="examen" class="peer sr-only" checked onchange="toggleSubject(true)">
                    <div class="text-center py-2 rounded-lg border border-transparent bg-white/5 text-gray-400 hover:bg-white/10 peer-checked:bg-red-500/20 peer-checked:border-red-500 peer-checked:text-red-400 transition-all text-xs font-bold uppercase">Examen</div>
                </label>
                <label class="cursor-pointer">
                    <input type="radio" name="type" value="tarea" class="peer sr-only" onchange="toggleSubject(true)">
                    <div class="text-center py-2 rounded-lg border border-transparent bg-white/5 text-gray-400 hover:bg-white/10 peer-checked:bg-yellow-500/20 peer-checked:border-yellow-500 peer-checked:text-yellow-400 transition-all text-xs font-bold uppercase">Tarea</div>
                </label>
                <label class="cursor-pointer">
                    <input type="radio" name="type" value="nota" class="peer sr-only" onchange="toggleSubject(false)">
                    <div class="text-center py-2 rounded-lg border border-transparent bg-white/5 text-gray-400 hover:bg-white/10 peer-checked:bg-blue-500/20 peer-checked:border-blue-500 peer-checked:text-blue-400 transition-all text-xs font-bold uppercase">Nota</div>
                </label>
            </div>
            <div><label class="block text-xs font-bold text-gray-500 mb-1 uppercase">Título</label><input type="text" name="title" required class="w-full px-3 py-2 rounded-lg input-liquid text-sm" placeholder="Ej. Examen Tema 5"></div>
            <div><label class="block text-xs font-bold text-gray-500 mb-1 uppercase">Fecha</label><input type="date" name="date" required class="w-full px-3 py-2 rounded-lg input-liquid text-sm text-gray-300"></div>
            <div id="subjectField"><label class="block text-xs font-bold text-gray-500 mb-1 uppercase">Asignatura</label><input type="text" name="subject" class="w-full px-3 py-2 rounded-lg input-liquid text-sm" placeholder="Ej. Matemáticas"></div>
            <div><label class="block text-xs font-bold text-gray-500 mb-1 uppercase">Descripción</label><textarea name="description" rows="3" class="w-full px-3 py-2 rounded-lg input-liquid text-sm resize-none" placeholder="Detalles adicionales..."></textarea></div>
            <div class="flex gap-3 pt-2">
                <button type="button" onclick="closeAddModal()" class="flex-1 py-2.5 rounded-lg border border-white/10 text-gray-400 hover:bg-white/10 hover:text-white transition text-xs font-bold">CANCELAR <span class="opacity-50 ml-1">ESC</span></button>
                <button type="submit" class="flex-1 py-2.5 rounded-lg bg-white/10 hover:bg-white/20 text-white border border-white/20 transition text-xs font-bold shadow-lg">AÑADIR <span class="opacity-50 ml-1">CTRL+ENTER</span></button>
            </div>
        </form>
    </div>
</div>

<div id="viewModal" class="hidden fixed inset-0 z-[100] bg-black/80 backdrop-blur-sm flex items-center justify-center p-4 animate-enter">
    <div class="glass-panel w-full max-w-sm p-6 rounded-2xl border-t-4 shadow-2xl relative" id="viewModalCard">
        <button onclick="document.getElementById('viewModal').classList.add('hidden')" class="absolute top-4 right-4 text-gray-500 hover:text-white"><i class="fa-solid fa-xmark"></i></button>
        <div class="mb-4"><span id="viewTypeBadge" class="px-2 py-1 rounded text-[0.6rem] font-bold uppercase tracking-widest border">TIPO</span></div>
        <h3 id="viewTitle" class="text-2xl font-bold text-white mb-1 leading-tight">Título</h3>
        <p id="viewDate" class="text-xs font-mono text-gray-400 mb-4 flex items-center gap-2"><i class="fa-regular fa-clock"></i> <span>Fecha</span></p>
        <div id="viewSubjectContainer" class="mb-4 p-3 bg-white/5 rounded-lg border border-white/5">
            <span class="text-[0.65rem] text-gray-500 uppercase block mb-1">Asignatura</span>
            <span id="viewSubject" class="text-sm font-bold text-purple-300">Mates</span>
        </div>
        <div class="mb-6"><span class="text-[0.65rem] text-gray-500 uppercase block mb-1">Descripción</span><p id="viewDesc" class="text-sm text-gray-300 leading-relaxed bg-black/20 p-3 rounded-lg border border-white/5 min-h-[60px]">Desc</p></div>
        <a id="deleteBtn" href="#" onclick="return confirm('¿Borrar este evento?')" class="block w-full text-center py-2 rounded bg-red-500/10 text-red-400 text-xs font-bold border border-red-500/20 hover:bg-red-500 hover:text-white transition">BORRAR ENTRADA</a>
    </div>
</div>

<script>
    function toggleSubject(show) {
        const field = document.getElementById('subjectField');
        if(show) { field.classList.remove('hidden'); field.querySelector('input').required = true; }
        else { field.classList.add('hidden'); field.querySelector('input').required = false; }
    }
    function openAddModal() {
        document.getElementById('addModal').classList.remove('hidden');
        document.querySelector('#addModal input[name="title"]').focus();
    }
    function closeAddModal() { document.getElementById('addModal').classList.add('hidden'); }
    function openViewModal(event) {
        const m = document.getElementById('viewModal');
        const card = document.getElementById('viewModalCard');
        const badge = document.getElementById('viewTypeBadge');
        document.getElementById('viewTitle').innerText = event.title;
        document.getElementById('viewDate').querySelector('span').innerText = event.date;
        document.getElementById('viewDesc').innerText = event.description || 'Sin descripción';
        document.getElementById('deleteBtn').href = "/delete_event/" + event.id;
        const subCont = document.getElementById('viewSubjectContainer');
        card.classList.remove('border-t-red-500', 'border-t-yellow-500', 'border-t-blue-500');
        badge.className = 'px-2 py-1 rounded text-[0.6rem] font-bold uppercase tracking-widest border';

        if(event.type === 'examen') {
            card.classList.add('border-t-red-500'); badge.classList.add('bg-red-500/10', 'border-red-500/30', 'text-red-400');
            badge.innerText = 'EXAMEN'; subCont.style.display = 'block'; document.getElementById('viewSubject').innerText = event.subject;
        } else if(event.type === 'tarea') {
            card.classList.add('border-t-yellow-500'); badge.classList.add('bg-yellow-500/10', 'border-yellow-500/30', 'text-yellow-400');
            badge.innerText = 'TAREA'; subCont.style.display = 'block'; document.getElementById('viewSubject').innerText = event.subject;
        } else {
            card.classList.add('border-t-blue-500'); badge.classList.add('bg-blue-500/10', 'border-blue-500/30', 'text-blue-400');
            badge.innerText = 'NOTA'; subCont.style.display = 'none';
        }
        m.classList.remove('hidden');
    }
</script>
{% endblock %}
""")

# --- AGENDA TEMPLATE (MODIFICADO: Indigo + Line Clamp + Modal Lectura) ---
AGENDA_TEMPLATE = BASE_HTML_TEMPLATE.replace('{% block content %}{% endblock %}', """
{% block content %}
<div class="max-w-4xl mx-auto animate-enter">
    <div class="flex items-center gap-3 mb-8">
        <div class="w-10 h-10 bg-indigo-500 rounded-lg flex items-center justify-center shadow-[0_0_15px_rgba(99,102,241,0.5)]">
            <i class="fa-solid fa-book-bookmark text-white"></i>
        </div>
        <h2 class="text-2xl font-bold text-white tracking-wide">AGENDA PERSONAL</h2>
    </div>

    <div class="glass-panel p-6 rounded-2xl border border-white/10 mb-12 shadow-xl">
        <form method="POST" action="{{ url_for('add_note') }}" class="space-y-4">
            <input type="text" name="title" class="w-full bg-transparent border-b border-white/10 text-xl font-bold text-white placeholder-gray-600 focus:outline-none focus:border-indigo-500 py-2 transition-colors" placeholder="Título de la nota..." required>
            <textarea name="content" class="w-full bg-black/20 rounded-lg p-4 text-sm text-gray-300 placeholder-gray-600 focus:outline-none focus:ring-1 focus:ring-indigo-500/50 resize-y min-h-[150px] custom-scrollbar" placeholder="Escribe aquí tus ideas, recordatorios o tareas..."></textarea>
            <div class="flex justify-end">
                <button type="submit" class="px-6 py-2 rounded-lg bg-indigo-500/20 border border-indigo-500/30 text-indigo-300 font-bold text-xs hover:bg-indigo-500 hover:text-white transition-all shadow-lg hover:shadow-indigo-500/30">
                    GUARDAR NOTA <i class="fa-solid fa-floppy-disk ml-2"></i>
                </button>
            </div>
        </form>
    </div>

    <div class="mb-4 flex items-center gap-2 text-xs font-mono text-gray-500 uppercase tracking-widest">
        <i class="fa-solid fa-box-archive"></i> Notas Guardadas ({{ notes|length }})
    </div>

    {% if notes %}
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            {% for note in notes %}
            <div onclick='openNoteModal({{ note | tojson }})' class="card-dynamic glass-panel p-5 rounded-xl border-l-4 border-l-indigo-500/50 relative group cursor-pointer hover:bg-white/5 transition-all h-40 flex flex-col">
                <div class="flex justify-between items-start mb-2">
                    <h3 class="font-bold text-white text-lg leading-tight truncate pr-4">{{ note.title }}</h3>
                    <div class="text-[0.6rem] font-mono text-indigo-400 whitespace-nowrap">{{ note.date.split(' ')[0] }}</div>
                </div>
                <div class="text-sm text-gray-400 leading-relaxed overflow-hidden line-clamp-3 flex-1 relative">
                    {{ note.content }}
                    <div class="absolute bottom-0 left-0 w-full h-4 bg-gradient-to-t from-[#13141c] to-transparent"></div>
                </div>
                <div class="mt-2 text-[0.6rem] text-indigo-500 font-bold uppercase tracking-wider opacity-0 group-hover:opacity-100 transition-opacity flex justify-between items-center">
                    <span>LEER MÁS <i class="fa-solid fa-arrow-up-right-from-square ml-1"></i></span>
                </div>
            </div>
            {% endfor %}
        </div>
    {% else %}
        <div class="text-center py-12 border-2 border-dashed border-white/5 rounded-xl text-gray-600">
            <i class="fa-solid fa-feather text-2xl mb-2 opacity-50"></i>
            <p class="text-sm">Tu agenda está vacía.</p>
        </div>
    {% endif %}
</div>

<div id="noteModal" class="hidden fixed inset-0 z-[100] bg-black/90 backdrop-blur-md flex items-center justify-center p-4 animate-enter">
    <div class="glass-panel w-full max-w-2xl p-0 rounded-2xl border border-indigo-500/30 shadow-[0_0_50px_rgba(99,102,241,0.2)] flex flex-col max-h-[80vh]">
        <div class="p-6 border-b border-white/10 flex justify-between items-start bg-indigo-500/5">
            <div>
                <h3 id="noteModalTitle" class="text-2xl font-bold text-white leading-tight">Título de la Nota</h3>
                <span id="noteModalDate" class="text-xs font-mono text-indigo-300 mt-1 block">Fecha</span>
            </div>
            <button onclick="document.getElementById('noteModal').classList.add('hidden')" class="text-gray-500 hover:text-white transition p-2">
                <i class="fa-solid fa-xmark text-xl"></i>
            </button>
        </div>

        <div class="p-8 overflow-y-auto custom-scrollbar flex-1">
            <p id="noteModalContent" class="text-gray-300 leading-relaxed whitespace-pre-wrap text-sm md:text-base">Contenido...</p>
        </div>

        <div class="p-4 border-t border-white/10 flex justify-end gap-3 bg-black/20">
            <button onclick="document.getElementById('noteModal').classList.add('hidden')" class="px-4 py-2 rounded text-xs font-bold text-gray-400 hover:text-white transition">CERRAR</button>
            <a id="noteModalDelete" href="#" onclick="return confirm('¿Borrar esta nota definitivamente?')" class="px-4 py-2 rounded bg-red-500/10 border border-red-500/20 text-red-400 text-xs font-bold hover:bg-red-500 hover:text-white transition">ELIMINAR</a>
        </div>
    </div>
</div>

<script>
    function openNoteModal(note) {
        document.getElementById('noteModalTitle').innerText = note.title;
        document.getElementById('noteModalDate').innerText = note.date;
        document.getElementById('noteModalContent').innerText = note.content;
        document.getElementById('noteModalDelete').href = "/delete_note/" + note.id;
        document.getElementById('noteModal').classList.remove('hidden');
    }
</script>
{% endblock %}
""")

# --- HORARIO TEMPLATE ---
HORARIO_TEMPLATE = BASE_HTML_TEMPLATE.replace('{% block content %}{% endblock %}', """
{% block content %}
<div class="animate-enter">

    <div class="flex items-center gap-4 mb-8">
        <div class="w-12 h-12 rounded-xl bg-orange-500/10 border border-orange-500/20 flex items-center justify-center shadow-[0_0_15px_rgba(251,146,60,0.2)]">
            <i class="fa-solid fa-table-cells text-orange-400 text-xl"></i>
        </div>
        <div>
            <h2 class="text-3xl font-bold text-white tracking-wide uppercase">Horario</h2>
            <p class="text-xs font-mono text-gray-500 mt-1">4ºE — SEMANA LECTIVA</p>
        </div>
    </div>

    <div class="glass-panel rounded-2xl overflow-hidden border border-white/10 shadow-2xl">
        <!-- Header días -->
        <div class="grid grid-cols-6 bg-black/30 border-b border-white/10">
            <div class="py-4 px-4 text-xs font-bold text-gray-600 uppercase tracking-widest flex items-center justify-center">
                <i class="fa-solid fa-clock text-orange-500/50 mr-2"></i>Hora
            </div>
            {% for dia in ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes'] %}
            <div class="py-4 px-2 text-center text-xs font-bold text-gray-300 uppercase tracking-widest border-l border-white/5">{{ dia }}</div>
            {% endfor %}
        </div>

        {% set franjas = [
            ('8:15–09:10',   [('H,G','hg'), ('Religión','religion'), ('Lengua','lengua'), ('Mates','mates'), ('Lengua','lengua')]),
            ('9:10–10:05',  [('Optativa','optativa'), ('Mates','mates'), ('Biología','bio'), ('H,G','hg'), ('Proyecto','proyecto')]),
            ('10:05–11:00', [('Biología','bio'), ('Inglés','ingles'), ('H,G','hg'), ('E,F','ef'), ('Optativa','optativa')]),
            ('11:00–11:40', [('Recreo','recreo'), ('Recreo','recreo'), ('Recreo','recreo'), ('Recreo','recreo'), ('Recreo','recreo')]),
            ('11:40–12:25', [('Inglés','ingles'), ('Lengua','lengua'), ('Inglés','ingles'), ('Inglés','ingles'), ('F,Q','fq')]),
            ('12:25–13:20', [('Lengua','lengua'), ('F,Q','fq'), ('Mates','mates'), ('Optativa','optativa'), ('Tutoría','tutoria')]),
            ('13:20–14:15', [('F,Q','fq'), ('Biología','bio'), ('E,F','ef'), ('Proyecto','proyecto'), ('Mates','mates')])
        ] %}

        {% set colors = {
            'hg':       'text-orange-300 bg-orange-500/10 border-orange-500/30',
            'religion': 'text-purple-300 bg-purple-500/10 border-purple-500/30',
            'lengua':   'text-green-300 bg-green-500/10 border-green-500/30',
            'mates':    'text-cyan-300 bg-cyan-500/10 border-cyan-500/30',
            'bio':      'text-emerald-300 bg-emerald-500/10 border-emerald-500/30',
            'ingles':   'text-yellow-300 bg-yellow-500/10 border-yellow-500/30',
            'ef':       'text-blue-300 bg-blue-500/10 border-blue-500/30',
            'fq':       'text-pink-300 bg-pink-500/10 border-pink-500/30',
            'optativa': 'text-violet-300 bg-violet-500/10 border-violet-500/30',
            'proyecto': 'text-teal-300 bg-teal-500/10 border-teal-500/30',
            'tutoria':  'text-red-300 bg-red-500/10 border-red-500/30',
            'recreo':   'text-gray-400 bg-white/5 border-white/10'
        } %}

        {% set icons = {
            'hg':       'fa-solid fa-landmark',
            'religion': 'fa-solid fa-dove',
            'lengua':   'fa-solid fa-book-open',
            'mates':    'fa-solid fa-calculator',
            'bio':      'fa-solid fa-dna',
            'ingles':   'fa-solid fa-globe',
            'ef':       'fa-solid fa-person-running',
            'fq':       'fa-solid fa-flask',
            'optativa': 'fa-solid fa-star',
            'proyecto': 'fa-solid fa-lightbulb',
            'tutoria':  'fa-solid fa-chalkboard-user',
            'recreo':   'fa-solid fa-mug-hot'
        } %}

        {% for hora, asignaturas in franjas %}
        <div class="grid grid-cols-6 border-b border-white/5 {% if loop.last %}border-b-0{% endif %} {% if 'Recreo' in asignaturas[0][0] %}bg-black/20{% else %}hover:bg-white/[0.015] transition-colors{% endif %}">
            <div class="px-4 py-3 flex items-center justify-center border-r border-white/5">
                <span class="text-[0.65rem] font-mono text-gray-500 text-center leading-tight whitespace-nowrap">{{ hora }}</span>
            </div>
            {% for nombre, key in asignaturas %}
            <div class="px-2 py-2 border-l border-white/5 flex items-center justify-center">
                {% if nombre == 'Recreo' %}
                <div class="w-full text-center py-1 text-[0.65rem] font-bold uppercase tracking-widest text-gray-600 flex items-center justify-center gap-1">
                    <i class="fa-solid fa-mug-hot text-gray-700"></i> Recreo
                </div>
                {% else %}
                <div class="w-full px-2 py-2 rounded-lg border {{ colors[key] }} text-center transition-all hover:scale-105 hover:shadow-lg cursor-default">
                    <i class="{{ icons[key] }} text-[0.6rem] block mb-1 opacity-70"></i>
                    <span class="text-[0.7rem] font-bold leading-tight">{{ nombre }}</span>
                </div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
        {% endfor %}
    </div>

    <div class="mt-6 glass-panel rounded-xl p-4 border border-white/10">
        <p class="text-[0.65rem] font-bold text-gray-600 uppercase tracking-widest mb-3">Leyenda</p>
        <div class="flex flex-wrap gap-2">
            {% set leyenda = [
                ('H,G', 'hg'), ('Religión', 'religion'), ('Lengua', 'lengua'), ('Mates', 'mates'),
                ('Biología', 'bio'), ('Inglés', 'ingles'), ('E,F', 'ef'), ('F,Q', 'fq'),
                ('Optativa', 'optativa'), ('Proyecto', 'proyecto'), ('Tutoría', 'tutoria')
            ] %}
            {% for nombre, key in leyenda %}
            <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full border text-[0.65rem] font-bold {{ colors[key] }}">
                <i class="{{ icons[key] }} text-[0.6rem]"></i> {{ nombre }}
            </span>
            {% endfor %}
        </div>
    </div>

</div>
{% endblock %}
""")


# --- INDEX TEMPLATE ---
INDEX_TEMPLATE = BASE_HTML_TEMPLATE.replace('{% block content %}{% endblock %}', """
{% block content %}
    <div class="flex items-center justify-between mb-8 animate-enter">
        <div class="flex items-center gap-3">
            <div class="w-1.5 h-8 bg-gradient-to-b from-cyan-400 to-blue-600 rounded-full shadow-[0_0_15px_rgba(34,211,238,0.5)]"></div>
            <h2 class="text-2xl font-bold text-white tracking-wide">MÓDULOS PÚBLICOS</h2>
        </div>
        <div class="hidden lg:block text-xs text-gray-600 font-mono">SEARCH_MODULE_V1.0 <span class="text-[0.6rem] bg-white/5 border border-white/10 px-1 rounded ml-2">PRESS /</span></div>
    </div>

    <div class="relative z-30 mb-12 animate-enter delay-100">
        <div class="glass-panel rounded-2xl p-1 flex items-center transition-all focus-within:ring-2 focus-within:ring-purple-500/30 focus-within:border-purple-500/50 hover:shadow-[0_0_20px_rgba(139,92,246,0.1)]">
            <div class="pl-6 pr-4 text-gray-500"><i class="fa-solid fa-magnifying-glass"></i></div>
            <input type="text" id="searchInput" class="w-full bg-transparent border-none text-white placeholder-gray-500 py-4 focus:ring-0 focus:outline-none text-base font-medium" placeholder="Buscar módulo (ej. Matemáticas)...">
            <div class="h-8 w-[1px] bg-white/10 mx-2"></div>

            <div class="relative min-w-[200px]" id="filterDropdownContainer">
                <button type="button" class="w-full flex items-center justify-between px-6 py-3 text-sm font-bold text-gray-300 hover:text-white transition-colors outline-none rounded-xl hover:bg-white/5">
                    <span id="filterLabel">Asignatura</span>
                    <i id="filterArrow" class="fa-solid fa-chevron-down text-xs text-purple-400 transition-transform duration-300"></i>
                </button>
                <input type="hidden" id="subjectFilter" value="all">
                <div id="filterMenu" class="hidden absolute top-full right-0 mt-3 w-64 glass-panel rounded-xl overflow-hidden shadow-2xl z-50">
                    <ul class="py-2 max-h-60 overflow-y-auto custom-scrollbar">
                        <li><button type="button" class="filter-item w-full text-left px-5 py-3 text-sm text-gray-400 hover:text-white hover:bg-purple-500/20 transition-all" data-value="all"><span>Todas</span></button></li>
                        <div class="h-[1px] bg-white/5 my-1"></div>
                        {% for subject in subject_icons.keys() %}
                        <li><button type="button" class="filter-item w-full text-left px-5 py-3 text-sm text-gray-300 hover:text-white hover:bg-purple-500/20 transition-all flex items-center gap-3" data-value="{{ subject }}">
                            <span class="w-1 h-4 bg-purple-500 rounded-full opacity-50 shadow-[0_0_8px_rgba(168,85,247,0.5)]"></span>
                            <span>{{ subject }}</span>
                        </button></li>
                        {% endfor %}
                    </ul>
                </div>
            </div>
        </div>
    </div>

    {% set public_pages = pages | selectattr('is_private', 'false') | list %}
    <div id="modulesGrid" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6 animate-enter delay-200">
    {% if public_pages %}
        {% for page in public_pages %}
        <article class="module-card card-dynamic fast-glass rounded-xl overflow-hidden group h-full relative border border-white/5 hover:border-white/50"
                 data-title="{{ page.title|lower }}"
                 data-subject="{{ page.subject }}">

            <div class="absolute inset-0 {{ page.color }} blur-3xl opacity-0 group-hover:opacity-40 transition-opacity duration-300 pointer-events-none"></div>
            <a href="{{ url_for('show_page', page_slug=page.slug) }}" class="relative z-10 block h-full flex flex-col">
                <div class="p-6 flex items-start gap-4 {{ page.color }} bg-opacity-10 group-hover:bg-opacity-50 transition-all border-b border-white/5">
                    <div class="bg-black/50 w-12 h-12 rounded-xl flex items-center justify-center border border-white/10 group-hover:scale-110 transition-transform">
                        <i class="{{ page.icon }} text-xl text-white"></i>
                    </div>
                    <div>
                        <h3 class="text-lg font-bold text-white mb-1">{{ page.title }}</h3>
                        <span class="text-[0.65rem] uppercase px-2 py-0.5 rounded border border-white/10 bg-black/40">{{ page.subject }}</span>
                    </div>
                </div>
                <div class="p-4 mt-auto flex justify-between items-center bg-[#0a0b10]/40">
                    <span class="text-xs font-mono text-green-400 flex items-center gap-1.5"><span class="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse"></span> ONLINE</span>
                    <i class="fa-solid fa-arrow-right-long text-gray-500 group-hover:text-cyan-400 transition-colors"></i>
                </div>
            </a>
        </article>
        {% endfor %}
    {% endif %}
    </div>

    <div id="noResults" class="hidden animate-enter mt-8 fast-glass rounded-2xl p-12 text-center border-dashed border-gray-700">
        <h3 class="text-xl font-bold text-white mb-2">Nada por aquí...</h3>
        <p class="text-gray-500 text-sm">No encontramos módulos que coincidan con tu búsqueda.</p>
    </div>

    <script>
        document.addEventListener('DOMContentLoaded', () => {
            initDropdown('filterDropdownContainer', 'subjectFilter', 'filterLabel', 'filterArrow', 'filterMenu', '.filter-item');

            const searchInput = document.getElementById('searchInput');
            const subjectInput = document.getElementById('subjectFilter');
            const cards = document.querySelectorAll('.module-card');
            const noResults = document.getElementById('noResults');

            function filter() {
                const term = searchInput.value.toLowerCase();
                const subj = subjectInput.value;
                let visible = 0;

                cards.forEach(card => {
                    const t = card.getAttribute('data-title');
                    const s = card.getAttribute('data-subject');
                    const matchT = t.includes(term);
                    const matchS = subj === 'all' || s === subj;

                    if(matchT && matchS) {
                        card.style.display = ''; visible++;
                    } else {
                        card.style.display = 'none';
                    }
                });
                noResults.classList.toggle('hidden', visible > 0);
            }

            searchInput.addEventListener('input', filter);
            subjectInput.addEventListener('change', filter);
        });
    </script>
{% endblock %}
""")

# --- RUTAS PRINCIPALES ---
@app.route('/')
def index():
    pages = load_pages()
    for p in pages:
        if 'is_private' not in p: p['is_private'] = False
    gs_reason = request.args.get('gs_reason','')
    return render_cached(INDEX_TEMPLATE, title='Inicio', pages=pages, url_for=url_for, session=session, subject_icons=SUBJECT_ICONS, gs_reason=gs_reason)

@app.route('/horario')
def horario():
    return render_cached(HORARIO_TEMPLATE, title='Horario', url_for=url_for, session=session, gs_reason='')


@app.route('/calendar')
def calendar_view():
    today = datetime.date.today()
    try: year = int(request.args.get('year', today.year)); month = int(request.args.get('month', today.month))
    except ValueError: year, month = today.year, today.month

    prev_date = datetime.date(year, month, 1) - datetime.timedelta(days=1)
    next_date = datetime.date(year, month, 28) + datetime.timedelta(days=7); next_date = next_date.replace(day=1)
    month_names = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

    cal = calendar.Calendar(firstweekday=0)
    raw_cal = cal.monthdayscalendar(year, month)
    all_events = load_events()
    month_data = []

    for week in raw_cal:
        week_data = []
        for day in week:
            day_events = []
            if day != 0:
                date_str = f"{year}-{month:02d}-{day:02d}"
                day_events = [e for e in all_events if e['date'] == date_str]
            week_data.append((day, day_events))
        month_data.append(week_data)

    return render_cached(CALENDAR_TEMPLATE,
        title='Calendario', year=year, month=month, month_name=month_names[month],
        prev_year=prev_date.year, prev_month=prev_date.month, next_year=next_date.year, next_month=next_date.month,
        month_days=month_data, current_day=today.day, current_month=today.month, current_year=today.year,
        url_for=url_for, session=session)

@app.route('/add_event', methods=['POST'])
def add_event():
    import uuid
    events = load_events()
    new_event = {
        'id': str(uuid.uuid4()), 'type': request.form.get('type'), 'title': request.form.get('title'),
        'date': request.form.get('date'), 'subject': request.form.get('subject', ''), 'description': request.form.get('description', '')
    }
    if new_event['type'] == 'nota': new_event['subject'] = ''
    events.append(new_event)
    save_events(events)
    y, m, d = map(int, new_event['date'].split('-'))
    return redirect(url_for('calendar_view', year=y, month=m))

@app.route('/delete_event/<event_id>')
def delete_event(event_id):
    events = load_events()
    events = [e for e in events if e['id'] != event_id]
    save_events(events)
    return redirect(url_for('calendar_view'))

# --- RUTAS DE AGENDA ---
@app.route('/agenda')
def agenda():
    notes = load_agenda()
    notes.reverse()
    return render_cached(AGENDA_TEMPLATE, title='Agenda', notes=notes, url_for=url_for, session=session, gs_reason='')

@app.route('/add_note', methods=['POST'])
def add_note():
    notes = load_agenda()
    new_note = {
        'id': str(uuid.uuid4()),
        'title': request.form.get('title'),
        'content': request.form.get('content'),
        'date': datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    }
    notes.append(new_note)
    save_agenda(notes)
    return redirect(url_for('agenda'))

@app.route('/delete_note/<note_id>')
def delete_note(note_id):
    notes = load_agenda()
    notes = [n for n in notes if n['id'] != note_id]
    save_agenda(notes)
    return redirect(url_for('agenda'))

PAGE_DETAIL_TEMPLATE = BASE_HTML_TEMPLATE.replace('{% block content %}{% endblock %}', """
{% block content %}
<div class="max-w-5xl mx-auto animate-enter">
    <div class="flex items-center justify-between mb-8">
        <div class="flex items-center gap-5">
            <div class="w-16 h-16 rounded-2xl {{ page.color }} shadow-[0_0_30px_rgba(0,0,0,0.3)] flex items-center justify-center border border-white/20">
                <i class="{{ page.icon }} text-2xl drop-shadow-md"></i>
            </div>
            <div>
                <h2 class="text-3xl font-bold text-white mb-2 tracking-tight">{{ page.title }}</h2>
                <div class="flex items-center gap-2">
                    <span class="px-3 py-1 rounded-md bg-white/5 border border-white/10 text-xs font-mono text-cyan-400">{{ page.subject }}</span>
                    {% if page.is_private %}
                    <span class="px-3 py-1 rounded-md bg-pink-500/10 border border-pink-500/20 text-xs font-mono text-pink-400 flex items-center gap-1"><i class="fa-solid fa-lock text-[0.6rem]"></i> ENCRYPTED</span>
                    {% endif %}
                </div>
            </div>
        </div>
        <a href="{{ url_for('private_zone' if page.is_private else 'index') }}" class="group flex items-center gap-2 px-5 py-2.5 rounded-lg border border-white/10 hover:bg-white/5 text-sm text-gray-400 hover:text-white transition-all"><i class="fa-solid fa-arrow-left group-hover:-translate-x-1 transition-transform"></i> VOLVER <span class="ml-2 text-[0.6rem] bg-white/5 px-1 rounded opacity-50">ESC</span></a>
    </div>
    <div class="glass-panel p-2 rounded-xl shadow-2xl animate-enter delay-100 border border-white/10">
        <div class="bg-black rounded-lg overflow-hidden relative min-h-[500px]">
            {{ page.embed_code|safe }}
        </div>
    </div>
</div>
{% endblock %}
""")

@app.route('/page/<page_slug>')
def show_page(page_slug):
    pages = load_pages()
    page = next((p for p in pages if p['slug'] == page_slug), None)
    if not page: return "404 Not Found", 404
    if page.get('is_private'):
        current_user = session.get('private_user')
        is_admin = session.get('logged_in')
        allowed_users = page.get('allowed_users', [])
        if not is_admin:
            if not current_user:
                return redirect(url_for('index'))
            u = get_user(current_user)
            if not u or u.get('banned'):
                session.clear(); return redirect(url_for('index'))
            # 'all' means all registered users, else check whitelist
            if allowed_users and 'all' not in allowed_users and current_user not in allowed_users:
                return redirect(url_for('private_zone'))
    return render_cached(PAGE_DETAIL_TEMPLATE, title=page['title'], page=page, url_for=url_for, session=session, gs_reason='')

LOGIN_TEMPLATE = BASE_HTML_TEMPLATE.replace('{% block content %}{% endblock %}', """
{% block content %}
<div class="max-w-sm mx-auto mt-16 animate-enter">
    <div class="glass-panel p-8 rounded-2xl border-t-4 border-t-purple-500 relative overflow-hidden shadow-[0_0_50px_rgba(0,0,0,0.5)]">
        <div class="absolute top-0 right-0 w-40 h-40 bg-purple-500/20 blur-[60px] rounded-full pointer-events-none"></div>
        <div class="text-center mb-8 relative z-10">
            <h2 class="text-2xl font-bold text-white tracking-wide">SYSTEM ROOT</h2>
            <p class="text-xs text-gray-500 mt-1 font-mono uppercase">Authentication Required</p>
        </div>
        {% if error %}<div class="mb-6 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-xs text-center"><i class="fa-solid fa-circle-xmark"></i> {{ error }}</div>{% endif %}
        <form method="POST" class="relative z-10 space-y-5">
            <div><label class="block text-xs font-bold text-gray-500 mb-2 uppercase">Usuario</label><input type="text" name="username" class="w-full px-4 py-3 rounded-lg input-liquid text-sm" required autofocus></div>
            <div><label class="block text-xs font-bold text-gray-500 mb-2 uppercase">Password</label><input type="password" name="password" class="w-full px-4 py-3 rounded-lg input-liquid text-sm" required></div>
            <button type="submit" class="w-full btn-glow text-white font-bold py-3.5 rounded-lg shadow-lg mt-2 tracking-wider">INICIAR SESIÓN</button>
            <a href="{{ url_for('index') }}" class="block text-center text-xs text-gray-600 hover:text-white mt-4">CANCELAR</a>
        </form>
    </div>
</div>
{% endblock %}
""")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('username') == ADMIN_USER and check_password_hash(ADMIN_PASS_HASH, request.form.get('password')):
            session.clear(); session['logged_in'] = True; session['username'] = ADMIN_USER
            return redirect(url_for('admin'))
        return render_cached(LOGIN_TEMPLATE, title='Login', error='Acceso Denegado', url_for=url_for, session=session, gs_reason='')
    return render_cached(LOGIN_TEMPLATE, title='Login', url_for=url_for, session=session, gs_reason='')

ADMIN_TEMPLATE = BASE_HTML_TEMPLATE.replace('{% block content %}{% endblock %}', """
{% block content %}
<div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
    <section class="lg:col-span-1 animate-enter">
        <div class="glass-panel p-6 rounded-2xl relative shadow-xl">
            <h3 class="text-lg font-bold text-white mb-6">{% if edit_page %} EDITAR {% else %} CREAR NODO {% endif %}</h3>
            {% if message %}<div class="mb-5 p-3 bg-green-500/10 border border-green-500/20 rounded-lg text-green-400 text-xs">{{ message }}</div>{% endif %}
            <form id="node-form" method="POST" action="{{ url_for('update_page', page_slug=edit_page.slug) if edit_page else url_for('add_page') }}" class="space-y-5">
                <div><label class="block text-xs font-bold text-gray-500 mb-2 uppercase">Título</label><input type="text" name="title" required class="w-full px-4 py-3 rounded-lg input-liquid text-sm" value="{{ edit_page.title if edit_page else '' }}"></div>

                <div>
                    <label class="block text-xs font-bold text-gray-500 mb-2 uppercase">Asignatura</label>
                    <div class="relative" id="adminSubjectDropdown">
                        <button type="button" class="w-full px-4 py-3 rounded-lg input-liquid text-sm flex items-center justify-between text-left hover:border-purple-500/50 transition-colors">
                            <span id="adminSubjectLabel">{{ edit_page.subject if edit_page else 'Seleccionar...' }}</span>
                            <i id="adminSubjectArrow" class="fa-solid fa-chevron-down text-xs text-purple-400 transition-transform duration-300"></i>
                        </button>
                        <input type="hidden" name="subject" id="adminSubjectInput" value="{{ edit_page.subject if edit_page else '' }}" required>
                        <div id="adminSubjectMenu" class="hidden absolute top-full left-0 w-full mt-2 glass-panel rounded-lg z-50 overflow-hidden shadow-2xl border border-white/10">
                            <ul class="max-h-48 overflow-y-auto py-1 custom-scrollbar">
                                {% for subject in subject_icons.keys() %}
                                <li><button type="button" class="admin-subject-item w-full text-left px-4 py-2 text-sm text-gray-300 hover:text-white hover:bg-purple-500/20 transition-all" data-value="{{ subject }}"><span>{{ subject }}</span></button></li>
                                {% endfor %}
                            </ul>
                        </div>
                    </div>
                </div>

                <div><label class="block text-xs font-bold text-gray-500 mb-2 uppercase">Embed</label><textarea name="embed_code" required class="w-full px-4 py-3 rounded-lg input-liquid text-xs font-mono h-24 resize-none">{{ edit_page.embed_code if edit_page else '' }}</textarea></div>

                <div>
                    <span class="block text-xs font-bold text-gray-500 mb-2 uppercase">Icono</span>
                    <div id="icon-container" class="grid grid-cols-4 gap-2 mb-2 p-2 rounded bg-black/20 border border-white/5 min-h-[50px]"></div>
                    <input type="hidden" id="selected-icon" name="icon" required value="{{ edit_page.icon if edit_page else '' }}">
                </div>

                <div>
                    <span class="block text-xs font-bold text-gray-500 mb-2 uppercase">Color</span>
                    <div class="flex gap-2 justify-between">
                        {% for color_name, color_class in theme_colors.items() %}
                        <label class="cursor-pointer group relative">
                            <input type="radio" name="color" value="{{ color_class }}" required class="sr-only peer" {% if edit_page and edit_page.color == color_class %}checked{% endif %} {% if not edit_page and loop.first %}checked{% endif %}>
                            <div class="w-8 h-8 rounded-full border-2 border-transparent peer-checked:border-white transition-all {{ color_class.split(' ')[0] }} opacity-60 peer-checked:opacity-100"></div>
                        </label>
                        {% endfor %}
                    </div>
                </div>
                <div class="p-3 bg-white/5 rounded-lg border border-white/5 flex items-center justify-between">
                    <span class="text-sm font-bold text-pink-400"><i class="fa-solid fa-lock"></i> Modo Privado</span>
                    <input type="checkbox" name="is_private" id="check_private" {% if edit_page and edit_page.is_private %}checked{% endif %}>
                </div>
                <div id="private_options" class="hidden p-3 bg-pink-500/10 border border-pink-500/20 rounded-lg">
                    <p class="text-xs text-gray-300 mb-2">Usuarios permitidos:</p>
                    <div class="grid grid-cols-2 gap-2">
                    {% for user in private_users.keys() %}
                        <label class="text-xs text-gray-400"><input type="checkbox" name="allowed_users" value="{{ user }}" {% if edit_page and user in edit_page.allowed_users %}checked{% endif %}> {{ user }}</label>
                    {% endfor %}
                    </div>
                </div>
                <button type="submit" class="w-full btn-glow text-white font-bold py-3 rounded-lg">GUARDAR <span class="opacity-50 text-[0.6rem] ml-1">CTRL+ENTER</span></button>
            </form>
        </div>
    </section>
    <section class="lg:col-span-2 animate-enter delay-100">
        <!-- Tab bar -->
        <div class="flex gap-2 mb-4">
            <button onclick="adminTab('pages')" id="atab-pages" class="px-4 py-2 rounded-xl text-xs font-bold tracking-wider transition-all bg-purple-500/20 border border-purple-500/40 text-purple-200">
                <i class="fa-solid fa-database mr-1.5"></i>PÁGINAS ({{ pages|length }})
            </button>
            <button onclick="adminTab('users')" id="atab-users" class="px-4 py-2 rounded-xl text-xs font-bold tracking-wider transition-all bg-white/5 border border-white/10 text-gray-400 hover:text-white hover:bg-white/10">
                <i class="fa-solid fa-users mr-1.5"></i>USUARIOS ({{ private_users|length }})
            </button>
        </div>

        <!-- PAGES PANEL -->
        <div id="apanel-pages" class="glass-panel p-6 rounded-2xl min-h-[600px]">
            <h3 class="text-sm font-bold text-gray-400 uppercase tracking-widest mb-4">Base de datos de contenido</h3>
            <div class="space-y-3">
                {% for page in pages %}
                <div class="flex items-center justify-between p-3 rounded-xl bg-white/5 border border-white/5 hover:border-purple-500/30 transition-all">
                    <div class="flex items-center gap-4 overflow-hidden">
                        <div class="w-10 h-10 rounded-lg flex items-center justify-center {{ page.color }} bg-opacity-20 text-white border border-white/10"><i class="{{ page.icon }}"></i></div>
                        <div>
                            <h4 class="font-bold text-sm text-gray-200">{{ page.title }}</h4>
                            {% if page.is_private %}<span class="text-[0.6rem] text-pink-400 font-mono"><i class="fa-solid fa-lock mr-1"></i>PRIVADO</span>{% endif %}
                        </div>
                    </div>
                    <div class="flex gap-2">
                        <a href="{{ url_for('admin', edit_slug=page.slug) }}" class="text-yellow-400 hover:text-white transition"><i class="fa-solid fa-pencil"></i></a>
                        <a href="{{ url_for('delete_page', page_slug=page.slug) }}" onclick="return confirm('Confirmar')" class="text-red-400 hover:text-white transition"><i class="fa-solid fa-trash"></i></a>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>

        <!-- USERS PANEL -->
        <div id="apanel-users" class="hidden glass-panel p-6 rounded-2xl min-h-[600px]">
            <div class="flex items-center justify-between mb-5">
                <h3 class="text-sm font-bold text-gray-400 uppercase tracking-widest">Alumnos registrados</h3>
                <span class="text-xs text-gray-600 font-mono">{{ private_users|length }} cuenta(s)</span>
            </div>

            {% if private_users %}
            <div class="space-y-3" id="users-list">
                {% for uname, udata in private_users.items() %}
                <div class="p-4 rounded-xl bg-white/5 border border-white/8 hover:border-purple-500/20 transition-all" id="urow-{{ loop.index }}">
                    <div class="flex items-center gap-3">
                        <!-- Avatar -->
                        <div class="w-10 h-10 rounded-full bg-gradient-to-br from-purple-500/30 to-pink-500/20 border border-white/10 flex items-center justify-center flex-shrink-0">
                            <span class="text-sm font-bold text-purple-300">{{ uname[0].upper() }}</span>
                        </div>
                        <div class="flex-1 min-w-0">
                            <div class="flex items-center gap-2">
                                <span class="font-bold text-sm text-white">{{ uname }}</span>
                                {% if udata.get('banned') %}
                                <span class="text-[0.55rem] bg-red-500/20 border border-red-500/30 text-red-400 px-2 py-0.5 rounded-full font-mono">BANEADO</span>
                                {% endif %}
                            </div>
                            <p class="text-[0.6rem] text-gray-600 font-mono mt-0.5">Registrado {{ udata.get('created_at','')[:10] }}</p>
                            {% if udata.get('message') %}
                            <p class="text-[0.6rem] text-purple-400 mt-0.5"><i class="fa-solid fa-message mr-1"></i>{{ udata['message'] }}</p>
                            {% endif %}
                        </div>
                        <!-- Actions -->
                        <div class="flex items-center gap-1.5 flex-shrink-0">
                            <button onclick="openMsgModal('{{ uname }}')" title="Enviar mensaje"
                                class="w-8 h-8 rounded-lg bg-purple-500/10 border border-purple-500/20 text-purple-400 hover:bg-purple-500/25 transition flex items-center justify-center">
                                <i class="fa-solid fa-satellite-dish text-xs"></i>
                            </button>
                            {% if udata.get('banned') %}
                            <button onclick="adminAction('unban','{{ uname }}')" title="Desbanear"
                                class="w-8 h-8 rounded-lg bg-green-500/10 border border-green-500/20 text-green-400 hover:bg-green-500/25 transition flex items-center justify-center">
                                <i class="fa-solid fa-unlock text-xs"></i>
                            </button>
                            {% else %}
                            <button onclick="adminAction('kick','{{ uname }}')" title="Banear / Expulsar"
                                class="w-8 h-8 rounded-lg bg-yellow-500/10 border border-yellow-500/20 text-yellow-400 hover:bg-yellow-500/25 transition flex items-center justify-center">
                                <i class="fa-solid fa-ban text-xs"></i>
                            </button>
                            {% endif %}
                            <button onclick="adminAction('delete','{{ uname }}')" title="Eliminar cuenta"
                                class="w-8 h-8 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 hover:bg-red-500/25 transition flex items-center justify-center">
                                <i class="fa-solid fa-trash text-xs"></i>
                            </button>
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <div class="flex flex-col items-center justify-center py-16 text-center opacity-50">
                <i class="fa-solid fa-users text-3xl text-gray-700 mb-3"></i>
                <p class="text-sm text-gray-600">Aún no hay alumnos registrados</p>
                <p class="text-xs text-gray-700 mt-1">Aparecerán aquí cuando se registren en la web</p>
            </div>
            {% endif %}
        </div>
    </section>
</div>

<!-- MSG MODAL -->
<div id="msgModal" class="hidden fixed inset-0 z-[150] flex items-center justify-center p-4 bg-black/70 backdrop-blur-md">
    <div class="glass-panel w-full max-w-sm rounded-2xl border border-purple-500/30 p-6 shadow-[0_0_40px_rgba(168,85,247,0.2)]">
        <h4 class="text-sm font-bold text-white mb-1">Mensaje en tiempo real</h4>
        <p class="text-xs text-gray-500 mb-4">Se mostrará como notificación en el navegador de <span id="msg-target-label" class="text-purple-300"></span></p>
        <textarea id="msg-text" rows="3" class="w-full px-4 py-3 rounded-xl input-liquid text-sm resize-none mb-4" placeholder="Escribe el mensaje..."></textarea>
        <div class="flex gap-3">
            <button onclick="sendMsg()" class="flex-1 btn-glow text-white font-bold py-2.5 rounded-xl text-sm"><i class="fa-solid fa-paper-plane mr-2"></i>ENVIAR</button>
            <button onclick="document.getElementById('msgModal').classList.add('hidden')" class="px-4 py-2.5 rounded-xl bg-white/5 border border-white/10 text-gray-400 hover:text-white transition text-sm">CANCELAR</button>
        </div>
    </div>
</div>

<script>
    let _msgTarget = '';
    function adminTab(tab) {
        document.getElementById('apanel-pages').classList.toggle('hidden', tab!=='pages');
        document.getElementById('apanel-users').classList.toggle('hidden', tab!=='users');
        document.getElementById('atab-pages').className = `px-4 py-2 rounded-xl text-xs font-bold tracking-wider transition-all ${tab==='pages' ? 'bg-purple-500/20 border border-purple-500/40 text-purple-200' : 'bg-white/5 border border-white/10 text-gray-400 hover:text-white hover:bg-white/10'}`;
        document.getElementById('atab-users').className = `px-4 py-2 rounded-xl text-xs font-bold tracking-wider transition-all ${tab==='users' ? 'bg-purple-500/20 border border-purple-500/40 text-purple-200' : 'bg-white/5 border border-white/10 text-gray-400 hover:text-white hover:bg-white/10'}`;
    }
    function openMsgModal(username) {
        _msgTarget = username;
        document.getElementById('msg-target-label').textContent = username;
        document.getElementById('msg-text').value = '';
        document.getElementById('msgModal').classList.remove('hidden');
    }
    async function sendMsg() {
        const text = document.getElementById('msg-text').value.trim();
        if (!text) return;
        const res = await fetch('/api/admin/send_message', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username:_msgTarget, text})});
        const d = await res.json();
        if (d.ok) { document.getElementById('msgModal').classList.add('hidden'); }
    }
    async function adminAction(action, username) {
        const labels = {kick:'¿Banear a '+username+'?', delete:'¿Eliminar cuenta de '+username+'? Esta acción es irreversible.', unban:'¿Desbanear a '+username+'?'};
        if (!confirm(labels[action])) return;
        const endpoints = {kick:'/api/admin/kick', delete:'/api/admin/delete_user', unban:'/api/admin/unban'};
        const res = await fetch(endpoints[action], {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username})});
        if ((await res.json()).ok) location.reload();
    }
    const subjectIcons = {{ subject_icons | tojson }};
    const iconContainer = document.getElementById('icon-container');
    const selectedIconInput = document.getElementById('selected-icon');

    document.addEventListener('DOMContentLoaded', () => {
        initDropdown('adminSubjectDropdown', 'adminSubjectInput', 'adminSubjectLabel', 'adminSubjectArrow', 'adminSubjectMenu', '.admin-subject-item');

        const subjInput = document.getElementById('adminSubjectInput');
        if(subjInput.value) renderIcons(subjInput.value);

        subjInput.addEventListener('change', (e) => {
             selectedIconInput.value = '';
             renderIcons(e.target.value);
        });
    });

    function renderIcons(subject) {
        iconContainer.innerHTML = '';
        const icons = subjectIcons[subject] || [];
        const fallbackIcons = ['fa-solid fa-book', 'fa-solid fa-cube', 'fa-solid fa-atom', 'fa-solid fa-calculator'];
        const listToUse = icons.length > 0 ? icons : fallbackIcons;

        const currentIcon = selectedIconInput.value;

        listToUse.forEach((iconClass, index) => {
            const label = document.createElement('label');
            label.className = 'cursor-pointer w-full relative group';
            const radio = document.createElement('input');
            radio.type = 'radio'; radio.name = 'icon_temp'; radio.value = iconClass;
            radio.className = 'sr-only peer';

            if (currentIcon === iconClass) radio.checked = true;
            else if (!currentIcon && index === 0) { radio.checked = true; selectedIconInput.value = iconClass; }

            const div = document.createElement('div');
            div.className = 'h-10 rounded-lg flex items-center justify-center text-gray-500 bg-white/5 border border-white/5 peer-checked:bg-purple-500 peer-checked:text-white peer-checked:shadow-[0_0_15px_rgba(168,85,247,0.5)] transition-all hover:bg-white/10';
            div.innerHTML = `<i class="${iconClass}"></i>`;

            radio.addEventListener('change', () => { if(radio.checked) selectedIconInput.value = iconClass; });
            label.append(radio, div);
            iconContainer.appendChild(label);
        });
    }

    const cp = document.getElementById('check_private');
    const po = document.getElementById('private_options');
    function toggleP() { if(cp.checked) po.classList.remove('hidden'); else po.classList.add('hidden'); }
    cp.addEventListener('change', toggleP); toggleP();
</script>
{% endblock %}
""")

@app.route('/admin')
@app.route('/admin/<edit_slug>')
@admin_required
def admin(edit_slug=None):
    pages = load_pages()
    for p in pages:
        if 'is_private' not in p: p['is_private'] = False
        if 'allowed_users' not in p: p['allowed_users'] = []
    page_to_edit = next((p for p in pages if p['slug'] == edit_slug), None) if edit_slug else None
    return render_cached(ADMIN_TEMPLATE, title='ADMIN', session=session, theme_colors=THEME_COLORS, private_users=load_users(), pages=pages, edit_page=page_to_edit, url_for=url_for, message=session.pop('message', None), subject_icons=SUBJECT_ICONS, gs_reason='')

@app.route('/add_page', methods=['POST'])
@admin_required
def add_page(): return process_page_form(is_update=False)

@app.route('/update_page/<page_slug>', methods=['POST'])
@admin_required
def update_page(page_slug): return process_page_form(is_update=True, old_slug=page_slug)

def process_page_form(is_update, old_slug=None):
    title = request.form.get('title')
    embed_code = request.form.get('embed_code')
    subject = request.form.get('subject')
    icon = request.form.get('icon')
    color = request.form.get('color')
    is_private = request.form.get('is_private') == 'on'
    allowed_users = request.form.getlist('allowed_users')

    if not all([title, embed_code, subject, icon, color]):
        session['message'] = 'Faltan datos'; return redirect(url_for('admin'))

    pages = load_pages()
    new_slug = title.lower().replace(' ', '-').replace('/', '')
    new_page = {'title': title, 'embed_code': embed_code, 'subject': subject, 'icon': icon, 'color': color, 'slug': new_slug, 'is_private': is_private, 'allowed_users': allowed_users}

    if is_update and old_slug:
        for i, p in enumerate(pages):
            if p['slug'] == old_slug: pages[i] = new_page; break
        session['message'] = 'ACTUALIZADO'
    else:
        pages.append(new_page); session['message'] = 'CREADO'

    save_pages(pages)
    return redirect(url_for('admin'))

@app.route('/delete_page/<page_slug>')
@admin_required
def delete_page(page_slug):
    save_pages([p for p in load_pages() if p['slug'] != page_slug])
    session['message'] = 'ELIMINADO'
    return redirect(url_for('admin'))

PRIVATE_LOGIN_TEMPLATE = BASE_HTML_TEMPLATE.replace('{% block content %}{% endblock %}', """
{% block content %}
<div class="max-w-sm mx-auto mt-16 animate-enter">
    <div class="glass-panel p-8 rounded-2xl border-t-4 border-t-pink-500 relative overflow-hidden shadow-[0_0_50px_rgba(0,0,0,0.5)]">
        <div class="absolute top-0 right-0 w-40 h-40 bg-pink-500/20 blur-[60px] rounded-full pointer-events-none"></div>
        <div class="text-center mb-8 relative z-10">
            <h2 class="text-xl font-bold text-white tracking-wide">ACCESO ALUMNO</h2>
        </div>
        {% if error %}<div class="mb-5 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-xs text-center">{{ error }}</div>{% endif %}
        <form method="POST" class="space-y-4 relative z-10">
            <input type="text" name="p_username" class="w-full px-4 py-3 rounded-lg input-liquid text-sm" placeholder="Tu Nombre" required>
            <input type="password" name="p_password" class="w-full px-4 py-3 rounded-lg input-liquid text-sm" placeholder="Contraseña" required>
            <button type="submit" class="w-full btn-glow text-white font-bold py-3 rounded-lg">ENTRAR</button>
        </form>
    </div>
</div>
{% endblock %}
""")

# ─────────────────────────────────────────────────────────
# API ENDPOINTS — auth y SSE
# ─────────────────────────────────────────────────────────

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    ok, msg = register_user(username, password)
    if ok:
        session['private_user'] = username
        session['login_ts'] = datetime.datetime.utcnow().timestamp()
        session.permanent = True
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': msg}), 400

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    u = get_user(username)
    if not u:
        return jsonify({'ok': False, 'error': 'Usuario no encontrado. ¿Te has registrado ya?'}), 401
    if u.get('banned'):
        return jsonify({'ok': False, 'error': 'Tu acceso ha sido revocado.'}), 403
    if not check_password_hash(u['hash'], password):
        return jsonify({'ok': False, 'error': 'Contraseña incorrecta'}), 401
    session['private_user'] = username
    session['login_ts'] = datetime.datetime.utcnow().timestamp()
    session.permanent = True
    return jsonify({'ok': True})

@app.route('/api/sse/<username>')
def api_sse(username):
    """Server-Sent Events stream per user."""
    def stream():
        q = get_sse_queue(username)
        # Send initial ping
        yield 'data: {"type":"ping"}\n\n'
        while True:
            try:
                msg = q.get(timeout=20)
                yield f'data: {json.dumps(msg)}\n\n'
            except queue.Empty:
                yield 'data: {"type":"ping"}\n\n'
    return Response(stream(), mimetype='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.route('/api/admin/send_message', methods=['POST'])
@admin_required
def api_send_message():
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    text = (data.get('text') or '').strip()
    if not username or not text:
        return jsonify({'ok': False, 'error': 'Datos incompletos'}), 400
    push_message(username, text)
    return jsonify({'ok': True})

@app.route('/api/admin/kick', methods=['POST'])
@admin_required
def api_kick():
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    if not username:
        return jsonify({'ok': False}), 400
    kick_user(username)
    users = load_users()
    if username in users:
        users[username]['banned'] = True
        save_users(users)
    return jsonify({'ok': True})

@app.route('/api/admin/unban', methods=['POST'])
@admin_required
def api_unban():
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    users = load_users()
    if username in users:
        users[username]['banned'] = False
        save_users(users)
    return jsonify({'ok': True})

@app.route('/api/admin/delete_user', methods=['POST'])
@admin_required
def api_delete_user():
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    users = load_users()
    if username in users:
        del users[username]
        save_users(users)
    return jsonify({'ok': True})

# ─────────────────────────────────────────────────────────
# AUTH WALL page (for ?gs_reason= redirects)
# ─────────────────────────────────────────────────────────
@app.route('/auth_wall')
def auth_wall():
    reason = request.args.get('reason','')
    # Just redirect to index with gs_reason param so modal picks it up
    return redirect(url_for('index', gs_reason=reason))

# Legacy private_login kept for backward compat — redirects to auth wall
@app.route('/private_login', methods=['GET', 'POST'])
def private_login():
    return redirect(url_for('index'))

PRIVATE_ZONE_TEMPLATE = BASE_HTML_TEMPLATE.replace('{% block content %}{% endblock %}', """
{% block content %}
<main class="animate-enter">
    <div class="flex items-center justify-between mb-8">
        <div class="flex items-center gap-3">
            <div class="w-10 h-10 bg-pink-500 rounded-lg flex items-center justify-center shadow-[0_0_15px_rgba(236,72,153,0.5)]"><i class="fa-solid fa-folder-open text-white"></i></div>
            <h2 class="text-xl font-bold text-white tracking-wide">ARCHIVOS CLASIFICADOS</h2>
        </div>
        <div class="px-4 py-2 rounded-full bg-pink-500/10 border border-pink-500/20 text-pink-400 text-xs font-bold">AGENTE: {{ session['private_user'] }}</div>
    </div>
    {% if pages %}
        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
            {% for page in pages %}
            <article class="card-dynamic fast-glass rounded-xl overflow-hidden border-l-4 border-l-pink-500 group shadow-lg">
                <a href="{{ url_for('show_page', page_slug=page.slug) }}" class="block p-5 relative overflow-hidden">
                    <div class="absolute inset-0 bg-pink-500/5 group-hover:bg-pink-500/10 transition-colors duration-300"></div>
                    <div class="flex items-center gap-4 relative z-10">
                        <div class="bg-black/40 w-12 h-12 rounded-lg flex items-center justify-center border border-white/5 group-hover:border-pink-500/30 transition-all">
                            <i class="{{ page.icon }} text-pink-400 text-lg group-hover:scale-110 transition-transform"></i>
                        </div>
                        <div><h3 class="text-lg font-bold text-white">{{ page.title }}</h3><p class="text-[0.65rem] uppercase text-gray-500">Solo ojos autorizados</p></div>
                    </div>
                </a>
            </article>
            {% endfor %}
        </div>
    {% else %}
        <div class="p-12 fast-glass border-dashed border-pink-500/30 rounded-xl text-center text-gray-500"><i class="fa-solid fa-box-open text-3xl mb-3 opacity-50"></i><p>Sin asignaciones.</p></div>
    {% endif %}
</main>
{% endblock %}
""")

@app.route('/private')
@private_required
def private_zone():
    user = session.get('private_user')
    all_pages = load_pages()
    # Show pages where allowed_users contains the user's name, or 'all'
    my_pages = [p for p in all_pages if p.get('is_private') and
                ('all' in p.get('allowed_users', []) or user in p.get('allowed_users', []))]
    return render_cached(PRIVATE_ZONE_TEMPLATE, title='Zona Privada', pages=my_pages, url_for=url_for, session=session, gs_reason='')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('index'))


# ═══════════════════════════════════════════════════════
# CHAT
# ═══════════════════════════════════════════════════════

CHAT_TEMPLATE = BASE_HTML_TEMPLATE.replace('{% block content %}{% endblock %}', """{% block content %}
<style>
/* ── CHAT LAYOUT ───────────────────────────────────── */
#chat-page { display:flex; flex-direction:column; height:calc(100vh - 10rem); max-width:800px; margin:0 auto; }
#chat-header {
    flex-shrink:0; display:flex; align-items:center; gap:12px;
    padding:1rem 1.25rem;
    background: rgba(255,255,255,0.04);
    backdrop-filter: blur(12px);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 18px 18px 0 0;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.07);
}
#chat-messages {
    flex:1; overflow-y:auto; padding:1rem 1.25rem;
    display:flex; flex-direction:column; gap:0.65rem;
    background: rgba(255,255,255,0.02);
    border-left: 1px solid rgba(255,255,255,0.06);
    border-right: 1px solid rgba(255,255,255,0.06);
    scroll-behavior: smooth;
}
#chat-messages::-webkit-scrollbar { width:4px; }
#chat-messages::-webkit-scrollbar-track { background:transparent; }
#chat-messages::-webkit-scrollbar-thumb { background:rgba(168,85,247,0.3); border-radius:4px; }
#chat-footer {
    flex-shrink:0; padding:0.85rem 1rem;
    background: rgba(255,255,255,0.04);
    backdrop-filter: blur(12px);
    border: 1px solid rgba(255,255,255,0.09);
    border-top: none;
    border-radius: 0 0 18px 18px;
    box-shadow: inset 0 -1px 0 rgba(255,255,255,0.04);
}

/* ── BUBBLE BASE ────────────────────────────────────── */
.chat-row { display:flex; align-items:flex-end; gap:8px; max-width:78%; }
.chat-row.mine  { align-self:flex-end;  flex-direction:row-reverse; }
.chat-row.theirs{ align-self:flex-start; }

.bubble {
    position:relative;
    padding: 0.55rem 0.85rem;
    border-radius: 18px;
    backdrop-filter: blur(8px);
    border: 1px solid rgba(255,255,255,0.08);
    box-shadow: 0 2px 10px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.08);
    max-width: 100%;
    word-break: break-word;
}
/* MINE — purple glow */
.bubble.mine {
    background: linear-gradient(135deg, rgba(109,40,217,0.55) 0%, rgba(168,85,247,0.45) 100%);
    border-color: rgba(168,85,247,0.35);
    border-radius: 18px 4px 18px 18px;
    box-shadow: 0 2px 12px rgba(168,85,247,0.2), inset 0 1px 0 rgba(255,255,255,0.12);
}
/* THEIRS — glass */
.bubble.theirs {
    background: rgba(255,255,255,0.07);
    border-color: rgba(255,255,255,0.1);
    border-radius: 4px 18px 18px 18px;
}
.bubble.deleted {
    opacity:0.4; font-style:italic;
    background: rgba(255,255,255,0.03) !important;
    border-color: rgba(255,255,255,0.05) !important;
}
.bubble-img {
    max-width:240px; max-height:280px; width:100%;
    border-radius:12px; display:block; margin-bottom:0.4rem;
    cursor:pointer; transition:transform 0.15s;
}
.bubble-img:hover { transform:scale(1.02); }
.bubble-text { font-size:0.88rem; color:#f3f4f6; line-height:1.45; }
.bubble-meta {
    display:flex; align-items:center; gap:6px;
    margin-top:0.3rem; font-size:0.58rem; color:rgba(255,255,255,0.35);
}
.bubble.mine  .bubble-meta { justify-content:flex-end; }
.bubble.theirs .bubble-meta { justify-content:flex-start; }
.read-avatars { display:flex; gap:2px; }
.read-av {
    width:14px; height:14px; border-radius:50%;
    background:linear-gradient(135deg,rgba(168,85,247,0.6),rgba(34,211,238,0.5));
    border:1px solid rgba(255,255,255,0.15);
    font-size:0.45rem; color:#fff;
    display:inline-flex; align-items:center; justify-content:center;
    font-weight:700;
}

/* ── SENDER AVATAR ─────────────────────────────────── */
.sender-av {
    width:30px; height:30px; border-radius:50%; flex-shrink:0;
    display:flex; align-items:center; justify-content:center;
    font-size:0.7rem; font-weight:800; color:#fff;
    border:1px solid rgba(255,255,255,0.12);
}

/* ── HOVER ACTIONS ─────────────────────────────────── */
.msg-actions {
    position:absolute; top:-28px;
    display:none; gap:3px; align-items:center;
    background:rgba(12,12,22,0.95); backdrop-filter:blur(16px);
    border:1px solid rgba(255,255,255,0.1); border-radius:10px;
    padding:3px 6px;
    box-shadow:0 4px 16px rgba(0,0,0,0.5);
    z-index:20;
}
.chat-row.mine  .msg-actions { right:0; }
.chat-row.theirs .msg-actions { left:0; }
.chat-row:hover .msg-actions { display:flex; }
.msg-action-btn {
    width:24px; height:24px; border-radius:6px;
    display:flex; align-items:center; justify-content:center;
    font-size:0.65rem; cursor:pointer; transition:all 0.12s;
    color:#9ca3af;
}
.msg-action-btn:hover { background:rgba(255,255,255,0.1); color:#fff; }
.msg-action-btn.del:hover { color:#f87171; background:rgba(239,68,68,0.15); }

/* ── INPUT AREA ────────────────────────────────────── */
#chat-input-row { display:flex; align-items:flex-end; gap:8px; }
#chat-input {
    flex:1; resize:none; max-height:120px; min-height:40px;
    padding:0.6rem 1rem;
    background: rgba(255,255,255,0.06); backdrop-filter:blur(8px);
    border:1px solid rgba(255,255,255,0.1); border-radius:14px;
    color:#f3f4f6; font-size:0.875rem; line-height:1.4;
    outline:none; transition:border-color 0.2s, box-shadow 0.2s;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.06);
}
#chat-input:focus { border-color:rgba(168,85,247,0.5); box-shadow:0 0 0 3px rgba(168,85,247,0.1), inset 0 1px 0 rgba(255,255,255,0.08); }
#chat-input::placeholder { color:rgba(255,255,255,0.25); }
.chat-icon-btn {
    width:40px; height:40px; border-radius:12px; flex-shrink:0;
    display:flex; align-items:center; justify-content:center;
    cursor:pointer; transition:all 0.18s; font-size:0.9rem;
}
#btn-send {
    background:linear-gradient(135deg, rgba(109,40,217,0.8), rgba(168,85,247,0.8));
    border:1px solid rgba(168,85,247,0.5); color:#fff;
    box-shadow:0 0 14px rgba(168,85,247,0.3);
}
#btn-send:hover { box-shadow:0 0 20px rgba(168,85,247,0.5); transform:scale(1.05); }
#btn-img { background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1); color:#9ca3af; }
#btn-img:hover { background:rgba(255,255,255,0.12); color:#fff; }

/* ── IMAGE PREVIEW ─────────────────────────────────── */
#img-preview-wrap {
    display:none; margin-bottom:0.5rem;
    padding:0.4rem; background:rgba(255,255,255,0.05); border-radius:10px;
    border:1px solid rgba(255,255,255,0.08);
    position:relative; display:inline-block;
}
#img-preview { max-height:80px; border-radius:8px; }
#img-clear { position:absolute; top:-6px; right:-6px; width:18px; height:18px;
    background:#ef4444; border-radius:50%; display:flex; align-items:center;
    justify-content:center; font-size:0.55rem; color:#fff; cursor:pointer;
    box-shadow:0 2px 6px rgba(0,0,0,0.4); }

/* ── EDIT INPUT ────────────────────────────────────── */
.edit-input {
    width:100%; background:rgba(255,255,255,0.08); border:1px solid rgba(168,85,247,0.4);
    border-radius:10px; padding:0.4rem 0.6rem; color:#f3f4f6; font-size:0.85rem;
    outline:none; resize:none;
}
.edit-input:focus { border-color:rgba(168,85,247,0.7); box-shadow:0 0 0 3px rgba(168,85,247,0.1); }

/* ── LIGHTBOX ──────────────────────────────────────── */
#lightbox { display:none; position:fixed; inset:0; z-index:500;
    background:rgba(0,0,0,0.92); backdrop-filter:blur(20px);
    align-items:center; justify-content:center; cursor:zoom-out; }
#lightbox.active { display:flex; }
#lightbox img { max-width:90vw; max-height:90vh; border-radius:12px;
    box-shadow:0 0 60px rgba(0,0,0,0.8); }

/* ── DATE DIVIDER ──────────────────────────────────── */
.date-divider {
    text-align:center; font-size:0.58rem; color:rgba(255,255,255,0.25);
    font-family:monospace; letter-spacing:0.08em;
    display:flex; align-items:center; gap:10px; margin:0.5rem 0;
}
.date-divider::before,.date-divider::after {
    content:''; flex:1; height:1px; background:rgba(255,255,255,0.07);
}

/* ── NO SESSION BANNER ─────────────────────────────── */
#no-session-bar {
    text-align:center; padding:0.6rem; font-size:0.75rem;
    background:rgba(168,85,247,0.08); border-bottom:1px solid rgba(168,85,247,0.15);
    color:#c4b5fd;
}
</style>

<div id="chat-page">
    <!-- HEADER -->
    <div id="chat-header">
        <div class="w-10 h-10 rounded-full bg-gradient-to-br from-cyan-500/30 to-purple-500/30 border border-white/10 flex items-center justify-center flex-shrink-0">
            <i class="fa-solid fa-users text-cyan-400 text-sm"></i>
        </div>
        <div class="flex-1">
            <h2 class="text-sm font-bold text-white tracking-wide">Grupo clase — 4ºE</h2>
            <p class="text-[0.62rem] text-gray-500 font-mono" id="online-count">cargando...</p>
        </div>
        <div class="flex items-center gap-2">
            <span class="text-[0.58rem] text-gray-700 font-mono hidden sm:block">Historial se borra cada sábado</span>
            <div class="w-2 h-2 rounded-full bg-cyan-400 animate-pulse"></div>
        </div>
    </div>

    {% if not current_user %}
    <div id="no-session-bar">
        <i class="fa-solid fa-circle-info mr-1.5"></i>
        Estás en modo lectura. <button onclick="document.getElementById('authWallModal').classList.remove('hidden')" class="underline font-bold hover:text-white transition">Inicia sesión</button> para escribir.
    </div>
    {% endif %}

    <!-- MESSAGES -->
    <div id="chat-messages">
        <div id="msgs-inner"></div>
        <div id="scroll-anchor"></div>
    </div>

    <!-- FOOTER -->
    <div id="chat-footer">
        {% if current_user %}
        <div id="img-preview-wrap" style="display:none; margin-bottom:0.5rem;">
            <img id="img-preview" src="" alt="">
            <div id="img-clear" onclick="clearImgPreview()"><i class="fa-solid fa-xmark"></i></div>
        </div>
        <div id="chat-input-row">
            <label class="chat-icon-btn" id="btn-img" title="Subir imagen">
                <i class="fa-solid fa-image"></i>
                <input type="file" id="img-file" accept="image/*" class="hidden" onchange="previewImg(this)">
            </label>
            <textarea id="chat-input" placeholder="Escribe un mensaje…" rows="1" onkeydown="handleKey(event)" oninput="autoResize(this)"></textarea>
            <button class="chat-icon-btn" id="btn-send" onclick="sendMessage()" title="Enviar (Enter)">
                <i class="fa-solid fa-paper-plane"></i>
            </button>
        </div>
        {% else %}
        <div class="text-center text-xs text-gray-600 py-1">Solo lectura — inicia sesión para participar</div>
        {% endif %}
    </div>
</div>

<!-- LIGHTBOX -->
<div id="lightbox" onclick="closeLightbox()">
    <img id="lightbox-img" src="" alt="">
</div>

<script>
const ME = {{ current_user|tojson }};
let _allMsgs   = [];
let _imgFile   = null;
let _editingId = null;

// ─── AVATAR COLOR ─────────────────────────────────────
function avatarColor(name) {
    if (!name) return '#6b7280';
    const colors = ['#7c3aed','#0891b2','#0d9488','#d97706','#dc2626','#7c3aed','#4f46e5','#db2777'];
    let h = 0; for (const c of name) h = (h*31 + c.charCodeAt(0)) & 0x7fffffff;
    return colors[h % colors.length];
}

// ─── RENDER MESSAGES ──────────────────────────────────
function ts(iso) {
    const d = new Date(iso);
    return d.toLocaleTimeString('es-ES',{hour:'2-digit',minute:'2-digit'});
}
function dateLabel(iso) {
    const d = new Date(iso);
    const today = new Date();
    const yesterday = new Date(today); yesterday.setDate(today.getDate()-1);
    if (d.toDateString() === today.toDateString()) return 'Hoy';
    if (d.toDateString() === yesterday.toDateString()) return 'Ayer';
    return d.toLocaleDateString('es-ES',{weekday:'long',day:'numeric',month:'long'});
}

function renderMsgs(msgs) {
    const container = document.getElementById('msgs-inner');
    let lastDate = '';
    let html = '';
    msgs.forEach(m => {
        const day = new Date(m.ts).toDateString();
        if (day !== lastDate) {
            html += `<div class="date-divider">${dateLabel(m.ts)}</div>`;
            lastDate = day;
        }
        html += renderBubble(m);
    });
    container.innerHTML = html;
}

function renderBubble(m, animate=false) {
    const isMine = ME && m.username === ME;
    const side   = isMine ? 'mine' : 'theirs';
    const del    = m.deleted;

    // Read avatars (exclude self)
    const readers = (m.read_by||[]).filter(u => u !== m.username).slice(0,5);
    const readHtml = readers.map(u =>
        `<span class="read-av" title="${u}" style="background:linear-gradient(135deg,${avatarColor(u)}88,${avatarColor(u)}44)">${u[0].toUpperCase()}</span>`
    ).join('');

    let bodyHtml = '';
    if (del) {
        bodyHtml = `<span class="bubble-text" style="color:rgba(255,255,255,0.3)"><i class="fa-solid fa-ban mr-1 text-xs"></i>Mensaje eliminado</span>`;
    } else {
        if (m.image) bodyHtml += `<img src="/chat_uploads/${m.image}" class="bubble-img" onclick="openLightbox(this.src)" loading="lazy">`;
        if (m.text)  bodyHtml += `<div class="bubble-text" id="txt-${m.id}">${escHtml(m.text)}${m.edited?'<span style="font-size:0.6rem;opacity:0.45;margin-left:4px">(editado)</span>':''}</div>`;
    }

    const actions = (ME && !del) ? `
        <div class="msg-actions">
            ${isMine && m.text ? `<div class="msg-action-btn" onclick="startEdit('${m.id}')" title="Editar"><i class="fa-solid fa-pencil"></i></div>` : ''}
            ${isMine ? `<div class="msg-action-btn del" onclick="deleteMsg('${m.id}')" title="Eliminar"><i class="fa-solid fa-trash"></i></div>` : ''}
            <div class="msg-action-btn" onclick="copyText('${m.id}')" title="Copiar"><i class="fa-solid fa-copy"></i></div>
        </div>` : '';

    const av = `<div class="sender-av" style="background:${avatarColor(m.username)}22;border-color:${avatarColor(m.username)}44;color:${avatarColor(m.username)}">${m.username[0].toUpperCase()}</div>`;

    return `<div class="chat-row ${side}" id="row-${m.id}" data-id="${m.id}" ${animate?'style="animation:fadeInUp 0.2s ease"':''}>
        ${side==='theirs'?av:''}
        <div style="display:flex;flex-direction:column;gap:2px;max-width:100%">
            ${side==='theirs'?`<span style="font-size:0.62rem;color:${avatarColor(m.username)};font-weight:700;padding-left:4px">${escHtml(m.username)}</span>`:''}
            <div class="bubble ${side} ${del?'deleted':''}" id="bub-${m.id}">
                ${actions}
                ${bodyHtml}
                <div class="bubble-meta">
                    <span>${ts(m.ts)}</span>
                    ${readHtml ? `<div class="read-avatars">${readHtml}</div>` : ''}
                </div>
            </div>
        </div>
        ${side==='mine'?av:''}
    </div>`;
}

function escHtml(s) {
    if (!s) return '';
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
}

// ─── INITIAL LOAD ──────────────────────────────────────
async function loadMessages() {
    const res  = await fetch('/api/chat/messages');
    _allMsgs   = await res.json();
    renderMsgs(_allMsgs);
    scrollBottom(true);
    markVisible();
}

function scrollBottom(instant=false) {
    const el = document.getElementById('chat-messages');
    if (instant) el.scrollTop = el.scrollHeight;
    else setTimeout(()=>{ el.scrollTop = el.scrollHeight; }, 50);
}

// ─── SSE ─────────────────────────────────────────────
function initChatSSE() {
    const es = new EventSource('/api/chat/stream');
    es.onmessage = ev => {
        const d = JSON.parse(ev.data);
        if      (d.type === 'new')    handleNew(d.msg);
        else if (d.type === 'edit')   handleEdit(d.msg);
        else if (d.type === 'delete') handleDelete(d.id);
        else if (d.type === 'read')   handleRead(d.msg_id, d.username);
        else if (d.type === 'online') document.getElementById('online-count').textContent = d.text;
    };
    es.onerror = () => {};
}

function handleNew(msg) {
    const idx = _allMsgs.findIndex(m => m.id === msg.id);
    if (idx !== -1) return; // duplicate
    _allMsgs.push(msg);
    const container = document.getElementById('msgs-inner');
    const div = document.createElement('div');
    div.innerHTML = renderBubble(msg, true);
    container.appendChild(div.firstElementChild);
    const wasAtBottom = document.getElementById('chat-messages').scrollHeight - document.getElementById('chat-messages').scrollTop < 200;
    if (wasAtBottom || (ME && msg.username === ME)) scrollBottom();
    markVisible();
}
function handleEdit(msg) {
    const idx = _allMsgs.findIndex(m=>m.id===msg.id);
    if (idx!==-1) _allMsgs[idx] = msg;
    const txtEl = document.getElementById('txt-'+msg.id);
    if (txtEl) txtEl.innerHTML = escHtml(msg.text)+'<span style="font-size:0.6rem;opacity:0.45;margin-left:4px">(editado)</span>';
}
function handleDelete(id) {
    const idx = _allMsgs.findIndex(m=>m.id===id);
    if (idx!==-1) { _allMsgs[idx].deleted=true; _allMsgs[idx].text=''; }
    const bub = document.getElementById('bub-'+id);
    if (bub) {
        bub.classList.add('deleted');
        const txt = bub.querySelector('.bubble-text');
        if (txt) txt.innerHTML = '<i class="fa-solid fa-ban mr-1 text-xs"></i>Mensaje eliminado';
        const actions = bub.querySelector('.msg-actions');
        if (actions) actions.remove();
    }
}
function handleRead(msg_id, username) {
    const msg = _allMsgs.find(m=>m.id===msg_id);
    if (msg && !msg.read_by.includes(username)) msg.read_by.push(username);
    // Update read avatars in DOM
    const bub = document.getElementById('bub-'+msg_id);
    if (!bub || !msg) return;
    const readers = (msg.read_by||[]).filter(u=>u!==msg.username).slice(0,5);
    let ra = bub.querySelector('.read-avatars');
    if (!ra) {
        const meta = bub.querySelector('.bubble-meta');
        if (meta) { ra = document.createElement('div'); ra.className='read-avatars'; meta.appendChild(ra); }
    }
    if (ra) ra.innerHTML = readers.map(u=>`<span class="read-av" title="${u}" style="background:linear-gradient(135deg,${avatarColor(u)}88,${avatarColor(u)}44)">${u[0].toUpperCase()}</span>`).join('');
}

// ─── MARK READ ────────────────────────────────────────
function markVisible() {
    if (!ME) return;
    // Mark all visible messages as read
    const unread = _allMsgs.filter(m => !m.deleted && !m.read_by.includes(ME));
    unread.forEach(m => {
        fetch('/api/chat/read/'+m.id, {method:'POST'});
    });
}

// ─── SEND MESSAGE ─────────────────────────────────────
async function sendMessage() {
    if (!ME) return;
    const txt   = document.getElementById('chat-input').value.trim();
    const file  = _imgFile;
    if (!txt && !file) return;

    const fd = new FormData();
    fd.append('text', txt);
    if (file) fd.append('image', file);

    document.getElementById('chat-input').value = '';
    autoResize(document.getElementById('chat-input'));
    clearImgPreview();

    await fetch('/api/chat/send', {method:'POST', body:fd});
}

function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}
function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

// ─── IMAGE ────────────────────────────────────────────
function previewImg(input) {
    if (!input.files[0]) return;
    _imgFile = input.files[0];
    const reader = new FileReader();
    reader.onload = e => {
        document.getElementById('img-preview').src = e.target.result;
        document.getElementById('img-preview-wrap').style.display = 'inline-block';
    };
    reader.readAsDataURL(_imgFile);
}
function clearImgPreview() {
    _imgFile = null;
    document.getElementById('img-file').value = '';
    document.getElementById('img-preview').src = '';
    document.getElementById('img-preview-wrap').style.display = 'none';
}

// ─── EDIT ────────────────────────────────────────────
function startEdit(id) {
    if (_editingId) cancelEdit(_editingId);
    _editingId = id;
    const msg = _allMsgs.find(m=>m.id===id);
    if (!msg) return;
    const txtEl = document.getElementById('txt-'+id);
    if (!txtEl) return;
    txtEl.innerHTML = `
        <textarea class="edit-input" id="edit-ta-${id}" rows="2">${msg.text}</textarea>
        <div style="display:flex;gap:6px;margin-top:6px">
            <button onclick="submitEdit('${id}')" style="font-size:0.7rem;padding:3px 10px;border-radius:8px;background:rgba(168,85,247,0.3);border:1px solid rgba(168,85,247,0.5);color:#e9d5ff;cursor:pointer">Guardar</button>
            <button onclick="cancelEdit('${id}')" style="font-size:0.7rem;padding:3px 10px;border-radius:8px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);color:#9ca3af;cursor:pointer">Cancelar</button>
        </div>`;
    document.getElementById('edit-ta-'+id)?.focus();
}
async function submitEdit(id) {
    const newText = document.getElementById('edit-ta-'+id)?.value.trim();
    if (!newText) return;
    await fetch('/api/chat/edit/'+id, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:newText})});
    _editingId = null;
}
function cancelEdit(id) {
    const msg = _allMsgs.find(m=>m.id===id);
    const txtEl = document.getElementById('txt-'+id);
    if (msg && txtEl) txtEl.innerHTML = escHtml(msg.text)+(msg.edited?'<span style="font-size:0.6rem;opacity:0.45;margin-left:4px">(editado)</span>':'');
    _editingId = null;
}

// ─── DELETE ──────────────────────────────────────────
async function deleteMsg(id) {
    if (!confirm('¿Eliminar este mensaje?')) return;
    await fetch('/api/chat/delete/'+id, {method:'POST'});
}

// ─── COPY ────────────────────────────────────────────
function copyText(id) {
    const msg = _allMsgs.find(m=>m.id===id);
    if (msg?.text) navigator.clipboard.writeText(msg.text);
}

// ─── LIGHTBOX ────────────────────────────────────────
function openLightbox(src) {
    document.getElementById('lightbox-img').src = src;
    document.getElementById('lightbox').classList.add('active');
}
function closeLightbox() { document.getElementById('lightbox').classList.remove('active'); }

// ─── INIT ────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    loadMessages();
    initChatSSE();
});
</script>
{% endblock %}""")

# ─── CHAT ROUTES ────────────────────────────────────────────

@app.route('/chat')
def chat():
    maybe_saturday_cleanup()
    current_user = session.get('private_user') or (session.get('logged_in') and ADMIN_USER) or None
    return render_cached(CHAT_TEMPLATE, title='Chat', current_user=current_user, url_for=url_for, session=session, gs_reason='')

@app.route('/api/chat/messages')
def api_chat_messages():
    maybe_saturday_cleanup()
    return jsonify(load_chat())

@app.route('/api/chat/send', methods=['POST'])
def api_chat_send():
    user = session.get('private_user') or (session.get('logged_in') and ADMIN_USER)
    if not user: return jsonify({'ok':False,'error':'Sin sesión'}), 401
    text  = (request.form.get('text') or '').strip()
    image_name = None
    f = request.files.get('image')
    if f and f.filename and allowed_file(f.filename):
        ext = f.filename.rsplit('.',1)[1].lower()
        image_name = str(uuid.uuid4())[:8] + '.' + ext
        f.save(os.path.join(UPLOAD_FOLDER, image_name))
    if not text and not image_name:
        return jsonify({'ok':False,'error':'Vacío'}), 400
    msg = {
        'id':        str(uuid.uuid4()),
        'username':  user,
        'text':      text,
        'image':     image_name,
        'ts':        datetime.datetime.utcnow().isoformat(),
        'edited':    False,
        'deleted':   False,
        'read_by':   [user],
    }
    msgs = load_chat(); msgs.append(msg); save_chat(msgs)
    chat_broadcast({'type':'new','msg':msg})
    return jsonify({'ok':True})

@app.route('/api/chat/edit/<msg_id>', methods=['POST'])
def api_chat_edit(msg_id):
    user = session.get('private_user') or (session.get('logged_in') and ADMIN_USER)
    if not user: return jsonify({'ok':False}), 401
    data = request.get_json(silent=True) or {}
    new_text = (data.get('text') or '').strip()
    if not new_text: return jsonify({'ok':False,'error':'Vacío'}), 400
    msgs = load_chat()
    for m in msgs:
        if m['id'] == msg_id and m['username'] == user and not m['deleted']:
            m['text'] = new_text; m['edited'] = True; break
    else: return jsonify({'ok':False,'error':'No autorizado'}), 403
    save_chat(msgs)
    msg = next(m for m in msgs if m['id']==msg_id)
    chat_broadcast({'type':'edit','msg':msg})
    return jsonify({'ok':True})

@app.route('/api/chat/delete/<msg_id>', methods=['POST'])
def api_chat_delete(msg_id):
    user = session.get('private_user') or (session.get('logged_in') and ADMIN_USER)
    if not user: return jsonify({'ok':False}), 401
    msgs = load_chat()
    for m in msgs:
        if m['id'] == msg_id and (m['username'] == user or session.get('logged_in')):
            if m.get('image'):
                try: os.remove(os.path.join(UPLOAD_FOLDER, m['image']))
                except: pass
            m['deleted'] = True; m['text'] = ''; m['image'] = None; break
    else: return jsonify({'ok':False,'error':'No autorizado'}), 403
    save_chat(msgs)
    chat_broadcast({'type':'delete','id':msg_id})
    return jsonify({'ok':True})

@app.route('/api/chat/read/<msg_id>', methods=['POST'])
def api_chat_read(msg_id):
    user = session.get('private_user') or (session.get('logged_in') and ADMIN_USER)
    if not user: return jsonify({'ok':False}), 401
    msgs = load_chat()
    for m in msgs:
        if m['id'] == msg_id and user not in m['read_by']:
            m['read_by'].append(user); break
    save_chat(msgs)
    chat_broadcast({'type':'read','msg_id':msg_id,'username':user})
    return jsonify({'ok':True})

@app.route('/chat_uploads/<filename>')
def chat_upload_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/api/chat/stream')
def api_chat_stream():
    q = queue.Queue(maxsize=50)
    with _chat_listeners_lock:
        _chat_listeners.append(q)
    def stream():
        try:
            yield 'data: {"type":"ping"}\n\n'
            while True:
                try:
                    data = q.get(timeout=20)
                    yield f'data: {data}\n\n'
                except queue.Empty:
                    yield 'data: {"type":"ping"}\n\n'
        finally:
            with _chat_listeners_lock:
                try: _chat_listeners.remove(q)
                except ValueError: pass
    return Response(stream(), mimetype='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


if __name__ == '__main__':
    if not os.path.exists(PAGES_FILE): save_pages([])
    if not os.path.exists(EVENTS_FILE): save_events([])
    if not os.path.exists(AGENDA_FILE): save_agenda([])
    if not os.path.exists(USERS_FILE): save_users({})
    # threaded=True es esencial: el SSE necesita su propio hilo por usuario.
    # Sin esto, una conexion SSE bloquea TODA la web para el resto.
    # debug=False elimina la recompilacion de codigo en cada request.
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True, use_reloader=False)
