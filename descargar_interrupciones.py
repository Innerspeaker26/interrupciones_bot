# -*- coding: utf-8 -*-
"""
Descarga el crudo de interrupciones (datos + poligonos dibujados) del Survey123
"FORMULARIO N002 Prueba Semaforo" del portal SUNASS y lo deja en data_raw/,
listo para que preparar_datos.py genere la base limpia del agente.

La descarga viene FILTRADA desde el servidor (ver clausula_where): solo las
interrupciones que aun no terminan -- se preservan aunque sean mas antiguas que
la ventana -- y las iniciadas en los ultimos VENTANA_DIAS dias. Asi se bajan
~1,300 filas en segundos en vez de las ~30,000 de la base completa.

Al final, la base limpia se publica en la carpeta del Drive (CARPETA_DRIVE)
con el patron de MOREA: copia con fecha + copia fija `interrupciones_limpio_
latest.parquet` que conserva su ID de Drive entre corridas.

Automatizacion: actualizar_base.bat envuelve este script para el Programador
de tareas de Windows (registro en logs/actualizar_base.log).

    python descargar_interrupciones.py              # descarga y prepara la base
    python descargar_interrupciones.py --solo-descargar

Formato: File Geodatabase (zip con .gdb), NO shapefile. El shapefile trunca los
nombres de campo a 10 caracteres (el pipeline espera nombres largos como
'fecha_estimada_de_restablecimie') y sus fechas pierden la hora, que aqui es
critica (cortes 06:00 -> 18:00). Tampoco GeoPackage ni GeoJSON: el "GPKG" de
este portal sale en formato SQLite propietario de Esri (st_geometry) que GDAL
no lee, y el GeoJSON es tan pesado que la descarga revienta la memoria de la
libreria arcgis. El FGDB viene comprimido (liviano), conserva poligonos,
nombres completos y fechas con hora en UTC, y geopandas lo lee via OpenFileGDB.

Requisitos:
    pip install arcgis
    Perfil de credenciales "sunass_survey" apuntando al portal SUNASS, p. ej.:
        from arcgis.gis import GIS
        GIS("https://geosunass.sunass.gob.pe/gisportal", "usuario", "clave",
            profile="sunass_survey")

Las fechas de ArcGIS llegan en UTC: por eso la preparacion se invoca con --utc.
El control de zona horaria de preparar_datos.py (Cerro Azul 17/07 a las 06:00)
avisa si el supuesto deja de cumplirse.
"""

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arcgis.gis import GIS

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
PERFIL = "sunass_survey"

# URL del portal empresarial (por si el perfil no lo tuviera guardado).
PORTAL_URL = "https://geosunass.sunass.gob.pe/gisportal"

# El crudo se deja donde preparar_datos.py lo busca por defecto.
BASE_DIR = Path(__file__).resolve().parent
CARPETA_DESTINO = BASE_DIR / "data_raw"

# Carpeta del Drive (sincronizada con Google Drive Desktop) donde se publica la
# base limpia tras cada corrida, con el mismo patron que MOREA: una copia con
# fecha (historial) y una copia de NOMBRE FIJO que se sobrescribe -- al
# conservar el mismo archivo (mismo ID de Drive), una app en la nube puede
# bajarla siempre desde el mismo enlace.
CARPETA_DRIVE = Path(r"G:\Mi unidad\data_interrupciones_bot")
NOMBRE_FIJO = "interrupciones_limpio_latest.parquet"
PARQUET_LIMPIO = BASE_DIR / "data" / "interrupciones_limpio.parquet"
METADATA_LIMPIA = BASE_DIR / "data" / "metadata.json"

# ID del formulario de Survey123: "FORMULARIO N002 Prueba Semaforo"
SURVEY_ITEM_ID = "3bc0e99719754f1b902266f8efc0821c"

# Nombre amigable para el archivo de salida
NOMBRE = "interrupciones_semaforo"

# Sello de fecha/hora para nombrar el archivo -> yymmdd_hhmm  (ej. 260809_1430)
FECHA = f"{datetime.now():%y%m%d_%H%M}"

# Ventana de descarga: en vez de bajar toda la base (30 mil filas), el portal
# filtra ANTES de exportar. Se conservan las interrupciones que aun no terminan
# (aunque tengan mas de VENTANA_DIAS de antiguedad) y las iniciadas en la ventana.
VENTANA_DIAS = 30


def clausula_where() -> str:
    """WHERE que evalua el servidor. Las fechas del servicio estan en UTC, por
    eso el limite de la ventana se calcula en UTC y 'no terminada' se compara
    contra CURRENT_TIMESTAMP (que el servidor tambien evalua en UTC).
    Ojo: el campo llega recortado a 31 caracteres desde Survey123."""
    desde = datetime.now(timezone.utc) - timedelta(days=VENTANA_DIAS)
    return ("fecha_estimada_de_restablecimie >= CURRENT_TIMESTAMP "
            f"OR fecha_de_inicio >= TIMESTAMP '{desde:%Y-%m-%d %H:%M:%S}'")


# ---------------------------------------------------------------------------
# Funciones
# ---------------------------------------------------------------------------
def resolver_feature_service(gis, survey_item_id):
    """A partir del item del formulario de Survey123, obtiene el Feature
    Service que contiene los datos capturados (atributos + poligonos).
    """
    form = gis.content.get(survey_item_id)
    if form is None:
        raise ValueError(f"No se encontró el item '{survey_item_id}'. "
                         f"¿El perfil apunta al portal correcto y tienes acceso?")

    print(f"  Formulario: '{form.title}'  (tipo: {form.type})")

    # Si el ID ya fuese directamente un Feature Layer, se usa tal cual.
    if form.type == "Feature Service":
        return form

    # Caso normal: el formulario está relacionado con su Feature Service.
    relacionados = form.related_items("Survey2Service", "forward")
    if not relacionados:
        # Algunos surveys usan la relación de datos "Survey2Data"
        relacionados = form.related_items("Survey2Data", "forward")

    if not relacionados:
        raise ValueError("No se encontró el Feature Service relacionado con "
                         "el formulario. Revisa que el survey tenga datos.")

    fs = relacionados[0]
    print(f"  Feature Service: '{fs.title}'  (id: {fs.id})")
    return fs


def exportar(gis, item, nombre, carpeta):
    """Exporta el Feature Service a File Geodatabase, descarga el zip y lo
    extrae como <nombre>_<fecha>.gdb dentro de `carpeta`. Devuelve la ruta
    del .gdb, que preparar_datos.py lee directamente."""
    import shutil
    import zipfile

    where = clausula_where()
    print(f"  -> Exportando '{item.title}' a File Geodatabase...")
    print(f"     Filtro del servidor: {where}")
    titulo_export = f"{nombre}_{FECHA}_{datetime.now():%H%M%S}"
    export_item = item.export(titulo_export, "File Geodatabase",
                              parameters={"layers": [{"id": 0, "where": where}]})

    try:
        export_item.status(job_type="export")               # espera a que termine
        ruta_zip = Path(export_item.download(save_path=str(carpeta)))
        print(f"     Zip descargado ({ruta_zip.stat().st_size / 1e6:.1f} MB)")

        # El zip trae un unico .gdb con nombre generico (_ags_data<hash>.gdb):
        # se extrae a una carpeta de trabajo y se renombra al nombre definitivo.
        extraccion = carpeta / f"_extrayendo_{FECHA}"
        with zipfile.ZipFile(ruta_zip) as z:
            z.extractall(extraccion)
        gdb_origen = next(extraccion.glob("*.gdb"))

        destino = carpeta / f"{nombre}_{FECHA}.gdb"
        if destino.exists():
            shutil.rmtree(destino)
        gdb_origen.replace(destino)
        shutil.rmtree(extraccion, ignore_errors=True)
        ruta_zip.unlink()
        print(f"     Guardado en: {destino}")

        # Retencion en data_raw: cada corrida deja un .gdb (~3 MB) y a ritmo
        # de 15 min serian ~340 MB/dia. Se conservan solo los 5 mas recientes.
        gdbs = sorted(carpeta.glob(f"{nombre}_*.gdb"))
        for viejo in gdbs[:-5]:
            shutil.rmtree(viejo, ignore_errors=True)
        return destino
    finally:
        # Solo se borra el item de export recien creado por este script (por
        # referencia directa), nunca resultados de una busqueda en el portal.
        try:
            export_item.delete()
        except Exception as e:
            print(f"     (Aviso) No se pudo borrar el item temporal: {e}")


def publicar_en_drive() -> None:
    """Copia la base limpia recien generada a la carpeta del Drive: una copia
    con fecha y la copia fija que sobreescribe (patron survey_morea_latest).
    Si el Drive no esta montado, avisa sin tumbar la corrida: la base local
    quedo bien y la publicacion se recupera en la siguiente ejecucion."""
    if not PARQUET_LIMPIO.exists():
        print("  (Aviso) No hay base limpia que publicar en el Drive.")
        return
    try:
        CARPETA_DRIVE.mkdir(parents=True, exist_ok=True)
        historico = CARPETA_DRIVE / f"interrupciones_limpio_{FECHA}.parquet"
        shutil.copyfile(PARQUET_LIMPIO, historico)
        latest = CARPETA_DRIVE / NOMBRE_FIJO
        shutil.copyfile(PARQUET_LIMPIO, latest)
        if METADATA_LIMPIA.exists():
            shutil.copyfile(METADATA_LIMPIA, CARPETA_DRIVE / "metadata.json")
        print(f"  -> Publicado en Drive: {historico.name} + {NOMBRE_FIJO}")

        # Retencion: corriendo cada 15 min serian ~96 copias/dia (~400 MB).
        # Se conservan solo las ultimas MAX_HISTORICOS con fecha; la copia
        # fija (latest) nunca se toca.
        MAX_HISTORICOS = 30
        fechados = sorted(CARPETA_DRIVE.glob("interrupciones_limpio_2*.parquet"))
        for viejo in fechados[:-MAX_HISTORICOS]:
            viejo.unlink(missing_ok=True)
        if len(fechados) > MAX_HISTORICOS:
            print(f"     Retencion: {len(fechados) - MAX_HISTORICOS} copia(s) antigua(s) eliminada(s).")
    except Exception as e:  # G: sin montar, sin espacio, etc.
        print(f"  (Aviso) No se pudo publicar en el Drive: {e}")


def preparar_base(ruta_crudo: Path) -> bool:
    """Corre preparar_datos.py --utc sobre el crudo recién bajado, usando el
    Python del venv del proyecto (este script puede correr en otro entorno,
    donde vive `arcgis` pero quizá no `geopandas`)."""
    venv_py = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    interprete = str(venv_py) if venv_py.exists() else sys.executable
    print(f"\n  -> Preparando la base limpia ({Path(interprete).parent.parent.name})...")
    proc = subprocess.run(
        [interprete, str(BASE_DIR / "preparar_datos.py"),
         "--entrada", str(ruta_crudo), "--utc"],
        cwd=str(BASE_DIR),
    )
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Descarga el crudo de interrupciones a data_raw/.")
    ap.add_argument("--solo-descargar", action="store_true",
                    help="No ejecutar preparar_datos.py después de descargar.")
    args = ap.parse_args()

    inicio = datetime.now()
    print(f"===== Ejecución iniciada: {inicio:%Y-%m-%d %H:%M:%S} =====")

    CARPETA_DESTINO.mkdir(parents=True, exist_ok=True)

    # Conexión: usa el perfil; si falla, intenta con PORTAL_URL + perfil.
    try:
        gis = GIS(profile=PERFIL)
    except Exception:
        gis = GIS(PORTAL_URL, profile=PERFIL)
    print("Conectado como:", gis.properties.user.username)

    fs = resolver_feature_service(gis, SURVEY_ITEM_ID)
    ruta = exportar(gis, fs, NOMBRE, CARPETA_DESTINO)

    if args.solo_descargar:
        print("\n  Crudo descargado. Para generar la base limpia:")
        print(f"    python preparar_datos.py --entrada \"{ruta}\" --utc")
    elif preparar_base(ruta):
        publicar_en_drive()
    else:
        print("\n  [ALERTA] La preparación falló. Revisa el error de arriba y corre:")
        print(f"    python preparar_datos.py --entrada \"{ruta}\" --utc")

    fin = datetime.now()
    print(f"\n===== Finalizado: {fin:%Y-%m-%d %H:%M:%S} "
          f"(duración: {fin - inicio}) =====")
    print(f"Archivo: {ruta}")


if __name__ == "__main__":
    main()
