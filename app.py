from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
import os
from datetime import datetime, date
import threading
import re
import time
import json

# ── Configuración según entorno ───────────────────────────────────────────────
IS_CLOUD   = os.environ.get('CLOUD_MODE', '') == '1'
PORT       = int(os.environ.get('PORT', 5001))
SECRET_KEY = os.environ.get('SECRET_KEY', 'gestor_gastos_pareja_2024_secret')
# En Railway, si DATA_DIR no está en env vars, usa /data (ruta del volumen por defecto)
_default_data_dir = '/data' if IS_CLOUD else os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.environ.get('DATA_DIR', _default_data_dir).strip()

app = Flask(__name__)
app.secret_key = SECRET_KEY

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'gastos.db')
print(f'[config] DATA_DIR={DATA_DIR!r} DB_PATH={DB_PATH!r}')

NOMBRES_MESES = ['Enero','Febrero','Marzo','Abril','Mayo','Junio',
                 'Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre']

# Solo en modo local (PC Windows)
TUNNEL_URL    = None
TUNNEL_STATUS = 'off'
LOCAL_IP      = '127.0.0.1'

if not IS_CLOUD:
    import socket as _socket
    import subprocess as _sp
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


BACKUP_PATH = os.path.join(DATA_DIR, 'backup_datos.json')

def guardar_backup():
    """Guarda un JSON con todos los datos. Se llama al arrancar y por endpoint."""
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
        with open(BACKUP_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'[backup] Guardado en {BACKUP_PATH}')
    except Exception as e:
        print(f'[backup] Error al guardar: {e}')

SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'initial_data.json')

def restaurar_desde_backup():
    """Si la BD está vacía, intenta restaurar: 1) backup en volumen, 2) seed del repo."""
    try:
        conn = get_db()
        cnt = conn.execute('SELECT COUNT(*) as c FROM usuarios').fetchone()['c']
        conn.close()
        if cnt > 0:
            return  # Hay datos, no restaurar
        # 1. Backup en volumen persistente
        if os.path.exists(BACKUP_PATH):
            source = BACKUP_PATH
        # 2. Seed embebido en el repo (fallback cuando no hay volumen)
        elif os.path.exists(SEED_PATH):
            source = SEED_PATH
        else:
            return
        print(f'[backup] Base de datos vacía. Restaurando desde {source}...')
        with open(source, 'r', encoding='utf-8') as f:
            data = json.load(f)
        conn = get_db()
        conn.execute("PRAGMA foreign_keys = OFF")
        for u in data.get('usuarios', []):
            conn.execute("INSERT OR REPLACE INTO usuarios (id,nombre,color,emoji,pin,foto) VALUES(?,?,?,?,?,?)",
                (u['id'],u['nombre'],u['color'],u.get('emoji','person'),u.get('pin'),u.get('foto')))
        for c in data.get('categorias', []):
            conn.execute("INSERT OR REPLACE INTO categorias_gasto (id,nombre,color,icono,activa,es_ahorro) VALUES(?,?,?,?,?,?)",
                (c['id'],c['nombre'],c['color'],c.get('icono','bi-tag'),c.get('activa',1),c.get('es_ahorro',0)))
        for g in data.get('gastos', []):
            conn.execute("INSERT OR REPLACE INTO gastos (id,usuario_id,categoria_id,descripcion,importe,fecha,es_fijo,notas,meta_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (g['id'],g.get('usuario_id'),g.get('categoria_id'),g['descripcion'],g['importe'],g['fecha'],g.get('es_fijo',0),g.get('notas'),g.get('meta_id'),g.get('created_at')))
        for i in data.get('ingresos', []):
            conn.execute("INSERT OR REPLACE INTO ingresos (id,usuario_id,descripcion,importe,fecha,es_nomina,notas,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (i['id'],i.get('usuario_id'),i['descripcion'],i['importe'],i['fecha'],i.get('es_nomina',0),i.get('notas'),i.get('created_at')))
        for m in data.get('metas', []):
            conn.execute("INSERT OR REPLACE INTO metas_ahorro (id,nombre,descripcion,importe_objetivo,importe_actual,fecha_limite,completada,color,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (m['id'],m['nombre'],m.get('descripcion'),m['importe_objetivo'],m.get('importe_actual',0),m.get('fecha_limite'),m.get('completada',0),m.get('color','#4f8ef7'),m.get('created_at')))
        for a in data.get('aportaciones', []):
            conn.execute("INSERT OR REPLACE INTO aportaciones_meta (id,meta_id,usuario_id,importe,fecha,notas,gasto_id) VALUES(?,?,?,?,?,?,?)",
                (a['id'],a['meta_id'],a.get('usuario_id'),a['importe'],a['fecha'],a.get('notas'),a.get('gasto_id')))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
        conn.close()
        print(f'[backup] Restauración completada desde {BACKUP_PATH}')
    except Exception as e:
        print(f'[backup] Error al restaurar: {e}')


def init_db():
    conn = get_db()
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
        gasto_id INTEGER,
        FOREIGN KEY (meta_id) REFERENCES metas_ahorro(id),
        FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS notificaciones (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL,
        titulo TEXT NOT NULL,
        cuerpo TEXT NOT NULL,
        leida INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS presupuestos (
        id INTEGER PRIMARY KEY,
        categoria_id INTEGER NOT NULL UNIQUE,
        importe_mensual REAL NOT NULL,
        FOREIGN KEY (categoria_id) REFERENCES categorias_gasto(id)
    )''')

    # Migraciones para bases de datos existentes
    for migration in [
        "ALTER TABLE usuarios ADD COLUMN pin TEXT",
        "ALTER TABLE usuarios ADD COLUMN foto TEXT",
        "ALTER TABLE categorias_gasto ADD COLUMN es_ahorro INTEGER DEFAULT 0",
        "ALTER TABLE gastos ADD COLUMN meta_id INTEGER",
        "ALTER TABLE aportaciones_meta ADD COLUMN gasto_id INTEGER",
    ]:
        try:
            c.execute(migration)
        except Exception:
            pass

    conn.commit()
    conn.close()

    # Intentar restaurar desde backup ANTES de insertar datos por defecto
    restaurar_desde_backup()

    # Solo insertar datos por defecto si la BD sigue vacía (sin backup)
    conn2 = get_db()
    c2 = conn2.cursor()

    c2.execute("SELECT COUNT(*) as cnt FROM usuarios")
    if c2.fetchone()['cnt'] == 0:
        c2.execute("INSERT INTO usuarios (nombre, color, emoji) VALUES (?, ?, ?)", ('Persona 1', '#4f8ef7', 'person'))
        c2.execute("INSERT INTO usuarios (nombre, color, emoji) VALUES (?, ?, ?)", ('Persona 2', '#e74c8e', 'person-fill'))

    c2.execute("SELECT COUNT(*) as cnt FROM categorias_gasto")
    if c2.fetchone()['cnt'] == 0:
        cats = [
            ('Alimentacion', '#28a745', 'bi-basket2', 0),
            ('Ocio', '#ffc107', 'bi-controller', 0),
            ('Coche', '#dc3545', 'bi-car-front', 0),
            ('Servicios', '#17a2b8', 'bi-house', 0),
            ('Salud', '#e83e8c', 'bi-heart-pulse', 0),
            ('Ropa', '#6f42c1', 'bi-bag', 0),
            ('Viajes', '#fd7e14', 'bi-airplane', 0),
            ('Suscripciones', '#20c997', 'bi-phone', 0),
            ('Ahorro', '#0d6efd', 'bi-piggy-bank', 1),
            ('Otros', '#6c757d', 'bi-tres-dots', 0),
        ]
        for cat in cats:
            c2.execute("INSERT INTO categorias_gasto (nombre, color, icono, es_ahorro) VALUES (?, ?, ?, ?)", cat)
    else:
        c2.execute("SELECT id FROM categorias_gasto WHERE es_ahorro=1")
        if not c2.fetchone():
            c2.execute("INSERT INTO categorias_gasto (nombre, color, icono, es_ahorro) VALUES (?, ?, ?, 1)",
                       ('Ahorro', '#0d6efd', 'bi-piggy-bank'))

    conn2.commit()
    conn2.close()

    # Guardar backup actualizado
    guardar_backup()


def auto_generar_mes(mes_str):
    """Genera gastos fijos e ingresos nomina para el mes indicado si no existen."""
    primera_fecha = mes_str + "-01"
    conn = get_db()
    c = conn.cursor()
    changed = False

    # ── Gastos fijos ─────────────────────────────────────────────────────────
    # Obtener grupos únicos de gastos fijos
    c.execute("""
        SELECT DISTINCT descripcion, COALESCE(categoria_id,-1) AS cat, COALESCE(usuario_id,-1) AS uid
        FROM gastos WHERE es_fijo=1
    """)
    grupos = c.fetchall()
    for g in grupos:
        # Importe del registro más reciente de este grupo
        row = c.execute("""
            SELECT categoria_id, usuario_id, importe, notas FROM gastos
            WHERE es_fijo=1
              AND descripcion=?
              AND COALESCE(categoria_id,-1)=?
              AND COALESCE(usuario_id,-1)=?
            ORDER BY fecha DESC LIMIT 1
        """, (g['descripcion'], g['cat'], g['uid'])).fetchone()
        if not row:
            continue
        # ¿Ya existe para este mes?
        existe = c.execute("""
            SELECT 1 FROM gastos
            WHERE es_fijo=1 AND descripcion=?
              AND COALESCE(categoria_id,-1)=?
              AND COALESCE(usuario_id,-1)=?
              AND strftime('%Y-%m', fecha)=?
        """, (g['descripcion'], g['cat'], g['uid'], mes_str)).fetchone()
        if not existe:
            c.execute("""
                INSERT INTO gastos (usuario_id, categoria_id, descripcion, importe, fecha, es_fijo, notas)
                VALUES (?, ?, ?, ?, ?, 1, ?)
            """, (row['usuario_id'], row['categoria_id'], g['descripcion'],
                  row['importe'], primera_fecha, row['notas']))
            changed = True

    # ── Ingresos nómina ───────────────────────────────────────────────────────
    c.execute("""
        SELECT DISTINCT descripcion, usuario_id FROM ingresos WHERE es_nomina=1
    """)
    grupos_ing = c.fetchall()
    for n in grupos_ing:
        row = c.execute("""
            SELECT importe, notas FROM ingresos
            WHERE es_nomina=1 AND descripcion=? AND usuario_id=?
            ORDER BY fecha DESC LIMIT 1
        """, (n['descripcion'], n['usuario_id'])).fetchone()
        if not row:
            continue
        existe = c.execute("""
            SELECT 1 FROM ingresos
            WHERE es_nomina=1 AND descripcion=? AND usuario_id=?
              AND strftime('%Y-%m', fecha)=?
        """, (n['descripcion'], n['usuario_id'], mes_str)).fetchone()
        if not existe:
            c.execute("""
                INSERT INTO ingresos (usuario_id, descripcion, importe, fecha, es_nomina, notas)
                VALUES (?, ?, ?, ?, 1, ?)
            """, (n['usuario_id'], n['descripcion'], row['importe'], primera_fecha, row['notas']))
            changed = True

    if changed:
        conn.commit()
    conn.close()


def fecha_desde_form(valor_form):
    """Convierte YYYY-MM del input[type=month] a YYYY-MM-01 para la BD."""
    if not valor_form:
        hoy = date.today()
        return f"{hoy.year}-{hoy.month:02d}-01"
    if len(valor_form) == 7:
        return valor_form + "-01"
    return valor_form


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
            try: os.remove(CF_PATH)
            except Exception: pass
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
                print(f"[tunnel] URL lista: {TUNNEL_URL}")
                try:
                    while proc.poll() is None:
                        proc.stdout.read(4096)
                except Exception:
                    pass
                print("[tunnel] Proceso terminado. Reiniciando en 5s...")
                TUNNEL_STATUS = 'connecting'
                TUNNEL_URL = None
                time.sleep(5)
            except Exception as e:
                print(f"[tunnel] Error: {e}")
                TUNNEL_STATUS = 'error'
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
                    'app': 'gestorgastos',
                    'host': LOCAL_IP,
                    'port': PORT,
                    'tunnel': TUNNEL_URL or ''
                }).encode('utf-8')
                s.sendto(msg, ('<broadcast>', 5002))
            except Exception:
                pass
            time.sleep(3)

    def start_discovery_broadcast():
        threading.Thread(target=_udp_broadcast_worker, daemon=True).start()


@app.context_processor
def inject_globals():
    usuario_id = session.get('usuario_id')
    usuario_actual = None
    todos_usuarios = []
    if usuario_id:
        conn = get_db()
        usuario_actual = conn.execute("SELECT * FROM usuarios WHERE id=?", (usuario_id,)).fetchone()
        todos_usuarios = conn.execute("SELECT * FROM usuarios").fetchall()
        conn.close()
    hoy = date.today()
    return dict(
        usuario_actual=usuario_actual,
        todos_usuarios=todos_usuarios,
        mes_actual=hoy.month,
        anio_actual=hoy.year,
        nombres_meses=NOMBRES_MESES,
        tunnel_url=TUNNEL_URL,
        tunnel_status=TUNNEL_STATUS,
        local_ip=LOCAL_IP,
    )


# ── API ──────────────────────────────────────────────────────
@app.route('/api/ping')
def api_ping():
    return jsonify({'app': 'gestorgastos', 'status': 'ok'})

@app.route('/api/backup', methods=['GET'])
def api_backup_descargar():
    """Descarga el backup actual como JSON."""
    guardar_backup()
    if not os.path.exists(BACKUP_PATH):
        return jsonify({'error': 'no hay backup'}), 404
    with open(BACKUP_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return jsonify(data)

@app.route('/api/backup', methods=['POST'])
def api_backup_forzar():
    guardar_backup()
    size = os.path.getsize(BACKUP_PATH) if os.path.exists(BACKUP_PATH) else 0
    return jsonify({'ok': True, 'size_bytes': size, 'path': BACKUP_PATH})


@app.route('/usuarios/<int:uid>/foto', methods=['POST'])
def usuario_foto(uid):
    if 'usuario_id' not in session or int(session['usuario_id']) != uid:
        flash('No autorizado.', 'danger')
        return redirect(url_for('configuracion'))
    foto = request.files.get('foto')
    if foto and foto.filename:
        ext = foto.filename.rsplit('.', 1)[-1].lower()
        if ext not in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
            ext = 'jpg'
        fotos_dir = os.path.join(os.path.dirname(__file__), 'static', 'fotos')
        os.makedirs(fotos_dir, exist_ok=True)
        nombre = f'usuario_{uid}.{ext}'
        foto.save(os.path.join(fotos_dir, nombre))
        conn = get_db()
        conn.execute("UPDATE usuarios SET foto=? WHERE id=?", (f'fotos/{nombre}', uid))
        conn.commit()
        conn.close()
        flash('Foto de perfil actualizada.', 'success')
    return redirect(url_for('configuracion'))

@app.route('/api/tunnel')
def api_tunnel():
    return jsonify({'url': TUNNEL_URL, 'status': TUNNEL_STATUS})


# ── API JSON para Flutter ─────────────────────────────────────────────────────

@app.route('/api/usuarios')
def api_usuarios():
    conn = get_db()
    rows = conn.execute("SELECT id, nombre, color, emoji, foto, pin FROM usuarios").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/usuarios/<int:uid>', methods=['PUT'])
def api_usuario_editar(uid):
    d = request.get_json()
    conn = get_db()
    conn.execute("UPDATE usuarios SET nombre=?, color=?, emoji=? WHERE id=?", (
        d.get('nombre','').strip(), d.get('color','#4f8ef7'), d.get('emoji','person'), uid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/usuarios/<int:uid>/pin', methods=['PUT'])
def api_usuario_pin(uid):
    d = request.get_json()
    pin = d.get('pin', '').strip()
    conn = get_db()
    conn.execute("UPDATE usuarios SET pin=? WHERE id=?", (pin if pin else None, uid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/login', methods=['POST'])
def api_login():
    d = request.get_json()
    uid = d.get('usuario_id')
    pin = d.get('pin', '')
    conn = get_db()
    u = conn.execute("SELECT pin FROM usuarios WHERE id=?", (uid,)).fetchone()
    conn.close()
    if u is None:
        return jsonify({'ok': False, 'error': 'Usuario no encontrado'}), 404
    if u['pin'] and u['pin'] != pin:
        return jsonify({'ok': False, 'error': 'PIN incorrecto'}), 401
    return jsonify({'ok': True})

@app.route('/api/categorias')
def api_categorias():
    conn = get_db()
    rows = conn.execute("SELECT * FROM categorias_gasto ORDER BY nombre").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/categorias', methods=['POST'])
def api_categoria_crear():
    d = request.get_json()
    conn = get_db()
    cur = conn.execute("INSERT INTO categorias_gasto (nombre, color, icono) VALUES(?,?,?)", (
        d.get('nombre','').strip(), d.get('color','#6c757d'), d.get('icono','bi-tag')))
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return jsonify({'id': cid}), 201

@app.route('/api/categorias/<int:cid>', methods=['DELETE'])
def api_categoria_eliminar(cid):
    conn = get_db()
    cat = conn.execute("SELECT es_ahorro FROM categorias_gasto WHERE id=?", (cid,)).fetchone()
    if cat and cat['es_ahorro']:
        return jsonify({'ok': False, 'error': 'No se puede eliminar la categoría de Ahorro'}), 400
    cnt = conn.execute("SELECT COUNT(*) as c FROM gastos WHERE categoria_id=?", (cid,)).fetchone()['c']
    if cnt > 0:
        return jsonify({'ok': False, 'error': f'Hay {cnt} gasto(s) con esta categoría'}), 400
    conn.execute("DELETE FROM categorias_gasto WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/gastos')
def api_gastos():
    mes = request.args.get('mes', date.today().month, type=int)
    anio = request.args.get('anio', date.today().year, type=int)
    cat_filtro = request.args.get('cat', '')
    usr_filtro = request.args.get('usr', '')
    fijo_filtro = request.args.get('fijo', '')
    mes_str = f"{anio}-{mes:02d}"
    auto_generar_mes(mes_str)
    conn = get_db()
    q = """SELECT g.*, u.nombre as unombre, u.color as ucolor, u.emoji as uemoji,
               c.nombre as cnombre, c.color as ccolor, c.icono as cicono
        FROM gastos g
        LEFT JOIN usuarios u ON g.usuario_id=u.id
        LEFT JOIN categorias_gasto c ON g.categoria_id=c.id
        WHERE strftime('%Y-%m', g.fecha)=?"""
    params = [mes_str]
    if cat_filtro:
        q += " AND g.categoria_id=?"; params.append(cat_filtro)
    if usr_filtro:
        q += " AND g.usuario_id=?"; params.append(usr_filtro)
    if fijo_filtro == '1':
        q += " AND g.es_fijo=1"
    elif fijo_filtro == '0':
        q += " AND g.es_fijo=0"
    q += " ORDER BY g.fecha DESC, g.created_at DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/gastos', methods=['POST'])
def api_gasto_crear():
    d = request.get_json()
    importe = float(d.get('importe', 0))
    meta_id = d.get('meta_id')
    usuario_id = d.get('usuario_id')
    categoria_id = d.get('categoria_id')
    conn = get_db()

    cur = conn.execute("""INSERT INTO gastos
        (usuario_id, categoria_id, descripcion, importe, fecha, es_fijo, notas, meta_id)
        VALUES(?,?,?,?,?,?,?,?)""", (
        usuario_id, categoria_id,
        d.get('descripcion', '').strip(),
        importe,
        d.get('fecha'), 1 if d.get('es_fijo') else 0,
        d.get('notas', '').strip(), meta_id
    ))
    gid = cur.lastrowid

    # Si la categoría es ahorro y hay meta vinculada, actualizar meta
    if categoria_id and meta_id:
        cat = conn.execute("SELECT es_ahorro FROM categorias_gasto WHERE id=?", (categoria_id,)).fetchone()
        if cat and cat['es_ahorro']:
            meta = conn.execute("SELECT * FROM metas_ahorro WHERE id=?", (meta_id,)).fetchone()
            if meta:
                nuevo_total = meta['importe_actual'] + importe
                completada = 1 if nuevo_total >= meta['importe_objetivo'] else 0
                conn.execute("UPDATE metas_ahorro SET importe_actual=?, completada=? WHERE id=?",
                             (nuevo_total, completada, meta_id))
                conn.execute("""INSERT INTO aportaciones_meta (meta_id, usuario_id, importe, fecha, notas, gasto_id)
                    VALUES(?,?,?,?,?,?)""", (meta_id, usuario_id, importe, d.get('fecha'), d.get('notas',''), gid))

    conn.commit()

    # Notificar a los demás usuarios en transacción separada (no bloquea la creación)
    try:
        desc = d.get('descripcion', '').strip()
        otros = conn.execute("SELECT id FROM usuarios WHERE id != ?", (usuario_id or -1,)).fetchall()
        for u in otros:
            conn.execute("""INSERT INTO notificaciones (usuario_id, titulo, cuerpo)
                VALUES(?,?,?)""", (u['id'], 'Nuevo gasto añadido', f'{desc}: {importe:.2f}€'))
        conn.commit()
    except Exception:
        pass

    conn.close()

    # Actualizar backup tras cambio de datos
    threading.Thread(target=guardar_backup, daemon=True).start()

    return jsonify({'id': gid}), 201

@app.route('/api/gastos/<int:gid>', methods=['PUT'])
def api_gasto_editar(gid):
    d = request.get_json()
    conn = get_db()
    conn.execute("""UPDATE gastos SET usuario_id=?, categoria_id=?, descripcion=?,
        importe=?, fecha=?, es_fijo=?, notas=? WHERE id=?""", (
        d.get('usuario_id'), d.get('categoria_id'),
        d.get('descripcion', '').strip(),
        float(d.get('importe', 0)),
        d.get('fecha'), 1 if d.get('es_fijo') else 0,
        d.get('notas', '').strip(), gid
    ))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/gastos/<int:gid>', methods=['DELETE'])
def api_gasto_eliminar(gid):
    conn = get_db()
    conn.execute("DELETE FROM gastos WHERE id=?", (gid,))
    conn.commit()
    conn.close()
    threading.Thread(target=guardar_backup, daemon=True).start()
    return jsonify({'ok': True})

@app.route('/api/ingresos')
def api_ingresos():
    mes = request.args.get('mes', date.today().month, type=int)
    anio = request.args.get('anio', date.today().year, type=int)
    mes_str = f"{anio}-{mes:02d}"
    auto_generar_mes(mes_str)
    conn = get_db()
    rows = conn.execute("""
        SELECT i.*, u.nombre as unombre, u.color as ucolor, u.emoji as uemoji
        FROM ingresos i
        LEFT JOIN usuarios u ON i.usuario_id=u.id
        WHERE strftime('%Y-%m', i.fecha)=?
        ORDER BY i.fecha DESC, i.created_at DESC
    """, (mes_str,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/ingresos', methods=['POST'])
def api_ingreso_crear():
    d = request.get_json()
    conn = get_db()
    cur = conn.execute("""INSERT INTO ingresos
        (usuario_id, descripcion, importe, fecha, es_nomina, notas)
        VALUES(?,?,?,?,?,?)""", (
        d.get('usuario_id'),
        d.get('descripcion', '').strip(),
        float(d.get('importe', 0)),
        d.get('fecha'), 1 if d.get('es_nomina') else 0,
        d.get('notas', '').strip()
    ))
    conn.commit()
    iid = cur.lastrowid
    conn.close()
    return jsonify({'id': iid}), 201

@app.route('/api/ingresos/<int:iid>', methods=['PUT'])
def api_ingreso_editar(iid):
    d = request.get_json()
    conn = get_db()
    conn.execute("""UPDATE ingresos SET usuario_id=?, descripcion=?,
        importe=?, fecha=?, es_nomina=?, notas=? WHERE id=?""", (
        d.get('usuario_id'),
        d.get('descripcion', '').strip(),
        float(d.get('importe', 0)),
        d.get('fecha'), 1 if d.get('es_nomina') else 0,
        d.get('notas', '').strip(), iid
    ))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/ingresos/<int:iid>', methods=['DELETE'])
def api_ingreso_eliminar(iid):
    conn = get_db()
    conn.execute("DELETE FROM ingresos WHERE id=?", (iid,))
    conn.commit()
    conn.close()
    threading.Thread(target=guardar_backup, daemon=True).start()
    return jsonify({'ok': True})

@app.route('/api/dashboard')
def api_dashboard():
    mes = request.args.get('mes', date.today().month, type=int)
    anio = request.args.get('anio', date.today().year, type=int)
    mes_str = f"{anio}-{mes:02d}"
    auto_generar_mes(mes_str)
    conn = get_db()

    total_gastos = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE strftime('%Y-%m',fecha)=?", (mes_str,)
    ).fetchone()['t']
    total_ingresos = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM ingresos WHERE strftime('%Y-%m',fecha)=?", (mes_str,)
    ).fetchone()['t']
    gastos_fijos = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE es_fijo=1 AND strftime('%Y-%m',fecha)=?", (mes_str,)
    ).fetchone()['t']

    gastos_cat = [dict(r) for r in conn.execute("""
        SELECT c.nombre, c.color, c.icono, COALESCE(SUM(g.importe),0) as total
        FROM categorias_gasto c
        LEFT JOIN gastos g ON g.categoria_id=c.id AND strftime('%Y-%m',g.fecha)=?
        WHERE c.activa=1 GROUP BY c.id HAVING total > 0 ORDER BY total DESC
    """, (mes_str,)).fetchall()]

    evolucion = []
    for i in range(5, -1, -1):
        m = mes - i
        a = anio
        while m <= 0: m += 12; a -= 1
        ms = f"{a}-{m:02d}"
        g = conn.execute("SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE strftime('%Y-%m',fecha)=?", (ms,)).fetchone()['t']
        inc = conn.execute("SELECT COALESCE(SUM(importe),0) as t FROM ingresos WHERE strftime('%Y-%m',fecha)=?", (ms,)).fetchone()['t']
        evolucion.append({'label': NOMBRES_MESES[m-1][:3], 'gastos': round(g,2), 'ingresos': round(inc,2)})

    ultimos = [dict(r) for r in conn.execute("""
        SELECT g.id, g.descripcion, g.importe, g.fecha, g.es_fijo,
               u.nombre as unombre, u.color as ucolor,
               c.nombre as cnombre, c.color as ccolor, c.icono as cicono
        FROM gastos g
        LEFT JOIN usuarios u ON g.usuario_id=u.id
        LEFT JOIN categorias_gasto c ON g.categoria_id=c.id
        WHERE strftime('%Y-%m',g.fecha)=?
        ORDER BY g.fecha DESC, g.created_at DESC LIMIT 6
    """, (mes_str,)).fetchall()]

    conn.close()
    return jsonify({
        'mes': mes, 'anio': anio, 'mes_str': mes_str,
        'nombre_mes': NOMBRES_MESES[mes-1],
        'total_gastos': total_gastos,
        'total_ingresos': total_ingresos,
        'balance': total_ingresos - total_gastos,
        'gastos_fijos': gastos_fijos,
        'gastos_variables': total_gastos - gastos_fijos,
        'gastos_categoria': gastos_cat,
        'evolucion': evolucion,
        'ultimos_gastos': ultimos,
    })

@app.route('/api/metas')
def api_metas():
    conn = get_db()
    rows = conn.execute("""
        SELECT *, ROUND(importe_actual*100.0/NULLIF(importe_objetivo,0),1) as progreso
        FROM metas_ahorro ORDER BY completada ASC, fecha_limite ASC
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/metas', methods=['POST'])
def api_meta_crear():
    d = request.get_json()
    conn = get_db()
    cur = conn.execute("""INSERT INTO metas_ahorro
        (nombre, descripcion, importe_objetivo, fecha_limite, color)
        VALUES(?,?,?,?,?)""", (
        d.get('nombre','').strip(), d.get('descripcion','').strip(),
        float(d.get('importe_objetivo',0)),
        d.get('fecha_limite') or None, d.get('color','#4f8ef7')
    ))
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return jsonify({'id': mid}), 201

@app.route('/api/metas/<int:mid>', methods=['PUT'])
def api_meta_editar(mid):
    d = request.get_json()
    conn = get_db()
    conn.execute("""UPDATE metas_ahorro SET nombre=?, descripcion=?, importe_objetivo=?,
        fecha_limite=?, color=? WHERE id=?""", (
        d.get('nombre','').strip(), d.get('descripcion','').strip(),
        float(d.get('importe_objetivo',0)),
        d.get('fecha_limite') or None, d.get('color','#4f8ef7'), mid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/metas/<int:mid>', methods=['DELETE'])
def api_meta_eliminar_api(mid):
    conn = get_db()
    conn.execute("DELETE FROM aportaciones_meta WHERE meta_id=?", (mid,))
    conn.execute("UPDATE gastos SET meta_id=NULL WHERE meta_id=?", (mid,))
    conn.execute("DELETE FROM metas_ahorro WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/metas/<int:mid>/aportaciones')
def api_meta_aportaciones(mid):
    conn = get_db()
    rows = conn.execute("""
        SELECT a.*, u.nombre as unombre, u.color as ucolor
        FROM aportaciones_meta a LEFT JOIN usuarios u ON a.usuario_id=u.id
        WHERE a.meta_id=? ORDER BY a.fecha DESC
    """, (mid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/metas/<int:mid>/abonar', methods=['POST'])
def api_meta_abonar(mid):
    d = request.get_json()
    importe = float(d.get('importe', 0))
    conn = get_db()
    meta = conn.execute("SELECT * FROM metas_ahorro WHERE id=?", (mid,)).fetchone()
    conn.execute("""INSERT INTO aportaciones_meta (meta_id, usuario_id, importe, fecha, notas)
        VALUES(?,?,?,?,?)""", (mid, d.get('usuario_id'), importe, d.get('fecha'), d.get('notas','')))
    nuevo_total = meta['importe_actual'] + importe
    completada = 1 if nuevo_total >= meta['importe_objetivo'] else 0
    conn.execute("UPDATE metas_ahorro SET importe_actual=?, completada=? WHERE id=?",
                 (nuevo_total, completada, mid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'completada': bool(completada)})

@app.route('/api/resumen')
def api_resumen():
    anio = request.args.get('anio', date.today().year, type=int)
    conn = get_db()
    meses = []
    for m in range(1, 13):
        ms = f"{anio}-{m:02d}"
        g = conn.execute("SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE strftime('%Y-%m',fecha)=?", (ms,)).fetchone()['t']
        inc = conn.execute("SELECT COALESCE(SUM(importe),0) as t FROM ingresos WHERE strftime('%Y-%m',fecha)=?", (ms,)).fetchone()['t']
        meses.append({'mes': NOMBRES_MESES[m-1], 'mes_num': m, 'gastos': g, 'ingresos': inc, 'balance': inc-g})
    conn.close()
    return jsonify({'anio': anio, 'meses': meses})


# ── Foto de perfil (Flutter) ─────────────────────────────────────────────────

@app.route('/api/usuarios/<int:uid>/foto_flutter', methods=['POST'])
def api_usuario_foto_flutter(uid):
    foto = request.files.get('foto')
    if not foto or not foto.filename:
        return jsonify({'ok': False, 'error': 'Sin archivo'}), 400
    ext = foto.filename.rsplit('.', 1)[-1].lower()
    if ext not in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
        ext = 'jpg'
    fotos_dir = os.path.join(DATA_DIR, 'fotos')
    os.makedirs(fotos_dir, exist_ok=True)
    nombre = f'usuario_{uid}.{ext}'
    foto.save(os.path.join(fotos_dir, nombre))
    conn = get_db()
    conn.execute("UPDATE usuarios SET foto=? WHERE id=?", (nombre, uid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'foto': nombre})

@app.route('/api/fotos/<path:filename>')
def api_foto_serve(filename):
    from flask import send_from_directory
    fotos_dir = os.path.join(DATA_DIR, 'fotos')
    return send_from_directory(fotos_dir, filename)


# ── Presupuestos ─────────────────────────────────────────────────────────────

@app.route('/api/presupuestos')
def api_presupuestos():
    mes = request.args.get('mes', date.today().month, type=int)
    anio = request.args.get('anio', date.today().year, type=int)
    mes_str = f"{anio}-{mes:02d}"
    conn = get_db()
    rows = conn.execute("""
        SELECT p.id, p.categoria_id, p.importe_mensual,
               c.nombre as categoria_nombre, c.color as categoria_color,
               COALESCE((SELECT SUM(g.importe) FROM gastos g
                         WHERE g.categoria_id=p.categoria_id
                           AND strftime('%Y-%m',g.fecha)=?), 0) as gastado
        FROM presupuestos p
        JOIN categorias_gasto c ON c.id=p.categoria_id
        ORDER BY c.nombre
    """, (mes_str,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/presupuestos', methods=['POST'])
def api_presupuesto_crear():
    d = request.get_json()
    conn = get_db()
    try:
        cur = conn.execute("INSERT OR REPLACE INTO presupuestos (categoria_id, importe_mensual) VALUES(?,?)", (
            d.get('categoria_id'), float(d.get('importe_mensual', 0))
        ))
        conn.commit()
        pid = cur.lastrowid
    except Exception as e:
        conn.close()
        return jsonify({'ok': False, 'error': str(e)}), 400
    conn.close()
    return jsonify({'id': pid}), 201

@app.route('/api/presupuestos/<int:pid>', methods=['PUT'])
def api_presupuesto_editar(pid):
    d = request.get_json()
    conn = get_db()
    conn.execute("UPDATE presupuestos SET importe_mensual=? WHERE id=?",
                 (float(d.get('importe_mensual', 0)), pid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/presupuestos/<int:pid>', methods=['DELETE'])
def api_presupuesto_eliminar(pid):
    conn = get_db()
    conn.execute("DELETE FROM presupuestos WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


# ── Notificaciones ───────────────────────────────────────────────────────────

@app.route('/api/notificaciones')
def api_notificaciones():
    uid = request.args.get('usuario_id', type=int)
    if not uid:
        return jsonify([])
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM notificaciones
        WHERE usuario_id=? AND leida=0
        ORDER BY created_at DESC LIMIT 50
    """, (uid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/notificaciones/<int:nid>/leer', methods=['POST'])
def api_notificacion_leer(nid):
    conn = get_db()
    conn.execute("UPDATE notificaciones SET leida=1 WHERE id=?", (nid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/notificaciones/leer_todas', methods=['POST'])
def api_notificaciones_leer_todas():
    uid = request.get_json().get('usuario_id')
    if uid:
        conn = get_db()
        conn.execute("UPDATE notificaciones SET leida=1 WHERE usuario_id=?", (uid,))
        conn.commit()
        conn.close()
    return jsonify({'ok': True})


# ── INICIO / USUARIO ─────────────────────────────────────────
@app.route('/')
def index():
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))
    return redirect(url_for('dashboard'))


@app.route('/seleccionar')
def seleccionar_usuario():
    conn = get_db()
    usuarios = conn.execute("SELECT * FROM usuarios").fetchall()
    conn.close()
    return render_template('seleccionar_usuario.html', usuarios=usuarios)


@app.route('/seleccionar/<int:uid>')
def set_usuario(uid):
    conn = get_db()
    u = conn.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
    conn.close()
    if u:
        if u['pin']:
            return redirect(url_for('pin_usuario', uid=uid))
        session['usuario_id'] = uid
    return redirect(url_for('dashboard'))


@app.route('/pin/<int:uid>', methods=['GET', 'POST'])
def pin_usuario(uid):
    conn = get_db()
    u = conn.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
    conn.close()
    if not u or not u['pin']:
        session['usuario_id'] = uid
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        if request.form.get('pin', '') == u['pin']:
            session['usuario_id'] = uid
            return redirect(url_for('dashboard'))
        flash('PIN incorrecto.', 'danger')
    return render_template('pin_entrada.html', usuario=u)


@app.route('/cambiar_usuario')
def cambiar_usuario():
    session.pop('usuario_id', None)
    return redirect(url_for('seleccionar_usuario'))


# ── DASHBOARD ────────────────────────────────────────────────
@app.route('/dashboard')
def dashboard():
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))

    hoy = date.today()
    mes = request.args.get('mes', hoy.month, type=int)
    anio = request.args.get('anio', hoy.year, type=int)
    mes_str = f"{anio}-{mes:02d}"
    auto_generar_mes(mes_str)

    auto_generar_mes(mes_str)

    conn = get_db()

    total_gastos = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE strftime('%Y-%m',fecha)=?", (mes_str,)
    ).fetchone()['t']

    total_ingresos = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM ingresos WHERE strftime('%Y-%m',fecha)=?", (mes_str,)
    ).fetchone()['t']

    balance = total_ingresos - total_gastos

    gastos_fijos_total = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE es_fijo=1 AND strftime('%Y-%m',fecha)=?", (mes_str,)
    ).fetchone()['t']
    gastos_variables_total = total_gastos - gastos_fijos_total

    gastos_categoria = [dict(r) for r in conn.execute("""
        SELECT c.nombre, c.color, c.icono, COALESCE(SUM(g.importe),0) as total
        FROM categorias_gasto c
        LEFT JOIN gastos g ON g.categoria_id=c.id AND strftime('%Y-%m',g.fecha)=?
        WHERE c.activa=1
        GROUP BY c.id HAVING total > 0 ORDER BY total DESC
    """, (mes_str,)).fetchall()]

    gastos_usuario = conn.execute("""
        SELECT u.nombre, u.color, u.emoji, COALESCE(SUM(g.importe),0) as total
        FROM usuarios u
        LEFT JOIN gastos g ON g.usuario_id=u.id AND strftime('%Y-%m',g.fecha)=?
        GROUP BY u.id
    """, (mes_str,)).fetchall()

    ingresos_usuario = conn.execute("""
        SELECT u.nombre, u.color, u.emoji, COALESCE(SUM(i.importe),0) as total
        FROM usuarios u
        LEFT JOIN ingresos i ON i.usuario_id=u.id AND strftime('%Y-%m',i.fecha)=?
        GROUP BY u.id
    """, (mes_str,)).fetchall()

    ultimos_gastos = conn.execute("""
        SELECT g.*, u.nombre as unombre, u.color as ucolor, u.emoji as uemoji,
               c.nombre as cnombre, c.color as ccolor, c.icono as cicono
        FROM gastos g
        LEFT JOIN usuarios u ON g.usuario_id=u.id
        LEFT JOIN categorias_gasto c ON g.categoria_id=c.id
        WHERE strftime('%Y-%m',g.fecha)=?
        ORDER BY g.fecha DESC, g.created_at DESC LIMIT 6
    """, (mes_str,)).fetchall()

    metas = conn.execute("""
        SELECT *, ROUND(importe_actual*100.0/NULLIF(importe_objetivo,0),1) as progreso
        FROM metas_ahorro WHERE completada=0
        ORDER BY fecha_limite ASC LIMIT 3
    """).fetchall()

    evolucion = []
    for i in range(5, -1, -1):
        m = mes - i
        a = anio
        while m <= 0:
            m += 12
            a -= 1
        ms = f"{a}-{m:02d}"
        g = conn.execute("SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE strftime('%Y-%m',fecha)=?", (ms,)).fetchone()['t']
        inc = conn.execute("SELECT COALESCE(SUM(importe),0) as t FROM ingresos WHERE strftime('%Y-%m',fecha)=?", (ms,)).fetchone()['t']
        evolucion.append({'label': NOMBRES_MESES[m-1][:3], 'gastos': round(g, 2), 'ingresos': round(inc, 2)})

    conn.close()

    return render_template('dashboard.html',
        mes=mes, anio=anio, mes_str=mes_str,
        nombre_mes=NOMBRES_MESES[mes-1],
        total_gastos=total_gastos, total_ingresos=total_ingresos, balance=balance,
        gastos_fijos_total=gastos_fijos_total, gastos_variables_total=gastos_variables_total,
        gastos_categoria=gastos_categoria,
        gastos_usuario=gastos_usuario, ingresos_usuario=ingresos_usuario,
        ultimos_gastos=ultimos_gastos, metas=metas, evolucion=evolucion
    )


# ── GASTOS ───────────────────────────────────────────────────
@app.route('/gastos')
def gastos():
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))

    hoy = date.today()
    mes = request.args.get('mes', hoy.month, type=int)
    anio = request.args.get('anio', hoy.year, type=int)
    cat_filtro = request.args.get('cat', '')
    usr_filtro = request.args.get('usr', '')
    fijo_filtro = request.args.get('fijo', '')
    mes_str = f"{anio}-{mes:02d}"

    auto_generar_mes(mes_str)

    conn = get_db()
    q = """SELECT g.*, u.nombre as unombre, u.color as ucolor, u.emoji as uemoji,
           c.nombre as cnombre, c.color as ccolor, c.icono as cicono
           FROM gastos g
           LEFT JOIN usuarios u ON g.usuario_id=u.id
           LEFT JOIN categorias_gasto c ON g.categoria_id=c.id
           WHERE strftime('%Y-%m',g.fecha)=?"""
    params = [mes_str]
    if cat_filtro:
        q += " AND g.categoria_id=?"; params.append(cat_filtro)
    if usr_filtro:
        q += " AND g.usuario_id=?"; params.append(usr_filtro)
    if fijo_filtro == '1':
        q += " AND g.es_fijo=1"
    elif fijo_filtro == '0':
        q += " AND g.es_fijo=0"
    q += " ORDER BY g.fecha DESC, g.created_at DESC"

    lista = conn.execute(q, params).fetchall()
    categorias = conn.execute("SELECT * FROM categorias_gasto WHERE activa=1 ORDER BY nombre").fetchall()
    usuarios = conn.execute("SELECT * FROM usuarios").fetchall()
    total = sum(g['importe'] for g in lista)
    conn.close()

    return render_template('gastos.html',
        gastos=lista, categorias=categorias, usuarios=usuarios,
        mes=mes, anio=anio, total=total, nombre_mes=NOMBRES_MESES[mes-1],
        cat_filtro=cat_filtro, usr_filtro=usr_filtro, fijo_filtro=fijo_filtro
    )


@app.route('/gastos/nuevo', methods=['GET', 'POST'])
def gasto_nuevo():
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))
    conn = get_db()
    categorias = conn.execute("SELECT * FROM categorias_gasto WHERE activa=1 ORDER BY nombre").fetchall()
    usuarios = conn.execute("SELECT * FROM usuarios").fetchall()
    metas_activas = conn.execute(
        "SELECT id, nombre FROM metas_ahorro WHERE completada=0 ORDER BY nombre"
    ).fetchall()

    if request.method == 'POST':
        es_fijo = 1 if request.form.get('es_fijo') else 0
        uid = request.form.get('usuario_id') or None
        if es_fijo:
            uid = None  # Gastos fijos son compartidos, sin usuario asignado

        fecha = fecha_desde_form(request.form.get('fecha'))
        categoria_id = request.form.get('categoria_id') or None
        meta_id = request.form.get('meta_id') or None
        importe = float(request.form.get('importe', 0))

        cursor = conn.execute("""INSERT INTO gastos
            (usuario_id, categoria_id, descripcion, importe, fecha, es_fijo, notas, meta_id)
            VALUES(?,?,?,?,?,?,?,?)""", (
            uid, categoria_id,
            request.form.get('descripcion', '').strip(),
            importe, fecha, es_fijo,
            request.form.get('notas', '').strip(),
            meta_id
        ))
        gasto_id = cursor.lastrowid

        # Si es categoria Ahorro y tiene meta seleccionada, crear aportacion automatica
        if categoria_id and meta_id:
            cat = conn.execute("SELECT es_ahorro FROM categorias_gasto WHERE id=?", (categoria_id,)).fetchone()
            if cat and cat['es_ahorro']:
                aport_uid = uid or session['usuario_id']
                conn.execute("""INSERT INTO aportaciones_meta
                    (meta_id, usuario_id, importe, fecha, notas, gasto_id)
                    VALUES(?,?,?,?,?,?)""", (meta_id, aport_uid, importe, fecha, '', gasto_id))
                conn.execute("""UPDATE metas_ahorro SET
                    importe_actual = importe_actual + ?,
                    completada = CASE WHEN importe_actual + ? >= importe_objetivo THEN 1 ELSE 0 END
                    WHERE id=?""", (importe, importe, meta_id))

        conn.commit()
        conn.close()
        flash('Gasto registrado correctamente.', 'success')
        return redirect(url_for('gastos'))

    conn.close()
    return render_template('gasto_form.html', categorias=categorias, usuarios=usuarios,
                           metas_activas=metas_activas, gasto=None)


@app.route('/gastos/<int:gid>/editar', methods=['GET', 'POST'])
def gasto_editar(gid):
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))
    conn = get_db()
    gasto = conn.execute("SELECT * FROM gastos WHERE id=?", (gid,)).fetchone()
    categorias = conn.execute("SELECT * FROM categorias_gasto WHERE activa=1 ORDER BY nombre").fetchall()
    usuarios = conn.execute("SELECT * FROM usuarios").fetchall()
    metas_activas = conn.execute(
        "SELECT id, nombre FROM metas_ahorro WHERE completada=0 ORDER BY nombre"
    ).fetchall()

    if request.method == 'POST':
        es_fijo = 1 if request.form.get('es_fijo') else 0
        uid = request.form.get('usuario_id') or None
        if es_fijo:
            uid = None
        fecha = fecha_desde_form(request.form.get('fecha'))

        conn.execute("""UPDATE gastos SET usuario_id=?, categoria_id=?, descripcion=?,
            importe=?, fecha=?, es_fijo=?, notas=? WHERE id=?""", (
            uid,
            request.form.get('categoria_id') or None,
            request.form.get('descripcion', '').strip(),
            float(request.form.get('importe', 0)),
            fecha, es_fijo,
            request.form.get('notas', '').strip(),
            gid
        ))
        conn.commit()
        conn.close()
        flash('Gasto actualizado.', 'success')
        return redirect(url_for('gastos'))

    conn.close()
    return render_template('gasto_form.html', categorias=categorias, usuarios=usuarios,
                           metas_activas=metas_activas, gasto=gasto)


@app.route('/gastos/<int:gid>/eliminar', methods=['POST'])
def gasto_eliminar(gid):
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))
    conn = get_db()
    g = conn.execute("SELECT * FROM gastos WHERE id=?", (gid,)).fetchone()
    if g and g['meta_id']:
        conn.execute("DELETE FROM aportaciones_meta WHERE gasto_id=?", (gid,))
        nueva_t = conn.execute(
            "SELECT COALESCE(SUM(importe),0) as t FROM aportaciones_meta WHERE meta_id=?",
            (g['meta_id'],)
        ).fetchone()['t']
        conn.execute("""UPDATE metas_ahorro SET importe_actual=?,
            completada=CASE WHEN ? >= importe_objetivo THEN 1 ELSE 0 END WHERE id=?""",
            (nueva_t, nueva_t, g['meta_id']))
    conn.execute("DELETE FROM gastos WHERE id=?", (gid,))
    conn.commit()
    conn.close()
    flash('Gasto eliminado.', 'warning')
    return redirect(url_for('gastos'))


@app.route('/gastos/<int:gid>/desvinc_meta', methods=['POST'])
def gasto_desvinc_meta(gid):
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))
    conn = get_db()
    g = conn.execute("SELECT * FROM gastos WHERE id=?", (gid,)).fetchone()
    if g and g['meta_id']:
        mid = g['meta_id']
        conn.execute("DELETE FROM aportaciones_meta WHERE gasto_id=?", (gid,))
        conn.execute("UPDATE gastos SET meta_id=NULL WHERE id=?", (gid,))
        nueva_t = conn.execute(
            "SELECT COALESCE(SUM(importe),0) as t FROM aportaciones_meta WHERE meta_id=?", (mid,)
        ).fetchone()['t']
        conn.execute("""UPDATE metas_ahorro SET importe_actual=?,
            completada=CASE WHEN ? >= importe_objetivo THEN 1 ELSE 0 END WHERE id=?""",
            (nueva_t, nueva_t, mid))
        conn.commit()
        flash('Vinculacion con meta eliminada.', 'info')
    conn.close()
    return redirect(url_for('gastos'))


# ── INGRESOS ─────────────────────────────────────────────────
@app.route('/ingresos')
def ingresos():
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))

    hoy = date.today()
    mes = request.args.get('mes', hoy.month, type=int)
    anio = request.args.get('anio', hoy.year, type=int)
    usr_filtro = request.args.get('usr', '')
    mes_str = f"{anio}-{mes:02d}"

    auto_generar_mes(mes_str)

    conn = get_db()
    q = """SELECT i.*, u.nombre as unombre, u.color as ucolor, u.emoji as uemoji
           FROM ingresos i JOIN usuarios u ON i.usuario_id=u.id
           WHERE strftime('%Y-%m',i.fecha)=?"""
    params = [mes_str]
    if usr_filtro:
        q += " AND i.usuario_id=?"; params.append(usr_filtro)
    q += " ORDER BY i.fecha DESC, i.created_at DESC"

    lista = conn.execute(q, params).fetchall()
    usuarios = conn.execute("SELECT * FROM usuarios").fetchall()
    total = sum(i['importe'] for i in lista)
    total_nomina = sum(i['importe'] for i in lista if i['es_nomina'])
    conn.close()

    return render_template('ingresos.html',
        ingresos=lista, usuarios=usuarios,
        mes=mes, anio=anio, total=total, total_nomina=total_nomina,
        nombre_mes=NOMBRES_MESES[mes-1], usr_filtro=usr_filtro
    )


@app.route('/ingresos/nuevo', methods=['GET', 'POST'])
def ingreso_nuevo():
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))
    conn = get_db()
    usuarios = conn.execute("SELECT * FROM usuarios").fetchall()
    if request.method == 'POST':
        conn.execute("""INSERT INTO ingresos (usuario_id, descripcion, importe, fecha, es_nomina, notas)
            VALUES(?,?,?,?,?,?)""", (
            request.form.get('usuario_id', session['usuario_id']),
            request.form.get('descripcion', '').strip(),
            float(request.form.get('importe', 0)),
            fecha_desde_form(request.form.get('fecha')),
            1 if request.form.get('es_nomina') else 0,
            request.form.get('notas', '').strip()
        ))
        conn.commit()
        conn.close()
        flash('Ingreso registrado correctamente.', 'success')
        return redirect(url_for('ingresos'))
    conn.close()
    return render_template('ingreso_form.html', usuarios=usuarios, ingreso=None)


@app.route('/ingresos/<int:iid>/editar', methods=['GET', 'POST'])
def ingreso_editar(iid):
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))
    conn = get_db()
    ingreso = conn.execute("SELECT * FROM ingresos WHERE id=?", (iid,)).fetchone()
    usuarios = conn.execute("SELECT * FROM usuarios").fetchall()
    if request.method == 'POST':
        conn.execute("""UPDATE ingresos SET usuario_id=?, descripcion=?, importe=?,
            fecha=?, es_nomina=?, notas=? WHERE id=?""", (
            request.form.get('usuario_id'),
            request.form.get('descripcion', '').strip(),
            float(request.form.get('importe', 0)),
            fecha_desde_form(request.form.get('fecha')),
            1 if request.form.get('es_nomina') else 0,
            request.form.get('notas', '').strip(),
            iid
        ))
        conn.commit()
        conn.close()
        flash('Ingreso actualizado.', 'success')
        return redirect(url_for('ingresos'))
    conn.close()
    return render_template('ingreso_form.html', usuarios=usuarios, ingreso=ingreso)


@app.route('/ingresos/<int:iid>/eliminar', methods=['POST'])
def ingreso_eliminar(iid):
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))
    conn = get_db()
    conn.execute("DELETE FROM ingresos WHERE id=?", (iid,))
    conn.commit()
    conn.close()
    flash('Ingreso eliminado.', 'warning')
    return redirect(url_for('ingresos'))


# ── METAS DE AHORRO ──────────────────────────────────────────
@app.route('/metas')
def metas():
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))
    conn = get_db()
    lista = conn.execute("""
        SELECT *, ROUND(importe_actual*100.0/NULLIF(importe_objetivo,0),1) as progreso
        FROM metas_ahorro ORDER BY completada ASC, fecha_limite ASC
    """).fetchall()
    # Gastos de Ahorro sin meta asignada → "Ahorro General"
    ahorro_general = conn.execute("""
        SELECT g.id, g.importe, g.fecha, g.notas,
               u.nombre as unombre, u.color as ucolor
        FROM gastos g
        JOIN categorias_gasto c ON g.categoria_id = c.id
        LEFT JOIN usuarios u ON g.usuario_id = u.id
        WHERE c.es_ahorro = 1 AND (g.meta_id IS NULL OR g.meta_id = 0)
        ORDER BY g.fecha DESC
    """).fetchall()
    ahorro_general_total = sum(r['importe'] for r in ahorro_general)
    conn.close()
    return render_template('metas.html', metas=lista,
                           ahorro_general=ahorro_general,
                           ahorro_general_total=ahorro_general_total)


@app.route('/metas/nueva', methods=['GET', 'POST'])
def meta_nueva():
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))
    if request.method == 'POST':
        conn = get_db()
        conn.execute("""INSERT INTO metas_ahorro (nombre, descripcion, importe_objetivo, fecha_limite, color)
            VALUES(?,?,?,?,?)""", (
            request.form.get('nombre', '').strip(),
            request.form.get('descripcion', '').strip(),
            float(request.form.get('importe_objetivo', 0)),
            request.form.get('fecha_limite') or None,
            request.form.get('color', '#4f8ef7')
        ))
        conn.commit()
        conn.close()
        flash('Meta de ahorro creada.', 'success')
        return redirect(url_for('metas'))
    return render_template('meta_form.html', meta=None)


@app.route('/metas/<int:mid>/editar', methods=['GET', 'POST'])
def meta_editar(mid):
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))
    conn = get_db()
    meta = conn.execute("SELECT * FROM metas_ahorro WHERE id=?", (mid,)).fetchone()
    if request.method == 'POST':
        conn.execute("""UPDATE metas_ahorro SET nombre=?, descripcion=?, importe_objetivo=?,
            fecha_limite=?, color=? WHERE id=?""", (
            request.form.get('nombre', '').strip(),
            request.form.get('descripcion', '').strip(),
            float(request.form.get('importe_objetivo', 0)),
            request.form.get('fecha_limite') or None,
            request.form.get('color', '#4f8ef7'),
            mid
        ))
        conn.commit()
        conn.close()
        flash('Meta actualizada.', 'success')
        return redirect(url_for('metas'))
    conn.close()
    return render_template('meta_form.html', meta=meta)


@app.route('/metas/<int:mid>/abonar', methods=['GET', 'POST'])
def meta_abonar(mid):
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))
    conn = get_db()
    meta = conn.execute("SELECT * FROM metas_ahorro WHERE id=?", (mid,)).fetchone()
    usuarios = conn.execute("SELECT * FROM usuarios").fetchall()
    aportaciones = conn.execute("""
        SELECT a.*, u.nombre as unombre, u.color as ucolor
        FROM aportaciones_meta a LEFT JOIN usuarios u ON a.usuario_id=u.id
        WHERE a.meta_id=? ORDER BY a.fecha DESC
    """, (mid,)).fetchall()
    if request.method == 'POST':
        importe = float(request.form.get('importe', 0))
        fecha = fecha_desde_form(request.form.get('fecha'))
        conn.execute("""INSERT INTO aportaciones_meta (meta_id, usuario_id, importe, fecha, notas)
            VALUES(?,?,?,?,?)""", (
            mid,
            request.form.get('usuario_id', session['usuario_id']),
            importe, fecha,
            request.form.get('notas', '').strip()
        ))
        nuevo_total = meta['importe_actual'] + importe
        completada = 1 if nuevo_total >= meta['importe_objetivo'] else 0
        conn.execute("UPDATE metas_ahorro SET importe_actual=?, completada=? WHERE id=?",
                     (nuevo_total, completada, mid))
        conn.commit()
        conn.close()
        if completada:
            flash(f'Meta "{meta["nombre"]}" completada. Enhorabuena!', 'success')
        else:
            flash('Aportacion registrada.', 'success')
        return redirect(url_for('metas'))
    conn.close()
    return render_template('meta_abonar.html', meta=meta, usuarios=usuarios, aportaciones=aportaciones)


@app.route('/metas/<int:mid>/eliminar', methods=['POST'])
def meta_eliminar(mid):
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))
    conn = get_db()
    conn.execute("DELETE FROM aportaciones_meta WHERE meta_id=?", (mid,))
    conn.execute("UPDATE gastos SET meta_id=NULL WHERE meta_id=?", (mid,))
    conn.execute("DELETE FROM metas_ahorro WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    flash('Meta eliminada.', 'warning')
    return redirect(url_for('metas'))


# ── RESUMEN ANUAL ─────────────────────────────────────────────
@app.route('/resumen')
def resumen():
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))

    anio = request.args.get('anio', date.today().year, type=int)
    conn = get_db()
    meses_data = []
    for m in range(1, 13):
        ms = f"{anio}-{m:02d}"
        g = conn.execute("SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE strftime('%Y-%m',fecha)=?", (ms,)).fetchone()['t']
        inc = conn.execute("SELECT COALESCE(SUM(importe),0) as t FROM ingresos WHERE strftime('%Y-%m',fecha)=?", (ms,)).fetchone()['t']
        meses_data.append({'mes': NOMBRES_MESES[m-1], 'mes_num': m, 'gastos': g, 'ingresos': inc, 'balance': inc - g})

    gastos_cat = conn.execute("""
        SELECT c.nombre, c.color, COALESCE(SUM(g.importe),0) as total
        FROM categorias_gasto c
        LEFT JOIN gastos g ON g.categoria_id=c.id AND strftime('%Y',g.fecha)=?
        GROUP BY c.id HAVING total > 0 ORDER BY total DESC
    """, (str(anio),)).fetchall()

    total_g = sum(m['gastos'] for m in meses_data)
    total_i = sum(m['ingresos'] for m in meses_data)
    conn.close()

    return render_template('resumen.html',
        anio=anio, meses_data=meses_data,
        total_gastos_anio=total_g, total_ingresos_anio=total_i,
        balance_anio=total_i - total_g, gastos_cat=gastos_cat
    )


# ── CONFIGURACION ─────────────────────────────────────────────
@app.route('/configuracion', methods=['GET', 'POST'])
def configuracion():
    if 'usuario_id' not in session:
        return redirect(url_for('seleccionar_usuario'))

    conn = get_db()
    if request.method == 'POST':
        accion = request.form.get('accion')

        if accion == 'editar_usuario':
            conn.execute("UPDATE usuarios SET nombre=?, color=?, emoji=? WHERE id=?", (
                request.form.get('nombre', '').strip(),
                request.form.get('color', '#4f8ef7'),
                request.form.get('emoji', 'person'),
                request.form.get('uid')
            ))
            conn.commit()
            flash('Usuario actualizado.', 'success')

        elif accion == 'set_pin':
            uid = request.form.get('uid')
            # Solo se puede cambiar el propio PIN
            if str(uid) != str(session.get('usuario_id')):
                flash('Solo puedes cambiar tu propio PIN.', 'danger')
            else:
                pin = request.form.get('pin', '').strip()
                if pin == '':
                    conn.execute("UPDATE usuarios SET pin=NULL WHERE id=?", (uid,))
                    conn.commit()
                    flash('PIN eliminado.', 'info')
                elif len(pin) == 4 and pin.isdigit():
                    conn.execute("UPDATE usuarios SET pin=? WHERE id=?", (pin, uid))
                    conn.commit()
                    flash('PIN establecido correctamente.', 'success')
                else:
                    flash('El PIN debe ser de exactamente 4 digitos numericos.', 'danger')

        elif accion == 'nueva_categoria':
            nombre = request.form.get('nombre', '').strip()
            if nombre:
                conn.execute("INSERT INTO categorias_gasto (nombre, color, icono) VALUES(?,?,?)", (
                    nombre,
                    request.form.get('color', '#6c757d'),
                    request.form.get('icono', 'bi-tag')
                ))
                conn.commit()
                flash('Categoria creada.', 'success')

        elif accion == 'eliminar_categoria':
            cid = request.form.get('cid')
            cat = conn.execute("SELECT es_ahorro FROM categorias_gasto WHERE id=?", (cid,)).fetchone()
            if cat and cat['es_ahorro']:
                flash('No se puede eliminar la categoria de Ahorro.', 'danger')
            else:
                cnt = conn.execute("SELECT COUNT(*) as c FROM gastos WHERE categoria_id=?", (cid,)).fetchone()['c']
                if cnt > 0:
                    flash(f'No se puede eliminar: hay {cnt} gasto(s) con esta categoria.', 'danger')
                else:
                    conn.execute("DELETE FROM categorias_gasto WHERE id=?", (cid,))
                    conn.commit()
                    flash('Categoria eliminada.', 'warning')

        conn.close()
        return redirect(url_for('configuracion'))

    usuarios = conn.execute("SELECT * FROM usuarios").fetchall()
    categorias = conn.execute("SELECT * FROM categorias_gasto ORDER BY nombre").fetchall()
    conn.close()
    return render_template('configuracion.html', usuarios=usuarios, categorias=categorias)


if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    init_db()
    if not IS_CLOUD:
        start_tunnel()
        start_discovery_broadcast()
        webbrowser.open(f'http://localhost:{PORT}')
    app.run(debug=False, host='0.0.0.0', port=PORT)
