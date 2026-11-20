# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Verificación pública de una firma por su identificador (ADR-054 §2).

``GET /api/v1/public/signature/{tenant}/{firma_id}``, sin sesión. Responde la
pregunta probatoria —«¿esta firma es válida, quién la hizo, cuándo, con qué sello
de tiempo, y está acreditada?»— sin exponer el documento.

- **Sin contenido.** Ni el XML ni el PDF: el veredicto y los metadatos que la
  firma declara (``PublicSignatureVerification``).
- **No es oráculo.** El ``firma_id`` es un UUID de 128 bits; uno inexistente, uno
  de otra entidad y uno de un objeto clasificado dan el MISMO 404. Quien consulta
  sin sesión tiene el clearance más bajo (Pública = 1), la misma regla que la
  consulta de radicado por código (``document/routers/public.py``).
- **El mismo motor.** El XML firmado vigente se descarga de storage y pasa por
  ``verificar_xades``, el mismo de ``POST /verify``, con la revocación personal en
  línea (ADR-054 §3).
- **La constancia simple se dice como tal.** Una firma ``nativa`` (HMAC) responde
  ``kind=simple_signature_record`` y ``valida=None``: un tercero no puede
  verificarla y la superficie no finge que sí.

El prefijo ``/api/v1/public/signature/`` (y no ``/api/v1/public/{tenant}/firma/``)
es para que el gateway lo enrute por prefijo a este servicio, igual que
``/api/v1/public/archive/``; el resto de ``/api/v1/public/`` es de document-service.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from orpycamcp_common.tenant import AtencionPublica, atencion_publica, slug_valido
from orpycamcp_common.tenant import tenant_schema as _schema_de

from signature.core.constants import (
    PROVIDERS_FIRMA_ELECTRONICA,
    signature_kind,
)
from signature.repositories.firma_repository import FirmaRepository
from signature.schemas.firma import PublicSignatureVerification
from signature.services.verification import verificar_xades
from storage import api as storage_api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/public/signature", tags=["public"])

#: Clearance de quien consulta sin sesión: Pública (RF-SEG-08).
CLEARANCE_ANONIMO = 1


def _no_encontrada() -> HTTPException:
    """404 neutro: el mismo cuerpo para entidad, firma u objeto que no se alcanza."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "detail": "No encontrado", "status": 404},
    )


async def get_public_tenant_conn(
    request: Request, tenant: str = Path(...)
) -> AsyncGenerator[asyncpg.Connection, None]:
    """Conexión de LECTURA pública al schema de ``tenant`` (ADR-035).

    Una entidad suspendida sigue respondiendo verificaciones (son lecturas);
    inexistente, dada de baja o sin aprovisionar: el 404 neutro. Si el registro
    de entidades no responde: 503, que no distingue entidades.
    """
    if not slug_valido(tenant):
        raise _no_encontrada()
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        try:
            atencion = await atencion_publica(conn, tenant)
        except Exception as exc:  # noqa: BLE001 — no poder saberlo se niega
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "tenant_estado_no_disponible",
                    "detail": "No se pudo comprobar el estado de la entidad.",
                    "status": 503,
                },
            ) from exc
        if atencion is AtencionPublica.NINGUNA:
            raise _no_encontrada()
        await conn.execute(f'SET search_path = "{_schema_de(tenant)}"')
        try:
            yield conn
        finally:
            await conn.execute("SET search_path = public")


async def _huella_documento(conn: asyncpg.Connection, row: asyncpg.Record) -> str | None:
    """SHA-256 del documento firmado, no el de los bytes del HMAC o del envoltorio.

    ``firma.payload_sha256`` NO siempre es la huella del documento (ver
    ``FirmaResponse.payload_sha256``): en la cadena, la del documento es
    ``firma_solicitud.payload_sha256``, y si esa solicitud no la fijó se responde
    ``None`` antes que publicar una huella que no es la del documento. Sin cadena
    (sello institucional de un índice o acta), el XML sellado ES el documento y su
    huella es la de la fila.
    """
    try:
        solicitud = await conn.fetchrow(
            "SELECT s.payload_sha256 FROM firma_cadena c "
            "JOIN firma_solicitud s ON s.id = c.solicitud_id "
            "WHERE c.firma_id = $1 LIMIT 1",
            row["id"],
        )
    except asyncpg.UndefinedTableError:
        solicitud = None
    if solicitud is not None:
        huella = solicitud["payload_sha256"]
        return f"sha256:{huella.strip()}" if huella else None
    return f"sha256:{row['payload_sha256'].strip()}"


@router.get(
    "/{tenant}/{firma_id}",
    response_model=PublicSignatureVerification,
    summary="Verificación pública de una firma por su identificador (ADR-054)",
)
async def verificar_publica(
    tenant: str,
    firma_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_public_tenant_conn),
) -> PublicSignatureVerification:
    repo = FirmaRepository(conn)
    row = await repo.get(firma_id, max_clearance=CLEARANCE_ANONIMO)
    if row is None:
        raise _no_encontrada()

    comun = {
        "firma_id": row["id"],
        "kind": signature_kind(row["provider"]),
        "firmante_nombre": row.get("firmante_nombre"),
        "firmado_en": row["sello_tiempo"],
        "huella_documento": await _huella_documento(conn, row),
        "constancia": f"/api/v1/signature/firmas/{row['id']}",
    }

    if row["provider"] not in PROVIDERS_FIRMA_ELECTRONICA:
        return PublicSignatureVerification(
            **comun,
            valida=None,
            firma_criptograficamente_valida=None,
            motivo="constancia_firma_simple_no_verificable_por_terceros",
            acreditado=False,
        )

    try:
        vigente = await repo.artefacto_vigente(firma_id)
    except asyncpg.UndefinedTableError:
        vigente = None
    minio_key = vigente["minio_key"] if vigente else row.get("minio_key_firmado")
    if not minio_key:
        return PublicSignatureVerification(
            **comun,
            valida=False,
            motivo="xml_firmado_no_disponible",
            nivel=row.get("xades_level"),
            acreditado=False,
        )
    try:
        xml = await storage_api.read_object(tenant_slug=tenant, object_key=minio_key)
    except storage_api.StorageError as exc:
        # Storage caído no es «firma inválida»: se dice que no se pudo comprobar.
        logger.warning("Verificación pública: no se pudo descargar el XML de %s", firma_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "verificacion_no_disponible",
                "detail": "No se pudo comprobar la firma en este momento.",
                "status": 503,
            },
        ) from exc

    veredicto = await verificar_xades(conn, row, xml.decode("utf-8", errors="replace"), tenant)
    return PublicSignatureVerification(
        **comun,
        valida=veredicto.valida,
        firma_criptograficamente_valida=veredicto.firma_criptograficamente_valida,
        revocacion_status=veredicto.revocacion_status,
        motivo=veredicto.motivo,
        nivel=veredicto.xades_level,
        sello_tiempo=veredicto.tsa_timestamp,
        acreditado=veredicto.acreditado,
    )
