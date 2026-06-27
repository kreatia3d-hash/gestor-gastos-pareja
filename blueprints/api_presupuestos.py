from datetime import date

from flask import Blueprint, request, jsonify
from auth_routes import requiere_auth
from .helpers import _nido, _verificar_limite_free, get_db

bp = Blueprint('presupuestos', __name__)


@bp.route('/api/presupuestos')
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
        WHERE p.mes=? AND p.anio=?
        ORDER BY c.nombre
    """, (mes_str, mes, anio)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/presupuestos', methods=['POST'])
def api_presupuesto_crear():
    d = request.get_json()
    hoy = date.today()
    mes = int(d.get('mes', hoy.month))
    anio = int(d.get('anio', hoy.year))
    cat_id = d.get('categoria_id')
    importe = float(d.get('importe_mensual', 0))
    conn = get_db()

    limite = _verificar_limite_free(conn, 'presupuestos', mes=mes, anio=anio)
    if limite:
        conn.close()
        return limite

    try:
        existe = conn.execute(
            "SELECT id FROM presupuestos WHERE categoria_id=? AND mes=? AND anio=?",
            (cat_id, mes, anio)
        ).fetchone()
        if existe:
            conn.execute("UPDATE presupuestos SET importe_mensual=? WHERE id=?",
                         (importe, existe['id']))
            pid = existe['id']
        else:
            cur = conn.execute(
                "INSERT INTO presupuestos (categoria_id, importe_mensual, mes, anio) VALUES(?,?,?,?)",
                (cat_id, importe, mes, anio)
            )
            pid = cur.lastrowid
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({'ok': False, 'error': str(e)}), 400
    conn.close()
    return jsonify({'id': pid}), 201


@bp.route('/api/presupuestos/<int:pid>', methods=['PUT'])
def api_presupuesto_editar(pid):
    d = request.get_json()
    conn = get_db()
    conn.execute("UPDATE presupuestos SET importe_mensual=? WHERE id=?",
                 (float(d.get('importe_mensual', 0)), pid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@bp.route('/api/presupuestos/<int:pid>', methods=['DELETE'])
def api_presupuesto_eliminar(pid):
    conn = get_db()
    conn.execute("DELETE FROM presupuestos WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})
