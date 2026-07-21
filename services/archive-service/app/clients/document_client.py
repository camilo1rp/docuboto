# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Cliente del document-service: resolución cross-servicio de los `file_id` de
los anexos de un lote de radicados (E10 debt, cableado del AIP OAIS al cierre
del expediente — ver `.claude/agent-memory/orfeo-architect/
project_aip-wiring-e10-diseno.md`).

archive-service conoce `exp.radicados` (`{radicado_id, content_hash, estado}`)
pero NO los `file_id` de sus anexos digitales — esa tabla vive en
document-service. `POST /api/v1/internal/documentos/anexos`
(`document-service/app/routers/internal.py`) resuelve el inventario TOTAL en
UN solo round-trip batch, SIN el gate de clearance (no-read-up, RF-SEG-08)
que sí aplica `GET /documents/{id}/anexos` — el empaquetado del AIP es un
ACTO DE SISTEMA que necesita el inventario COMPLETO (filtrar por el
clearance de quien cerró el expediente dejaría anexos fuera en silencio,
AIP incompleto), mismo criterio que `storage_client.get_aip`/
`proteger_indice_worm_internal`.

D-02: `X-Internal-Token` compartido con `document-service.
INTERNAL_SERVICE_TOKEN` — único llamador legítimo de ese endpoint interno.
"""

from __future__ import annotations

import logging
import uuid

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class DocumentClientError(Exception):
    """document-service no respondió (o respondió con error) al pedir el
    inventario de anexos — el llamador (`AipWiringService`) lo trata como
    fallo transitorio (backoff), NUNCA sustituye un inventario vacío en su
    lugar (RF-FIR-15: un AIP "completo" con documentos faltantes por un
    error de red sería peor que no empaquetarlo todavía)."""


async def fetch_anexos_internal(
    *,
    tenant_slug: str,
    radicado_ids: list[uuid.UUID],
    max_clearance: int | None = None,
) -> dict[str, list[dict]]:
    """Inventario de anexos (`file_id`/`filename`) de un LOTE de radicados — UN
    solo round-trip batch hacia `POST /api/v1/internal/documentos/anexos`.

    `max_clearance=None` (AIP, acto de sistema): inventario TOTAL sin gate.
    `max_clearance` con valor (exportación, RF-SEG-08): document-service filtra
    por `nivel_seguridad <= max_clearance` y devuelve una entrada por cada
    radicado VISIBLE (aunque sin anexos), OMITIENDO los sobre-clearance — así el
    llamador puede filtrar también el manifiesto (sin fuga de existencia).

    Devuelve ``{}`` sin llamada HTTP si ``radicado_ids`` está vacío. Forma de
    retorno: ``{str(radicado_id): [{"file_id", "filename"}, ...]}``.

    Lanza ``DocumentClientError`` ante cualquier fallo (red, timeout, HTTP
    distinto de 200)."""
    if not radicado_ids:
        return {}
    body: dict = {"radicado_ids": [str(r) for r in radicado_ids]}
    if max_clearance is not None:
        body["max_clearance"] = max_clearance
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.document_internal_token,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.document_service_url, timeout=30.0
        ) as client:
            resp = await client.post(
                "/api/v1/internal/documentos/anexos", json=body, headers=headers
            )
    except httpx.RequestError as exc:
        raise DocumentClientError(
            f"document-service inalcanzable al pedir el inventario de anexos: {exc}"
        ) from exc
    if resp.status_code != 200:
        raise DocumentClientError(
            f"document-service → HTTP {resp.status_code} al pedir el inventario de "
            f"anexos: {resp.text[:300]!r}"
        )
    return resp.json()


async def fetch_radicado_metadata_internal(
    *, tenant_slug: str, radicado_ids: list[uuid.UUID], max_clearance: int
) -> dict[str, dict]:
    """Metadata (`subject`/`doc_type`) de los radicados VISIBLES para enriquecer
    el manifiesto de exportación (E02/export) — clearance-aware (no-read-up).
    Devuelve ``{str(radicado_id): {"subject", "doc_type"}}`` solo de los visibles.

    Lanza ``DocumentClientError`` ante cualquier fallo."""
    if not radicado_ids:
        return {}
    body = {
        "radicado_ids": [str(r) for r in radicado_ids],
        "max_clearance": max_clearance,
    }
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.document_internal_token,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.document_service_url, timeout=30.0
        ) as client:
            resp = await client.post(
                "/api/v1/internal/documentos/metadata", json=body, headers=headers
            )
    except httpx.RequestError as exc:
        raise DocumentClientError(
            f"document-service inalcanzable al pedir la metadata de radicados: {exc}"
        ) from exc
    if resp.status_code != 200:
        raise DocumentClientError(
            f"document-service → HTTP {resp.status_code} al pedir la metadata de radicados"
        )
    return resp.json()
