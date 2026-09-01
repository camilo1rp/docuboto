# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Barrido periódico de migración de formato — E10, RF-PRE-04.

Cierra el pendiente que dejó declarado la migración a PDF/A: la conversión
existía y **solo se disparaba a mano, archivo por archivo**. Un archivo de una
entidad tiene cientos de miles de ficheros; una capacidad que hay que invocar
uno a uno es una capacidad construida y no ofrecida.

── La política la declara la ENTIDAD, no este código ────────────────────────

Qué formato está en riesgo de obsolescencia es un juicio archivístico que
depende del parque documental de cada institución y de su plan de preservación
(PINAR, Ac. AGN 003/2015). Por eso el job **no tiene una lista propia** y
**vacía significa no migrar nada**: con la política sin declarar el barrido
recorre los tenants y no toca un solo archivo.

Dónde está declarada, y en qué orden se lee:

1. **El plan de preservación de la entidad** (`preservacion_plan.formatos_origen`,
   RF-PRE-01) — el sitio propio. Lo escribe el archivista desde
   `/admin/preservacion`, queda versionado append-only y dice quién lo declaró
   y cuándo. Es el otro extremo de `formatos_destino`, que ya vivía ahí.
2. **El despliegue** (`FORMAT_MIGRATION_MIME_TYPES`) — solo si el plan de esa
   entidad no dice nada. Es un valor por defecto de la instalación: en una
   instalación de una sola entidad, el que despliega ES la entidad; en una
   compartida, cada entidad lo sustituye declarando su plan.

Nunca al revés. Que la política viviera **solo** en una variable de entorno era
justamente lo que contradecía el párrafo de arriba: una variable de entorno es
la misma para todas las entidades de la instalación, no la puede tocar el
archivista, no queda versionada y no dice quién la puso.

Es el mismo criterio que el barrido de disposición —que cuenta y avisa pero no
elimina— con una diferencia que conviene tener presente: **migrar no destruye**.
El original se conserva intacto (Decreto 2609/2012 art. 32) y el derivado
convive con él. Lo peligroso aquí no es convertir de más; es creer que se
convirtió. De ahí que el job no promueva nada que no haya pasado el validador,
igual que la ruta manual.

── Por qué no reintenta lo que falló, y sí lo que no se pudo verificar ──────

`fallido` dice algo del ARCHIVO: no se pudo convertir, o el resultado no era
conforme. Reintentarlo cada día no lo va a arreglar y cuesta un subproceso por
archivo y barrida; hace falta que alguien lo mire.

`no_verificado` dice algo del DESPLIEGUE: se convirtió y no había validador con
qué comprobarlo. Ese sí se reintenta — el día que veraPDF esté instalado, la
barrida siguiente lo resuelve sola sin que nadie tenga que acordarse.

── Aislamiento ──────────────────────────────────────────────────────────────

Un fallo queda contenido en su archivo, y una barrida fallida en su tenant: que
un PDF corrupto reviente su conversión no puede dejar sin migrar al resto del
archivo, ni a las demás entidades. Mismo criterio que las otras barridas.
"""

from __future__ import annotations

import logging

import asyncpg

from app.core.config import settings
from app.core.database import get_pool, tenant_schema
from app.repositories.preservacion_repository import PreservacionRepository
from app.services.format_migration_service import (
    FormatMigrationService,
    MigradorNoDisponibleError,
)

logger = logging.getLogger(__name__)


async def list_active_tenants(pool: asyncpg.Pool) -> list[str]:
    """Slugs de los tenants activos.

    Mismas columnas que el barrido de archive-service
    (`app/jobs/indice_reconciliation.py`), verificadas contra las migraciones
    de tenant-service.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT slug FROM public.tenants "
            "WHERE status = 'active' AND provisioning_status = 'active'"
        )
    return [row["slug"] for row in rows]


async def run_once(*, descargar, subir_factory) -> dict[str, int]:
    """Una barrida sobre todos los tenants activos.

    `descargar` y `subir_factory` se inyectan por la misma razón que en el
    servicio: el job decide **a quién** le toca migrar, no cómo se hablan MinIO
    y la tabla de archivos. Así el orden de los pasos —donde está la
    corrección— se prueba sin levantar un almacén.

    `subir_factory(conn, slug)` es una FÁBRICA y no una función suelta porque
    subir el derivado necesita la conexión **de ese tenant**: una función
    construida una vez fuera del bucle escribiría los archivos de todas las
    entidades en el schema de la primera.
    """
    resumen = {
        "tenants": 0,
        "sin_politica": 0,
        "candidatos": 0,
        "migrados": 0,
        "fallidos": 0,
    }

    por_defecto = settings.format_migration_mime_list
    destino = f"PDF/A-{settings.pdfa_profile}"
    pool = get_pool()

    for slug in await list_active_tenants(pool):
        try:
            async with pool.acquire() as conn:
                await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
                repo = PreservacionRepository(conn, slug)

                # La política se resuelve POR ENTIDAD y en este orden: lo que
                # diga su plan de preservación vigente manda; si su plan no
                # declara nada, cae al valor del despliegue. Nunca al revés —
                # el plan es donde el archivista de esa entidad puede escribir,
                # y donde el cambio queda versionado y con autor.
                formatos = await repo.current_formatos_origen()
                fuente = "plan"
                if not formatos:
                    formatos, fuente = por_defecto, "despliegue"
                if not formatos:
                    # Ni el plan ni el despliegue dicen nada: esta entidad no
                    # ha declarado política y no se le toca un solo archivo.
                    resumen["sin_politica"] += 1
                    continue

                pendientes = await repo.list_pendientes_migracion(
                    formatos,
                    formato_destino=destino,
                    limit=settings.format_migration_batch_size,
                )
                if not pendientes:
                    continue

                logger.info(
                    "Migración de formato: tenant=%s política=%s (%s), %d candidatos",
                    slug,
                    fuente,
                    ", ".join(formatos),
                    len(pendientes),
                )

                resumen["tenants"] += 1
                resumen["candidatos"] += len(pendientes)
                servicio = FormatMigrationService(conn, slug)

                for fila in pendientes:
                    try:
                        registro = await servicio.migrar_a_pdfa(
                            fila["id"],
                            perfil=settings.pdfa_profile,
                            # Sin persona detrás: lo hizo la preservación
                            # programada, y el registro tiene que poder decirlo.
                            actor=None,
                            descargar=descargar,
                            subir=subir_factory(conn, slug),
                        )
                    except MigradorNoDisponibleError:
                        # No hay convertidor en este despliegue. Se corta la
                        # barrida ENTERA en vez de intentarlo archivo por
                        # archivo: la causa es del despliegue y no va a cambiar
                        # a mitad del recorrido.
                        logger.info(
                            "Migración de formato: sin convertidor configurado; "
                            "barrida omitida."
                        )
                        return resumen
                    except Exception:
                        resumen["fallidos"] += 1
                        logger.warning(
                            "Migración de formato: falló el archivo %s en tenant=%s",
                            fila["id"],
                            slug,
                            exc_info=True,
                        )
                        continue

                    if registro.get("estado") == "migrado":
                        resumen["migrados"] += 1
                    else:
                        resumen["fallidos"] += 1
        except Exception:
            logger.warning(
                "Migración de formato: barrida fallida para tenant=%s", slug, exc_info=True
            )

    logger.info("Migración de formato: %s", resumen)
    return resumen
