# Cierra cualquier instancia previa de descargar_interrupciones.py que haya
# quedado atascada. Una corrida normal dura ~40 segundos y la tarea se dispara
# cada 15 minutos, asi que si al empezar ya hay una viva, esta colgada.
#
# Por que importa: un cuelgue no solo pierde su propia corrida. El proceso
# muerto mantiene abierto logs\actualizar_base.log (es su stdout redirigido),
# y eso impide que las corridas siguientes escriban en el log; la tarea sigue
# disparandose y terminando con codigo 0, pero sin hacer nada. Asi se congelo
# la base entre el 07/09 y el 10/09 de 2026.

Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*descargar_interrupciones*' } |
    ForEach-Object {
        Write-Output "Cerrando instancia atascada (PID $($_.ProcessId), iniciada $($_.CreationDate))"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
