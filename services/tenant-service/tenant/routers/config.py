# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Endpoints de configuración por tenant (E14): festivos, días hábiles, parámetros.

Las escrituras exigen `USUA_PERM_ADMIN` (`tenant/core/authz.py`). Los GET de festivos
y `business-days/calculate` quedan solo autenticados: el cálculo de plazos legales
de respuesta los consume para cualquier usuario radicador, no solo administradores.
"""

import logging
import re
import uuid
from datetime import date, timedelta
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError

from tenant.clients import pasarela
from tenant.core import audit as audit_core
from tenant.core import declaracion
from tenant.core.authz import require_permission
from tenant.core.calendar import add_business_days, colombian_holidays
from tenant.core.database import get_tenant_conn
from tenant.repositories.config_repository import ConfigRepository
from tenant.schemas.config import (
    AsistenteResponse,
    AsistenteUpdate,
    BusinessDaysResult,
    HolidayCreate,
    HolidayResponse,
    HolidaySeedRequest,
    IntelligenceCapabilities,
    IntelligenceInventory,
    IntelligenceLevelUpdate,
    IntelligenceResponse,
    IntelligenceUpdate,
    ParamResponse,
    ParamUpdate,
    WithdrawResponse,
)

router = APIRouter(prefix="/api/v1", tags=["config"])
logger = logging.getLogger(__name__)


# --- festivos ---------------------------------------------------------------
@router.get("/config/holidays", response_model=list[HolidayResponse])
async def list_holidays(
    year: int | None = Query(None, ge=2000, le=2100),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[HolidayResponse]:
    repo = ConfigRepository(conn)
    return [HolidayResponse(**dict(r)) for r in await repo.list_holidays(year)]


@router.post(
    "/config/holidays",
    response_model=HolidayResponse,
    status_code=status.HTTP_201_CREATED,
    # Nivel 3: un administrador en modo LECTURA (nivel 1) no escribe el calendario
    # que fija los plazos legales de toda la entidad.
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def create_holiday(
    payload: HolidayCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> HolidayResponse:
    repo = ConfigRepository(conn)
    try:
        async with conn.transaction():
            row = await repo.insert_holiday(
                fecha=payload.fecha,
                descripcion=payload.descripcion,
                tipo=payload.tipo,
                is_national=payload.is_national,
            )
            await audit_core.append(
                conn,
                tenant_slug=request.headers.get("X-Tenant-Slug"),
                action="config.festivo_creado",
                object_type="festivo",
                object_ref=str(payload.fecha),
                actor=audit_core.actor_from(request),
                payload={"descripcion": payload.descripcion, "tipo": payload.tipo},
                canal=audit_core.CANAL_PETICION,
            )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "conflict",
                "detail": f"Ya existe un día no hábil registrado para el {payload.fecha}",
                "status": 409,
            },
        )
    return HolidayResponse(**dict(row))


@router.delete(
    "/config/holidays/{holiday_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    # Nivel 3: un administrador en modo LECTURA (nivel 1) no escribe el calendario
    # que fija los plazos legales de toda la entidad.
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def delete_holiday(
    holiday_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    repo = ConfigRepository(conn)
    async with conn.transaction():
        if not await repo.delete_holiday(holiday_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "not_found", "detail": "No existe el día no hábil", "status": 404},
            )
        await audit_core.append(
            conn,
            tenant_slug=request.headers.get("X-Tenant-Slug"),
            action="config.festivo_eliminado",
            object_type="festivo",
            object_ref=str(holiday_id),
            actor=audit_core.actor_from(request),
            canal=audit_core.CANAL_PETICION,
        )


@router.post(
    "/config/holidays/seed",
    status_code=status.HTTP_202_ACCEPTED,
    # Nivel 3: un administrador en modo LECTURA (nivel 1) no escribe el calendario
    # que fija los plazos legales de toda la entidad.
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def seed_holidays(
    payload: HolidaySeedRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, int]:
    if payload.year_to < payload.year_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_range", "detail": "year_to < year_from", "status": 400},
        )
    rows: list[tuple[date, str, str, bool]] = []
    for year in range(payload.year_from, payload.year_to + 1):
        for fecha, descripcion in colombian_holidays(year).items():
            rows.append((fecha, descripcion, "festivo", True))
    repo = ConfigRepository(conn)
    async with conn.transaction():
        created = await repo.bulk_insert_holidays(rows)
        await audit_core.append(
            conn,
            tenant_slug=request.headers.get("X-Tenant-Slug"),
            action="config.festivos_sembrados",
            object_type="festivo",
            object_ref=f"{payload.year_from}-{payload.year_to}",
            actor=audit_core.actor_from(request),
            payload={"created": created},
            canal=audit_core.CANAL_PETICION,
        )
    return {"created": created}


# --- días hábiles -----------------------------------------------------------
@router.get("/business-days/calculate", response_model=BusinessDaysResult)
async def calculate_business_days(
    from_date: date = Query(..., alias="from"),
    days: int = Query(..., ge=0, le=3650),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> BusinessDaysResult:
    repo = ConfigRepository(conn)
    # Ventana amplia para cubrir fines de semana y festivos del intervalo.
    window_end = from_date + timedelta(days=days * 2 + 60)
    non_business = await repo.non_business_dates(from_date, window_end)
    due = add_business_days(from_date, days, non_business)
    return BusinessDaysResult(from_date=from_date, business_days=days, due_date=due)


# --- parámetros -------------------------------------------------------------
#: Parámetros cuyo valor es un vocabulario CERRADO que lee otro servicio. Un
#: valor fuera del vocabulario se rechaza al escribir (422): quien lo lee
#: (signature-service) aplica ante un valor desconocido la política más estricta,
#: así que dejar pasar un «clasificado» mal tecleado bloquearía la firma de la
#: entidad entera en vez de avisar a quien lo escribió.
#: `firma_personal_require_xades`: política XAdES de la entidad (ADR-016, AREA-04-12).
#: El orden de la tupla ES el orden de rigor (de menor a mayor): `relaja_politica`
#: lo usa para saber si un cambio baja la exigencia.
PARAMS_VOCABULARIO_CERRADO: dict[str, tuple[str, ...]] = {
    "firma_personal_require_xades": ("off", "clasificados", "todos"),
}

#: Claves que `PUT /config/params/{key}` admite: las que SIEMBRAN las migraciones de
#: tenant (009 membrete «Entidad», 010 política XAdES, 012 política de tratamiento
#: de datos) y que el código de algún servicio LEE. Cerrado a propósito: la lectura
#: (`GET /config/params`) es abierta a todo el tenant, así que el upsert de una clave
#: cualquiera convertiría la tabla en un buzón donde alguien guardaría un secreto que
#: cualquier usuario lee. Añadir un parámetro = sembrarlo en una migración Y listarlo
#: aquí; el guardián `shared/tests/test_params_admitidos_son_los_sembrados.py` falla
#: si divergen (sembradas = admitidas ∪ `PARAMS_INERTES`).
PARAMS_ADMITIDOS: frozenset[str] = frozenset({
    # 009_params_entidad (membrete)
    "entidad_nombre",
    "entidad_sigla",
    "entidad_nit",
    "entidad_direccion",
    "entidad_telefono",
    "entidad_email",
    "entidad_web",
    # 010_params_firma
    "firma_personal_require_xades",
    # 012_politica_tratamiento
    "politica_tratamiento_version",
    "politica_tratamiento_url",
    "politica_tratamiento_texto",
})

#: Claves que la migración 002 SIEMBRA (herencia de `sgd_param_admin` de Orfeo) pero
#: que NINGÚN servicio lee: configuración inerte. Se SIEMBRAN, pero el PUT las rechaza
#: (422): admitir su edición prometería un efecto que no existe (p. ej. cambiar
#: `formato_numero_radicado` no cambia el número, que sale de
#: `{YEAR}-{TENANT_CODE}-{TYPE}-{SEQ}`; y si algún día se cablea, cambiarlo a mitad de
#: año rompería la numeración continua del Ac. AGN 060/2001). Para volver a
#: configurarlas: que un servicio las lea Y moverlas a `PARAMS_ADMITIDOS`. El guardián
#: exige que toda sembrada esté en una de las dos listas y que ninguna esté en ambas.
PARAMS_INERTES: frozenset[str] = frozenset({
    "unicoconsecutivo",
    "formato_numero_radicado",
    "depe_digits",
    "consec_digits",
    "dias_bloqueo",
    "expediente_number_format",
    "notificaciones_mail",
    "archivado_requiere_exp",
    "reasigna_requiere_exp",
    "firma_digital_provider",
})

#: Mínimo de caracteres (sin espacios laterales) del `motivo` de un cambio que
#: RELAJA una política. Una sola palabra no es una justificación auditable.
MOTIVO_MIN_CARACTERES = 10


def relaja_politica(key: str, antes: object, despues: object) -> bool:
    """¿El cambio baja la exigencia de un parámetro de vocabulario ordenado?

    Si el «antes» no es un valor del vocabulario (sin fila, JSON null = «manda el
    entorno», o texto corrupto) NO se sabe qué política regía de verdad: se
    supone la más estricta, de modo que fijar cualquier valor menor exige
    motivo. Es el lado seguro: pedir una justificación de más cuesta una frase;
    no pedirla deja relajar sin rastro.
    """
    orden = PARAMS_VOCABULARIO_CERRADO.get(key)
    if orden is None or despues not in orden:
        return False
    rigor_antes = orden.index(antes) if isinstance(antes, str) and antes in orden else len(orden) - 1
    return orden.index(despues) < rigor_antes


@router.get("/config/params")
async def get_params(conn: asyncpg.Connection = Depends(get_tenant_conn)) -> dict[str, object]:
    repo = ConfigRepository(conn)
    return await repo.get_params()


@router.get("/config/params/{key}", response_model=ParamResponse)
async def get_param(key: str, conn: asyncpg.Connection = Depends(get_tenant_conn)) -> ParamResponse:
    repo = ConfigRepository(conn)
    value = await repo.get_param(key)
    if value is None and key not in await repo.get_params():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": f"No existe el parámetro «{key}»", "status": 404},
        )
    return ParamResponse(key=key, value=value)


@router.put(
    "/config/params/{key}",
    response_model=ParamResponse,
    # Nivel 3: un administrador en modo LECTURA (nivel 1) no cambia parámetros;
    # el de la política de firma relajaba la exigencia de la entidad entera.
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def set_param(
    key: str,
    payload: ParamUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> ParamResponse:
    if key not in PARAMS_ADMITIDOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "config_param_desconocido",
                "detail": (
                    f"«{key}» no es un parámetro configurable. Admitidos: "
                    f"{', '.join(sorted(PARAMS_ADMITIDOS))}"
                ),
                "status": 422,
            },
        )
    if payload.value is None:
        # JSON null solo lo escribe la siembra (010/011: «la entidad no lo define»,
        # manda el entorno). Por la API borraría en silencio la decisión de la
        # entidad y dejaría que el respaldo del despliegue decida por ella.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "valor_nulo",
                "detail": f"«{key}» no admite null: indique un valor",
                "status": 422,
            },
        )
    if key == "politica_tratamiento_url" and payload.value != "":
        # Se enlaza en la página pública de la ventanilla: solo http(s) (un
        # `javascript:`/`data:` sería XSS almacenado). Vacío = «sin URL».
        if not isinstance(payload.value, str) or not re.fullmatch(
            r"https?://[^\s<>\"']+", payload.value, re.IGNORECASE
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "url_invalida",
                    "detail": "«politica_tratamiento_url» debe ser una URL http:// o https://",
                    "status": 422,
                },
            )
    admitidos = PARAMS_VOCABULARIO_CERRADO.get(key)
    if admitidos is not None and payload.value not in admitidos:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "valor_invalido",
                "detail": f"«{key}» solo admite: {', '.join(admitidos)}",
                "status": 422,
            },
        )
    repo = ConfigRepository(conn)
    actor = audit_core.actor_from(request)
    motivo = (payload.motivo or "").strip()
    relaja: list[bool] = [False]

    def _guard(antes: object) -> None:
        # Se evalúa con el «antes» ya bloqueado (ver `upsert_param`).
        relaja[0] = relaja_politica(key, antes, payload.value)
        if relaja[0] and len(motivo) < MOTIVO_MIN_CARACTERES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "motivo_requerido",
                    "detail": (
                        f"Relajar «{key}» exige un motivo de al menos "
                        f"{MOTIVO_MIN_CARACTERES} caracteres"
                    ),
                    "status": 422,
                },
            )

    async with conn.transaction():
        # `upsert_param` ya escribe `admin_audit` (columna `updated_by`, por-tenant,
        # no inmutable); este `append` añade la traza Canal A (`public.audit_log`,
        # cadena de hash) en la MISMA transacción.
        existia, antes = await repo.upsert_param(key, payload.value, actor, guard=_guard)
        await audit_core.append(
            conn,
            tenant_slug=request.headers.get("X-Tenant-Slug"),
            action="config.parametro_actualizado",
            object_type="parametro",
            object_ref=key,
            actor=actor,
            # `antes` + `despues`: sin el valor previo, el asiento solo dice cómo
            # quedó el parámetro, no qué estaba vigente hasta ese momento.
            payload={
                "value": payload.value,
                "antes": antes,
                "despues": payload.value,
                "existia": existia,
                # Quién relaja una política deja dicho por qué; el asiento lo
                # marca para que sea consultable sin comparar antes/despues.
                "relaja_politica": relaja[0],
                "motivo": motivo or None,
            },
            canal=audit_core.CANAL_PETICION,
        )
    return ParamResponse(key=key, value=payload.value)


@router.get("/config/asistente", response_model=AsistenteResponse)
async def get_asistente(
    request: Request, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> AsistenteResponse:
    """¿Tiene la entidad activado el conocimiento (buscar, antecedentes)? (ADR-037)"""
    slug = request.headers.get("X-Tenant-Slug", "")
    habilitado = await ConfigRepository(conn).get_asistente(slug)
    return AsistenteResponse(habilitado=habilitado)


@router.put(
    "/config/asistente",
    response_model=AsistenteResponse,
    # Nivel 3, como el resto de parámetros de la entidad: encenderlo es una decisión
    # de tratamiento de datos de la entidad (Ley 1581 art. 4 lit. b y art. 17), no de
    # cualquier administrador en modo lectura.
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def set_asistente(
    payload: AsistenteUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> AsistenteResponse:
    """Enciende o apaga el conocimiento de LA entidad del request (ADR-037).

    El slug sale de la cabecera ya validada por ``get_tenant_conn``, nunca del cuerpo:
    una entidad no puede tocar la bandera de otra. Lectura con bloqueo, escritura y
    asiento Canal A en UNA transacción; pedir el valor vigente no es un acto (200 sin
    asiento).

    **Encender** es una transmisión de datos a un encargado (DUR 1074/2015 [confirmar
    numeral]; no una transferencia del art. 26): exige ``declaracion_transmision: true``
    (``422 declaracion_requerida``) y que la entidad tenga publicada su política de
    tratamiento (``409 politica_no_publicada``, el mismo código de la ventanilla). El
    asiento deja la versión de la política y la declaración. **Apagar** no
    pide nada.
    """
    slug = request.headers.get("X-Tenant-Slug", "")
    repo = ConfigRepository(conn)
    motivo = (payload.motivo or "").strip() or None
    async with conn.transaction():
        antes = await repo.lock_asistente(slug)
        if antes is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "tenant_not_found", "detail": "La entidad no existe", "status": 404},
            )
        if antes == payload.habilitado:
            return AsistenteResponse(habilitado=antes, cambiado=False)
        version: str | None = None
        if payload.habilitado:
            if not payload.declaracion_transmision:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "declaracion_requerida",
                        "detail": (
                            "Encender el conocimiento exige declarar que la política de "
                            "tratamiento cubre la transmisión: envíe declaracion_transmision: true"
                        ),
                        "status": 422,
                    },
                )
            valor = await repo.get_param("politica_tratamiento_version")
            version = valor.strip() if isinstance(valor, str) and valor.strip() else None
            if version is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "politica_no_publicada",
                        "detail": (
                            "La entidad no ha publicado su política de tratamiento de datos "
                            "(Ley 1581, art. 17); publíquela antes de encender el conocimiento"
                        ),
                        "status": 409,
                    },
                )
        await repo.set_asistente(slug, payload.habilitado)
        asiento: dict[str, object] = {"antes": antes, "despues": payload.habilitado, "motivo": motivo}
        if payload.habilitado:
            asiento |= {
                "politica_tratamiento_version": version,
                "declaracion_transmision": True,
            }
        await audit_core.append(
            conn,
            tenant_slug=slug,
            action="tenant.asistente_cambiado",
            object_type="tenant",
            object_ref=slug,
            actor=audit_core.actor_from(request),
            payload=asiento,
            canal=audit_core.CANAL_PETICION,
        )
    return AsistenteResponse(habilitado=payload.habilitado, cambiado=True)


def _falla(code: int, error: str, detail: str, **extra: object) -> HTTPException:
    cuerpo: dict[str, object] = {"error": error, "detail": detail, "status": code, **extra}
    return HTTPException(status_code=code, detail=cuerpo)


def _niveles(raw: object) -> list[int]:
    if not isinstance(raw, (list, tuple)):
        return []
    salida: list[int] = []
    for valor in raw:
        try:
            numero = int(valor)
        except (TypeError, ValueError):
            continue
        if numero in (1, 2, 3) and numero not in salida:
            salida.append(numero)
    return sorted(salida)


def _texto(valor: str | None) -> str:
    return (valor or "").strip()


def _respuesta(
    fila: Any,
    suspended: bool,
    *,
    changed: bool = False,
    purge: Literal["accepted", "pending", "not_applicable"] = "not_applicable",
) -> IntelligenceResponse:
    provider = fila["intelligence_provider"]
    if provider not in ("local", "ppi"):
        provider = "local"
    return IntelligenceResponse(
        provider=provider,
        levels=_niveles(fila["intelligence_levels"]),
        send_metadata=bool(fila["intelligence_send_metadata"]),
        suspended=suspended,
        changed=changed,
        purge=purge,
    )


async def _vista(slug: str) -> dict[str, Any]:
    try:
        return await pasarela.capacidades(slug)
    except pasarela.PasarelaError as exc:
        if exc.status == 409:
            raise _falla(
                409,
                "capacidades_invalidas",
                "La declaración del proveedor no se pudo comprobar",
            ) from None
        raise _falla(
            503,
            "capacidades_no_disponibles",
            "No se pudo leer la declaración del proveedor",
        ) from None


async def _politica(repo: ConfigRepository) -> str:
    valor = await repo.get_param("politica_tratamiento_version")
    version = valor.strip() if isinstance(valor, str) and valor.strip() else None
    if version is None:
        raise _falla(
            409,
            "politica_no_publicada",
            "La entidad no ha publicado su política de tratamiento de datos "
            "(Ley 1581, art. 17); publíquela antes de transmitir",
        )
    return version


def _nombres(texto: str, exigidos: list[str]) -> None:
    if not texto:
        raise _falla(
            422,
            "declaracion_requerida",
            "Falta la declaración del administrador",
        )
    faltantes = declaracion.faltan(texto, exigidos)
    if faltantes:
        raise _falla(
            422,
            "declaracion_incompleta",
            "La declaración no nombra lo que declara el proveedor",
            faltan=faltantes,
        )


def _ruta(vista: dict[str, Any], nivel: int, etiqueta: str) -> dict[str, Any]:
    fila = declaracion.fila_de(vista, nivel)
    if fila is None:
        raise _falla(
            409,
            "nivel_no_declarado",
            f"El proveedor no declara la ruta de {etiqueta}",
        )
    if not declaracion.ruta_admitida(fila):
        raise _falla(
            409,
            "ruta_no_admitida",
            f"La ruta de {etiqueta} conserva o entrena con los datos",
        )
    return fila


async def _registrar(slug: str, nivel: int, sha: object) -> None:
    """Anota la huella ya confirmada. Si falla, el cambio de columna sigue."""
    if not isinstance(sha, str) or len(sha) != 64:
        raise _falla(
            503,
            "capacidades_no_disponibles",
            "No se pudo leer la declaración del proveedor",
        )
    try:
        await pasarela.aceptar(slug, nivel, sha)
    except pasarela.PasarelaError as exc:
        if exc.error == "declaracion_distinta":
            raise _falla(
                409,
                "declaracion_distinta",
                "La declaración del proveedor cambió. Léala de nuevo y declare otra vez",
            ) from None
        logger.warning("no se pudo registrar la declaración aceptada")
        raise _falla(
            503,
            "declaracion_no_registrada",
            "El cambio quedó guardado, pero la pasarela no registró la declaración. "
            "Vuelva a enviarla",
        ) from None


async def _purgar(
    slug: str, actor: uuid.UUID | None, *, reason: str, levels: list[int] | None
) -> bool:
    if actor is None:
        return False
    try:
        cuerpo = await pasarela.purgar(slug, actor=actor, reason=reason, levels=levels)
    except pasarela.PasarelaError:
        logger.warning("el borrado en el proveedor no quedó pedido")
        return False
    return bool(cuerpo.get("accepted")) and isinstance(cuerpo.get("purge_ref"), str)


async def _asiento(
    conn: asyncpg.Connection,
    *,
    slug: str,
    action: str,
    actor: uuid.UUID | None,
    payload: dict[str, object],
) -> None:
    await audit_core.append(
        conn,
        tenant_slug=slug,
        action=action,
        object_type="tenant",
        object_ref=slug,
        actor=actor,
        canal=audit_core.CANAL_PETICION,
        payload=payload,
    )


@router.get(
    "/config/intelligence",
    response_model=IntelligenceResponse,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def get_intelligence(
    request: Request, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> IntelligenceResponse:
    """Proveedor, niveles y metadatos de la entidad (ADR-045 §4)."""
    slug = request.headers.get("X-Tenant-Slug", "")
    repo = ConfigRepository(conn)
    fila = await repo.get_intelligence(slug)
    if fila is None:
        raise _falla(404, "tenant_not_found", "La entidad no existe")
    return _respuesta(fila, await repo.envio_suspendido())


@router.get(
    "/config/intelligence/capabilities",
    response_model=IntelligenceCapabilities,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def get_intelligence_capabilities(request: Request) -> IntelligenceCapabilities:
    """Proveedor, subencargados y ruta de cada nivel, ya comprobados.

    No cambia nada y no transmite registros. Sin esta lectura el administrador
    no puede redactar la declaración.
    """
    vista = await _vista(request.headers.get("X-Tenant-Slug", ""))
    try:
        return IntelligenceCapabilities.model_validate(
            {
                "provider": vista["provider"],
                "subprocessors": vista["subprocessors"],
                "routing": vista["routing"],
            }
        )
    except (KeyError, TypeError, ValidationError):
        raise _falla(
            503,
            "capacidades_no_disponibles",
            "No se pudo leer la declaración del proveedor",
        ) from None


@router.get(
    "/config/intelligence/inventory",
    response_model=IntelligenceInventory,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def get_intelligence_inventory(request: Request) -> IntelligenceInventory:
    """Cuántos radicados conserva el proveedor. También con la entidad en local."""
    slug = request.headers.get("X-Tenant-Slug", "")
    try:
        cuerpo = await pasarela.inventario(slug)
    except pasarela.PasarelaError:
        raise _falla(
            503,
            "inventario_no_disponible",
            "No se pudo leer cuántos radicados conserva el proveedor",
        ) from None
    try:
        return IntelligenceInventory.model_validate(cuerpo)
    except ValidationError:
        raise _falla(
            503,
            "inventario_no_disponible",
            "No se pudo leer cuántos radicados conserva el proveedor",
        ) from None


@router.put(
    "/config/intelligence",
    response_model=IntelligenceResponse,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def set_intelligence(
    payload: IntelligenceUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> IntelligenceResponse:
    """Elige ``local`` o ``ppi``, y enciende o apaga los metadatos de plantilla.

    Pasar a ``ppi`` exige la bandera, la política publicada y una declaración
    que nombra al proveedor y a sus subencargados. Habilita solo PÚBLICA.
    Volver a ``local`` no pide nada y no borra lo ya transmitido.
    """
    slug = request.headers.get("X-Tenant-Slug", "")
    repo = ConfigRepository(conn)
    actor = audit_core.actor_from(request)
    texto = _texto(payload.transmission_statement)
    registrar: tuple[int, str] | None = None
    async with conn.transaction():
        fila = await repo.lock_intelligence(slug)
        if fila is None:
            raise _falla(404, "tenant_not_found", "La entidad no existe")
        antes = str(fila["intelligence_provider"])
        quiere = payload.provider if payload.provider is not None else antes
        meta_antes = bool(fila["intelligence_send_metadata"])
        meta = payload.send_metadata if payload.send_metadata is not None else meta_antes
        niveles = _niveles(fila["intelligence_levels"])
        cambio_proveedor = quiere != antes
        cambio_meta = meta != meta_antes
        if quiere != "ppi":
            if texto and not cambio_proveedor:
                raise _falla(
                    422,
                    "declaracion_no_aplica",
                    "La declaración solo aplica con el proveedor ppi",
                )
            texto = ""
        redeclara = quiere == "ppi" and antes == "ppi" and bool(texto)
        if not cambio_proveedor and not cambio_meta and not redeclara:
            return _respuesta(fila, await repo.envio_suspendido())
        version: str | None = None
        sha: str | None = None
        if quiere == "ppi" and (cambio_proveedor or redeclara):
            if not fila["asistente_habilitado"]:
                raise _falla(
                    409,
                    "bandera_apagada",
                    "Encienda el conocimiento de la entidad antes de usar ppi",
                )
            if not texto:
                raise _falla(
                    422,
                    "declaracion_requerida",
                    "Falta la declaración del administrador",
                )
            version = await _politica(repo)
            vista = await _vista(slug)
            _nombres(texto, declaracion.exigidos_proveedor(vista))
            _ruta(vista, 1, "PÚBLICA")
            sha = vista.get("sha256") if isinstance(vista.get("sha256"), str) else None
            if not isinstance(sha, str) or len(sha) != 64:
                raise _falla(
                    503,
                    "capacidades_no_disponibles",
                    "No se pudo leer la declaración del proveedor",
                )
            if cambio_proveedor:
                niveles = [1]
        if cambio_proveedor or cambio_meta or niveles != _niveles(fila["intelligence_levels"]):
            await repo.set_intelligence(
                slug, provider=quiere, levels=niveles, send_metadata=meta
            )
        if cambio_proveedor or redeclara:
            await _asiento(
                conn,
                slug=slug,
                action="tenant.intelligence_changed",
                actor=actor,
                payload={
                    "antes": antes,
                    "despues": quiere,
                    "transmission_statement": texto or None,
                    "capabilities_sha256": sha,
                    "politica_tratamiento_version": version,
                    "levels_antes": _niveles(fila["intelligence_levels"]),
                    "levels_despues": niveles,
                },
            )
        if cambio_meta:
            await _asiento(
                conn,
                slug=slug,
                action="tenant.intelligence_metadata_changed",
                actor=actor,
                payload={"antes": meta_antes, "despues": meta},
            )
        if sha is not None:
            registrar = (1, sha)
        fila = {
            "intelligence_provider": quiere,
            "intelligence_levels": niveles,
            "intelligence_send_metadata": meta,
        }
    if registrar is not None:
        await _registrar(slug, registrar[0], registrar[1])
    return _respuesta(fila, await repo.envio_suspendido(), changed=True)


@router.put(
    "/config/intelligence/levels",
    response_model=IntelligenceResponse,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def set_intelligence_level(
    payload: IntelligenceLevelUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> IntelligenceResponse:
    """Habilita o deshabilita RESERVADA o CLASIFICADA, un nivel por vez.

    Habilitar nombra la ruta, los modelos y los subencargados de ese nivel.
    Deshabilitar es inmediato y ordena el borrado de lo ya transmitido.
    """
    slug = request.headers.get("X-Tenant-Slug", "")
    repo = ConfigRepository(conn)
    actor = audit_core.actor_from(request)
    texto = _texto(payload.statement)
    etiqueta = "RESERVADA" if payload.level == 2 else "CLASIFICADA"
    registrar: tuple[int, str] | None = None
    purgar = False
    async with conn.transaction():
        fila = await repo.lock_intelligence(slug)
        if fila is None:
            raise _falla(404, "tenant_not_found", "La entidad no existe")
        niveles = _niveles(fila["intelligence_levels"])
        ya = payload.level in niveles
        if payload.enabled == ya and not (payload.enabled and texto):
            return _respuesta(fila, await repo.envio_suspendido())
        version: str | None = None
        sha: str | None = None
        if payload.enabled:
            if fila["intelligence_provider"] != "ppi":
                raise _falla(
                    409,
                    "proveedor_local",
                    "Habilitar un nivel exige que la entidad use ppi",
                )
            if not fila["asistente_habilitado"]:
                raise _falla(
                    409,
                    "bandera_apagada",
                    "Encienda el conocimiento de la entidad antes de habilitar el nivel",
                )
            version = await _politica(repo)
            vista = await _vista(slug)
            exigidos = declaracion.exigidos_nivel(vista, payload.level)
            if exigidos is None:
                raise _falla(
                    409,
                    "nivel_no_declarado",
                    f"El proveedor no declara la ruta de {etiqueta}",
                )
            _ruta(vista, payload.level, etiqueta)
            _nombres(texto, exigidos)
            sha = vista.get("sha256") if isinstance(vista.get("sha256"), str) else None
            if not isinstance(sha, str) or len(sha) != 64:
                raise _falla(
                    503,
                    "capacidades_no_disponibles",
                    "No se pudo leer la declaración del proveedor",
                )
            nuevos = sorted(set(niveles) | {payload.level})
        else:
            nuevos = [nivel for nivel in niveles if nivel != payload.level]
            texto = ""
            purgar = True
        if nuevos != niveles:
            await repo.set_intelligence(
                slug,
                provider=str(fila["intelligence_provider"]),
                levels=nuevos,
                send_metadata=bool(fila["intelligence_send_metadata"]),
            )
        await _asiento(
            conn,
            slug=slug,
            action="tenant.intelligence_level_enabled",
            actor=actor,
            payload={
                "level": payload.level,
                "antes": ya,
                "despues": payload.enabled,
                "transmission_statement": texto or None,
                "capabilities_sha256": sha,
                "politica_tratamiento_version": version,
            },
        )
        if sha is not None:
            registrar = (payload.level, sha)
        fila = {
            "intelligence_provider": fila["intelligence_provider"],
            "intelligence_levels": nuevos,
            "intelligence_send_metadata": fila["intelligence_send_metadata"],
        }
    if registrar is not None:
        await _registrar(slug, registrar[0], registrar[1])
    purge: Literal["accepted", "pending", "not_applicable"] = "not_applicable"
    if purgar:
        ok = await _purgar(slug, actor, reason="host_request", levels=[payload.level])
        purge = "accepted" if ok else "pending"
    return _respuesta(fila, await repo.envio_suspendido(), changed=True, purge=purge)


@router.post(
    "/config/intelligence/withdraw",
    response_model=WithdrawResponse,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=3))],
)
async def withdraw_intelligence(
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> WithdrawResponse:
    """Pide al proveedor el borrado de todo lo de la entidad y deja asiento.

    No cambia el proveedor. El asiento se escribe solo si el proveedor acepta.
    """
    slug = request.headers.get("X-Tenant-Slug", "")
    repo = ConfigRepository(conn)
    actor = audit_core.actor_from(request)
    async with conn.transaction():
        fila = await repo.lock_intelligence(slug)
        if fila is None:
            raise _falla(404, "tenant_not_found", "La entidad no existe")
    if actor is None:
        raise _falla(403, "forbidden", "Falta la identidad del administrador")
    try:
        cuerpo = await pasarela.purgar(slug, actor=actor, reason="withdrawn", levels=None)
    except pasarela.PasarelaError:
        raise _falla(
            502,
            "purga_no_aceptada",
            "El proveedor no aceptó el borrado. No quedó asiento",
        ) from None
    ref = cuerpo.get("purge_ref")
    if not cuerpo.get("accepted") or not isinstance(ref, str) or not ref:
        raise _falla(
            502,
            "purga_no_aceptada",
            "El proveedor no aceptó el borrado. No quedó asiento",
        )
    async with conn.transaction():
        await _asiento(
            conn,
            slug=slug,
            action="tenant.intelligence_withdrawn",
            actor=actor,
            payload={"purge_ref": ref},
        )
    return WithdrawResponse(purge_ref=ref)
