import os
import io
import base64
import json as _json
from datetime import date

from flask import Blueprint, request, jsonify
from auth_routes import requiere_auth
from .helpers import _nido, get_db, NOMBRES_MESES

bp = Blueprint('ia', __name__)

try:
    import anthropic as _anthropic
    _ANTHROPIC_CLIENT = _anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))
except Exception:
    _ANTHROPIC_CLIENT = None


@bp.route('/api/ia/analisis')
def api_ia_analisis():
    mes = request.args.get('mes', date.today().month, type=int)
    anio = request.args.get('anio', date.today().year, type=int)
    mes_str = f"{anio}-{mes:02d}"

    if not _ANTHROPIC_CLIENT or not os.environ.get('ANTHROPIC_API_KEY'):
        return jsonify({'error': 'IA no configurada'}), 503

    conn = get_db()

    total_gastos = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE strftime('%Y-%m',fecha)=?", (mes_str,)
    ).fetchone()['t']
    total_ingresos = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM ingresos WHERE strftime('%Y-%m',fecha)=?", (mes_str,)
    ).fetchone()['t']
    gastos_fijos = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE es_fijo=1 AND strftime('%Y-%m',fecha)=?", (mes_str,)
    ).fetchone()['t']

    cats = conn.execute("""
        SELECT c.nombre, COALESCE(SUM(g.importe),0) as total
        FROM categorias_gasto c
        LEFT JOIN gastos g ON g.categoria_id=c.id AND strftime('%Y-%m',g.fecha)=?
        WHERE c.activa=1 GROUP BY c.id HAVING total>0 ORDER BY total DESC LIMIT 8
    """, (mes_str,)).fetchall()

    gastos_lista = conn.execute("""
        SELECT g.descripcion, g.importe, g.fecha, g.es_fijo, c.nombre as cat
        FROM gastos g LEFT JOIN categorias_gasto c ON g.categoria_id=c.id
        WHERE strftime('%Y-%m',g.fecha)=?
        ORDER BY g.importe DESC LIMIT 20
    """, (mes_str,)).fetchall()

    presupuestos = conn.execute("""
        SELECT c.nombre, p.importe_mensual,
               COALESCE((SELECT SUM(g.importe) FROM gastos g WHERE g.categoria_id=c.id AND strftime('%Y-%m',g.fecha)=?),0) as gastado
        FROM presupuestos p JOIN categorias_gasto c ON p.categoria_id=c.id
        WHERE p.mes=? AND p.anio=?
    """, (mes_str, mes, anio)).fetchall()

    # Evolución últimos 3 meses
    evolucion = []
    for i in range(2, -1, -1):
        m = mes - i; a = anio
        while m <= 0: m += 12; a -= 1
        ms2 = f"{a}-{m:02d}"
        g2 = conn.execute("SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE strftime('%Y-%m',fecha)=?", (ms2,)).fetchone()['t']
        inc2 = conn.execute("SELECT COALESCE(SUM(importe),0) as t FROM ingresos WHERE strftime('%Y-%m',fecha)=?", (ms2,)).fetchone()['t']
        evolucion.append(f"{NOMBRES_MESES[m-1]}: gastos {g2:.2f}€, ingresos {inc2:.2f}€")

    conn.close()

    nombre_mes = NOMBRES_MESES[mes - 1]
    cats_txt = '\n'.join(f"  - {c['nombre']}: {c['total']:.2f}€" for c in cats)
    gastos_txt = '\n'.join(
        f"  - {g['descripcion']} ({g['cat'] or '?'}): {g['importe']:.2f}€{'  [fijo]' if g['es_fijo'] else ''}"
        for g in gastos_lista
    )
    presu_txt = '\n'.join(
        f"  - {p['nombre']}: presupuesto {p['importe_mensual']:.2f}€, gastado {p['gastado']:.2f}€"
        for p in presupuestos
    ) if presupuestos else '  (sin presupuestos definidos)'
    evol_txt = ' | '.join(evolucion)
    balance = total_ingresos - total_gastos

    prompt = f"""Eres Vera, la asistente financiera de Nido by Kreatia. Analizas las finanzas de una pareja de manera directa, honesta y cercana — sin rodeos pero con cariño.

DATOS DE {nombre_mes.upper()} {anio}:
- Ingresos totales: {total_ingresos:.2f}€
- Gastos totales: {total_gastos:.2f}€
- Balance: {balance:+.2f}€
- Gastos fijos: {gastos_fijos:.2f}€
- Gastos variables: {total_gastos - gastos_fijos:.2f}€

POR CATEGORÍA:
{cats_txt if cats_txt else '  (sin datos)'}

PRESUPUESTOS:
{presu_txt}

GASTOS PRINCIPALES:
{gastos_txt if gastos_txt else '  (sin gastos registrados)'}

EVOLUCIÓN ÚLTIMOS 3 MESES:
{evol_txt}

Responde en español con 4 secciones usando exactamente estos emojis como cabeceras (sin texto antes del emoji):
📊 **Resumen del mes** — 2-3 frases sobre la situación general
🔍 **Lo que llama la atención** — anomalías, gastos inusuales o patrones preocupantes
📅 **Previsión de fin de mes** — si el mes no ha terminado, proyecta; si ya terminó, valora
💡 **Consejo de Vera** — UN consejo concreto y accionable

Sé directa, usa números reales, y mantén un tono cercano pero profesional. Máximo 200 palabras en total."""

    try:
        msg = _ANTHROPIC_CLIENT.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=600,
            messages=[{'role': 'user', 'content': prompt}]
        )
        texto = msg.content[0].text
    except Exception as e:
        return jsonify({'error': f'Error al contactar la IA: {str(e)}'}), 500

    return jsonify({
        'analisis': texto,
        'mes': mes, 'anio': anio, 'nombre_mes': nombre_mes,
        'resumen': {
            'ingresos': total_ingresos,
            'gastos': total_gastos,
            'balance': balance,
        }
    })


@bp.route('/api/ia/chat', methods=['POST'])
def api_ia_chat():
    d = request.get_json()
    mensaje = d.get('mensaje', '').strip()
    uid = d.get('usuario_id')
    mes = int(d.get('mes', date.today().month))
    anio = int(d.get('anio', date.today().year))
    historial = d.get('historial', [])  # [{rol, texto}]

    if not _ANTHROPIC_CLIENT or not os.environ.get('ANTHROPIC_API_KEY'):
        return jsonify({'error': 'IA no configurada'}), 503

    mes_str = f"{anio}-{mes:02d}"
    conn = get_db()

    nido_id = _nido()
    cats = [dict(r) for r in conn.execute(
        "SELECT id, nombre FROM categorias_gasto WHERE activa=1 AND nido_id=? ORDER BY nombre",
        (nido_id,)).fetchall()]
    total_g = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM gastos WHERE nido_id=? AND strftime('%Y-%m',fecha)=?",
        (nido_id, mes_str,)
    ).fetchone()['t']
    total_i = conn.execute(
        "SELECT COALESCE(SUM(importe),0) as t FROM ingresos WHERE nido_id=? AND strftime('%Y-%m',fecha)=?",
        (nido_id, mes_str,)
    ).fetchone()['t']
    u_row = conn.execute("SELECT nombre FROM usuarios WHERE id=?", (uid,)).fetchone()
    u_nombre = u_row['nombre'] if u_row else 'usuario'

    # Detalle de gastos del mes (máx 60, ordenados por fecha desc, con id)
    gastos_det = conn.execute("""
        SELECT g.id, g.descripcion, g.importe, g.fecha, g.es_fijo,
               COALESCE(c.nombre,'Sin categoría') as cat
        FROM gastos g LEFT JOIN categorias_gasto c ON g.categoria_id=c.id
        WHERE g.nido_id=? AND strftime('%Y-%m',g.fecha)=?
        ORDER BY g.fecha DESC, g.created_at DESC LIMIT 60
    """, (nido_id, mes_str)).fetchall()

    # Resumen por categoría
    cats_resumen = conn.execute("""
        SELECT COALESCE(c.nombre,'Sin categoría') as cat,
               COALESCE(SUM(g.importe),0) as total, COUNT(*) as n
        FROM gastos g LEFT JOIN categorias_gasto c ON g.categoria_id=c.id
        WHERE g.nido_id=? AND strftime('%Y-%m',g.fecha)=?
        GROUP BY g.categoria_id ORDER BY total DESC
    """, (nido_id, mes_str)).fetchall()

    # Ingresos del mes (con id)
    ingresos_det = conn.execute("""
        SELECT id, descripcion, importe, es_nomina
        FROM ingresos WHERE nido_id=? AND strftime('%Y-%m',fecha)=?
        ORDER BY fecha DESC
    """, (nido_id, mes_str)).fetchall()

    conn.close()

    if cats:
        cats_txt = '\n'.join(f"  id={c['id']}: {c['nombre']}" for c in cats)
        cats_instruccion = (
            "CATEGORIZACIÓN AUTOMÁTICA: elige SIEMPRE la categoria_id más apropiada basándote "
            "en la descripción — nunca preguntes la categoría. Ejemplos: supermercado/comida→Alimentación, "
            "gasolina/coche/aparcamiento→Coche o Transporte, Netflix/Spotify/HBO→Suscripciones, "
            "médico/farmacia→Salud, ropa/zapatos→Ropa, hotel/vuelo/viaje→Viajes, "
            "alquiler/luz/agua/internet→Casa o Servicios, bar/restaurante→Ocio."
        )
    else:
        cats_txt = "  (sin categorías aún — usa categoria_id: null)"
        cats_instruccion = "No hay categorías disponibles, usa categoria_id: null."

    gastos_txt = '\n'.join(
        f"  id={g['id']} | {g['descripcion']} ({g['cat']}): {g['importe']:.2f}€"
        f"{'  [fijo]' if g['es_fijo'] else ''} [{g['fecha']}]"
        for g in gastos_det
    ) if gastos_det else '  (sin gastos este mes)'

    cats_resumen_txt = '\n'.join(
        f"  {r['cat']}: {r['total']:.2f}€ ({r['n']} transacciones)"
        for r in cats_resumen
    ) if cats_resumen else '  (sin datos)'

    ingresos_txt = '\n'.join(
        f"  id={i['id']} | {i['descripcion']}: {i['importe']:.2f}€"
        f"{'  [nómina]' if i['es_nomina'] else ''}"
        for i in ingresos_det
    ) if ingresos_det else '  (sin ingresos este mes)'

    system_prompt = f"""Eres Vera, la asistente financiera de Nido by Kreatia. Hablas con {u_nombre}.
Eres directa, cercana, sin rodeos pero con cariño. Respuestas cortas y útiles.

RESUMEN {NOMBRES_MESES[mes-1].upper()} {anio}:
- Gastos: {total_g:.2f}€  |  Ingresos: {total_i:.2f}€  |  Balance: {total_i-total_g:+.2f}€

GASTOS POR CATEGORÍA:
{cats_resumen_txt}

GASTOS DEL MES (más recientes primero — cada uno tiene su id):
{gastos_txt}

INGRESOS DEL MES:
{ingresos_txt}

CATEGORÍAS DISPONIBLES (usa el id exacto al registrar):
{cats_txt}

REGLA CRÍTICA: Responde SIEMPRE con JSON válido y NADA más. Estructura:
{{"respuesta": "texto para el usuario", "accion": null}}

Si el usuario quiere registrar un GASTO (compra, pago, factura…):
{{"respuesta": "texto breve confirmando", "accion": {{"tipo": "gasto", "descripcion": "descripción corta", "importe": 30.0, "categoria_id": 5, "es_fijo": false}}}}

Si el usuario quiere registrar un INGRESO (le han pagado, cobrado, recibido…):
{{"respuesta": "texto breve confirmando", "accion": {{"tipo": "ingreso", "descripcion": "descripción corta", "importe": 20.0, "es_nomina": false}}}}

Si el usuario quiere ELIMINAR un gasto concreto (usa el id de la lista):
{{"respuesta": "¿Confirmas que quieres eliminar [descripción] de [importe]€?", "accion": {{"tipo": "eliminar_gasto", "id": 123, "descripcion": "descripción", "importe": 30.0}}}}

Si el usuario quiere ELIMINAR un ingreso concreto:
{{"respuesta": "¿Confirmas que quieres eliminar [descripción] de [importe]€?", "accion": {{"tipo": "eliminar_ingreso", "id": 456, "descripcion": "descripción", "importe": 20.0}}}}

{cats_instruccion} Importe siempre número decimal sin €.
Si el usuario no menciona importe al registrar, pregúntale. No inventes importes.
Si el usuario menciona eliminar pero no es claro cuál, pregunta cuál exactamente."""

    messages = [{'role': h['rol'], 'content': h['texto']} for h in historial[-12:]]
    # La API de Anthropic requiere que el primer mensaje sea del rol 'user'
    while messages and messages[0]['role'] != 'user':
        messages.pop(0)
    messages.append({'role': 'user', 'content': mensaje})

    try:
        msg = _ANTHROPIC_CLIENT.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=700,
            system=system_prompt,
            messages=messages,
        )
        raw = msg.content[0].text.strip()
        start = raw.find('{'); end = raw.rfind('}') + 1
        data = _json.loads(raw[start:end]) if start >= 0 and end > start else {'respuesta': raw, 'accion': None}
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify(data)


@bp.route('/api/ia/analizar_documento', methods=['POST'])
def api_ia_analizar_documento():
    """Analiza un extracto bancario (imagen o PDF) y devuelve transacciones extraídas."""
    if not _ANTHROPIC_CLIENT or not os.environ.get('ANTHROPIC_API_KEY'):
        return jsonify({'error': 'IA no configurada'}), 503

    archivo = request.files.get('archivo')
    if not archivo:
        return jsonify({'error': 'No se recibió ningún archivo'}), 400

    nido_id = _nido()
    conn = get_db()
    cats = [dict(r) for r in conn.execute(
        "SELECT id, nombre FROM categorias_gasto WHERE activa=1 AND nido_id=? ORDER BY nombre",
        (nido_id,)).fetchall()]
    conn.close()

    cats_txt = '\n'.join(f"  id={c['id']}: {c['nombre']}" for c in cats)
    nombre_archivo = (archivo.filename or '').lower()
    contenido_bytes = archivo.read()

    instrucciones = f"""Eres un extractor de transacciones bancarias experto.
Analiza el extracto bancario y extrae TODAS las transacciones visibles.

CATEGORÍAS DISPONIBLES (usa el id exacto):
{cats_txt}

Devuelve SOLO JSON válido con esta estructura:
{{"transacciones": [
  {{
    "tipo": "gasto",
    "descripcion": "descripción limpia",
    "importe": 45.30,
    "fecha": "2024-06-15",
    "categoria_id": 1,
    "categoria_nombre": "Alimentación",
    "es_fijo": false
  }}
]}}

REGLAS:
- tipo: "gasto" si es cargo/débito/pago; "ingreso" si es abono/crédito/transferencia recibida/nómina
- Descripción limpia: sin códigos bancarios raros, solo texto legible y descriptivo
- importe: número decimal positivo sin símbolo de moneda
- fecha: YYYY-MM-DD (si no hay año, usa {date.today().year})
- categoria_id: id de la más apropiada de la lista; en caso de duda usa la de "Otros"
- es_fijo: true para gastos recurrentes fijos (alquiler, seguros, suscripciones)
- Para ingresos añade el campo "es_nomina": true si parece nómina o salario
CATEGORIZACIÓN: supermercado/comida→Alimentación, gasolina/aparcamiento→Coche, Netflix/Spotify/HBO→Suscripciones, médico/farmacia→Salud, ropa/zapatos→Ropa, hotel/vuelo→Viajes, alquiler/luz/agua/internet→Servicios, bar/restaurante/ocio→Ocio.
Devuelve SOLO el JSON, sin texto adicional antes ni después."""

    try:
        if nombre_archivo.endswith('.pdf'):
            try:
                import pdfplumber
            except ImportError:
                return jsonify({'error': 'Soporte PDF no disponible en el servidor'}), 500

            texto_paginas = []
            with pdfplumber.open(io.BytesIO(contenido_bytes)) as pdf:
                for page in pdf.pages:
                    texto = page.extract_text()
                    if texto:
                        texto_paginas.append(texto)
            contenido_texto = '\n'.join(texto_paginas)

            if not contenido_texto.strip():
                return jsonify({'error': 'No se pudo extraer texto del PDF. Prueba a subir una imagen del extracto.'}), 422

            msg = _ANTHROPIC_CLIENT.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=4096,
                messages=[{'role': 'user', 'content': f"{instrucciones}\n\nEXTRACTO BANCARIO:\n{contenido_texto}"}]
            )
        else:
            mime_type = archivo.mimetype or 'image/jpeg'
            if not mime_type.startswith('image/'):
                mime_type = 'image/jpeg'
            imagen_b64 = base64.b64encode(contenido_bytes).decode('utf-8')

            msg = _ANTHROPIC_CLIENT.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=4096,
                messages=[{
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image',
                            'source': {'type': 'base64', 'media_type': mime_type, 'data': imagen_b64}
                        },
                        {'type': 'text', 'text': instrucciones}
                    ]
                }]
            )

        raw = msg.content[0].text.strip()
        start = raw.find('{'); end = raw.rfind('}') + 1
        data = _json.loads(raw[start:end]) if start >= 0 and end > start else {'transacciones': []}

        transacciones = data.get('transacciones', [])
        total_gastos = sum(t.get('importe', 0) for t in transacciones if t.get('tipo') == 'gasto')
        total_ingresos = sum(t.get('importe', 0) for t in transacciones if t.get('tipo') == 'ingreso')

        return jsonify({
            'transacciones': transacciones,
            'categorias': cats,
            'total': len(transacciones),
            'total_gastos': round(total_gastos, 2),
            'total_ingresos': round(total_ingresos, 2),
        })

    except Exception as e:
        return jsonify({'error': f'Error al analizar el documento: {str(e)}'}), 500


@bp.route('/api/ia/registrar_batch', methods=['POST'])
def api_ia_registrar_batch():
    """Registra en batch las transacciones confirmadas por el usuario."""
    d = request.get_json()
    transacciones = d.get('transacciones', [])
    uid = d.get('usuario_id')

    if not transacciones:
        return jsonify({'error': 'No hay transacciones para registrar'}), 400

    nido_id = _nido()
    conn = get_db()
    registradas = 0
    errores = []

    for t in transacciones:
        try:
            tipo = t.get('tipo')
            if tipo == 'gasto':
                conn.execute(
                    """INSERT INTO gastos
                       (usuario_id, categoria_id, descripcion, importe, fecha, es_fijo, notas, nido_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (uid, t.get('categoria_id'), t.get('descripcion'), t.get('importe'),
                     t.get('fecha'), 1 if t.get('es_fijo') else 0, '', nido_id)
                )
            elif tipo == 'ingreso':
                conn.execute(
                    """INSERT INTO ingresos
                       (usuario_id, descripcion, importe, fecha, es_nomina, categoria, notas, nido_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (uid, t.get('descripcion'), t.get('importe'),
                     t.get('fecha'), 1 if t.get('es_nomina') else 0,
                     t.get('categoria_nombre', 'Otros'), '', nido_id)
                )
            registradas += 1
        except Exception as e:
            errores.append({'descripcion': t.get('descripcion', '?'), 'error': str(e)})

    conn.commit()
    conn.close()

    return jsonify({'registradas': registradas, 'errores': errores})
