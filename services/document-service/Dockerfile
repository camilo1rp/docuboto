# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

# document-service — se construye con el contexto en la RAÍZ del repo (ADR-010),
# para instalar la librería compartida orpycamcp_common desde shared/ (auditoría
# canónica + sobre de eventos Redis Streams).
#   build: { context: ., dockerfile: services/document-service/Dockerfile }
FROM python:3.11-slim

WORKDIR /app

# 1) Librería compartida (capa cacheable; cambia poco).
COPY shared /shared
RUN pip install --no-cache-dir /shared

# 2) Dependencias del servicio.
COPY services/document-service/requirements.txt services/document-service/requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# 3) Código del servicio.
COPY services/document-service/ .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
