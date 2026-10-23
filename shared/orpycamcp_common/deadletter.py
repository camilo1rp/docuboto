# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Visibilidad de la DLQ compartida por clearance, resuelta EN el ``WHERE`` (AREA-10-08).

``evento_dead_letter`` es una sola tabla física que escriben workflow, signature,
notification y knowledge. Cada fila guarda el sobre de un evento sobre un radicado, y
quien lista, abre, reprocesa o descarta no debe ver lo que su clearance no alcanza
(RF-SEG-08, no-read-up). El gate se hace con un ``LEFT JOIN LATERAL`` contra
``radicados`` VIVO dentro de la propia consulta, de modo que paginación y recuento
cuentan exactamente lo que el llamante puede ver, sin tope de candidatas ni filtro en
Python.

Regla de nivel (fail-closed, idéntica a la que había copiada en cuatro servicios):
``max(nivel_vivo, nivel_embebido)``; sin radicado vivo, sin identificadores o con un
nivel embebido que no es un entero limpio, el nivel es ``CLASIFICADA`` (3). Respecto a
la versión en Python solo difiere de forma restrictiva: ``tracking_number`` no se cruza
contra ``id::text``; un embebido ``2.0``/``true`` cuenta como duda (3); un sobre doblemente
codificado o que es una lista cuenta como duda (3).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

import asyncpg
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from orpycamcp_common.tenant import tenant_schema

CLASIFICADA = 3

_UUID_RE = "^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$"


def _from(schema: str) -> str:
    """FROM con ``radicados`` CALIFICADA con el schema del tenant.

    Sin calificar, el nombre se resolvería por el ``search_path`` (``"tenant_x"``):
    si el tenant no tuviera ``radicados``, caería a otra del ``public`` y el gate leería
    niveles de un lugar equivocado. Calificada, la ausencia es ``UndefinedTable`` (503).
    ``schema`` sale de ``tenant_schema`` (slug validado); se entrecomilla.
    """
    return f"""
FROM evento_dead_letter d
CROSS JOIN LATERAL (SELECT d.payload->'payload' AS p) j
CROSS JOIN LATERAL (
    SELECT COALESCE(j.p->>'nivel_nuevo', j.p->>'nivel_seguridad') AS v
) emb
LEFT JOIN LATERAL (
    SELECT COALESCE(
        (SELECT r.nivel_seguridad FROM "{schema}".radicados r
          WHERE r.tracking_number = j.p->>'tracking_number'),
        (SELECT r.nivel_seguridad FROM "{schema}".radicados r
          WHERE r.id = CASE WHEN j.p->>'id' ~* '{_UUID_RE}'
                            THEN (j.p->>'id')::uuid END)
    ) AS nivel
) rv ON true
"""


class RadicadosAusenteError(RuntimeError):
    """La tabla ``radicados`` no existe en el schema del tenant.

    No se traduce a «lista vacía»: un gate que no puede evaluarse no debe parecer un
    gate que no encontró nada. El router lo responde con 503.
    """


def _gate(clearance_ph: str) -> str:
    return (
        "COALESCE(CASE WHEN rv.nivel IS NULL THEN NULL "
        "WHEN emb.v IS NULL THEN rv.nivel "
        "WHEN emb.v ~ '^-?[0-9]{1,9}$' THEN GREATEST(rv.nivel, emb.v::int) "
        f"END, {CLASIFICADA}) <= {clearance_ph}"
    )


def visible_deadletter_sql(
    select: str,
    *,
    schema: str,
    where: str,
    clearance_ph: str,
    tail: str = "",
) -> str:
    """Construye ``SELECT <select> FROM evento_dead_letter d … WHERE <where> AND <gate>``.

    ``where`` son las condiciones del llamante (siempre sobre el alias ``d``) y el gate
    de clearance se le añade SIEMPRE: no hay forma de pedir la tabla sin él. ``tail`` es
    ``ORDER BY``/``LIMIT``/``FOR UPDATE OF d``. Los valores van por placeholders; aquí
    solo se interpola SQL fijo del código, nunca entrada de usuario.
    """
    return f"SELECT {select} {_from(schema)} WHERE ({where}) AND {_gate(clearance_ph)} {tail}".strip()


async def list_visible(
    conn: asyncpg.Connection,
    *,
    tenant_slug: str,
    origin_group: str,
    status: str,
    clearance: int,
    columns: str,
    origin_stream: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[asyncpg.Record], int]:
    """Página y total de las filas visibles para ``clearance``.

    Ambas consultas salen del mismo constructor y corren en una transacción de solo
    lectura ``REPEATABLE READ`` para que total y página describan la misma foto. Un
    ``offset`` pasado de fin devuelve ``([], total)``, no ``([], 0)``.
    """
    where = "d.tenant_slug = $1 AND d.status = $2 AND d.origin_group = $3"
    params: list[object] = [tenant_slug, status, origin_group]
    if origin_stream is not None:
        params.append(origin_stream)
        where += f" AND d.origin_stream = ${len(params)}"
    params.append(clearance)
    clearance_ph = f"${len(params)}"

    schema = tenant_schema(tenant_slug)
    page_sql = visible_deadletter_sql(
        columns,
        schema=schema,
        where=where,
        clearance_ph=clearance_ph,
        tail=(
            "ORDER BY d.dead_lettered_at DESC, d.event_id DESC "
            f"LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}"
        ),
    )
    count_sql = visible_deadletter_sql(
        "count(*)", schema=schema, where=where, clearance_ph=clearance_ph
    )
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            rows = await conn.fetch(page_sql, *params, limit, offset)
            total = await conn.fetchval(count_sql, *params)
    except asyncpg.UndefinedTableError as exc:
        if "radicados" in str(exc):
            raise RadicadosAusenteError(str(exc)) from exc
        raise
    return list(rows), int(total or 0)


async def get_visible(
    conn: asyncpg.Connection,
    *,
    event_id: str,
    tenant_slug: str,
    origin_group: str,
    clearance: int,
    for_update: bool = False,
) -> asyncpg.Record | None:
    """Una fila por ``event_id`` si existe Y es visible; ``None`` en ambos casos.

    Devolver el mismo ``None`` para «no existe» y «existe pero no alcanzas el nivel»
    es lo que permite al router responder un 404 indistinguible.
    ``for_update`` bloquea solo la fila de la DLQ (``FOR UPDATE OF d``), no ``radicados``.
    """
    sql = visible_deadletter_sql(
        "d.*",
        schema=tenant_schema(tenant_slug),
        where="d.event_id = $1 AND d.tenant_slug = $2 AND d.origin_group = $3",
        clearance_ph="$4",
        tail="FOR UPDATE OF d" if for_update else "",
    )
    try:
        return await conn.fetchrow(sql, event_id, tenant_slug, origin_group, clearance)
    except asyncpg.UndefinedTableError as exc:
        if "radicados" in str(exc):
            raise RadicadosAusenteError(str(exc)) from exc
        raise


async def contar_pendientes(
    conn: asyncpg.Connection, schemas: Sequence[str], grupos: Sequence[str]
) -> tuple[int, datetime | None]:
    """``(n, más_antiguo)`` de las filas ``pending`` de los *grupos* sumadas sobre *schemas*.

    Es para MÉTRICAS (C18, ADR-041): un entero agregado y un instante, sin contenido
    de ningún evento. Por eso NO aplica clearance y NO reutiliza
    :func:`visible_deadletter_sql`, que gatea por la persona que consulta: aquí no hay
    persona, y un número agregado no revela el nivel de ningún radicado. Que nadie lo
    «arregle» metiéndole el gate: dejaría la métrica en 0 para siempre.

    UNA consulta ``UNION ALL`` con la tabla calificada por schema (el ``search_path``
    no interviene); usa ``ix_edl_pending`` (índice parcial). Sin schemas no consulta.
    Si la tabla falta en alguno, la excepción sube: el llamador lo trata como «no
    verificado», nunca como una suma parcial.
    """
    if not schemas:
        return 0, None
    ramas = " UNION ALL ".join(
        f'SELECT count(*) AS n, min(dead_lettered_at) AS m FROM "{_schema_valido(s)}".evento_dead_letter '
        "WHERE status = 'pending' AND origin_group = ANY($1::text[])"
        for s in schemas
    )
    fila = await conn.fetchrow(
        f"SELECT COALESCE(sum(n), 0)::bigint AS n, min(m) AS m FROM ({ramas}) u", list(grupos)
    )
    assert fila is not None
    return int(fila["n"]), fila["m"]


def _schema_valido(schema: str) -> str:
    """Un nombre de schema que no sale de ``tenant_schema`` no se interpola jamás."""
    if not re.fullmatch(r"tenant_[a-z0-9_]+", schema):
        raise ValueError(f"schema de tenant inválido: {schema!r}")
    return schema


def instalar_manejador_radicados_ausente(app: Starlette) -> None:
    """Registra el 503 de :class:`RadicadosAusenteError` en la aplicación.

    Un único manejador por servicio evita envolver cada ruta (y cada transacción de
    replay/descarte) en su propio ``try``: la excepción sale, la transacción revierte
    y el cliente recibe un error explícito en lugar de una lista vacía.
    """

    async def _manejador(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": "service_unavailable",
                "detail": "No se pudo evaluar el nivel de seguridad de los eventos",
                "status": 503,
            },
        )

    app.add_exception_handler(RadicadosAusenteError, _manejador)
