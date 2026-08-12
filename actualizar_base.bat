@echo off
rem ==========================================================================
rem Actualiza la base del GeoAgente: descarga el crudo del portal SUNASS
rem (FORMULARIO N002) y regenera data/interrupciones_limpio.parquet.
rem Pensado para ejecutarse desde el Programador de tareas de Windows.
rem Registro de cada corrida en logs\actualizar_base.log
rem ==========================================================================
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs

echo ====================================================== >> logs\actualizar_base.log
echo Inicio: %date% %time% >> logs\actualizar_base.log

rem Usa el Python del sistema, que es el que tiene instalado arcgis.
rem (descargar_interrupciones.py delega la preparacion al .venv del proyecto)
python descargar_interrupciones.py >> logs\actualizar_base.log 2>&1

echo Fin: %date% %time% (codigo %errorlevel%) >> logs\actualizar_base.log
endlocal
