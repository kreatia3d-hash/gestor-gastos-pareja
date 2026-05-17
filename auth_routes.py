"""
Auth layer para Nido – Google (Firebase) Sign-In + JWT propio.
Todas las rutas nuevas de auth, nido e invitaciones van aquí.
"""
import os, uuid, json, sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, request, jsonify, render_template, current_app, g

try:
    import jwt as pyjwt
    import requests as _req
    _DEPS_OK = True
except ImportError:
    _DEPS_OK = False
    print('[auth] AVISO: PyJWT o requests no instalados. Auth desactivado.')

auth_bp = Blueprint('auth', __name__)

JWT_SECRET          = os.environ.get('JWT_SECRET', 'nido-secret-cambia-en-produccion-2025')
FIREBASE_WEB_API_KEY = os.environ.get('FIREBASE_WEB_API_KEY', '')
BASE_URL            = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'web-production-2694.up.railway.app')


# ── Helpers DB ────────────────────────────────────────────────────────────────

def _get_db():
    db_path = current_app.config['DB_PATH']
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


# ── Firebase token verification ───────────────────────────────────────────────

def _verify_firebase_token(id_token: str):
    """Verifica un Firebase ID Token con la REST API de Firebase.
    Devuelve dict {uid, email, nombre, foto} o None si inválido."""
    if not FIREBASE_WEB_API_KEY or not _DEPS_OK:
        return None
    try:
        resp = _req.post(
            f'https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={FIREBASE_WEB_API_KEY}',
            json={'idToken': id_token},
            timeout=8
        )
        data = resp.json()
        if 'users' in data and data['users']:
            u = data['users'][0]
            return {
                'uid':    u['localId'],
                'email':  u.get('email', ''),
                'nombre': u.get('displayName', ''),
                'foto':   u.get('photoUrl', ''),
            }
    except Exception as e:
        print(f'[auth] Firebase verify error: {e}')
    return None


# ── JWT ───────────────────────────────────────────────────────────────────────

def generate_jwt(user_id: int, nido_id: int, firebase_uid: str) -> str:
    payload = {
        'user_id':     user_id,
        'nido_id':     nido_id,
        'uid':         firebase_uid,
        'exp':         datetime.utcnow() + timedelta(days=90),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm='HS256')


def decode_jwt(token: str) -> dict:
    return pyjwt.decode(token, JWT_SECRET, algorithms=['HS256'])


def get_nido_id_from_request() -> int:
    """Lee el nido_id del JWT Bearer. Si no hay JWT devuelve 1 (compatibilidad)."""
    if not _DEPS_OK:
        return 1
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        try:
            payload = decode_jwt(auth[7:])
            return payload.get('nido_id', 1)
        except Exception:
            pass
    return 1


def requiere_auth(f):
    """Decorador que exige JWT válido. Pone g.nido_id, g.user_id, g.firebase_uid."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _DEPS_OK:
            g.nido_id = 1; g.user_id = 1; g.firebase_uid = ''
            return f(*args, **kwargs)
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'error': 'No autorizado'}), 401
        try:
            payload = decode_jwt(auth[7:])
            g.nido_id     = payload['nido_id']
            g.user_id     = payload['user_id']
            g.firebase_uid = payload['uid']
        except Exception:
            return jsonify({'error': 'Token inválido o expirado'}), 401
        return f(*args, **kwargs)
    return decorated


# ── /api/auth/google ──────────────────────────────────────────────────────────

@auth_bp.route('/api/auth/google', methods=['POST'])
def api_auth_google():
    """
    Recibe Firebase ID Token. Devuelve JWT propio + info del nido.
    Flujos:
      · Usuario existente        → JWT + info completa
      · 1er usuario (dato virgen)→ JWT + asigna nido_id=1
      · Usuario nuevo sin nido   → {necesita_nido:true} (sin JWT)
    """
    d = request.get_json(silent=True) or {}
    id_token = d.get('id_token', '')
    if not id_token:
        return jsonify({'error': 'Falta id_token'}), 400

    fb = _verify_firebase_token(id_token)
    if not fb:
        return jsonify({'error': 'Token de Google inválido'}), 401

    conn = _get_db()

    # ¿Existe ya este usuario de Firebase en nuestra BD?
    fuser = conn.execute(
        'SELECT * FROM firebase_users WHERE firebase_uid=?', (fb['uid'],)
    ).fetchone()

    if fuser:
        # Actualizar nombre/foto por si cambió en Google
        conn.execute(
            'UPDATE firebase_users SET nombre=?, foto=? WHERE firebase_uid=?',
            (fb['nombre'], fb['foto'], fb['uid'])
        )
        conn.commit()
        nido = conn.execute('SELECT * FROM nidos WHERE id=?', (fuser['nido_id'],)).fetchone()
        miembros_cnt = conn.execute(
            'SELECT COUNT(*) as c FROM firebase_users WHERE nido_id=?', (fuser['nido_id'],)
        ).fetchone()['c']
        conn.close()
        token = generate_jwt(fuser['id'], fuser['nido_id'], fb['uid'])
        return jsonify({
            'token':        token,
            'usuario_id':   fuser['usuario_id'],
            'nido_id':      fuser['nido_id'],
            'nido_nombre':  nido['nombre'] if nido else 'Mi Nido',
            'es_nuevo':     False,
            'necesita_nido':False,
            'nombre':       fb['nombre'],
            'email':        fb['email'],
            'miembros':     miembros_cnt,
        })

    # Usuario nuevo → ¿hay nido_id=1 sin ningún firebase_user asignado?
    sin_reclamar = conn.execute(
        'SELECT COUNT(*) as c FROM firebase_users WHERE nido_id=1'
    ).fetchone()['c']

    if sin_reclamar == 0:
        # Reclamar el nido existente con los datos actuales
        primer_usuario = conn.execute(
            'SELECT id FROM usuarios WHERE nido_id=1 ORDER BY id LIMIT 1'
        ).fetchone()
        if primer_usuario:
            usuario_id = primer_usuario['id']
            if fb['nombre']:
                conn.execute('UPDATE usuarios SET nombre=? WHERE id=?',
                             (fb['nombre'], usuario_id))
        else:
            cur = conn.execute(
                'INSERT INTO usuarios (nombre, color, emoji, nido_id) VALUES(?,?,?,1)',
                (fb['nombre'] or fb['email'].split('@')[0], '#5C8374', 'person')
            )
            usuario_id = cur.lastrowid

        cur2 = conn.execute(
            'INSERT INTO firebase_users (firebase_uid, email, nombre, foto, nido_id, usuario_id)'
            ' VALUES(?,?,?,?,1,?)',
            (fb['uid'], fb['email'], fb['nombre'], fb['foto'], usuario_id)
        )
        user_id = cur2.lastrowid
        conn.commit()
        conn.close()
        token = generate_jwt(user_id, 1, fb['uid'])
        return jsonify({
            'token':        token,
            'usuario_id':   usuario_id,
            'nido_id':      1,
            'nido_nombre':  'Mi Nido',
            'es_nuevo':     True,
            'necesita_nido':False,
            'nombre':       fb['nombre'],
            'email':        fb['email'],
            'miembros':     1,
        })

    # Nuevo usuario que necesita crear su propio nido o unirse a uno por invitación
    conn.close()
    return jsonify({
        'token':         None,
        'es_nuevo':      True,
        'necesita_nido': True,
        'nombre':        fb['nombre'],
        'email':         fb['email'],
        'firebase_uid':  fb['uid'],
    })


# ── /api/nido ─────────────────────────────────────────────────────────────────

@auth_bp.route('/api/nido', methods=['GET'])
@requiere_auth
def api_nido_info():
    """Devuelve info del nido actual: nombre, miembros, plan."""
    conn = _get_db()
    nido = conn.execute('SELECT * FROM nidos WHERE id=?', (g.nido_id,)).fetchone()
    miembros = conn.execute(
        '''SELECT u.id, u.nombre, u.color, u.emoji, u.foto,
                  fu.email
           FROM firebase_users fu
           JOIN usuarios u ON u.id=fu.usuario_id
           WHERE fu.nido_id=?''',
        (g.nido_id,)
    ).fetchall()
    conn.close()
    return jsonify({
        'id':       nido['id'] if nido else g.nido_id,
        'nombre':   nido['nombre'] if nido else 'Mi Nido',
        'plan':     nido['plan'] if nido else 'free',
        'miembros': [dict(m) for m in miembros],
    })


@auth_bp.route('/api/nido/crear', methods=['POST'])
def api_nido_crear():
    """Crea un nido nuevo para un usuario que acaba de registrarse.
    Requiere id_token de Firebase (no JWT todavía)."""
    d = request.get_json(silent=True) or {}
    id_token = d.get('id_token', '')
    nombre_nido = d.get('nombre', 'Mi Nido').strip() or 'Mi Nido'

    fb = _verify_firebase_token(id_token)
    if not fb:
        return jsonify({'error': 'Token de Google inválido'}), 401

    conn = _get_db()

    # Evitar duplicados: si ya tiene nido devolver info existente
    fuser = conn.execute(
        'SELECT * FROM firebase_users WHERE firebase_uid=?', (fb['uid'],)
    ).fetchone()
    if fuser:
        conn.close()
        token = generate_jwt(fuser['id'], fuser['nido_id'], fb['uid'])
        return jsonify({'token': token, 'nido_id': fuser['nido_id'], 'ok': True})

    # Crear nido
    cur_nido = conn.execute(
        'INSERT INTO nidos (nombre) VALUES(?)', (nombre_nido,)
    )
    nido_id = cur_nido.lastrowid

    # Crear entrada en tabla usuarios (persona del nido)
    nombre_persona = fb['nombre'] or fb['email'].split('@')[0]
    cur_u = conn.execute(
        'INSERT INTO usuarios (nombre, color, emoji, nido_id) VALUES(?,?,?,?)',
        (nombre_persona, '#5C8374', 'person', nido_id)
    )
    usuario_id = cur_u.lastrowid

    # Categorías por defecto para este nido
    cats_default = [
        ('Alimentación', '#28a745', 'bi-basket2', 0),
        ('Ocio',         '#ffc107', 'bi-controller', 0),
        ('Transporte',   '#17a2b8', 'bi-train-front', 0),
        ('Casa',         '#6f42c1', 'bi-house', 0),
        ('Salud',        '#e83e8c', 'bi-heart-pulse', 0),
        ('Ropa',         '#fd7e14', 'bi-bag', 0),
        ('Viajes',       '#20c997', 'bi-airplane', 0),
        ('Suscripciones','#0dcaf0', 'bi-phone', 0),
        ('Ahorro',       '#5C8374', 'bi-piggy-bank', 1),
        ('Otros',        '#6c757d', 'bi-three-dots', 0),
    ]
    for cat in cats_default:
        conn.execute(
            'INSERT INTO categorias_gasto (nombre, color, icono, es_ahorro, nido_id) VALUES(?,?,?,?,?)',
            (*cat, nido_id)
        )

    # Crear firebase_user
    cur_fu = conn.execute(
        'INSERT INTO firebase_users (firebase_uid, email, nombre, foto, nido_id, usuario_id)'
        ' VALUES(?,?,?,?,?,?)',
        (fb['uid'], fb['email'], fb['nombre'], fb['foto'], nido_id, usuario_id)
    )
    user_id = cur_fu.lastrowid
    conn.commit()
    conn.close()

    token = generate_jwt(user_id, nido_id, fb['uid'])
    return jsonify({
        'token':      token,
        'nido_id':    nido_id,
        'usuario_id': usuario_id,
        'ok':         True,
    }), 201


# ── /api/nido/invitar ─────────────────────────────────────────────────────────

@auth_bp.route('/api/nido/invitar', methods=['POST'])
@requiere_auth
def api_nido_invitar():
    """Genera un token de invitación para unirse al nido actual.
    El nido solo puede tener 2 miembros."""
    conn = _get_db()
    miembros = conn.execute(
        'SELECT COUNT(*) as c FROM firebase_users WHERE nido_id=?', (g.nido_id,)
    ).fetchone()['c']
    if miembros >= 2:
        conn.close()
        return jsonify({'error': 'El nido ya tiene 2 miembros'}), 400

    # Invalidar invitaciones anteriores de este nido
    conn.execute(
        "UPDATE invitaciones SET estado='expirada' WHERE nido_id=? AND estado='pendiente'",
        (g.nido_id,)
    )
    token = uuid.uuid4().hex
    expires = (datetime.utcnow() + timedelta(days=7)).isoformat()
    conn.execute(
        'INSERT INTO invitaciones (nido_id, creada_por, token, expires_at) VALUES(?,?,?,?)',
        (g.nido_id, g.user_id, token, expires)
    )
    conn.commit()
    conn.close()

    invite_url = f'https://{BASE_URL}/invite/{token}'
    return jsonify({'token': token, 'url': invite_url, 'expires_at': expires})


# ── /api/invitaciones/<token> ─────────────────────────────────────────────────

@auth_bp.route('/api/invitaciones/<token>', methods=['GET'])
def api_invitacion_info(token):
    """Devuelve info pública de la invitación (para mostrar antes de aceptar)."""
    conn = _get_db()
    inv = conn.execute('SELECT * FROM invitaciones WHERE token=?', (token,)).fetchone()
    if not inv:
        conn.close()
        return jsonify({'error': 'Invitación no encontrada'}), 404
    if inv['estado'] != 'pendiente':
        conn.close()
        return jsonify({'error': 'Invitación ya usada o expirada'}), 410
    if datetime.fromisoformat(inv['expires_at']) < datetime.utcnow():
        conn.execute("UPDATE invitaciones SET estado='expirada' WHERE token=?", (token,))
        conn.commit()
        conn.close()
        return jsonify({'error': 'Invitación expirada'}), 410

    nido = conn.execute('SELECT nombre FROM nidos WHERE id=?', (inv['nido_id'],)).fetchone()
    primer_miembro = conn.execute(
        '''SELECT u.nombre FROM firebase_users fu
           JOIN usuarios u ON u.id=fu.usuario_id
           WHERE fu.nido_id=? ORDER BY fu.id LIMIT 1''',
        (inv['nido_id'],)
    ).fetchone()
    conn.close()
    return jsonify({
        'nido_nombre':      nido['nombre'] if nido else 'Un nido',
        'invitado_por':     primer_miembro['nombre'] if primer_miembro else 'Tu pareja',
        'expires_at':       inv['expires_at'],
    })


@auth_bp.route('/api/invitaciones/<token>/aceptar', methods=['POST'])
def api_invitacion_aceptar(token):
    """Acepta la invitación con Firebase ID Token. Devuelve JWT del nido."""
    d = request.get_json(silent=True) or {}
    id_token = d.get('id_token', '')

    fb = _verify_firebase_token(id_token)
    if not fb:
        return jsonify({'error': 'Token de Google inválido'}), 401

    conn = _get_db()
    inv = conn.execute('SELECT * FROM invitaciones WHERE token=?', (token,)).fetchone()
    if not inv or inv['estado'] != 'pendiente':
        conn.close()
        return jsonify({'error': 'Invitación no válida o ya usada'}), 410
    if datetime.fromisoformat(inv['expires_at']) < datetime.utcnow():
        conn.close()
        return jsonify({'error': 'Invitación expirada'}), 410

    # ¿El usuario ya tiene nido?
    fuser = conn.execute(
        'SELECT * FROM firebase_users WHERE firebase_uid=?', (fb['uid'],)
    ).fetchone()
    if fuser:
        if fuser['nido_id'] == inv['nido_id']:
            conn.close()
            token_jwt = generate_jwt(fuser['id'], fuser['nido_id'], fb['uid'])
            return jsonify({'token': token_jwt, 'ok': True})
        conn.close()
        return jsonify({'error': 'Ya perteneces a otro nido'}), 409

    nido_id = inv['nido_id']

    # Asignar al segundo usuario existente del nido (o crear uno nuevo)
    segundo = conn.execute(
        '''SELECT u.id FROM usuarios u
           LEFT JOIN firebase_users fu ON fu.usuario_id=u.id
           WHERE u.nido_id=? AND fu.id IS NULL
           ORDER BY u.id LIMIT 1''',
        (nido_id,)
    ).fetchone()

    if segundo:
        usuario_id = segundo['id']
        if fb['nombre']:
            conn.execute('UPDATE usuarios SET nombre=? WHERE id=?',
                         (fb['nombre'], usuario_id))
    else:
        nombre_persona = fb['nombre'] or fb['email'].split('@')[0]
        cur = conn.execute(
            'INSERT INTO usuarios (nombre, color, emoji, nido_id) VALUES(?,?,?,?)',
            (nombre_persona, '#D4886A', 'person-fill', nido_id)
        )
        usuario_id = cur.lastrowid

    cur_fu = conn.execute(
        'INSERT INTO firebase_users (firebase_uid, email, nombre, foto, nido_id, usuario_id)'
        ' VALUES(?,?,?,?,?,?)',
        (fb['uid'], fb['email'], fb['nombre'], fb['foto'], nido_id, usuario_id)
    )
    user_id = cur_fu.lastrowid
    conn.execute("UPDATE invitaciones SET estado='aceptada' WHERE token=?", (token,))
    conn.commit()
    conn.close()

    token_jwt = generate_jwt(user_id, nido_id, fb['uid'])
    return jsonify({
        'token':      token_jwt,
        'nido_id':    nido_id,
        'usuario_id': usuario_id,
        'ok':         True,
    })


# ── /invite/<token>  Web landing page ────────────────────────────────────────

@auth_bp.route('/invite/<token>', methods=['GET'])
def web_invite_landing(token):
    """Página web que abre cuando el invitado recibe el link por WhatsApp."""
    conn = _get_db()
    inv = conn.execute('SELECT * FROM invitaciones WHERE token=?', (token,)).fetchone()
    valida = inv and inv['estado'] == 'pendiente'
    nido_nombre = 'Tu nido'
    invitado_por = 'Tu pareja'
    if valida:
        nido = conn.execute('SELECT nombre FROM nidos WHERE id=?', (inv['nido_id'],)).fetchone()
        primer_miembro = conn.execute(
            '''SELECT u.nombre FROM firebase_users fu
               JOIN usuarios u ON u.id=fu.usuario_id
               WHERE fu.nido_id=? ORDER BY fu.id LIMIT 1''',
            (inv['nido_id'],)
        ).fetchone()
        if nido:           nido_nombre = nido['nombre']
        if primer_miembro: invitado_por = primer_miembro['nombre']
    conn.close()
    return render_template('invite.html',
        valida=valida, token=token,
        nido_nombre=nido_nombre, invitado_por=invitado_por)
