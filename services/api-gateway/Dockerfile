# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

FROM python:3.11-slim
WORKDIR /app

# La librería compartida se instala igual que en los otros once servicios.
#
# El gateway era el único que no la tenía, y por eso el contexto de construcción
# pasa a ser la raíz del repositorio. La necesita para FIRMAR las cabeceras de
# identidad que inyecta: sin firma, esas cabeceras son una afirmación y no una
# identidad, y dentro de la red cualquiera podía emitirlas.
COPY shared /shared
RUN pip install --no-cache-dir /shared

COPY services/api-gateway/requirements.txt services/api-gateway/requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY services/api-gateway/ .
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
CMD ["uvicorn", "app.main:app", "--no-access-log", "--host", "0.0.0.0", "--port", "8080"]
