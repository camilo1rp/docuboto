# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Ingesta de correo (E19): cuentas, autorización OAuth2 y bandeja compartida.

Hubo una ruta heredada —`POST /ingest/email/poll` y `GET /ingest/email/estado`,
sobre las variables `IMAP_*`— que configuraba UN solo buzón para todo el
despliegue y radicaba de golpe todo lo no leído. Queda retirada, y no por
limpieza: no podía expresar lo que exige el Acuerdo AGN 060/2001 art. 13
(«asignarán responsabilidades de acuerdo con la cantidad de cuentas
habilitadas»), guardaba la contraseña en claro en el entorno, y radicaba el
buzón entero —publicidad incluida— porque no tenía dónde separar lo que se
radica de lo que no.

Lo que la sustituye vive aquí abajo, en tres carriles:

* **`/email/apps-oauth`** — la aplicación OAuth2 *interna* de cada entidad, con
  el `client_secret` cifrado en reposo.
* **`/email/cuentas`** — una fila por buzón (institucional o personal del
  funcionario), con su carpeta de origen, su carpeta «Radicados», su modo
  (asistido o automático) y su responsable.
* **`/email/cuentas/{id}/bandeja`** — el espejo del buzón: los funcionarios ven
  lo que hay, reservan un mensaje mientras lo trabajan y radican. Un correo
  radicado sale de la bandeja y se mueve en el servidor, así que dos personas no
  pueden radicarlo dos veces.

Gate: `USUA_PERM_ADMIN` para configurar, `PERM_RADI` para trabajar la bandeja.
El recorte de QUÉ cuentas alcanza cada quien no vive en el gate sino en el
`WHERE` del repositorio.
"""

import logging
import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status

from app.core.clearance import check_permission, require_permission
from app.core.database import get_redis, get_tenant_client, get_tenant_conn
from app.repositories.email_mensaje_repository import EmailMensajeRepository
from app.schemas.email_cuenta import (
    AppOAuthCrear,
    AppOAuthResponse,
    CuentaCrear,
    CuentaResponse,
    PruebaConexion,
)
from app.services.document_service import DocumentService, InvalidTenantError
from app.services.email_bandeja_service import EmailBandejaService
from app.services.email_cuenta_service import EmailCuentaService, ErrorDeCuenta

logger = logging.getLogger(__name__)


def _tenant_slug(request: Request) -> str:
    slug = request.headers.get("X-Tenant-Slug", "")
    if not slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "missing_tenant", "detail": "X-Tenant-Slug requerido", "status": 400},
        )
    return slug


# ═══════════════════════════════════════════════════════════════════════════
# Cuentas de correo
#
# Gate: `USUA_PERM_ADMIN` para configurar. El recorte de QUÉ cuentas alcanza
# cada quien no vive aquí sino en el `WHERE` del repositorio: una cuenta
# personal es de su dueño, una institucional de sus funcionarios asignados, y
# el administrador ve las institucionales pero **no** las personales de nadie.
# ═══════════════════════════════════════════════════════════════════════════

cuentas_router = APIRouter(prefix="/api/v1/email/cuentas", tags=["email-cuentas"])
apps_router = APIRouter(prefix="/api/v1/email/apps-oauth", tags=["email-cuentas"])


def _actor(request: Request) -> uuid.UUID:
    crudo = request.headers.get("X-User-Id", "")
    try:
        return uuid.UUID(crudo)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "missing_user", "detail": "X-User-Id requerido", "status": 400},
        )


async def _es_admin(conn: asyncpg.Connection, actor: uuid.UUID) -> bool:
    """Si el llamante tiene la competencia administrativa sobre correo.

    Se resuelve **contra la base**, con el mismo `check_permission` que usan los
    gates del servicio. La primera versión de esto leía una cabecera
    `X-User-Permissions`, y eso estaba roto de un modo que no se ve: **esa
    cabecera no la produce nadie**. El gateway no la emite —los tests de
    mcp-server lo dejan escrito, «cabecera que nadie producía», porque allí ya
    apareció el mismo defecto—, así que la función habría devuelto `False`
    siempre y ningún administrador habría visto ni una cuenta institucional. La
    pantalla saldría vacía, sin error, indistinguible de «no hay cuentas
    configuradas».

    No es una habilitación de seguridad por sí sola: gobierna qué cuentas
    INSTITUCIONALES se ven, nunca las personales de otro (ver el `WHERE` del
    repositorio).
    """
    return await check_permission(conn, actor, "USUA_PERM_ADMIN")


def _traducir(exc: ErrorDeCuenta) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"error": exc.codigo, "detail": exc.detalle, "status": exc.status},
    )


@apps_router.post(
    "", response_model=AppOAuthResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def registrar_app_oauth(
    request: Request,
    datos: AppOAuthCrear,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> Any:
    """Registra la aplicación que la ENTIDAD creó ante Google o Microsoft.

    No se pide en el alta de la entidad a propósito: cuando se crea una
    institución nadie sabe aún qué correo usará, quien la da de alta no suele
    tener a mano los permisos de administrador de Azure o Google Cloud, y muchas
    entidades trabajarán contra IMAP de hosting sin necesitar esto. Exigirlo
    entonces bloquearía el alta por un dato que puede tardar semanas.
    """
    slug = _tenant_slug(request)
    try:
        return await EmailCuentaService(conn, slug).crear_app(datos, actor=_actor(request))
    except ErrorDeCuenta as exc:
        raise _traducir(exc)


@apps_router.get(
    "", response_model=list[AppOAuthResponse],
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def listar_apps_oauth(
    request: Request, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> Any:
    return await EmailCuentaService(conn, _tenant_slug(request)).listar_apps()


@cuentas_router.post(
    "", response_model=CuentaResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def crear_cuenta(
    request: Request,
    datos: CuentaCrear,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> Any:
    slug = _tenant_slug(request)
    try:
        fila = await EmailCuentaService(conn, slug).crear_cuenta(datos, actor=_actor(request))
    except ErrorDeCuenta as exc:
        raise _traducir(exc)
    return dict(fila) | {"funcionarios": []}


@cuentas_router.get(
    "", response_model=list[CuentaResponse],
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def listar_cuentas(
    request: Request, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> Any:
    slug = _tenant_slug(request)
    actor = _actor(request)
    return await EmailCuentaService(conn, slug).listar(
        actor=actor, es_admin=await _es_admin(conn, actor)
    )


@cuentas_router.post(
    "/{cuenta_id}/probar", response_model=PruebaConexion,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def probar_cuenta(
    request: Request,
    cuenta_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    password: str | None = Body(default=None, embed=True),
) -> Any:
    """Comprueba la conexión y devuelve las carpetas del buzón. Sin efectos.

    Las carpetas son la mitad del valor: la de filtrado se elige de esta lista
    en vez de escribirse a mano. Una errata al teclearla se manifestaría como
    «la ingesta no trae nada», indistinguible de «no ha llegado correo».

    `password` solo para cuentas personales, cuya credencial no se guarda: viaja
    en esta petición y muere con ella.
    """
    slug = _tenant_slug(request)
    try:
        actor = _actor(request)
        ok, detalle, carpetas = await EmailCuentaService(conn, slug).probar(
            cuenta_id,
            actor=actor,
            es_admin=await _es_admin(conn, actor),
            password=password,
        )
    except ErrorDeCuenta as exc:
        raise _traducir(exc)
    return PruebaConexion(ok=ok, detalle=detalle, carpetas=carpetas)


@cuentas_router.post(
    "/{cuenta_id}/oauth/iniciar",
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def iniciar_autorizacion(
    request: Request,
    cuenta_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, str]:
    """Devuelve la URL del proveedor a la que mandar al administrador.

    El sistema **no** redirige por su cuenta ni abre la ventana: entrega la URL y
    la interfaz decide. Así el flujo se puede completar desde la sesión del
    navegador del administrador, que es quien tiene que autenticarse ante Google
    o Microsoft — el servidor no tiene forma de hacerlo por él, ni debe.
    """
    slug = _tenant_slug(request)
    actor = _actor(request)
    try:
        url = await EmailCuentaService(conn, slug).iniciar_autorizacion(
            cuenta_id, actor=actor, es_admin=await _es_admin(conn, actor)
        )
    except ErrorDeCuenta as exc:
        raise _traducir(exc)
    return {"authorization_url": url}


@cuentas_router.post(
    "/{cuenta_id}/oauth/canjear",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def canjear_autorizacion(
    request: Request,
    cuenta_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    code: str = Body(..., embed=True),
    state: str = Body(..., embed=True),
) -> None:
    """Cierra el flujo: canjea el código por el token de refresco y lo guarda.

    Lo llama la propia aplicación tras recibir la redirección del proveedor, con
    la sesión del administrador ya establecida. Se hace así, y no con un endpoint
    público que reciba la redirección directamente, para no abrir superficie sin
    autenticar en el gateway: la redirección aterriza en la interfaz, que ya sabe
    quién es el usuario.
    """
    slug = _tenant_slug(request)
    actor = _actor(request)
    try:
        await EmailCuentaService(conn, slug).canjear_autorizacion(
            cuenta_id, code=code, state=state, actor=actor,
            es_admin=await _es_admin(conn, actor),
        )
    except ErrorDeCuenta as exc:
        raise _traducir(exc)


# ═══════════════════════════════════════════════════════════════════════════
# Bandeja de correo (E19 Increment 2)
#
# Varias personas de correspondencia trabajan el mismo buzón. La reserva impide
# que dos radiquen el mismo correo; el gate de permiso es el de radicar, porque
# eso es lo que se acaba haciendo aquí.
# ═══════════════════════════════════════════════════════════════════════════

bandeja_router = APIRouter(prefix="/api/v1/email/cuentas/{cuenta_id}/bandeja", tags=["email-bandeja"])


@bandeja_router.post("/sincronizar", dependencies=[Depends(require_permission("PERM_RADI"))])
async def sincronizar_bandeja(
    request: Request,
    cuenta_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    password: str | None = Body(default=None, embed=True),
) -> dict[str, Any]:
    """Trae de la carpeta de origen los correos que aún no están en la bandeja.

    **No radica nada**: solo trae. Quién radica y con qué número lo decide un
    funcionario después, correo a correo.
    """
    slug = _tenant_slug(request)
    actor = _actor(request)
    try:
        return await EmailBandejaService(conn, slug).sincronizar(
            cuenta_id, actor=actor, es_admin=await _es_admin(conn, actor), password=password
        )
    except ErrorDeCuenta as exc:
        raise _traducir(exc)


@bandeja_router.get("", dependencies=[Depends(require_permission("PERM_RADI"))])
async def listar_bandeja(
    request: Request,
    response: Response,
    cuenta_id: uuid.UUID,
    estado: str = "pendiente",
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[dict[str, Any]]:
    """Los correos de la cuenta, el más antiguo por FECHA DE RECIBO primero.

    Una ventanilla atiende por orden de llegada, y la llegada es la del correo al
    buzón, no la del sondeo que lo trajo.
    """
    repo = EmailMensajeRepository(conn)
    filas = await repo.bandeja(cuenta_id, estado=estado)
    response.headers["X-Total-Count"] = str(await repo.contar(cuenta_id, estado=estado))
    return [dict(f) for f in filas]


@bandeja_router.get("/estado", dependencies=[Depends(require_permission("PERM_RADI"))])
async def estado_bandeja(
    cuenta_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, Any]:
    """Resumen barato para que las pantallas abiertas sepan si algo cambió.

    Devuelve cuántos correos hay pendientes, cuántos están ocupados por alguien y
    la marca de la última novedad. La pantalla compara esa marca con la que tenía
    y solo recarga la lista si cambió: sondear una fila cada pocos segundos es
    sostenible con diez operadores mirando la ventanilla; traerse la lista entera
    no lo es.
    """
    return await EmailMensajeRepository(conn).estado_bandeja(cuenta_id)


@bandeja_router.post("/{mensaje_id}/reservar", dependencies=[Depends(require_permission("PERM_RADI"))])
async def reservar_correo(
    request: Request,
    cuenta_id: uuid.UUID,
    mensaje_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, Any]:
    """Toma el correo para este funcionario mientras lo trabaja.

    `409 correo_ocupado` si otro lo tiene — que es la respuesta correcta y no un
    error del sistema: significa que la reserva está haciendo su trabajo.
    """
    slug = _tenant_slug(request)
    try:
        fila = await EmailBandejaService(conn, slug).reservar(
            mensaje_id, cuenta_id, actor=_actor(request)
        )
    except ErrorDeCuenta as exc:
        raise _traducir(exc)
    return dict(fila)


@bandeja_router.post("/{mensaje_id}/radicar", dependencies=[Depends(require_permission("PERM_RADI"))])
async def radicar_correo(
    request: Request,
    cuenta_id: uuid.UUID,
    mensaje_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    tenant_client: Any = Depends(get_tenant_client),
    redis: Any = Depends(get_redis),
    cuerpo: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Convierte el correo en un radicado de Entrada con sus soportes."""
    slug = _tenant_slug(request)
    actor = _actor(request)
    try:
        servicio = DocumentService(conn, slug, tenant_client, redis)
    except InvalidTenantError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_tenant_slug", "detail": str(exc), "status": 400},
        )
    try:
        return await EmailBandejaService(conn, slug).radicar(
            mensaje_id,
            cuenta_id,
            actor=actor,
            es_admin=await _es_admin(conn, actor),
            password=cuerpo.get("password"),
            datos=cuerpo,
            servicio=servicio,
        )
    except ErrorDeCuenta as exc:
        raise _traducir(exc)


@bandeja_router.post("/{mensaje_id}/descartar", dependencies=[Depends(require_permission("PERM_RADI"))])
async def descartar_correo(
    request: Request,
    cuenta_id: uuid.UUID,
    mensaje_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    motivo: str = Body(..., embed=True, min_length=5),
) -> dict[str, Any]:
    """Aparta un correo que llegó a la carpeta y no debía radicarse.

    Exige motivo: es una decisión sobre una comunicación oficial recibida, y la
    unidad de correspondencia responde de ella (Ac. 060/2001 art. 3).
    """
    slug = _tenant_slug(request)
    try:
        fila = await EmailBandejaService(conn, slug).descartar(
            mensaje_id, cuenta_id, actor=_actor(request), motivo=motivo
        )
    except ErrorDeCuenta as exc:
        raise _traducir(exc)
    return dict(fila)
