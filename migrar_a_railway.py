"""
Migración de datos locales (gastos.db) → Railway API
Detecta y evita duplicados por (fecha, importe, usuario_id, descripcion).
"""
import sqlite3
import requests
import json
import time

BASE_URL = 'https://web-production-2694.up.railway.app'
LOCAL_DB  = r'c:\Users\Leo\Desktop\claude\gestor_gastos_pareja\gastos.db'

def api_get(path, params=None):
    for _ in range(5):
        try:
            r = requests.get(f'{BASE_URL}{path}', params=params,
                             headers={'Accept': 'application/json'}, timeout=15)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(3)
    return []

def api_post(path, body):
    for _ in range(5):
        try:
            r = requests.post(f'{BASE_URL}{path}', json=body,
                              headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
                              timeout=15)
            if r.status_code in (200, 201):
                return r.json()
        except Exception:
            pass
        time.sleep(3)
    return {'error': 'fallo'}

def api_put(path, body):
    for _ in range(5):
        try:
            r = requests.put(f'{BASE_URL}{path}', json=body,
                             headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
                             timeout=15)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(3)
    return {'error': 'fallo'}

def clave_gasto(g):
    return (g.get('fecha','')[:10], round(float(g.get('importe', 0)), 2),
            g.get('usuario_id'), g.get('descripcion','').strip().lower())

def clave_ingreso(i):
    return (i.get('fecha','')[:10], round(float(i.get('importe', 0)), 2),
            i.get('usuario_id'), i.get('descripcion','').strip().lower())

def main():
    conn = sqlite3.connect(LOCAL_DB)
    conn.row_factory = sqlite3.Row

    print('=== Migracion completa de datos locales a Railway ===\n')

    # 1. Actualizar usuarios
    print('1. Actualizando usuarios...')
    for u in conn.execute('SELECT * FROM usuarios').fetchall():
        res = api_put(f'/api/usuarios/{u["id"]}', {
            'nombre': u['nombre'], 'color': u['color'], 'emoji': u['emoji'] or 'person',
        })
        if u['pin']:
            api_put(f'/api/usuarios/{u["id"]}/pin', {'pin': u['pin']})
        print(f'  {u["id"]}: {u["nombre"]} -> {res.get("nombre", res)}')

    # 2. Sincronizar categorias
    print('\n2. Sincronizando categorias...')
    cats_railway = {c['nombre'].lower(): c for c in api_get('/api/categorias')}
    cats_locales  = conn.execute('SELECT * FROM categorias_gasto').fetchall()
    cat_map = {}
    for c in cats_locales:
        nombre_low = c['nombre'].lower()
        if nombre_low in cats_railway:
            cat_map[c['id']] = cats_railway[nombre_low]['id']
            print(f'  "{c["nombre"]}" -> id {cat_map[c["id"]]} (existente)')
        else:
            res = api_post('/api/categorias', {
                'nombre': c['nombre'], 'color': c['color'], 'icono': c['icono'] or 'bi-tag',
            })
            cat_map[c['id']] = res.get('id')
            print(f'  "{c["nombre"]}" -> id {cat_map[c["id"]]} (creada)')

    # 3. Obtener gastos ya existentes en Railway (todos los meses)
    print('\n3. Migrando gastos (con deduplicacion)...')
    meses_gastos = conn.execute(
        'SELECT DISTINCT substr(fecha,1,4) as y, substr(fecha,6,2) as m FROM gastos'
    ).fetchall()

    existentes_gastos = set()
    for row in meses_gastos:
        lote = api_get('/api/gastos', {'mes': row['m'], 'anio': row['y']})
        for g in lote:
            existentes_gastos.add(clave_gasto(g))
    print(f'  Gastos ya en Railway: {len(existentes_gastos)}')

    gastos_locales = conn.execute('SELECT * FROM gastos ORDER BY fecha, id').fetchall()
    ok = skip = err = 0
    for g in gastos_locales:
        k = (g['fecha'][:10], round(float(g['importe']), 2), g['usuario_id'],
             (g['descripcion'] or '').strip().lower())
        if k in existentes_gastos:
            skip += 1
            continue
        body = {
            'descripcion': g['descripcion'],
            'importe': g['importe'],
            'fecha': g['fecha'],
            'es_fijo': bool(g['es_fijo']),
            'usuario_id': g['usuario_id'],
            'categoria_id': cat_map.get(g['categoria_id']) if g['categoria_id'] else None,
            'notas': g['notas'] or '',
            'meta_id': None,
        }
        res = api_post('/api/gastos', body)
        if 'id' in res:
            ok += 1
        else:
            print(f'  ERROR "{g["descripcion"]}": {res}')
            err += 1
    print(f'  Insertados: {ok} | Saltados (ya existian): {skip} | Errores: {err}')

    # 4. Migrar ingresos (con deduplicacion)
    print('\n4. Migrando ingresos (con deduplicacion)...')
    meses_ingresos = conn.execute(
        'SELECT DISTINCT substr(fecha,1,4) as y, substr(fecha,6,2) as m FROM ingresos'
    ).fetchall()

    existentes_ingresos = set()
    for row in meses_ingresos:
        lote = api_get('/api/ingresos', {'mes': row['m'], 'anio': row['y']})
        for i in lote:
            existentes_ingresos.add(clave_ingreso(i))
    print(f'  Ingresos ya en Railway: {len(existentes_ingresos)}')

    ingresos_locales = conn.execute('SELECT * FROM ingresos ORDER BY fecha, id').fetchall()
    ok = skip = err = 0
    for i in ingresos_locales:
        k = (i['fecha'][:10], round(float(i['importe']), 2), i['usuario_id'],
             (i['descripcion'] or '').strip().lower())
        if k in existentes_ingresos:
            skip += 1
            continue
        body = {
            'descripcion': i['descripcion'],
            'importe': i['importe'],
            'fecha': i['fecha'],
            'es_nomina': bool(i['es_nomina']),
            'usuario_id': i['usuario_id'],
            'notas': i['notas'] or '',
        }
        res = api_post('/api/ingresos', body)
        if 'id' in res:
            ok += 1
        else:
            print(f'  ERROR "{i["descripcion"]}": {res}')
            err += 1
    print(f'  Insertados: {ok} | Saltados (ya existian): {skip} | Errores: {err}')

    # 5. Migrar metas
    print('\n5. Migrando metas...')
    metas_railway = {m['nombre'].lower(): m for m in api_get('/api/metas')}
    metas_locales = conn.execute('SELECT * FROM metas_ahorro').fetchall()
    meta_map = {}
    for m in metas_locales:
        if m['nombre'].lower() in metas_railway:
            mid = metas_railway[m['nombre'].lower()]['id']
            meta_map[m['id']] = mid
            print(f'  Meta "{m["nombre"]}" ya existe (id {mid})')
        else:
            body = {
                'nombre': m['nombre'],
                'descripcion': m['descripcion'] or '',
                'importe_objetivo': m['importe_objetivo'],
                'fecha_limite': m['fecha_limite'],
                'color': m['color'],
            }
            res = api_post('/api/metas', body)
            if 'id' in res:
                meta_map[m['id']] = res['id']
                if m['importe_actual'] and m['importe_actual'] > 0:
                    api_post(f'/api/metas/{res["id"]}/abonar', {
                        'importe': m['importe_actual'],
                        'usuario_id': 1,
                        'fecha': '2026-01-01',
                        'notas': 'Saldo restaurado desde migracion',
                    })
                print(f'  Meta "{m["nombre"]}" creada (objetivo: {m["importe_objetivo"]})')
            else:
                print(f'  ERROR meta "{m["nombre"]}": {res}')

    conn.close()

    # 6. Forzar backup
    print('\n6. Forzando backup en Railway...')
    backup = api_post('/api/backup', {})
    print(f'  Backup: {backup}')

    # 7. Verificacion final
    print('\n=== Verificacion final ===')
    usuarios_r = api_get('/api/usuarios')
    metas_r    = api_get('/api/metas')
    print(f'Usuarios: {[u["nombre"] for u in usuarios_r]}')
    print(f'Metas: {[m["nombre"] for m in metas_r]}')
    totales = {'gastos': 0, 'ingresos': 0}
    for row in meses_gastos:
        totales['gastos'] += len(api_get('/api/gastos', {'mes': row['m'], 'anio': row['y']}))
    for row in meses_ingresos:
        totales['ingresos'] += len(api_get('/api/ingresos', {'mes': row['m'], 'anio': row['y']}))
    print(f'Gastos Railway (todos los meses): {totales["gastos"]}')
    print(f'Ingresos Railway (todos los meses): {totales["ingresos"]}')
    print('\nMigracion completada.')

if __name__ == '__main__':
    main()
