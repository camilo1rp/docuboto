# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Consulta pública por código de verificación (E13, F2).

Endpoint SIN autenticación: un ciudadano consulta la trazabilidad básica de su
radicado con un código no adivinable. El tenant viaja en la ruta (no hay JWT del
que extraerlo); el slug se valida y se fija el search_path al schema del tenant
(ADR-002/012). Solo se expone información NO sensible (sin datos personales ni
contenido del documento).
"""

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.core.database import (
    get_db_conn,
    get_redis,
    get_tenant_client,
    tenant_schema,
)
from app.schemas.document import DocumentCreate
from app.schemas.public import (
    PqrsCreate,
    PqrsResponse,
    PublicVerifyResponse,
    TipoVentanilla,
    VentanillaRadicacion,
    VentanillaRespuesta,
)
from app.services.document_service import DocumentService, InvalidTenantError
from app.services.ventanilla_service import VentanillaService

router = APIRouter(prefix="/api/v1/public", tags=["public"])


@router.post(
    "/{tenant}/pqrs",
    response_model=PqrsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Radicación ciudadana de PQRS (público, Ley 1755/2015)",
)
async def radicar_pqrs(
    tenant: str,
    payload: PqrsCreate,
    conn: asyncpg.Connection = Depends(get_db_conn),
    tenant_client: Any = Depends(get_tenant_client),
    redis: Any = Depends(get_redis),
) -> PqrsResponse:
    schema = tenant_schema(tenant)  # valida el slug
    await conn.execute(f'SET search_path = "{schema}", public')
    try:
        try:
            service = DocumentService(conn, tenant, tenant_client, redis)
        except InvalidTenantError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "invalid_tenant_slug", "detail": str(exc), "status": 400},
            )
        doc = DocumentCreate(
            doc_type="E",
            subject=payload.asunto,
            sender_name=payload.nombre,
            observations=payload.descripcion,
            metadata={
                "canal": "pqrs_ciudadano",
                "tipo_pqrs": payload.tipo,
                "ciudadano": {
                    "nombre": payload.nombre,
                    "identificacion": payload.identificacion,
                    "email": payload.email,
                },
            },
        )
        result = await service.create(doc, registered_by=None)
        code = await conn.fetchval(
            "SELECT verification_code FROM radicados WHERE id = $1", result.id
        )
    finally:
        await conn.execute("SET search_path = public")
    return PqrsResponse(
        radicado_id=result.id,
        tracking_number=result.tracking_number,
        verification_code=code,
    )


@router.get("/{tenant}/verify/{code}", response_model=PublicVerifyResponse)
async def verify_radicado(
    tenant: str,
    code: str,
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> PublicVerifyResponse:
    schema = tenant_schema(tenant)  # valida el slug (anti-inyección)
    await conn.execute(f'SET search_path = "{schema}", public')
    try:
        # NO-READ-UP EN UNA SUPERFICIE ANÓNIMA (RF-SEG-08).
        #
        # `verification_code` lo tiene **todo** radicado: la migración 010 lo
        # añadió con `DEFAULT gen_random_uuid()`, así que existe también para
        # los internos y para los clasificados, que nunca tuvieron un ciudadano
        # detrás. Sin este filtro, quien tuviera el código de un radicado
        # RESERVADO obtenía, sin autenticarse y a través del gateway, su número
        # oficial, su tipo, su estado y su fecha. El código no es adivinable
        # (128 bits), pero vive en una columna que viaja en respaldos, informes
        # y exportaciones: allí donde se filtre uno, seguía una lectura anónima
        # de metadatos clasificados. Y el resto del sistema trata la EXISTENCIA
        # de un radicado reservado como protegida — el acceso directo devuelve
        # 404 y no 403 precisamente para no confirmarla.
        #
        # Un llamante anónimo tiene, por definición, el clearance más bajo
        # (Pública = 1). Esto no es una política nueva: es la que ya rige en
        # todo el sistema, que faltaba en esta puerta.
        #
        # **No se usa `_PUBLIC`** (el chokepoint de OAI/CMIS, que exige
        # `publicable AND nivel_seguridad = 1 AND status <> 'anulado'`) porque
        # aquí serían dos errores: una PQRS ciudadana NO es un documento
        # publicado de oficio —exigir `publicable` rompería la consulta para
        # todas—, y ocultar los anulados haría creer al ciudadano que su código
        # es inválido cuando lo que pasó es que su radicado se anuló, que es
        # justo lo que tiene derecho a saber.
        row = await conn.fetchrow(
            "SELECT tracking_number, doc_type, status, registered_at, dest_dept "
            "FROM radicados WHERE verification_code = $1 AND nivel_seguridad = 1",
            code,
        )
    except (
        asyncpg.exceptions.UndefinedTableError,
        asyncpg.exceptions.InvalidSchemaNameError,
    ):
        row = None  # tenant inexistente → tratar como no encontrado (sin filtrar)
    finally:
        await conn.execute("SET search_path = public")

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "detail": "Código de verificación no encontrado",
                "status": 404,
            },
        )
    return PublicVerifyResponse(**dict(row))


# ---------------------------------------------------------------------------
# Ventanilla única de radicación
# ---------------------------------------------------------------------------


@router.get(
    "/{tenant}/ventanilla/tipos",
    response_model=list[TipoVentanilla],
    summary="Qué tipos de trámite recibe la entidad por ventanilla pública",
)
async def tipos_de_ventanilla(
    tenant: str,
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[TipoVentanilla]:
    """Sin esto, quien radica tendría que adivinar el código de un tipo documental.

    Solo devuelve lo que la entidad marcó `admite_ventanilla_publica`: no expone
    su catálogo interno.
    """
    schema = tenant_schema(tenant)  # valida el slug (anti-inyección)
    await conn.execute(f'SET search_path = "{schema}", public')
    try:
        return await VentanillaService(conn, tenant).tipos_admitidos()
    except (
        asyncpg.exceptions.UndefinedTableError,
        asyncpg.exceptions.InvalidSchemaNameError,
    ):
        # Tenant inexistente: lista vacía, no un error que confirme si existe.
        return []
    finally:
        await conn.execute("SET search_path = public")


@router.post(
    "/{tenant}/ventanilla",
    response_model=VentanillaRespuesta,
    status_code=status.HTTP_201_CREATED,
    summary="Radicación externa por ventanilla única (público)",
)
async def radicar_en_ventanilla(
    tenant: str,
    datos: str = Form(
        ...,
        description=(
            "Los campos de la radicación en JSON (ver VentanillaRadicacion). Viajan "
            "en una parte del multipart para que la radicación y sus anexos sean "
            "UNA sola petición."
        ),
    ),
    anexos: list[UploadFile] = File(default=[]),
    conn: asyncpg.Connection = Depends(get_db_conn),
    tenant_client: Any = Depends(get_tenant_client),
    redis: Any = Depends(get_redis),
) -> VentanillaRespuesta:
    """La puerta por la que entra la correspondencia externa de la entidad.

    Sustituye a la radicación «de PQRS», que fijaba en código la taxonomía de la
    Ley 1755 y no admitía ni una tutela ni un oficio de otra entidad.

    **Multipart, con los datos en una parte JSON y los anexos en otras.** Una
    ventanilla cuyo caso principal —una tutela con sus soportes— necesitara un
    endpoint distinto del caso simple serían dos puertas, que es justo lo que
    una ventanilla ÚNICA no debe tener. Sin anexos, el multipart lleva una sola
    parte y ya está.

    Los anexos se validan y se trasladan ANTES de asignar número: un número de
    radicado es inmutable (Ley 594/2000), así que no puede quedar uno emitido
    al que le falten sus soportes.
    """
    try:
        payload = VentanillaRadicacion.model_validate_json(datos)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "datos_invalidos",
                "detail": f"La parte 'datos' no es una radicación válida: {exc}",
                "status": 422,
            },
        ) from exc

    # Se leen aquí, dentro del request, porque `UploadFile` se cierra al
    # terminar; y se acota el número ANTES de leer, para no pagar el traslado a
    # memoria de una subida que se va a rechazar.
    from app.core.anexos_publicos import MAX_ANEXOS

    if len(anexos) > MAX_ANEXOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "anexo_rechazado",
                "detail": (
                    f"Se admiten como máximo {MAX_ANEXOS} archivos por radicación; "
                    f"se enviaron {len(anexos)}."
                ),
                "status": 422,
            },
        )
    ficheros = [(a.filename or "anexo", await a.read()) for a in anexos]

    schema = tenant_schema(tenant)
    await conn.execute(f'SET search_path = "{schema}", public')
    try:
        try:
            service = DocumentService(conn, tenant, tenant_client, redis)
        except InvalidTenantError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "invalid_tenant_slug", "detail": str(exc), "status": 400},
            )
        return await VentanillaService(conn, tenant).radicar(payload, service, ficheros)
    finally:
        await conn.execute("SET search_path = public")
