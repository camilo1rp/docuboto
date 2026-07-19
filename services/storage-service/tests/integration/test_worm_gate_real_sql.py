# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Matriz del gate de disposición E10 Inc.2 (remedio) contra Postgres REAL.

`PreservacionWormRepository.active_lock_for_expediente` decide con una sola
condición SQL (`legal_hold OR (retain_until > now() AND tipo <> ALL($2::text[]))`)
si un expediente sigue bajo protección WORM. `tests/test_purge_worm_gate.py`
(carril unit, `conn` mockeado) verifica que `StorageService.purge_for_disposition`
LLAMA a esa condición con los argumentos correctos, pero un mock no puede probar
que la condición SQL en sí filtra bien — un `conn.fetchrow` mockeado devuelve lo
que el test configuró sin evaluar el `WHERE`. Este archivo cierra ese hueco:
ejercita el repositorio contra un Postgres real (schema de tenant desechable,
migraciones tenant reales de storage-service aplicadas) y verifica el
comportamiento de la matriz completa del remedio (E10 Inc.2):

  1. Contenido (`indice`/`aip`) con `retain_until` futuro → bloquea.
  2. SOLO un `acta_transferencia` con `retain_until` futuro → YA NO bloquea
     (el cambio central de este remedio).
  3. `legal_hold` en el acta → bloquea incondicionalmente, sin importar
     `retain_until` ni que su `tipo` esté excluido.
  4. Fail-closed ante un `tipo` fuera de `exclude_retention_tipos` — incluido
     uno NO clasificado (ver nota más abajo sobre el CHECK constraint).
  5. Mezcla índice+acta: bloquea si el índice está vigente, procede si el
     índice venció y solo queda el acta vigente.
  6. Regresión: `exclude_retention_tipos` con su default `()` conserva el
     comportamiento all-or-nothing anterior a este remedio.

Corre con: `docker compose run --rm -e RUN_INTEGRATION=1 storage-service
pytest -q -m integration tests/integration`
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.core.preservacion_tipos import CONTROL_INSTRUMENT_TIPOS
from app.repositories.preservacion_worm_repository import PreservacionWormRepository

pytestmark = pytest.mark.integration

_SHA = "a" * 64
_FUTURE = datetime.now(tz=UTC) + timedelta(days=10)
_PAST = datetime.now(tz=UTC) - timedelta(days=1)


async def _insert(
    repo: PreservacionWormRepository,
    *,
    expediente_id: str,
    tipo: str,
    retain_until: datetime,
    legal_hold: bool = False,
) -> asyncpg.Record:
    return await repo.insert(
        expediente_id=expediente_id,
        tipo=tipo,
        bucket="orpycamcp-itest-preservation",
        object_key=f"{expediente_id}/{tipo}/{uuid.uuid4()}.bin",
        version_id="v1",
        sha256=_SHA,
        retain_until=retain_until,
        retention_mode="COMPLIANCE",
        legal_hold=legal_hold,
        file_id=None,
    )


@pytest.mark.asyncio
async def test_content_indice_blocks_with_real_sql(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Matriz #1: `indice` con retención futura bloquea, incluso excluyendo
    los instrumentos de control (el índice NO está en esa lista)."""
    slug, conn = tenant_ctx
    repo = PreservacionWormRepository(conn, slug)
    exp = f"EXP-{uuid.uuid4().hex[:8]}"
    await _insert(repo, expediente_id=exp, tipo="indice", retain_until=_FUTURE)

    lock = await repo.active_lock_for_expediente(
        exp, exclude_retention_tipos=tuple(CONTROL_INSTRUMENT_TIPOS)
    )

    assert lock is not None
    assert lock["tipo"] == "indice"


@pytest.mark.asyncio
async def test_content_aip_blocks_with_real_sql(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Matriz #1 (variante `aip`): mismo bloqueo que `indice` — el gate no
    filtra por tipo de contenido, solo excluye instrumentos de control."""
    slug, conn = tenant_ctx
    repo = PreservacionWormRepository(conn, slug)
    exp = f"EXP-{uuid.uuid4().hex[:8]}"
    await _insert(repo, expediente_id=exp, tipo="aip", retain_until=_FUTURE)

    lock = await repo.active_lock_for_expediente(
        exp, exclude_retention_tipos=tuple(CONTROL_INSTRUMENT_TIPOS)
    )

    assert lock is not None
    assert lock["tipo"] == "aip"


@pytest.mark.asyncio
async def test_acta_alone_no_longer_blocks_content_real_sql(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Matriz #2 — EL CAMBIO CENTRAL del remedio: un `acta_transferencia` con
    retención futura, SIN ningún índice/aip vigente en el expediente, ya NO
    bloquea la purga del contenido cuando se excluyen los instrumentos de
    control."""
    slug, conn = tenant_ctx
    repo = PreservacionWormRepository(conn, slug)
    exp = f"EXP-{uuid.uuid4().hex[:8]}"
    await _insert(repo, expediente_id=exp, tipo="acta_transferencia", retain_until=_FUTURE)

    lock = await repo.active_lock_for_expediente(
        exp, exclude_retention_tipos=tuple(CONTROL_INSTRUMENT_TIPOS)
    )

    assert lock is None


@pytest.mark.asyncio
async def test_legal_hold_on_acta_blocks_unconditionally_real_sql(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Matriz #3: `legal_hold=true` en el acta bloquea SIEMPRE, aunque su
    `retain_until` ya haya vencido y su `tipo` esté en la lista de exclusión —
    no se puede burlar el hold excluyendo el tipo."""
    slug, conn = tenant_ctx
    repo = PreservacionWormRepository(conn, slug)
    exp = f"EXP-{uuid.uuid4().hex[:8]}"
    await _insert(
        repo,
        expediente_id=exp,
        tipo="acta_transferencia",
        retain_until=_PAST,  # vencido...
        legal_hold=True,  # ...pero el hold sigue bloqueando
    )

    lock = await repo.active_lock_for_expediente(
        exp, exclude_retention_tipos=tuple(CONTROL_INSTRUMENT_TIPOS)
    )

    assert lock is not None
    assert lock["legal_hold"] is True


@pytest.mark.asyncio
async def test_unclassified_tipo_blocks_fail_closed_real_sql(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Matriz #4 — fail-closed: un `tipo` que NO está en
    `exclude_retention_tipos` bloquea, sea cual sea su nombre.

    El CHECK `preservacion_worm_objeto_tipo_check` (migración 010) ya actúa
    como un allowlist a nivel de esquema (`indice`, `aip`,
    `acta_transferencia`) — hoy es estructuralmente imposible que llegue a la
    tabla un `tipo` fuera de esos tres, así que el escenario "tipo
    completamente desconocido" que pide la matriz no es alcanzable con el
    esquema intacto. Para probar el mecanismo SQL genérico
    (`tipo <> ALL($2::text[])`, exclusión por nombre — NO por pertenecer a un
    allowlist positivo de tipos de contenido conocidos) en vez de una
    propiedad accidental del CHECK actual, este test relaja el CHECK
    SOLO dentro del schema desechable del test (`DROP CONSTRAINT`, nunca
    tocado en producción) e inserta un `tipo` inventado. Ver el hueco
    correspondiente para el auditor en el resumen final de la tarea."""
    slug, conn = tenant_ctx
    schema = f"tenant_{slug.replace('-', '_')}"
    await conn.execute(
        f'ALTER TABLE "{schema}".preservacion_worm_objeto '
        "DROP CONSTRAINT preservacion_worm_objeto_tipo_check"
    )
    repo = PreservacionWormRepository(conn, slug)
    exp = f"EXP-{uuid.uuid4().hex[:8]}"
    await _insert(
        repo, expediente_id=exp, tipo="futuro_no_clasificado", retain_until=_FUTURE
    )

    # `exclude_retention_tipos` solo conoce los instrumentos de control de HOY
    # (`CONTROL_INSTRUMENT_TIPOS = {'acta_transferencia'}`) — el tipo inventado
    # no figura ahí, así que debe seguir bloqueando (dirección segura).
    lock = await repo.active_lock_for_expediente(
        exp, exclude_retention_tipos=tuple(CONTROL_INSTRUMENT_TIPOS)
    )

    assert lock is not None
    assert lock["tipo"] == "futuro_no_clasificado"


@pytest.mark.asyncio
async def test_mixed_indice_vigente_and_acta_vigente_blocks_by_indice(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Matriz #5a: índice vigente + acta vigente → bloquea (por el índice,
    aunque el acta ya esté excluida)."""
    slug, conn = tenant_ctx
    repo = PreservacionWormRepository(conn, slug)
    exp = f"EXP-{uuid.uuid4().hex[:8]}"
    await _insert(repo, expediente_id=exp, tipo="indice", retain_until=_FUTURE)
    await _insert(repo, expediente_id=exp, tipo="acta_transferencia", retain_until=_FUTURE)

    lock = await repo.active_lock_for_expediente(
        exp, exclude_retention_tipos=tuple(CONTROL_INSTRUMENT_TIPOS)
    )

    assert lock is not None
    assert lock["tipo"] == "indice"


@pytest.mark.asyncio
async def test_mixed_indice_vencido_and_acta_vigente_proceeds(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Matriz #5b: índice YA VENCIDO + acta todavía vigente → la purga
    procede (ni el índice vencido ni el acta excluida bloquean)."""
    slug, conn = tenant_ctx
    repo = PreservacionWormRepository(conn, slug)
    exp = f"EXP-{uuid.uuid4().hex[:8]}"
    await _insert(repo, expediente_id=exp, tipo="indice", retain_until=_PAST)
    await _insert(repo, expediente_id=exp, tipo="acta_transferencia", retain_until=_FUTURE)

    lock = await repo.active_lock_for_expediente(
        exp, exclude_retention_tipos=tuple(CONTROL_INSTRUMENT_TIPOS)
    )

    assert lock is None


@pytest.mark.asyncio
async def test_default_exclude_is_all_or_nothing_regression(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Matriz #6 — regresión: sin pasar `exclude_retention_tipos` (default
    `()`), el comportamiento sigue siendo all-or-nothing, igual que antes de
    este remedio — para no romper otros llamadores que todavía no distinguen
    contenido de instrumento de control."""
    slug, conn = tenant_ctx
    repo = PreservacionWormRepository(conn, slug)
    exp = f"EXP-{uuid.uuid4().hex[:8]}"
    await _insert(repo, expediente_id=exp, tipo="acta_transferencia", retain_until=_FUTURE)

    lock = await repo.active_lock_for_expediente(exp)  # sin exclude_retention_tipos

    assert lock is not None
    assert lock["tipo"] == "acta_transferencia"
