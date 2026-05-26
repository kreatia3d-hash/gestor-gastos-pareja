import os
from flask import Blueprint, request, jsonify, send_from_directory
from auth_routes import requiere_auth
from .helpers import _nido, get_db, DATA_DIR, _upload_cloudinary

bp = Blueprint('usuarios', __name__)


@bp.route('/api/usuarios')
def api_usuarios():
    conn = get_db()
    # Solo usuarios que han autenticado (tienen firebase_users), no placeholders de seed
    rows = conn.execute(
        """SELECT u.id, u.nombre, u.color, u.emoji, u.foto, u.pin
           FROM usuarios u
           WHERE u.nido_id=?
             AND EXISTS (SELECT 1 FROM firebase_users fu WHERE fu.usuario_id = u.id)""",
        (_nido(),)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['tiene_pin'] = bool(d.pop('pin'))
        result.append(d)
    return jsonify(result)


@bp.route('/api/usuarios/<int:uid>', methods=['PUT'])
def api_usuario_editar(uid):
    d = request.get_json()
    conn = get_db()
    conn.execute("UPDATE usuarios SET nombre=?, color=?, emoji=? WHERE id=?", (
        d.get('nombre', '').strip(), d.get('color', '#4f8ef7'), d.get('emoji', 'person'), uid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@bp.route('/api/usuarios/<int:uid>/pin', methods=['PUT'])
def api_usuario_pin(uid):
    d = request.get_json()
    pin = d.get('pin', '').strip()
    conn = get_db()
    conn.execute("UPDATE usuarios SET pin=? WHERE id=?", (pin if pin else None, uid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@bp.route('/api/usuarios/<int:uid>/foto_flutter', methods=['POST'])
def api_usuario_foto_flutter(uid):
    foto = request.files.get('foto')
    if not foto or not foto.filename:
        return jsonify({'ok': False, 'error': 'Sin archivo'}), 400
    file_bytes = foto.read()
    cloudinary_url = os.environ.get('CLOUDINARY_URL', '')
    if cloudinary_url:
        try:
            url = _upload_cloudinary(file_bytes, f'usuario_{uid}')
            conn = get_db()
            conn.execute("UPDATE usuarios SET foto=? WHERE id=?", (url, uid))
            conn.commit()
            conn.close()
            return jsonify({'ok': True, 'foto': url})
        except Exception as e:
            return jsonify({'ok': False, 'error': f'Cloudinary: {e}'}), 500
    else:
        # Fallback local (sin Cloudinary configurado — solo para desarrollo)
        ext = (foto.filename.rsplit('.', 1)[-1].lower() or 'jpg')
        if ext not in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
            ext = 'jpg'
        fotos_dir = os.path.join(DATA_DIR, 'fotos')
        os.makedirs(fotos_dir, exist_ok=True)
        nombre = f'usuario_{uid}.{ext}'
        with open(os.path.join(fotos_dir, nombre), 'wb') as f:
            f.write(file_bytes)
        conn = get_db()
        conn.execute("UPDATE usuarios SET foto=? WHERE id=?", (nombre, uid))
        conn.commit()
        conn.close()
        return jsonify({'ok': True, 'foto': nombre})


@bp.route('/api/fotos/<path:filename>')
def api_foto_serve(filename):
    fotos_dir = os.path.join(DATA_DIR, 'fotos')
    return send_from_directory(fotos_dir, filename)
