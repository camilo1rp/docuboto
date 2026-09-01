# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

# storage-service — se construye con el contexto en la RAÍZ del repo (ADR-010),
# para instalar la librería compartida orpycamcp_common desde shared/ (auditoría
# inmutable ADR-008, soft-delete auditado D-04).
#   build: { context: ., dockerfile: services/storage-service/Dockerfile }
#
# Dos imágenes salen de este archivo (E10 Increment C2b):
#   - target por defecto ("base", lo que usa docker-compose.yml de dev): SIN
#     Java/veraPDF. `PDFA_VALIDATOR` por defecto es "stub" y aunque se pida
#     "verapdf" sin binario, la factory (`app/services/pdfa/factory.py`)
#     degrada honestamente a `StubPdfaValidator` -- dev sigue liviano.
#   - target "production" (infra/docker-compose.prod.yml): añade un JRE
#     headless + el CLI real de veraPDF y corre como usuario NO-root, porque
#     `SubprocessVeraPdfValidator` ejecuta ese binario sobre PDFs subidos por
#     cualquier tenant -- input NO confiable (ver
#     `app/services/pdfa/subprocess_verapdf.py` para el modelo de seguridad
#     completo del subprocess en sí).
FROM python:3.11-slim AS base
WORKDIR /app

# 0) Librerías nativas de WeasyPrint (Pango, Cairo, HarfBuzz), que es como el
# servicio GENERA los documentos de salida en PDF/A (E23, RF-PRO-03).
#
# **Van en `base` y no solo en `production`, al revés que veraPDF y Ghostscript,
# y es deliberado.** El test que prueba que lo generado es de verdad un
# documento de archivo tiene que EJECUTAR el renderizador y comprobar el PDF/A
# resultante; con las librerías solo en producción, ese test no correría nunca
# donde se corren los tests, y la única garantía sería un mock del renderizador
# —es decir, una afirmación sobre él en vez de una comprobación—. Son ~40 MB
# frente a los ~200 del JRE de veraPDF: proporcionado.
#
# Sin ellas el servicio ARRANCA igual: `render/factory.py` caza el fallo de
# import y declara que este despliegue no genera documentos (503), en vez de
# tumbar el proceso por una capacidad opcional.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libjpeg62-turbo \
        libffi8 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# 1) Librería compartida (capa cacheable; cambia poco).
COPY shared /shared
RUN pip install --no-cache-dir /shared

# 2) Dependencias del servicio.
COPY services/storage-service/requirements.txt services/storage-service/requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# 3) Código del servicio.
COPY services/storage-service/ .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8005"]

# ─── Stage: verapdf-builder (SOLO alcanzado por el target "production") ───
# Descarga e instala veraPDF con su instalador oficial (IzPack, modo
# "Automated installation" -- http://docs.verapdf.org/install/#autoinstall)
# en un stage APARTE: así curl/unzip, el .zip del instalador y el JDK del
# INSTALADOR (openjdk-21-jre-headless) NUNCA llegan a la imagen final -- solo
# se copia el resultado ya instalado (/opt/verapdf). El flujo de abajo (URL,
# nombre del script `verapdf-install`, y el XML de `auto-install.xml`) se
# verificó CONTRA UNA INSTALACIÓN REAL (no solo la documentación) al escribir
# este Dockerfile -- veraPDF 1.30.2, ver `app/services/pdfa/subprocess_verapdf.py`
# para cómo se parsea su salida JSON real.
FROM python:3.11-slim AS verapdf-builder
# El instalador oficial de veraPDF NO versiona la URL de descarga (siempre
# sirve el último release estable en esta ruta fija) -- no hay forma de
# pinnear una versión exacta vía URL sin mirror propio. Quien necesite
# reproducibilidad estricta debe mirrorear este .zip y sobrescribir
# VERAPDF_INSTALLER_URL con `--build-arg` apuntando a su copia.
ARG VERAPDF_INSTALLER_URL=https://software.verapdf.org/releases/verapdf-installer.zip
# JRE 21 (LTS), NO 17: la base `python:3.11-slim` sigue Debian testing/trixie,
# cuyos repos ya NO ofrecen `openjdk-17-jre-headless` (reemplazado por 21/25
# -- verificado en build real, `apt-cache search openjdk` solo lista 21/25).
# veraPDF 1.30.x corre sobre Java 21 sin problema (probado). Se fija el LTS
# (21), no el 25 más nuevo/menos probado por el ecosistema Java.
ARG JAVA_PACKAGE=openjdk-21-jre-headless

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        "$JAVA_PACKAGE" curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/verapdf-install

# El XML de "Automated installation" NO viene empaquetado en el .zip del
# instalador -- hay que escribirlo (ver la documentación oficial, sección
# enlazada arriba). `installpath` fija el destino; el panel `PacksPanel`
# selecciona SOLO "veraPDF GUI" (contiene los jars del núcleo/motor de
# validación, pese al nombre) y "veraPDF CLI" (el wrapper `verapdf`/`verapdf.bat`)
# -- se omiten el modelo de validación en fuente, la documentación y los
# plugins de ejemplo (innecesarios para invocar el CLI vía subprocess).
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
    # El .zip extrae a un único directorio "verapdf-greenfield-<version>/"
    # (el glob evita hardcodear la versión, que no aparece en la URL) --
    # dentro va el script `verapdf-install` (SIN extensión en Linux/Mac).
    && cd verapdf-greenfield-*/ \
    && chmod +x verapdf-install \
    && ./verapdf-install ../auto-install.xml \
    && cd .. \
    && rm -rf verapdf-installer.zip verapdf-greenfield-*/ \
    && chmod -R a+rX /opt/verapdf \
    # Falla explícito en BUILD (fail-closed) si el instalador no dejó el
    # binario esperado -- mejor un build roto que un despliegue silencioso
    # sin veraPDF (que la factory degradaría a stub sin que nadie lo note).
    && test -x /opt/verapdf/verapdf

# ─── Stage: production (deploy con veraPDF REAL vía subprocess) ───────────
FROM base AS production
ARG JAVA_PACKAGE=openjdk-21-jre-headless

# Runtime que el wrapper shell `/opt/verapdf/verapdf` necesita en PATH para
# invocar su JVM (el propio veraPDF ya viene instalado desde el stage
# anterior) -- mismo paquete que verapdf-builder, ver el comentario ahí.
# `ghostscript` es el CONVERTIDOR de la migración de formato contra la
# obsolescencia (E10, RF-PRE-04; Decreto 2609/2012 art. 32). Va en el mismo
# `apt-get` que la JVM porque comparten motivo: son las dos herramientas
# externas que el servicio necesita para preservar de verdad.
#
# **Sin él NO hay migración de formato, y eso es correcto**: la factory
# (`app/services/migracion/factory.py`) devuelve `None` y el endpoint responde
# 503. A diferencia del validador PDF/A, aquí no hay stub al que degradar — un
# validador puede declarar honestamente «no evaluado», pero un migrador falso
# tendría que inventarse el archivo.
RUN apt-get update \
    && apt-get install -y --no-install-recommends "$JAVA_PACKAGE" ghostscript \
    && rm -rf /var/lib/apt/lists/*

COPY --from=verapdf-builder /opt/verapdf /opt/verapdf

# Config de despliegue: ruta ABSOLUTA al binario (la factory exige eso para
# activar `SubprocessVeraPdfValidator` en vez de degradar a stub, ver
# `app/services/pdfa/factory.py::_resolve_verapdf_binary`).
ENV RENDER_ENGINE=weasyprint \
    VERAPDF_BINARY=/opt/verapdf/verapdf \
    PDFA_VALIDATOR=verapdf \
    PRESERVATION_PDFA_VALIDATION_ENABLED=true \
    GHOSTSCRIPT_BINARY=/usr/bin/gs \
    FORMAT_MIGRATOR=ghostscript

# Usuario NO-root: `SubprocessVeraPdfValidator` ejecuta un binario Java sobre
# PDFs subidos por cualquier tenant (input NO confiable) -- si el proceso
# padre corriera como root, un 0-day en la JVM/veraPDF escalaría a root DEL
# CONTENEDOR, no solo del proceso. `/app` (código+venv del servicio) y
# `/opt/verapdf` quedan de lectura/ejecución para este usuario; los
# temporales por-invocación del validador van a `/tmp` (mkdtemp 0700, ver
# `subprocess_verapdf.py`), que ya es escribible para cualquier usuario.
RUN groupadd --system orpycamcp \
    && useradd --system --gid orpycamcp --home-dir /app --shell /usr/sbin/nologin orpycamcp \
    && chown -R orpycamcp:orpycamcp /app
USER orpycamcp

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8005"]
