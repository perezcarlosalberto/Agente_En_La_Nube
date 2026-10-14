"""
Práctica 2: Mi Agente en la Nube — Agente de Soporte con Python, Gemini y GitHub Actions

Flujo del agente:
    Google Forms  ->  Google Sheets  ->  (este script)  ->  Gemini  ->  Google Sheets

El agente lee las quejas/sugerencias nuevas registradas por el formulario,
las analiza con Gemini (clasificación y sentimiento) y escribe el resultado
en las columnas D, E y F de la hoja de cálculo.

Formas de ejecutarlo:
    python agente_soporte.py            -> modo normal: revisa cada INTERVALO_MINUTOS (Ctrl+C para salir)
    python agente_soporte.py --una-vez  -> un solo ciclo y termina (lo usa GitHub Actions)
    python agente_soporte.py --reprocesar -> vuelve a clasificar TODAS las filas una vez y termina
"""

# =====================================================================
# 1. IMPORTACIONES (Paso 4.2 / Práctica 2: Pasos 1.1, 1.2 y 1.3)
# =====================================================================
import json      # Convertir JSON (respuesta de Gemini y token de Google) en diccionario
import os        # Rutas de archivos y variables de entorno
import sys       # Leer los argumentos con los que se ejecutó el script (--una-vez)
import time      # Pausas entre ciclos del agente

import schedule  # Programar la ejecución automática cada cierto tiempo
import gspread   # Leer y escribir en Google Sheets
from dotenv import load_dotenv    # Cargar la API Key desde el archivo .env
from google import genai          # Cliente de la API de Gemini
from google.genai import types    # Configuración de la petición a Gemini
from google.genai import errors as genai_errors        # Errores de la API (saturado, sin cuota...)
from google.oauth2.credentials import Credentials      # Credenciales a partir del token guardado
from google.auth.transport.requests import Request     # Renovar el token sin abrir el navegador


# =====================================================================
# 2. CONFIGURACIÓN (Paso 4.3 / Práctica 2: Paso 1.1)
# =====================================================================
# Carpeta donde está este script. Así el agente encuentra sus archivos
# aunque se ejecute desde otra carpeta (por ejemplo, con el botón Run de VS Code).
CARPETA_SCRIPT = os.path.dirname(os.path.abspath(__file__))

# Cargar las variables del archivo .env (solo existe en la computadora).
# En GitHub Actions no hay .env: los valores llegan como variables de entorno desde los Secrets.
load_dotenv(os.path.join(CARPETA_SCRIPT, ".env"))

# Archivo OAuth descargado en la Fase 2
ARCHIVO_CREDENCIALES = os.path.join(CARPETA_SCRIPT, "client_secrets.json")

# Aquí se guarda el token después de autorizar en el navegador la primera vez.
# Su contenido es el que se guarda en el Secret GOOGLE_TOKEN_JSON de GitHub.
# Si algún día da error de "invalid_grant", basta con borrar este archivo.
ARCHIVO_TOKEN = os.path.join(CARPETA_SCRIPT, "token_autorizado.json")

# Permisos que se piden a Google: los mismos que gspread.oauth() usaba por defecto en la Práctica 1
SCOPES_GOOGLE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Nombre EXACTO de la hoja de cálculo creada en la Fase 1
NOMBRE_HOJA = "Respuestas de Quejas (Base de Datos)"

# API Key de Gemini obtenida en la Fase 3.
# En la computadora se lee del archivo .env; en GitHub Actions, del Secret GEMINI_API_KEY.
# Si no existe, queda el texto de marcador y la validación del punto de entrada lo detecta.
TEXTO_MARCADOR = "PEGA_AQUI_TU_API_KEY"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", TEXTO_MARCADOR)

# Modelos de Gemini que usa el agente, en orden de preferencia.
# Si el primero falla (saturado, sin cuota o retirado), se intenta con el siguiente.
MODELOS_GEMINI = [
    "gemini-3.5-flash-lite",   # Principal: Gemini 3.5 Flash Lite
    "gemini-3.1-flash-lite",   # Respaldo: Gemini 3.1 Flash Lite
]

# Únicas respuestas válidas: Gemini está obligado a elegir una de cada lista
CATEGORIAS = ["Ventas", "Soporte Técnico", "Logística"]
SENTIMIENTOS = ["Positivo", "Negativo", "Neutro"]

# Cada cuántos minutos el agente revisa si hay nuevas quejas
INTERVALO_MINUTOS = 1

# Índices de columnas del Sheet (gspread cuenta desde 1: A=1, B=2, ...)
COL_NOMBRE = 2          # B - Tu Nombre
COL_COMENTARIO = 3      # C - Describe tu queja...
COL_CLASIFICACION = 4   # D - Clasificación IA
COL_SENTIMIENTO = 5     # E - Sentimiento IA
COL_EJECUTADO = 6       # F - Agente Ejecutado

# Cliente de Gemini (se crea en el bloque principal, al final del archivo)
cliente_gemini = None


# =====================================================================
# 3. FUNCIONES AUXILIARES
# =====================================================================
def obtener_celda(fila, columna):
    """Devuelve el valor de una columna (A=1) de la fila, o "" si no existe."""
    if len(fila) >= columna:
        return fila[columna - 1].strip()
    return ""


# =====================================================================
# 4. CONECTAR CON GOOGLE SHEETS (Paso 4.4 / Práctica 2: Paso 1.2)
# =====================================================================
def conectar_sheets():
    """Se autentica con OAuth 2.0 y devuelve la primera hoja (sheet1).

    - En GitHub Actions: usa el token del Secret GOOGLE_TOKEN_JSON (sin navegador).
    - En la computadora: inicia sesión con el navegador, igual que en la Práctica 1.
    """
    token_json = os.getenv("GOOGLE_TOKEN_JSON", "").strip()

    if token_json:
        # Convertir el texto del token en diccionario (JSONDecodeError si se copió incompleto)
        info_token = json.loads(token_json)
        creds = Credentials.from_authorized_user_info(info_token, SCOPES_GOOGLE)
        # Pedir a Google un acceso nuevo con el refresh token incluido en el token
        creds.refresh(Request())
        cliente_sheets = gspread.authorize(creds)
        return cliente_sheets.open(NOMBRE_HOJA).sheet1

    # La primera vez abre el navegador para iniciar sesión y autorizar.
    cliente_sheets = gspread.oauth(
        scopes=SCOPES_GOOGLE,
        credentials_filename=ARCHIVO_CREDENCIALES,
        authorized_user_filename=ARCHIVO_TOKEN,
    )
    libro = cliente_sheets.open(NOMBRE_HOJA)
    return libro.sheet1


# =====================================================================
# 5. ANALIZAR CON GEMINI (Paso 4.5)
# =====================================================================
# Instrucción del sistema: el "rol" del agente y los criterios para clasificar.
# Los ejemplos son los resultados esperados de la Práctica 1 (Paso 5.2) y las correcciones del alumno.
INSTRUCCION_SISTEMA = """Eres un analista de soporte al cliente de una tienda. Tu única tarea es clasificar el comentario de un cliente y devolver un JSON.

CATEGORÍAS (elige exactamente una):
- Ventas: el producto en sí (calidad, defectos, que no sirva, que no sea lo que el cliente esperaba, si lo recomienda o no); el proceso de compra cuando NO se menciona una falla de la app o de la página; precios, promociones, descuentos, disponibilidad, pagos, cobros, facturas, cambios y devoluciones; y la atención del personal o la atención al cliente en general (vendedores, trato, amabilidad).
- Soporte Técnico: fallas de la app, la página web o el sistema (se cierra, marca error, no carga, no deja iniciar sesión, error al pagar en la página); dudas sobre cómo usar, instalar, configurar o actualizar la app o el producto; ayuda técnica.
- Logística: envío y entrega: retrasos, pedidos que no llegan o que llegan antes o a tiempo, paquetería, rastreo, dirección de entrega, empaque, y productos que llegan golpeados o dañados por el transporte.

REGLAS:
1. Si el comentario mezcla varios temas, elige el problema principal: el más grave o, si pesan igual, el que se menciona primero.
2. Un producto defectuoso o que no sirve es Ventas; un producto golpeado o dañado durante el envío es Logística.
3. Un problema para comprar o pagar es Ventas, salvo que mencione una falla de la app o de la página (entonces es Soporte Técnico).

SENTIMIENTO (elige exactamente uno):
- Positivo: satisfacción, elogios o agradecimiento.
- Negativo: quejas, molestia o insatisfacción, incluido el sarcasmo ("Genial, otra vez se cerró la app").
- Neutro: preguntas, solicitudes de información o comentarios sin una emoción clara.
Si hay emociones mezcladas, elige la que predomina.

EJEMPLOS:
"No entiendo cómo funciona la nueva app, el botón de ayuda no sirve." -> Soporte Técnico / Negativo
"Me atendió un vendedor llamado Juan y fue increíble, ¡resolvió todo! 10/10." -> Ventas / Positivo
"Mi paquete lleva 2 semanas sin llegar y nadie me da información." -> Logística / Negativo
"Tuve problemas para realizar la compra" -> Ventas / Negativo
"No me sirvió el producto, no lo recomiendo" -> Ventas / Negativo"""

# Estructura obligatoria de la respuesta: Gemini solo puede elegir valores de estas listas
ESQUEMA_RESPUESTA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "clasificacion": types.Schema(type=types.Type.STRING, enum=CATEGORIAS),
        "sentimiento": types.Schema(type=types.Type.STRING, enum=SENTIMIENTOS),
    },
    required=["clasificacion", "sentimiento"],
)


def consultar_modelo(modelo, comentario):
    """Pide a un modelo de Gemini que analice el comentario y devuelve el resultado validado."""
    # Instrucción del usuario: el comentario a analizar
    instruccion_usuario = f'Comentario del cliente: "{comentario}"'

    # Llamada a la API de Gemini
    respuesta = cliente_gemini.models.generate_content(
        model=modelo,
        contents=instruccion_usuario,
        config=types.GenerateContentConfig(
            system_instruction=INSTRUCCION_SISTEMA,
            response_mime_type="application/json",  # responder JSON puro
            response_schema=ESQUEMA_RESPUESTA,      # solo valores permitidos
            temperature=0,                          # respuestas consistentes
            # No usamos herramientas; desactivarlo evita un aviso innecesario en pantalla
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )

    # Limpiar la respuesta: quitar posibles bloques ```json ... ```
    texto = (respuesta.text or "").strip()
    texto = texto.replace("```json", "").replace("```", "").strip()

    # Convertir el texto JSON en diccionario (lanza JSONDecodeError si no es válido)
    resultado = json.loads(texto)

    clasificacion = resultado.get("clasificacion")
    sentimiento = resultado.get("sentimiento")
    if clasificacion not in CATEGORIAS or sentimiento not in SENTIMIENTOS:
        # No se escribe nada raro en la hoja: la fila queda pendiente para el siguiente ciclo
        raise ValueError(f"respuesta fuera de las opciones permitidas: {resultado}")

    return {"clasificacion": clasificacion, "sentimiento": sentimiento}


def analizar_con_gemini(comentario):
    """Envía el comentario a Gemini y devuelve {'clasificacion', 'sentimiento'}.

    Prueba los modelos de MODELOS_GEMINI en orden: si uno falla (saturado, sin cuota
    o retirado), intenta con el siguiente. Si todos fallan, lanza el último error.
    """
    ultimo_error = None
    for modelo in MODELOS_GEMINI:
        try:
            return consultar_modelo(modelo, comentario)
        except genai_errors.APIError as error:
            print(f"    Aviso: el modelo {modelo} falló ({error.code} {error.status}); probando el siguiente...")
            ultimo_error = error

    raise ultimo_error


# =====================================================================
# 6. AGENTE PRINCIPAL (Paso 4.6)
# =====================================================================
def ejecutar_agente(reprocesar=False):
    """Un ciclo completo: conectar, leer, filtrar, analizar y escribir.

    Con reprocesar=True vuelve a analizar también las filas que ya tenían "SI".
    Devuelve False si no se pudo conectar a Google Sheets y True si el ciclo terminó.
    """
    accion = "Reclasificando todas las quejas" if reprocesar else "Revisando nuevas quejas"
    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {accion}...")

    # 1) Conectar y 2) Leer todas las filas
    try:
        hoja = conectar_sheets()
        filas = hoja.get_all_values()
    except Exception as error:
        print(f"  ERROR al conectar con Google Sheets: {error!r}")
        return False

    filas = filas[1:]  # Omitir la fila 1 (encabezados)
    ya_procesadas = 0
    procesadas = 0

    # La fila 2 de la hoja es el primer registro real
    for numero_fila, fila in enumerate(filas, start=2):
        # 3) Filtrar: si la columna F ya dice "SI", esa fila ya fue procesada
        if not reprocesar and obtener_celda(fila, COL_EJECUTADO).upper() == "SI":
            ya_procesadas += 1
            continue

        comentario = obtener_celda(fila, COL_COMENTARIO)
        if not comentario:
            continue  # Fila sin comentario: no hay nada que analizar

        nombre = obtener_celda(fila, COL_NOMBRE) or "Anónimo"

        # 4) Analizar con Gemini (si falla, se omite la fila y se sigue con la siguiente)
        try:
            analisis = analizar_con_gemini(comentario)
        except json.JSONDecodeError as error:
            print(f"  Fila {numero_fila}: Gemini no devolvió un JSON válido ({error}). Se omite.")
            continue
        except Exception as error:
            print(f"  Fila {numero_fila}: error al consultar Gemini ({error}). Se omite.")
            continue

        print(f"  Cliente: {nombre}")
        print(f"    Clasificación: {analisis['clasificacion']}")
        print(f"    Sentimiento:   {analisis['sentimiento']}")

        # 5) Escribir resultados en D, E y marcar F con "SI" (una sola escritura para las 3 celdas,
        #    así no se rebasa el límite de escrituras por minuto de Google Sheets)
        try:
            rango = (f"{gspread.utils.rowcol_to_a1(numero_fila, COL_CLASIFICACION)}:"
                     f"{gspread.utils.rowcol_to_a1(numero_fila, COL_EJECUTADO)}")
            hoja.update(
                range_name=rango,
                values=[[analisis["clasificacion"], analisis["sentimiento"], "SI"]],
            )
            print(f"    -> Resultados escritos en Google Sheets (fila {numero_fila}).")
            procesadas += 1
        except Exception as error:
            print(f"    ERROR al escribir en la hoja (fila {numero_fila}): {error}")

    if procesadas == 0:
        print("  No hay quejas nuevas por procesar.")
    else:
        print(f"  Ciclo terminado: {procesadas} queja(s) procesada(s).")

    if reprocesar:
        print(f"  Resumen: {procesadas} fila(s) reclasificada(s).")
    else:
        print(f"  Resumen: {ya_procesadas} fila(s) ya procesada(s) | {procesadas} fila(s) nueva(s) procesada(s).")
    return True


# =====================================================================
# 7. PUNTO DE ENTRADA (Paso 4.7 / Práctica 2: Pasos 1.3 y 1.4)
# =====================================================================
if __name__ == "__main__":
    # Validar la API Key
    if not GEMINI_API_KEY.strip() or GEMINI_API_KEY == TEXTO_MARCADOR:
        print("ERROR: Falta la API Key de Gemini (GEMINI_API_KEY).")
        print("       En la computadora: copia '.env.example' como '.env' y pega ahí tu clave.")
        print("       En GitHub Actions: revisa que exista el Secret 'GEMINI_API_KEY'.")
        raise SystemExit(1)

    # Validar el archivo de credenciales. Solo se necesita para iniciar sesión con el
    # navegador; en GitHub Actions el acceso llega en GOOGLE_TOKEN_JSON y este archivo no existe.
    if not os.getenv("GOOGLE_TOKEN_JSON", "").strip() and not os.path.exists(ARCHIVO_CREDENCIALES):
        print(f"ERROR: No se encontró '{os.path.basename(ARCHIVO_CREDENCIALES)}'.")
        print(f"       Colócalo en la carpeta: {CARPETA_SCRIPT}")
        print("       (En GitHub Actions: revisa que exista el Secret 'GOOGLE_TOKEN_JSON').")
        raise SystemExit(1)

    # Inicializar el cliente de Gemini (variable global usada por analizar_con_gemini).
    # Si Gemini está saturado (errores 429/503/504), reintenta hasta 3 veces con el mismo
    # modelo; después analizar_con_gemini() pasa al modelo de respaldo.
    cliente_gemini = genai.Client(
        api_key=GEMINI_API_KEY.strip(),
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=3, initial_delay=2, max_delay=30),
        ),
    )

    # Modo "una vez" (GitHub Actions) y modo "reprocesar": un solo ciclo y terminar.
    # El código de salida le indica a GitHub si salió bien (0 = verde) o mal (1 = rojo).
    reprocesar = "--reprocesar" in sys.argv
    if "--una-vez" in sys.argv or reprocesar:
        print("=" * 60)
        if reprocesar:
            print(" Agente de Soporte: reclasificar todas las filas (--reprocesar)")
        else:
            print(" Agente de Soporte: ejecución única (--una-vez)")
        print(f" Hoja: '{NOMBRE_HOJA}'")
        print("=" * 60)
        exito = ejecutar_agente(reprocesar=reprocesar)
        sys.exit(0 if exito else 1)

    print("=" * 60)
    print(" Agente de Soporte iniciado")
    print(f" Revisará '{NOMBRE_HOJA}' cada {INTERVALO_MINUTOS} minutos.")
    print(" Presiona Ctrl+C para detenerlo.")
    print("=" * 60)

    # Ejecutar inmediatamente, sin esperar el primer intervalo
    ejecutar_agente()

    # Programar ejecuciones automáticas
    schedule.every(INTERVALO_MINUTOS).minutes.do(ejecutar_agente)

    # Mantener el script corriendo: revisar tareas pendientes cada 30 segundos
    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        print("\nAgente detenido por el usuario.")
