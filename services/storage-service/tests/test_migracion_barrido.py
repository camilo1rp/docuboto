# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Barrido periódico de migración de formato — E10, RF-PRE-04.

Lo que se prueba aquí es **de quién es la decisión**. La conversión y sus
garantías —fixity antes, validación después, original intacto— ya están
probadas en `test_migracion_formato.py`; el barrido añade una sola cosa nueva y
es quién elige qué se convierte.

La respuesta tiene que ser: la entidad, en su política declarada. Un job que lo
decidiera por su cuenta estaría convirtiendo el archivo de alguien según una
lista adivinada por el fabricante.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.jobs import format_migration
from app.jobs.format_migration import run_once


async def _nada(*_a, **_k):
    raise AssertionError("no debía llegar a convertir")


def _factory_prohibida(_conn, _slug):
    return _nada


def _pool_con(conn):
    """`pool.acquire()` es un gestor de contexto asíncrono, no una corrutina:
    un `AsyncMock` devuelve lo segundo y el `async with` revienta *dentro* del
    `except` del job, así que la barrida saldría en cero sin decir por qué."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool


@pytest.mark.asyncio
async def test_SIN_politica_declarada_no_se_migra_nada() -> None:
    """**El comportamiento por defecto, y el que más importa.** Con la lista
    vacía el job no toca un solo archivo: qué formato está en riesgo es un
    juicio archivístico de cada institución y de su PINAR, no del código.

    Ni siquiera se enumeran los tenants — el corte es antes de mirar la base."""
    with (
        patch.object(format_migration.settings, "format_migration_mime_types", ""),
        patch.object(format_migration, "get_pool") as pool,
    ):
        resumen = await run_once(descargar=_nada, subir_factory=_factory_prohibida)

    assert resumen == {"tenants": 0, "candidatos": 0, "migrados": 0, "fallidos": 0}
    pool.assert_not_called()


@pytest.mark.asyncio
async def test_con_politica_se_migra_lo_que_esa_politica_dice() -> None:
    """El control positivo. Sin él, un job roto que no migrara nunca pasaría el
    test de arriba y parecería correcto."""
    file_id = uuid.uuid4()
    conn = AsyncMock()
    pool = _pool_con(conn)

    migrar = AsyncMock(return_value={"estado": "migrado"})

    with (
        patch.object(
            format_migration.settings, "format_migration_mime_types", "application/msword"
        ),
        patch.object(format_migration, "get_pool", return_value=pool),
        patch.object(format_migration, "list_active_tenants", AsyncMock(return_value=["demo"])),
        patch.object(
            format_migration.PreservacionRepository,
            "list_pendientes_migracion",
            AsyncMock(return_value=[{"id": file_id, "mime_type": "application/msword"}]),
        ),
        patch.object(format_migration.FormatMigrationService, "migrar_a_pdfa", migrar),
    ):
        resumen = await run_once(descargar=_nada, subir_factory=lambda c, s: _nada)

    assert resumen["migrados"] == 1
    assert migrar.await_args.args[0] == file_id
    # Sin persona detrás: lo hizo la preservación programada, y el registro
    # tiene que poder decirlo.
    assert migrar.await_args.kwargs["actor"] is None


@pytest.mark.asyncio
async def test_un_archivo_que_revienta_no_se_lleva_a_los_demas() -> None:
    """Aislamiento por archivo. Que un PDF corrupto reviente su conversión no
    puede dejar sin migrar al resto del archivo de la entidad."""
    conn = AsyncMock()
    pool = _pool_con(conn)

    migrar = AsyncMock(side_effect=[RuntimeError("pdf ilegible"), {"estado": "migrado"}])

    with (
        patch.object(
            format_migration.settings, "format_migration_mime_types", "application/msword"
        ),
        patch.object(format_migration, "get_pool", return_value=pool),
        patch.object(format_migration, "list_active_tenants", AsyncMock(return_value=["demo"])),
        patch.object(
            format_migration.PreservacionRepository,
            "list_pendientes_migracion",
            AsyncMock(return_value=[{"id": uuid.uuid4()}, {"id": uuid.uuid4()}]),
        ),
        patch.object(format_migration.FormatMigrationService, "migrar_a_pdfa", migrar),
    ):
        resumen = await run_once(descargar=_nada, subir_factory=lambda c, s: _nada)

    assert resumen == {"tenants": 1, "candidatos": 2, "migrados": 1, "fallidos": 1}


@pytest.mark.asyncio
async def test_sin_convertidor_la_barrida_se_corta_ENTERA() -> None:
    """La causa es del despliegue y no va a cambiar a mitad del recorrido:
    intentarlo archivo por archivo gastaría una descarga por cada uno para
    llegar al mismo 503."""
    from app.services.format_migration_service import MigradorNoDisponibleError

    conn = AsyncMock()
    pool = _pool_con(conn)

    migrar = AsyncMock(side_effect=MigradorNoDisponibleError("sin ghostscript"))

    with (
        patch.object(
            format_migration.settings, "format_migration_mime_types", "application/msword"
        ),
        patch.object(format_migration, "get_pool", return_value=pool),
        patch.object(
            format_migration, "list_active_tenants", AsyncMock(return_value=["demo", "otra"])
        ),
        patch.object(
            format_migration.PreservacionRepository,
            "list_pendientes_migracion",
            AsyncMock(return_value=[{"id": uuid.uuid4()}, {"id": uuid.uuid4()}]),
        ),
        patch.object(format_migration.FormatMigrationService, "migrar_a_pdfa", migrar),
    ):
        resumen = await run_once(descargar=_nada, subir_factory=lambda c, s: _nada)

    # Un solo intento: ni el segundo archivo, ni el segundo tenant.
    assert migrar.await_count == 1
    assert resumen["migrados"] == 0


@pytest.mark.asyncio
async def test_cada_tenant_recibe_SU_conexion_para_subir() -> None:
    """El derivado se escribe en el schema del tenant que se está barriendo.
    Una función de subida construida una vez fuera del bucle escribiría los
    archivos de todas las entidades en el schema de la primera — por eso se
    inyecta una FÁBRICA y no una función suelta."""
    conn = AsyncMock()
    pool = _pool_con(conn)
    vistos = []

    def fabrica(c, slug):
        vistos.append(slug)
        return _nada

    with (
        patch.object(
            format_migration.settings, "format_migration_mime_types", "application/msword"
        ),
        patch.object(format_migration, "get_pool", return_value=pool),
        patch.object(
            format_migration, "list_active_tenants", AsyncMock(return_value=["demo", "otra"])
        ),
        patch.object(
            format_migration.PreservacionRepository,
            "list_pendientes_migracion",
            AsyncMock(return_value=[{"id": uuid.uuid4()}]),
        ),
        patch.object(
            format_migration.FormatMigrationService,
            "migrar_a_pdfa",
            AsyncMock(return_value={"estado": "migrado"}),
        ),
    ):
        await run_once(descargar=_nada, subir_factory=fabrica)

    assert vistos == ["demo", "otra"]
