# Reporte de pruebas — GeoAgente de interrupciones de agua (Lima)

**Fecha:** 31/07/2026 · **Modelo objetivo:** `google/gemma-4-e2b` (LM Studio local)

---

## Qué se hizo

1. **Copias de respaldo** de todos los archivos originales en
   `copias_backup_2026-07-31/` (código + parquets + metadata, sin modificar los originales).
2. **Prueba de los componentes deterministas** del agente (tools, extracción por
   reglas y guardarraíles) en un entorno aislado: **25/25 pruebas OK**.
3. **Scripts entregados** para que corras la prueba completa del LLM en tu máquina.

---

## Resultado de las pruebas deterministas (25/25 OK)

Estas son las partes que NO dependen del LLM — el grueso de la lógica del agente,
por diseño ("el agente corre lo justo necesario"):

| Bloque | Verificado | Resultado |
|---|---|---|
| Carga de datos | 8 166 registros, 6 940 con geometría, 63 distritos, 5 provincias | OK |
| Zona horaria | control Cerro Azul 06:00, `zona_horaria_ok=True` | OK |
| `ubicar_por_distrito` | nombre exacto + errata ("miraflorez"→MIRAFLORES) | OK |
| `ubicar_por_coordenadas` | rechaza Cusco (fuera BBOX), acepta Lima centro | OK |
| `interrupciones_imprevistas / _programadas` | filtran y responden | OK |
| `verificar_cisternas` | busca patrón "cistern" en vigentes | OK |
| `resumen_general` | panorama sin ubicación | OK |
| Orden de tools | detalle sin ubicar pide ubicación primero | OK |
| Extracción por reglas | clasifica saludo/actual/programado/cisternas/ubicación | OK |
| Caso vulnerable | detecta "adulta mayor" | OK |
| Guardarraíl 1 | bloquea afirmación sin evidencia | OK |
| Guardarraíl 2 | bloquea teléfono no oficial (987654321) | OK |
| Guardarraíl 3 | alerta fecha no verificada + nota | OK |
| Guardarraíl 4 | agrega cierre 1899; no bloquea el 1899 legítimo | OK |

Dato: al momento de la prueba había **1 interrupción imprevista activa real en
CERRO AZUL** y **5 programadas** en lo que resta del mes — coincide con el caso de
control del README.

---

## La prueba del LLM (gemma-4-e2b) NO se pudo ejecutar aquí — y por qué

El modelo corre en **tu LM Studio local** (`http://127.0.0.1:1234`). El entorno
sandbox donde trabajo está aislado y **no puede alcanzar el localhost de tu
máquina**, así que no hay forma de que llame a gemma-4-e2b desde aquí. Además,
`deepagents` exige **Python ≥ 3.11** y el sandbox solo tiene 3.10.

Por eso te dejé el script `test_agente_local.py`: hace exactamente esa prueba
(construir el agente + varias consultas reales al modelo + guardarraíles), pero
pensado para correr **en tu máquina**, donde sí está LM Studio.

---

## Hallazgos que conviene arreglar antes de correr la app

1. **Estructura de carpetas.** El código busca los datos en `data/` y la skill en
   `skills/<name>/`, pero en esta entrega están en la **raíz**. Tal como está, la
   app fallaría con `FileNotFoundError` y el agente correría **sin skill** (fallo
   silencioso). → **Solución: `python setup_estructura.py`** (crea `data/`,
   `skills/interrupciones-agua-lima/` y un `.env` por defecto, copiando sin borrar).

2. **Archivos referenciados que faltan** en la carpeta: `test_tools.py`,
   `demo_agente.ipynb`, `diagrama_arquitectura.py`, `.env.example`,
   `arquitectura.png`. No bloquean el agente, pero el README los menciona.

3. **Nombre del modelo.** Verifica que `google/gemma-4-e2b` sea **exactamente** el
   id que muestra LM Studio (cópialo de la lista de modelos cargados). El id debe
   coincidir carácter por carácter o la llamada falla. El modelo debe tener soporte
   de *tool calling*.

---

## Cómo correr la prueba completa en tu máquina

```powershell
cd C:\Users\51964\Documents\Interrupciones_geoagente

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python setup_estructura.py        # arregla data/ y skills/ y crea .env
python test_deterministico.py     # revalida la logica sin LLM (debe dar 25/25)

# En LM Studio: carga gemma-4-e2b y enciende el server (Developer -> Start Server)
python test_agente_local.py       # prueba real contra el LLM, con tiempos
```

`test_agente_local.py` verifica Python, la estructura, hace ping a LM Studio, lista
los modelos cargados y luego lanza 6 consultas reales (saludo, corte actual,
programados con memoria de contexto, cisternas, panorama de Lima y caso
vulnerable), pasando cada respuesta por los guardarraíles.

---

## Archivos nuevos entregados

- `copias_backup_2026-07-31/` — respaldo íntegro de los originales.
- `setup_estructura.py` — deja la carpeta con la estructura que el código espera.
- `test_deterministico.py` — banco de 25 pruebas sin LLM (ya ejecutado: 25/25 OK).
- `test_agente_local.py` — prueba de extremo a extremo con gemma-4-e2b en LM Studio.
- `REPORTE_PRUEBAS.md` — este documento.
