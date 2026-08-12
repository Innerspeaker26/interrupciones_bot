"""
Construccion del GeoAgente de interrupciones de agua.

Patron del curso (deepagents):

    modelo local (LM Studio) -> create_deep_agent(tools, system_prompt, skills,
                                                  checkpointer=InMemorySaver())

El agente hace lo MINIMO: conversa y redacta. Todo lo determinista vive fuera:
`preparar_datos.py` deja la base limpia ANTES de arrancar, la app resuelve la
ubicacion, `tools.py` filtra y dibuja, `guardrails.py` valida la respuesta. Asi
el resultado no depende de que un modelo local de 2B acierte a encadenar cinco
llamadas, y cambiar de modelo solo toca este archivo.

Los tres bloques de la rubrica:
  - MEMORIA: `InMemorySaver` (LangGraph guarda la conversacion por `thread_id`).
  - OUTPUT ESTRUCTURADO: `extraction.py` (Pydantic).
  - GUARDARRAILES: `guardrails.py`, sobre la respuesta final.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

from deepagents import create_deep_agent

from tools import TOOLS

load_dotenv()

SCRIPT_DIR = Path(__file__).resolve().parent

# `skills=[ruta]`: deepagents lista la carpeta y busca SKILL.md dentro de cada
# SUBCARPETA cuyo nombre coincida con el `name` del frontmatter. Si se apunta mal
# NO salta error: el agente simplemente se queda sin procedimiento. De ahi
# verificar_skills().
SKILLS_DIR = SCRIPT_DIR / "skills"

# --------------------------------------------------------------------------- #
# 1. Modelo: LM Studio (local) o Gemini (API), elegido con LLM_PROVIDER en .env
#
# Ambos hablan la API compatible con OpenAI, asi que el mismo ChatOpenAI sirve
# para los dos y el resto del agente no se entera del cambio.
#
# La clave de Gemini vive SOLO en el .env (GEMINI_API_KEY): nunca se escribe en
# el codigo ni se imprime. Con LM Studio la api_key es un placeholder (no la
# valida, pero el SDK falla si va vacia).
# temperature=0 no es opcional: con temperatura alta el modelo improvisa los
# argumentos de las tools.
# --------------------------------------------------------------------------- #
PROVIDER = os.getenv("LLM_PROVIDER", "lmstudio").strip().lower()
TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))

if PROVIDER == "gemini":
    BASE_URL = os.getenv(
        "GEMINI_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
    )
    MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    _api_key = os.getenv("GEMINI_API_KEY", "")
    if not _api_key:
        raise RuntimeError(
            "LLM_PROVIDER=gemini pero falta GEMINI_API_KEY en el .env. "
            "Abre el .env y agrega la linea:  GEMINI_API_KEY=tu_clave  "
            "(sin comillas ni espacios)."
        )
elif PROVIDER == "lmstudio":
    BASE_URL = os.getenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
    MODEL_NAME = os.getenv("LMSTUDIO_MODEL", "google/gemma-4-e2b")
    _api_key = "lm-studio"
else:
    raise RuntimeError(
        f"LLM_PROVIDER='{PROVIDER}' no reconocido: usa 'lmstudio' o 'gemini'."
    )

local_llm = ChatOpenAI(
    base_url=BASE_URL,
    api_key=_api_key,
    model=MODEL_NAME,
    temperature=TEMPERATURE,
)


# --------------------------------------------------------------------------- #
# 2. System prompt
#
# Corto a proposito: define QUE es el agente y sus limites duros; el COMO vive
# en la skill. Cada token de aqui se paga en cada llamada del modelo local.
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """Eres el asistente de una empresa de agua potable de Lima. Atiendes por
chat a ciudadanos que quieren saber si hay corte de agua en su zona. Espanol, tuteas,
respuestas cortas.

Para consultas de cortes, sigue la skill 'interrupciones-agua-lima'.

Si el mensaje trae una linea [UBICACION ...], el ciudadano YA esta ubicado: no llames
ninguna tool de ubicacion, pasa directo a interrupciones_imprevistas() e
interrupciones_programadas().

Si no la trae y el ciudadano no dice donde vive, pidele su distrito. No lo asumas.

Si pregunta por el panorama de toda la ciudad y no por su zona ("¿cuantas interrupciones
hay ahora?", "¿en que distritos habra cortes?"), usa resumen_general(tipo). Esa no
necesita ubicacion.

Usa UNICAMENTE lo que devuelven las tools. No inventes horarios, fechas, causas ni
telefonos. Si saluda o pregunta algo fuera de tema, contesta en una linea sin llamar
tools. Cierra con "Para mas informacion, comunicate al 1899." cuando des informacion
del servicio.
"""


# --------------------------------------------------------------------------- #
# 3. El agente
# --------------------------------------------------------------------------- #
def verificar_skills() -> list[str]:
    """Skills que deepagents va a cargar de verdad. Vacia = el agente correra
    sin procedimiento (fallo silencioso: no hay excepcion, solo improvisacion)."""
    encontradas = []
    if not SKILLS_DIR.is_dir():
        return encontradas
    for sub in sorted(SKILLS_DIR.iterdir()):
        skill_md = sub / "SKILL.md"
        if not (sub.is_dir() and skill_md.is_file()):
            continue
        m = re.search(r"^name:\s*(.+)$", skill_md.read_text(encoding="utf-8"), re.MULTILINE)
        nombre = (m.group(1).strip() if m else "") or sub.name
        if nombre != sub.name:
            print(f"[aviso] La skill '{nombre}' esta en la carpeta '{sub.name}'; deben coincidir.")
        encontradas.append(nombre)
    return encontradas


def crear_agente(model=None, checkpointer=None):
    """Construye el agente. Se puede inyectar otro modelo (util para probar)."""
    if not verificar_skills():
        print(f"[aviso] Sin skills en {SKILLS_DIR}. Estructura: skills/<nombre>/SKILL.md")
    return create_deep_agent(
        model=model or local_llm,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
        skills=[str(SKILLS_DIR)],
        checkpointer=checkpointer or InMemorySaver(),  # <- la memoria
    )


agent = crear_agente()


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def texto_respuesta(mensaje) -> str:
    """Texto del mensaje. Unos modelos devuelven str y otros lista de bloques."""
    c = getattr(mensaje, "content", mensaje)
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c if isinstance(p, dict)).strip()
    return (c or "").strip()


def preguntar(pregunta: str, thread_id: str = "cli") -> str:
    """Atajo para consola y notebook: una consulta, una respuesta ya validada."""
    from guardrails import aplicar_guardarrailes
    from tools import clear_session_data, get_session_data

    clear_session_data()
    resultado = agent.invoke(
        {"messages": [{"role": "user", "content": pregunta}]},
        config={"configurable": {"thread_id": thread_id}},
    )
    bruto = texto_respuesta(resultado["messages"][-1])
    return aplicar_guardarrailes(bruto, get_session_data()).texto


if __name__ == "__main__":
    import sys

    consulta = " ".join(sys.argv[1:]) or "Vivo en Cerro Azul, hay corte de agua ahora?"
    print(f"\n> {consulta}\n")
    print(preguntar(consulta))
