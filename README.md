# GeoAgente de interrupciones de agua — Lima (versión ajustada)

Asistente conversacional que le dice a un ciudadano si hay corte de agua en su zona,
cuándo vuelve el servicio y si hay abastecimiento con cisternas. Obtiene su ubicación
del navegador, por desplegable de distrito o con un clic en el mapa.

**Proyecto final · GeoAgents**

![Arquitectura](arquitectura.png)

---

## El problema

Cuando a una familia de Lima se le corta el agua, la información existe pero es
inutilizable: el prestador la publica en un visor geoespacial con capas, filtros y
lenguaje técnico. La persona quiere saber tres cosas —**¿hay corte?**, **¿cuándo
vuelve?**, **¿hay cisterna?**— y para llegar a ellas tiene que interpretar un mapa que
no fue diseñado para ella.

El aporte de la IA generativa no es el análisis espacial: eso ya lo hace GeoPandas. Es
traducir el resultado de un `sjoin` a una frase que una persona entiende, y sostener la
conversación que sigue.

## Principio de diseño: el agente corre lo justo necesario

Todo lo que puede ser determinista, lo es — y todo lo que puede hacerse UNA sola vez,
se hace fuera de la app. El LLM solo conversa y redacta.

| Tarea | Quién la hace | Cuándo |
|---|---|---|
| Limpiar la base (UTC→Lima, nombres, geometrías) | `preparar_datos.py` | **Antes** de arrancar, una vez |
| Obtener la ubicación | La app (GPS, desplegable o clic en mapa) | Cada sesión |
| Cruce espacial | La app, llamando la tool directamente | Cada consulta |
| Filtrar activas / programadas | `tools.py`, con pandas | Cada consulta |
| Dibujar el mapa | La app, con folium | Cada respuesta |
| Validar la respuesta | `guardrails.py`, con reglas | Cada respuesta |
| **Conversar y redactar** | **El agente (gemma-4-e2b en LM Studio)** | Cada turno |

Cuando la base se vuelva dinámica, un script `.bat` (pendiente) descargará el crudo a
`data_raw/` y llamará a `preparar_datos.py` antes de arrancar la app. **El agente nunca
ejecuta ese pipeline**: solo lee el parquet limpio.

---

## Puesta en marcha

Requiere **Python 3.12**.

```powershell
cd proyecto_interrupciones_ajustado

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env
```

### 1. Preparar la base (fuera del agente)

```powershell
python preparar_datos.py                  # toma el crudo más reciente de data_raw/
python preparar_datos.py --utc            # si el crudo viene directo de ArcGIS (fechas UTC)
python preparar_datos.py --entrada x.gpkg # o un archivo concreto
```

Genera `data/interrupciones_limpio.parquet` y `data/metadata.json` (cuándo y de qué
archivo se generó, y el control de zona horaria: el corte de Cerro Azul del 17/07/2026
debe leerse 06:00 → 18:00). La app muestra esa metadata en la barra lateral y **se
niega a arrancar si la base limpia no existe**, con la instrucción exacta.

### 2. LM Studio (el modelo corre en tu máquina, sin API key)

1. Abre **LM Studio**, carga un modelo con soporte de *tool calling* y enciende el
   servidor local (queda en `http://127.0.0.1:1234`).
2. Copia el identificador exacto del modelo en el `.env`:

```
LMSTUDIO_MODEL=google/gemma-4-e2b
```

Con 4 GB de VRAM conviene: **Context Length 32768** (el andamiaje de `deepagents`
ocupa ~6 000 tokens por sí solo) y **Max Concurrent Predictions en 1**.

### 3. Correr

```powershell
streamlit run app.py           # la app completa
python agent.py                # una consulta por consola
python test_tools.py           # banco de pruebas de velocidad del modelo
jupyter lab demo_agente.ipynb  # el recorrido paso a paso
```

Diagnóstico rápido de los datos:

```powershell
python -c "import tools, json; print(json.dumps(tools.diagnostico_datos(), indent=2))"
```

---

## Los cinco bloques del agente (rúbrica)

### 1. System prompt — `agent.py`

Define **qué** es el agente y sus límites duros: solo usa lo que devuelven las tools,
no asume distritos, cierra con el teléfono de atención. Corto a propósito: el
procedimiento vive en la skill y cada token se paga en cada llamada del modelo local.

### 2. Tools — `tools.py`

Seis tools en dos grupos. Las de ubicación dejan las filas en `_SESSION_DATA` y las de
detalle las leen; por eso el orden importa.

| Grupo | Tool | Qué hace |
|---|---|---|
| Ubicar | `ubicar_por_coordenadas` | Cruce espacial *point-in-polygon* con `sjoin` |
| Ubicar | `ubicar_por_distrito` | Por nombre, tolerando tildes y erratas (difflib 0.75) |
| Consultar | `interrupciones_imprevistas` | Cortes activos **en este instante** |
| Consultar | `interrupciones_programadas` | Cortes de aquí a fin de mes |
| Consultar | `verificar_cisternas` | Busca "cisterna" en el texto de la EP |
| Panorama | `resumen_general` | Toda Lima, sin necesitar ubicación |

El mapa (`construir_mapa`) **no es una tool**: lo llama la app. Así sale siempre y el
modelo se ahorra una llamada.

### 3. Skill — `skills/interrupciones-agua-lima/SKILL.md`

El procedimiento de atención: en qué orden llamar las tools, qué hacer cuando la
ubicación no cae en ninguna zona y cómo redactar. Ojo con la estructura: deepagents
busca `SKILL.md` dentro de cada **subcarpeta** cuyo nombre coincida con el `name` del
frontmatter; si se apunta mal no salta error y el agente improvisa. Por eso `agent.py`
incluye `verificar_skills()`.

### 4. Memoria y output estructurado

**Memoria** — `InMemorySaver` como checkpointer de LangGraph, un `thread_id` por sesión
de navegador: el ciudadano puede preguntar "¿y hay cisternas?" sin repetir dónde vive.
El botón *Nueva consulta* genera un `thread_id` nuevo.

**Output estructurado** — `extraction.py` convierte el texto libre en un
`ConsultaCiudadano` de Pydantic **antes** de que el agente actúe: de ahí sale el
distrito con el que la app pre-resuelve la ubicación y la marca de **caso vulnerable**
que dispara un guardarraíl. Si el modelo local falla al producir JSON, cae a extracción
por reglas sin tumbar la app.

### 5. Guardarraíles — `guardrails.py`

Código determinista que corre **siempre** sobre la respuesta, contrastando cada dato
sensible contra la **evidencia** (el texto exacto que devolvieron las tools del turno).

| # | Guardarraíl | Acción |
|---|---|---|
| 1 | Afirma algo del servicio sin haber consultado ninguna tool | **Bloquea** |
| 2 | Da un teléfono que no es el oficial ni sale del registro | **Bloquea** |
| 3 | Menciona fechas u horas ausentes en la evidencia | Alerta + nota |
| 4 | Falta el cierre, o no atendió un caso vulnerable | Corrige |

---

## La interfaz

Streamlit, porque la aplicación es un **chat con un mapa**. Tres zonas:

- **Panel de control (barra lateral, ancho fijo)** — paso 1 *¿Dónde estás?* con
  pestañas GPS / distrito, el estado del sistema (modelo, **cuándo se preparó la
  base**, conteos, hora de Lima), las opciones y el botón *Nueva consulta*.
- **Chat (mitad izquierda)** — paso 2 *Pregúntame*: historial en un contenedor
  desplazable de altura fija, **botones de consulta rápida** en cuadrícula 2×2
  (¿hay corte? / programados / cisternas / panorama de Lima), respuesta automática
  al ubicarse, y un monitor técnico plegable con la extracción, las tools
  ejecutadas y lo que corrigieron los guardarraíles.
- **Mapa (mitad derecha)** — clicable como tercera forma de ubicarse, con las
  zonas afectadas pintadas tras cada respuesta.

El chat y el mapa se reparten **a partes iguales** el ancho que deja la barra
lateral. Sobre ambos, una tarjeta de estado con semáforo: rojo si hay corte
activo, celeste si no, ámbar si la ubicación no se reconoció.

---

## Los datos

`data_raw/` guarda el crudo tal como llega de la fuente; `data/` lo que dejó el
pipeline. La base actual: 8 166 interrupciones de SEDAPAL y otras EPS, 63 distritos,
6 940 con polígono de zona afectada (las 1 226 sin polígono cuentan solo en consultas
por distrito).

Lo que resuelve `preparar_datos.py` (aprendido a base de errores):

- **Zona horaria.** ArcGIS entrega fechas en UTC: un corte de 06:00 a 18:00 se
  mostraba de 11:00 a 23:00. El flag `--utc` hace la conversión y `metadata.json`
  guarda el resultado del caso de control.
- **Nombres sucios.** `" LURIGANCHO"` junto a `"LURIGANCHO"`, `"CAÑETE"` junto a
  `"CANETE"`, `"LIM"` por `"LIMA"`. Sin normalizar, una consulta ve la mitad de sus
  registros.
- **Geometrías rotas.** `make_valid()` una sola vez, no en cada arranque.
- **Recorte.** De 41 columnas crudas a las 14 que el agente usa, con el nombre de
  `zona` (el campo más informativo entre localidad/sector) ya precalculado.

---

## Estructura

```
proyecto_interrupciones_ajustado/
├── preparar_datos.py           # pipeline de limpieza (FUERA del agente)
├── data_raw/                   # crudo de la fuente (aquí dejará archivos el futuro .bat)
├── data/
│   ├── interrupciones_limpio.parquet
│   └── metadata.json           # cuándo/de qué se generó + control de zona horaria
├── app.py                      # UI de Streamlit
├── agent.py                    # LM Studio + create_deep_agent + InMemorySaver
├── tools.py                    # las 6 tools + el mapa + el diagnóstico
├── extraction.py               # output estructurado (Pydantic)
├── guardrails.py               # los 4 guardarraíles
├── skills/
│   └── interrupciones-agua-lima/
│       └── SKILL.md            # el procedimiento de atención
├── demo_agente.ipynb           # recorrido paso a paso
├── test_tools.py               # banco de pruebas de velocidad
├── diagrama_arquitectura.py    # genera arquitectura.png
├── requirements.txt
└── .env.example
```

---

## Limitaciones conocidas

- El cruce fino es a nivel de **zona afectada**, no de dirección exacta.
- Los registros **sin geometría** solo aparecen en consultas por distrito.
- La base es una **foto**: el dinamismo llegará con el `.bat` que alimente `data_raw/`
  y llame a `preparar_datos.py`.
- La memoria es `InMemorySaver`: se pierde al reiniciar. Para producción, un
  checkpointer persistente (SQLite o Postgres).
- El agente no valida que la persona sea usuaria del servicio; solo informa cortes
  publicados.
