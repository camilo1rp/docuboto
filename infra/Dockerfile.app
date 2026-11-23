# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

# La imagen de la app (ADR-047): los nueve paquetes de dominio más `docuboto`,
# un uvicorn, y lo que antes solo llevaba el `worker` —JRE, veraPDF y
# Ghostscript—, porque sus bucles corren ahora dentro del proceso. Vive en
# `infra/` y la construcción parte de la raíz del repositorio:
#
#   build: { context: ., dockerfile: infra/Dockerfile.app }

# El instalador de veraPDF va en su propia etapa: curl, unzip y el JDK del
# instalador no llegan a la imagen final.
FROM python:3.11-slim AS verapdf-builder
ARG VERAPDF_INSTALLER_URL=https://software.verapdf.org/releases/verapdf-installer.zip
ARG JAVA_PACKAGE=openjdk-21-jre-headless

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        "$JAVA_PACKAGE" curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/verapdf-install

RUN cat > auto-install.xml <<'XML_EOF'
<AutomatedInstallation langpack="eng">
    <com.izforge.izpack.panels.htmlhello.HTMLHelloPanel id="welcome"/>
    <com.izforge.izpack.panels.target.TargetPanel id="install_dir">
        <installpath>/opt/verapdf</installpath>
    </com.izforge.izpack.panels.target.TargetPanel>
    <com.izforge.izpack.panels.packs.PacksPanel id="sdk_pack_select">
        <pack index="0" name="veraPDF GUI" selected="true"/>
        <pack index="1" name="veraPDF CLI" selected="true"/>
        <pack index="2" name="veraPDF Validation model" selected="false"/>
        <pack index="3" name="veraPDF Documentation" selected="false"/>
        <pack index="4" name="veraPDF Sample Plugins" selected="false"/>
    </com.izforge.izpack.panels.packs.PacksPanel>
    <com.izforge.izpack.panels.install.InstallPanel id="install"/>
    <com.izforge.izpack.panels.finish.FinishPanel id="finish"/>
</AutomatedInstallation>
XML_EOF

RUN curl -fsSL "$VERAPDF_INSTALLER_URL" -o verapdf-installer.zip \
    && unzip -q verapdf-installer.zip \
    && cd verapdf-greenfield-*/ \
    && chmod +x verapdf-install \
    && ./verapdf-install ../auto-install.xml \
    && cd .. \
    && rm -rf verapdf-installer.zip verapdf-greenfield-*/ \
    && chmod -R a+rX /opt/verapdf \
    && test -x /opt/verapdf/verapdf

FROM python:3.11-slim

ARG JAVA_PACKAGE=openjdk-21-jre-headless
WORKDIR /app

# Librerías nativas de WeasyPrint (Pango, Cairo, HarfBuzz): el render de PDF/A
# se EJECUTA en el proceso. JRE y Ghostscript: veraPDF y la migración de
# formato, que antes vivían en el `worker`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libjpeg62-turbo \
        libffi8 fonts-dejavu-core "$JAVA_PACKAGE" ghostscript \
    && rm -rf /var/lib/apt/lists/*

COPY --from=verapdf-builder /opt/verapdf /opt/verapdf

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

ENV PYTHONPATH=/app \
    RENDER_ENGINE=weasyprint \
    VERAPDF_BINARY=/opt/verapdf/verapdf \
    GHOSTSCRIPT_BINARY=/usr/bin/gs

RUN groupadd --system orpycamcp \
    && useradd --system --gid orpycamcp --home-dir /app --shell /usr/sbin/nologin orpycamcp \
    && chown -R orpycamcp:orpycamcp /app
USER orpycamcp

# XSD junto a los paquetes, no dentro: `archive.services.index_xsd` y
# `storage.services.aip_builder` los resuelven en `/app/schemas`.
COPY --chown=orpycamcp:orpycamcp services/docuboto/schemas/ /app/schemas/

# `--no-access-log`: el registro de acceso de uvicorn incluye la CADENA DE
# CONSULTA. El middleware propio la omite; el de uvicorn la reponía.
# La versión del lanzamiento (ADR-053 §3): la expone /health y la anota el migrador.
ARG ORPYCA_VERSION=dev
ENV ORPYCA_VERSION=${ORPYCA_VERSION}
CMD ["uvicorn", "docuboto.main:app", "--no-access-log", "--host", "0.0.0.0", "--port", "8000"]
