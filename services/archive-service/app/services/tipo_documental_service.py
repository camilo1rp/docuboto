# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Mutaciones del catálogo de tipos documentales (3.er nivel TRD, E03/RF-MET-07)
con traza de auditoría inmutable (E08 §9, Canal A).

`tipos_documentales.py` llamaba a `ArchiveRepository` directamente sin capa de
servicio; esta clase la envuelve solo para las mutaciones (create/update/delete)
para que queden auditadas igual que las series TRD — las lecturas siguen
resolviendo contra el repositorio desde el router, sin cambios.
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
from orpycamcp_common import audit

from app.repositories.archive_repository import ArchiveRepository
from app.schemas.archive import TipoDocumentalResponse

_SERVICE = "archive-service"


class TipoDocumentalService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._repo = ArchiveRepository(conn, tenant_slug)

    async def create(
        self, data: dict[str, Any], *, actor: uuid.UUID | None
    ) -> TipoDocumentalResponse:
        # Atomicidad mutación<->asiento (mismo patrón que TRD/`update_expediente`):
        # el INSERT y `audit.append` son un único acto dentro de una sola
        # `conn.transaction()` — si el asiento falla, el alta no queda
        # persistida (fail-closed).
        async with self._conn.transaction():
            row = await self._repo.create_tipo_documental(data)
            # Estado inicial completo (no solo `id`), por consistencia con la
            # corrección del mismo defecto en `create_trd_serie`.
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.tipo_documental_created",
                object_type="tipo_documental",
                object_ref=row["code"],
                actor=str(actor) if actor else None,
                payload={
                    "id": str(row["id"]),
                    "trd_serie_id": str(row["trd_serie_id"]) if row["trd_serie_id"] else None,
                    "code": row["code"],
                    "nombre": row["nombre"],
                    "obligatorio": row["obligatorio"],
                    "metadata_template": row["metadata_template"],
                    "activo": row["activo"],
                },
                canal=audit.CANAL_PETICION,
            )
        return TipoDocumentalResponse(**dict(row))

    async def update(
        self, tipo_id: uuid.UUID, data: dict[str, Any], *, actor: uuid.UUID | None
    ) -> TipoDocumentalResponse | None:
        # Atomicidad mutación<->asiento: el UPDATE y `audit.append` son un
        # único acto dentro de una sola `conn.transaction()`.
        async with self._conn.transaction():
            existing = await self._repo.get_tipo_documental(tipo_id)
            if existing is None:
                return None
            row = await self._repo.update_tipo_documental(tipo_id, data)
            if row is None:
                return None
            # Diff before/after POR CAMPO (mismo defecto que `update_trd_serie`,
            # severidad menor): `data` ya viene con `exclude_unset=True` desde
            # el router, así que sus claves son las que el llamante realmente
            # envió — pero un campo enviado con el mismo valor que ya tenía no
            # es un cambio auditable.
            changes: dict[str, dict[str, Any]] = {}
            for field, sent in data.items():
                before = existing[field]
                if sent != before:
                    changes[field] = {"before": before, "after": sent}
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.tipo_documental_updated",
                object_type="tipo_documental",
                object_ref=row["code"],
                actor=str(actor) if actor else None,
                payload={"id": str(tipo_id), "changes": changes},
                canal=audit.CANAL_PETICION,
            )
        return TipoDocumentalResponse(**dict(row))

    async def delete(self, tipo_id: uuid.UUID, *, actor: uuid.UUID | None) -> bool:
        # Atomicidad mutación<->asiento, mismo patrón que create/update.
        async with self._conn.transaction():
            deleted = await self._repo.delete_tipo_documental(tipo_id)
            if deleted:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.tipo_documental_deleted",
                    object_type="tipo_documental",
                    object_ref=str(tipo_id),
                    actor=str(actor) if actor else None,
                    payload={},
                    canal=audit.CANAL_PETICION,
                )
        return deleted
