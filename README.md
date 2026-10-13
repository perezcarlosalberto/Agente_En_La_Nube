# Agente de Soporte en la Nube (GitHub Actions + Gemini)

Agente de inteligencia artificial que lee las quejas y sugerencias registradas en un
**Google Form**, las analiza con la **API de Gemini** (clasificación y sentimiento) y guarda
el resultado en **Google Sheets**, que funciona como base de datos.

Práctica 2 *"Mi Agente en la Nube"*: Desarrollo de Agentes Inteligentes, Unidad 2.
Es la continuación de la Práctica 1: el mismo agente, pero ahora lo ejecuta **GitHub Actions
cada 5 minutos**, aunque la computadora esté apagada.

## Funcionamiento

```
GitHub Actions  ──►  agente_soporte.py  ──►  Gemini AI  ──►  Google Sheets
(cron: 5 min)        (--una-vez)             (análisis)      (base de datos)
```

Cada ejecución del workflow:

1. **Percibe:** lee todas las filas de la hoja de cálculo.
2. **Filtra:** omite las filas que ya tienen `SI` en la columna F (su "memoria").
3. **Razona:** envía cada comentario nuevo a Gemini, que devuelve un JSON con:
   - **Clasificación:** `Ventas`, `Soporte Técnico` o `Logística`
   - **Sentimiento:** `Positivo`, `Negativo` o `Neutro`
4. **Actúa:** escribe la clasificación (D), el sentimiento (E) y `SI` (F).
5. **Termina:** muestra el resumen (filas ya procesadas y nuevas) y sale con código `0`
   (o `1` si no pudo conectarse a Google Sheets, y GitHub marca la ejecución en rojo).

### Estructura de la hoja de cálculo

| Columna | Encabezado | La llena |
|---|---|---|
| A | Marca temporal | Google Forms |
| B | Tu Nombre | Google Forms |
| C | Describe tu queja, sugerencia o comentario | Google Forms |
| D | Clasificación IA | El agente |
| E | Sentimiento IA | El agente |
| F | Agente Ejecutado | El agente (`SI`) |

## Estructura del proyecto

```
agente-soporte/
├── .github/workflows/agente.yml   # Workflow de GitHub Actions (cada 5 min + botón manual)
├── agente_soporte.py              # Script del agente
├── requirements.txt               # Dependencias que instala el runner
├── .env.example                   # Plantilla de la API Key (sin la clave real)
├── .gitignore                     # Excluye credenciales y archivos locales
└── README.md
```

Archivos locales que **no** se suben al repositorio (los excluye `.gitignore`):

| Archivo | Contenido | En GitHub Actions se reemplaza por |
|---|---|---|
| `.env` | API Key de Gemini | Secret `GEMINI_API_KEY` |
| `client_secrets.json` | Credenciales OAuth de Google Cloud | No se necesita |
| `token_autorizado.json` | Token de Google (con refresh token) | Secret `GOOGLE_TOKEN_JSON` |

## Ejecución en la computadora

```bash
pip install -r requirements.txt
```

1. Copiar `.env.example` como `.env` y pegar la API Key de Gemini:
   ```
   GEMINI_API_KEY=tu_clave_aqui
   ```
2. Colocar `client_secrets.json` junto a `agente_soporte.py`.
3. Ejecutar:

```bash
python agente_soporte.py            # modo normal: revisa cada minuto (Ctrl+C para detener)
python agente_soporte.py --una-vez  # un solo ciclo y termina (como en GitHub Actions)
```

La primera vez se abre el navegador para iniciar sesión con Google. El token se guarda en
`token_autorizado.json` y no se vuelve a pedir.

## Ejecución en GitHub Actions

El workflow `.github/workflows/agente.yml` se dispara:

- **`schedule`** con `cron: "*/5 * * * *"`: cada 5 minutos (hora UTC), el intervalo mínimo que
  permite GitHub. GitHub puede retrasar las ejecuciones programadas varios minutos.
- **`workflow_dispatch`**: botón **Run workflow** en la pestaña *Actions* para probarlo al momento.

Con `concurrency`, si una ejecución todavía no termina cuando llega la siguiente, la nueva espera
su turno, así que dos ejecuciones nunca procesan la misma queja al mismo tiempo.

Pasos del trabajo `ejecutar-agente` (en `ubuntu-latest`, límite de 10 minutos):

| # | Paso | Qué hace |
|---|---|---|
| 1 | Descargar el código | `actions/checkout@v4` |
| 2 | Instalar Python | `actions/setup-python@v5` con Python 3.12 |
| 3 | Instalar dependencias | `pip install -r requirements.txt` |
| 4 | Ejecutar el agente | `python agente_soporte.py --una-vez` con los Secrets como variables de entorno |

### Secrets del repositorio

En **Settings → Secrets and variables → Actions**:

| Secret | Valor |
|---|---|
| `GEMINI_API_KEY` | Solo la clave de Gemini (sin `GEMINI_API_KEY=` ni comillas) |
| `GOOGLE_TOKEN_JSON` | Contenido completo de `token_autorizado.json`, incluidas las llaves `{ }` |

### Duración del token de Google

Mientras la app de Google Cloud está en modo **Testing**, el token caduca a los 7 días y el
workflow falla con `invalid_grant`. Con la app publicada **En producción** el token es permanente.
Para renovarlo (rotar el secreto): borrar `token_autorizado.json`, ejecutar
`python agente_soporte.py --una-vez` en la computadora, iniciar sesión y pegar el contenido
nuevo en el Secret `GOOGLE_TOKEN_JSON` con **Update secret**.

### Cuota de minutos

Este repositorio es público, y en repositorios públicos GitHub Actions no descuenta minutos de la
cuota gratuita, por eso el agente puede correr cada 5 minutos. En un repositorio privado (2,000
minutos al mes, y cada ejecución cuenta como mínimo 1 minuto) cada 5 minutos serían unas 8,640
ejecuciones al mes y no cabrían; ahí habría que usar 30 minutos (unas 1,440 al mes), como indica la práctica.

En repositorios públicos GitHub desactiva los workflows programados si el repositorio pasa 60 días
sin actividad. Para reactivarlo basta con habilitarlo de nuevo en la pestaña *Actions* o hacer un commit.

## Solución de errores comunes

| Error | Solución |
|---|---|
| `Falta la API Key de Gemini` en los registros de GitHub | Revisar que el Secret se llame exactamente `GEMINI_API_KEY` |
| `No se encontró 'client_secrets.json'` en GitHub | Falta el Secret `GOOGLE_TOKEN_JSON` o su nombre está mal escrito |
| `invalid_grant: Token has been expired or revoked` | Renovar el token (ver arriba) y actualizar `GOOGLE_TOKEN_JSON` |
| `JSONDecodeError` al conectar con Google Sheets | El Secret `GOOGLE_TOKEN_JSON` está incompleto: copiar todo, con las llaves `{ }` |
| El workflow no aparece en la pestaña *Actions* | El archivo debe estar en `.github/workflows/` (con punto y en plural) |
| `Invalid workflow file` | Revisar la sangría del YAML: espacios, nunca tabuladores |
| `ModuleNotFoundError` en GitHub | Agregar la librería a `requirements.txt` y hacer push |
| `SpreadsheetNotFound` | El nombre de la hoja debe coincidir exactamente con `NOMBRE_HOJA` |
| `404 NOT_FOUND` (modelo) | Cambiar `MODELO_GEMINI` por un modelo disponible en AI Studio |
| `503 UNAVAILABLE` | Gemini está saturado; el agente reintenta y, si no, en la siguiente ejecución |

## Notas

- **Modelo:** el agente usa `gemini-3.1-flash-lite`, configurable en `MODELO_GEMINI`.
- **Reintentos:** si Gemini está saturado (errores 429, 503 o 504), la petición se reintenta
  hasta 5 veces; si aun así falla, la fila queda pendiente para la siguiente ejecución.
- **Visibilidad:** el repositorio es público para que cualquier persona pueda consultarlo.
  Los registros de cada ejecución (pestaña *Actions*) muestran el nombre de cada cliente con la
  clasificación y el sentimiento de su queja, así que también son visibles para todos. Los
  secretos no se exponen: viven en GitHub Secrets y en los registros aparecen como `***`.
