# Práctica 3: Mi Agente en un Contenedor — receta de la imagen del Agente de Soporte
#
# Construir la imagen:    docker build -t agente-soporte .
# Ejecutar una vez:       docker run --rm --env-file .env --env-file token.env agente-soporte --una-vez
# Modo continuo:          docker run -d --name agente --restart unless-stopped --env-file .env --env-file token.env agente-soporte
#
# Los secretos (.env y token.env) NO entran a la imagen: se entregan con --env-file al ejecutar.

# 1) Imagen base de Docker Hub: un Linux mínimo con Python 3.12 ya instalado
FROM python:3.12-slim

# 2) Mostrar cada print() al instante; sin esto, docker logs aparece vacío o con retraso
ENV PYTHONUNBUFFERED=1

# 3) Carpeta de trabajo dentro del contenedor (Docker la crea si no existe)
WORKDIR /app

# 4) Copiar primero solo la lista de librerías: si después únicamente cambia el script,
#    Docker reutiliza desde la caché la capa de pip install (la más lenta)
COPY requirements.txt .

# 5) Instalar las librerías sin guardar los archivos temporales de pip (imagen más pequeña)
RUN pip install --no-cache-dir -r requirements.txt

# 6) Ahora sí, copiar el script del agente
COPY agente_soporte.py .

# 7) Comando que ejecuta el contenedor al arrancar. Lo que se escriba después del nombre
#    de la imagen en docker run se agrega al final (por ejemplo, --una-vez)
ENTRYPOINT ["python", "agente_soporte.py"]
