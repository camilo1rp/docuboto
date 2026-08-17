# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

# whisper-service — transcripción de voz EN LA PROPIA ENTIDAD (ADR-020).
#
# Se construye con el contexto en el directorio del servicio: a diferencia de los
# demás, NO instala `shared/` porque no escribe en `audit_log` ni publica eventos
# — transcribe un audio y devuelve texto, sin tocar base de datos. Añadir la
# librería compartida "por si acaso" sería superficie que nadie usa.
#
# NO lleva `openai-whisper`, que arrastra torch (varios GB). `faster-whisper` usa
# CTranslate2 y corre en CPU: es lo que permite que una entidad sin GPU transcriba
# su propia voz en vez de mandarla fuera.
FROM python:3.11-slim
WORKDIR /app

# ffmpeg lo necesita el decodificador de audio: el navegador graba en
# `webm/opus` y sin él no hay nada que transcribir. Es la única dependencia de
# sistema.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY services/whisper-service/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock

COPY services/whisper-service/app ./app

# El modelo NO se descarga al construir la imagen: se baja al primer arranque y
# queda en el volumen `whisper-models`. Empaquetarlo ataría la imagen a un modelo
# concreto y la haría crecer cientos de megabytes para quien luego elija otro.
ENV WHISPER_CACHE_DIR=/models
VOLUME ["/models"]

EXPOSE 8012
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8012"]
