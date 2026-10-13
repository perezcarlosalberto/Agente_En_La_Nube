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

# Modelo de Gemini que usa el agente.
# El PDF indica "gemini-2.5-flash", pero Google ya no lo ofrece a usuarios nuevos.
MODELO_GEMINI = "gemini-3.1-flash-lite"   # Gemini 3.1 Flash Lite

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
def analizar_con_gemini(comentario):
    """Envía el comentario a Gemini y devuelve {'clasificacion', 'sentimiento'}."""
    # Instrucción del sistema: el "rol" del agente
    instruccion_sistema = (
        "Eres un analista de soporte al cliente. Tu única tarea es devolver "
        "una respuesta en formato JSON válido, sin texto adicional, sin "
        "explicaciones y sin bloques de código."
    )

    # Instrucción del usuario: el comentario y lo que queremos obtener
    instruccion_usuario = f"""Analiza el siguiente comentario de un cliente:

"{comentario}"

1. Clasifícalo en UNA sola de estas categorías: Ventas, Soporte Técnico o Logística.
2. Detecta su sentimiento: Positivo, Negativo o Neutro.

Responde únicamente con un JSON con esta estructura exacta:
{{"clasificacion": "<categoría>", "sentimiento": "<sentimiento>"}}"""

    # Llamada a la API de Gemini
    respuesta = cliente_gemini.models.generate_content(
        model=MODELO_GEMINI,
        contents=instruccion_usuario,
        config=types.GenerateContentConfig(
            system_instruction=instruccion_sistema,
            response_mime_type="application/json",  # refuerza que responda JSON puro
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

    return {
        "clasificacion": resultado.get("clasificacion", "Sin clasificar"),
        "sentimiento": resultado.get("sentimiento", "Sin sentimiento"),
    }


# =====================================================================
# 6. AGENTE PRINCIPAL (Paso 4.6)
# =====================================================================
def ejecutar_agente():
    """Un ciclo completo: conectar, leer, filtrar, analizar y escribir.

    Devuelve False si no se pudo conectar a Google Sheets y True si el ciclo terminó.
    """
    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Revisando nuevas quejas...")

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
        if obtener_celda(fila, COL_EJECUTADO).upper() == "SI":
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

        # 5) Escribir resultados en D, E y marcar F con "SI"
        try:
            hoja.update_cell(numero_fila, COL_CLASIFICACION, analisis["clasificacion"])
            hoja.update_cell(numero_fila, COL_SENTIMIENTO, analisis["sentimiento"])
            hoja.update_cell(numero_fila, COL_EJECUTADO, "SI")
            print(f"    -> Resultados escritos en Google Sheets (fila {numero_fila}).")
            procesadas += 1
        except Exception as error:
            print(f"    ERROR al escribir en la hoja (fila {numero_fila}): {error}")

    if procesadas == 0:
        print("  No hay quejas nuevas por procesar.")
    else:
        print(f"  Ciclo terminado: {procesadas} queja(s) procesada(s).")

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
    # Si Gemini está saturado (errores 429/503/504), reintenta solo hasta 5 veces.
    cliente_gemini = genai.Client(
        api_key=GEMINI_API_KEY.strip(),
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=5, initial_delay=2, max_delay=30),
        ),
    )

    # Modo "una vez" (GitHub Actions): un solo ciclo y terminar.
    # El código de salida le indica a GitHub si salió bien (0 = verde) o mal (1 = rojo).
    if "--una-vez" in sys.argv:
        print("=" * 60)
        print(" Agente de Soporte: ejecución única (--una-vez)")
        print(f" Hoja: '{NOMBRE_HOJA}'")
        print("=" * 60)
        exito = ejecutar_agente()
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
