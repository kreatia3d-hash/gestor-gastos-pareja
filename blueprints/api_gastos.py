import threading
from datetime import date

from flask import Blueprint, request, jsonify
from auth_routes import requiere_auth
from .helpers import _nido, _verificar_limite_free, get_db, auto_generar_mes

bp = Blueprint('gastos', __name__)


# Importado tardío para evitar ciclo — guardar_backup vive en app.py pero se
# inyecta en cada blueprint a través del contexto de la aplicación.
def _guardar_backup():
    from flask import current_app
    # app.py expone guardar_backup como atributo del app object
    fn = current_app.config.get('guardar_backup')
    if fn:
        fn()


@bp.route('/api/categorias')
def api_categorias():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM categorias_gasto WHERE nido_id=? ORDER BY nombre", (_nido(),)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@bp.route('/api/categorias', methods=['POST'])
def api_categoria_crear():
    d = request.get_json()
    conn = get_db()
    cur = conn.execute("INSERT INTO categorias_gasto (nombre, color, icono) VALUES(?,?,?)", (
        d.get('nombre', '').strip(), d.get('color', '#6c757d'), d.get('icono', 'bi-tag')))
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return jsonify({'id': cid}), 201


@bp.route('/api/categorias/<int:cid>', methods=['DELETE'])
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


@bp.route('/api/gastos')
def api_gastos():
    mes = request.args.get('mes', date.today().month, type=int)
    anio = request.args.get('anio', date.today().year, type=int)
    cat_filtro = request.args.get('cat', '')
    usr_filtro = request.args.get('usr', '')
    fijo_filtro = request.args.get('fijo', '')
    mes_str = f"{anio}-{mes:02d}"
    nid = _nido()
    auto_generar_mes(mes_str, nid)
    conn = get_db()
    q = """SELECT g.*, u.nombre as unombre, u.color as ucolor, u.emoji as uemoji,
               c.nombre as cnombre, c.color as ccolor, c.icono as cicono
        FROM gastos g
        LEFT JOIN usuarios u ON g.usuario_id=u.id
        LEFT JOIN categorias_gasto c ON g.categoria_id=c.id
        WHERE strftime('%Y-%m', g.fecha)=? AND g.nido_id=?"""
    params = [mes_str, nid]
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


@bp.route('/api/gastos', methods=['POST'])
def api_gasto_crear():
    d = request.get_json()
    importe = float(d.get('importe', 0))
    meta_id = d.get('meta_id')
    usuario_id = d.get('usuario_id')
    categoria_id = d.get('categoria_id')
    conn = get_db()

    limite = _verificar_limite_free(conn, 'gastos')
    if limite:
        conn.close()
        return limite

    cur = conn.execute("""INSERT INTO gastos
        (usuario_id, categoria_id, descripcion, importe, fecha, es_fijo, notas, meta_id, nido_id)
        VALUES(?,?,?,?,?,?,?,?,?)""", (
        usuario_id, categoria_id,
        d.get('descripcion', '').strip(),
        importe,
        d.get('fecha'), 1 if d.get('es_fijo') else 0,
        d.get('notas', '').strip(), meta_id, _nido()
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
                    VALUES(?,?,?,?,?,?)""", (meta_id, usuario_id, importe, d.get('fecha'), d.get('notas', ''), gid))

    conn.commit()

    # Notificar a los demás usuarios en transacción separada (no bloquea la creación)
    try:
        desc = d.get('descripcion', '').strip()
        creado_por = d.get('creado_por') or usuario_id
        quien_row = conn.execute("SELECT nombre FROM usuarios WHERE id=?", (creado_por,)).fetchone() if creado_por else None
        quien = quien_row['nombre'] if quien_row else 'Alguien'
        otros = conn.execute("SELECT id FROM usuarios WHERE id != ?", (creado_por or -1,)).fetchall()
        for u in otros:
            conn.execute("""INSERT INTO notificaciones (usuario_id, titulo, cuerpo)
                VALUES(?,?,?)""", (u['id'], f'{quien} añadió un gasto', f'{desc}: {importe:.2f}€'))
        conn.commit()

        # Enviar push FCM en segundo plano (no bloquea la respuesta)
        nido_id_push = _nido()
        titulo_push = f'{quien} añadió un gasto'
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

    # Actualizar backup tras cambio de datos
    threading.Thread(target=_guardar_backup, daemon=True).start()

    return jsonify({'id': gid}), 201


@bp.route('/api/gastos/<int:gid>', methods=['PUT'])
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


@bp.route('/api/gastos/<int:gid>', methods=['DELETE'])
def api_gasto_eliminar(gid):
    conn = get_db()
    conn.execute("DELETE FROM gastos WHERE id=?", (gid,))
    conn.commit()
    conn.close()
    threading.Thread(target=_guardar_backup, daemon=True).start()
    return jsonify({'ok': True})
