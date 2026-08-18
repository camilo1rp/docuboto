# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Índice electrónico del expediente (E15, Acuerdo AGN 001/2024).

Lectura del índice vigente / por versión / XML / historial, `rebuild` (admin) y
el reintento del sellado XAdES-B (`POST /indice/firmar`, E06 Inc.1, RT-16). La
verificación de fixity contra storage (T-14) ya contrasta la firma real.

Todas las rutas de LECTURA exigen `USUA_PERM_EXPEDIENTE` (`require_permission`,
que además fija el search_path del tenant) y aplican no-read-up (RF-SEG-08):
`IndexService` resuelve el clearance real del llamante y devuelve 404 si el
expediente supera su nivel de seguridad, ANTES de servir el índice — cierre de
un gap donde `/indice`, `/indice.xml` y `/indice/versions` no tenían gate
alguno, y `/indice/verify` sí exigía el permiso pero no contrastaba clearance.
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

from app.core.clearance import CLEARANCE_SIN_RESTRICCION, require_permission
from app.core.database import get_db_conn
from app.repositories.archive_repository import ArchiveRepository
from app.schemas.archive import (
    IndiceFirmarResult,
    IndicePendienteFirma,
    IndicePendienteRenovacion,
    IndiceResponse,
    IndiceVerifyResponse,
    IndiceVersionSummary,
    RebuildResult,
)
from app.services.index_service import IndexService

router = APIRouter(prefix="/api/v1/expedientes", tags=["indice"])


def _require_tenant(x_tenant_slug: str | None = Header(default=None)) -> str:
    if not x_tenant_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_tenant",
                "detail": "Falta la cabecera X-Tenant-Slug",
                "status": 400,
            },
        )
    return x_tenant_slug


def _optional_user(x_user_id: str | None = Header(default=None)) -> uuid.UUID | None:
    """X-User-Id como UUID, para resolver el clearance del llamante (RF-SEG-08).

    Gemelo de `expedientes.py::_optional_user`: usado en las lecturas del
    índice (indice, indice.xml, versions, verify) — a diferencia de
    `_actor_id` (str crudo, reenviado a signature-service), aquí se necesita
    el UUID para `ArchiveRepository.caller_clearance`.
    """
    if not x_user_id:
        return None
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_user_id",
                "detail": "La cabecera X-User-Id debe ser un UUID válido",
                "status": 400,
            },
        )


def _optional_canal(x_canal: str | None = Header(default=None)) -> str:
    """Canal de origen de la petición (X-Canal, inyectado server-side por el
    gateway — D-03, nunca confiado de un cliente directo) — decide si esta
    lectura del índice deja pista de acceso (E15 perfil v4, R3-a). Ausente
    (llamada sin pasar por el gateway, o tests que no lo declaran) => 'system':
    no se audita como consulta de un usuario real."""
    return x_canal or "system"


def _actor_id(x_user_id: str | None = Header(default=None)) -> str | None:
    """X-User-Id crudo (str) para reenviar tal cual a signature-service.

    Se valida el formato aquí (fail-fast, 400) pero se conserva como str: el
    cliente HTTP interno lo envía directamente como cabecera, sin re-serializar.
    """
    if not x_user_id:
        return None
    try:
        uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_user_id",
                "detail": "La cabecera X-User-Id debe ser un UUID válido",
                "status": 400,
            },
        )
    return x_user_id


@router.get(
    "/{expediente_id}/indice",
    response_model=IndiceResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_indice(
    expediente_id: uuid.UUID,
    version: int | None = Query(default=None, ge=0),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    canal: str = Depends(_optional_canal),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> IndiceResponse:
    return await IndexService(conn, tenant_slug).get_indice(
        expediente_id, version, user_id=user_id, canal=canal
    )


@router.get(
    "/{expediente_id}/indice.xml",
    response_class=PlainTextResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_indice_xml(
    expediente_id: uuid.UUID,
    version: int | None = Query(default=None, ge=0),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    canal: str = Depends(_optional_canal),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> Response:
    xml = await IndexService(conn, tenant_slug).get_xml(
        expediente_id, version, user_id=user_id, canal=canal
    )
    return Response(content=xml, media_type="application/xml")


@router.get(
    "/{expediente_id}/indice/versions",
    response_model=list[IndiceVersionSummary],
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def list_indice_versions(
    expediente_id: uuid.UUID,
    response: Response,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[IndiceVersionSummary]:
    items, total = await IndexService(conn, tenant_slug).list_versions(
        expediente_id, page=page, size=size, user_id=user_id
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get(
    "/{expediente_id}/indice/verify",
    response_model=IndiceVerifyResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def verify_indice(
    expediente_id: uuid.UUID,
    version: int | None = Query(default=None, ge=0),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> IndiceVerifyResponse:
    return await IndexService(conn, tenant_slug).verify(expediente_id, version, user_id=user_id)


@router.post(
    "/{expediente_id}/indice/firmar",
    response_model=IndiceFirmarResult,
    dependencies=[
        Depends(require_permission("USUA_PERM_EXPEDIENTE")),
        Depends(require_permission("PERM_FIRMA")),
    ],
)
async def firmar_indice(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    actor_id: str | None = Depends(_actor_id),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> IndiceFirmarResult:
    """Reintento del sellado XAdES-B del índice (E06 Inc.1, RT-16).

    Requiere USUA_PERM_EXPEDIENTE + PERM_FIRMA (``require_permission``,
    fail-closed): sin identidad resoluble (sin X-User-Id, o malformado) o sin
    ambos permisos, la petición se rechaza con 403 ANTES de tocar el índice —
    un llamador sin autorización nunca puede mutar el estado del índice
    (defensa en profundidad; antes solo el signature-service, aguas abajo,
    aplicaba PERM_FIRMA y un 403 de allí volteaba el índice a
    'pendiente_firma' igualmente).

    Idempotente: 409 si el índice vigente ya está firmado (inmutable). Sin
    X-User-Id (atribución del acto de firma, RF-FIR-11/12) la firma no se
    intenta y responde 503 (el índice queda/permanece 'pendiente_firma').
    """
    signed = await IndexService(conn, tenant_slug).firmar_indice_retry(
        expediente_id, tenant_slug=tenant_slug, actor_id=actor_id
    )
    return IndiceFirmarResult(
        firma_id=signed.firma_id,
        minio_key_firmado=signed.minio_key_firmado,
        nivel_conformidad=signed.nivel_conformidad,
        xades_level=signed.xades_level,
        acreditado=signed.acreditado,
    )


@router.post(
    "/{expediente_id}/indice/rebuild",
    response_model=RebuildResult,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def rebuild_indice(
    expediente_id: uuid.UUID,
    request: Request,
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> RebuildResult:
    """Regenera (admin) el índice electrónico — ver `IndexService.regenerate`.

    M2 (RF-SEG-08, resuelto): la mutación es LEGÍTIMA sin clearance de
    lectura (misma lógica que link/unlink) y SIEMPRE se ejecuta —
    `IndexService.regenerate` sigue resolviendo existencia/estado con
    `CLEARANCE_SIN_RESTRICCION` internamente, SIN CAMBIOS: ese método lo
    reutiliza `close_expediente` (canal 'system') para su propio asiento de
    auditoría, que necesita los valores REALES. La redacción de la RESPUESTA
    de ESTE endpoint vive aquí, en el handler, después de la regeneración:
    se acota a un ack minimalista si el llamante no puede LEER el expediente
    (clearance REAL, no `CLEARANCE_SIN_RESTRICCION` — esa constante solo
    sirve para la lectura barata del `nivel_seguridad`, no para la decisión
    de redacción).

    M2 hallazgo #1 (oráculo residual de ciclo de vida): `regenerate` lanza su
    propio 409 `expediente_not_open` (con el estado LITERAL en el cuerpo)
    INTERNAMENTE, vía su `assert_open` -- un no-lector que sondea un
    expediente CLASIFICADA closed/transferred por su UUID recibía ese 409
    crudo. Se intercepta en el `except`: si el llamante no puede leer, el 409
    se sustituye por el mismo ack acotado que la rama "no puede leer" del
    camino de éxito -- indistinguible entre sí, sea cual sea el estado del
    expediente. `IndexService.regenerate`/`assert_open` NO se tocan:
    `close_expediente` los reutiliza y debe seguir viendo el 409/los valores
    reales. Cualquier otra excepción (p.ej. 404 `expediente_not_found`,
    residual aceptado por diseño, coherente con M1) se propaga sin interceptar.

    `xsd_version` en el ack acotado es SIEMPRE `None` (auditoría de cierre de
    hallazgo #1, cobertura de testing-engineer): la rama `except` -- el único
    dato que tiene a mano es el 409 de `regenerate`, que aborta ANTES de
    calcular ninguna versión real -- no puede rellenarlo con un valor real sin
    una consulta adicional. Antes esta rama de éxito (open, no-lector)
    devolvía `rebuild_result.xsd_version` (valor real, p.ej. "v2"/"v4")
    mientras la rama `except` (closed/transferred, no-lector) devolvía `None`
    -- un no-lector podía distinguir open (xsd_version poblado) de
    closed/transferred (xsd_version `None`) SOLO por ese campo, pese a que
    `version`/`xml_sha256`/`num_documentos`/`unchanged` ya eran uniformes:
    el mismo oráculo residual de ciclo de vida que hallazgo #1 buscaba cerrar,
    reabierto por una vía distinta. Se unifica a `None` en ambas ramas.
    """
    generado_por = request.headers.get("X-Username") or "system"
    archive_repo = ArchiveRepository(conn, tenant_slug)

    async def _puede_leer() -> bool:
        # Clearance REAL del llamante vs `nivel_seguridad` del expediente. La
        # decisión de redacción SIEMPRE es el clearance real; el
        # `CLEARANCE_SIN_RESTRICCION` de `get_expediente_by_id` solo evita
        # perder la fila por su propio filtro no-read-up. `exp is None`
        # (inexistente) -> False; en la rama de éxito la fila siempre existe.
        clearance = await archive_repo.caller_clearance(user_id)
        exp = await archive_repo.get_expediente_by_id(
            expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
        )
        return exp is not None and exp.nivel_seguridad <= clearance

    # `puede_leer` se resuelve DESPUÉS de `regenerate` en la rama de éxito
    # (preserva el orden de llamadas que asumen los tests del camino normal) y
    # DENTRO del `except` para la rama #1 -- en ambos casos corre a lo sumo una
    # vez. `regenerate`/`assert_open` NO se tocan (los reutiliza
    # `close_expediente` con valores reales).
    try:
        rebuild_result = await IndexService(conn, tenant_slug).regenerate(
            expediente_id, generado_por=generado_por
        )
    except HTTPException as exc:
        # #1: un no-lector que sondea un CLASIFICADA closed/transferred por su
        # UUID recibía el 409 `expediente_not_open` con el estado LITERAL en el
        # cuerpo -> oráculo de ciclo de vida. Se sustituye por el mismo ack
        # acotado que la rama "no puede leer" de abajo (indistinguible del caso
        # open acotado). Se intercepta CUALQUIER 409 de `regenerate` (hoy
        # `expediente_not_open` y el `expediente_closed`/índice-firmado, ambos
        # revelan ciclo de vida o estado del índice de un CLASIFICADA),
        # desacoplado del string de error interno del servicio. El 404
        # `expediente_not_found` (residual aceptado, existencia por UUIDv4 no
        # adivinable, coherente con M1) y cualquier otra excepción se propagan
        # sin interceptar.
        if (
            exc.status_code == status.HTTP_409_CONFLICT
            and not await _puede_leer()
        ):
            return RebuildResult(
                version=None,
                xml_sha256=None,
                num_documentos=None,
                unchanged=False,
                xsd_version=None,
            )
        raise
    if await _puede_leer():
        return rebuild_result
    return RebuildResult(
        version=None,
        xml_sha256=None,
        num_documentos=None,
        unchanged=False,
        # `None`, no `rebuild_result.xsd_version` (ver hallazgo #1 en el
        # docstring): debe ser indistinguible del ack de la rama `except`
        # (expediente closed/transferred), que no dispone de un valor real.
        xsd_version=None,
    )


@router.get(
    "/indices/pendientes-firma",
    response_model=list[IndicePendienteFirma],
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def list_indices_pendientes_firma(
    response: Response,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[IndicePendienteFirma]:
    """Visibilidad operacional de índices pendientes de sellado (residual Ac.
    AGN 001/2024 art. 4.3.2.4) — para que ningún índice sin firmar quede
    silenciado en `audit_log`: complementa al job de reconciliación
    (`app/jobs/indice_reconciliation.py`) y al reintento manual
    (`POST /{expediente_id}/indice/firmar`) con visibilidad directa,
    incluidos los `reconcile_agotado=TRUE` que requieren intervención humana.

    No-read-up (RF-SEG-08): filtra por el clearance real del llamante, igual
    que `GET /expedientes` (`IndexService.list_pendientes_firma`).

    Bajo `/indices/...` (no `/{expediente_id}/...`): no colisiona con ninguna
    ruta de este router — todas exigen un segundo segmento literal
    (`indice`, `indice.xml`, ...) distinto de `pendientes-firma`.
    """
    items, total = await IndexService(conn, tenant_slug).list_pendientes_firma(
        page=page, size=size, user_id=user_id
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get(
    "/indices/pendientes-renovacion",
    response_model=list[IndicePendienteRenovacion],
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def list_indices_pendientes_renovacion(
    response: Response,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[IndicePendienteRenovacion]:
    """Visibilidad operacional de artefactos WORM de series de disposición
    Conservación Total (CT) cuya renovación de retención está AGOTADA
    (`worm_renovacion_agotado=TRUE`) o EN RIESGO de vencer sin renovar (E10
    debt, AGN 001/2024 Art. 4.3.2.6) -- gemela de
    `GET /indices/pendientes-firma` para el eje de RENOVACIÓN: complementa al
    job de reconciliación (`run_once_worm_renovacion`,
    `app/jobs/indice_reconciliation.py`) con visibilidad directa, incluidos
    los agotados que requieren intervención manual URGENTE (un lock
    COMPLIANCE de conservación PERMANENTE a punto de expirar sin extender).

    No-read-up (RF-SEG-08): filtra por el clearance real del llamante, igual
    que `list_indices_pendientes_firma` (`IndexService.list_pendientes_renovacion`).

    Cubre AMBOS artefactos protegidos bajo WORM -- índice electrónico y acta
    de transferencia -- distinguidos por `tipo` en cada fila, en vez de dos
    endpoints gemelos (mismo criterio de no proliferación que
    `POST /proteger-artefacto` en storage-service).

    Bajo `/indices/...` (no `/{expediente_id}/...`): mismo criterio de
    `list_indices_pendientes_firma` -- no colisiona con ninguna ruta de este
    router.
    """
    items, total = await IndexService(conn, tenant_slug).list_pendientes_renovacion(
        page=page, size=size, user_id=user_id
    )
    response.headers["X-Total-Count"] = str(total)
    return items
