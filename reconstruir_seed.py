"""Reconstruye initial_data.json desde la API de Railway."""
import requests, json, time

BASE = 'https://web-production-2694.up.railway.app'
OUT  = r'c:\Users\Leo\Desktop\claude\gestor_gastos_pareja\initial_data.json'

def get(path, params=None):
    for _ in range(3):
        try:
            r = requests.get(f'{BASE}{path}', params=params,
                             headers={'Accept': 'application/json'}, timeout=15)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(2)
    return []

from datetime import date, timedelta

usuarios   = get('/api/usuarios')
categorias = get('/api/categorias')
metas      = get('/api/metas')

meses = []
hoy = date.today()
for i in range(-3, 4):
    d = date(hoy.year, hoy.month, 1)
    mes_num = hoy.month + i
    anio = hoy.year
    while mes_num > 12: mes_num -= 12; anio += 1
    while mes_num < 1:  mes_num += 12; anio -= 1
    meses.append((str(mes_num), str(anio)))

gastos = []
for m, y in meses:
    gastos.extend(get('/api/gastos', {'mes': m, 'anio': y}))

ingresos = []
for m, y in meses:
    ingresos.extend(get('/api/ingresos', {'mes': m, 'anio': y}))

presupuestos = []
for m, y in meses:
    presupuestos.extend(get('/api/presupuestos', {'mes': m, 'anio': y}))

data = {
    'usuarios': usuarios,
    'categorias': categorias,
    'gastos': gastos,
    'ingresos': ingresos,
    'metas': metas,
    'presupuestos': presupuestos,
    'aportaciones': [],
    'backup_at': date.today().isoformat() + 'T00:00:00',
}
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f'Seed guardado: {len(usuarios)} usuarios, {len(categorias)} categorias, '
      f'{len(gastos)} gastos, {len(ingresos)} ingresos, {len(metas)} metas, '
      f'{len(presupuestos)} presupuestos')
print(f'Archivo: {OUT}')
