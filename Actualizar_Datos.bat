@echo off
REM ============================================================
REM  Actualizar datos ANAS y publicar a GitHub (Render).
REM  Doble clic para ejecutar. Debe correr desde una red que
REM  alcance stradeanas.it (tu maquina local lo hace; Render no).
REM ============================================================
cd /d "%~dp0"

echo Activando entorno...
call ".venv\Scripts\activate.bat"

echo Sincronizando con GitHub...
git pull --rebase --autostash origin main

echo.
echo Ejecutando scraping + comparativo...
python actualizar_datos.py
if errorlevel 1 (
    echo.
    echo ============================================================
    echo  ERROR: el scraping no obtuvo datos de ANAS.
    echo  NO se publica nada. Revisa tu conexion y reintenta.
    echo ============================================================
    pause
    exit /b 1
)

echo.
echo Publicando a GitHub...
git add data/processed/master_avanzamento.xlsx data/processed/anas_obras_*.csv
git commit -m "Update master state - scraping locale" || echo (Nessuna modifica da commitare)
git push origin main

echo.
echo ============================================================
echo  LISTO. Datos publicados.
echo  Render mostrara los datos nuevos tras el redeploy (1-2 min).
echo ============================================================
pause
