# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma declarativa de un anexo (E06, ADR-016; AREA-05-03-S).

Firma electrónica SIMPLE: identidad + huella + sello de tiempo. La huella la
resuelve el servidor; el registro y su asiento en `public.audit_log` son UN acto.
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.clients import storage_client
from app.repositories.signature_repository import SignatureRepository
from app.schemas.signature import SignatureCreate, SignatureResponse

_SERVICE = "document-service"


def not_found(document_id: uuid.UUID) -> HTTPException:
    """404 NEUTRO: mismo cuerpo para «no existe» y «supera tu clearance».

    Si difirieran, un actor sin nivel podría sondear el servicio para confirmar la
    existencia de radicados reservados (RF-SEG-08, Ley 1712/2014).
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "document_not_found", "detail": str(document_id), "status": 404},
    )


class SignatureService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._slug = tenant_slug
        self._repo = SignatureRepository(conn)

    async def firmar(
        self,
        document_id: uuid.UUID,
        payload: SignatureCreate,
        *,
        signer: uuid.UUID | None,
        signer_name: str | None,
        max_clearance: int,
    ) -> SignatureResponse:
        """Resuelve la huella en el servidor y registra firma + asiento atómicamente.

        El `content_hash` del cuerpo se ignora. 404 neutro si el radicado no
        existe, supera el clearance o el anexo no es suyo; 503 si storage no
        responde (no se firma sin saber QUÉ se firma); 409 si el anexo no tiene
        huella.
        """
        if payload.anexo_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "anexo_requerido",
                    "detail": "Indica el anexo a firmar: el servidor resuelve su huella.",
                    "status": 422,
                },
            )
        file_id = await self._repo.anexo_file_id(document_id, payload.anexo_id, max_clearance)
        if file_id is None:
            raise not_found(document_id)
        try:
            huella = await storage_client.fetch_file_sha256(
                tenant_slug=self._slug, file_id=file_id
            )
        except storage_client.StorageContentError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "huella_no_verificable",
                    "detail": f"No se pudo resolver la huella del anexo, no se firma. ({exc})",
                    "status": 503,
                },
            ) from exc
        if not huella:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "anexo_sin_huella",
                    "detail": "El anexo no tiene huella registrada: no se firma lo que no se verifica.",
                    "status": 409,
                },
            )
        async with self._conn.transaction():
            row = await self._repo.insert(
                radicado_id=document_id,
                anexo_id=payload.anexo_id,
                signer_id=signer,
                signer_name=signer_name,
                content_hash=huella.lower(),
                reason=payload.reason,
                max_clearance=max_clearance,
            )
            if row is None:
                raise not_found(document_id)
            await audit.append(
                self._conn,
                tenant_slug=self._slug,
                service=_SERVICE,
                action="document.signature_created",
                object_type="radicado",
                object_ref=str(document_id),
                actor=str(signer) if signer else None,
                payload={
                    "signature_id": str(row["id"]),
                    "anexo_id": str(payload.anexo_id),
                    "content_hash": row["content_hash"],
                },
                canal=audit.CANAL_PETICION,
            )
        return SignatureResponse(**dict(row))
