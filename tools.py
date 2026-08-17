"""
Tools del GeoAgente de interrupciones de agua (Lima).

Este modulo NO limpia datos: consume el parquet que dejo `preparar_datos.py`
Si la base limpia no existe, falla al arrancar con la instruccion exacta para generarla. El agente
corre lo justo necesario.
"""

#-------------------------------------------------------------------#
# Importaciones, constantes y rutas
#-------------------------------------------------------------------#

from __future__ import annotations

import difflib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import folium
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

TZ_LIMA = ZoneInfo("America/Lima")
TELEFONO = "1899"

# Caja de cobertura: Lima, Callao y provincias (Canete, Huaral, Huaura, Barranca).
BBOX = {"lat_min": -13.6, "lat_max": -10.2, "lon_min": -77.9, "lon_max": -76.0}

DATA_DIR = Path(__file__).resolve().parent / "data"
PARQUET_LIMPIO = DATA_DIR / "interrupciones_limpio.parquet"
METADATA = DATA_DIR / "metadata.json"

# Copia fija del Drive que la tarea programada sobrescribe cada 15 minutos
# (descargar_interrupciones.py -> publicar_en_drive). El ID no cambia entre
# corridas, asi que este enlace siempre apunta a la base mas fresca. La app
# (local o en Streamlit Cloud) la baja de aqui; si falla, usa data/ local.
DRIVE_FILE_ID = "1sukL7Bbz1dvhdOpwzSJlRUaTBrYGG3H2"
URL_BASE_DRIVE = f"https://drive.google.com/uc?export=download&id={DRIVE_FILE_ID}"
TTL_BASE_SEG = 15 * 60          # mismo ritmo que la tarea programada

# --------------------------------------------------------------------------- #
# Estado compartido
#   _BASE_CACHE: el parquet cargado + cuando se cargo (se refresca por TTL).
#   _SESSION_DATA: lo propio de la consulta actual — punto ubicado, filas
#   coincidentes y la evidencia contra la que verifican los guardarrailes.
# --------------------------------------------------------------------------- #
_BASE_CACHE: dict = {}
_SESSION_DATA: dict = {}


def get_session_data() -> dict:
    return _SESSION_DATA


def clear_session_data() -> None:
    _SESSION_DATA.clear()


def _evidencia(texto: str) -> str:
    """Registra lo que devolvio una tool. Es la unica forma de que los
    guardarrailes distingan un dato real de uno inventado por el modelo."""
    _SESSION_DATA.setdefault("evidencia", []).append(texto)
    return texto


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _norm(texto) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).strip().upper()


def _ahora() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(TZ_LIMA).replace(tzinfo=None))


def _fecha(x) -> str:
    return "sin dato" if pd.isna(x) else pd.to_datetime(x).strftime("%d/%m/%Y %H:%M")


# --------------------------------------------------------------------------- #
# Carga (la base ya viene limpia)
# --------------------------------------------------------------------------- #
def _leer_parquet_drive():
    """Baja la copia fija del Drive. Devuelve None si no se pudo (sin internet,
    Drive caido, permiso revocado): el llamador cae a la copia local."""
    import io

    import requests

    try:
        r = requests.get(URL_BASE_DRIVE, timeout=20)
        r.raise_for_status()
        # Un enlace sin permiso devuelve HTML, no parquet: mejor detectarlo
        # aqui que cachear basura.
        if not r.content.startswith(b"PAR1"):
            raise ValueError("la respuesta no es un parquet (¿permiso del enlace?)")
        gdf = gpd.read_parquet(io.BytesIO(r.content))
        print(f"[base] Refrescada desde el Drive: {len(gdf):,} registros.")
        return gdf
    except Exception as e:  # noqa: BLE001
        print(f"[base] Drive no disponible ({type(e).__name__}: {e}); se usa la copia local.")
        return None


def cargar_base() -> gpd.GeoDataFrame:
    """Todos los registros, incluidos los ~15% sin poligono (cuentan para la
    consulta por distrito, no para el cruce espacial).

    La base viva esta en el Drive (la tarea programada la republica cada 15
    min): se baja de ahi y se refresca por TTL, sin reiniciar la app ni cortar
    las sesiones de chat. El parquet local de data/ es el respaldo."""
    import time

    ahora = time.time()
    if "base" in _BASE_CACHE and ahora - _BASE_CACHE.get("ts", 0) < TTL_BASE_SEG:
        return _BASE_CACHE["base"]

    gdf = _leer_parquet_drive()
    if gdf is None:
        if "base" in _BASE_CACHE:
            # Sin conexion pero con base previa: seguir con ella y reintentar
            # el Drive cuando venza el proximo TTL.
            _BASE_CACHE["ts"] = ahora
            return _BASE_CACHE["base"]
        if not PARQUET_LIMPIO.is_file():
            raise FileNotFoundError(
                f"No se pudo bajar la base del Drive y tampoco existe la copia "
                f"local ({PARQUET_LIMPIO}). Genera con: python preparar_datos.py"
            )
        gdf = gpd.read_parquet(PARQUET_LIMPIO)

    _BASE_CACHE["base"] = gdf
    _BASE_CACHE["ts"] = ahora
    _BASE_CACHE.pop("espacial", None)   # derivado de la base: se invalida junto
    return gdf


def cargar_base_espacial() -> gpd.GeoDataFrame:
    """Solo filas con geometria: lo unico que puede entrar a un sjoin."""
    if "espacial" not in _BASE_CACHE:
        gdf = cargar_base()
        _BASE_CACHE["espacial"] = gdf[gdf.geometry.notna()].copy()
    return _BASE_CACHE["espacial"]


def metadata_base() -> dict:
    """Cuando y de que archivo se preparo la base. Lo muestra la app."""
    if METADATA.is_file():
        return json.loads(METADATA.read_text(encoding="utf-8"))
    return {}


def listar_provincias(ambito_lima: bool = False) -> list[str]:
    """Con ambito_lima=True se limita al departamento de Lima y al Callao (el
    mismo recorte del panorama), para el desplegable de la app."""
    gdf = cargar_base()
    if ambito_lima:
        gdf = _ambito_lima(gdf)
    return sorted(gdf["prov_norm"].dropna().unique().tolist())


def listar_distritos(provincia: str = "") -> list[str]:
    gdf = cargar_base()
    if provincia:
        gdf = gdf[gdf["prov_norm"] == _norm(provincia)]
    return sorted(gdf["dist_norm"].dropna().unique().tolist())


# --------------------------------------------------------------------------- #
# Filtros temporales (la logica de negocio)
# --------------------------------------------------------------------------- #
def _en_curso(df: pd.DataFrame, tipo: str = "") -> pd.DataFrame:
    """Cortes con el servicio INTERRUMPIDO en este instante, sean imprevistos o
    programados: inicio <= ahora <= fin_estimado. Con `tipo` se filtra a uno solo.

    Para el ciudadano lo que importa es si tiene agua, no si el corte estaba
    previsto: un corte programado ya iniciado lo deja sin servicio igual que uno
    imprevisto. Antes esos casos no aparecian en ningun conteo (no eran
    "imprevistas activas" ni "programadas por empezar") y la app respondia que no
    habia nada mientras la zona estaba sin agua."""
    ahora = _ahora()
    m = ((df["situacion"] == "SERVICIO INTERRUMPIDO")
         & (df["inicio"] <= ahora)
         & (ahora <= df["fin_estimado"]))
    if tipo:
        m &= (df["tipo"] == tipo)
    return df[m]


def _activas_ahora(df: pd.DataFrame) -> pd.DataFrame:
    """Imprevistas ocurriendo ahora."""
    return _en_curso(df, "IMPREVISTA")


def _programadas_en_curso(df: pd.DataFrame) -> pd.DataFrame:
    """Programadas que ya empezaron y aun no terminan."""
    return _en_curso(df, "PROGRAMADA")


def _programadas_resto_mes(df: pd.DataFrame) -> pd.DataFrame:
    """Programadas que aun NO empiezan, de aqui a fin del mes en curso."""
    ahora = _ahora()
    return df[(df["tipo"] == "PROGRAMADA")
              & (df["inicio"] >= ahora)
              & (df["inicio"] <= ahora.to_period("M").end_time)]


def _guardar(hits: pd.DataFrame, etiqueta: str, punto: dict | None = None) -> str:
    """Registra coincidencias y devuelve el resumen. Si no hubo, NO guarda nada:
    guardar un vacio haria que las tools de detalle digan "no hay cortes" cuando
    en realidad nunca se ubico a la persona."""
    if hits is None or hits.empty:
        _SESSION_DATA.pop("coincidencias", None)
        return _evidencia(f"{etiqueta}. No hay registros de interrupciones para esa ubicacion.")
    _SESSION_DATA["coincidencias"] = hits
    if punto:
        _SESSION_DATA["punto"] = punto
    # El primer numero es el que decide el semaforo de la app: cuenta TODO lo
    # que deja sin agua ahora, imprevisto o programado ya iniciado.
    n_impr = len(_activas_ahora(hits))
    n_curso = len(_programadas_en_curso(hits))
    n_prog = len(_programadas_resto_mes(hits))
    detalle = f"{n_impr} imprevista(s) y {n_curso} programada(s) ya iniciada(s)"
    return _evidencia(
        f"{etiqueta}. Hay {n_impr + n_curso} interrupcion(es) con el servicio cortado "
        f"ahora ({detalle}) y {n_prog} programada(s) por empezar en lo que resta del mes."
    )


def _resolver(texto: str, opciones: list[str]) -> str:
    """De mas estricto a mas permisivo: exacto normalizado -> contencion
    ("lurigancho" -> SAN JUAN DE LURIGANCHO) -> difflib 0.75 ("miraflorez").
    El umbral 0.75 corrige erratas sin confundir SJL con SJM."""
    t = _norm(texto)
    if not t:
        return ""
    if t in opciones:
        return t
    contiene = [o for o in opciones if t in o or o in t]
    if len(contiene) == 1:
        return contiene[0]
    aprox = difflib.get_close_matches(t, opciones, n=1, cutoff=0.75)
    return aprox[0] if aprox else ""


# --------------------------------------------------------------------------- #
# TOOLS (lo que ve el modelo)
# --------------------------------------------------------------------------- #

#   ubicar_por_coordenadas(lat, lon)
#       Cruce espacial point-in-polygon: convierte el GPS en un Point y hace
#       sjoin contra los poligonos de zonas afectadas. Filtro previo barato con
#       BBOX (un GPS de Cusco se rechaza sin tocar la base). Si el punto no cae
#       en ningun poligono, sugiere pasar a ubicar_por_distrito. Guarda el punto
#       siempre (para el mapa) y deduce el distrito por moda de los hits.

def ubicar_por_coordenadas(lat: float, lon: float) -> str:
    """
    Ubica al ciudadano por coordenadas GPS y carga las interrupciones de su zona.
    Llamar ANTES que las tools de detalle.

    Args:
        lat: Latitud, ej. -12.0464.
        lon: Longitud, ej. -77.0428.
    """
    # El punto se guarda siempre: es lo que el mapa usa para centrarse, aunque
    # el clic caiga en una zona sin interrupciones registradas.
    _SESSION_DATA["punto"] = {"lat": lat, "lon": lon, "etiqueta": ""}

    if not (BBOX["lat_min"] <= lat <= BBOX["lat_max"] and BBOX["lon_min"] <= lon <= BBOX["lon_max"]):
        return _evidencia(
            f"El punto ({lat}, {lon}) esta fuera de Lima y provincias. Pide al ciudadano su distrito."
        )



    gdf = cargar_base_espacial()
    punto = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs=gdf.crs)
    hits = gdf.sjoin(punto, predicate="contains").drop(columns="index_right", errors="ignore")
    if hits.empty:
        return _evidencia(
            f"El punto ({lat}, {lon}) no cae en ninguna zona con interrupciones registradas. "
            "Pide el distrito y usa ubicar_por_distrito."
        )
    distrito = hits["dist_norm"].mode().iat[0]
    return _guardar(hits, f"Ubicado en el distrito de {distrito}",
                    {"lat": lat, "lon": lon, "etiqueta": distrito})

#   ubicar_por_distrito(distrito, provincia="")
#       Busqueda por nombre con tolerancia a errores via _resolver():
#      } La provincia opcional desambigua homonimos (San Luis de
#       Lima vs San Luis de Canete). Incluye las filas SIN geometria
#      , que el cruce GPS no puede ver. El centroide de los poligonos del
#       distrito sirve de punto para centrar el mapa.


def ubicar_por_distrito(distrito: str, provincia: str = "") -> str:
    """
    Ubica al ciudadano por su distrito (tolera tildes y erratas) y carga las
    interrupciones. Usar cuando no comparte GPS.

    Args:
        distrito: Ej. "San Juan de Lurigancho".
        provincia: Opcional, solo si hay distritos homonimos.
    """
    gdf = cargar_base()
    if provincia:
        prov = _resolver(provincia, listar_provincias())
        if prov:
            gdf = gdf[gdf["prov_norm"] == prov]

    objetivo = _resolver(distrito, sorted(gdf["dist_norm"].dropna().unique()))
    if not objetivo:
        disponibles = ", ".join(listar_distritos()[:12])
        return _evidencia(
            f"No encontre el distrito '{distrito}'. Pide al ciudadano que lo confirme. "
            f"Algunos disponibles: {disponibles}..."
        )

    hits = gdf[gdf["dist_norm"] == objetivo].copy()
    punto = None
    con_geom = hits[hits.geometry.notna()]
    if not con_geom.empty:
        union = con_geom.geometry.union_all()
        # El centroide de una zona costera o con forma concava puede caer FUERA
        # de tierra (p. ej. Cerro Azul, con el marcador en el mar). Si el
        # centroide no queda dentro de la zona, se usa representative_point(),
        # que siempre devuelve un punto contenido en el poligono.
        c = union.centroid
        if not union.contains(c):
            c = union.representative_point()
        punto = {"lat": float(c.y), "lon": float(c.x), "etiqueta": objetivo}
    return _guardar(hits, f"Distrito {objetivo}", punto)

#   interrupciones_imprevistas()
#       Filtra las coincidencias con _activas_ahora(): tipo IMPREVISTA, situacion
#       SERVICIO INTERRUMPIDO, inicio <= ahora <= fin_estimado. Devuelve hasta 5
#       (las mas recientes primero) con zona, causa, inicio, restablecimiento
#       estimado y acciones de la EP.

def interrupciones_imprevistas() -> str:
    """
    Interrupciones con el servicio CORTADO AHORA MISMO en la zona ya ubicada,
    distinguiendo las imprevistas de las programadas que ya empezaron.
    Devuelve causa, inicio, restablecimiento estimado y acciones de la empresa.
    """
    if "coincidencias" not in _SESSION_DATA:
        return "Primero ubica al ciudadano con ubicar_por_coordenadas o ubicar_por_distrito."
    hits = _SESSION_DATA["coincidencias"]
    act, curso = _activas_ahora(hits), _programadas_en_curso(hits)
    if act.empty and curso.empty:
        return _evidencia("No hay ninguna interrupcion cortando el servicio en tu zona en este momento.")

    def _detalle(r, programada: bool) -> str:
        return (
            f"\n- Zona: {r['zona']} ({r['eps']})"
            f"\n  Tipo: {'PROGRAMADA, ya iniciada' if programada else 'IMPREVISTA'}"
            f"\n  Causa: {r['clasificacion']} - {r['motivo']}"
            f"\n  Inicio: {_fecha(r['inicio'])}"
            f"\n  Restablecimiento estimado: {_fecha(r['fin_estimado'])}"
            f"\n  Duracion estimada: {r['duracion']}"
            f"\n  Acciones de la EP: {r['acciones']}"
        )

    partes = []
    if not act.empty:
        partes.append(f"Hay {len(act)} interrupcion(es) imprevista(s) activa(s) ahora:")
        partes += [_detalle(r, False) for _, r in act.sort_values("inicio", ascending=False).head(5).iterrows()]
    if not curso.empty:
        if partes:
            partes.append("\n")
        partes.append(f"Ademas hay {len(curso)} corte(s) programado(s) EN CURSO ahora mismo:")
        partes += [_detalle(r, True) for _, r in curso.sort_values("inicio", ascending=False).head(5).iterrows()]
    return _evidencia("\n".join(partes))

#   interrupciones_programadas()
#       Filtra con _programadas_resto_mes(): tipo PROGRAMADA con inicio entre
#       ahora y fin de mes. Hasta 5, ordenadas por fecha (la mas proxima primero).

def interrupciones_programadas() -> str:
    """
    Interrupciones PROGRAMADAS de la zona ya ubicada: primero las que estan en
    curso ahora mismo y luego las que aun no empiezan, hasta fin de mes.
    Devuelve dia y hora de inicio, fin estimado y causa.
    """
    if "coincidencias" not in _SESSION_DATA:
        return "Primero ubica al ciudadano con ubicar_por_coordenadas o ubicar_por_distrito."
    hits = _SESSION_DATA["coincidencias"]
    curso, prog = _programadas_en_curso(hits), _programadas_resto_mes(hits)
    if curso.empty and prog.empty:
        return _evidencia("No hay interrupciones programadas en curso ni por empezar "
                          "en lo que resta del mes en tu zona.")

    def _detalle(r) -> str:
        return (
            f"\n- Zona: {r['zona']} ({r['eps']})"
            f"\n  Causa: {r['clasificacion']} - {r['motivo']}"
            f"\n  Inicio programado: {_fecha(r['inicio'])}"
            f"\n  Fin estimado: {_fecha(r['fin_estimado'])}"
            f"\n  Duracion estimada: {r['duracion']}"
        )

    partes = []
    if not curso.empty:
        partes.append(f"Hay {len(curso)} corte(s) programado(s) EN CURSO ahora mismo "
                      f"(el servicio ya esta cortado):")
        partes += [_detalle(r) for _, r in curso.sort_values("inicio").head(5).iterrows()]
    if not prog.empty:
        if partes:
            partes.append("\n")
        partes.append(f"Hay {len(prog)} interrupcion(es) programada(s) que aun no empiezan "
                      f"en lo que resta del mes:")
        partes += [_detalle(r) for _, r in prog.sort_values("inicio").head(5).iterrows()]
    return _evidencia("\n".join(partes))

#   verificar_cisternas()
#       Busca el patron "cistern" (regex, sin distinguir mayusculas) en los
#       campos de texto libre (acciones, detalle) de las interrupciones VIGENTES
#       (activas + programadas). Solo vigentes: mencionar cisternas de un corte
#       ya terminado confunde. Devuelve max 2 menciones, sin duplicados.

def verificar_cisternas() -> str:
    """
    Revisa si las interrupciones vigentes de la zona ya ubicada mencionan
    abastecimiento con camiones cisterna.
    """
    if "coincidencias" not in _SESSION_DATA:
        return "Primero ubica al ciudadano con ubicar_por_coordenadas o ubicar_por_distrito."
    df = _SESSION_DATA["coincidencias"]
    # _en_curso cubre imprevistas y programadas ya iniciadas; el otro filtro, las
    # programadas por empezar. Sin solaparse (inicio <= ahora vs inicio >= ahora).
    vigentes = pd.concat([_en_curso(df), _programadas_resto_mes(df)])
    if vigentes.empty:
        return _evidencia("No hay interrupciones vigentes en tu zona, no aplica cisternas.")

    patron = re.compile(r"cistern", re.IGNORECASE)
    menciones = [
        str(r.get(campo) or "").strip()
        for _, r in vigentes.iterrows()
        for campo in ("acciones", "detalle")
        if patron.search(str(r.get(campo) or ""))
    ]
    if not menciones:
        return _evidencia("No se menciona abastecimiento con cisternas en las interrupciones de tu zona.")
    unicas = list(dict.fromkeys(menciones))[:2]
    return _evidencia("Si hay abastecimiento con cisternas:\n" + "\n".join(f"- {t}" for t in unicas))

# CASO APARTE — resumen_general(tipo="ambas")
#       La unica que NO necesita ubicacion: trabaja sobre toda la base. Para
#       preguntas de panorama ("¿cuantas interrupciones hay?", "¿que distritos?").
#       Con tipo="activas"/"programadas"/"ambas" filtra que reportar; devuelve
#       conteos por distrito y las 3 programadas mas proximas.

DEPS_PANORAMA = {"LIMA", "CALLAO"}


def _ambito_lima(gdf):
    """Recorta al departamento de Lima y al Callao. La base nueva es NACIONAL,
    pero la app (y su panorama) son de Lima: sin este recorte el resumen mezcla
    Trujillo o Tacna. Las fuentes viejas no traen departamento y ya venian
    acotadas: se devuelven tal cual."""
    if "dep_norm" in gdf.columns and gdf["dep_norm"].ne("").any():
        return gdf[gdf["dep_norm"].isin(DEPS_PANORAMA)]
    return gdf


def resumen_general(tipo: str = "ambas") -> str:
    """
    Panorama de Lima y Callao (todas sus provincias); no necesita ubicacion.
    Usar para "cuantas interrupciones hay" o "que distritos estan afectados".

    Args:
        tipo: "activas", "programadas" o "ambas".
    """
    gdf = _ambito_lima(cargar_base())
    t = _norm(tipo)
    partes = []

    if t != "PROGRAMADAS":
        act = _activas_ahora(gdf)
        if act.empty:
            partes.append("Ahora mismo no hay interrupciones imprevistas activas en la base.")
        else:
            pd_ = act["dist_norm"].value_counts()
            partes.append(
                f"Ahora mismo hay {len(act)} interrupcion(es) imprevista(s) activa(s) en "
                f"{len(pd_)} distrito(s): " + ", ".join(f"{d} ({n})" for d, n in pd_.items()) + "."
            )
        # Los programados ya iniciados dejan sin agua igual: se reportan aparte
        # para no mezclarlos con los imprevistos.
        curso = _programadas_en_curso(gdf)
        if not curso.empty:
            pc = curso["dist_norm"].value_counts()
            partes.append(
                f"Ademas hay {len(curso)} corte(s) programado(s) EN CURSO en "
                f"{len(pc)} distrito(s): " + ", ".join(f"{d} ({n})" for d, n in pc.items()) + "."
            )

    if t != "ACTIVAS":
        prog = _programadas_resto_mes(gdf)
        if prog.empty:
            partes.append("No hay interrupciones programadas en lo que resta del mes.")
        else:
            pd_ = prog["dist_norm"].value_counts()
            proximas = prog.sort_values("inicio").head(3)
            fechas = "; ".join(f"{r['dist_norm']} el {_fecha(r['inicio'])}" for _, r in proximas.iterrows())
            partes.append(
                f"Hay {len(prog)} interrupcion(es) programada(s) que aun no empiezan en lo que "
                f"resta del mes en {len(pd_)} distrito(s): "
                + ", ".join(f"{d} ({n})" for d, n in pd_.items())
                + f". Las mas proximas: {fechas}."
            )
    return _evidencia(" ".join(partes))


# El mapa NO es tool: lo dibuja la app tras cada respuesta. Sale siempre y el
# modelo se ahorra una llamada (medio minuto en un LLM local).
TOOLS = [
    ubicar_por_coordenadas,
    ubicar_por_distrito,
    interrupciones_imprevistas,
    interrupciones_programadas,
    verificar_cisternas,
    resumen_general,
]


# --------------------------------------------------------------------------- #
# Mapa (lo llama la app, no el agente)
# --------------------------------------------------------------------------- #
def construir_mapa():
    """Rojo: imprevistas activas. Naranja: programadas del mes. Sin ubicacion,
    un mapa de Lima clicable para elegirla ahi mismo."""
    if "coincidencias" not in _SESSION_DATA:
        p = _SESSION_DATA.get("punto") or {}
        m = folium.Map(location=[p.get("lat", -12.05), p.get("lon", -77.05)],
                       zoom_start=13 if p else 10, tiles="cartodbpositron")
        if p.get("lat") is not None:
            folium.Marker([p["lat"], p["lon"]],
                          tooltip="Tu ubicacion (sin interrupciones registradas)",
                          icon=folium.Icon(color="gray", icon="tint", prefix="fa")).add_to(m)
        return m

    hits = _SESSION_DATA["coincidencias"]
    hits = hits[hits.geometry.notna()]
    punto = _SESSION_DATA.get("punto") or {}
    lat, lon = punto.get("lat", -12.0464), punto.get("lon", -77.0428)
    m = folium.Map(location=[lat, lon], zoom_start=13, tiles="cartodbpositron")

    # Tres capas: los dos primeros colores son cortes EN CURSO (el ciudadano no
    # tiene agua ahora); el tercero, lo que aun no empieza.
    for nombre, sub, color in [
        ("Imprevistas activas", _activas_ahora(hits), "#d7191c"),
        ("Programadas en curso", _programadas_en_curso(hits), "#e6550d"),
        ("Programadas por empezar", _programadas_resto_mes(hits), "#fdae61"),
    ]:
        if sub.empty:
            continue
        capa = folium.FeatureGroup(name=f"{nombre} ({len(sub)})")
        for _, r in sub.head(50).iterrows():
            folium.GeoJson(
                r.geometry,
                style_function=lambda _f, c=color: {"fillColor": c, "color": c,
                                                    "weight": 1, "fillOpacity": 0.45},
                tooltip=folium.Tooltip(
                    f"<b>{r['zona']}</b><br>{r['clasificacion']}<br>"
                    f"Inicio: {_fecha(r['inicio'])}<br>"
                    f"Restablecimiento: {_fecha(r['fin_estimado'])}", sticky=True),
            ).add_to(capa)
        capa.add_to(m)

    folium.Marker([lat, lon], tooltip=punto.get("etiqueta") or "Tu ubicacion",
                  icon=folium.Icon(color="blue", icon="tint", prefix="fa")).add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    return m


# --------------------------------------------------------------------------- #
# Diagnostico (no es tool)
# --------------------------------------------------------------------------- #
def diagnostico_datos() -> dict:
    """Salud de la base limpia. `zona_horaria_ok` sale de metadata.json, que lo
    escribio preparar_datos.py: si es False, hay que regenerar la base."""
    gdf = cargar_base()
    meta = metadata_base()
    # Los contadores Activas/Programadas se acotan a Lima y Callao, igual que
    # resumen_general: por diseno ambos numeros deben coincidir SIEMPRE.
    lima = _ambito_lima(gdf)
    return {
        "registros": len(gdf),
        "con_geometria": int(gdf.geometry.notna().sum()),
        "sin_geometria": int(gdf.geometry.isna().sum()),
        "provincias": int(gdf["prov_norm"].nunique()),
        "distritos": int(gdf["dist_norm"].nunique()),
        "ahora_lima": _ahora().strftime("%d/%m/%Y %H:%M"),
        # sin_agua_ahora = TODO lo que corta el servicio en este instante; el
        # desglose permite que el panel distinga imprevisto de programado.
        "sin_agua_ahora": len(_en_curso(lima)),
        "activas_ahora": len(_activas_ahora(lima)),
        "programadas_en_curso": len(_programadas_en_curso(lima)),
        "programadas_resto_mes": len(_programadas_resto_mes(lima)),
        "base_generada": meta.get("generado"),
        "archivo_origen": meta.get("archivo_origen"),
        "zona_horaria_ok": meta.get("zona_horaria_ok"),
    }
