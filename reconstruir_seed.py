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

usuarios   = get('/api/usuarios')
categorias = get('/api/categorias')
metas      = get('/api/metas')

gastos = []
for m, y in [('4','2026'),('5','2026'),('6','2026'),('7','2026'),('8','2026'),('9','2026')]:
    gastos.extend(get('/api/gastos', {'mes': m, 'anio': y}))

ingresos = []
for m, y in [('3','2026'),('4','2026'),('5','2026'),('6','2026'),('7','2026'),('8','2026'),('9','2026')]:
    ingresos.extend(get('/api/ingresos', {'mes': m, 'anio': y}))

data = {
    'usuarios': usuarios,
    'categorias': categorias,
    'gastos': gastos,
    'ingresos': ingresos,
    'metas': metas,
    'aportaciones': [],
    'backup_at': '2026-05-09T19:30:00',
}
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f'Seed guardado: {len(usuarios)} usuarios, {len(categorias)} categorias, '
      f'{len(gastos)} gastos, {len(ingresos)} ingresos, {len(metas)} metas')
print(f'Archivo: {OUT}')
