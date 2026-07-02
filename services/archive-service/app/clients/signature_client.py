# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Cliente del signature-service para sellar el índice al cierre (E06 Inc.1, RT-16).

Best-effort: si el signature-service no está disponible (o rechaza la
solicitud), `sign_xml`/`verify` devuelven ``None`` y el llamador decide —
decisión del usuario: el cierre del expediente NO se bloquea, el índice queda
'pendiente_firma' y puede reintentarse vía `POST /indice/firmar`.

BUGFIX (E06 Inc.1): esta versión corrige que `sign_xml` NO enviaba
``X-User-Id`` — signature-service exige esa cabecera (``Depends(get_actor)``,
401 fail-closed) para atribuir el acto de firma (RF-FIR-11/12/14) a quien
cierra el expediente. Sin ella, /sign-xml SIEMPRE devolvía 401 y el índice
nunca llegaba a firmarse.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SignedIndice:
    """Resultado de sellar el índice electrónico con XAdES-B (E06 Inc.1).

    Espejo (subconjunto) del `FirmaResponse` de signature-service. NUNCA
    transporta material de clave — solo lo que el servicio expone
    públicamente. `acreditado` es SIEMPRE False en Inc.1 (no hay CA acreditada
    ONAC): no declarar conforme una firma cualificada (RF-FIR-15).
    """

    firma_id: uuid.UUID
    minio_key_firmado: str | None
    nivel_conformidad: str | None
    xades_level: str | None
    tipo_firma: str | None
    acreditado: bool
    certificado_fingerprint: str | None


@dataclass(slots=True)
class VerifyResult:
    """Veredicto de `POST /signature/verify` (E06 Inc.1, RF-FIR-15)."""

    valida: bool
    nivel_conformidad: str | None
    acreditado: bool
    motivo: str | None


async def sign_xml(
    *,
    tenant_slug: str,
    actor_id: str,
    payload_xml: str,
    objeto_id: str,
    indice_id: str,
    version: int,
) -> SignedIndice | None:
    """Solicita el sellado XAdES-B del índice vigente (E06 Inc.1).

    `actor_id` (X-User-Id, UUID interno de `auth_users.id`) viaja SIEMPRE como
    cabecera — nunca en el cuerpo (RF-FIR-11/12: el firmante NO se declara,
    se autentica). `indice_id`/`version` identifican la versión del índice que
    se está firmando (fila de `expediente_indice`), distinta de `objeto_id`
    (el `expediente_id`, preservado por compatibilidad con la auditoría).

    Best-effort: devuelve ``None`` ante cualquier fallo (sello no
    aprovisionado → 503, servicio inalcanzable, etc.) sin lanzar excepción —
    el llamador (`IndexService.firmar_indice`) deja el índice en
    'pendiente_firma' y no bloquea el cierre del expediente.
    """
    body = {
        "payload_xml": payload_xml,
        "objeto_tipo": "indice",
        "objeto_id": objeto_id,
        "formato": "XAdES",
        "indice_id": indice_id,
        "version": version,
    }
    headers = {"X-Tenant-Slug": tenant_slug, "X-User-Id": actor_id}
    try:
        async with httpx.AsyncClient(
            base_url=settings.signature_service_url, timeout=10.0
        ) as client:
            resp = await client.post("/api/v1/signature/sign-xml", json=body, headers=headers)
            if resp.status_code in (200, 201):
                data = resp.json()
                return SignedIndice(
                    firma_id=uuid.UUID(data["firma_id"]),
                    minio_key_firmado=data.get("minio_key_firmado"),
                    nivel_conformidad=data.get("nivel_conformidad"),
                    xades_level=data.get("xades_level"),
                    tipo_firma=data.get("tipo_firma"),
                    acreditado=bool(data.get("acreditado", False)),
                    certificado_fingerprint=data.get("certificado_fingerprint"),
                )
            logger.warning("signature-service → HTTP %s al firmar índice", resp.status_code)
    except httpx.RequestError as exc:
        logger.warning("signature-service inalcanzable al firmar índice: %s", exc)
    return None


async def verify(
    *,
    tenant_slug: str,
    firma_id: str,
    payload_xml: str,
) -> VerifyResult | None:
    """Verifica la firma XAdES-B del índice (E06 Inc.1, público en signature-service).

    IMPORTANTE — cambio de semántica: `payload_xml` debe ser el XML YA
    FIRMADO (descargado de MinIO vía `app.clients.storage_client`), NO el XML
    en claro que produce `index_builder.build_xml`. El llamador
    (`IndexService.verify`) es responsable de esa descarga.

    Best-effort: devuelve ``None`` si el signature-service no está disponible
    (el llamador no declara conforme lo que no pudo verificar, RF-FIR-15).
    """
    body = {"firma_id": firma_id, "payload_xml": payload_xml}
    headers = {"X-Tenant-Slug": tenant_slug}
    try:
        async with httpx.AsyncClient(
            base_url=settings.signature_service_url, timeout=5.0
        ) as client:
            resp = await client.post("/api/v1/signature/verify", json=body, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return VerifyResult(
                    valida=bool(data.get("valida")),
                    nivel_conformidad=data.get("nivel_conformidad"),
                    acreditado=bool(data.get("acreditado", False)),
                    motivo=data.get("motivo"),
                )
            logger.warning("signature-service → HTTP %s al verificar índice", resp.status_code)
    except httpx.RequestError as exc:
        logger.warning("signature-service inalcanzable al verificar índice: %s", exc)
    return None
