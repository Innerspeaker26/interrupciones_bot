@echo off
rem ==========================================================================
rem Actualiza la base del GeoAgente y la publica en Google Drive.
rem
rem Ciclo completo (lo hace descargar_interrupciones.py):
rem   1. Descarga el crudo filtrado del portal SUNASS (FORMULARIO N002).
rem   2. Regenera data\interrupciones_limpio.parquet con preparar_datos.py.
rem   3. Publica en  G:\Mi unidad\data_interrupciones_bot  la copia con fecha
rem      y la copia fija interrupciones_limpio_latest.parquet (mismo ID de
rem      Drive en cada corrida, para consumo de la app en la nube).
rem
rem Pensado para el Programador de tareas de Windows. Registrar con:
rem   schtasks /Create /TN "Actualizar base GeoAgente" /TR "%~f0" /SC DAILY /ST 06:00 /F
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
