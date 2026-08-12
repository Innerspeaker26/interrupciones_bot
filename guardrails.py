"""
Guardarrailes: revisan y corrigen la respuesta del agente ANTES de mostrarsela
al ciudadano.

Se apoyan en la "evidencia": el texto exacto que devolvieron las tools durante el
turno, que `tools.py` va acumulando en `_SESSION_DATA["evidencia"]`. Todo dato
sensible de la respuesta se contrasta contra esa evidencia.

Cuatro reglas, de la mas severa a la mas leve:

  1. BLOQUEO   - responder sobre cortes sin haber consultado ninguna tool.
  2. BLOQUEO   - dar un telefono que no es el de atencion oficial.
  3. ALERTA    - fechas u horas que no aparecen en el resultado de ninguna tool.
  4. CORRECCION- falta el cierre con el telefono, o no se atendio un caso vulnerable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TELEFONO_OFICIAL = "1899"
CIERRE_OFICIAL = f"Para mas informacion, comunicate al {TELEFONO_OFICIAL}."

MENSAJE_SIN_EVIDENCIA = (
    "Para decirte si hay corte de agua necesito saber donde estas. "
    "Indicame tu direccion, tu urbanizacion o el nombre de tu distrito y lo reviso.\n\n"
    + CIERRE_OFICIAL
)

MENSAJE_TELEFONO_INVENTADO = (
    "Revise la informacion de tu zona, pero preferi no mostrarte la respuesta porque "
    "incluia un numero de contacto que no corresponde al canal oficial. "
    "Vuelve a preguntarme y lo reviso de nuevo.\n\n" + CIERRE_OFICIAL
)

NOTA_VULNERABLE = (
    "\n\nComo mencionas a una persona o establecimiento en situacion vulnerable, "
    f"llama al {TELEFONO_OFICIAL} e indicalo al inicio de la llamada para que tu caso "
    "se atienda de forma prioritaria."
)

NOTA_FECHAS = (
    "\n\n_Nota del sistema: algunas fechas u horas de esta respuesta no pudieron "
    "verificarse contra el registro de interrupciones. Confirmalas al "
    f"{TELEFONO_OFICIAL} antes de tomar una decision._"
)


@dataclass
class RevisionGuardarrailes:
    """Resultado de pasar una respuesta por los guardarrailes."""

    texto: str
    alertas: list[str] = field(default_factory=list)
    bloqueado: bool = False

    @property
    def ok(self) -> bool:
        return not self.alertas and not self.bloqueado

    def resumen(self) -> list[dict]:
        """Formato de tabla para el panel monitor de la app."""
        if self.ok:
            return [{"guardarrail": "todos", "resultado": "sin observaciones"}]
        filas = [{"guardarrail": a.split(":", 1)[0], "resultado": a.split(":", 1)[-1].strip()}
                 for a in self.alertas]
        if self.bloqueado:
            filas.append({"guardarrail": "accion", "resultado": "respuesta bloqueada y reemplazada"})
        return filas


# --------------------------------------------------------------------------- #
# Patrones
# --------------------------------------------------------------------------- #
# Fechas dd/mm/aaaa y horas HH:MM tal como las emiten las tools.
_RE_FECHA = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
_RE_HORA = re.compile(r"\b\d{1,2}:\d{2}\b")

# Telefonos peruanos plausibles: 3 o 4 digitos cortos (1899, 1808), fijos de Lima
# (7 digitos) y celulares (9 digitos), con o sin separadores.
_RE_TELEFONO = re.compile(
    r"(?<!\d)(?:\+?51[\s-]?)?(?:\d{3}[\s-]?\d{4}|\d{3}[\s-]?\d{3}[\s-]?\d{3}|\b1\d{3}\b)(?!\d)"
)

# El agente AFIRMA un hecho sobre el servicio.
#
#
# Se prefiere que se escape alguna afirmacion rara antes que bloquear conversacion
# legitima: los guardarrailes 2 y 3 siguen cubriendo telefonos y fechas inventadas.
_RE_AFIRMA_ESTADO = re.compile(
    r"("
    r"\b(no|s[ií])\b[\s,]+hay\s+(un[ao]?\s+|\d+\s+|ningun[ao]?\s+)?(interrupci|corte|cistern)"
    r"|\bhay\s+\d+\s*(interrupci|corte)"
    r"|\b(tu|su)\s+zona\s+(no\s+)?(esta|tiene|se\s+encuentra)\s+afectad"
    r"|restablec\w*\s+(estimad|previst)"
    r"|\bel\s+servicio\s+(se\s+)?(restablec|vuelve|volver|retorna)"
    r"|\bvuelve\s+(el\s+)?(agua|servicio)"
    r"|\d{1,2}:\d{2}"                       # una hora concreta
    r"|\d{1,2}/\d{1,2}/\d{2,4}"             # una fecha concreta
    r")",
    re.IGNORECASE,
)


def _solo_digitos(t: str) -> str:
    return re.sub(r"\D", "", t)


# --------------------------------------------------------------------------- #
# Guardarrail 1: no responder sobre el servicio sin haber consultado las tools
# --------------------------------------------------------------------------- #
def _sin_evidencia(texto: str, evidencia: list[str]) -> bool:
    """True si el agente afirma algo sobre el servicio sin haber llamado una tool.

    Saludar, pedir la ubicacion o responder algo fuera de tema NO cuenta como
    afirmar: esas respuestas pasan sin tocar.
    """
    return bool(_RE_AFIRMA_ESTADO.search(texto)) and not evidencia


# --------------------------------------------------------------------------- #
# Guardarrail 2: telefonos que no son el canal oficial
# --------------------------------------------------------------------------- #
def _telefonos_inventados(texto: str, evidencia_txt: str) -> list[str]:
    """Numeros de telefono en la respuesta que no son el oficial ni salen de una tool."""
    sospechosos = []
    for bruto in _RE_TELEFONO.findall(texto):
        num = _solo_digitos(bruto)
        if num.endswith(TELEFONO_OFICIAL) or num == TELEFONO_OFICIAL:
            continue
        if num and num in _solo_digitos(evidencia_txt):
            continue  # el numero si aparece en el registro, es legitimo
        sospechosos.append(bruto.strip())
    return sospechosos


# --------------------------------------------------------------------------- #
# Guardarrail 3: fechas y horas que no salen de ninguna tool
# --------------------------------------------------------------------------- #
def _fechas_no_verificadas(texto: str, evidencia_txt: str) -> list[str]:
    """Fechas dd/mm/aaaa y horas HH:MM de la respuesta ausentes en la evidencia.

    Solo mira formatos que las tools emiten literalmente. Si el modelo reescribe
    "27/07/2026 14:00" como "el 27 de julio a las 2 de la tarde", este chequeo no
    lo marca: se prefiere no alertar antes que alertar de mas.
    """
    faltantes = []
    for patron in (_RE_FECHA, _RE_HORA):
        for valor in patron.findall(texto):
            if valor not in evidencia_txt:
                faltantes.append(valor)
    return list(dict.fromkeys(faltantes))


# --------------------------------------------------------------------------- #
# Aplicacion
# --------------------------------------------------------------------------- #
def aplicar_guardarrailes(
    texto: str,
    session_data: dict | None = None,
    consulta=None,
) -> RevisionGuardarrailes:
    """
    Revisa la respuesta del agente y devuelve la version que se le muestra al
    ciudadano.

    Parameters
    ----------
    texto : str
        Respuesta cruda del modelo.
    session_data : dict, optional
        Estado de las tools del turno; de aqui sale la evidencia.
    consulta : ConsultaCiudadano, optional
        Extraccion estructurada del mensaje, para el chequeo de caso vulnerable.

    Returns
    -------
    RevisionGuardarrailes
        Texto final, lista de alertas y si hubo bloqueo.
    """
    session_data = session_data or {}
    evidencia: list[str] = session_data.get("evidencia", [])
    evidencia_txt = "\n".join(evidencia)
    rev = RevisionGuardarrailes(texto=(texto or "").strip())

    if not rev.texto:
        rev.alertas.append("respuesta vacia: el modelo no devolvio texto")
        rev.bloqueado = True
        rev.texto = MENSAJE_SIN_EVIDENCIA
        return rev

    # --- 1. Sin evidencia -> bloqueo ---------------------------------------- #
    if _sin_evidencia(rev.texto, evidencia):
        rev.alertas.append(
            "sin_evidencia: el agente afirmo algo sobre el servicio sin consultar ninguna tool"
        )
        rev.bloqueado = True
        rev.texto = MENSAJE_SIN_EVIDENCIA
        return rev

    # --- 2. Telefonos inventados -> bloqueo --------------------------------- #
    inventados = _telefonos_inventados(rev.texto, evidencia_txt)
    if inventados:
        rev.alertas.append(
            f"telefono_no_oficial: aparecieron numeros no verificados ({', '.join(inventados)})"
        )
        rev.bloqueado = True
        rev.texto = MENSAJE_TELEFONO_INVENTADO
        return rev

    # --- 3. Fechas no verificadas -> alerta + nota -------------------------- #
    faltantes = _fechas_no_verificadas(rev.texto, evidencia_txt)
    if faltantes:
        rev.alertas.append(
            f"fecha_no_verificada: {', '.join(faltantes[:5])} no aparece(n) en el registro"
        )
        rev.texto += NOTA_FECHAS

    # --- 4. Correcciones suaves --------------------------------------------- #
    if getattr(consulta, "caso_vulnerable", False) and "prioritaria" not in rev.texto.lower():
        rev.alertas.append("caso_vulnerable: se agrego la via de atencion prioritaria")
        rev.texto += NOTA_VULNERABLE

    # El cierre institucional se exige solo cuando el agente entrego informacion
    # del servicio. Pegarselo a un saludo o a una pregunta suelta ("¿en que distrito
    # vives?") suena a robot y no aporta nada.
    if evidencia and TELEFONO_OFICIAL not in rev.texto:
        rev.alertas.append("cierre_faltante: se agrego el telefono de atencion")
        rev.texto = rev.texto.rstrip() + "\n\n" + CIERRE_OFICIAL

    return rev
