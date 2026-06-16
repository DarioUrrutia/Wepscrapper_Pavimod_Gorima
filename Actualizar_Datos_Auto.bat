@echo off
REM ============================================================
REM  Version AUTOMATICA (sin pausa) para Tarea Programada.
REM  La ejecuta el Programador de tareas de Windows cada 15 dias.
REM  Registra todo en data\runs\auto_update.log
REM ============================================================
cd /d "%~dp0"

if not exist "data\runs" mkdir "data\runs"
set "LOG=data\runs\auto_update.log"

echo. >> "%LOG%"
echo ======================================================== >> "%LOG%"
echo  INICIO: %date% %time% >> "%LOG%"
echo ======================================================== >> "%LOG%"

call ".venv\Scripts\activate.bat"

echo --- git pull --- >> "%LOG%"
git pull --rebase --autostash origin main >> "%LOG%" 2>&1

echo --- scraping + comparativo --- >> "%LOG%"
python actualizar_datos.py >> "%LOG%" 2>&1
if errorlevel 1 (
    echo RESULTADO: ERROR - ANAS sin datos, NO se publica. %date% %time% >> "%LOG%"
    exit /b 1
)

echo --- publicando a GitHub --- >> "%LOG%"
git add data/processed/master_avanzamento.xlsx data/processed/anas_obras_*.csv >> "%LOG%" 2>&1
git commit -m "Update master state - auto" >> "%LOG%" 2>&1
git push origin main >> "%LOG%" 2>&1

echo RESULTADO: OK publicado. %date% %time% >> "%LOG%"
exit /b 0
