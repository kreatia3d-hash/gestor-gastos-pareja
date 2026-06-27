try:
    from .api_gastos import bp as gastos_bp
    from .api_ingresos import bp as ingresos_bp
    from .api_metas import bp as metas_bp
    from .api_presupuestos import bp as presupuestos_bp
    from .api_dashboard import bp as dashboard_bp
    from .api_ia import bp as ia_bp
    from .api_usuarios import bp as usuarios_bp
    from .api_datos import bp as datos_bp
    from .paginas import bp as paginas_bp
    from .api_push import bp as push_bp
except Exception as _e:
    import traceback, sys
    print(f'[blueprints] ERROR al importar: {_e}', file=sys.stderr)
    traceback.print_exc()
    raise

__all__ = [
    'gastos_bp', 'ingresos_bp', 'metas_bp', 'presupuestos_bp',
    'dashboard_bp', 'ia_bp', 'usuarios_bp', 'datos_bp', 'paginas_bp', 'push_bp',
]
