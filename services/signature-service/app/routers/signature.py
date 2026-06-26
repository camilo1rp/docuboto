# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma de payloads (sign-xml) y verificación (E06, ADR-016, RF-FIR-11..14)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import settings
from app.core.database import get_tenant_conn
from app.core.security import get_current_user
from app.repositories.firma_repository import FirmaRepository
from app.schemas.firma import (
    FirmaResponse,
    SignXmlRequest,
    VerifyRequest,
    VerifyResponse,
)
from app.services import signer

try:
    from orpycamcp_common import audit as _audit
    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "signature-service"

router = APIRouter(prefix="/api/v1/signature", tags=["signature"])


@router.post("/sign-xml", response_model=FirmaResponse, status_code=status.HTTP_201_CREATED)
async def sign_xml(
    payload: SignXmlRequest,
    claims: dict[str, Any] = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> FirmaResponse:
    """
    Firma electrónica de un payload XML o hash de documento (RF-FIR-11..14).

    - El firmante se toma exclusivamente del JWT validado (claims["user_id"]).
    - Se verifica PERM_FIRMA antes de llegar aquí (ver get_current_user).
    - El sello_tiempo es generado por el servidor (no lo aporta el cliente).
    - La firma_blob es HMAC-SHA256(secret, sha256 | firmante | sello_tiempo),
      no reproducible sin conocer el secreto del servicio (RF-FIR-12).
    - El acto de firma queda registrado en audit_log (RF-FIR-14).

    TODO (RF-FIR-13): reto 2FA ligado al hash_documento — diferido a F4.
    """
    firmante_id = uuid.UUID(claims["user_id"])
    tenant_slug: str = claims.get("tenant_slug") or ""

    # Server-generated timestamp — never from the client request.
    sello_tiempo_dt = datetime.now(timezone.utc)
    sello_tiempo_str = sello_tiempo_dt.astimezone(timezone.utc).isoformat()

    result = signer.firmar(
        payload.payload_xml.encode(),
        str(firmante_id),
        sello_tiempo_str,
        settings.signature_secret,
    )

    row = await FirmaRepository(conn).insert(
        {
            "objeto_tipo": payload.objeto_tipo,
            "objeto_id": payload.objeto_id,
            "formato_solicitado": payload.formato,
            "firmante_id": firmante_id,
            "payload_sha256": result["payload_sha256"],
            "firma_blob": result["firma_blob"],
            "provider": result["provider"],
            "sello_tiempo": sello_tiempo_dt,
        }
    )

    # Immutable audit record — RF-FIR-14, ADR-008.
    if _AUDIT_AVAILABLE:
        await _audit.append(
            conn,
            tenant_slug=tenant_slug,
            service=_SERVICE,
            action="firma.creada",
            object_type=payload.objeto_tipo,
            object_ref=str(payload.objeto_id) if payload.objeto_id else None,
            actor=str(firmante_id),
            payload={
                "firma_id": str(row["id"]),
                "objeto_tipo": payload.objeto_tipo,
                "provider": result["provider"],
            },
        )

    d = dict(row)
    return FirmaResponse(
        firma_id=d["id"],
        objeto_tipo=d["objeto_tipo"],
        objeto_id=d.get("objeto_id"),
        formato_solicitado=d.get("formato_solicitado"),
        firmante_id=d.get("firmante_id"),
        payload_sha256=d["payload_sha256"],
        provider=d["provider"],
        sello_tiempo=d["sello_tiempo"],
        created_at=d["created_at"],
    )


@router.post("/verify", response_model=VerifyResponse)
async def verify(
    payload: VerifyRequest,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> VerifyResponse:
    """
    Verifica la integridad de una firma existente.

    No requiere autenticación — es una operación de consulta pública.
    Recomputa el HMAC con firmante_id y sello_tiempo almacenados en BD
    y lo compara con la firma_blob registrada.
    """
    row = await FirmaRepository(conn).get(payload.firma_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "firma_not_found", "detail": str(payload.firma_id), "status": 404},
        )

    firmante_id = str(row["firmante_id"]) if row["firmante_id"] else ""
    sello_tiempo_str = (
        row["sello_tiempo"].astimezone(timezone.utc).isoformat()
        if row["sello_tiempo"]
        else ""
    )

    valida = signer.verificar(
        payload.payload_xml.encode(),
        firmante_id,
        row["firma_blob"],
        sello_tiempo_str,
        settings.signature_secret,
    )
    return VerifyResponse(firma_id=row["id"], valida=valida, provider=row["provider"])
