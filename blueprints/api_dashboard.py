import threading
from collections import defaultdict
from datetime import date

from flask import Blueprint, request, jsonify
from auth_routes import requiere_auth
from .helpers import _nido, get_db, NOMBRES_MESES, auto_generar_mes

bp = Blueprint('dashboard', __name__)


def _guardar_backup():
    from flask import current_app
    fn = current_app.config.get('guardar_backup')
    if fn:
        fn()


@bp.route('/api/dashboard')
def api_dashboard():
    mes = request.args.get('mes', date.today().month, type=int)
    anio = request.args.get('anio', date.today().year, type=int)
    mes_str = f"{anio}-{mes:02d}"
    nid = _nido()
    auto_generar_mes(mes_str, nid)
    conn = get_db()

    total_gastos = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE strftime('%Y-%m',fecha)=? AND nido_id=?",
        (mes_str, nid)
    ).fetchone()['t']
    total_ingresos = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM ingresos WHERE strftime('%Y-%m',fecha)=? AND nido_id=?",
        (mes_str, nid)
    ).fetchone()['t']
    gastos_fijos = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE es_fijo=1 AND strftime('%Y-%m',fecha)=? AND nido_id=?",
        (mes_str, nid)
    ).fetchone()['t']

    gastos_cat = [dict(r) for r in conn.execute("""
        SELECT c.nombre, c.color, c.icono, COALESCE(SUM(g.importe),0) as total
        FROM categorias_gasto c
        LEFT JOIN gastos g ON g.categoria_id=c.id AND strftime('%Y-%m',g.fecha)=? AND g.nido_id=?
        WHERE c.activa=1 AND c.nido_id=?
        GROUP BY c.id HAVING total > 0 ORDER BY total DESC
    """, (mes_str, nid, nid)).fetchall()]

    evolucion = []
    for i in range(5, -1, -1):
        m = mes - i
        a = anio
        while m <= 0: m += 12; a -= 1
        ms = f"{a}-{m:02d}"
        gv = conn.execute(
            "SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE strftime('%Y-%m',fecha)=? AND nido_id=?",
            (ms, nid)
        ).fetchone()['t']
        inc = conn.execute(
            "SELECT COALESCE(SUM(importe),0) as t FROM ingresos WHERE strftime('%Y-%m',fecha)=? AND nido_id=?",
            (ms, nid)
        ).fetchone()['t']
        evolucion.append({'label': NOMBRES_MESES[m-1][:3], 'gastos': round(gv, 2), 'ingresos': round(inc, 2)})

    ultimos = [dict(r) for r in conn.execute("""
        SELECT g.id, g.descripcion, g.importe, g.fecha, g.es_fijo,
               u.nombre as unombre, u.color as ucolor,
               c.nombre as cnombre, c.color as ccolor, c.icono as cicono
        FROM gastos g
        LEFT JOIN usuarios u ON g.usuario_id=u.id
        LEFT JOIN categorias_gasto c ON g.categoria_id=c.id
        WHERE strftime('%Y-%m',g.fecha)=? AND g.nido_id=?
        ORDER BY g.fecha DESC, g.created_at DESC LIMIT 6
    """, (mes_str, nid)).fetchall()]

    # Evolución por categoría (últimos 6 meses)
    meses_labels = []
    meses_strs = []
    for i in range(5, -1, -1):
        m2 = mes - i; a2 = anio
        while m2 <= 0: m2 += 12; a2 -= 1
        meses_strs.append(f"{a2}-{m2:02d}")
        meses_labels.append(NOMBRES_MESES[m2-1][:3])

    inicio_6m = meses_strs[0]
    raw_cat_evol = conn.execute("""
        SELECT c.nombre, c.color,
               strftime('%Y-%m', g.fecha) as mes,
               COALESCE(SUM(g.importe), 0) as total
        FROM gastos g
        JOIN categorias_gasto c ON g.categoria_id = c.id
        WHERE g.nido_id = ? AND strftime('%Y-%m', g.fecha) >= ?
        GROUP BY c.nombre, c.color, mes
        ORDER BY mes, total DESC
    """, (nid, inicio_6m)).fetchall()

    cat_totals = defaultdict(lambda: {'color': '#6c757d', 'meses': {m: 0 for m in meses_strs}})
    for r in raw_cat_evol:
        cat_totals[r['nombre']]['color'] = r['color']
        cat_totals[r['nombre']]['meses'][r['mes']] = round(float(r['total']), 2)

    top_cats = sorted(cat_totals.items(), key=lambda x: sum(x[1]['meses'].values()), reverse=True)[:4]
    series_cat = [
        {'nombre': nombre, 'color': info['color'],
         'valores': [info['meses'].get(m, 0) for m in meses_strs]}
        for nombre, info in top_cats
    ]

    # Mes anterior para comparativa
    mes_ant = mes - 1 if mes > 1 else 12
    anio_ant = anio if mes > 1 else anio - 1
    mes_ant_str = f"{anio_ant}-{mes_ant:02d}"
    gastos_mes_ant = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE strftime('%Y-%m',fecha)=? AND nido_id=?",
        (mes_ant_str, nid)
    ).fetchone()['t']
    ingresos_mes_ant = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM ingresos WHERE strftime('%Y-%m',fecha)=? AND nido_id=?",
        (mes_ant_str, nid)
    ).fetchone()['t']

    def _variacion(actual, anterior):
        if anterior == 0:
            return None
        return round((actual - anterior) / anterior * 100, 1)

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
        'evolucion_categorias': {'meses': meses_labels, 'series': series_cat},
        'ultimos_gastos': ultimos,
        'comparativa': {
            'gastos_mes_anterior': gastos_mes_ant,
            'ingresos_mes_anterior': ingresos_mes_ant,
            'variacion_gastos': _variacion(total_gastos, gastos_mes_ant),
            'variacion_ingresos': _variacion(total_ingresos, ingresos_mes_ant),
        },
    })


@bp.route('/api/resumen')
def api_resumen():
    anio = request.args.get('anio', date.today().year, type=int)
    nid = _nido()
    conn = get_db()
    meses = []
    for m in range(1, 13):
        ms = f"{anio}-{m:02d}"
        gv = conn.execute(
            "SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE strftime('%Y-%m',fecha)=? AND nido_id=?",
            (ms, nid)
        ).fetchone()['t']
        inc = conn.execute(
            "SELECT COALESCE(SUM(importe),0) as t FROM ingresos WHERE strftime('%Y-%m',fecha)=? AND nido_id=?",
            (ms, nid)
        ).fetchone()['t']
        meses.append({'mes': NOMBRES_MESES[m-1], 'mes_num': m, 'gastos': gv, 'ingresos': inc, 'balance': inc - gv})
    conn.close()
    return jsonify({'anio': anio, 'meses': meses})


@bp.route('/api/copiar_mes', methods=['POST'])
def api_copiar_mes():
    """Copia gastos fijos e ingresos nómina de un mes al siguiente."""
    d = request.get_json()
    m_orig = int(d.get('mes_origen', 0))
    y_orig = int(d.get('anio_origen', 0))
    m_dest = int(d.get('mes_destino', 0))
    y_dest = int(d.get('anio_destino', 0))
    if not all([m_orig, y_orig, m_dest, y_dest]):
        return jsonify({'error': 'faltan parámetros'}), 400

    orig_str = f'{y_orig}-{m_orig:02d}'
    dest_str = f'{y_dest}-{m_dest:02d}'
    fecha_dest = f'{y_dest}-{m_dest:02d}-01'

    conn = get_db()

    # Gastos fijos del mes origen → mes destino (sin duplicados)
    gastos = conn.execute(
        "SELECT * FROM gastos WHERE es_fijo=1 AND substr(fecha,1,7)=?", (orig_str,)
    ).fetchall()
    n_gastos = 0
    for g in gastos:
        existe = conn.execute(
            """SELECT id FROM gastos WHERE es_fijo=1 AND descripcion=?
               AND importe=? AND substr(fecha,1,7)=?""",
            (g['descripcion'], g['importe'], dest_str)
        ).fetchone()
        if not existe:
            conn.execute(
                """INSERT INTO gastos (usuario_id,categoria_id,descripcion,importe,
                   fecha,es_fijo,notas,meta_id) VALUES(?,?,?,?,?,1,?,?)""",
                (g['usuario_id'], g['categoria_id'], g['descripcion'],
                 g['importe'], fecha_dest, g['notas'], g['meta_id'])
            )
            n_gastos += 1

    # Ingresos nómina del mes origen → mes destino (sin duplicados)
    ingresos = conn.execute(
        "SELECT * FROM ingresos WHERE es_nomina=1 AND substr(fecha,1,7)=?", (orig_str,)
    ).fetchall()
    n_ingresos = 0
    for i in ingresos:
        existe = conn.execute(
            """SELECT id FROM ingresos WHERE es_nomina=1 AND descripcion=?
               AND importe=? AND substr(fecha,1,7)=?""",
            (i['descripcion'], i['importe'], dest_str)
        ).fetchone()
        if not existe:
            conn.execute(
                """INSERT INTO ingresos (usuario_id,descripcion,importe,
                   fecha,es_nomina,notas) VALUES(?,?,?,?,1,?)""",
                (i['usuario_id'], i['descripcion'], i['importe'], fecha_dest, i['notas'])
            )
            n_ingresos += 1

    # Presupuestos del mes origen → mes destino (sin duplicados)
    presupuestos = conn.execute(
        "SELECT * FROM presupuestos WHERE mes=? AND anio=?", (m_orig, y_orig)
    ).fetchall()
    n_presupuestos = 0
    for p in presupuestos:
        existe = conn.execute(
            "SELECT id FROM presupuestos WHERE categoria_id=? AND mes=? AND anio=?",
            (p['categoria_id'], m_dest, y_dest)
        ).fetchone()
        if not existe:
            conn.execute(
                "INSERT INTO presupuestos (categoria_id, importe_mensual, mes, anio) VALUES(?,?,?,?)",
                (p['categoria_id'], p['importe_mensual'], m_dest, y_dest)
            )
            n_presupuestos += 1

    conn.commit()
    conn.close()
    threading.Thread(target=_guardar_backup, daemon=True).start()
    return jsonify({'ok': True, 'gastos_copiados': n_gastos, 'ingresos_copiados': n_ingresos,
                    'presupuestos_copiados': n_presupuestos})
