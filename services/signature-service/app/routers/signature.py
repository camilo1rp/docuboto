# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma de payloads (sign-xml) y verificación (E06, ADR-016)."""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.core.database import get_tenant_conn
from app.repositories.firma_repository import FirmaRepository
from app.schemas.firma import (
    FirmaResponse,
    SignXmlRequest,
    VerifyRequest,
    VerifyResponse,
)
from app.services import signer

router = APIRouter(prefix="/api/v1/signature", tags=["signature"])


@router.post("/sign-xml", response_model=FirmaResponse, status_code=status.HTTP_201_CREATED)
async def sign_xml(
    payload: SignXmlRequest,
    x_user_id: str | None = Header(default=None),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> FirmaResponse:
    firmante = payload.firmante_id or (uuid.UUID(x_user_id) if x_user_id else None)
    result = signer.firmar(payload.payload_xml.encode(), str(firmante) if firmante else "")
    row = await FirmaRepository(conn).insert(
        {
            "objeto_tipo": payload.objeto_tipo,
            "objeto_id": payload.objeto_id,
            "formato_solicitado": payload.formato,
            "firmante_id": firmante,
            "payload_sha256": result["payload_sha256"],
            "firma_blob": result["firma_blob"],
            "provider": result["provider"],
        }
    )
    d = dict(row)
    return FirmaResponse(
        firma_id=d["id"],
        **{
            k: d[k]
            for k in (
                "objeto_tipo",
                "objeto_id",
                "formato_solicitado",
                "payload_sha256",
                "provider",
                "created_at",
            )
        },
    )


@router.post("/verify", response_model=VerifyResponse)
async def verify(
    payload: VerifyRequest,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> VerifyResponse:
    row = await FirmaRepository(conn).get(payload.firma_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "firma_not_found", "detail": str(payload.firma_id), "status": 404},
        )
    firmante = str(row["firmante_id"]) if row["firmante_id"] else ""
    valida = signer.verificar(payload.payload_xml.encode(), firmante, row["firma_blob"])
    return VerifyResponse(firma_id=row["id"], valida=valida, provider=row["provider"])
