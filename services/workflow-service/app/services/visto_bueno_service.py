# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Vistos buenos secuenciales (paridad legado).

Cadena ordenada de revisores; cada uno actúa solo en su turno (menor orden
pendiente). Un rechazo detiene la cadena. Estado global: en_revision | aprobado |
rechazado.
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.repositories.visto_bueno_repository import VistoBuenoRepository
from app.schemas.visto_bueno import (
    CadenaCreate,
    CadenaResponse,
    VistoBuenoItem,
    VoBoDecision,
)


def _estado_global(items: list[VistoBuenoItem]) -> str:
    if any(i.estado == "rechazado" for i in items):
        return "rechazado"
    if items and all(i.estado == "aprobado" for i in items):
        return "aprobado"
    return "en_revision"


class VistoBuenoService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._repo = VistoBuenoRepository(conn, tenant_slug)

    async def crear(
        self, radicado_id: uuid.UUID, payload: CadenaCreate, actor: uuid.UUID | None = None
    ) -> CadenaResponse:
        if await self._repo.exists(radicado_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "cadena_existente",
                    "detail": "El radicado ya tiene una cadena de vistos buenos",
                    "status": 409,
                },
            )
        # Atomicidad (hallazgo 2.1): la creación de la cadena y su traza forense
        # commitean juntas. Si audit.append falla, la cadena NO queda persistida sin
        # auditoría (la transacción interna de crear_cadena actúa como savepoint).
        async with self._conn.transaction():
            await self._repo.crear_cadena(radicado_id, payload.revisores)
            # Auditoría forense transversal (E08 §9, Canal A): conformar la cadena de
            # revisores es una acción de gestión sobre el radicado y deja traza legal.
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service="workflow-service",
                action="vobo.cadena_creada",
                object_type="radicado",
                object_ref=str(radicado_id),
                actor=str(actor) if actor else None,
                payload={"revisores": [str(r) for r in payload.revisores]},
            )
        return await self.get(radicado_id)

    async def decidir(
        self, radicado_id: uuid.UUID, payload: VoBoDecision, actor: uuid.UUID | None
    ) -> CadenaResponse:
        # Requerir identidad: un actor desconocido no puede participar en la cadena.
        if actor is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "not_a_reviewer",
                    "detail": "Se requiere X-User-Id: el actor no pertenece a la cadena de revisores",
                    "status": 403,
                },
            )
        # Verificación de membership: el actor debe estar en la cadena de este radicado.
        if not await self._repo.is_reviewer(radicado_id, actor):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "not_a_reviewer",
                    "detail": "El actor no pertenece a la cadena de revisores de este radicado",
                    "status": 403,
                },
            )
        active = await self._repo.active(radicado_id)
        if active is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "sin_pendiente",
                    "detail": "No hay visto bueno pendiente",
                    "status": 404,
                },
            )
        # Verificación de turno: el actor debe ser el revisor de turno.
        if active["revisor_id"] != actor:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "no_es_su_turno",
                    "detail": "El visto bueno corresponde a otro revisor",
                    "status": 403,
                },
            )
        estado = "aprobado" if payload.aprobar else "rechazado"
        # Misma atomicidad que `crear`, y por una razón más fuerte: aprobar o rechazar
        # ES el acto de gestión sobre el radicado —el que decide si el trámite sigue o
        # se detiene—, mientras que conformar la cadena solo dice quién lo hará. Que la
        # constitución de la cadena dejara traza y la decisión no significaba que
        # `audit_log` pudiera decir a quién se le pidió el visto bueno, pero no quién lo
        # dio, cuándo, ni por qué (Ley 594/2000, art. 22: trazabilidad del trámite).
        #
        # `object_ref` es el UUID del radicado, igual que en `vobo.cadena_creada`: el
        # recorte por clearance de /admin/auditoria resuelve el nivel por id además de
        # por identidad natural, así que la fila queda protegida como el radicado que
        # describe — y por eso el comentario del revisor puede viajar en el payload,
        # como ya hace `workflow.devolver` con el suyo.
        async with self._conn.transaction():
            await self._repo.decide(active["id"], estado, payload.comentario)
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service="workflow-service",
                action="vobo.decidido",
                object_type="radicado",
                object_ref=str(radicado_id),
                actor=str(actor),
                payload={
                    "orden": active["orden"],
                    "estado": estado,
                    "comentario": payload.comentario,
                },
            )
        return await self.get(radicado_id)

    async def get(self, radicado_id: uuid.UUID) -> CadenaResponse:
        rows = await self._repo.list(radicado_id)
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "sin_cadena",
                    "detail": "El radicado no tiene cadena de vistos buenos",
                    "status": 404,
                },
            )
        items = [
            VistoBuenoItem(
                orden=r["orden"],
                revisor_id=r["revisor_id"],
                estado=r["estado"],
                comentario=r["comentario"],
                decidido_at=r["decidido_at"],
            )
            for r in rows
        ]
        return CadenaResponse(radicado_id=radicado_id, estado=_estado_global(items), items=items)
