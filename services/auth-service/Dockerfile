# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

# auth-service — se construye con el contexto en la RAÍZ del repo (ADR-010),
# para poder instalar la librería compartida orpycamcp_common desde shared/.
#   build: { context: ., dockerfile: services/auth-service/Dockerfile }
FROM python:3.11-slim

WORKDIR /app

# 1) Librería compartida (capa cacheable; cambia poco).
COPY shared /shared
RUN pip install --no-cache-dir /shared

# 2) Dependencias del servicio.
COPY services/auth-service/requirements.txt services/auth-service/requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# 3) Código del servicio.
COPY services/auth-service/ .

# Run tests: docker compose run --rm auth-service pytest
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

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
