"""
Auth layer para Nido – Google (Firebase) Sign-In + JWT propio.
Todas las rutas nuevas de auth, nido e invitaciones van aquí.
"""
import os, uuid, json, sqlite3, random
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


# ── /api/auth/debug ──────────────────────────────────────────────────────────

@auth_bp.route('/api/auth/debug', methods=['GET'])
def api_auth_debug():
    """Endpoint temporal para diagnosticar configuración de Firebase."""
    api_key = FIREBASE_WEB_API_KEY
    result = {
        'deps_ok': _DEPS_OK,
        'firebase_key_set': bool(api_key),
        'firebase_key_prefix': api_key[:12] + '...' if api_key else None,
        'firebase_project_id': os.environ.get('FIREBASE_PROJECT_ID', 'NO CONFIGURADO'),
        'jwt_secret_set': bool(JWT_SECRET and JWT_SECRET != 'nido-secret-cambia-en-produccion-2025'),
    }
    if api_key and _DEPS_OK:
        try:
            resp = _req.post(
                f'https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={api_key}',
                json={'idToken': 'test'},
                timeout=8
            )
            result['firebase_api_response'] = resp.json().get('error', {}).get('message', 'OK')
        except Exception as e:
            result['firebase_api_response'] = f'ERROR: {e}'
    return jsonify(result)


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
    token = f'{random.randint(100000, 999999)}'
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
    download_url = os.environ.get('DOWNLOAD_APK_URL', '')
    return _render_invite(valida, token, invitado_por, download_url)


def _render_invite(valida, token, invitado_por, download_url):
    dl_btn = f'''
      <a href="{download_url}" class="btn btn-download">
        <span>⬇</span> Descargar Nido (Android)
      </a>''' if download_url else ''

    if valida:
        content = f'''
      <span class="nido-icon">🪺</span>
      <div class="invite-label">Invitación personal</div>
      <h1>{invitado_por} quiere compartir el nido contigo</h1>
      <p class="desc">Gestiona el dinero en pareja de forma sencilla y sin discusiones.
        Gastos compartidos, metas de ahorro, y siempre al día — todo en un mismo lugar.</p>
      <div class="features">
        <div class="feature"><span class="feature-icon">💸</span>
          <span class="feature-text">Registra y divide gastos al instante</span></div>
        <div class="feature"><span class="feature-icon">🎯</span>
          <span class="feature-text">Crea metas de ahorro compartidas</span></div>
        <div class="feature"><span class="feature-icon">🔒</span>
          <span class="feature-text">Privado — solo vosotros dos lo veis</span></div>
      </div>
      {dl_btn}
      <a href="nido://invite/{token}" class="btn btn-primary">
        <span>🪺</span> Ya tengo la app — Unirme
      </a>
      <p class="hint">Descarga la app, inicia sesión con Google y vuelve aquí para unirte.</p>'''
    else:
        content = f'''
      <span class="nido-icon">⏳</span>
      <h1>Esta invitación ya no está disponible</h1>
      <p class="desc">El enlace ha expirado o ya fue utilizado.<br>
        Pide a tu pareja que genere uno nuevo desde Nido.</p>
      {dl_btn}'''

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
         display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px;
         position:relative;overflow-x:hidden}}
    body::before{{content:'';position:fixed;top:-30%;right:-20%;width:600px;height:600px;
                  background:radial-gradient(circle,rgba(92,131,116,.35) 0%,transparent 70%);pointer-events:none}}
    .wrapper{{width:100%;max-width:440px;position:relative;z-index:1}}
    .marca{{text-align:center;margin-bottom:28px}}
    .marca-logo{{font-family:'Fraunces',serif;font-size:38px;font-weight:700;color:var(--crema)}}
    .marca-sub{{font-size:12px;color:rgba(245,237,224,.5);letter-spacing:2px;text-transform:uppercase;margin-top:2px}}
    .card{{background:var(--crema);border-radius:28px;padding:40px 32px 36px;box-shadow:0 32px 80px rgba(0,0,0,.35)}}
    .nido-icon{{font-size:56px;text-align:center;margin-bottom:20px;display:block}}
    .invite-label{{display:inline-block;background:rgba(92,131,116,.12);color:var(--salvia);
                   font-size:12px;font-weight:600;letter-spacing:1px;text-transform:uppercase;
                   padding:4px 12px;border-radius:20px;margin-bottom:12px}}
    h1{{font-family:'Fraunces',serif;font-size:24px;font-weight:700;color:var(--carbon);
        line-height:1.25;margin-bottom:12px}}
    .desc{{color:#6B7B74;font-size:15px;line-height:1.65;margin-bottom:24px}}
    .features{{display:flex;flex-direction:column;gap:10px;margin-bottom:28px}}
    .feature{{display:flex;align-items:center;gap:12px;background:rgba(63,94,84,.06);
              border-radius:12px;padding:12px 16px}}
    .feature-icon{{font-size:20px;flex-shrink:0}}
    .feature-text{{font-size:13px;color:#4A5A54;font-weight:500}}
    .btn{{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;
          padding:17px 24px;border-radius:16px;font-family:'Inter',sans-serif;font-size:16px;
          font-weight:600;text-decoration:none;margin-bottom:12px;border:none;cursor:pointer;
          transition:transform .15s,box-shadow .15s}}
    .btn:active{{transform:scale(.98)}}
    .btn-primary{{background:var(--bosque);color:var(--crema);box-shadow:0 8px 24px rgba(63,94,84,.35)}}
    .btn-primary:hover{{background:#344f46;transform:translateY(-1px)}}
    .btn-download{{background:linear-gradient(135deg,var(--dorado),#d4a55a);color:var(--carbon);
                   box-shadow:0 8px 24px rgba(232,184,109,.4)}}
    .btn-download:hover{{transform:translateY(-1px)}}
    .hint{{text-align:center;color:#9BA8A3;font-size:12px;line-height:1.6;margin-top:4px}}
    .footer{{text-align:center;margin-top:20px;color:rgba(245,237,224,.35);font-size:12px}}
    @media(max-width:480px){{.card{{padding:32px 24px 28px}}h1{{font-size:20px}}}}
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="marca">
      <div class="marca-logo">Nido</div>
      <div class="marca-sub">by Kreatia</div>
    </div>
    <div class="card">{content}</div>
    <div class="footer">Nido by Kreatia · Solo para parejas</div>
  </div>
</body>
</html>'''
    from flask import make_response
    return make_response(html, 200)
