"""
Funciones y constantes compartidas por todos los blueprints.
"""
import os
from datetime import date

from flask import request, jsonify
from auth_routes import get_nido_id_from_request, requiere_auth  # noqa: F401 (re-exported)
from db import get_db  # noqa: F401 (re-exported)

# ── Constantes ────────────────────────────────────────────────────────────────

NOMBRES_MESES = [
    'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
    'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
]

CATEGORIA_ICONOS = {
    'Comida':        'bi-cart',
    'Casa':          'bi-house',
    'Coche':         'bi-fuel-pump',
    'Ocio':          'bi-controller',
    'Salud':         'bi-heart-pulse',
    'Salario':       'bi-briefcase',
    'Extra':         'bi-gift',
    'Otros':         'bi-three-dots',
    'Ahorro':        'bi-piggy-bank',
    'Supermercado':  'bi-basket2',
    'Restaurante':   'bi-cup-hot',
    'Transporte':    'bi-train-front',
    'Ropa':          'bi-bag',
    'Viaje':         'bi-airplane',
    'Telefono':      'bi-phone',
    'Suscripciones': 'bi-tv',
    'Mascota':       'bi-paw',
    'Educacion':     'bi-book',
}

# ── Entorno ───────────────────────────────────────────────────────────────────

IS_CLOUD = os.environ.get('CLOUD_MODE', '') == '1'
# blueprints/ lives inside the project root, so go up one level
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_default_data_dir = '/data' if IS_CLOUD else _project_root
DATA_DIR = os.environ.get('DATA_DIR', _default_data_dir).strip()

# ── Helper: nido_id desde JWT ─────────────────────────────────────────────────

def _nido() -> int:
    return get_nido_id_from_request()


# ── Límites plan FREE ─────────────────────────────────────────────────────────

_LIMITES_FREE = {
    'gastos':       {'tipo': 'mes',       'limite': 50},
    'ingresos':     {'tipo': 'mes',       'limite': 10},
    'metas_ahorro': {'tipo': 'total',     'limite': 3},
    'presupuestos': {'tipo': 'mes_pres',  'limite': 5},
}


def _verificar_limite_free(conn, tabla: str, mes=None, anio=None):
    """Devuelve una respuesta 402 si el nido FREE supera el límite, o None si puede continuar."""
    return None  # Límites desactivados temporalmente
    nid = _nido()
    fila = conn.execute("SELECT plan FROM nidos WHERE id=?", (nid,)).fetchone()
    if fila and fila['plan'] == 'pro':
        return None
    cfg = _LIMITES_FREE.get(tabla)
    if not cfg:
        return None
    limite = cfg['limite']
    hoy = date.today()
    if cfg['tipo'] == 'mes':
        m = mes or hoy.month
        a = anio or hoy.year
        mes_str = f"{a}-{m:02d}"
        count = conn.execute(
            f"SELECT COUNT(*) as cnt FROM {tabla} WHERE nido_id=? AND strftime('%Y-%m', fecha)=?",
            (nid, mes_str)
        ).fetchone()['cnt']
    elif cfg['tipo'] == 'mes_pres':
        m = mes or hoy.month
        a = anio or hoy.year
        count = conn.execute(
            f"SELECT COUNT(*) as cnt FROM {tabla} WHERE nido_id=? AND mes=? AND anio=?",
            (nid, m, a)
        ).fetchone()['cnt']
    else:
        count = conn.execute(
            f"SELECT COUNT(*) as cnt FROM {tabla} WHERE nido_id=?", (nid,)
        ).fetchone()['cnt']
    if count >= limite:
        return jsonify({'error': 'limite_free', 'tabla': tabla, 'limite': limite}), 402
    return None


# ── Auto-generación de mes ────────────────────────────────────────────────────

def auto_generar_mes(mes_str, nido_id: int):
    """Genera gastos fijos e ingresos nómina para el mes indicado si no existen."""
    primera_fecha = mes_str + "-01"
    conn = get_db()
    c = conn.cursor()
    changed = False

    # ── Gastos fijos ─────────────────────────────────────────────────────────
    c.execute("""
        SELECT DISTINCT descripcion, COALESCE(categoria_id,-1) AS cat, COALESCE(usuario_id,-1) AS uid
        FROM gastos WHERE es_fijo=1 AND nido_id=?
    """, (nido_id,))
    grupos = c.fetchall()
    for g in grupos:
        row = c.execute("""
            SELECT categoria_id, usuario_id, importe, notas FROM gastos
            WHERE es_fijo=1 AND nido_id=?
              AND descripcion=?
              AND COALESCE(categoria_id,-1)=?
              AND COALESCE(usuario_id,-1)=?
            ORDER BY fecha DESC LIMIT 1
        """, (nido_id, g['descripcion'], g['cat'], g['uid'])).fetchone()
        if not row:
            continue
        existe = c.execute("""
            SELECT 1 FROM gastos
            WHERE es_fijo=1 AND nido_id=? AND descripcion=?
              AND COALESCE(categoria_id,-1)=?
              AND COALESCE(usuario_id,-1)=?
              AND strftime('%Y-%m', fecha)=?
        """, (nido_id, g['descripcion'], g['cat'], g['uid'], mes_str)).fetchone()
        if not existe:
            c.execute("""
                INSERT INTO gastos (usuario_id, categoria_id, descripcion, importe, fecha, es_fijo, notas, nido_id)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """, (row['usuario_id'], row['categoria_id'], g['descripcion'],
                  row['importe'], primera_fecha, row['notas'], nido_id))
            changed = True

    # ── Ingresos nómina ───────────────────────────────────────────────────────
    c.execute("""
        SELECT DISTINCT descripcion, usuario_id FROM ingresos WHERE es_nomina=1 AND nido_id=?
    """, (nido_id,))
    grupos_ing = c.fetchall()
    for n in grupos_ing:
        row = c.execute("""
            SELECT importe, notas FROM ingresos
            WHERE es_nomina=1 AND nido_id=? AND descripcion=? AND usuario_id=?
            ORDER BY fecha DESC LIMIT 1
        """, (nido_id, n['descripcion'], n['usuario_id'])).fetchone()
        if not row:
            continue
        existe = c.execute("""
            SELECT 1 FROM ingresos
            WHERE es_nomina=1 AND nido_id=? AND descripcion=? AND usuario_id=?
              AND strftime('%Y-%m', fecha)=?
        """, (nido_id, n['descripcion'], n['usuario_id'], mes_str)).fetchone()
        if not existe:
            c.execute("""
                INSERT INTO ingresos (usuario_id, descripcion, importe, fecha, es_nomina, notas, nido_id)
                VALUES (?, ?, ?, ?, 1, ?, ?)
            """, (n['usuario_id'], n['descripcion'], row['importe'], primera_fecha, row['notas'], nido_id))
            changed = True

    if changed:
        conn.commit()
    conn.close()


# ── Cloudinary upload ─────────────────────────────────────────────────────────

def _upload_cloudinary(file_bytes, public_id: str) -> str:
    """Sube bytes a Cloudinary y devuelve la URL segura. Requiere CLOUDINARY_URL en env."""
    import cloudinary
    import cloudinary.uploader
    import io
    cloudinary.config(cloudinary_url=os.environ.get('CLOUDINARY_URL', ''))
    result = cloudinary.uploader.upload(
        io.BytesIO(file_bytes),
        public_id=public_id,
        overwrite=True,
        resource_type='image',
        folder='nido/avatares',
        transformation=[{'width': 400, 'height': 400, 'crop': 'fill', 'gravity': 'face'}],
    )
    return result['secure_url']


# ── Fecha helper ──────────────────────────────────────────────────────────────

def fecha_desde_form(valor_form):
    """Convierte YYYY-MM del input[type=month] a YYYY-MM-01 para la BD."""
    if not valor_form:
        hoy = date.today()
        return f"{hoy.year}-{hoy.month:02d}-01"
    if len(valor_form) == 7:
        return valor_form + "-01"
    return valor_form
