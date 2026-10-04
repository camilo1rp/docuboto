# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Atomicidad del archivo físico contra Postgres REAL (AREA-07-20).

`FisicoService` envuelve cada acto de custodia —alta de ubicación y de unidad,
vínculo expediente→unidad, préstamo y devolución— en `conn.transaction()` con su
`audit.append` (Ley 594/2000 art. 26: la cadena de custodia física), pero nada lo
probaba contra una base de verdad: los tests de `test_fisico.py` doblan la
conexión, y con un doble el rollback no existe.

Patrón de `test_atomicity.py` (b2): se ejecuta la implementación REAL de
`audit.append` y se revienta DESPUÉS, dentro de la transacción del servicio. Si
`audit.append` (o el write) estuviera fuera del bloque, la fila o el asiento
sobrevivirían: el caso de fallo debe dejar **cero filas y cero asientos**, y la
cadena de `audit_log` íntegra. Cada acto lleva su control positivo (un solo
asiento y `verify_chain` íntegra): sin él, un servicio que nunca escribiera pasa
el caso de fallo.

Mutación que debe tumbarlo: mover `audit.append` fuera de `conn.transaction()` en
cualquiera de los cinco métodos — el caso de fallo deja un asiento huérfano.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date

import asyncpg
import pytest
from fastapi import HTTPException
from orpycamcp_common import audit

from app.core.database import tenant_schema
from app.schemas.fisico import (
    ExpedienteUnidadLink,
    PrestamoCreate,
    UbicacionCreate,
    UnidadCreate,
)
from app.services.fisico_service import FisicoService

pytestmark = pytest.mark.integration

ACTOR = uuid.uuid4()


@dataclass
class Contexto:
    slug: str
    conn: asyncpg.Connection
    servicio: FisicoService
    unidad_id: uuid.UUID | None = None
    expediente_id: uuid.UUID | None = None
    prestamo_id: uuid.UUID | None = None


async def _contar(conn: asyncpg.Connection, slug: str, sql: str) -> int:
    return int(await conn.fetchval(sql.replace("{s}", f'"{tenant_schema(slug)}"')))


async def _asientos(conn: asyncpg.Connection, slug: str, accion: str) -> int:
    return int(
        await conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2",
            slug,
            accion,
        )
    )


async def _con_unidad(ctx: Contexto) -> None:
    ub = await ctx.servicio.create_ubicacion(
        UbicacionCreate(nivel="deposito", codigo="DEP-1", nombre="Depósito 1"), actor=ACTOR
    )
    unidad = await ctx.servicio.create_unidad(
        UnidadCreate(tipo="caja", codigo="CAJ-1", ubicacion_id=ub.id), actor=ACTOR
    )
    ctx.unidad_id = unidad.id


async def _con_expediente(ctx: Contexto) -> None:
    await _con_unidad(ctx)
    ctx.expediente_id = await ctx.conn.fetchval(
        f'INSERT INTO "{tenant_schema(ctx.slug)}".expedientes (code, name) '
        "VALUES ('EXP-FIS-1', 'Expediente físico') RETURNING id"
    )


async def _con_prestamo(ctx: Contexto) -> None:
    await _con_unidad(ctx)
    prestamo = await ctx.servicio.create_prestamo(
        ctx.unidad_id,
        PrestamoCreate(prestado_a="Jurídica", fecha_devolucion_esperada=date(2099, 1, 1)),
        str(ACTOR),
        actor=ACTOR,
    )
    ctx.prestamo_id = prestamo.id


@dataclass
class Acto:
    nombre: str
    accion: str  # acción del asiento que debe dejar
    preparar: Callable[[Contexto], Awaitable[None]] | None
    ejecutar: Callable[[Contexto], Awaitable[object]]
    filas: str  # SQL con `{s}` (schema) que cuenta lo que el acto escribe


async def _crear_ubicacion(ctx: Contexto) -> object:
    return await ctx.servicio.create_ubicacion(
        UbicacionCreate(nivel="deposito", codigo="DEP-X", nombre="Depósito X"), actor=ACTOR
    )


async def _crear_unidad(ctx: Contexto) -> object:
    return await ctx.servicio.create_unidad(
        UnidadCreate(tipo="caja", codigo="CAJ-X", ubicacion_id=None), actor=ACTOR
    )


async def _vincular(ctx: Contexto) -> object:
    return await ctx.servicio.link_expediente(
        ctx.unidad_id, ExpedienteUnidadLink(expediente_id=ctx.expediente_id), actor=ACTOR
    )


async def _prestar(ctx: Contexto) -> object:
    return await ctx.servicio.create_prestamo(
        ctx.unidad_id,
        PrestamoCreate(prestado_a="Archivo", fecha_devolucion_esperada=date(2099, 1, 1)),
        str(ACTOR),
        actor=ACTOR,
    )


async def _devolver(ctx: Contexto) -> object:
    return await ctx.servicio.devolver_prestamo(ctx.prestamo_id, str(ACTOR), actor=ACTOR)


ACTOS = [
    Acto(
        "create_ubicacion",
        "archive.ubicacion_created",
        None,
        _crear_ubicacion,
        "SELECT count(*) FROM {s}.ubicacion",
    ),
    Acto(
        "create_unidad",
        "archive.unidad_created",
        None,
        _crear_unidad,
        "SELECT count(*) FROM {s}.unidad_conservacion",
    ),
    Acto(
        "link_expediente",
        "archive.unidad_expediente_linked",
        _con_expediente,
        _vincular,
        "SELECT count(*) FROM {s}.expediente_unidad",
    ),
    Acto(
        "create_prestamo",
        "archive.prestamo_created",
        _con_unidad,
        _prestar,
        "SELECT count(*) FROM {s}.prestamo",
    ),
    Acto(
        "devolver_prestamo",
        "archive.prestamo_devuelto",
        _con_prestamo,
        _devolver,
        "SELECT count(*) FROM {s}.prestamo WHERE estado = 'devuelto'",
    ),
]


async def _armar(tenant_ctx, acto: Acto) -> tuple[Contexto, int, int]:
    slug, conn = tenant_ctx
    ctx = Contexto(slug=slug, conn=conn, servicio=FisicoService(conn, slug))
    if acto.preparar is not None:
        await acto.preparar(ctx)
    filas = await _contar(conn, slug, acto.filas)
    asientos = await _asientos(conn, slug, acto.accion)
    return ctx, filas, asientos


@pytest.mark.parametrize("acto", ACTOS, ids=[a.nombre for a in ACTOS])
async def test_el_acto_confirmado_deja_una_fila_y_un_asiento_con_la_cadena_integra(
    tenant_ctx, acto: Acto
) -> None:
    """Control positivo: sin él, un servicio que nunca escribiera pasaba el
    caso de fallo de abajo."""
    ctx, filas_antes, asientos_antes = await _armar(tenant_ctx, acto)

    await acto.ejecutar(ctx)

    assert await _contar(ctx.conn, ctx.slug, acto.filas) == filas_antes + 1
    assert await _asientos(ctx.conn, ctx.slug, acto.accion) == asientos_antes + 1
    res = await audit.verify_chain(ctx.conn, ctx.slug)
    assert res.ok is True, res.roturas


@pytest.mark.parametrize("acto", ACTOS, ids=[a.nombre for a in ACTOS])
async def test_un_fallo_despues_del_asiento_no_deja_ni_fila_ni_asiento(
    tenant_ctx, monkeypatch: pytest.MonkeyPatch, acto: Acto
) -> None:
    ctx, filas_antes, asientos_antes = await _armar(tenant_ctx, acto)
    original = audit.append

    async def _append_y_luego_falla(*args: object, **kwargs: object) -> str:
        # El asiento REAL queda escrito dentro de la transacción del servicio y
        # SOLO DESPUÉS revienta: si `audit.append` o el write vivieran fuera del
        # bloque, sobrevivirían al fallo.
        if kwargs.get("action") == acto.accion:
            await original(*args, **kwargs)  # type: ignore[arg-type]
            raise RuntimeError("boom-post-audit (inyección de fallo, AREA-07-20)")
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(audit, "append", _append_y_luego_falla)

    with pytest.raises(RuntimeError, match="boom-post-audit"):
        await acto.ejecutar(ctx)

    assert await _contar(ctx.conn, ctx.slug, acto.filas) == filas_antes
    assert await _asientos(ctx.conn, ctx.slug, acto.accion) == asientos_antes
    res = await audit.verify_chain(ctx.conn, ctx.slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


async def test_el_prestamo_que_falla_no_deja_el_movimiento_RETIRADO(
    tenant_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El préstamo escribe DOS filas (préstamo y movimiento) más el asiento, y
    las tres son un solo acto: una cadena de custodia con el movimiento
    «RETIRADO» y sin préstamo diría que la unidad salió del estante y a nadie."""
    slug, conn = tenant_ctx
    ctx = Contexto(slug=slug, conn=conn, servicio=FisicoService(conn, slug))
    await _con_unidad(ctx)
    original = audit.append

    async def _boom(*args: object, **kwargs: object) -> str:
        if kwargs.get("action") == "archive.prestamo_created":
            await original(*args, **kwargs)  # type: ignore[arg-type]
            raise RuntimeError("boom-post-audit")
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(audit, "append", _boom)
    with pytest.raises(RuntimeError):
        await _prestar(ctx)

    movimientos = await _contar(
        conn, slug, "SELECT count(*) FROM {s}.movimiento_fisico WHERE accion = 'RETIRADO'"
    )
    assert movimientos == 0


async def test_no_se_presta_una_unidad_con_contenido_sobre_el_clearance(tenant_ctx) -> None:
    """AREA-01-13: un usuario de clearance 1 (el ACTOR no tiene fila en
    `auth_users`: PUBLICA) prestaba una unidad con un expediente nivel 3. El
    gate vive en el WHERE del INSERT: 403 y NADA escrito (ni préstamo, ni
    movimiento RETIRADO, ni asiento). Mutacion: quitar el `NOT EXISTS` de
    `insert_prestamo` deja el prestamo creado y tumba este test."""
    slug, conn = tenant_ctx
    ctx = Contexto(slug=slug, conn=conn, servicio=FisicoService(conn, slug))
    await _con_expediente(ctx)
    await conn.execute(
        f'UPDATE "{tenant_schema(slug)}".expedientes SET nivel_seguridad = 3 WHERE id = $1',
        ctx.expediente_id,
    )
    await _vincular(ctx)

    with pytest.raises(HTTPException) as exc:
        await _prestar(ctx)

    assert exc.value.status_code == 403
    assert await _contar(conn, slug, "SELECT count(*) FROM {s}.prestamo") == 0
    assert (
        await _contar(
            conn, slug, "SELECT count(*) FROM {s}.movimiento_fisico WHERE accion = 'RETIRADO'"
        )
        == 0
    )
    assert await _asientos(conn, slug, "archive.prestamo_created") == 0
