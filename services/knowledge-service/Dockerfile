# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

# knowledge-service — se construye con el contexto en la RAÍZ del repo (ADR-010),
# para poder instalar la librería compartida orpycamcp_common desde shared/ (E21:
# auditoría inmutable + sobre de eventos consumidos del worker de ingesta).
#   build: { context: ., dockerfile: services/knowledge-service/Dockerfile }
FROM python:3.11-slim
WORKDIR /app

# 1) Librería compartida (capa cacheable; cambia poco).
COPY shared /shared
RUN pip install --no-cache-dir /shared

# 2) Dependencias del servicio.
#
# WITH_ML controla la CAPA ML OPCIONAL (`sentence-transformers` → torch CPU, ~1,5 GB), que
# respalda `EMBEDDING_PROVIDER=local_st` — el proveedor de embeddings soberano. El DEFAULT
# es 1: `docker build` sin argumentos produce la imagen completa, así que una instalación
# tenant local no cambia en nada.
#
# WITH_ML=0 produce una imagen ligera (~150 MB de deps) para máquinas que no pueden ejecutar
# modelos locales. El CÓDIGO es idéntico en ambos casos: `embeddings.py` importa
# `sentence_transformers` de forma perezosa (`_ensure_loaded`), nunca al importar el módulo.
# Con WITH_ML=0 el servicio arranca y opera con `EMBEDDING_PROVIDER` = `stub` o `ollama`;
# si se dejara en `local_st`, el guard fail-closed de `main.py::lifespan` aborta el arranque
# con ImportError — deliberado: nunca degrada en silencio.
ARG WITH_ML=1
COPY services/knowledge-service/requirements.txt services/knowledge-service/requirements.lock ./
COPY services/knowledge-service/requirements-ml.txt services/knowledge-service/requirements-ml.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
 && if [ "$WITH_ML" = "1" ]; then pip install --no-cache-dir -r requirements-ml.lock; fi

# 3) Código del servicio.
COPY services/knowledge-service/ .

# Usuario NO-root. Cada uno de estos servicios procesa entrada que no controla
# —cuerpos HTTP, XML, PDF, correo, eventos de Redis—, así que un fallo de memoria
# o una deserialización insegura en cualquier dependencia escalaría a root DEL
# CONTENEDOR y no solo del proceso. En auth-service pesa además que las claves de
# cifrado (KEK del 2FA y de la firma personal) viven en su memoria.
#
# `/app` queda de solo lectura para este usuario: el servicio no escribe en su
# propio árbol, y lo temporal va a `/tmp`. Mismo patrón que storage-service, que
# ya lo hacía por el subproceso de veraPDF.
RUN groupadd --system orpycamcp \
    && useradd --system --gid orpycamcp --home-dir /app --shell /usr/sbin/nologin orpycamcp \
    && chown -R orpycamcp:orpycamcp /app
USER orpycamcp

# `--no-access-log`: el registro de acceso de uvicorn incluye la CADENA DE
# CONSULTA, y ahí viajan números de radicado, asuntos y términos de
# búsqueda. El middleware propio de este proyecto la omite a propósito
# —lo dice su docstring— y el de uvicorn la reponía por detrás, así que la
# garantía escrita no se cumplía. Se apaga el de uvicorn, no el propio:
# la traza estructurada sigue emitiéndose, sin el contenido documental.
CMD ["uvicorn", "app.main:app", "--no-access-log", "--host", "0.0.0.0", "--port", "8011"]
