"""
setup_estructura.py
-------------------
Deja la carpeta con la estructura que el codigo REALMENTE espera.

tools.py busca:   ./data/interrupciones_limpio.parquet  y  ./data/metadata.json
agent.py busca:   ./skills/<name>/SKILL.md   (name = frontmatter de SKILL.md)

En esta entrega los archivos estan en la RAIZ, asi que la app fallaria con
FileNotFoundError y el agente correria sin skill (fallo silencioso). Este script
lo corrige copiando (no moviendo) a su sitio y crea un .env por defecto.

Uso:
    python setup_estructura.py
Idempotente: se puede correr varias veces sin romper nada.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

RAIZ = Path(__file__).resolve().parent


def _copiar(origen: Path, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    if origen.resolve() == destino.resolve():
        return
    shutil.copy2(origen, destino)
    print(f"  copiado: {origen.name} -> {destino.relative_to(RAIZ)}")


def preparar_data() -> None:
    print("[data/]")
    data = RAIZ / "data"
    parquet = RAIZ / "interrupciones_limpio.parquet"
    meta = RAIZ / "metadata.json"
    if (data / "interrupciones_limpio.parquet").is_file():
        print("  ya existe data/interrupciones_limpio.parquet")
    elif parquet.is_file():
        _copiar(parquet, data / "interrupciones_limpio.parquet")
    else:
        print("  [!] No encuentro interrupciones_limpio.parquet en la raiz.")
    if (data / "metadata.json").is_file():
        print("  ya existe data/metadata.json")
    elif meta.is_file():
        _copiar(meta, data / "metadata.json")
    else:
        print("  [!] No encuentro metadata.json en la raiz.")


def preparar_skill() -> None:
    print("[skills/]")
    skill_raiz = RAIZ / "SKILL.md"
    if not skill_raiz.is_file():
        # tal vez ya esta dentro de una subcarpeta
        existentes = list((RAIZ / "skills").glob("*/SKILL.md")) if (RAIZ / "skills").is_dir() else []
        if existentes:
            print(f"  ya existe {existentes[0].relative_to(RAIZ)}")
            return
        print("  [!] No encuentro SKILL.md")
        return
    texto = skill_raiz.read_text(encoding="utf-8")
    m = re.search(r"^name:\s*(.+)$", texto, re.MULTILINE)
    nombre = (m.group(1).strip() if m else "interrupciones-agua-lima")
    destino = RAIZ / "skills" / nombre / "SKILL.md"
    if destino.is_file():
        print(f"  ya existe skills/{nombre}/SKILL.md")
    else:
        _copiar(skill_raiz, destino)
        print(f"  (name del frontmatter: '{nombre}')")


def preparar_env() -> None:
    print("[.env]")
    env = RAIZ / ".env"
    if env.is_file():
        print("  ya existe .env (no lo toco)")
        return
    env.write_text(
        "LMSTUDIO_BASE_URL=http://127.0.0.1:1234/v1\n"
        "LMSTUDIO_MODEL=google/gemma-4-e2b\n"
        "LLM_TEMPERATURE=0\n",
        encoding="utf-8",
    )
    print("  creado .env por defecto (ajusta LMSTUDIO_MODEL al id EXACTO de LM Studio)")


if __name__ == "__main__":
    print("=" * 60)
    print("Preparando estructura del GeoAgente")
    print("=" * 60)
    preparar_data()
    preparar_skill()
    preparar_env()
    print("=" * 60)
    print("Listo. Ahora:")
    print("  python setup_estructura.py   (esto)")
    print("  python test_agente_local.py  (prueba el LLM gemma-4-e2b)")
    print("  streamlit run app.py         (la app completa)")
    print("=" * 60)
