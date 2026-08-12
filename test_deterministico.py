"""
Banco de pruebas de los componentes DETERMINISTAS del GeoAgente
(tools, extraccion por reglas y guardarrailes) -- sin necesidad del LLM.

Ejecutar dentro de una estructura con data/interrupciones_limpio.parquet.
"""
from __future__ import annotations
import sys, traceback

OK, FAIL = "[OK] ", "[FALLO] "
resultados = []

def check(nombre, cond, detalle=""):
    estado = OK if cond else FAIL
    resultados.append((cond, nombre))
    print(f"{estado}{nombre}" + (f"  ->  {detalle}" if detalle else ""))

print("=" * 70)
print("1) CARGA DE DATOS Y DIAGNOSTICO")
print("=" * 70)
import tools
diag = tools.diagnostico_datos()
for k, v in diag.items():
    print(f"    {k}: {v}")
check("La base carga y tiene registros", diag["registros"] > 0, f"{diag['registros']} registros")
check("Hay geometrias para el cruce espacial", diag["con_geometria"] > 0, f"{diag['con_geometria']}")
check("Metadata de zona horaria OK", diag.get("zona_horaria_ok") is True)

print("\n" + "=" * 70)
print("2) TOOLS DE UBICACION")
print("=" * 70)
# --- por distrito
tools.clear_session_data()
distritos = tools.listar_distritos()
print(f"    distritos disponibles: {len(distritos)} (ej: {distritos[:5]})")
d = distritos[0]
r = tools.ubicar_por_distrito(d)
print(f"    ubicar_por_distrito('{d}') -> {r[:120]}")
check("ubicar_por_distrito devuelve texto", isinstance(r, str) and len(r) > 0)
check("ubicar_por_distrito deja coincidencias en sesion",
      "coincidencias" in tools.get_session_data())

# --- tolerancia a erratas
tools.clear_session_data()
r_errata = tools.ubicar_por_distrito("miraflorez")  # con z, errata deliberada
print(f"    ubicar_por_distrito('miraflorez') -> {r_errata[:100]}")
check("Tolera erratas (difflib) o pide confirmacion",
      isinstance(r_errata, str) and len(r_errata) > 0)

# --- por coordenadas fuera de Lima (Cusco) -> debe rechazar
tools.clear_session_data()
r_cusco = tools.ubicar_por_coordenadas(-13.53, -71.97)
print(f"    ubicar_por_coordenadas(Cusco) -> {r_cusco[:90]}")
check("Rechaza coordenadas fuera del BBOX de Lima",
      "fuera de Lima" in r_cusco)

# --- por coordenadas centro de Lima
tools.clear_session_data()
r_lima = tools.ubicar_por_coordenadas(-12.0464, -77.0428)
print(f"    ubicar_por_coordenadas(Lima centro) -> {r_lima[:110]}")
check("Coordenadas de Lima devuelven respuesta", isinstance(r_lima, str) and len(r_lima) > 0)

print("\n" + "=" * 70)
print("3) TOOLS DE DETALLE (sobre una ubicacion valida)")
print("=" * 70)
# Ubicar en un distrito con registros para poblar la sesion
tools.clear_session_data()
# elegir distrito con mas registros
import pandas as pd
base = tools.cargar_base()
distrito_top = base["dist_norm"].value_counts().index[0]
print(f"    usando distrito con mas registros: {distrito_top}")
tools.ubicar_por_distrito(distrito_top)
imp = tools.interrupciones_imprevistas()
prog = tools.interrupciones_programadas()
cist = tools.verificar_cisternas()
print(f"    imprevistas -> {imp[:90]}")
print(f"    programadas -> {prog[:90]}")
print(f"    cisternas   -> {cist[:90]}")
check("interrupciones_imprevistas responde", isinstance(imp, str) and len(imp) > 0)
check("interrupciones_programadas responde", isinstance(prog, str) and len(prog) > 0)
check("verificar_cisternas responde", isinstance(cist, str) and len(cist) > 0)

# --- resumen general (no necesita ubicacion)
tools.clear_session_data()
res = tools.resumen_general("ambas")
print(f"    resumen_general('ambas') -> {res[:140]}")
check("resumen_general funciona sin ubicacion", isinstance(res, str) and len(res) > 0)

# --- guard: detalle sin ubicar debe pedir ubicacion
tools.clear_session_data()
r_sin = tools.interrupciones_imprevistas()
check("Detalle sin ubicar pide ubicacion primero",
      "ubica" in r_sin.lower(), r_sin[:60])

print("\n" + "=" * 70)
print("4) EXTRACCION POR REGLAS (modo offline / fallback)")
print("=" * 70)
import extraction
casos = [
    ("Vivo en San Juan de Lurigancho, hay corte de agua ahora?", "corte_actual"),
    ("Habra cortes programados este mes en Miraflores?", "corte_programado"),
    ("Hay cisternas o reparto de agua?", "cisternas"),
    ("Mi lat -12.05 lon -77.04", "ubicacion"),
    ("hola", "saludo"),
]
for texto, esperado in casos:
    c = extraction.extract_query(texto, usar_llm=False)
    print(f"    '{texto[:45]}...' -> tipo={c.tipo_consulta} distrito={c.distrito} "
          f"vuln={c.caso_vulnerable} metodo={c.metodo}")
    check(f"Clasifica '{esperado}'", c.tipo_consulta == esperado,
          f"obtuvo {c.tipo_consulta}")

# caso vulnerable
c_vuln = extraction.extract_query("Mi abuela es adulta mayor y no hay agua en Surco", usar_llm=False)
print(f"    caso vulnerable -> {c_vuln.caso_vulnerable}, distrito={c_vuln.distrito}")
check("Detecta caso vulnerable (adulta mayor)", c_vuln.caso_vulnerable is True)

print("\n" + "=" * 70)
print("5) GUARDARRAILES")
print("=" * 70)
import guardrails

# GR1: afirma sin evidencia -> bloquea
rev1 = guardrails.aplicar_guardarrailes("Si, hay un corte de agua en tu zona.", {"evidencia": []})
print(f"    GR1 sin evidencia -> bloqueado={rev1.bloqueado}")
check("GR1 bloquea afirmacion sin evidencia", rev1.bloqueado is True)

# GR2: telefono inventado -> bloquea
rev2 = guardrails.aplicar_guardarrailes(
    "Hay un corte. Llama al 987654321 para mas info.",
    {"evidencia": ["Hay 1 interrupcion imprevista activa"]})
print(f"    GR2 telefono inventado -> bloqueado={rev2.bloqueado} alertas={rev2.alertas}")
check("GR2 bloquea telefono no oficial", rev2.bloqueado is True)

# GR3: fecha no verificada -> alerta + nota
rev3 = guardrails.aplicar_guardarrailes(
    "El servicio se restablece el 15/12/2099 a las 08:00.",
    {"evidencia": ["Hay 1 interrupcion imprevista activa ahora"]})
print(f"    GR3 fecha no verificada -> bloqueado={rev3.bloqueado} alertas={rev3.alertas}")
check("GR3 alerta fecha no verificada", any("fecha" in a for a in rev3.alertas))
check("GR3 agrega nota del sistema", "Nota del sistema" in rev3.texto)

# GR4: cierre faltante -> se agrega telefono oficial
rev4 = guardrails.aplicar_guardarrailes(
    "No hay interrupciones imprevistas activas en tu zona.",
    {"evidencia": ["No hay interrupciones imprevistas activas en tu zona en este momento."]})
print(f"    GR4 cierre -> texto termina con 1899? {'1899' in rev4.texto}")
check("GR4 agrega cierre oficial 1899", "1899" in rev4.texto)

# GR ok: telefono oficial no se bloquea
rev5 = guardrails.aplicar_guardarrailes(
    "No hay cortes. Para mas informacion, comunicate al 1899.",
    {"evidencia": ["No hay interrupciones imprevistas activas"]})
check("Telefono oficial 1899 NO se bloquea", rev5.bloqueado is False)

print("\n" + "=" * 70)
n_ok = sum(1 for ok, _ in resultados if ok)
n_total = len(resultados)
print(f"RESULTADO: {n_ok}/{n_total} pruebas OK")
if n_ok < n_total:
    print("Fallaron:")
    for ok, nombre in resultados:
        if not ok:
            print(f"   - {nombre}")
print("=" * 70)
sys.exit(0 if n_ok == n_total else 1)
