"""
test_agente_local.py
--------------------
Prueba de extremo a extremo del GeoAgente contra el modelo LOCAL de LM Studio
(por defecto google/gemma-4-e2b). Este script SI usa el LLM: por eso hay que
correrlo en tu maquina, con LM Studio abierto y el servidor local encendido.

Requisitos previos (una sola vez):
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1        # PowerShell (Windows)
    pip install -r requirements.txt
    python setup_estructura.py          # crea data/ y skills/<name>/
    # En LM Studio: carga el modelo y enciende el server (Developer -> Start Server)

Luego:
    python test_agente_local.py

Qué hace, en orden:
  1. Verifica Python >= 3.11 (deepagents lo exige).
  2. Verifica la estructura data/ y skills/.
  3. Hace ping al endpoint de LM Studio y lista los modelos cargados.
  4. Construye el agente y lanza varias consultas reales, midiendo el tiempo.
  5. Pasa cada respuesta por los guardarrailes y reporta un veredicto.

No falla la app si el modelo tropieza: reporta el problema y sigue.
"""
from __future__ import annotations

import os
import sys
import time
import json
import urllib.request
import urllib.error
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
LINEA = "=" * 70


def paso(txt: str) -> None:
    print(f"\n{LINEA}\n{txt}\n{LINEA}")


# --------------------------------------------------------------------------- #
# 1. Python
# --------------------------------------------------------------------------- #
def check_python() -> bool:
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 11)
    print(f"Python {v.major}.{v.minor}.{v.micro}  ->  {'OK' if ok else 'FALLO: deepagents pide >= 3.11'}")
    return ok


# --------------------------------------------------------------------------- #
# 2. Estructura
# --------------------------------------------------------------------------- #
def check_estructura() -> bool:
    ok = True
    parquet = RAIZ / "data" / "interrupciones_limpio.parquet"
    if parquet.is_file():
        print(f"OK   data/interrupciones_limpio.parquet")
    else:
        print(f"FALLO  falta {parquet}. Corre: python setup_estructura.py")
        ok = False
    skills = list((RAIZ / "skills").glob("*/SKILL.md")) if (RAIZ / "skills").is_dir() else []
    if skills:
        print(f"OK   skill en {skills[0].relative_to(RAIZ)}")
    else:
        print("AVISO  no hay skills/<name>/SKILL.md; el agente correra SIN procedimiento.")
    return ok


# --------------------------------------------------------------------------- #
# 3. LM Studio
# --------------------------------------------------------------------------- #
def check_lmstudio() -> tuple[bool, str]:
    base = os.getenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
    modelo_cfg = os.getenv("LMSTUDIO_MODEL", "google/gemma-4-e2b")
    url = f"{base}/models"
    print(f"Endpoint: {url}")
    print(f"Modelo en .env: {modelo_cfg}")
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ids = [m.get("id") for m in data.get("data", [])]
        print(f"Modelos cargados en LM Studio: {ids or '(ninguno)'}")
        if not ids:
            print("AVISO  El server responde pero no hay modelos cargados. Carga uno en LM Studio.")
            return False, modelo_cfg
        if modelo_cfg not in ids:
            print(f"AVISO  '{modelo_cfg}' no esta entre los cargados. Usa uno de la lista o ajusta .env.")
        return True, modelo_cfg
    except urllib.error.URLError as e:
        print(f"FALLO  No pude conectar a LM Studio ({e}).")
        print("       Abre LM Studio -> pestana Developer -> Start Server (127.0.0.1:1234).")
        return False, modelo_cfg


# --------------------------------------------------------------------------- #
# 4-5. Consultas reales + guardarrailes
# --------------------------------------------------------------------------- #
CONSULTAS = [
    "Hola",                                                 # saludo, no debe llamar tools
    "Vivo en San Juan de Lurigancho, hay corte de agua ahora?",
    "Y hay cortes programados este mes?",                   # memoria: usa el distrito anterior
    "Hay cisternas?",                                       # memoria + cisternas
    "Cuantas interrupciones hay ahora en toda Lima?",       # resumen_general, sin ubicacion
    "Mi abuela es adulta mayor y no llega agua a Surco",    # caso vulnerable
]


def probar_agente() -> None:
    from agent import agent, texto_respuesta
    from guardrails import aplicar_guardarrailes
    from tools import clear_session_data, get_session_data

    thread = "test-local"
    for i, pregunta in enumerate(CONSULTAS, 1):
        print(f"\n--- Consulta {i}: {pregunta}")
        clear_session_data()
        t0 = time.time()
        try:
            resultado = agent.invoke(
                {"messages": [{"role": "user", "content": pregunta}]},
                config={"configurable": {"thread_id": thread}},
            )
            bruto = texto_respuesta(resultado["messages"][-1])
            rev = aplicar_guardarrailes(bruto, get_session_data())
            dt = time.time() - t0
            print(f"    tiempo: {dt:.1f}s | tools usadas: "
                  f"{len(get_session_data().get('evidencia', []))} | "
                  f"guardarrailes: {'sin observaciones' if rev.ok else rev.alertas}")
            print(f"    respuesta: {rev.texto[:400]}")
        except Exception as e:  # noqa: BLE001
            print(f"    ERROR en la consulta: {type(e).__name__}: {e}")


# --------------------------------------------------------------------------- #
def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(RAIZ / ".env")

    paso("1) Python")
    py_ok = check_python()

    paso("2) Estructura de carpetas")
    est_ok = check_estructura()

    paso("3) Conexion con LM Studio")
    lm_ok, _ = check_lmstudio()

    if not (py_ok and est_ok):
        print("\nCorrige lo anterior antes de probar el LLM.")
        return 1
    if not lm_ok:
        print("\nLM Studio no esta listo. El resto de la prueba (LLM) se omite.")
        return 1

    paso("4-5) Consultas reales al agente + guardarrailes")
    probar_agente()
    print(f"\n{LINEA}\nPrueba finalizada.\n{LINEA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
