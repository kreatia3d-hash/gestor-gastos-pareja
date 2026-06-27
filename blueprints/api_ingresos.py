import threading
from datetime import date

from flask import Blueprint, request, jsonify
from auth_routes import requiere_auth
from .helpers import _nido, _verificar_limite_free, get_db, auto_generar_mes

bp = Blueprint('ingresos', __name__)

_CATEGORIAS_INGRESO = [
    'Salario', 'Freelance', 'Alquiler', 'Inversiones', 'Pensión',
    'Subsidio', 'Beca', 'Premio', 'Regalo', 'Reembolso', 'Extra', 'Otros'
]


def _guardar_backup():
    from flask import current_app
    fn = current_app.config.get('guardar_backup')
    if fn:
        fn()


@bp.route('/api/ingresos/categorias')
def api_ingresos_categorias():
    return jsonify(_CATEGORIAS_INGRESO)


@bp.route('/api/ingresos')
def api_ingresos():
    mes = request.args.get('mes', date.today().month, type=int)
    anio = request.args.get('anio', date.today().year, type=int)
    mes_str = f"{anio}-{mes:02d}"
    nid = _nido()
    auto_generar_mes(mes_str, nid)
    conn = get_db()
    rows = conn.execute("""
        SELECT i.*, u.nombre as unombre, u.color as ucolor, u.emoji as uemoji
        FROM ingresos i
        LEFT JOIN usuarios u ON i.usuario_id=u.id
        WHERE strftime('%Y-%m', i.fecha)=? AND i.nido_id=?
        ORDER BY i.fecha DESC, i.created_at DESC
    """, (mes_str, nid)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/ingresos', methods=['POST'])
def api_ingreso_crear():
    d = request.get_json()
    usuario_id = d.get('usuario_id')
    desc = d.get('descripcion', '').strip()
    importe = float(d.get('importe', 0))
    conn = get_db()

    limite = _verificar_limite_free(conn, 'ingresos')
    if limite:
        conn.close()
        return limite

    cur = conn.execute("""INSERT INTO ingresos
        (usuario_id, descripcion, importe, fecha, es_nomina, notas, nido_id, categoria)
        VALUES(?,?,?,?,?,?,?,?)""", (
        usuario_id, desc, importe,
        d.get('fecha'), 1 if d.get('es_nomina') else 0,
        d.get('notas', '').strip(), _nido(),
        d.get('categoria', 'Salario').strip()
    ))
    conn.commit()
    iid = cur.lastrowid

    try:
        creado_por = d.get('creado_por') or usuario_id
        quien_row = conn.execute("SELECT nombre FROM usuarios WHERE id=?", (creado_por,)).fetchone() if creado_por else None
        quien = quien_row['nombre'] if quien_row else 'Alguien'
        otros = conn.execute("SELECT id FROM usuarios WHERE id != ?", (creado_por or -1,)).fetchall()
        for u in otros:
            conn.execute("""INSERT INTO notificaciones (usuario_id, titulo, cuerpo)
                VALUES(?,?,?)""", (u['id'], f'{quien} añadió un ingreso', f'{desc}: {importe:.2f}€'))
        conn.commit()

        # Enviar push FCM en segundo plano
        nido_id_push = _nido()
        titulo_push = f'{quien} añadió un ingreso'
        cuerpo_push = f'{desc}: {importe:.2f}€'
        import threading as _threading
        from blueprints.api_push import enviar_push_nido
        _threading.Thread(
            target=enviar_push_nido,
            args=(nido_id_push, creado_por or -1, titulo_push, cuerpo_push),
            daemon=True,
        ).start()
    except Exception:
        pass

    conn.close()
    return jsonify({'id': iid}), 201


@bp.route('/api/ingresos/<int:iid>', methods=['PUT'])
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


@bp.route('/api/ingresos/<int:iid>', methods=['DELETE'])
def api_ingreso_eliminar(iid):
    conn = get_db()
    conn.execute("DELETE FROM ingresos WHERE id=?", (iid,))
    conn.commit()
    conn.close()
    threading.Thread(target=_guardar_backup, daemon=True).start()
    return jsonify({'ok': True})
