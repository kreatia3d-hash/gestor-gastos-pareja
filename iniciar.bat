@echo off
title Gestor de Gastos en Pareja
color 0A
cls
echo.
echo  =========================================
echo    GESTOR DE GASTOS EN PAREJA
echo  =========================================
echo.

:: Abrir el puerto 5001 en el firewall de Windows (para que el movil pueda conectarse)
netsh advfirewall firewall show rule name="Gestor Gastos App" >nul 2>&1
if %errorlevel% neq 0 (
    echo  Configurando acceso de red por primera vez...
    netsh advfirewall firewall add rule name="Gestor Gastos App" protocol=TCP dir=in localport=5001 action=allow >nul 2>&1
    netsh advfirewall firewall add rule name="Gestor Gastos UDP" protocol=UDP dir=in localport=5002 action=allow >nul 2>&1
)

echo  Comprobando dependencias...
pip install -r requirements.txt --quiet --disable-pip-version-check 2>nul
echo  OK
echo.
echo  Iniciando la aplicacion...
echo  El navegador se abrira automaticamente.
echo.
echo  El tunel de internet se crea en ~5-10 segundos.
echo  Cuando este listo, aparecera la URL en Configuracion.
echo.
echo  Para cerrar la app, cierra esta ventana.
echo  =========================================
echo.
python app.py
echo.
echo  La aplicacion se ha cerrado.
pause
