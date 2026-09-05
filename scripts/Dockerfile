# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

# ── Imagen de los guiones de operación ──────────────────────────────────────
#
# Los guiones de `scripts/` (siembra, corpus, perfiles) necesitan `httpx` y
# `asyncpg`, y el host no los tiene ni debe tenerlos: la regla del proyecto es
# que todo corre dentro de Docker.
#
# **Por qué una imagen propia y no la de un servicio.** Reutilizar, por ejemplo,
# la de auth-service no cuesta nada hoy y ata los guiones de operación a las
# dependencias de otro: el día que ese servicio deje de necesitar `httpx`, el
# entorno de pruebas dejaría de poder reconstruirse y nada lo avisaría hasta que
# alguien lo intentara. Dos dependencias declaradas aquí valen más que un
# acoplamiento invisible.
#
# **Por qué los guiones corren DENTRO de la red.** No es una comodidad: sus
# valores por defecto ya son nombres internos (`postgres:5432`,
# `keycloak:8080`), porque `seed_demo.py` habla con Postgres por asyncpg y con
# Keycloak por su API de administración, y ninguno de los dos está publicado
# igual desde fuera.
#
# **Lo que esta imagen NO hace**: emitir certificados. `gen-user-cert.sh` corre
# en el HOST a propósito — la clave de la sub-CA de usuarios no se monta en
# ningún contenedor, y esa invariante la declara el propio script.
FROM python:3.11-slim

# Sin `requirements.lock` propio: son dos dependencias directas de una
# herramienta de desarrollo, no de un servicio desplegable. Fijar las MAYORES
# evita la sorpresa de una incompatible sin imponer un lockfile más que
# mantener. Si algún día esto crece, lo que toca es el lock, no más pines.
RUN pip install --no-cache-dir "httpx>=0.27,<0.29" "asyncpg>=0.29,<0.31"

WORKDIR /repo

# El repo se monta `:ro` al invocar (ver `scripts/reset-entorno.sh`). Nada se
# copia dentro: los guiones cambian a menudo y una copia obligaría a reconstruir
# la imagen en cada edición — y, peor, dejaría correr una versión vieja sin que
# se notara.
ENTRYPOINT ["python"]
