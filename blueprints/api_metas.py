from flask import Blueprint, request, jsonify
from auth_routes import requiere_auth
from .helpers import _nido, _verificar_limite_free, get_db

bp = Blueprint('metas', __name__)


@bp.route('/api/metas')
def api_metas():
    conn = get_db()
    rows = conn.execute("""
        SELECT *, ROUND(importe_actual*100.0/NULLIF(importe_objetivo,0),1) as progreso
        FROM metas_ahorro WHERE nido_id=?
        ORDER BY completada ASC, fecha_limite ASC
    """, (_nido(),)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/metas', methods=['POST'])
def api_meta_crear():
    d = request.get_json()
    conn = get_db()

    limite = _verificar_limite_free(conn, 'metas_ahorro')
    if limite:
        conn.close()
        return limite

    cur = conn.execute("""INSERT INTO metas_ahorro
        (nombre, descripcion, importe_objetivo, fecha_limite, color, nido_id)
        VALUES(?,?,?,?,?,?)""", (
        d.get('nombre', '').strip(), d.get('descripcion', '').strip(),
        float(d.get('importe_objetivo', 0)),
        d.get('fecha_limite') or None, d.get('color', '#4f8ef7'), _nido()
    ))
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return jsonify({'id': mid}), 201


@bp.route('/api/metas/<int:mid>', methods=['PUT'])
def api_meta_editar(mid):
    d = request.get_json()
    conn = get_db()
    conn.execute("""UPDATE metas_ahorro SET nombre=?, descripcion=?, importe_objetivo=?,
        fecha_limite=?, color=? WHERE id=?""", (
        d.get('nombre', '').strip(), d.get('descripcion', '').strip(),
        float(d.get('importe_objetivo', 0)),
        d.get('fecha_limite') or None, d.get('color', '#4f8ef7'), mid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@bp.route('/api/metas/<int:mid>', methods=['DELETE'])
def api_meta_eliminar_api(mid):
    conn = get_db()
    conn.execute("DELETE FROM aportaciones_meta WHERE meta_id=?", (mid,))
    conn.execute("UPDATE gastos SET meta_id=NULL WHERE meta_id=?", (mid,))
    conn.execute("DELETE FROM metas_ahorro WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@bp.route('/api/metas/<int:mid>/aportaciones')
def api_meta_aportaciones(mid):
    conn = get_db()
    rows = conn.execute("""
        SELECT a.*, u.nombre as unombre, u.color as ucolor
        FROM aportaciones_meta a LEFT JOIN usuarios u ON a.usuario_id=u.id
        WHERE a.meta_id=? ORDER BY a.fecha DESC
    """, (mid,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/metas/<int:mid>/abonar', methods=['POST'])
def api_meta_abonar(mid):
    d = request.get_json()
    importe = float(d.get('importe', 0))
    conn = get_db()
    meta = conn.execute("SELECT * FROM metas_ahorro WHERE id=?", (mid,)).fetchone()
    conn.execute("""INSERT INTO aportaciones_meta (meta_id, usuario_id, importe, fecha, notas)
        VALUES(?,?,?,?,?)""", (mid, d.get('usuario_id'), importe, d.get('fecha'), d.get('notas', '')))
    nuevo_total = meta['importe_actual'] + importe
    completada = 1 if nuevo_total >= meta['importe_objetivo'] else 0
    conn.execute("UPDATE metas_ahorro SET importe_actual=?, completada=? WHERE id=?",
                 (nuevo_total, completada, mid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'completada': bool(completada)})
