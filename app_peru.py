"""
Punto de entrada del despliegue NACIONAL. Correr con:

    streamlit run app_peru.py

Existe por dos razones, las dos practicas:

1. Streamlit Cloud identifica cada app por repositorio + rama + archivo
   principal. Con los tres iguales no crea un despliegue nuevo: reabre el que
   ya hay. Este archivo da el tercer valor distinto y permite tener las dos
   apps -Lima y Peru- saliendo del mismo repositorio.

2. Fija el ambito en codigo, asi que el despliegue nacional no depende de que
   un secret se haya guardado bien. Un fallo menos que diagnosticar.

No duplica nada: ejecuta app.py tal cual, que sigue siendo la unica app.
"""

import os
import runpy
from pathlib import Path

# Antes de que app.py (y tools.py) lean la variable al importarse.
os.environ["AMBITO"] = "nacional"

runpy.run_path(str(Path(__file__).resolve().parent / "app.py"), run_name="__main__")
