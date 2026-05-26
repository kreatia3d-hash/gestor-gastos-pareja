import os
from datetime import date

from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash, jsonify)
from .helpers import (
    _nido, get_db, auto_generar_mes, fecha_desde_form,
    NOMBRES_MESES, CATEGORIA_ICONOS,
)

bp = Blueprint('paginas', __name__)

# Tunnel globals are read from app config so blueprints don't need to import them
_TUNNEL_URL    = None   # fallback only; real value comes from current_app.config
_TUNNEL_STATUS = 'off'
_LOCAL_IP      = '127.0.0.1'


def _tunnel_url():
    from flask import current_app
    return current_app.config.get('TUNNEL_URL', None)


def _tunnel_status():
    from flask import current_app
    return current_app.config.get('TUNNEL_STATUS', 'off')


def _local_ip():
    from flask import current_app
    return current_app.config.get('LOCAL_IP', '127.0.0.1')


# ── Context processor (inyecta CATEGORIA_ICONOS en todos los templates) ───────

@bp.app_context_processor
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
        CATEGORIA_ICONOS=CATEGORIA_ICONOS,
        usuario_actual=usuario_actual,
        todos_usuarios=todos_usuarios,
        mes_actual=hoy.month,
        anio_actual=hoy.year,
        nombres_meses=NOMBRES_MESES,
        tunnel_url=_tunnel_url(),
        tunnel_status=_tunnel_status(),
        local_ip=_local_ip(),
    )


# ── API utilitarios públicos ──────────────────────────────────────────────────

@bp.route('/api/ping')
def api_ping():
    return jsonify({'app': 'gestorgastos', 'status': 'ok'})


@bp.route('/api/version')
def api_version():
    return jsonify({
        'version':      '2.8.1',
        'build':        29,
        'min_version':  '2.0.0',
        'min_build':    1,
        'force_update': False,
        'changelog':    'Seguridad: todos los endpoints protegidos con JWT, timeouts en llamadas API',
        'download_url': os.environ.get('DOWNLOAD_APK_URL', ''),
    })


@bp.route('/api/tunnel')
def api_tunnel():
    return jsonify({'url': _tunnel_url(), 'status': _tunnel_status()})


# ── Foto de perfil (web form) ─────────────────────────────────────────────────

@bp.route('/usuarios/<int:uid>/foto', methods=['POST'])
def usuario_foto(uid):
    if 'usuario_id' not in session or int(session['usuario_id']) != uid:
        flash('No autorizado.', 'danger')
        return redirect(url_for('paginas.configuracion'))
    foto = request.files.get('foto')
    if foto and foto.filename:
        ext = foto.filename.rsplit('.', 1)[-1].lower()
        if ext not in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
            ext = 'jpg'
        from flask import current_app
        fotos_dir = os.path.join(os.path.dirname(current_app.root_path), 'static', 'fotos')
        os.makedirs(fotos_dir, exist_ok=True)
        nombre = f'usuario_{uid}.{ext}'
        foto.save(os.path.join(fotos_dir, nombre))
        conn = get_db()
        conn.execute("UPDATE usuarios SET foto=? WHERE id=?", (f'fotos/{nombre}', uid))
        conn.commit()
        conn.close()
        flash('Foto de perfil actualizada.', 'success')
    return redirect(url_for('paginas.configuracion'))


# ── Notificaciones ────────────────────────────────────────────────────────────

@bp.route('/api/notificaciones')
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


@bp.route('/api/notificaciones/<int:nid>/leer', methods=['POST'])
def api_notificacion_leer(nid):
    conn = get_db()
    conn.execute("UPDATE notificaciones SET leida=1 WHERE id=?", (nid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@bp.route('/api/notificaciones/leer_todas', methods=['POST'])
def api_notificaciones_leer_todas():
    uid = request.get_json().get('usuario_id')
    if uid:
        conn = get_db()
        conn.execute("UPDATE notificaciones SET leida=1 WHERE usuario_id=?", (uid,))
        conn.commit()
        conn.close()
    return jsonify({'ok': True})


# ── INICIO / USUARIO ──────────────────────────────────────────────────────────

@bp.route('/')
def index():
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))
    return redirect(url_for('paginas.dashboard'))


@bp.route('/seleccionar')
def seleccionar_usuario():
    conn = get_db()
    usuarios = conn.execute("SELECT * FROM usuarios").fetchall()
    conn.close()
    return render_template('seleccionar_usuario.html', usuarios=usuarios)


@bp.route('/seleccionar/<int:uid>')
def set_usuario(uid):
    conn = get_db()
    u = conn.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
    conn.close()
    if u:
        if u['pin']:
            return redirect(url_for('paginas.pin_usuario', uid=uid))
        session['usuario_id'] = uid
    return redirect(url_for('paginas.dashboard'))


@bp.route('/pin/<int:uid>', methods=['GET', 'POST'])
def pin_usuario(uid):
    conn = get_db()
    u = conn.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
    conn.close()
    if not u or not u['pin']:
        session['usuario_id'] = uid
        return redirect(url_for('paginas.dashboard'))
    if request.method == 'POST':
        if request.form.get('pin', '') == u['pin']:
            session['usuario_id'] = uid
            return redirect(url_for('paginas.dashboard'))
        flash('PIN incorrecto.', 'danger')
    return render_template('pin_entrada.html', usuario=u)


@bp.route('/cambiar_usuario')
def cambiar_usuario():
    session.pop('usuario_id', None)
    return redirect(url_for('paginas.seleccionar_usuario'))


# ── DASHBOARD ─────────────────────────────────────────────────────────────────

@bp.route('/dashboard')
def dashboard():
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))

    hoy = date.today()
    mes = request.args.get('mes', hoy.month, type=int)
    anio = request.args.get('anio', hoy.year, type=int)
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

    balance = total_ingresos - total_gastos

    gastos_fijos_total = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE es_fijo=1 AND strftime('%Y-%m',fecha)=?",
        (mes_str,)
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
        g = conn.execute(
            "SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE strftime('%Y-%m',fecha)=?", (ms,)
        ).fetchone()['t']
        inc = conn.execute(
            "SELECT COALESCE(SUM(importe),0) as t FROM ingresos WHERE strftime('%Y-%m',fecha)=?", (ms,)
        ).fetchone()['t']
        evolucion.append({'label': NOMBRES_MESES[m-1][:3], 'gastos': round(g, 2), 'ingresos': round(inc, 2)})

    conn.close()

    return render_template('dashboard.html',
                           mes=mes, anio=anio, mes_str=mes_str,
                           nombre_mes=NOMBRES_MESES[mes-1],
                           total_gastos=total_gastos, total_ingresos=total_ingresos, balance=balance,
                           gastos_fijos_total=gastos_fijos_total,
                           gastos_variables_total=gastos_variables_total,
                           gastos_categoria=gastos_categoria,
                           gastos_usuario=gastos_usuario, ingresos_usuario=ingresos_usuario,
                           ultimos_gastos=ultimos_gastos, metas=metas, evolucion=evolucion)


# ── GASTOS ────────────────────────────────────────────────────────────────────

@bp.route('/gastos')
def gastos():
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))

    hoy = date.today()
    mes = request.args.get('mes', hoy.month, type=int)
    anio = request.args.get('anio', hoy.year, type=int)
    cat_filtro = request.args.get('cat', '')
    usr_filtro = request.args.get('usr', '')
    fijo_filtro = request.args.get('fijo', '')
    mes_str = f"{anio}-{mes:02d}"
    auto_generar_mes(mes_str, _nido())

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
                           cat_filtro=cat_filtro, usr_filtro=usr_filtro, fijo_filtro=fijo_filtro)


@bp.route('/gastos/nuevo', methods=['GET', 'POST'])
def gasto_nuevo():
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))
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
            cat = conn.execute(
                "SELECT es_ahorro FROM categorias_gasto WHERE id=?", (categoria_id,)
            ).fetchone()
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
        return redirect(url_for('paginas.gastos'))

    conn.close()
    return render_template('gasto_form.html', categorias=categorias, usuarios=usuarios,
                           metas_activas=metas_activas, gasto=None)


@bp.route('/gastos/<int:gid>/editar', methods=['GET', 'POST'])
def gasto_editar(gid):
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))
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
        return redirect(url_for('paginas.gastos'))

    conn.close()
    return render_template('gasto_form.html', categorias=categorias, usuarios=usuarios,
                           metas_activas=metas_activas, gasto=gasto)


@bp.route('/gastos/<int:gid>/eliminar', methods=['POST'])
def gasto_eliminar(gid):
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))
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
    return redirect(url_for('paginas.gastos'))


@bp.route('/gastos/<int:gid>/desvinc_meta', methods=['POST'])
def gasto_desvinc_meta(gid):
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))
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
    return redirect(url_for('paginas.gastos'))


# ── INGRESOS ──────────────────────────────────────────────────────────────────

@bp.route('/ingresos')
def ingresos():
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))

    hoy = date.today()
    mes = request.args.get('mes', hoy.month, type=int)
    anio = request.args.get('anio', hoy.year, type=int)
    usr_filtro = request.args.get('usr', '')
    mes_str = f"{anio}-{mes:02d}"
    auto_generar_mes(mes_str, _nido())

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
                           nombre_mes=NOMBRES_MESES[mes-1], usr_filtro=usr_filtro)


@bp.route('/ingresos/nuevo', methods=['GET', 'POST'])
def ingreso_nuevo():
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))
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
        return redirect(url_for('paginas.ingresos'))
    conn.close()
    return render_template('ingreso_form.html', usuarios=usuarios, ingreso=None)


@bp.route('/ingresos/<int:iid>/editar', methods=['GET', 'POST'])
def ingreso_editar(iid):
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))
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
        return redirect(url_for('paginas.ingresos'))
    conn.close()
    return render_template('ingreso_form.html', usuarios=usuarios, ingreso=ingreso)


@bp.route('/ingresos/<int:iid>/eliminar', methods=['POST'])
def ingreso_eliminar(iid):
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))
    conn = get_db()
    conn.execute("DELETE FROM ingresos WHERE id=?", (iid,))
    conn.commit()
    conn.close()
    flash('Ingreso eliminado.', 'warning')
    return redirect(url_for('paginas.ingresos'))


# ── METAS DE AHORRO ───────────────────────────────────────────────────────────

@bp.route('/metas')
def metas():
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))
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


@bp.route('/metas/nueva', methods=['GET', 'POST'])
def meta_nueva():
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))
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
        return redirect(url_for('paginas.metas'))
    return render_template('meta_form.html', meta=None)


@bp.route('/metas/<int:mid>/editar', methods=['GET', 'POST'])
def meta_editar(mid):
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))
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
        return redirect(url_for('paginas.metas'))
    conn.close()
    return render_template('meta_form.html', meta=meta)


@bp.route('/metas/<int:mid>/abonar', methods=['GET', 'POST'])
def meta_abonar(mid):
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))
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
        return redirect(url_for('paginas.metas'))
    conn.close()
    return render_template('meta_abonar.html', meta=meta, usuarios=usuarios, aportaciones=aportaciones)


@bp.route('/metas/<int:mid>/eliminar', methods=['POST'])
def meta_eliminar(mid):
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))
    conn = get_db()
    conn.execute("DELETE FROM aportaciones_meta WHERE meta_id=?", (mid,))
    conn.execute("UPDATE gastos SET meta_id=NULL WHERE meta_id=?", (mid,))
    conn.execute("DELETE FROM metas_ahorro WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    flash('Meta eliminada.', 'warning')
    return redirect(url_for('paginas.metas'))


# ── RESUMEN ANUAL ─────────────────────────────────────────────────────────────

@bp.route('/resumen')
def resumen():
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))

    anio = request.args.get('anio', date.today().year, type=int)
    conn = get_db()
    meses_data = []
    for m in range(1, 13):
        ms = f"{anio}-{m:02d}"
        g = conn.execute(
            "SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE strftime('%Y-%m',fecha)=?", (ms,)
        ).fetchone()['t']
        inc = conn.execute(
            "SELECT COALESCE(SUM(importe),0) as t FROM ingresos WHERE strftime('%Y-%m',fecha)=?", (ms,)
        ).fetchone()['t']
        meses_data.append({
            'mes': NOMBRES_MESES[m-1], 'mes_num': m,
            'gastos': g, 'ingresos': inc, 'balance': inc - g
        })

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
                           balance_anio=total_i - total_g, gastos_cat=gastos_cat)


# ── CONFIGURACION ─────────────────────────────────────────────────────────────

@bp.route('/configuracion', methods=['GET', 'POST'])
def configuracion():
    if 'usuario_id' not in session:
        return redirect(url_for('paginas.seleccionar_usuario'))

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
            cat = conn.execute(
                "SELECT es_ahorro FROM categorias_gasto WHERE id=?", (cid,)
            ).fetchone()
            if cat and cat['es_ahorro']:
                flash('No se puede eliminar la categoria de Ahorro.', 'danger')
            else:
                cnt = conn.execute(
                    "SELECT COUNT(*) as c FROM gastos WHERE categoria_id=?", (cid,)
                ).fetchone()['c']
                if cnt > 0:
                    flash(f'No se puede eliminar: hay {cnt} gasto(s) con esta categoria.', 'danger')
                else:
                    conn.execute("DELETE FROM categorias_gasto WHERE id=?", (cid,))
                    conn.commit()
                    flash('Categoria eliminada.', 'warning')

        conn.close()
        return redirect(url_for('paginas.configuracion'))

    usuarios = conn.execute("SELECT * FROM usuarios").fetchall()
    categorias = conn.execute("SELECT * FROM categorias_gasto ORDER BY nombre").fetchall()
    conn.close()
    return render_template('configuracion.html', usuarios=usuarios, categorias=categorias)


# ── Landing y páginas legales ─────────────────────────────────────────────────

@bp.route('/landing')
def landing():
    return render_template('landing.html')


@bp.route('/politica-privacidad')
def politica_privacidad():
    return render_template('politica_privacidad.html')


@bp.route('/terminos')
def terminos():
    return render_template('terminos.html')
