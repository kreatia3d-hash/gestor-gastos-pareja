"""
Auth layer para Nido – Google (Firebase) Sign-In + JWT propio.
Todas las rutas nuevas de auth, nido e invitaciones van aquí.
"""
import os, uuid, json, secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, request, jsonify, render_template, current_app, g
import db as _db

try:
    import jwt as pyjwt
    import requests as _req
    _DEPS_OK = True
except ImportError:
    _DEPS_OK = False
    print('[auth] AVISO: PyJWT o requests no instalados. Auth desactivado.')

auth_bp = Blueprint('auth', __name__)

_PRO_EMAILS = frozenset({'kreatia3d@gmail.com'})

JWT_SECRET = os.environ.get('JWT_SECRET')
if not JWT_SECRET:
    raise RuntimeError('JWT_SECRET no configurado en variables de entorno. Añádelo en Railway antes de desplegar.')
FIREBASE_WEB_API_KEY = os.environ.get('FIREBASE_WEB_API_KEY', '')
BASE_URL            = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'web-production-2694.up.railway.app')


# ── Helpers DB ────────────────────────────────────────────────────────────────

def _get_db():
    _db.set_db_path(current_app.config['DB_PATH'])
    return _db.get_db()


# ── Firebase token verification ───────────────────────────────────────────────

def _verify_firebase_token(id_token: str):
    """Verifica un Firebase ID Token usando el endpoint accounts:lookup de Google.
    Requiere FIREBASE_WEB_API_KEY sin restricciones de plataforma.
    Devuelve dict con info del usuario, o lanza excepción con mensaje de error."""
    if not _DEPS_OK:
        raise ValueError('PyJWT/requests no instalado')
    if not FIREBASE_WEB_API_KEY:
        raise ValueError('FIREBASE_WEB_API_KEY no configurado')
    resp = _req.post(
        f'https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={FIREBASE_WEB_API_KEY}',
        json={'idToken': id_token},
        timeout=8,
    )
    if resp.status_code != 200:
        try:
            msg = resp.json().get('error', {}).get('message', resp.text)
        except Exception:
            msg = resp.text
        raise ValueError(f'accounts:lookup {resp.status_code}: {msg}')
    data = resp.json()
    users = data.get('users', [])
    if not users:
        raise ValueError('accounts:lookup: lista de usuarios vacía')
    u = users[0]
    return {
        'uid':    u.get('localId', ''),
        'email':  u.get('email', ''),
        'nombre': u.get('displayName', ''),
        'foto':   u.get('photoUrl', ''),
    }


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
            return jsonify({'error': 'Servicio de autenticación no disponible'}), 503
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

    try:
        fb = _verify_firebase_token(id_token)
    except Exception as e:
        print(f'[auth] verify failed: {e}')
        return jsonify({'error': f'Token inválido: {e}'}), 401

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
        if fb['email'].lower() in _PRO_EMAILS:
            conn.execute("UPDATE nidos SET plan='pro' WHERE id=?", (fuser['nido_id'],))
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
        if fb['email'].lower() in _PRO_EMAILS:
            conn.execute("UPDATE nidos SET plan='pro' WHERE id=1")
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
    # Auto-upgrade a PRO si el usuario está en la lista privilegiada
    fu_row = conn.execute('SELECT email FROM firebase_users WHERE id=?', (g.user_id,)).fetchone()
    plan_actual = nido['plan'] if nido else 'free'
    if fu_row and fu_row['email'].lower() in _PRO_EMAILS and plan_actual != 'pro':
        conn.execute("UPDATE nidos SET plan='pro' WHERE id=?", (g.nido_id,))
        conn.commit()
        plan_actual = 'pro'
    conn.close()
    return jsonify({
        'id':       nido['id'] if nido else g.nido_id,
        'nombre':   nido['nombre'] if nido else 'Mi Nido',
        'plan':     plan_actual,
        'miembros': [dict(m) for m in miembros],
    })


@auth_bp.route('/api/nido/pareja', methods=['DELETE'])
@requiere_auth
def api_nido_quitar_pareja():
    """Elimina al otro miembro del nido. Sus datos históricos permanecen en el nido."""
    conn = _get_db()
    miembros = conn.execute(
        'SELECT id, usuario_id, email FROM firebase_users WHERE nido_id=?', (g.nido_id,)
    ).fetchall()
    if len(miembros) < 2:
        conn.close()
        return jsonify({'error': 'No hay pareja que eliminar'}), 400
    otro = next((m for m in miembros if m['id'] != g.user_id), None)
    if not otro:
        conn.close()
        return jsonify({'error': 'No se encontró al otro miembro'}), 404
    # Desvincular del nido (los datos históricos se quedan en el nido)
    conn.execute('DELETE FROM firebase_users WHERE id=?', (otro['id'],))
    # Invalidar invitaciones activas del nido
    conn.execute("UPDATE invitaciones SET estado='expirada' WHERE nido_id=? AND estado='pendiente'",
                 (g.nido_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@auth_bp.route('/api/nido/pareja/datos', methods=['DELETE'])
@requiere_auth
def api_borrar_datos_pareja():
    """Elimina todos los gastos e ingresos de un usuario concreto en este nido."""
    usuario_id = request.args.get('usuario_id', type=int)
    if not usuario_id:
        return jsonify({'error': 'Falta usuario_id'}), 400
    conn = _get_db()
    # El usuario debe pertenecer (o haber pertenecido) al nido
    en_nido = conn.execute(
        'SELECT 1 FROM usuarios WHERE id=? AND nido_id=?', (usuario_id, g.nido_id)
    ).fetchone()
    if not en_nido:
        conn.close()
        return jsonify({'error': 'Usuario no pertenece a este nido'}), 403
    n_g = conn.execute(
        "SELECT COUNT(*) as c FROM gastos WHERE usuario_id=? AND nido_id=?",
        (usuario_id, g.nido_id)
    ).fetchone()['c']
    n_i = conn.execute(
        "SELECT COUNT(*) as c FROM ingresos WHERE usuario_id=? AND nido_id=?",
        (usuario_id, g.nido_id)
    ).fetchone()['c']
    conn.execute("DELETE FROM gastos WHERE usuario_id=? AND nido_id=?",
                 (usuario_id, g.nido_id))
    conn.execute("DELETE FROM ingresos WHERE usuario_id=? AND nido_id=?",
                 (usuario_id, g.nido_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'gastos_borrados': n_g, 'ingresos_borrados': n_i})


@auth_bp.route('/api/nido/plan', methods=['POST'])
@requiere_auth
def api_nido_plan():
    """Actualiza el plan del nido ('free' o 'pro'). Lo llama Flutter tras una compra RevenueCat."""
    d = request.get_json(silent=True) or {}
    plan = d.get('plan', '').lower()
    if plan not in ('free', 'pro'):
        return jsonify({'error': 'plan inválido'}), 400
    conn = _get_db()
    conn.execute("UPDATE nidos SET plan=? WHERE id=?", (plan, g.nido_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'plan': plan})


@auth_bp.route('/api/nido/crear', methods=['POST'])
def api_nido_crear():
    """Crea un nido nuevo para un usuario que acaba de registrarse.
    Requiere id_token de Firebase (no JWT todavía)."""
    d = request.get_json(silent=True) or {}
    id_token = d.get('id_token', '')
    nombre_nido = d.get('nombre', 'Mi Nido').strip() or 'Mi Nido'

    try:
        fb = _verify_firebase_token(id_token)
    except Exception as e:
        return jsonify({'error': f'Token inválido: {e}'}), 401

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
    if fb['email'].lower() in _PRO_EMAILS:
        conn.execute("UPDATE nidos SET plan='pro' WHERE id=?", (nido_id,))
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

    # Invalidar invitaciones anteriores de este nido
    conn.execute(
        "UPDATE invitaciones SET estado='expirada' WHERE nido_id=? AND estado='pendiente'",
        (g.nido_id,)
    )
    token = secrets.token_urlsafe(16)
    expires = (datetime.utcnow() + timedelta(days=7)).isoformat()
    conn.execute(
        'INSERT INTO invitaciones (nido_id, creada_por, token, expires_at) VALUES(?,?,?,?)',
        (g.nido_id, g.user_id, token, expires)
    )
    conn.commit()
    conn.close()

    invite_url = f'https://{BASE_URL}/invite/{token}'
    return jsonify({'token': token, 'url': invite_url, 'expires_at': expires})


# ── Fusión de nidos ───────────────────────────────────────────────────────────

def _fusionar_nidos(conn, nido_origen: int, nido_destino: int) -> dict:
    """Mueve todos los datos de nido_origen a nido_destino. Devuelve estadísticas."""
    stats = {}

    # Tablas con nido_id directo
    for tabla in ('gastos', 'ingresos', 'metas_ahorro', 'presupuestos', 'notificaciones'):
        try:
            r = conn.execute(f"SELECT COUNT(*) as c FROM {tabla} WHERE nido_id=?",
                             (nido_origen,)).fetchone()
            n = r['c'] if r else 0
            if n:
                conn.execute(f"UPDATE {tabla} SET nido_id=? WHERE nido_id=?",
                             (nido_destino, nido_origen))
            stats[tabla] = n
        except Exception:
            stats[tabla] = 0

    # Categorías: migrar con deduplicación por nombre
    cats_orig = conn.execute(
        "SELECT * FROM categorias_gasto WHERE nido_id=?", (nido_origen,)
    ).fetchall()
    cat_map = {}
    for cat in cats_orig:
        existing = conn.execute(
            "SELECT id FROM categorias_gasto WHERE nido_id=? AND nombre=?",
            (nido_destino, cat['nombre'])
        ).fetchone()
        if existing:
            cat_map[cat['id']] = existing['id']
        else:
            cur = conn.execute(
                "INSERT INTO categorias_gasto (nombre, color, icono, activa, es_ahorro, nido_id)"
                " VALUES (?,?,?,?,?,?)",
                (cat['nombre'], cat['color'], cat['icono'],
                 cat['activa'], cat['es_ahorro'], nido_destino)
            )
            cat_map[cat['id']] = cur.lastrowid

    # Reasignar categoria_id en gastos migrados
    for old_id, new_id in cat_map.items():
        if old_id != new_id:
            conn.execute(
                "UPDATE gastos SET categoria_id=? WHERE categoria_id=? AND nido_id=?",
                (new_id, old_id, nido_destino)
            )

    # Mover usuarios del nido origen al destino
    conn.execute("UPDATE usuarios SET nido_id=? WHERE nido_id=?",
                 (nido_destino, nido_origen))

    # Limpiar nido origen
    conn.execute("DELETE FROM categorias_gasto WHERE nido_id=?", (nido_origen,))
    conn.execute("UPDATE invitaciones SET estado='expirada' WHERE nido_id=?", (nido_origen,))
    conn.execute("DELETE FROM nidos WHERE id=?", (nido_origen,))

    return stats


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

    try:
        fb = _verify_firebase_token(id_token)
    except Exception as e:
        return jsonify({'error': f'Token inválido: {e}'}), 401

    conn = _get_db()
    inv = conn.execute('SELECT * FROM invitaciones WHERE token=?', (token,)).fetchone()
    if not inv or inv['estado'] != 'pendiente':
        conn.close()
        return jsonify({'error': 'Invitación no válida o ya usada'}), 410
    if datetime.fromisoformat(inv['expires_at']) < datetime.utcnow():
        conn.close()
        return jsonify({'error': 'Invitación expirada'}), 410

    # Nido lleno: ya hay 2 miembros activos
    nido_lleno = conn.execute(
        'SELECT COUNT(*) as c FROM firebase_users WHERE nido_id=?', (inv['nido_id'],)
    ).fetchone()['c'] >= 2

    # ¿El usuario ya tiene nido?
    fuser = conn.execute(
        'SELECT * FROM firebase_users WHERE firebase_uid=?', (fb['uid'],)
    ).fetchone()
    if fuser:
        if fuser['nido_id'] == inv['nido_id']:
            conn.close()
            token_jwt = generate_jwt(fuser['id'], fuser['nido_id'], fb['uid'])
            return jsonify({'token': token_jwt, 'ok': True})
        # Ya tiene otro nido → fusionarlo con el nido destino
        if not nido_lleno:
            stats = _fusionar_nidos(conn, fuser['nido_id'], inv['nido_id'])
            # Actualizar firebase_user al nido destino
            conn.execute('UPDATE firebase_users SET nido_id=? WHERE id=?',
                         (inv['nido_id'], fuser['id']))
            conn.execute("UPDATE invitaciones SET estado='aceptada' WHERE token=?", (token,))
            conn.commit()
            conn.close()
            token_jwt = generate_jwt(fuser['id'], inv['nido_id'], fb['uid'])
            return jsonify({
                'token':          token_jwt,
                'nido_id':        inv['nido_id'],
                'usuario_id':     fuser['usuario_id'],
                'ok':             True,
                'merge_realizado': True,
                'merge_stats':    stats,
            })
        conn.close()
        return jsonify({'error': 'El nido de destino ya está completo'}), 400

    nido_id = inv['nido_id']

    if nido_lleno:
        conn.close()
        return jsonify({'error': 'El nido ya está completo'}), 400

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


# ── /api/referidos  Programa de referidos ────────────────────────────────────

@auth_bp.route('/api/referidos', methods=['GET'])
@requiere_auth
def api_referidos():
    """Devuelve el número de amigos que se han unido via invitación del usuario actual."""
    conn = _get_db()
    total = conn.execute(
        "SELECT COUNT(*) as c FROM invitaciones WHERE creada_por=? AND estado='aceptada'",
        (g.user_id,)
    ).fetchone()['c']
    conn.close()
    return jsonify({'total_referidos': total, 'user_id': g.user_id})


# ── /.well-known/assetlinks.json  (Android App Links verification) ───────────

@auth_bp.route('/.well-known/assetlinks.json', methods=['GET'])
def assetlinks():
    """Permite que Android verifique el App Link y abra la app directamente."""
    from flask import Response
    data = json.dumps([{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": "com.gestorgastos.gastos_en_pareja",
            "sha256_cert_fingerprints": [
                "12:26:82:A1:4B:80:1A:1E:C9:88:48:37:0A:8C:1A:4D:23:48:F0:DF:31:43:BF:A6:59:BC:90:4F:79:BA:A5:D9"
            ]
        }
    }])
    return Response(data, mimetype='application/json')


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
    download_url     = os.environ.get('DOWNLOAD_APK_URL', '')
    play_store_url   = os.environ.get('PLAY_STORE_URL', '')
    return _render_invite(valida, token, invitado_por, download_url, play_store_url)


def _render_invite(valida, token, invitado_por, download_url='', play_store_url=''):
    # Sección de descarga — siempre visible, con al menos un fallback
    if play_store_url:
        descarga_html = f'<a href="{play_store_url}" class="btn btn-download">⬇ Descargar en Google Play</a>'
    elif download_url:
        descarga_html = f'<a href="{download_url}" class="btn btn-download">⬇ Descargar Nido (Android)</a>'
    else:
        descarga_html = '<p class="hint-box">📱 Busca <strong>Nido by Kreatia</strong> en la Play Store para instalar la app.</p>'

    if valida:
        content = f'''
      <div class="invite-label">🪺 Invitación personal</div>
      <h1>{invitado_por} te invita<br>a su nido</h1>
      <p class="desc">Gestiona el dinero en pareja: gastos, ingresos, metas de ahorro y análisis con IA.</p>

      <div class="codigo-box" onclick="copiarCodigo()" title="Pulsa para copiar">
        <div class="codigo-label">Tu código de invitación</div>
        <div class="codigo-valor" id="codigo-texto">{token}</div>
        <div class="codigo-hint" id="copia-msg">📋 Pulsa para copiar</div>
      </div>

      <button class="btn btn-primary" id="btn-abrir" onclick="abrirApp()">
        🪺 Ya tengo Nido — Unirme al nido
      </button>
      <div id="app-no-instalada" style="display:none">
        <p class="hint-box">La app no se abrió automáticamente.<br>
          Descárgala y usa el código <strong>{token}</strong> para unirte.</p>
      </div>

      <div class="o-sep">¿No tienes la app todavía?</div>
      {descarga_html}

      <p class="hint">Tras instalar Nido: inicia sesión con Google y podrás <strong>unirte a este nido</strong> con el código de arriba, o <strong>crear el tuyo propio</strong> con tu pareja.</p>'''
    else:
        content = f'''
      <div style="font-size:56px;text-align:center;margin-bottom:20px">⏳</div>
      <h1>Invitación no disponible</h1>
      <p class="desc">El enlace ha expirado o ya fue utilizado.<br>Pide a tu pareja que genere uno nuevo desde Nido.</p>
      <div class="o-sep">¿Quieres probar Nido?</div>
      {descarga_html}'''

    html = f'''<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{invitado_por} te invita a Nido</title>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    *,*::before,*::after{{margin:0;padding:0;box-sizing:border-box}}
    :root{{--bosque:#3F5E54;--salvia:#5C8374;--crema:#F5EDE0;--dorado:#E8B86D;--carbon:#2D2D2D}}
    body{{font-family:'Inter',sans-serif;background:var(--bosque);min-height:100vh;
         display:flex;flex-direction:column;align-items:center;justify-content:center;
         padding:20px 16px;position:relative;overflow-x:hidden}}
    body::before{{content:'';position:fixed;top:-30%;right:-20%;width:500px;height:500px;
                  background:radial-gradient(circle,rgba(92,131,116,.3) 0%,transparent 70%);pointer-events:none}}
    .wrapper{{width:100%;max-width:420px;position:relative;z-index:1}}
    .marca{{text-align:center;margin-bottom:20px}}
    .marca-logo{{font-family:'Fraunces',serif;font-size:34px;font-weight:700;color:var(--crema)}}
    .marca-sub{{font-size:11px;color:rgba(245,237,224,.45);letter-spacing:2px;text-transform:uppercase;margin-top:2px}}
    .card{{background:var(--crema);border-radius:24px;padding:32px 28px 28px;box-shadow:0 24px 64px rgba(0,0,0,.35)}}
    .invite-label{{display:inline-flex;align-items:center;gap:6px;background:rgba(92,131,116,.12);
                   color:var(--salvia);font-size:12px;font-weight:600;letter-spacing:.5px;
                   padding:5px 12px;border-radius:20px;margin-bottom:14px}}
    h1{{font-family:'Fraunces',serif;font-size:22px;font-weight:700;color:var(--carbon);
        line-height:1.25;margin-bottom:10px}}
    .desc{{color:#6B7B74;font-size:14px;line-height:1.6;margin-bottom:22px}}
    .codigo-box{{background:var(--bosque);border-radius:14px;padding:18px 16px;margin-bottom:18px;
                 text-align:center;cursor:pointer;transition:opacity .15s;user-select:none;
                 -webkit-tap-highlight-color:transparent}}
    .codigo-box:active{{opacity:.85}}
    .codigo-label{{color:rgba(245,237,224,.55);font-size:10px;letter-spacing:2px;
                   text-transform:uppercase;margin-bottom:10px}}
    .codigo-valor{{font-family:'Fraunces',serif;font-size:38px;font-weight:700;
                   color:var(--dorado);letter-spacing:5px;word-break:break-all}}
    .codigo-hint{{color:rgba(245,237,224,.5);font-size:11px;margin-top:8px;display:flex;
                  align-items:center;justify-content:center;gap:5px}}
    .btn{{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;
          padding:16px 20px;border-radius:14px;font-family:'Inter',sans-serif;font-size:15px;
          font-weight:600;text-decoration:none;margin-bottom:10px;border:none;cursor:pointer;
          transition:transform .15s,box-shadow .15s;-webkit-tap-highlight-color:transparent}}
    .btn:active{{transform:scale(.97)}}
    .btn-primary{{background:var(--bosque);color:var(--crema);box-shadow:0 6px 20px rgba(63,94,84,.3)}}
    .btn-download{{background:linear-gradient(135deg,var(--dorado),#d4a55a);color:var(--carbon);
                   box-shadow:0 6px 20px rgba(232,184,109,.35)}}
    .hint{{text-align:center;color:#9BA8A3;font-size:12px;line-height:1.6;margin-top:6px}}
    .hint strong{{color:#6B7B74}}
    .hint-box{{background:rgba(92,131,116,.1);border-radius:10px;padding:12px 14px;
               text-align:center;color:#5C8374;font-size:13px;line-height:1.5;margin-bottom:12px}}
    .hint-box strong{{color:var(--bosque)}}
    .steps{{margin-bottom:20px}}
    .step{{display:flex;align-items:flex-start;gap:10px;margin-bottom:10px}}
    .step-num{{background:var(--bosque);color:var(--crema);border-radius:50%;width:22px;height:22px;
               display:flex;align-items:center;justify-content:center;font-size:11px;
               font-weight:700;flex-shrink:0;margin-top:1px}}
    .step-text{{font-size:13px;color:#4A5A54;line-height:1.45}}
    .o-sep{{display:flex;align-items:center;gap:8px;margin:14px 0 10px;color:#9BA8A3;font-size:12px}}
    .o-sep::before,.o-sep::after{{content:'';flex:1;height:1px;background:rgba(155,168,163,.25)}}
    .footer{{text-align:center;margin-top:16px;color:rgba(245,237,224,.3);font-size:11px}}
    @media(max-width:400px){{.card{{padding:24px 20px 22px}}.codigo-valor{{font-size:32px}}h1{{font-size:20px}}}}
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="marca">
      <div class="marca-logo">Nido</div>
      <div class="marca-sub">by Kreatia</div>
    </div>
    <div class="card">{content}</div>
    <div class="footer">Nido by Kreatia &middot; Solo para parejas</div>
  </div>
  <script>
    function copiarCodigo() {{
      var codigo = '{token}';
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(codigo).then(function() {{
          document.getElementById('copia-msg').textContent = '✓ ¡Copiado!';
          setTimeout(function() {{ document.getElementById('copia-msg').textContent = '📋 Pulsa para copiar'; }}, 2200);
        }});
      }} else {{
        var ta = document.createElement('textarea');
        ta.value = codigo; ta.style.position='fixed'; ta.style.opacity='0';
        document.body.appendChild(ta); ta.select();
        document.execCommand('copy'); document.body.removeChild(ta);
        document.getElementById('copia-msg').textContent = '✓ ¡Copiado!';
        setTimeout(function() {{ document.getElementById('copia-msg').textContent = '📋 Pulsa para copiar'; }}, 2200);
      }}
    }}
    function abrirApp() {{
      var inicio = Date.now();
      var iframe = document.createElement('iframe');
      iframe.style.display='none';
      iframe.src = 'nido://invite/{token}';
      document.body.appendChild(iframe);
      setTimeout(function() {{
        document.body.removeChild(iframe);
        if (Date.now() - inicio < 3000) {{
          var d = document.getElementById('app-no-instalada');
          if (d) d.style.display='block';
          var b = document.getElementById('btn-abrir');
          if (b) b.style.display='none';
        }}
      }}, 2500);
    }}
  </script>
</body>
</html>'''
    from flask import make_response
    return make_response(html, 200)
