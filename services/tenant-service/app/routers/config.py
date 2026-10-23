# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Endpoints de configuración por tenant (E14): festivos, días hábiles, parámetros.

Las escrituras exigen `USUA_PERM_ADMIN` (`app/core/authz.py`). Los GET de festivos
y `business-days/calculate` quedan solo autenticados: el cálculo de plazos legales
de respuesta los consume para cualquier usuario radicador, no solo administradores.
"""

import re
import uuid
from datetime import date, timedelta

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from orpycamcp_common.tenant import LOCAL_EMBEDDING_PROVIDERS

from app.core import audit as audit_core
from app.core.authz import require_permission
from app.core.calendar import add_business_days, colombian_holidays
from app.core.config import settings
from app.core.database import get_tenant_conn
from app.repositories.config_repository import ConfigRepository
from app.schemas.config import (
    AsistenteResponse,
    AsistenteUpdate,
    BusinessDaysResult,
    HolidayCreate,
    HolidayResponse,
    HolidaySeedRequest,
    ParamResponse,
    ParamUpdate,
    ProveedoresAsistente,
)

router = APIRouter(prefix="/api/v1", tags=["config"])


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


def _proveedores() -> ProveedoresAsistente:
    """Proveedores efectivos del asistente (los mismos ajustes que usan los servicios)."""
    return ProveedoresAsistente(
        asistente="anthropic",
        modelo=settings.assistant_model,
        rag=settings.ai_provider,
        embeddings=settings.embedding_provider,
        embeddings_local=settings.embedding_provider in LOCAL_EMBEDDING_PROVIDERS,
    )


@router.get("/config/asistente", response_model=AsistenteResponse)
async def get_asistente(
    request: Request, conn: asyncpg.Connection = Depends(get_tenant_conn)
) -> AsistenteResponse:
    """¿Tiene la entidad activado el asistente y la búsqueda semántica? (ADR-037)

    Devuelve además los proveedores efectivos (a quién se transmitiría), para que la tarjeta
    de ``/admin/parametros`` pueda pintarlos antes de encender.
    """
    slug = request.headers.get("X-Tenant-Slug", "")
    habilitado = await ConfigRepository(conn).get_asistente(slug)
    return AsistenteResponse(habilitado=habilitado, proveedores=_proveedores())


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
    """Enciende o apaga el asistente de LA entidad del request (ADR-037).

    El slug sale de la cabecera ya validada por ``get_tenant_conn``, nunca del cuerpo:
    una entidad no puede tocar la bandera de otra. Lectura con bloqueo, escritura y
    asiento Canal A en UNA transacción; pedir el valor vigente no es un acto (200 sin
    asiento).

    **Encender** es una transmisión de datos a un encargado (DUR 1074/2015 [confirmar
    numeral]; no una transferencia del art. 26): exige ``declaracion_transmision: true``
    (``422 declaracion_requerida``) y que la entidad tenga publicada su política de
    tratamiento (``409 politica_no_publicada``, el mismo código de la ventanilla). El
    asiento deja la versión de la política, los proveedores y la declaración. **Apagar** no
    pide nada.
    """
    slug = request.headers.get("X-Tenant-Slug", "")
    repo = ConfigRepository(conn)
    proveedores = _proveedores()
    motivo = (payload.motivo or "").strip() or None
    async with conn.transaction():
        antes = await repo.lock_asistente(slug)
        if antes is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "tenant_not_found", "detail": "La entidad no existe", "status": 404},
            )
        if antes == payload.habilitado:
            return AsistenteResponse(habilitado=antes, cambiado=False, proveedores=proveedores)
        version: str | None = None
        if payload.habilitado:
            if not payload.declaracion_transmision:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "declaracion_requerida",
                        "detail": (
                            "Encender el asistente transmite datos a los proveedores indicados: "
                            "envíe declaracion_transmision: true"
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
                            "(Ley 1581, art. 17); publíquela antes de encender el asistente"
                        ),
                        "status": 409,
                    },
                )
        await repo.set_asistente(slug, payload.habilitado)
        asiento: dict[str, object] = {"antes": antes, "despues": payload.habilitado, "motivo": motivo}
        if payload.habilitado:
            asiento |= {
                "politica_tratamiento_version": version,
                "proveedores": proveedores.model_dump(),
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
    return AsistenteResponse(habilitado=payload.habilitado, cambiado=True, proveedores=proveedores)
