# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Endpoints internos de archive-service (D-02, ``X-Internal-Token``).

Servicio-a-servicio únicamente: el gateway no enruta ``/api/v1/internal/*``.
Router PROPIO, separado de ``expedientes``/``indice``, para que su gate
(token, no JWT/RBAC) no se mezcle con el de las rutas de usuario.

``GET /expedientes/{ref}/indice/huella`` (AREA-04-09 Media-2): la huella que una
cadena de firma sobre un ``expediente`` sella es la del ÍNDICE ELECTRÓNICO
vigente —el artefacto canónico del expediente—, no el identificador. Es una
huella (content-free); el no-read-up lo aplica signature-service al abrir la
cadena, así que aquí no hay gate de clearance.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.core.database import get_db_conn, tenant_schema
from app.core.internal_auth import require_internal_token

router = APIRouter(
    prefix="/api/v1/internal/expedientes",
    tags=["internal"],
    dependencies=[Depends(require_internal_token)],
)


@router.get(
    "/{ref}/indice/huella",
    summary="SHA-256 del índice electrónico VIGENTE de un expediente (acto de sistema)",
)
async def huella_indice_vigente(
    ref: str,
    x_tenant_slug: str | None = Header(default=None),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> dict[str, object]:
    """`ref` es el código del expediente o su UUID (lo que la cadena guarda como
    `objeto_id`). Devuelve la huella canónica (`xml_sha256`, C14N) de la última
    versión del índice, que es la que el firmante vio y firma.

    `400` sin tenant; `404` si el expediente no existe; `409 indice_no_disponible`
    si aún no tiene índice generado (no hay contenido que firmar).
    """
    if not x_tenant_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_tenant",
                "detail": "Falta la cabecera X-Tenant-Slug",
                "status": 400,
            },
        )
    schema = tenant_schema(x_tenant_slug)  # 400 si el slug no es válido
    await conn.execute(f'SET search_path TO "{schema}", public')
    expediente = await conn.fetchrow(
        "SELECT id, code FROM expedientes WHERE code = $1 OR id::text = $1 LIMIT 1", ref
    )
    if expediente is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "expediente_not_found",
                "detail": f"Expediente {ref} no encontrado",
                "status": 404,
            },
        )
    indice = await conn.fetchrow(
        "SELECT version, estado, xml_sha256, num_documentos FROM expediente_indice "
        "WHERE expediente_id = $1 ORDER BY version DESC LIMIT 1",
        expediente["id"],
    )
    if indice is None or not indice["xml_sha256"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "indice_no_disponible",
                "detail": (
                    "El expediente no tiene índice electrónico generado: no hay "
                    "contenido que firmar."
                ),
                "status": 409,
            },
        )
    return {
        "expediente_id": str(expediente["id"]),
        "code": expediente["code"],
        "version": indice["version"],
        "estado": indice["estado"],
        "num_documentos": indice["num_documentos"],
        "huella_sha256": str(indice["xml_sha256"]).strip().lower(),
    }
