"""
Output estructurado: convierte el texto libre del ciudadano en un objeto tipado.

Mismo patron que deepagents/clase3/extraction.py: un modelo Pydantic +
`llm.with_structured_output(...)`. Aqui cumple dos funciones:

  1. Da visibilidad: la app muestra en el panel monitor QUE entendio el sistema
     antes de que el agente empiece a llamar tools.
  2. Alimenta a los guardarrailes: si el ciudadano marca un caso vulnerable
     (hospital, adulto mayor, bebe), la respuesta debe incluir la via de contacto
     prioritaria; eso se verifica despues contra este objeto.

Detalle importante con LM Studio: los modelos locales pequenos fallan a menudo al
producir JSON valido para `with_structured_output`. Por eso `extract_query` captura
la excepcion y cae a una extraccion por reglas. Nunca tumba la app: en el peor caso
devuelve el objeto con `metodo="fallback"`.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field


class ConsultaCiudadano(BaseModel):
    """Representacion estructurada de lo que pide el ciudadano en texto libre."""

    tipo_consulta: Literal[
        "corte_actual", "corte_programado", "cisternas", "ubicacion", "saludo", "otro"
    ] = Field(description="Que esta pidiendo el ciudadano")
    distrito: str | None = Field(
        default=None, description="Distrito de Lima mencionado, si aparece alguno"
    )
    direccion: str | None = Field(
        default=None,
        description="Direccion, urbanizacion o referencia mencionada, si aparece alguna",
    )
    lat: float | None = Field(default=None, description="Latitud, si el ciudadano la escribio")
    lon: float | None = Field(default=None, description="Longitud, si el ciudadano la escribio")
    caso_vulnerable: bool = Field(
        default=False,
        description=(
            "True si menciona personas o establecimientos sensibles: bebes, adultos "
            "mayores, enfermos, hospitales, postas, colegios"
        ),
    )
    metodo: str = Field(default="llm", description="Como se extrajo: 'llm' o 'fallback'")


# --------------------------------------------------------------------------- #
# Extraccion por reglas (respaldo)
# --------------------------------------------------------------------------- #
_RE_COORD = re.compile(
    r"(-?\d{1,2}\.\d{3,})\s*[, ]\s*(-?\d{1,3}\.\d{3,})"
)
_RE_LAT = re.compile(r"lat(?:itud)?\s*[:=]?\s*(-?\d{1,2}\.\d+)", re.IGNORECASE)
_RE_LON = re.compile(r"(?:lon|lng|longitud)\s*[:=]?\s*(-?\d{1,3}\.\d+)", re.IGNORECASE)
_RE_DIRECCION = re.compile(
    r"((?:av\.?|avenida|jr\.?|jiron|calle|urb\.?|urbanizacion|mz\.?|manzana|psje\.?|pasaje|"
    r"asentamiento|aa\.?hh\.?|cerca (?:de|al))[^,.;]{3,60})",
    re.IGNORECASE,
)

# Se usan expresiones regulares y no listas de palabras sueltas porque las
# variantes de genero y numero ("adulta mayor", "mis abuelos") se escapan de una
# comparacion literal, y un falso negativo aqui significa no darle atencion
# prioritaria a quien la necesita.
_RE_VULNERABLE = re.compile(
    r"\b(bebe|bebes|recien nacid[oa]|adult[oa]s? mayor(?:es)?|ancian[oa]s?|abuel[oa]s?|"
    r"enferm[oa]s?|hospital|clinica|posta|colegio|dialisis|discapacidad|gestante|embarazada|"
    r"tercera edad|oxigeno)\b"
)
_RE_CISTERNA = re.compile(r"\b(cistern\w*|camion(?:es)?|reparto de agua|agua en tanque)\b")
_RE_PROGRAMADO = re.compile(
    r"(programad\w*|proxim\w*|esta semana|este mes|resto del mes|va a haber|habra|"
    r"van a cortar|cortaran)"
)
_RE_ACTUAL = re.compile(
    r"(ahora|hoy|actual\w*|no (?:tengo|tenemos|hay) agua|sin agua|se fue el agua|"
    r"me quede sin|cort[eo]s?\b|no llega el agua|baja presion)"
)


def _sin_tildes(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _distritos_conocidos() -> list[str]:
    """Distritos de la base, normalizados, ordenados del nombre mas largo al mas corto.

    El orden importa: "San Juan de Lurigancho" debe probarse antes que "San Juan
    de Miraflores", y ambos antes que cualquier substring corto.
    """
    try:
        from tools import listar_distritos

        return sorted((_sin_tildes(d) for d in listar_distritos()), key=len, reverse=True)
    except Exception:  # noqa: BLE001
        return []


def extraccion_por_reglas(texto: str) -> ConsultaCiudadano:
    """Extraccion determinista, sin LLM. Es el respaldo y tambien el modo offline."""
    t = _sin_tildes(texto)

    lat = lon = None
    m = _RE_LAT.search(texto)
    if m:
        lat = float(m.group(1))
    m = _RE_LON.search(texto)
    if m:
        lon = float(m.group(1))
    if lat is None or lon is None:
        m = _RE_COORD.search(texto)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))

    distrito = None
    for d in _distritos_conocidos():
        if d and d in t:
            distrito = d.upper()
            break

    direccion = None
    m = _RE_DIRECCION.search(texto)
    if m:
        direccion = m.group(1).strip()

    if _RE_CISTERNA.search(t):
        tipo = "cisternas"
    elif _RE_PROGRAMADO.search(t):
        tipo = "corte_programado"
    elif _RE_ACTUAL.search(t):
        tipo = "corte_actual"
    elif lat is not None or distrito or direccion:
        tipo = "ubicacion"
    elif len(t.split()) <= 4:
        tipo = "saludo"
    else:
        tipo = "otro"

    return ConsultaCiudadano(
        tipo_consulta=tipo,
        distrito=distrito,
        direccion=direccion,
        lat=lat,
        lon=lon,
        caso_vulnerable=bool(_RE_VULNERABLE.search(t)),
        metodo="fallback",
    )


# --------------------------------------------------------------------------- #
# Extraccion con el LLM
# --------------------------------------------------------------------------- #
_PROMPT = (
    "Extrae la consulta estructurada del siguiente mensaje de un ciudadano que "
    "pregunta por cortes de agua en Lima. Si un campo no aparece en el mensaje, "
    "dejalo vacio. No inventes ubicaciones.\n\nMensaje:\n{texto}"
)

_extractor = None


def _get_extractor():
    global _extractor
    if _extractor is None:
        from agent import local_llm

        _extractor = local_llm.with_structured_output(ConsultaCiudadano)
    return _extractor


def extract_query(texto: str, usar_llm: bool = True) -> ConsultaCiudadano:
    """
    Extrae la consulta estructurada del texto libre.

    Intenta primero con el LLM. Si el modelo local no devuelve JSON valido (cosa
    frecuente con modelos pequenos) cae a la extraccion por reglas en vez de
    romper la app.
    """
    if not usar_llm:
        return extraccion_por_reglas(texto)
    try:
        salida = _get_extractor().invoke(_PROMPT.format(texto=texto))
        if isinstance(salida, ConsultaCiudadano):
            salida.metodo = "llm"
            # Si el modelo no detecto el caso vulnerable, la regla lo corrige:
            # es un falso negativo caro y la deteccion por palabras es fiable.
            if not salida.caso_vulnerable:
                salida.caso_vulnerable = extraccion_por_reglas(texto).caso_vulnerable
            return salida
        return ConsultaCiudadano(**{**dict(salida), "metodo": "llm"})
    except Exception:  # noqa: BLE001
        return extraccion_por_reglas(texto)
