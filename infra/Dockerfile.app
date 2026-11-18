# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

# El proceso Docuboto: los nueve paquetes de dominio más `docuboto`, un uvicorn.
# Vive en `infra/` a propósito: `servicios_propios()` recorre `services/*/Dockerfile`
# y un Dockerfile ahí forzaría imagen de producción y fila en la matriz de CI
# antes de que el registro publique `app`.
#
# Sin JRE: el trabajo pesado de PDF/A vive en `infra/Dockerfile.worker`.
#
#   build: { context: ., dockerfile: infra/Dockerfile.app }
FROM python:3.11-slim

WORKDIR /app

# Librerías nativas de WeasyPrint (Pango, Cairo, HarfBuzz). Mismo criterio que
# `storage-service` target `base`: el render de PDF/A tiene que EJECUTARSE en
# el proceso, no degradar a 503 porque las libs solo estaban en otra imagen.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libjpeg62-turbo \
        libffi8 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY shared /shared
RUN pip install --no-cache-dir /shared

COPY services/docuboto/requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY services/docuboto/docuboto /app/docuboto
COPY services/docuboto/auth /app/auth
COPY services/docuboto/tenant /app/tenant
COPY services/docuboto/document /app/document
COPY services/docuboto/archive /app/archive
COPY services/docuboto/storage /app/storage
COPY services/docuboto/workflow /app/workflow
COPY services/docuboto/notification /app/notification
COPY services/docuboto/signature /app/signature
COPY services/docuboto/knowledge /app/knowledge
COPY services/docuboto/migrations /app/migrations

ENV PYTHONPATH=/app

RUN groupadd --system orpycamcp \
    && useradd --system --gid orpycamcp --home-dir /app --shell /usr/sbin/nologin orpycamcp \
    && chown -R orpycamcp:orpycamcp /app
USER orpycamcp

# XSD live next to the service trees, not inside the Python package.
# `archive.services.index_xsd` and `storage.services.aip_builder` resolve them at `/app/schemas`.
COPY --chown=orpycamcp:orpycamcp services/docuboto/schemas/ /app/schemas/

# `--no-access-log`: el registro de acceso de uvicorn incluye la CADENA DE
# CONSULTA. El middleware propio la omite; el de uvicorn la reponía.
# La versión del lanzamiento (ADR-053 §3): la expone /health y la anota el migrador.
ARG ORPYCA_VERSION=dev
ENV ORPYCA_VERSION=${ORPYCA_VERSION}
CMD ["uvicorn", "docuboto.main:app", "--no-access-log", "--host", "0.0.0.0", "--port", "8000"]
