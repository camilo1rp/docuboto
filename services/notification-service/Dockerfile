# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

# notification-service — contexto de build en la RAÍZ del repo (ADR-010) para
# instalar la librería compartida orpycamcp_common desde shared/ (sobre canónico
# de eventos: consume orpycamcp.document.events con el mismo contrato del emisor).
#   build: { context: ., dockerfile: services/notification-service/Dockerfile }
FROM python:3.11-slim

WORKDIR /app

# 1) Librería compartida (capa cacheable; cambia poco).
COPY shared /shared
RUN pip install --no-cache-dir /shared

# 2) Dependencias del servicio.
COPY services/notification-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3) Código del servicio.
COPY services/notification-service/ .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8007"]
