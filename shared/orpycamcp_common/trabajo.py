# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Outbox of work that leaves the SQL transaction: one claim loop, handlers by type.

`event_id` is unique. A second enqueue is a no-op. The worker claims veraPDF,
render, and LTA renewal. The app claims mail, webhooks, and intelligence. A
failed row can be released from the admin view and claimed again.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

COLA_APP = "app"
COLA_WORKER = "worker"
COLAS = frozenset({COLA_APP, COLA_WORKER})

TIPO_MAIL = "mail.send"
TIPO_WEBHOOK = "webhook.deliver"
TIPO_INTELLIGENCE = "intelligence.forward"
TIPO_VERAPDF = "storage.verapdf"
TIPO_RENDER = "storage.render"
TIPO_LTA = "signature.lta_renew"

TIPOS_APP = frozenset({TIPO_MAIL, TIPO_WEBHOOK, TIPO_INTELLIGENCE})
TIPOS_WORKER = frozenset({TIPO_VERAPDF, TIPO_RENDER, TIPO_LTA})


@dataclass(frozen=True)
class Nueva:
    event_id: str
    tipo: str
    cola: str
    tenant_slug: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Fila:
    id: int
    event_id: str
    tipo: str
    cola: str
    tenant_slug: str
    payload: dict[str, Any]
    intentos: int
    ultimo_error: str | None


class Repositorio(Protocol):
    async def encolar(self, nueva: Nueva) -> bool: ...
    async def reclamar(self, cola: str, reclamador: str, limite: int) -> list[Fila]: ...
    async def marcar_hecho(self, fila_id: int) -> None: ...
    async def marcar_error(self, fila_id: int, error: str) -> None: ...
    async def reintentar(self, event_id: str) -> bool: ...
    async def listar_fallidos(self, limite: int = 200) -> list[Fila]: ...


Manejador = Callable[[Any, Fila], Awaitable[None]]


def cola_de(tipo: str) -> str:
    if tipo in TIPOS_WORKER:
        return COLA_WORKER
    return COLA_APP


def intelligence_se_omite(*, proveedor_url: str) -> bool:
    """No provider configured: mark the row done and do not call anyone."""
    return not (proveedor_url or "").strip()


def _payload(crudo: Any) -> dict[str, Any]:
    if crudo is None:
        return {}
    if isinstance(crudo, dict):
        return crudo
    if isinstance(crudo, str):
        leido = json.loads(crudo)
        return leido if isinstance(leido, dict) else {}
    return dict(crudo)


async def recorrer(
    repo: Repositorio,
    *,
    cola: str,
    reclamador: str,
    manejadores: Mapping[str, Manejador],
    conn: Any = None,
    limite: int = 50,
) -> int:
    """Claim a batch, run the handler for each type, mark done or failed.

    `conn` is the connection the handler may use (tenant search_path already set).
    Unknown types fail the row so they surface in the admin view.
    """
    if cola not in COLAS:
        raise ValueError(f"cola desconocida: {cola}")
    filas = await repo.reclamar(cola, reclamador, limite)
    hechos = 0
    for fila in filas:
        fn = manejadores.get(fila.tipo)
        if fn is None:
            await repo.marcar_error(fila.id, f"tipo desconocido: {fila.tipo}")
            continue
        try:
            await fn(conn, fila)
        except Exception as exc:  # noqa: BLE001 — the row keeps the reason
            await repo.marcar_error(fila.id, str(exc)[:500])
            continue
        await repo.marcar_hecho(fila.id)
        hechos += 1
    return hechos


class SqlRepo:
    """Tenant-schema `trabajo_fuera`. The caller already set search_path."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def encolar(self, nueva: Nueva) -> bool:
        if nueva.cola not in COLAS:
            raise ValueError(f"cola desconocida: {nueva.cola}")
        row = await self._conn.fetchrow(
            "INSERT INTO trabajo_fuera (event_id, tipo, cola, tenant_slug, payload) "
            "VALUES ($1, $2, $3, $4, $5::jsonb) "
            "ON CONFLICT (event_id) DO NOTHING "
            "RETURNING event_id",
            nueva.event_id,
            nueva.tipo,
            nueva.cola,
            nueva.tenant_slug,
            json.dumps(nueva.payload),
        )
        return row is not None

    async def reclamar(self, cola: str, reclamador: str, limite: int) -> list[Fila]:
        filas = await self._conn.fetch(
            "UPDATE trabajo_fuera AS t "
            "   SET reclamado_at = now(), reclamado_por = $1, intentos = t.intentos + 1 "
            " WHERE t.id IN ("
            "       SELECT id FROM trabajo_fuera "
            "        WHERE cola = $2 AND hecho_at IS NULL "
            "          AND (reclamado_at IS NULL "
            "               OR reclamado_at < now() - interval '5 minutes') "
            "        ORDER BY id LIMIT $3 FOR UPDATE SKIP LOCKED) "
            "RETURNING t.id, t.event_id, t.tipo, t.cola, t.tenant_slug, "
            "          t.payload, t.intentos, t.ultimo_error",
            reclamador,
            cola,
            limite,
        )
        return [_fila(r) for r in filas]

    async def marcar_hecho(self, fila_id: int) -> None:
        await self._conn.execute(
            "UPDATE trabajo_fuera SET hecho_at = now(), ultimo_error = NULL WHERE id = $1",
            fila_id,
        )

    async def marcar_error(self, fila_id: int, error: str) -> None:
        await self._conn.execute(
            "UPDATE trabajo_fuera SET ultimo_error = $2, reclamado_at = NULL, "
            "reclamado_por = NULL WHERE id = $1",
            fila_id,
            error[:500],
        )

    async def reintentar(self, event_id: str) -> bool:
        row = await self._conn.fetchrow(
            "UPDATE trabajo_fuera SET reclamado_at = NULL, reclamado_por = NULL, "
            "ultimo_error = NULL WHERE event_id = $1 AND hecho_at IS NULL "
            "RETURNING event_id",
            event_id,
        )
        return row is not None

    async def listar_fallidos(self, limite: int = 200) -> list[Fila]:
        filas = await self._conn.fetch(
            "SELECT id, event_id, tipo, cola, tenant_slug, payload, intentos, ultimo_error "
            "  FROM trabajo_fuera "
            " WHERE hecho_at IS NULL AND ultimo_error IS NOT NULL "
            " ORDER BY id LIMIT $1",
            limite,
        )
        return [_fila(r) for r in filas]


def _fila(r: Any) -> Fila:
    return Fila(
        id=int(r["id"]),
        event_id=str(r["event_id"]),
        tipo=str(r["tipo"]),
        cola=str(r["cola"]),
        tenant_slug=str(r["tenant_slug"]),
        payload=_payload(r["payload"]),
        intentos=int(r["intentos"]),
        ultimo_error=r["ultimo_error"],
    )
