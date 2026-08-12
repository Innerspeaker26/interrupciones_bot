# Guion del video — GeoAgente de interrupciones de agua (10 min)

> Leído a ritmo normal son ~9 min de narración; el minuto restante lo absorbe la
> demo en vivo. Cada bloque indica QUÉ MOSTRAR en pantalla mientras hablas.
> Los textos en cursiva son acotaciones, no se leen.

---

## [0:00 – 1:00] El problema y la solución

**Mostrar:** el visor geoespacial oficial de interrupciones (o una captura), y al lado tu app.

"Cuando a una familia de Lima se le corta el agua, la información existe: las
empresas prestadoras publican cada interrupción en un visor geoespacial. El
problema es que ese visor tiene capas, filtros y lenguaje técnico. La persona
solo quiere saber tres cosas: ¿hay corte en mi zona?, ¿cuándo vuelve el agua?,
¿y hay camiones cisterna?

Nuestro proyecto convierte ese visor técnico en una conversación. El ciudadano
comparte su ubicación o escribe su distrito, y un agente de IA le responde en
lenguaje simple, con datos reales de la base oficial: más de 8 000
interrupciones de SEDAPAL y otras EPS, en 63 distritos de Lima y provincias.

El aporte de la IA generativa aquí no es el análisis espacial — eso ya lo hace
GeoPandas. Es traducir el resultado de un cruce espacial a una frase que
cualquier persona entiende, y sostener la conversación que sigue."

---

## [1:00 – 2:15] La arquitectura: el agente hace lo mínimo

**Mostrar:** arquitectura.png o el diagrama del README.

"El principio de diseño de todo el proyecto es: el agente hace lo mínimo. Todo
lo que puede ser determinista, es código; el modelo de lenguaje solo conversa y
redacta.

¿Por qué? El modelo corre en local, con LM Studio: es gemma, un modelo de 2
mil millones de parámetros sobre una GPU de 4 gigas. Es gratis y privado, como
vimos en el curso, pero cada llamada cuesta decenas de segundos y un modelo
pequeño se equivoca si le pides encadenar cinco pasos.

Entonces repartimos el trabajo: un script de preparación limpia los datos ANTES
de que la app arranque. La app resuelve la ubicación con código, sin gastar
llamadas al modelo. Las tools filtran la base con pandas. Los guardarraíles
validan la respuesta con reglas. Y el agente recibe todo eso ya resuelto: su
único trabajo es decidir qué consultar y redactar la respuesta.

El sistema tiene los cinco bloques que pide el proyecto: system prompt, tools,
skill, memoria con output estructurado, y guardarraíles — más una interfaz en
Streamlit."

---

## [2:15 – 3:15] Los datos: pipeline fuera del agente

**Mostrar:** terminal corriendo `python preparar_datos.py` y el metadata.json resultante.

"Empecemos por los datos, porque aquí hubo trabajo sucio. La fuente entrega las
fechas en UTC: un corte publicado de 6 de la mañana a 6 de la tarde se mostraba
de 11 a 11. Trae nombres sucios — Lurigancho con espacio adelante, Cañete con y
sin eñe — y sin normalizar, una consulta ve la mitad de sus registros. Y un 15%
de los registros viene sin polígono de zona afectada.

Todo eso lo resuelve preparar_datos.py, un script que corre FUERA del agente,
una sola vez: corrige la zona horaria, normaliza nombres, repara geometrías,
recorta de 41 columnas a las 18 útiles y deja un parquet limpio más un archivo
de metadata que registra cuándo se generó la base y un caso de control de zona
horaria. Si la base no pasa el control, el script lo alerta; y si la app
arranca sin base limpia, se niega a iniciar y te dice el comando exacto.

Cuando la base se vuelva dinámica, un script de actualización solo tendrá que
descargar el crudo y llamar a este pipeline. El agente no se toca."

---

## [3:15 – 4:15] System prompt y tools

**Mostrar:** agent.py (el SYSTEM_PROMPT) y luego tools.py (la lista TOOLS).

"El system prompt define qué es el agente y sus límites duros: es el asistente
de una empresa de agua de Lima, tutea, responde corto, usa únicamente lo que
devuelven las tools, y nunca asume el distrito del ciudadano. Es corto a
propósito: cada token del prompt se paga en cada llamada de un modelo local.

Las tools son seis funciones de Python en dos grupos. Dos de ubicación: por
coordenadas — que hace el cruce espacial punto-en-polígono con GeoPandas — y
por distrito, que tolera tildes y errores de escritura: si escribes
'miraflorez' con zeta, un algoritmo de similitud lo corrige. Tres de consulta:
interrupciones imprevistas activas en este momento, programadas hasta fin de
mes, y verificación de cisternas. Y una de panorama general para preguntas
sobre toda la ciudad.

Los dos grupos comparten estado: las de ubicar dejan las filas de tu zona en un
diccionario de sesión, y las de detalle las leen — el mismo patrón de estado
compartido que vimos en el curso con el análisis de incendios."

---

## [4:15 – 5:00] La skill

**Mostrar:** skills/interrupciones-agua-lima/SKILL.md.

"La skill es la lógica humana que orquesta esas tools: un archivo SKILL.md que
el agente carga bajo demanda, no en cada turno — eso ahorra tokens y mantiene
la calidad del prompt.

Define el procedimiento de atención: primero ubicar — y si la app ya ubicó al
ciudadano, no volver a hacerlo—; después consultar imprevistas y programadas; y
cisternas solo si alguna de las anteriores devolvió algo. También fija cómo
responder: lo primero es la hora estimada de restablecimiento, que es lo que la
persona realmente quiere saber. Y una regla de oro: un dato que no devolvió una
tool, no existe."

---

## [5:00 – 6:15] Memoria, output estructurado y guardarraíles

**Mostrar:** el monitor del agente en la app (extracción JSON + tabla de guardarraíles).

"El proyecto incorpora los tres elementos de calidad del curso, no solo uno.

Memoria: un checkpointer InMemorySaver de LangGraph, con un thread por sesión.
El ciudadano puede preguntar '¿y hay cisternas?' sin repetir dónde vive, porque
el agente recuerda la conversación.

Output estructurado: antes de invocar al agente, un modelo Pydantic convierte
el texto libre en un objeto tipado: qué pide, qué distrito menciona, y algo
importante: si menciona un caso vulnerable — un bebé, un adulto mayor, un
hospital. Como el modelo local a veces falla generando JSON, hay una extracción
por reglas de respaldo: la app nunca se cae por eso.

Y los guardarraíles: cuatro reglas deterministas que revisan la respuesta del
modelo ANTES de mostrarla, comparándola contra la 'evidencia' — el texto exacto
que devolvieron las tools en ese turno. Si el modelo afirma algo del servicio
sin haber consultado ninguna tool, se bloquea la respuesta. Si da un teléfono
que no es el oficial, se bloquea. Si menciona una fecha que no salió de la
base, se agrega una nota de advertencia. Y si falta el cierre institucional o
no se atendió el caso vulnerable, se corrige. El system prompt le PIDE al
modelo no inventar; los guardarraíles lo GARANTIZAN con código."

---

## [6:15 – 9:00] Demo en vivo

**Mostrar:** la app corriendo. *Ensayar esta secuencia antes de grabar.*

"Veamos la aplicación funcionando. La interfaz tiene dos pasos.

*[Sidebar]* En la barra lateral está el estado del sistema: el modelo cargado,
cuándo se preparó la base, y cuántas interrupciones hay activas y programadas
en este momento.

*[Paso 1: elegir un distrito CON corte activo — verificar antes cuál tiene]*
Primero, la ubicación: puedo compartir mi GPS, elegir mi distrito, o hacer clic
directamente en el mapa. Elijo Cerro Azul... y sin escribir nada, el agente
responde solo: hay una interrupción imprevista activa, me dice la causa, desde
cuándo, y la hora estimada de restablecimiento. La tarjeta se pone roja y el
mapa pinta la zona afectada.

*[Botones rápidos]* Para quien no sabe qué preguntarle a un agente, estos
botones cubren las consultas típicas. Pregunto por cisternas: *[clic]* — el
agente recuerda que estoy en Cerro Azul, no me lo vuelve a preguntar. Eso es la
memoria funcionando.

*[Chat libre, con errata a propósito]* Ahora escribo con error: 'vivo en
miraflorez, ¿hay corte?' — lo reconoce igual y me responde sobre Miraflores.

*[Panorama]* Y una pregunta general: ¿cuántas interrupciones hay en Lima? Esta
no necesita ubicación: el agente usa la tool de resumen general.

*[Monitor]* Si activo el monitor del agente, veo la cocina: qué entendió la
extracción estructurada, qué tools ejecutó el agente con qué argumentos, y qué
revisaron los guardarraíles. Total transparencia de cada respuesta."

---

## [9:00 – 10:00] Cierre

**Mostrar:** el README o la estructura de archivos.

"En resumen: identificamos un proceso real que mejora con IA generativa — pasar
de un visor técnico a una conversación —. Construimos los bloques del agente:
seis tools geoespaciales, una skill que las orquesta, memoria conversacional,
output estructurado con Pydantic y cuatro guardarraíles deterministas. Y lo
disponibilizamos en Streamlit con tres formas de ubicarse y respuesta
automática.

Todo corre en local, sin API keys y sin costo por consulta. Las limitaciones
que conocemos: la base es una foto — el siguiente paso es el script de
actualización automática — y la memoria se pierde al reiniciar; en producción
usaríamos un checkpointer persistente y el feature service en vivo.

Gracias."

---
---

# Aspectos a tomar en cuenta (NO se leen — preparación)

**Antes de grabar:**

1. **Verifica qué distritos tienen cortes activos HOY**: corre
   `python -c "import tools; print(tools.resumen_general('ambas'))"` y elige
   para la demo un distrito con interrupción activa (que la tarjeta salga roja
   y el mapa pinte zonas). Si Cerro Azul ya no tiene corte activo, cambia el
   distrito en el guion.
2. **LM Studio listo**: modelo cargado, servidor encendido, Context Length
   32768, Max Concurrent Predictions = 1. Haz una consulta de calentamiento
   antes de grabar (la primera llamada siempre es más lenta).
3. **Regenera la base el día de la grabación** (`python preparar_datos.py`)
   para que la sidebar muestre fecha fresca.
4. **Ensaya la demo completa una vez**: con gemma-4-e2b cada respuesta puede
   tomar 20–40 segundos. Dos opciones: (a) graba la demo y acelera los
   silencios en edición (lo normal), o (b) graba las respuestas ya generadas y
   nárralas encima. No grabes en vivo sin ensayar.
5. **Cierra otras apps que usen GPU** (juegos, Chrome con muchas pestañas):
   compiten por los 4 GB de VRAM y el modelo se arrastra.

**Durante la grabación:**

6. Pantalla en 1080p, navegador con zoom 110–125% para que el texto se lea.
7. Cuando esperes al modelo, no te quedes callado: aprovecha para narrar lo que
   va a pasar ("el agente está consultando la tool de imprevistas...").
8. Muestra el monitor SOLO en la parte de la demo dedicada a él; para el resto
   de la demo apágalo (la respuesta se ve más limpia).

**Reparto si son 2–3 personas** (el trabajo es grupal): natural dividir en
(1) problema + arquitectura + datos, (2) los 5 bloques del agente,
(3) demo + cierre. Cada quien ~3 minutos.

**Si algo falla en vivo:** la pantalla de error de la app ya dice qué revisar
(LM Studio, base, código). Ten un segundo take del bloque de demo; no intentes
"arreglar en cámara".

**Ritmo:** el guion son ~1 350 palabras; a 150 palabras/minuto son 9 minutos.
Si te pasas, recorta primero el bloque de datos (3:15) y el cierre — la demo y
los guardarraíles son lo que más puntúa en la rúbrica.
