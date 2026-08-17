"""
App de Streamlit del GeoAgente de interrupciones de agua (Lima).

    streamlit run app.py

Reparto de trabajo, para que el agente haga lo minimo posible:

  - preparar_datos.py (FUERA de la app) ya dejo la base limpia.
  - La APP resuelve la ubicacion (GPS, desplegable o clic en el mapa) llamando
    directamente a la tool de cruce: codigo determinista, sin gastar una llamada
    al LLM, que en un modelo local es medio minuto.
  - El AGENTE recibe la ubicacion ya resuelta y solo consulta el detalle y redacta.
  - La APP dibuja el mapa despues de cada respuesta, tambien sin pasar por el modelo.

UX: tres formas de ubicarse (GPS / desplegables / clic en mapa), respuesta
automatica al ubicarse, botones de consulta rapida para no obligar a escribir, y
un monitor tecnico plegado que muestra extraccion, tools y guardarrailes.

Layout: el panel de control vive en la barra lateral (ancho fijo de Streamlit,
no cambia); el resto del ancho se reparte a partes iguales entre el chat y el
mapa, lado a lado, con el historial en un contenedor de altura fija para que
ambas columnas queden visualmente parejas.
"""

from __future__ import annotations

import uuid

import streamlit as st
from streamlit_folium import st_folium

st.set_page_config(
    page_title="GeoAgente · Interrupciones de agua Lima",
    page_icon="💧",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --------------------------------------------------------------------------- #
# Estilo
# --------------------------------------------------------------------------- #
st.markdown("""
<style>
/* Aprovechar TODO el ancho de la pagina: Streamlit centra el contenido en una
   columna de ancho maximo fijo; se libera para que chat y mapa crezcan. */
[data-testid="stMainBlockContainer"], section.main > div.block-container {
  max-width: 100%;
  padding-left: 2rem;
  padding-right: 2rem;
  padding-top: 1.5rem;
}

/* Cabecera */
.geo-header {
  background: linear-gradient(90deg, #0c4a6e 0%, #0e7490 60%, #0891b2 100%);
  border-radius: 14px; padding: 1.1rem 1.5rem; margin-bottom: 1rem; color: #fff;
}
.geo-header h1 { color:#fff; font-size:1.55rem; margin:0 0 .25rem 0; }
.geo-header p  { color:#cffafe; margin:0; font-size:.95rem; }

/* Tarjeta de estado de ubicacion */
.geo-status {
  border-radius: 10px; padding: .65rem 1rem; margin: .5rem 0 .25rem 0;
  font-size: .93rem; border: 1px solid transparent;
}
.geo-status.ok    { background:#ecfeff; border-color:#a5f3fc; color:#155e75; }
.geo-status.warn  { background:#fffbeb; border-color:#fde68a; color:#92400e; }
.geo-status.alert { background:#fef2f2; border-color:#fecaca; color:#991b1b; }

/* Botones de consulta rapida */
div[data-testid="stHorizontalBlock"] button[kind="secondary"] {
  border-radius: 999px; border: 1px solid #a5f3fc; background: #f0fdff;
  color: #0e7490; font-size: .87rem;
}
div[data-testid="stHorizontalBlock"] button[kind="secondary"]:hover {
  border-color: #0e7490; background: #ecfeff; color: #0c4a6e;
}

/* Panel lateral: espaciado moderado, sin scroll en pantallas normales */
section[data-testid="stSidebar"] div[data-testid="stMetricValue"] { font-size: 1.35rem; }
section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] { gap: 0.8rem; }
section[data-testid="stSidebar"] hr { margin: 0.8rem 0; }
div[data-testid="stSidebarUserContent"] { padding-top: 1.3rem; padding-bottom: 1.2rem; }
.paso { font-weight:600; color:#0c4a6e; font-size:1.05rem; margin:.35rem 0 .1rem 0; }
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Carga (cacheada: construir el agente y leer el parquet es caro)
# --------------------------------------------------------------------------- #
@st.cache_resource(show_spinner="Conectando con LM Studio y cargando la base...")
def cargar():
    from agent import BASE_URL, MODEL_NAME, agent, texto_respuesta
    from tools import diagnostico_datos

    return {
        "agent": agent,
        "texto_respuesta": texto_respuesta,
        "modelo": MODEL_NAME,
        "base_url": BASE_URL,
        "diag": diagnostico_datos(),
    }


try:
    CTX, ERROR = cargar(), None
except Exception as exc:  # noqa: BLE001
    CTX, ERROR = None, exc

st.markdown("""
<div class="geo-header">
  <h1>💧 GeoAgente de interrupciones de agua — Lima</h1>
  <p>Comparte tu ubicacion o dime tu distrito y te digo si hay corte de agua,
  cuando vuelve el servicio y si hay camiones cisterna.</p>
</div>
""", unsafe_allow_html=True)

if ERROR is not None:
    # El mensaje se adapta al tipo de fallo para no mandar a revisar LM Studio
    # cuando el problema es del codigo o de los datos.
    if isinstance(ERROR, FileNotFoundError):
        pista = ("Falta la base limpia. Ejecuta primero:\n\n"
                 "```\npython preparar_datos.py\n```")
    elif isinstance(ERROR, (NameError, ImportError, AttributeError, SyntaxError)):
        pista = "Es un error del codigo, no del servidor. Revisa el archivo que menciona."
    else:
        pista = ("Si usas LM Studio (LLM_PROVIDER=lmstudio): abrelo, carga un modelo y "
                 "enciende el servidor en http://127.0.0.1:1234, y revisa que "
                 "LMSTUDIO_MODEL del .env coincida con el identificador del modelo. "
                 "Si usas Gemini (LLM_PROVIDER=gemini): revisa que GEMINI_API_KEY "
                 "este en el .env y sea valida.")
    st.error(f"No pude iniciar el agente.\n\n`{type(ERROR).__name__}: {ERROR}`\n\n{pista}")
    st.stop()


from tools import (  # noqa: E402  (despues del st.stop para no fallar dos veces)
    clear_session_data,
    construir_mapa,
    diagnostico_datos,
    get_session_data,
    listar_distritos,
    listar_provincias,
    ubicar_por_coordenadas,
    ubicar_por_distrito,
)

# --------------------------------------------------------------------------- #
# Estado
# --------------------------------------------------------------------------- #
if "messages" not in st.session_state:
    st.session_state.messages = []
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "ubicacion" not in st.session_state:
    st.session_state.ubicacion = None      # {"lat","lon"} o {"distrito","provincia"}
if "ubicacion_respondida" not in st.session_state:
    st.session_state.ubicacion_respondida = None


# --------------------------------------------------------------------------- #
# Barra lateral = panel de control (ancho fijo: no compite con chat ni mapa)
# --------------------------------------------------------------------------- #
def resolver_ubicacion(u: dict) -> str:
    """Cruce espacial de la ubicacion guardada, sin LLM. Se llama en cada turno
    porque clear_session_data() limpia las coincidencias."""
    if u.get("lat") is not None:
        return ubicar_por_coordenadas(u["lat"], u["lon"])
    return ubicar_por_distrito(u.get("distrito", ""), u.get("provincia", ""))


with st.sidebar:
    st.markdown('<div class="paso">1 · ¿Donde estas?</div>', unsafe_allow_html=True)
    tab_gps, tab_distrito = st.tabs(["📡 Mi ubicacion", "🏙️ Mi distrito"])

    with tab_gps:
        st.caption("Pulsa el icono y acepta el permiso del navegador.")
        from streamlit_geolocation import streamlit_geolocation

        loc = streamlit_geolocation()
        # El componente devuelve las coordenadas en CADA reejecucion una vez
        # concedido el permiso: sin esta guarda, una lectura vieja del GPS pisa
        # el distrito elegido despues (mismo patron que el clic del mapa).
        if loc and loc.get("latitude") is not None:
            gps = (round(loc["latitude"], 6), round(loc["longitude"], 6))
            if st.session_state.get("ultimo_gps") != gps:
                st.session_state.ultimo_gps = gps
                st.session_state.ubicacion = {"lat": loc["latitude"], "lon": loc["longitude"]}

    with tab_distrito:
        # El desplegable cubre el departamento de Lima y el Callao (el mismo
        # ambito del panorama). Los demas distritos del pais siguen disponibles
        # escribiendolos en el chat.
        provincias = listar_provincias(ambito_lima=True)
        provincia = st.selectbox("Provincia", provincias,
                                 index=provincias.index("LIMA") if "LIMA" in provincias else 0)
        distrito = st.selectbox("Distrito", listar_distritos(provincia))
        if st.button("Usar este distrito", use_container_width=True):
            st.session_state.ubicacion = {"distrito": distrito, "provincia": provincia}

    st.caption("💡 O haz clic en el mapa, o escribe tu distrito en el chat.")

    st.divider()
    # En vivo (no el diag cacheado de CTX): la base se refresca desde el Drive
    # cada 15 min y estos contadores deben reflejarla sin reiniciar la app.
    d = diagnostico_datos()
    st.markdown("### ⚙️ Estado del sistema")
    st.caption(f"{d['registros']:,} interrupciones · {d['distritos']} distritos · "
               f"{d['provincias']} provincias · 🕐 {d['ahora_lima']}")

    c1, c2 = st.columns(2)
    # "Sin agua ahora" suma imprevistas y programadas ya iniciadas: las dos
    # dejan al vecino sin servicio. El desglose va debajo para distinguirlas.
    c1.metric("🔴 Sin agua ahora", d["sin_agua_ahora"])
    c2.metric("🟠 Por empezar", d["programadas_resto_mes"])
    st.caption(f"Ahora: {d['activas_ahora']} imprevista(s) · "
               f"{d['programadas_en_curso']} programada(s) en curso. "
               f"«Por empezar» son las programadas del resto del mes.")
    st.caption("Contadores de Lima y Callao; la base cubre todo el pais.")
    if d["zona_horaria_ok"] is False:
        st.warning("La base quedo con fechas en UTC. Regenera con "
                   "`python preparar_datos.py --utc`.")

    st.divider()
    if st.button("🧹 Nueva consulta", use_container_width=True, type="primary",
                 help="Borra el chat y la memoria del agente."):
        st.session_state.messages = []
        st.session_state.thread_id = str(uuid.uuid4())   # hilo nuevo = memoria en blanco
        st.session_state.ubicacion = None
        st.session_state.ubicacion_respondida = None
        clear_session_data()
        st.rerun()

# Opciones tecnicas fuera de la interfaz (antes eran toggles del panel): el
# monitor del agente queda apagado y la extraccion va siempre por reglas, que
# es mas rapida y no gasta una llamada al modelo.
show_monitor = False
usar_llm_extraccion = False


# --------------------------------------------------------------------------- #
# Area principal: chat y mapa a partes iguales (el panel de control quedo en la
# barra lateral, cuyo ancho fijo no se ve afectado por este reparto).
# --------------------------------------------------------------------------- #
# El cruce se ejecuta ANTES de dibujar: el mapa pinta las zonas que deja el cruce.
u = st.session_state.ubicacion
resumen = resolver_ubicacion(u) if u else ""

if u:
    import re as _re

    etiqueta = (f"Coordenadas {u['lat']:.5f}, {u['lon']:.5f}" if u.get("lat") is not None
                else f"{u['distrito']} ({u['provincia']})")
    # El primer numero del resumen es el total de cortes EN CURSO (imprevistos
    # + programados ya iniciados): el semaforo se pone rojo si el vecino no
    # tiene agua ahora, sea cual sea el motivo.
    m = _re.search(r"Hay (\d+) interrupcion", resumen)
    if m and int(m.group(1)) > 0:
        clase = "alert"      # sin servicio en este momento
    elif m:
        clase = "ok"         # ubicado, con servicio
    else:
        clase = "warn"       # fuera de cobertura / distrito no reconocido / sin registros
    st.markdown(f'<div class="geo-status {clase}">📍 <b>{etiqueta}</b> — {resumen}</div>',
                unsafe_allow_html=True)
else:
    st.markdown('<div class="geo-status warn">📍 Aun no me has dicho donde estas. '
                'Comparte tu ubicacion, elige tu distrito o haz clic en el mapa.</div>',
                unsafe_allow_html=True)

ALTO_MAPA = 430       # px; el historial del chat se dimensiona para quedar a la par
ALTO_HISTORIAL = 280

col_chat, col_mapa = st.columns(2, gap="medium")

with col_mapa:
    st.markdown('<div class="paso">🗺️ Mapa de interrupciones</div>', unsafe_allow_html=True)
    salida = st_folium(
        construir_mapa(),
        use_container_width=True,
        height=ALTO_MAPA,
        returned_objects=["last_clicked"],
        key="mapa_ubicacion",
    )
    # Clic en el mapa = tercera forma de ubicarse. st_folium devuelve el ULTIMO
    # clic en TODAS las reejecuciones, aunque sea de hace rato: se recuerda cual
    # fue el ultimo ya procesado y solo un clic NUEVO cambia la ubicacion. La
    # guarda anterior comparaba contra la ubicacion vigente, y un clic viejo
    # "revivia" pisando el distrito elegido despues en el panel (y disparando
    # otra respuesta automatica).
    if salida and salida.get("last_clicked"):
        lat, lon = salida["last_clicked"]["lat"], salida["last_clicked"]["lng"]
        clic = (round(lat, 6), round(lon, 6))
        if st.session_state.get("ultimo_clic") != clic:
            st.session_state.ultimo_clic = clic
            st.session_state.ubicacion = {"lat": lat, "lon": lon}
            st.rerun()


def render_monitor(extraido: dict, tool_calls: list[dict], guardarrailes: list[dict]) -> None:
    with st.expander("🔍 Monitor del agente"):
        c1, c2 = st.columns(2)
        c1.markdown("**Output estructurado (Pydantic)**")
        c1.json(extraido)
        c2.markdown("**Guardarrailes**")
        c2.table(guardarrailes)
        st.markdown("**Tools ejecutadas por el agente**")
        # if/else normal, no ternario: una expresion suelta hace que el "magic"
        # de Streamlit intente escribir su resultado y falle al parsear la linea.
        if tool_calls:
            st.table(tool_calls)
        else:
            st.caption("Ninguna: la app ya habia resuelto la ubicacion o no hacia "
                       "falta consultar.")


def tool_calls_del_turno(mensajes) -> list[dict]:
    """Empareja cada tool call con su resultado, para el monitor."""
    calls, pendientes = [], {}
    for m in mensajes:
        for tc in getattr(m, "tool_calls", None) or []:
            pendientes[tc["id"]] = {"tool": tc["name"], "args": tc["args"]}
        if type(m).__name__ == "ToolMessage":
            base = pendientes.get(m.tool_call_id, {"tool": getattr(m, "name", "?"), "args": {}})
            res = str(m.content)
            calls.append({**base, "resultado": res[:200] + ("..." if len(res) > 200 else "")})
    return calls


with col_chat:
    st.markdown('<div class="paso">2 · Preguntame</div>', unsafe_allow_html=True)
    # Contenedor de altura fija: el chat crece hacia adentro (con scroll) en vez
    # de empujar los botones y el input, y la columna queda a la par del mapa.
    historial = st.container(height=ALTO_HISTORIAL)

with historial:
    if not st.session_state.messages:
        st.caption("Aqui apareceran tus consultas y mis respuestas. Ubicate y te "
                   "cuento como esta el servicio en tu zona.")
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="💧" if msg["role"] == "assistant" else None):
            st.markdown(msg["content"])
            if show_monitor and "extraido" in msg:
                render_monitor(msg["extraido"], msg.get("tool_calls", []), msg.get("guardarrailes", []))


def ejecutar_turno(prompt: str, panorama: bool = False) -> bool:
    """Turno completo: extraer -> pre-resolver -> agente -> guardarrailes.
    Devuelve True si la ubicacion cambio (los llamadores reejecutan siempre
    igualmente, porque semaforo y mapa se pintan antes que el turno).

    Con panorama=True la pregunta es sobre TODA la ciudad: se responde de forma
    determinista con resumen_general (la misma fuente del panel lateral), sin
    inyectar la zona del ciudadano ni depender de que el modelo de 2B elija la
    tool correcta. Asi el total SIEMPRE coincide con "Activas ahora / Programadas".
    """
    from extraction import extract_query
    from guardrails import aplicar_guardarrailes

    ubicacion_previa = st.session_state.ubicacion

    st.session_state.messages.append({"role": "user", "content": prompt})
    # Todo el turno se pinta DENTRO del historial (contenedor de la columna de
    # chat): asi la respuesta en vivo aparece en su sitio y no al pie de la pagina.
    with historial, st.chat_message("user"):
        st.markdown(prompt)

    with historial, st.chat_message("assistant", avatar="💧"):
        # La evidencia se acumula por turno: se limpia antes para que los
        # guardarrailes no validen contra resultados de turnos anteriores.
        clear_session_data()

        with st.spinner("Revisando el registro de interrupciones..."):
            consulta = extract_query(prompt, usar_llm=usar_llm_extraccion)

            if panorama:
                # Panorama de toda Lima: determinista, sin ubicacion ni LLM.
                from tools import resumen_general

                bruto = resumen_general("ambas")
                tcalls = [{
                    "tool": "resumen_general",
                    "args": {"tipo": "ambas"},
                    "resultado": bruto[:200] + ("..." if len(bruto) > 200 else ""),
                }]
            else:
                # Pre-resolucion: si ya sabemos donde esta, se cruza aqui y se le
                # pasa hecho. Si escribio un distrito en el chat, ese manda.
                mensaje = prompt
                if consulta.distrito:
                    st.session_state.ubicacion = {"distrito": consulta.distrito, "provincia": ""}
                    # La ubicacion se fijo DESDE el chat y este turno ya la
                    # responde: se marca como respondida para que no salte
                    # ademas la "respuesta automatica al fijar la ubicacion".
                    st.session_state.ubicacion_respondida = st.session_state.ubicacion
                if st.session_state.ubicacion:
                    res = resolver_ubicacion(st.session_state.ubicacion)
                    mensaje = f"[UBICACION YA RESUELTA: {res}]\n\n{prompt}"

                try:
                    config = {"configurable": {"thread_id": st.session_state.thread_id}}
                    previos = CTX["agent"].get_state(config)
                    n_previos = len(previos.values.get("messages", [])) if previos.values else 0

                    respuesta = CTX["agent"].invoke(
                        {"messages": [{"role": "user", "content": mensaje}]}, config=config
                    )
                    bruto = CTX["texto_respuesta"](respuesta["messages"][-1])
                    tcalls = tool_calls_del_turno(respuesta["messages"][n_previos:])
                except Exception as exc:  # noqa: BLE001
                    st.error(f"El agente fallo: `{type(exc).__name__}: {exc}`")
                    st.session_state.messages.pop()
                    st.stop()

            revision = aplicar_guardarrailes(bruto, get_session_data(), consulta)

        st.markdown(revision.texto)
        if revision.bloqueado:
            st.warning(f"Guardarrail: {revision.alertas[0] if revision.alertas else 'respuesta bloqueada'}")

        guardado = {
            "role": "assistant",
            "content": revision.texto,
            "extraido": consulta.model_dump(),
            "tool_calls": tcalls,
            "guardarrailes": revision.resumen(),
        }
        if show_monitor:
            render_monitor(guardado["extraido"], tcalls, guardado["guardarrailes"])
        st.session_state.messages.append(guardado)

    return st.session_state.ubicacion != ubicacion_previa


# --- Respuesta automatica al fijar la ubicacion ----------------------------- #
# En cuanto se sabe donde esta, el agente contesta solo; `ubicacion_respondida`
# evita repetirlo en cada reejecucion de Streamlit. Si la ubicacion NO resolvio
# a ninguna zona (clase "warn"), no se gasta un turno del agente en pedir el
# distrito: el semaforo de arriba ya lo esta diciendo.
if u and st.session_state.ubicacion_respondida != u:
    st.session_state.ubicacion_respondida = u
    if clase != "warn":
        ejecutar_turno("¿Hay alguna interrupcion de agua en mi zona ahora o programada "
                       "en lo que resta del mes?")
        st.rerun()

# --- Botones de consulta rapida --------------------------------------------- #
# La mayoria de la gente no sabe que preguntarle a un agente: estos botones
# cubren las cuatro consultas tipicas sin obligar a escribir. Van en cuadricula
# de 2x2 porque ahora comparten media pantalla con el mapa.
# Cada accion: (etiqueta, pregunta, necesita_ubicacion, es_panorama).
ACCIONES = [
    ("🚱 ¿Hay corte ahora?", "¿Hay alguna interrupcion de agua en mi zona ahora mismo?", True, False),
    ("📅 Cortes programados", "¿Hay cortes de agua programados en mi zona en lo que resta del mes?", True, False),
    ("🚚 ¿Hay cisternas?", "¿Hay abastecimiento con camiones cisterna en mi zona?", True, False),
    ("🌆 Panorama de Lima", "¿Cuantas interrupciones hay ahora en toda Lima y en que distritos?", False, True),
]
accion_elegida = None
accion_panorama = False
with col_chat:
    for fila in (ACCIONES[:2], ACCIONES[2:]):
        for col, (etq, pregunta, necesita_ubic, es_panorama) in zip(st.columns(2), fila):
            with col:
                if st.button(etq, use_container_width=True, type="secondary",
                             disabled=necesita_ubic and not u,
                             help=None if u or not necesita_ubic else "Primero dime donde estas"):
                    accion_elegida = pregunta
                    accion_panorama = es_panorama
    prompt = st.chat_input("Escribe tu consulta... (ej. 'vivo en Cerro Azul, ¿hay corte?')")

if accion_elegida:
    ejecutar_turno(accion_elegida, panorama=accion_panorama)
    # Reejecutar siempre: el semaforo y el mapa se dibujaron ANTES del turno
    # (Streamlit corre de arriba hacia abajo) y podrian quedar desfasados.
    st.rerun()

if prompt:
    # Una pregunta explicita sobre toda la ciudad tambien va por la via
    # determinista, aunque el ciudadano ya este ubicado.
    import re as _re_pan

    es_pregunta_panorama = bool(_re_pan.search(
        r"toda\s+lima|toda\s+la\s+ciudad|en\s+qu[eé]\s+distritos?|\bpanorama\b",
        prompt, _re_pan.IGNORECASE))
    ejecutar_turno(prompt, panorama=es_pregunta_panorama)
    # chat_input devuelve None en la reejecucion, asi que no se reinvoca al agente.
    st.rerun()

st.caption("Este asistente informa cortes publicados por las empresas prestadoras. "
           "Emergencias: llama al 1899.")
