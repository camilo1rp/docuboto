# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lo que archive y document le piden a firma, como llamadas a función (ADR-047 §1).

Antes eran saltos HTTP al mismo proceso con ``X-Internal-Token``. Sin frontera de
red la autorización interna es la propia llamada; lo que queda es lo que la ruta
comprobaba en la BD: el veto de la entidad, ``PERM_FIRMA`` del actor cuando hay
un humano detrás, y los mismos asientos con el mismo actor.

Cada función abre su conexión al tenant, igual que la dependencia de la ruta: el
acto de firma se compromete en su propia transacción, nunca en la del llamante.

El sello institucional (índice y actas) solo se pide desde aquí. No tiene ruta
pública: lo que no está montado no se puede alcanzar desde fuera.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common.clearance import enforce_permission
from orpycamcp_common.database import acquire_tenant
from orpycamcp_common.tenant import veredicto_entidad
from pydantic import ValidationError

from signature.core.constants import SYSTEM_RECONCILER_ID
from signature.repositories.cadena_repository import CadenaRepository
from signature.repositories.firma_repository import FirmaRepository
from signature.schemas.cadena import InvalidarCircuitosRequest
from signature.schemas.firma import (
    FirmaResponse,
    InternalSignIndiceRequest,
    SignPersonalRequest,
    SignXmlRequest,
)
from signature.services import invalidacion, signing_service, verification
from signature.services.personal_signing import PersonalSigningService

logger = logging.getLogger(__name__)


# --- Conexión ---------------------------------------------------------------


@asynccontextmanager
async def _tenant_conn(tenant_slug: str) -> AsyncIterator[asyncpg.Connection]:
    """Conexión al schema del tenant con el mismo veto de entidad que ``get_tenant_conn``."""
    async with acquire_tenant(tenant_slug) as conn:
        veto = await veredicto_entidad(conn, tenant_slug)
        if veto is not None:
            raise HTTPException(
                status_code=veto[0],
                detail={"error": veto[1], "detail": veto[2], "status": veto[0]},
            )
        yield conn


def _codigo_error(exc: HTTPException) -> str | None:
    detail = exc.detail
    return detail.get("error") if isinstance(detail, dict) else None


# --- Sello institucional ------------------------------------------------------


@dataclass(slots=True)
class SignedIndice:
    """Lo que el llamante guarda de un sello XAdES (índice o acta). Nunca material de clave."""

    firma_id: uuid.UUID
    minio_key_firmado: str | None
    nivel_conformidad: str | None
    xades_level: str | None
    tipo_firma: str | None
    acreditado: bool
    certificado_fingerprint: str | None


@dataclass(slots=True)
class SignOutcome:
    """Desenlace de un intento de sellado (Ac. AGN 001/2024 art. 4.3.2.4).

    Exactamente uno queda poblado: ``signed``; ``seal_absent`` (no hay sello
    institucional: reintentar ahora no cambia nada); o ``transient`` (cualquier
    otro fallo: el llamante reintenta más tarde). Ningún fallo lanza: el cierre
    del expediente y la recepción de la transferencia no se bloquean por el sello.
    """

    signed: SignedIndice | None
    seal_absent: bool = False
    transient: bool = False


def _signed_indice(r: FirmaResponse) -> SignedIndice:
    return SignedIndice(
        firma_id=r.firma_id,
        minio_key_firmado=r.minio_key_firmado,
        nivel_conformidad=r.nivel_conformidad,
        xades_level=r.xades_level,
        tipo_firma=r.tipo_firma,
        acreditado=bool(r.acreditado),
        certificado_fingerprint=r.certificado_fingerprint,
    )


def _fallo_sellado(exc: Exception, *, contexto: str) -> SignOutcome:
    """Clasifica un fallo: sello ausente no se reintenta; el resto sí.

    Se registra el MOTIVO, no solo el código: «falta aprovisionar el sello», «la
    TSA no responde» y «no tiene PERM_FIRMA» tienen remedios distintos y las tres
    dejan el objeto en ``pendiente_firma``.
    """
    if isinstance(exc, HTTPException):
        codigo = _codigo_error(exc)
        if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE and codigo == "signing_seal_unavailable":
            logger.warning("firma: sello institucional ausente (%s)", contexto)
            return SignOutcome(signed=None, seal_absent=True)
        logger.warning("firma: HTTP %s al sellar (%s, transient): %s", exc.status_code, contexto, exc.detail)
    else:
        logger.warning("firma: fallo al sellar (%s, transient)", contexto, exc_info=exc)
    return SignOutcome(signed=None, transient=True)


async def sign_xml_outcome(
    *,
    tenant_slug: str,
    actor_id: str,
    payload_xml: str,
    objeto_id: str,
    indice_id: str | None,
    version: int,
    objeto_tipo: str = "indice",
) -> SignOutcome:
    """Sello institucional ATRIBUIDO al humano que cierra o recibe (``origen='manual'``).

    El sello es de la entidad, pero quien lo pide tiene que poder firmar:
    ``PERM_FIRMA`` a nivel de escritura (AREA-04-07), comprobado en la BD como lo
    hacía la ruta. Sin permiso es un fallo ``transient``, no se sella.
    """
    contexto = f"sign-xml ({objeto_tipo})"
    try:
        payload = SignXmlRequest(
            payload_xml=payload_xml,
            objeto_tipo=objeto_tipo,
            objeto_id=objeto_id,
            formato="XAdES",
            indice_id=indice_id,
            version=version,
        )
        actor = uuid.UUID(str(actor_id))
        async with _tenant_conn(tenant_slug) as conn:
            await enforce_permission(conn, actor, "PERM_FIRMA", 2, tenant_slug=tenant_slug)
            firma = await signing_service.sign_payload(
                conn=conn,
                tenant_slug=tenant_slug,
                payload=payload,
                firmante_id=actor,
                origen="manual",
            )
    except Exception as exc:  # noqa: BLE001 — el sello es best-effort: todo fallo se clasifica.
        return _fallo_sellado(exc, contexto=contexto)
    return SignOutcome(signed=_signed_indice(firma))


async def sign_xml(
    *,
    tenant_slug: str,
    actor_id: str,
    payload_xml: str,
    objeto_id: str,
    indice_id: str,
    version: int,
) -> SignedIndice | None:
    """Sello del índice para el reintento manual: un intento, ``None`` ante cualquier fallo."""
    outcome = await sign_xml_outcome(
        tenant_slug=tenant_slug,
        actor_id=actor_id,
        payload_xml=payload_xml,
        objeto_id=objeto_id,
        indice_id=indice_id,
        version=version,
    )
    return outcome.signed


async def sign_as_system(
    *,
    tenant_slug: str,
    payload_xml: str,
    objeto_id: str,
    indice_id: str | None,
    version: int,
    objeto_tipo: str = "indice",
) -> SignOutcome:
    """Sello institucional ATRIBUIDO A SISTEMA: lo pide el job de reconciliación de archive.

    Sin humano detrás: ``firmante_id=SYSTEM_RECONCILER_ID`` y ``origen='reconciliation'``
    en el asiento ``firma.creada``, distinguible del sellado manual. ``objeto_tipo``
    queda acotado al allow-set institucional (``InternalSignIndiceRequest``): por aquí
    no se firma nada fuera de él.
    """
    contexto = f"sistema ({objeto_tipo}, reconciliación)"
    try:
        pedido = InternalSignIndiceRequest(
            payload_xml=payload_xml,
            objeto_tipo=objeto_tipo,
            objeto_id=objeto_id,
            indice_id=indice_id,
            version=version,
        )
        payload = SignXmlRequest(
            payload_xml=pedido.payload_xml,
            objeto_tipo=pedido.objeto_tipo,
            objeto_id=pedido.objeto_id,
            formato="XAdES",
            indice_id=pedido.indice_id,
            version=pedido.version,
        )
        async with _tenant_conn(tenant_slug) as conn:
            firma = await signing_service.sign_payload(
                conn=conn,
                tenant_slug=tenant_slug,
                payload=payload,
                firmante_id=SYSTEM_RECONCILER_ID,
                origen="reconciliation",
            )
    except Exception as exc:  # noqa: BLE001 — el job reintenta con backoff: todo fallo se clasifica.
        return _fallo_sellado(exc, contexto=contexto)
    return SignOutcome(signed=_signed_indice(firma))


# --- Verificación -------------------------------------------------------------


@dataclass(slots=True)
class VerifyResult:
    """Veredicto de una firma (RF-FIR-15), el subconjunto que archive muestra y conserva."""

    valida: bool
    nivel_conformidad: str | None
    acreditado: bool
    motivo: str | None
    # Solo para firmas personales (`vigente`/`revocado`/`no_verificable`/`desconocido`).
    revocacion_status: str | None = None
    # Avisos de una verificación que SIGUE siendo válida.
    advertencias: list[str] = field(default_factory=list)
    # ¿El último sello de archivo aún prueba? `None` si la firma no es LTA.
    lta_vigente: bool | None = None


async def verify(*, tenant_slug: str, firma_id: str, payload_xml: str) -> VerifyResult | None:
    """Verifica ``payload_xml`` (el XML YA FIRMADO) contra la firma ``firma_id``.

    Sin usuario detrás no hay clearance que aplicar: quien llama ya resolvió el
    acceso al expediente. ``None`` si la firma no existe o no se pudo verificar;
    el llamante no declara conforme lo que no pudo verificar.
    """
    try:
        fid = uuid.UUID(str(firma_id))
        async with _tenant_conn(tenant_slug) as conn:
            row = await FirmaRepository(conn).get(fid, max_clearance=None)
            if row is None:
                logger.warning("firma: firma_id %s no existe al verificar", firma_id)
                return None
            r = await verification.verificar_firma(conn, row, payload_xml, tenant_slug)
    except Exception:  # noqa: BLE001 — no verificable no es «no válida»: el llamante decide.
        logger.warning("firma: no se pudo verificar %s", firma_id, exc_info=True)
        return None
    return VerifyResult(
        valida=r.valida,
        nivel_conformidad=r.nivel_conformidad,
        acreditado=r.acreditado,
        motivo=r.motivo,
        revocacion_status=r.revocacion_status,
        advertencias=list(r.advertencias),
        lta_vigente=r.lta_vigente,
    )


# --- Artefacto vigente de una firma LTA (C-6, ADR-042) ------------------------


@dataclass(slots=True, frozen=True)
class ArtefactoVigente:
    """Un eslabón vigente renovado (``ordinal`` >= 1) de una firma XAdES-LTA."""

    firma_id: str
    file_id: uuid.UUID
    minio_key: str
    xml_sha256: str
    ordinal: int


class SinRenovacion:
    """La firma no tiene eslabón renovado: el vigente es el original."""


class ErrorArtefacto:
    """No se pudo leer el artefacto vigente (NO equivale a «sin renovación»)."""


SIN_RENOVACION = SinRenovacion()
ERROR_ARTEFACTO = ErrorArtefacto()


async def artefactos_vigentes(
    *, tenant_slug: str, firma_ids: list[str]
) -> dict[str, ArtefactoVigente] | None:
    """``{firma_id: ArtefactoVigente}`` de las firmas del lote que existen.

    ``None`` ante cualquier fallo: quien llama NUNCA debe leerlo como «sin
    renovación». Una entidad suspendida lo sigue atendiendo (conservación): el
    reempaquetado del AIP no se detiene por la suspensión (ADR-035 §8).
    """
    try:
        ids = [uuid.UUID(str(f)) for f in firma_ids]
        async with _tenant_conn(tenant_slug) as conn:
            try:
                filas = await FirmaRepository(conn).artefactos_vigentes(ids)
            except asyncpg.UndefinedTableError:
                filas = []  # tenant sin la migración 026: ninguna firma se renovó
    except Exception:  # noqa: BLE001 — error, no «sin renovación»: el llamante reintenta.
        logger.warning("firma: artefactos vigentes no resueltos", exc_info=True)
        return None
    return {
        str(f["firma_id"]): ArtefactoVigente(
            firma_id=str(f["firma_id"]),
            file_id=uuid.UUID(f["minio_key"].split("/", 1)[0]),
            minio_key=f["minio_key"],
            xml_sha256=f["xml_sha256"],
            ordinal=f["ordinal"],
        )
        for f in filas
    }


async def resolver_artefacto_vigente(
    *, tenant_slug: str, firma_id: str
) -> ArtefactoVigente | SinRenovacion | ErrorArtefacto:
    """Tri-estado para UNA firma: ``ArtefactoVigente`` | ``SinRenovacion`` | ``ErrorArtefacto``."""
    lote = await artefactos_vigentes(tenant_slug=tenant_slug, firma_ids=[firma_id])
    if lote is None:
        return ERROR_ARTEFACTO
    return lote.get(firma_id) or SIN_RENOVACION


async def artefacto_vigente(*, tenant_slug: str, firma_id: str) -> str | None:
    """``minio_key`` del XML vigente de la firma, o ``None`` (sin renovación O error).

    Solo para lectores que ya degradan con aviso (``IndexService.verify``). El cableado del AIP
    NO debe usarla: distingue error de «sin renovación» con ``resolver_artefacto_vigente``."""
    r = await resolver_artefacto_vigente(tenant_slug=tenant_slug, firma_id=firma_id)
    return r.minio_key if isinstance(r, ArtefactoVigente) else None


# --- Firma personal -----------------------------------------------------------


@dataclass(slots=True)
class SignedPersonal:
    """Lo que el llamante guarda de una firma personal (``provider='xades_personal'``)."""

    firma_id: uuid.UUID
    minio_key_firmado: str | None
    xades_level: str | None
    acreditado: bool
    certificado_fingerprint: str | None
    nivel_conformidad: str | None


@dataclass(slots=True)
class SignPersonalOutcome:
    """Desenlace de una firma PERSONAL (E17/F4). Es interactiva: nunca se degrada en silencio.

    Exactamente uno queda poblado:
    - ``signed``: éxito.
    - ``rejected``: respuesta de NEGOCIO, ``(status_code, detail)`` tal cual (422
      ``sin_certificado_activo``, 401 ``totp_incorrecto``, 403 ``firmante_no_coincide``,
      409 ``2fa_requerido``, 404 ``object_not_found``...). El llamante la reenvía.
    - ``remote_failure``: fallo del lado de firma (5xx o excepción), ``(status_code,
      causa)``. Se separa del rechazo para que el mensaje nombre la causa real
      —MinIO sin disco, un ``CHECK`` que no admitía su propio valor— en vez de
      culpar al usuario.
    """

    signed: SignedPersonal | None
    rejected: tuple[int, dict] | None = None
    remote_failure: tuple[int, str] | None = None


def _causa(detail: object) -> str:
    """Texto legible del ``detail`` de un 5xx, para el mensaje al usuario."""
    if isinstance(detail, dict):
        error = detail.get("error")
        texto = detail.get("detail") or ""
        return f"{error}: {texto}".strip(": ") if error else str(texto)[:300]
    return str(detail)[:300]


async def sign_personal(
    *,
    tenant_slug: str,
    actor_id: str,
    payload_xml: str,
    objeto_tipo: str,
    objeto_id: str,
    totp_code: str,
    firmante_id: str | None = None,
) -> SignPersonalOutcome:
    """Firma PERSONAL PKI de ``payload_xml`` con el certificado de ``actor_id``.

    Mismo gate que ``POST /sign-personal``: ``PERM_FIRMA`` del actor en la BD. El
    TOTP se verifica-y-consume en auth para ese mismo actor. ``firmante_id`` es defensivo: si se declara,
    tiene que coincidir con el actor (403 ``firmante_no_coincide``).
    """
    try:
        payload = SignPersonalRequest(
            payload_xml=payload_xml,
            objeto_tipo=objeto_tipo,
            objeto_id=objeto_id,
            totp_code=totp_code,
            firmante_id=firmante_id,
        )
    except ValidationError as exc:
        return SignPersonalOutcome(
            signed=None,
            rejected=(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {"error": "validation_error", "detail": str(exc), "status": 422},
            ),
        )
    try:
        actor = uuid.UUID(str(actor_id))
        async with _tenant_conn(tenant_slug) as conn:
            await enforce_permission(conn, actor, "PERM_FIRMA", tenant_slug=tenant_slug)
            firma = await PersonalSigningService(conn, tenant_slug).sign(payload, actor)
    except HTTPException as exc:
        if exc.status_code >= 500:
            causa = _causa(exc.detail)
            logger.warning(
                "firma: HTTP %s al firmar personalmente (%s): %s", exc.status_code, objeto_tipo, causa
            )
            return SignPersonalOutcome(signed=None, remote_failure=(exc.status_code, causa))
        detail = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
        return SignPersonalOutcome(signed=None, rejected=(exc.status_code, detail))
    except Exception as exc:  # noqa: BLE001 — fail-closed, pero nombrando la causa.
        logger.warning("firma: fallo al firmar personalmente (%s)", objeto_tipo, exc_info=True)
        return SignPersonalOutcome(
            signed=None, remote_failure=(500, f"{type(exc).__name__}: {exc}"[:300])
        )
    return SignPersonalOutcome(
        signed=SignedPersonal(
            firma_id=firma.firma_id,
            minio_key_firmado=firma.minio_key_firmado,
            xades_level=firma.xades_level,
            acreditado=bool(firma.acreditado),
            certificado_fingerprint=firma.certificado_fingerprint,
            nivel_conformidad=firma.nivel_conformidad,
        )
    )


# --- Circuito de aprobación de un borrador (E23, RF-PRO-09) -------------------


class CircuitoNoVerificableError(RuntimeError):
    """No se pudo saber si hay un circuito abierto.

    El llamante responde 503 y NO aprueba: suponer «no hay circuito» sería
    exactamente el fallo que este gate impide.
    """


async def get_estado_circuito(*, borrador_id: str, tenant_slug: str) -> dict:
    """Si hay un circuito abierto sobre el borrador, y qué texto firmó el último cerrado.

    Document PREGUNTA en vez de guardar una marca que firma actualizara: una
    copia se queda rancia por caminos que no pasan por aquí (borrar la cadena,
    restaurar la base, un consumidor retrasado), y rancia del lado permisivo es
    una puerta abierta.

    Busca por OBJETO, no por el tipo declarado: el id es un UUID y basta; filtrar
    también por la etiqueta que eligió el solicitante dejaba esquivar el gate
    abriendo la cadena con otro tipo. Content-free (dos booleanos, un id, una
    huella): no hay nada que recortar por clearance.

    ``pendiente`` y ``completo`` no son opuestos: sin circuito los dos son ``False``.
    """
    try:
        async with _tenant_conn(tenant_slug) as conn:
            repo = CadenaRepository(conn)
            abierta = await repo.get_solicitud_por_objeto_id(borrador_id, solo_abierta=True)
            cerrada = await repo.ultimo_circuito_firmado(borrador_id)
    except Exception as exc:  # noqa: BLE001 — no saber es no poder aprobar, nunca aprobar de más.
        raise CircuitoNoVerificableError(f"estado del circuito no disponible: {exc}") from exc
    return {
        "objeto_tipo": "borrador",
        "objeto_id": borrador_id,
        "pendiente": abierta is not None,
        "completo": cerrada is not None,
        "solicitud_id": str(abierta["id"]) if abierta is not None else None,
        # Sin esta huella document no notaba que el texto cambió después de firmarse.
        "payload_sha256": cerrada["payload_sha256"] if cerrada is not None else None,
    }


async def invalidar_circuitos(
    *, borrador_id: str, tenant_slug: str, cuerpo_sha256_vigente: str
) -> int:
    """Deja sin efecto los circuitos del borrador que no recaen sobre su texto vigente.

    Se invalida lo que NO recae sobre la huella vigente (``IS DISTINCT FROM``): un
    aviso tardío tras h0→h1→h0 no tira el circuito abierto sobre el texto que sí
    está vigente. Deja asiento ``firma.circuito_invalidado``.

    Best-effort: la garantía dura es la comparación de huellas al radicar, así que
    un fallo aquí no puede volver inservible la edición. Devuelve cuántas se
    invalidaron, o -1 si no se pudo saber.
    """
    try:
        # Una huella mal formada invalidaría también el circuito sobre el texto vigente.
        vigente = InvalidarCircuitosRequest(cuerpo_sha256_vigente=cuerpo_sha256_vigente)
        async with _tenant_conn(tenant_slug) as conn:
            return await invalidacion.invalidar_por_contenido(
                conn,
                tenant_slug=tenant_slug,
                objeto_tipo="borrador",
                objeto_id=borrador_id,
                vigente=vigente.cuerpo_sha256_vigente,
                origen="sincrono",
                event_id=None,
            )
    except Exception:  # noqa: BLE001 — avisar no puede impedir trabajar.
        logger.warning(
            "No se pudieron invalidar los circuitos del borrador %s", borrador_id, exc_info=True
        )
        return -1
