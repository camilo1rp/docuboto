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
que extraerlo); la dependencia `get_public_tenant_conn` valida el slug, sirve a una entidad OPERATIVA o SUSPENDIDA en lectura (las escrituras de la suspendida: 503 `canal_no_disponible`; inexistente o sin aprovisionar: 404 neutro) y fija el search_path
(ADR-002/012). Solo se expone información NO sensible (sin datos personales ni
contenido del documento).
"""

from typing import Any

import asyncpg
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from orpycamcp_common import identidad
from orpycamcp_common.tenant import slug_valido

from app.core.database import (
    get_public_tenant_conn,
    get_public_tenant_conn_escritura,
    get_redis,
    get_tenant_client,
)
from app.schemas.public import (
    IDEMPOTENCY_KEY_RE,
    PoliticaTratamiento,
    PqrsCreate,
    PqrsResponse,
    PublicVerifyResponse,
    RetoAntirrobots,
    TipoVentanilla,
    VentanillaEstado,
    VentanillaRadicacion,
    VentanillaRespuesta,
)
from app.services import antirrobots
from app.services.document_service import DocumentService, InvalidTenantError
from app.services.ventanilla_service import VentanillaService

router = APIRouter(prefix="/api/v1/public", tags=["public"])


def _clave_idempotencia(valor: str | None) -> str | None:
    """Valida la `Idempotency-Key` pública; una clave mal formada es 422, no se ignora.

    Ignorarla en silencio dejaría al cliente creyendo que su reintento está
    protegido cuando no lo está.
    """
    if valor is None or not valor.strip():
        return None
    valor = valor.strip()
    if not IDEMPOTENCY_KEY_RE.match(valor):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "idempotency_key_invalida",
                "detail": "Idempotency-Key debe tener 16-128 caracteres [A-Za-z0-9_-].",
                "status": 422,
            },
        )
    return valor


def _ip_cliente(request: Request) -> str | None:
    """IP del cliente que el GATEWAY firmó para esta ruta (ADR-031), o None.

    Nunca lee `X-Forwarded-For` ni un `X-Real-Ip` sin firma: los escribe el
    cliente. Sin firma válida (llamada directa, firma vencida o de otra ruta, sin
    secreto) la constancia registra `ip=null, ip_verificada=false`.
    """
    return identidad.ip_publica_verificada(dict(request.headers), request.url.path)


@router.post(
    "/{tenant}/pqrs",
    response_model=PqrsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Radicación ciudadana de PQRS (público, Ley 1755/2015)",
)
async def radicar_pqrs(
    tenant: str,
    payload: PqrsCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    conn: asyncpg.Connection = Depends(get_public_tenant_conn_escritura),
    tenant_client: Any = Depends(get_tenant_client),
    redis: Any = Depends(get_redis),
) -> PqrsResponse:
    clave = _clave_idempotencia(idempotency_key)
    try:
        service = DocumentService(conn, tenant, tenant_client, redis)
    except InvalidTenantError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_tenant_slug", "detail": str(exc), "status": 400},
        )
    # DELEGA en la ventanilla única, que es lo que su propio esquema dice
    # que hace (`PqrsCreate`: «Delega en la ventanilla única mapeando `tipo`
    # al tipo documental que la entidad haya declarado»).
    #
    # No lo hacía: construía el `DocumentCreate` a mano y llamaba directo a
    # `DocumentService.create`, saltándose las tres decisiones que la
    # ventanilla declara suyas —el tipo admisible del catálogo, la
    # dependencia que recibe, y el enrutamiento a su bandeja—. El resultado
    # era exactamente el defecto que el módulo de la ventanilla dice haber
    # corregido: el radicado entraba con `dest_dept` nulo, fuera de la
    # bandeja de todos, con el término de la Ley 1755 corriendo. Y en una
    # entidad que deliberadamente NO habilitó la radicación en línea —la que
    # por `/ventanilla` responde 503— esta puerta seguía admitiendo.
    #
    # Dos puertas para el mismo acto con reglas distintas no se parchean:
    # se deja una sola fuente. La heredada se conserva porque es una
    # superficie pública ya publicada, pero pasa por donde pasan las demás.
    ventanilla = VentanillaRadicacion(
        tipo_documental=payload.tipo,
        nombre=payload.nombre,
        identificacion=payload.identificacion,
        email=payload.email,
        asunto=payload.asunto,
        descripcion=payload.descripcion,
        acepta_tratamiento=payload.acepta_tratamiento,
        politica_version=payload.politica_version,
        altcha=payload.altcha,
    )
    respuesta = await VentanillaService(conn, tenant, tenant_client, redis).radicar(
        ventanilla,
        service,
        idempotency_key=clave,
        ip=_ip_cliente(request),
    )
    return PqrsResponse(
        radicado_id=respuesta.radicado_id,
        tracking_number=respuesta.tracking_number,
        verification_code=respuesta.verification_code,
    )


@router.get("/{tenant}/verify/{code}", response_model=PublicVerifyResponse)
async def verify_radicado(
    tenant: str,
    code: str,
    conn: asyncpg.Connection = Depends(get_public_tenant_conn),
) -> PublicVerifyResponse:
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

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "detail": "No encontrado",
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
    conn: asyncpg.Connection = Depends(get_public_tenant_conn_escritura),
) -> list[TipoVentanilla]:
    """Sin esto, quien radica tendría que adivinar el código de un tipo documental.

    Solo devuelve lo que la entidad marcó `admite_ventanilla_publica`: no expone
    su catálogo interno.
    """
    return await VentanillaService(conn, tenant).tipos_admitidos()


@router.get(
    "/{tenant}/ventanilla/politica",
    response_model=PoliticaTratamiento,
    summary="Política de tratamiento de datos vigente de la entidad (Ley 1581, público)",
)
async def politica_de_ventanilla(
    tenant: str,
    conn: asyncpg.Connection = Depends(get_public_tenant_conn),
) -> PoliticaTratamiento:
    """Aviso de privacidad que el formulario muestra antes de pedir la autorización.

    Lectura estricta: si la tabla de parámetros no se puede leer es 503
    `politica_no_verificable`, nunca `publicada=false` (error no es «sin política»).
    La `huella` es del servidor. Se lee DIRECTO de `sgd_param_admin` y no por
    tenant-service, que veta a la entidad suspendida (ADR-035).

    Es LECTURA (`get_public_tenant_conn`): se sirve con la entidad operativa o
    suspendida; inexistente, dada de baja o sin aprovisionar responde el 404 neutro
    (ADR-035). No recibe datos del ciudadano, y el aviso se conoce antes de darlos.
    """
    # Una entidad operativa sin la tabla de dependencias responde neutro (sin
    # política) y NO 503. El 503 queda para fallos reales de tenant-service.
    if await conn.fetchval("SELECT to_regclass('dependencias')") is None:
        return PoliticaTratamiento(publicada=False)
    return await VentanillaService(conn, tenant).politica()


@router.get(
    "/{tenant}/ventanilla/reto",
    response_model=RetoAntirrobots,
    summary="Reto antirrobots ALTCHA para radicar (público)",
)
async def reto_de_ventanilla(tenant: str, response: Response) -> RetoAntirrobots:
    """Entrega un reto de prueba de trabajo ya firmado (C16, ADR-038).

    **Sin base de datos y sin Redis**: emitir es puro cálculo y no escribe nada
    que un anónimo pueda amplificar. El reto queda atado a la entidad de la ruta
    (`t` en el `salt`) y vence a los `ALTCHA_TTL_SEGUNDOS`. Se consume —un solo
    uso— al radicar, no al emitirlo. Cae en el cubo anónimo general del gateway,
    no en el estrecho de radicación. Slug inválido: 400 `invalid_tenant_slug`.
    """
    if not slug_valido(tenant):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_tenant_slug",
                "detail": "El identificador de la entidad no es válido",
                "status": 400,
            },
        )
    response.headers["Cache-Control"] = "no-store"
    return RetoAntirrobots(**antirrobots.emitir(tenant))


@router.get(
    "/{tenant}/ventanilla/estado",
    response_model=VentanillaEstado,
    summary="¿Está habilitada la ventanilla pública de la entidad? (verificación previa)",
)
async def estado_de_ventanilla(
    tenant: str,
    conn: asyncpg.Connection = Depends(get_public_tenant_conn_escritura),
    tenant_client: Any = Depends(get_tenant_client),
    redis: Any = Depends(get_redis),
) -> VentanillaEstado:
    """Comprueba, sin radicar, lo que `POST /ventanilla` exige por dentro.

    Inexistente, dada de baja o sin aprovisionar: el 404 neutro de las rutas públicas
    (ADR-035). Suspendida: `503 canal_no_disponible` + `Retry-After` (la ventanilla
    es una escritura pública y está cerrada; quien consulta merece saberlo).
    """
    return await VentanillaService(conn, tenant, tenant_client, redis).estado()


@router.post(
    "/{tenant}/ventanilla",
    response_model=VentanillaRespuesta,
    status_code=status.HTTP_201_CREATED,
    summary="Radicación externa por ventanilla única (público)",
)
async def radicar_en_ventanilla(
    tenant: str,
    request: Request,
    datos: str = Form(
        ...,
        description=(
            "Los campos de la radicación en JSON (ver VentanillaRadicacion). Viajan "
            "en una parte del multipart para que la radicación y sus anexos sean "
            "UNA sola petición."
        ),
    ),
    anexos: list[UploadFile] = File(default=[]),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    conn: asyncpg.Connection = Depends(get_public_tenant_conn_escritura),
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
    clave = _clave_idempotencia(idempotency_key)
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
    # terminar. El ORDEN es la mitad de la defensa: número y tamaño se comprueban
    # ANTES de traer un solo byte a memoria, y la lectura va acotada.
    #
    # Antes se acotaba solo el NÚMERO y a continuación se hacía `await a.read()`
    # sin tope: los cinco ficheros enteros entraban en RAM y el 422 por tamaño
    # llegaba cuando el daño ya estaba hecho. Como la puerta es anónima, bastaba
    # un multipart grande para tumbar la radicación de la entidad. El gemelo
    # correcto ya existía en storage-service (`_reject_if_too_large`), que mira
    # `file.size` antes de leer; ese criterio no había llegado hasta aquí.
    from app.core.anexos_publicos import (
        MAX_ANEXOS,
        MAX_BYTES_POR_ANEXO,
        AnexoRechazado,
        validar_conjunto,
    )

    def _rechazar(mensaje: str) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "anexo_rechazado", "detail": mensaje, "status": 422},
        )

    if len(anexos) > MAX_ANEXOS:
        raise _rechazar(
            f"Se admiten como máximo {MAX_ANEXOS} archivos por radicación; "
            f"se enviaron {len(anexos)}."
        )

    # Starlette ya spooled cada parte y conoce su tamaño, así que este chequeo es
    # gratis y evita el traslado de una subida que se va a rechazar.
    declarados = [a.size for a in anexos]
    if all(t is not None for t in declarados):
        try:
            validar_conjunto([t for t in declarados if t is not None])
        except AnexoRechazado as exc:
            raise _rechazar(str(exc)) from exc

    # Lectura ACOTADA. El chequeo anterior depende de que el parser declare el
    # tamaño; este no depende de nada, así que el consumo de memoria queda
    # limitado aunque `size` venga vacío. Se pide un byte de más justamente para
    # poder distinguir «cabe justo» de «se pasa».
    ficheros: list[tuple[str, bytes]] = []
    for anexo in anexos:
        contenido = await anexo.read(MAX_BYTES_POR_ANEXO + 1)
        if len(contenido) > MAX_BYTES_POR_ANEXO:
            raise _rechazar(
                f"Cada archivo puede pesar como máximo "
                f"{MAX_BYTES_POR_ANEXO // (1024 * 1024)} MB."
            )
        ficheros.append((anexo.filename or "anexo", contenido))

    # El tope del conjunto se reafirma aquí sobre los bytes REALES leídos.
    try:
        validar_conjunto([len(contenido) for _, contenido in ficheros])
    except AnexoRechazado as exc:
        raise _rechazar(str(exc)) from exc

    try:
        service = DocumentService(conn, tenant, tenant_client, redis)
    except InvalidTenantError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_tenant_slug", "detail": str(exc), "status": 400},
        )
    return await VentanillaService(conn, tenant, tenant_client, redis).radicar(
        payload,
        service,
        ficheros,
        idempotency_key=clave,
        ip=_ip_cliente(request),
    )
