from flask import Flask, request, jsonify
import os
from datetime import datetime, date
import threading
import json
import db as _db

# ── Configuración según entorno ───────────────────────────────────────────────
IS_CLOUD   = os.environ.get('CLOUD_MODE', '') == '1'
PORT       = int(os.environ.get('PORT', 5001))
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    raise RuntimeError('SECRET_KEY no configurado en variables de entorno. Añádelo en Railway antes de desplegar.')
# En Railway, si DATA_DIR no está en env vars, usa /data (ruta del volumen por defecto)
_default_data_dir = '/data' if IS_CLOUD else os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.environ.get('DATA_DIR', _default_data_dir).strip()

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ── Sentry (crash reporting) ──────────────────────────────────────────────────
_SENTRY_DSN = os.environ.get('SENTRY_DSN')
if _SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration
    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.1,
        environment='production' if IS_CLOUD else 'development',
    )

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'gastos.db')
app.config['DB_PATH'] = DB_PATH
_db.set_db_path(DB_PATH)

# ── Auth layer (Google Sign-In + JWT) ─────────────────────────────────────────
from auth_routes import auth_bp, get_nido_id_from_request, requiere_auth
app.register_blueprint(auth_bp)
print(f'[config] DATA_DIR={DATA_DIR!r} DB_PATH={DB_PATH!r}')

# ── Tunnel globals (solo modo local) ─────────────────────────────────────────
TUNNEL_URL    = None
TUNNEL_STATUS = 'off'
LOCAL_IP      = '127.0.0.1'

if not IS_CLOUD:
    import socket as _socket
    import subprocess as _sp
    import re
    import time
    import webbrowser

    CF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cloudflared.exe')
    CF_URL  = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"

    def _get_local_ip():
        try:
            s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return '127.0.0.1'

    LOCAL_IP = _get_local_ip()


def get_db():
    return _db.get_db()


BACKUP_PATH = os.path.join(DATA_DIR, 'backup_datos.json')


def guardar_backup():
    """Exporta todos los datos a JSON (para GDPR y recuperación manual)."""
    try:
        conn = get_db()
        data = {
            'usuarios':    [dict(r) for r in conn.execute('SELECT * FROM usuarios').fetchall()],
            'categorias':  [dict(r) for r in conn.execute('SELECT * FROM categorias_gasto').fetchall()],
            'gastos':      [dict(r) for r in conn.execute('SELECT * FROM gastos').fetchall()],
            'ingresos':    [dict(r) for r in conn.execute('SELECT * FROM ingresos').fetchall()],
            'metas':       [dict(r) for r in conn.execute('SELECT * FROM metas_ahorro').fetchall()],
            'aportaciones':[dict(r) for r in conn.execute('SELECT * FROM aportaciones_meta').fetchall()],
            'backup_at':   datetime.now().isoformat(),
        }
        conn.close()
        if not _db.DATABASE_URL:
            with open(BACKUP_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        return data
    except Exception as e:
        print(f'[backup] Error: {e}')
        return {}


def restaurar_desde_backup():
    """Solo para SQLite local: restaura datos desde el backup JSON si la BD está vacía."""
    if _db.DATABASE_URL:
        return  # PostgreSQL persiste por sí solo
    try:
        conn = get_db()
        cnt = conn.execute('SELECT COUNT(*) as c FROM usuarios').fetchone()['c']
        conn.close()
        if cnt > 0:
            return
        SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'initial_data.json')
        source = BACKUP_PATH if os.path.exists(BACKUP_PATH) else (SEED_PATH if os.path.exists(SEED_PATH) else None)
        if not source:
            return
        with open(source, 'r', encoding='utf-8') as f:
            data = json.load(f)
        import sqlite3 as _sq
        conn2 = _sq.connect(DB_PATH)
        conn2.row_factory = _sq.Row
        conn2.execute("PRAGMA foreign_keys = OFF")
        for u in data.get('usuarios', []):
            conn2.execute("INSERT OR REPLACE INTO usuarios (id,nombre,color,emoji,pin,foto) VALUES(?,?,?,?,?,?)",
                          (u['id'], u['nombre'], u['color'], u.get('emoji', 'person'), u.get('pin'), u.get('foto')))
        for c in data.get('categorias', []):
            conn2.execute("INSERT OR REPLACE INTO categorias_gasto (id,nombre,color,icono,activa,es_ahorro) VALUES(?,?,?,?,?,?)",
                          (c['id'], c['nombre'], c['color'], c.get('icono', 'bi-tag'), c.get('activa', 1), c.get('es_ahorro', 0)))
        for g in data.get('gastos', []):
            conn2.execute("INSERT OR REPLACE INTO gastos (id,usuario_id,categoria_id,descripcion,importe,fecha,es_fijo,notas,meta_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (g['id'], g.get('usuario_id'), g.get('categoria_id'), g['descripcion'], g['importe'], g['fecha'],
                           g.get('es_fijo', 0), g.get('notas'), g.get('meta_id'), g.get('created_at')))
        for i in data.get('ingresos', []):
            conn2.execute("INSERT OR REPLACE INTO ingresos (id,usuario_id,descripcion,importe,fecha,es_nomina,notas,created_at) VALUES(?,?,?,?,?,?,?,?)",
                          (i['id'], i.get('usuario_id'), i['descripcion'], i['importe'], i['fecha'],
                           i.get('es_nomina', 0), i.get('notas'), i.get('created_at')))
        for m in data.get('metas', []):
            conn2.execute("INSERT OR REPLACE INTO metas_ahorro (id,nombre,descripcion,importe_objetivo,importe_actual,fecha_limite,completada,color,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                          (m['id'], m['nombre'], m.get('descripcion'), m['importe_objetivo'], m.get('importe_actual', 0),
                           m.get('fecha_limite'), m.get('completada', 0), m.get('color', '#4f8ef7'), m.get('created_at')))
        conn2.execute("PRAGMA foreign_keys = ON")
        conn2.commit()
        conn2.close()
    except Exception as e:
        print(f'[backup] Error al restaurar: {e}')


def init_db():
    if _db.DATABASE_URL:
        _init_db_pg()
    else:
        _init_db_sqlite()


def _init_db_pg():
    """Crea el schema en PostgreSQL (Railway/producción)."""
    conn = get_db()
    c = conn.cursor()

    ddl_statements = [
        '''CREATE TABLE IF NOT EXISTS nidos (
            id SERIAL PRIMARY KEY,
            nombre TEXT NOT NULL DEFAULT 'Mi Nido',
            plan TEXT DEFAULT 'free',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            nombre TEXT NOT NULL,
            color TEXT DEFAULT '#4f8ef7',
            emoji TEXT DEFAULT 'person',
            pin TEXT,
            foto TEXT,
            nido_id INTEGER DEFAULT 1 REFERENCES nidos(id)
        )''',
        '''CREATE TABLE IF NOT EXISTS firebase_users (
            id SERIAL PRIMARY KEY,
            firebase_uid TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            nombre TEXT,
            foto TEXT,
            nido_id INTEGER REFERENCES nidos(id),
            usuario_id INTEGER REFERENCES usuarios(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS invitaciones (
            id SERIAL PRIMARY KEY,
            nido_id INTEGER NOT NULL REFERENCES nidos(id),
            creada_por INTEGER,
            token TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            estado TEXT DEFAULT 'pendiente',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS categorias_gasto (
            id SERIAL PRIMARY KEY,
            nombre TEXT NOT NULL,
            color TEXT DEFAULT '#6c757d',
            icono TEXT DEFAULT 'bi-tag',
            activa INTEGER DEFAULT 1,
            es_ahorro INTEGER DEFAULT 0,
            nido_id INTEGER DEFAULT 1 REFERENCES nidos(id)
        )''',
        '''CREATE TABLE IF NOT EXISTS gastos (
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER REFERENCES usuarios(id),
            categoria_id INTEGER REFERENCES categorias_gasto(id),
            descripcion TEXT NOT NULL,
            importe REAL NOT NULL,
            fecha TEXT NOT NULL,
            es_fijo INTEGER DEFAULT 0,
            notas TEXT,
            meta_id INTEGER,
            nido_id INTEGER DEFAULT 1 REFERENCES nidos(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS ingresos (
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER REFERENCES usuarios(id),
            descripcion TEXT NOT NULL,
            importe REAL NOT NULL,
            fecha TEXT NOT NULL,
            es_nomina INTEGER DEFAULT 0,
            notas TEXT,
            nido_id INTEGER DEFAULT 1 REFERENCES nidos(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS metas_ahorro (
            id SERIAL PRIMARY KEY,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            importe_objetivo REAL NOT NULL,
            importe_actual REAL DEFAULT 0,
            fecha_limite TEXT,
            completada INTEGER DEFAULT 0,
            color TEXT DEFAULT '#4f8ef7',
            nido_id INTEGER DEFAULT 1 REFERENCES nidos(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS aportaciones_meta (
            id SERIAL PRIMARY KEY,
            meta_id INTEGER REFERENCES metas_ahorro(id),
            usuario_id INTEGER REFERENCES usuarios(id),
            importe REAL NOT NULL,
            fecha TEXT NOT NULL,
            notas TEXT,
            gasto_id INTEGER
        )''',
        '''CREATE TABLE IF NOT EXISTS presupuestos (
            id SERIAL PRIMARY KEY,
            categoria_id INTEGER NOT NULL REFERENCES categorias_gasto(id),
            importe_mensual REAL NOT NULL,
            mes INTEGER NOT NULL DEFAULT 0,
            anio INTEGER NOT NULL DEFAULT 0,
            nido_id INTEGER DEFAULT 1 REFERENCES nidos(id)
        )''',
        '''CREATE TABLE IF NOT EXISTS notificaciones (
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            titulo TEXT NOT NULL,
            cuerpo TEXT NOT NULL,
            leida INTEGER DEFAULT 0,
            nido_id INTEGER DEFAULT 1 REFERENCES nidos(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )''',
    ]

    for stmt in ddl_statements:
        c.execute(stmt)

    conn.commit()

    # Nido por defecto y sincronizar secuencia
    conn.execute("INSERT INTO nidos (id, nombre) VALUES (1, 'Mi Nido') ON CONFLICT (id) DO NOTHING")
    conn.execute("SELECT setval(pg_get_serial_sequence('nidos','id'), GREATEST((SELECT MAX(id) FROM nidos), 1))")
    conn.commit()

    _seed_default_data()
    conn.close()


def _init_db_sqlite():
    """Crea el schema en SQLite (desarrollo local)."""
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(DB_PATH)
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY,
        nombre TEXT NOT NULL,
        color TEXT DEFAULT "#4f8ef7",
        emoji TEXT DEFAULT "person",
        pin TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS categorias_gasto (
        id INTEGER PRIMARY KEY,
        nombre TEXT NOT NULL,
        color TEXT DEFAULT "#6c757d",
        icono TEXT DEFAULT "bi-tag",
        activa INTEGER DEFAULT 1,
        es_ahorro INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS gastos (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER,
        categoria_id INTEGER,
        descripcion TEXT NOT NULL,
        importe REAL NOT NULL,
        fecha TEXT NOT NULL,
        es_fijo INTEGER DEFAULT 0,
        notas TEXT,
        meta_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
        FOREIGN KEY (categoria_id) REFERENCES categorias_gasto(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS ingresos (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER,
        descripcion TEXT NOT NULL,
        importe REAL NOT NULL,
        fecha TEXT NOT NULL,
        es_nomina INTEGER DEFAULT 0,
        notas TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS metas_ahorro (
        id INTEGER PRIMARY KEY,
        nombre TEXT NOT NULL,
        descripcion TEXT,
        importe_objetivo REAL NOT NULL,
        importe_actual REAL DEFAULT 0,
        fecha_limite TEXT,
        completada INTEGER DEFAULT 0,
        color TEXT DEFAULT "#4f8ef7",
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS aportaciones_meta (
        id INTEGER PRIMARY KEY,
        meta_id INTEGER,
        usuario_id INTEGER,
        importe REAL NOT NULL,
        fecha TEXT NOT NULL,
        notas TEXT,
        gasto_id INTEGER
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS notificaciones (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL,
        titulo TEXT NOT NULL,
        cuerpo TEXT NOT NULL,
        leida INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS presupuestos (
        id INTEGER PRIMARY KEY,
        categoria_id INTEGER NOT NULL UNIQUE,
        importe_mensual REAL NOT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS nidos (
        id INTEGER PRIMARY KEY,
        nombre TEXT NOT NULL DEFAULT "Mi Nido",
        plan TEXT DEFAULT "free",
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS firebase_users (
        id INTEGER PRIMARY KEY,
        firebase_uid TEXT UNIQUE NOT NULL,
        email TEXT NOT NULL,
        nombre TEXT,
        foto TEXT,
        nido_id INTEGER,
        usuario_id INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS invitaciones (
        id INTEGER PRIMARY KEY,
        nido_id INTEGER NOT NULL,
        creada_por INTEGER,
        token TEXT UNIQUE NOT NULL,
        expires_at TEXT NOT NULL,
        estado TEXT DEFAULT "pendiente",
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute("INSERT OR IGNORE INTO nidos (id, nombre) VALUES (1, 'Mi Nido')")

    for migration in [
        "ALTER TABLE usuarios ADD COLUMN pin TEXT",
        "ALTER TABLE usuarios ADD COLUMN foto TEXT",
        "ALTER TABLE categorias_gasto ADD COLUMN es_ahorro INTEGER DEFAULT 0",
        "ALTER TABLE gastos ADD COLUMN meta_id INTEGER",
        "ALTER TABLE aportaciones_meta ADD COLUMN gasto_id INTEGER",
        "ALTER TABLE presupuestos ADD COLUMN mes INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE presupuestos ADD COLUMN anio INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE usuarios ADD COLUMN nido_id INTEGER DEFAULT 1",
        "ALTER TABLE gastos ADD COLUMN nido_id INTEGER DEFAULT 1",
        "ALTER TABLE ingresos ADD COLUMN nido_id INTEGER DEFAULT 1",
        "ALTER TABLE metas_ahorro ADD COLUMN nido_id INTEGER DEFAULT 1",
        "ALTER TABLE categorias_gasto ADD COLUMN nido_id INTEGER DEFAULT 1",
        "ALTER TABLE presupuestos ADD COLUMN nido_id INTEGER DEFAULT 1",
        "ALTER TABLE notificaciones ADD COLUMN nido_id INTEGER DEFAULT 1",
        "ALTER TABLE ingresos ADD COLUMN categoria TEXT DEFAULT 'Salario'",
    ]:
        try:
            c.execute(migration)
        except Exception:
            pass

    hoy_mig = date.today()
    c.execute("UPDATE presupuestos SET mes=?, anio=? WHERE mes=0 AND anio=0",
              (hoy_mig.month, hoy_mig.year))
    conn.commit()
    conn.close()

    restaurar_desde_backup()
    _seed_default_data()


def _seed_default_data():
    """Inserta usuarios y categorías por defecto si la BD está vacía."""
    conn = get_db()
    cnt = conn.execute("SELECT COUNT(*) as cnt FROM usuarios").fetchone()['cnt']
    if cnt == 0:
        conn.execute("INSERT INTO usuarios (nombre, color, emoji) VALUES (?, ?, ?)",
                     ('Persona 1', '#4f8ef7', 'person'))

    cnt_cats = conn.execute("SELECT COUNT(*) as cnt FROM categorias_gasto").fetchone()['cnt']
    if cnt_cats == 0:
        cats = [
            ('Alimentacion', '#28a745', 'bi-basket2', 0),
            ('Ocio',         '#ffc107', 'bi-controller', 0),
            ('Coche',        '#dc3545', 'bi-car-front', 0),
            ('Servicios',    '#17a2b8', 'bi-house', 0),
            ('Salud',        '#e83e8c', 'bi-heart-pulse', 0),
            ('Ropa',         '#6f42c1', 'bi-bag', 0),
            ('Viajes',       '#fd7e14', 'bi-airplane', 0),
            ('Suscripciones', '#20c997', 'bi-phone', 0),
            ('Ahorro',       '#0d6efd', 'bi-piggy-bank', 1),
            ('Otros',        '#6c757d', 'bi-tres-dots', 0),
        ]
        for cat in cats:
            conn.execute(
                "INSERT INTO categorias_gasto (nombre, color, icono, es_ahorro) VALUES (?, ?, ?, ?)",
                cat)
    else:
        row = conn.execute("SELECT id FROM categorias_gasto WHERE es_ahorro=1").fetchone()
        if not row:
            conn.execute(
                "INSERT INTO categorias_gasto (nombre, color, icono, es_ahorro) VALUES (?, ?, ?, 1)",
                ('Ahorro', '#0d6efd', 'bi-piggy-bank'))

    conn.commit()
    conn.close()
    guardar_backup()


# ── Registrar blueprints ──────────────────────────────────────────────────────
from blueprints import (
    gastos_bp, ingresos_bp, metas_bp, presupuestos_bp,
    dashboard_bp, ia_bp, usuarios_bp, datos_bp, paginas_bp,
)

for bp in (gastos_bp, ingresos_bp, metas_bp, presupuestos_bp,
           dashboard_bp, ia_bp, usuarios_bp, datos_bp, paginas_bp):
    app.register_blueprint(bp)

# ── Exponer guardar_backup y rutas al config de la app (para blueprints) ─────
app.config['guardar_backup'] = guardar_backup
app.config['BACKUP_PATH']    = BACKUP_PATH
app.config['TUNNEL_URL']     = TUNNEL_URL
app.config['TUNNEL_STATUS']  = TUNNEL_STATUS
app.config['LOCAL_IP']       = LOCAL_IP

# ── Auth guard: exige JWT en /api/ (excepto endpoints públicos) ───────────────
_API_PUBLICA = frozenset({
    'paginas.api_ping',
    'paginas.api_version',
    'paginas.api_tunnel',
    'usuarios.api_foto_serve',
    'paginas.api_admin_reset',
})

@app.before_request
def _verificar_auth_api():
    """Exige JWT válido en todos los endpoints /api/ excepto los públicos."""
    if not request.path.startswith('/api/'):
        return
    endpoint = request.endpoint or ''
    if endpoint.startswith('auth.') or endpoint in _API_PUBLICA:
        return
    return requiere_auth(lambda: None)()


# ── Tunnel local (solo NO cloud) ──────────────────────────────────────────────
if not IS_CLOUD:
    def _descargar_cloudflared():
        print("[tunnel] Descargando cloudflared (~63 MB)...")
        try:
            cmd = (
                f'[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; '
                f'$wc = New-Object System.Net.WebClient; '
                f'$wc.DownloadFile("{CF_URL}", "{CF_PATH}")'
            )
            _sp.run(
                ['powershell', '-NonInteractive', '-Command', cmd],
                timeout=180, check=True,
                creationflags=_sp.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            size = os.path.getsize(CF_PATH)
            if size < 10_000_000:
                raise RuntimeError(f"Archivo demasiado pequeño: {size} bytes")
            print(f"[tunnel] Descarga completada ({size // 1_000_000} MB).")
            return True
        except Exception as e:
            print(f"[tunnel] Error al descargar: {e}")
            try:
                os.remove(CF_PATH)
            except Exception:
                pass
            return False

    def _lanzar_proceso_cf():
        flags = _sp.CREATE_NO_WINDOW if os.name == 'nt' else 0
        return _sp.Popen(
            [CF_PATH, 'tunnel', '--url', f'http://localhost:{PORT}', '--no-autoupdate'],
            stdout=_sp.PIPE, stderr=_sp.STDOUT,
            creationflags=flags, bufsize=0
        )

    def _tunnel_worker():
        global TUNNEL_URL, TUNNEL_STATUS
        if not os.path.exists(CF_PATH) or os.path.getsize(CF_PATH) < 10_000_000:
            TUNNEL_STATUS = 'downloading'
            if not _descargar_cloudflared():
                TUNNEL_STATUS = 'error'
                return
        while True:
            TUNNEL_STATUS = 'connecting'
            TUNNEL_URL_local = None
            print("[tunnel] Iniciando tunel publico...")
            try:
                proc = _lanzar_proceso_cf()
                deadline = time.time() + 90
                while time.time() < deadline:
                    raw = proc.stdout.readline()
                    if not raw:
                        break
                    line = raw.decode('utf-8', errors='ignore').strip()
                    if line:
                        print(f"[tunnel] {line}")
                    m = re.search(r'https://[\w.-]+\.trycloudflare\.com', line)
                    if m:
                        TUNNEL_URL_local = m.group(0)
                        break
                if not TUNNEL_URL_local:
                    proc.kill()
                    TUNNEL_STATUS = 'error'
                    print("[tunnel] No se encontró URL en 90s. Reintentando en 30s...")
                    time.sleep(30)
                    continue
                global TUNNEL_URL
                TUNNEL_URL = TUNNEL_URL_local
                TUNNEL_STATUS = 'ready'
                # Sync config so blueprints see updated value
                app.config['TUNNEL_URL']   = TUNNEL_URL
                app.config['TUNNEL_STATUS'] = TUNNEL_STATUS
                print(f"[tunnel] URL lista: {TUNNEL_URL}")
                try:
                    while proc.poll() is None:
                        proc.stdout.read(4096)
                except Exception:
                    pass
                print("[tunnel] Proceso terminado. Reiniciando en 5s...")
                TUNNEL_STATUS = 'connecting'
                TUNNEL_URL = None
                app.config['TUNNEL_URL']   = TUNNEL_URL
                app.config['TUNNEL_STATUS'] = TUNNEL_STATUS
                time.sleep(5)
            except Exception as e:
                print(f"[tunnel] Error: {e}")
                TUNNEL_STATUS = 'error'
                app.config['TUNNEL_STATUS'] = TUNNEL_STATUS
                time.sleep(30)

    def start_tunnel():
        threading.Thread(target=_tunnel_worker, daemon=True).start()

    def _udp_broadcast_worker():
        import socket as _sock
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        s.setsockopt(_sock.SOL_SOCKET, _sock.SO_BROADCAST, 1)
        s.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
        while True:
            try:
                msg = json.dumps({
                    'app':    'gestorgastos',
                    'host':   LOCAL_IP,
                    'port':   PORT,
                    'tunnel': TUNNEL_URL or ''
                }).encode('utf-8')
                s.sendto(msg, ('<broadcast>', 5002))
            except Exception:
                pass
            time.sleep(3)

    def start_discovery_broadcast():
        threading.Thread(target=_udp_broadcast_worker, daemon=True).start()


if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        init_db()
    except Exception as e:
        print(f'[startup] init_db warning: {e}')
    if not IS_CLOUD:
        start_tunnel()
        start_discovery_broadcast()
        webbrowser.open(f'http://localhost:{PORT}')
    app.run(debug=False, host='0.0.0.0', port=PORT)
