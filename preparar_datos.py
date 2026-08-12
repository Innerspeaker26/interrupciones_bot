"""
Pipeline de preparacion de la base de interrupciones. CORRE FUERA DEL AGENTE.

El agente debe correr solo lo necesario. La limpieza de la base de datos se realiza antes.
Dado que aun no se esta trabajanod con una base de datos dinamica, este script se corre
una sola vez, para generar una base limpia lista para el uso del agente.
Se planea, a traves de un bat, automatizar la ejecución de este script asi como la bajada de información

Por tanto, el agente no debe correr este script.

Salidas en data/:
  - interrupciones_limpio.parquet   la base lista (solo columnas utiles, tipos finos)
  - metadata.json                   cuando y de que archivo se genero, conteos y el
                                    resultado del control de zona horaria
"""

#--------------------------------------------------------------#
# Imports y constantes de configuración
# -------------------------------------------------------------# 

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import geopandas as gpd
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DIR_CRUDO = BASE_DIR / "data_raw"
DIR_SALIDA = BASE_DIR / "data"
PARQUET_LIMPIO = DIR_SALIDA / "interrupciones_limpio.parquet"
METADATA = DIR_SALIDA / "metadata.json"

TZ_LIMA = ZoneInfo("America/Lima")

# --------------------------------------------------------------------------- #
# Reglas de limpieza y funciones auxiliares
# Se disminuiria el tamaño de columnas, para contar con una base de datos menor
# --------------------------------------------------------------------------- #

# Nombres ajustados

RENOMBRES = {
    "tipo_de_interrupci_n": "tipo",
    "motivo_de_interrupci_n": "motivo",
    "detalle_de_motivo": "detalle",
    "acciones_correctivas": "acciones",
    "tiempo_total": "duracion",
    "n_de_conexiones_afectadas": "conexiones",
    "fecha_de_inicio": "inicio",
    "fecha_estimada_restablecimiento": "fin_estimado",
    # El FORMULARIO N002 (Survey123) nombra igual casi todo, salvo este campo,
    # que ademas llega recortado a 31 caracteres por el File Geodatabase.
    "fecha_estimada_de_restablecimie": "fin_estimado",
}

# Elección de columnas

COLS_TEXTO = ["provincia", "distrito", "localidad", "sector", "eps", "tipo",
              "situacion", "clasificacion", "motivo", "detalle", "acciones", "duracion"]
COLS_FECHA = ["inicio", "fin_estimado"]
COLS_FINALES = COLS_TEXTO + COLS_FECHA + ["conexiones", "zona", "prov_norm", "dist_norm",
                                          "departamento", "dep_norm", "geometry"]

# Solución de errores encontrados

FIX_PROVINCIA = {"LIM": "LIMA", "VILLA EL SALVADOR": "LIMA"}

# Control de zona horaria: con variable de control
CONTROL = {"distrito": "CERRO AZUL", "fecha": "2026-07-17", "hora_esperada": "06:00"}

## Funciones auxiliares
def _norm(texto) -> str:
    """MAYUSCULAS, sin tildes ni espacios sobrantes. 'CAÑETE ' -> 'CANETE'."""
    t = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).strip().upper()


def _zona(r: pd.Series) -> str:
    """Nombre de la zona afectada en terminos que el ciudadano reconozca.

    Cada EP llena `localidad` y `sector` a su manera (SEDAPAL pone el nombre en
    localidad y un codigo en sector; EMAPA Canete al reves). Se elige el valor mas
    informativo: ni vacio, ni codigo numerico pelado, ni una repeticion del distrito.
    """
    def _txt(v) -> str:
        return "" if pd.isna(v) else str(v).strip()

    distrito = _txt(r.get("distrito"))
    candidatos = [_txt(r.get(c)) for c in ("localidad", "sector")]
    utiles = [v for v in candidatos if v and not v.isdigit() and _norm(v) != _norm(distrito)]
    if utiles:
        return utiles[0]
    numerico = next((v for v in candidatos if v.isdigit()), "")
    if numerico and distrito:
        return f"sector {numerico} de {distrito}"
    return distrito or "tu zona"


def _leer(ruta: Path) -> gpd.GeoDataFrame:
    """Lee parquet/gpkg/geojson/shp/gdb con geometria, o csv sin ella."""
    ext = ruta.suffix.lower()
    if ext == ".parquet":
        return gpd.read_parquet(ruta)
    if ext in (".gpkg", ".geojson", ".json", ".shp", ".gdb"):
        # .gdb es una CARPETA File Geodatabase (descarga de descargar_interrupciones.py);
        # geopandas la lee con el driver OpenFileGDB.
        return gpd.read_file(ruta)
    if ext == ".csv":
        print("[aviso] CSV sin geometria: el cruce por coordenadas no funcionara, "
              "solo la consulta por distrito.")
        df = pd.read_csv(ruta)
        return gpd.GeoDataFrame(df, geometry=[None] * len(df), crs="EPSG:4326")
    raise SystemExit(f"Formato no soportado: {ruta.name} (usa parquet, gpkg, geojson, shp, gdb o csv)")


def _entrada_por_defecto() -> Path:
    """El archivo mas reciente de data_raw/. Es donde el futuro .bat dejara el crudo."""
    if DIR_CRUDO.is_dir():
        candidatos = [p for p in DIR_CRUDO.iterdir()
                      if p.suffix.lower() in (".parquet", ".gpkg", ".geojson", ".shp", ".csv", ".gdb")]
        if candidatos:
            return max(candidatos, key=lambda p: p.stat().st_mtime)
    raise SystemExit(
        f"No hay archivo crudo en {DIR_CRUDO}. Coloca ahi la descarga de la fuente "
        "(parquet/gpkg/csv) o pasa la ruta con --entrada."
    )


def preparar(entrada: Path, datos_en_utc: bool = False) -> dict:
    """Ejecuta todo el pipeline y devuelve el resumen que va a metadata.json."""
    print(f"Leyendo {entrada} ...")
    gdf = _leer(entrada)
    n_crudo = len(gdf)

    # --- 1. Renombrar al esquema limpio ------------------------------------- #
    gdf = gdf.rename(columns=RENOMBRES)
    faltan = [c for c in COLS_TEXTO + COLS_FECHA if c not in gdf.columns]
    if faltan:
        raise SystemExit(
            f"Al crudo le faltan columnas esperadas: {faltan}. "
            "Revisa que sea la base de interrupciones de la fuente habitual."
        )

    # --- 2. Fechas: parsear y, si el crudo viene de ArcGIS, pasar UTC -> Lima  #
    for col in COLS_FECHA:
        s = pd.to_datetime(gdf[col], errors="coerce")
        if datos_en_utc:
            if s.dt.tz is None:
                s = s.dt.tz_localize("UTC")
            s = s.dt.tz_convert(TZ_LIMA).dt.tz_localize(None)
        elif s.dt.tz is not None:
            s = s.dt.tz_convert(TZ_LIMA).dt.tz_localize(None)
        gdf[col] = s

    # --- 3. Texto: limpiar espacios y erratas, y crear columnas de busqueda -- #
    for col in COLS_TEXTO:
        gdf[col] = gdf[col].astype("string").str.strip()
    gdf["prov_norm"] = gdf["provincia"].map(_norm).replace(FIX_PROVINCIA)
    gdf["dist_norm"] = gdf["distrito"].map(_norm)
    # La base nueva es NACIONAL y trae departamento: se conserva normalizado para
    # que el panorama y los contadores del panel puedan acotarse a Lima/Callao.
    # Las fuentes viejas no lo traen (ya venian acotadas): queda vacio.
    if "departamento" not in gdf.columns:
        gdf["departamento"] = pd.NA
    gdf["departamento"] = gdf["departamento"].astype("string").str.strip()
    gdf["dep_norm"] = gdf["departamento"].map(lambda v: _norm(v) if pd.notna(v) else "")
    gdf["conexiones"] = pd.to_numeric(gdf.get("conexiones"), errors="coerce")

    # --- 4. Nombre de zona precalculado (antes se decidia en cada respuesta) - #
    gdf["zona"] = gdf.apply(_zona, axis=1)

    # --- 5. Geometrias: reparar una vez, no en cada arranque de la app ------- #
    #     Las filas sin poligono se conservan: no sirven para el cruce espacial
    #     pero si para la consulta por distrito (15% de la base).
    con_geom = gdf.geometry.notna()
    if con_geom.any():
        gdf.loc[con_geom, "geometry"] = gdf.loc[con_geom].geometry.make_valid()
        rotas = con_geom & (~gdf.geometry.is_valid | gdf.geometry.is_empty)
        if rotas.any():
            print(f"[aviso] {int(rotas.sum())} geometria(s) irreparable(s): quedan sin poligono.")
            gdf.loc[rotas, "geometry"] = None
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")

    # --- 6. Recorte de columnas y escritura ---------------------------------- #
    gdf = gdf[[c for c in COLS_FINALES if c in gdf.columns]]
    DIR_SALIDA.mkdir(exist_ok=True)
    gdf.to_parquet(PARQUET_LIMPIO)

    # --- 7. Control de zona horaria ------------------------------------------ #
    ctrl = gdf[(gdf["dist_norm"] == CONTROL["distrito"])
               & (gdf["inicio"].astype(str).str.startswith(CONTROL["fecha"]))]
    hora = ctrl["inicio"].iloc[0].strftime("%H:%M") if not ctrl.empty else None
    tz_ok = (hora == CONTROL["hora_esperada"]) if hora else None

    meta = {
        "generado": datetime.now(TZ_LIMA).strftime("%d/%m/%Y %H:%M"),
        "archivo_origen": entrada.name,
        "flag_utc": datos_en_utc,
        "registros_crudos": n_crudo,
        "registros": len(gdf),
        "con_geometria": int(gdf.geometry.notna().sum()),
        "provincias": int(gdf["prov_norm"].nunique()),
        "distritos": int(gdf["dist_norm"].nunique()),
        "control_cerro_azul": hora,
        "zona_horaria_ok": tz_ok,
    }
    METADATA.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nOK -> {PARQUET_LIMPIO.name}")
    print(f"  {meta['registros']:,} registros ({meta['con_geometria']:,} con poligono), "
          f"{meta['distritos']} distritos, {meta['provincias']} provincias")
    if tz_ok is False:
        print(f"  [ALERTA] Control de zona horaria: {hora}, se esperaba "
              f"{CONTROL['hora_esperada']}. Prueba {'sin' if datos_en_utc else 'con'} --utc.")
    elif tz_ok:
        print("  Control de zona horaria: OK (Cerro Azul 17/07 a las 06:00)")
    return meta

## Interruptor de llamada

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Prepara la base limpia que consume el agente.")
    ap.add_argument("--entrada", type=Path, default=None, help="Archivo crudo (por defecto, el mas reciente de data_raw/)")
    ap.add_argument("--utc", action="store_true", help="El crudo trae las fechas en UTC (descarga directa de ArcGIS)")
    args = ap.parse_args()
    try:
        preparar(args.entrada or _entrada_por_defecto(), datos_en_utc=args.utc)
    except SystemExit as e:
        print(f"ERROR: {e}")
        sys.exit(1)
