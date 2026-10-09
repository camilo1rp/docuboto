# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Transferencias documentales (E12).

Ciclo `preparada → enviada → recibida | rechazada`. Precondición: el expediente
debe estar `closed`. Al **recibir** se congela el expediente (`transferred`,
ya no admite documentos). El FUID (E17) acompaña la transferencia como entregable.
"""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import logging
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Literal

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.core.actor import Actor
from app.core.clearance import CLEARANCE_SIN_RESTRICCION, check_permission
from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.core.http_errors import motivo_de
from app.core.params_entidad import gate_xades_activo, modo_xades_de_la_entidad
from app.repositories.archive_repository import ArchiveRepository
from app.repositories.fisico_repository import FisicoRepository
from app.repositories.index_repository import IndexRepository
from app.repositories.transferencia_repository import TransferenciaRepository
from app.schemas.fisico import FuidResponse
from app.schemas.transferencia import (
    FirmaInobtenibleRequest,
    FundamentoLectura,
    FundamentoVerificacion,
    RevocarFirmaInobtenibleRequest,
    SobreFundamentoResponse,
    TransferenciaActaFirmaExcepcion,
    TransferenciaActaFirmaInfo,
    TransferenciaActaFirmaPersonalResponse,
    TransferenciaActaFirmaPersonalVerif,
    TransferenciaActaResponse,
    TransferenciaActaVerificacionResponse,
    TransferenciaCreate,
    TransferenciaRechazo,
    TransferenciaResponse,
    WormReintentoResponse,
)
from app.services.backoff import next_backoff_seconds
from app.services.constancia_firma_excepcion import (
    CONSTANCIA_VERSION,
    REVOCACION_VERSION,
    Identidad,
    build_constancia_xml,
    build_revocacion_xml,
    build_sobre_xml,
    compromiso_coincide,
    enlaces_de_constancia,
    enlaces_de_revocacion,
    huella_de_firmado,
    huella_de_fundamento,
    leer_sobre,
    motivo_publico,
    referencia_canonica,
    sha256_c14n,
)

# `_fuid_to_xml` (E12 H-I): reusada TAL CUAL, sin duplicar la serialización
# canónica (C14N) -- es el mismo cuerpo que congela el acta de entrega.
from app.services.fisico_service import FisicoService, _fuid_to_xml
from app.services.index_service import IndexService
from app.services.retention_math import base_worm, total_trd_years

if TYPE_CHECKING:
    from app.clients.signature_client import SignedPersonal

logger = logging.getLogger(__name__)

_SERVICE = "archive-service"

# 403 del conflicto de interés de la «firma inobtenible» (AREA-07-10): ni quien declara ni quien
# revoca la excepción puede ser parte de la transferencia. Un solo código para el servicio y el
# asiento de denegación.
_CONFLICTO_DE_INTERES = "declarante_es_parte_de_la_transferencia"

_VALID = {
    "preparada": {"enviada"},
    "enviada": {"recibida", "rechazada"},
    "recibida": set(),
    "rechazada": set(),
}

# H-M (Media, Ley 594/2000 art. 23 -- ciclo vital del documento): fase de
# archivo esperada en origen/destino según el tipo de transferencia.
# `ubicacion.tipo_archivo` (migración 010, `gestion|central|historico`) es la
# FASE de archivo, no un dato de contenido -- se valida en `create()` cuando
# el cliente aporta el par de ubicaciones.
_TIPO_ARCHIVO_ESPERADO: dict[str, tuple[str, str]] = {
    "primaria": ("gestion", "central"),
    "secundaria": ("central", "historico"),
}


_CN_RE = re.compile(r"(?:^|,)\s*CN=((?:\\.|[^,\\])+)", re.IGNORECASE)


def _cn_de(subject_dn: str | None) -> str | None:
    """CN del Subject DN de un certificado (`CN=Ana Pérez,O=Entidad,...`), sin escapes."""
    if not subject_dn:
        return None
    m = _CN_RE.search(subject_dn)
    if m is None:
        return None
    return re.sub(r"\\(.)", r"\1", m.group(1)).strip() or None


def _resp(row) -> TransferenciaResponse:
    return TransferenciaResponse(**dict(row))


_CABECERA_FUID_OBLIGATORIA = ("unidadAdministrativa", "oficinaProductora", "objeto")


def _cabecera_completa(acta_fuid_xml: str) -> bool:
    """¿Trae el acta congelada las tres entradas con las que abre el Anexo FUID? (AREA-07-11)

    Las actas anteriores a la migración 052 se sellaron con la cabecera en blanco y no
    se rehacen (son probatorias). Se declara el hueco al LEER: un XML que no se puede
    parsear cuenta como incompleto, nunca como completo."""
    import xml.etree.ElementTree as ET

    try:
        raiz = ET.fromstring(acta_fuid_xml)
    except ET.ParseError:
        return False
    cab = next((e for e in raiz if e.tag.rsplit("}", 1)[-1] == "cabecera"), None)
    if cab is None:
        return False
    valores = {e.tag.rsplit("}", 1)[-1]: (e.text or "").strip() for e in cab}
    return all(valores.get(campo) for campo in _CABECERA_FUID_OBLIGATORIA)


def _sal_hex(sal) -> str | None:
    return bytes(sal).hex() if sal is not None else None


def _sin_repetir(avisos: list[str]) -> list[str]:
    """Deduplica conservando el orden de aparición."""
    return list(dict.fromkeys(avisos))


class TransferenciaService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._repo = TransferenciaRepository(conn, tenant_slug)
        self._archive = ArchiveRepository(conn, tenant_slug)
        self._index_repo = IndexRepository(conn)
        # Fixity en la recepción (E12 H-G): reusa `IndexService.fixity`, el
        # mismo tramo puro-BD que `verify()` -- se conserva `self._index_repo`
        # aparte para el gate `firmado` (pre-flight barato, una fila, FUERA de
        # la tx de `recibir`).
        self._index_service = IndexService(conn, tenant_slug)
        self._conn = conn
        self._tenant = tenant_slug

    async def create(self, payload: TransferenciaCreate, actor: Actor) -> TransferenciaResponse:
        """Prepara una transferencia (crea la unidad de entrega). Acto de
        custodia (H-B): antes esta creación no dejaba NINGÚN asiento en
        `audit_log` ni en la hoja de ruta del expediente -- la primera pisada
        de un ciclo que sí termina auditado (`recibir`) empezaba invisible.

        `actor` es SIEMPRE `app.core.actor.Actor` (H-D): identidad unívoca
        resuelta en un único sitio (`X-User-Id`, sin fallback a username).
        """
        try:
            # Pre-condición interna de existencia/estado (¿existe y está
            # 'closed'?), no una lectura expuesta al llamante — el endpoint ya
            # exige USUA_PERM_EXPEDIENTE crud>=3 (D-08). RESUELTO-ortogonal (ya
            # NO es deuda): la transferencia es un acto de custodia, ortogonal
            # al clearance de lectura (Opción B, ver `core.clearance
            # .CLEARANCE_SIN_RESTRICCION` bloque (c)). RF-SEG-08.
            exp = await self._archive.get_expediente_by_id(
                payload.expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
            )
            if exp is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": "expediente_not_found",
                        "detail": str(payload.expediente_id),
                        "status": 404,
                    },
                )
            if exp.status != "closed":
                # Dos situaciones distintas con dos remedios distintos, y antes
                # las dos decían lo mismo. Sobre un expediente ya
                # `transferred`, «solo se transfiere un expediente cerrado» se
                # lee como «ciérrelo primero» — y no hay nada que cerrar: la
                # custodia ya cambió por la vía corta
                # (`ArchiveService.transfer_expediente`, el botón «Transferir
                # expediente» de la ficha), que no produce acta ni FUID y deja
                # este circuito inalcanzable para siempre. Quien recibe el
                # error tiene que poder saber cuál de las dos cosas le pasó.
                ya_transferido = exp.status == "transferred"
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": (
                            "expediente_ya_transferido"
                            if ya_transferido
                            else "expediente_not_closed"
                        ),
                        "detail": (
                            "El expediente ya está transferido: su custodia cambió "
                            "sin pasar por este circuito, así que no puede abrirse "
                            "una transferencia con acta de entrega para él"
                            if ya_transferido
                            else "Solo se transfiere un expediente cerrado"
                        ),
                        "status": 409,
                    },
                )
            # H-M (Media, Ley 594/2000 art. 23 -- ciclo vital) + cotejo (RF-FIR-15,
            # Ac. AGN 001/2024 Anexo FUID; Ley 594/2000 arts. 15/26): coherencia
            # origen/destino con el TIPO de transferencia, y obligatoriedad
            # CONDICIONADA a la tenencia física LOCALIZADA del expediente.
            # `expediente_unidad` es M2M (un expediente puede estar en VARIAS
            # unidades) y `unidad_conservacion.ubicacion_id` es NULLABLE
            # (migración 010, ON DELETE SET NULL) -- una unidad SIN
            # `ubicacion_id` no cuenta como tenencia localizada, no hay
            # ubicación contra la cual cotejar.
            #
            # Rama A (CON tenencia localizada): origen/destino OBLIGATORIOS y
            # el origen debe COTEJAR -- estar ENTRE las ubicaciones ACTUALES
            # del expediente (no una fase de archivo arbitraria). Sin esto, el
            # acta FUID congelada al recibir (H-I) atestiguaría un movimiento
            # no sustentado por el vínculo real `expediente_unidad` --
            # inventario incompleto.
            #
            # Rama B (SIN tenencia localizada -- expediente puramente
            # electrónico, o físico sin `ubicacion_id` resuelta): la
            # transferencia es LÓGICA y origen/destino físico son N/A: NO se
            # aportan (AREA-07-06: antes se aceptaba el par «ambos o ninguno»
            # sin cotejo, un residual declarado; ahora el par sin tenencia es
            # 422 `ubicacion_sin_tenencia`). Exigir topología física SIEMPRE
            # haría imposible transferir un expediente electrónico, el caso de
            # uso central de un DMS moderno.
            #
            # El destino NUNCA se cotea (en ninguna rama): es a DÓNDE VA, no
            # dónde está -- solo se valida por coherencia de tipo.
            #
            # Todo esto vive en `_validar_ubicaciones`, que corre DENTRO de la
            # transacción del acto con la tenencia bloqueada `FOR SHARE`
            # (AREA-07-06): leerla antes dejaba una ventana en la que la unidad
            # podía moverse entre el cotejo y el INSERT.
            #
            # Gate del índice (AREA-07-03): un expediente cuyo índice no está
            # firmado no puede ni PREPARAR la transferencia -- antes solo
            # `recibir` lo exigía, y el defecto avanzaba hasta la última fase
            # (enviada, a la espera de que el destino la recibiera y fallara).
            # Mismo código de error que `recibir`.
            current_indice = await self._index_repo.get_current(payload.expediente_id)
            if current_indice is None or current_indice["estado"] != "firmado":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "indice_no_firmado",
                        "detail": (
                            "El expediente no puede transferirse sin índice electrónico "
                            "firmado (Acuerdo AGN 001/2024 art. 4.3.2.4)"
                        ),
                        "status": 409,
                    },
                )

            # Atomicidad preparación<->auditoría (mismo patrón que
            # `ArchiveService.create_expediente`): INSERT + evento de hoja de
            # ruta + asiento forense son un único acto de negocio; `audit.append`
            # va LO ÚLTIMO (retiene el advisory lock del tenant el mínimo tiempo).
            async with self._conn.transaction():
                await self._validar_ubicaciones(payload)
                # Cabecera del FUID (migración 052, Anexo FUID Ac. AGN 001/2024): la
                # dependencia de quien PREPARA, resuelta AQUÍ y congelada con la
                # transferencia. Es quien ENTREGA los documentos —la oficina
                # productora del Anexo—, no quien los recibe; y se captura ahora
                # porque entre preparar y recibir pueden pasar semanas en las que el
                # preparador cambie de dependencia o la dependencia se renombre.
                # Lectura PLANA de identidad/organigrama: no es contenido del
                # expediente, no hay concern de clearance.
                depe = await self._repo.dependencia_de_usuario(actor.id)
                # H-L (Media, Ac. AGN 001/2024 Tít. 4.4): a lo sumo UNA
                # transferencia ACTIVA (preparada/enviada) por expediente
                # (índice único parcial, migración 024). El gate `closed` de
                # arriba NO cierra la carrera: dos `create()` concurrentes
                # sobre el mismo expediente pasan ambos ese chequeo -- el
                # ÍNDICE es quien serializa de verdad. El 2º INSERT viola la
                # unicidad y se traduce aquí a un 409 limpio, nunca un 500.
                try:
                    row = await self._repo.insert(
                        {
                            "tipo": payload.tipo,
                            "expediente_id": payload.expediente_id,
                            "ubicacion_origen_id": payload.ubicacion_origen_id,
                            "ubicacion_destino_id": payload.ubicacion_destino_id,
                            "observacion": payload.observacion,
                            "creada_por": actor.audit_actor,
                            "objeto": payload.objeto,
                            "oficina_productora_codigo": (
                                depe["oficina_productora_codigo"] if depe else None
                            ),
                            "oficina_productora_nombre": (
                                depe["oficina_productora_nombre"] if depe else None
                            ),
                            "unidad_administrativa_codigo": (
                                depe["unidad_administrativa_codigo"] if depe else None
                            ),
                            "unidad_administrativa_nombre": (
                                depe["unidad_administrativa_nombre"] if depe else None
                            ),
                        }
                    )
                except asyncpg.ForeignKeyViolationError as fk:
                    # AREA-07-08: la ubicación se valida arriba, pero otro proceso
                    # puede borrarla entre ese cotejo y este INSERT; la FK lo
                    # impide y antes salía como 500 SIN asiento (no pasaba por el
                    # `except HTTPException`). Ahora es el mismo 422 y deja asiento.
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail={
                            "error": "ubicacion_not_found",
                            "detail": "ubicacion_origen_id o ubicacion_destino_id no existe",
                            "status": 422,
                        },
                    ) from fk
                except asyncpg.UniqueViolationError as uv:
                    # Discrimina por CONSTRAINT, no solo por tipo de excepción:
                    # hoy el único índice único de `transferencias` (aparte del
                    # PK `id`, inalcanzable con `gen_random_uuid`) es el de H-L,
                    # pero una unicidad AÑADIDA por una migración futura no debe
                    # etiquetarse erróneamente como este 409 -- si la violación
                    # no es la del índice de H-L, se re-lanza el error original
                    # (mejor un fallo honesto que un mensaje engañoso).
                    if uv.constraint_name != "ux_transferencias_activa_por_expediente":
                        raise
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error": "transferencia_activa_existente",
                            "detail": (
                                "Ya existe una transferencia activa (preparada/enviada) "
                                "para este expediente"
                            ),
                            "status": 409,
                        },
                    ) from uv
                await self._archive.insert_expediente_evento(
                    payload.expediente_id,
                    numero_expediente=exp.code,
                    accion="transferencia_preparada",
                    actor=actor.id,
                    comentario=payload.observacion,
                    detalles={
                        "transferencia_id": str(row["id"]),
                        "tipo": payload.tipo,
                        "ubicacion_origen_id": str(payload.ubicacion_origen_id)
                        if payload.ubicacion_origen_id
                        else None,
                        "ubicacion_destino_id": str(payload.ubicacion_destino_id)
                        if payload.ubicacion_destino_id
                        else None,
                    },
                )
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action="archive.transferencia_preparada",
                    object_type="transferencia",
                    object_ref=str(row["id"]),
                    actor=actor.audit_actor,
                    payload={
                        "tipo": payload.tipo,
                        "expediente_id": str(payload.expediente_id),
                        "expediente_code": exp.code,
                        "estado_anterior": None,
                        "estado_nuevo": "preparada",
                        "ubicacion_origen_id": str(payload.ubicacion_origen_id)
                        if payload.ubicacion_origen_id
                        else None,
                        "ubicacion_destino_id": str(payload.ubicacion_destino_id)
                        if payload.ubicacion_destino_id
                        else None,
                    },
                    canal=audit.CANAL_PETICION,
                )
        except HTTPException as exc:
            # Denegación (H-B): mismo patrón best-effort que
            # `expediente_close_failed`/`expediente_transfer_failed`. No hay
            # transferencia creada que referenciar como `object_ref` (el fallo
            # es previo al INSERT) -- se ancla al EXPEDIENTE que se intentó
            # transferir, el objeto identificable más cercano.
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action="archive.transferencia_preparada_failed",
                    object_type="transferencia",
                    object_ref=str(payload.expediente_id),
                    actor=actor.audit_actor,
                    payload={
                        "expediente_id": str(payload.expediente_id),
                        "motivo": motivo_de(exc),
                    },
                    canal=audit.CANAL_PETICION,
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                logger.warning(
                    "audit.append(archive.transferencia_preparada_failed) failed",
                    exc_info=True,
                )
            raise
        return _resp(row)

    async def _validar_ubicaciones(self, payload: TransferenciaCreate) -> None:
        """Rama A/B de la coherencia origen/destino de `create` (ver los comentarios
        de ese método). Corre DENTRO de su transacción (AREA-07-06), con la tenencia
        bloqueada `FOR SHARE`. Lanza `HTTPException` 422; no devuelve nada."""
        origen_id = payload.ubicacion_origen_id
        destino_id = payload.ubicacion_destino_id
        fisico_repo = FisicoRepository(self._conn, self._tenant)
        # AREA-07-06: la tenencia se lee DENTRO de la transacción del acto y con
        # `FOR SHARE`, no antes: un movimiento de la unidad entre la lectura y el
        # INSERT dejaba al acta cotejada contra una ubicación que ya no era la actual.
        units = await fisico_repo.units_of_expediente(payload.expediente_id, lock_share=True)
        current_ubicaciones = {
            u["ubicacion_id"] for u in units if u["ubicacion_id"] is not None
        }
        tiene_tenencia_localizada = bool(current_ubicaciones)

        if tiene_tenencia_localizada:
            if origen_id is None or destino_id is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "ubicaciones_requeridas_fisico",
                        "detail": (
                            "El expediente tiene tenencia física localizada -- "
                            "ubicacion_origen_id y ubicacion_destino_id son "
                            "obligatorios (Ley 594/2000 arts. 15/26, Anexo FUID "
                            "Ac. AGN 001/2024)"
                        ),
                        "status": 422,
                    },
                )
        elif (origen_id is None) != (destino_id is None):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "ubicaciones_par_incompleto",
                    "detail": (
                        "ubicacion_origen_id y ubicacion_destino_id deben aportarse "
                        "juntas (traspaso con topología física) o ninguna (sin "
                        "topología física modelada)"
                    ),
                    "status": 422,
                },
            )
        if not tiene_tenencia_localizada and origen_id is not None and destino_id is not None:
            # AREA-07-06: sin tenencia localizada NO hay ubicación actual contra la cual
            # cotejar el origen, y antes el par se aceptaba tal cual: el acta FUID
            # atestiguaba un movimiento físico que ningún vínculo `expediente_unidad`
            # sustenta (el residual que la rama B declaraba). Una transferencia lógica
            # va SIN par; con par, hace falta tenencia que lo respalde.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "ubicacion_sin_tenencia",
                    "detail": (
                        "El expediente no tiene tenencia física localizada (ninguna unidad "
                        "de conservación con ubicación): una transferencia lógica no lleva "
                        "ubicacion_origen_id ni ubicacion_destino_id, porque no hay "
                        "ubicación actual contra la cual cotejarlas"
                    ),
                    "status": 422,
                },
            )
        # Ambos, no solo `origen_id`: las validaciones de arriba ya
        # garantizan "las dos o ninguna" (`ubicaciones_par_incompleto`),
        # pero ese invariante vive en otra rama y aquí `destino_id` se usa
        # igualmente. Explícito, mismo comportamiento.
        if origen_id is not None and destino_id is not None:
            if origen_id == destino_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "ubicaciones_origen_destino_iguales",
                        "detail": "ubicacion_origen_id y ubicacion_destino_id no pueden coincidir",
                        "status": 422,
                    },
                )
            ub_origen = await fisico_repo.get_ubicacion(origen_id)
            ub_destino = await fisico_repo.get_ubicacion(destino_id)
            if ub_origen is None or ub_destino is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "ubicacion_not_found",
                        "detail": "ubicacion_origen_id o ubicacion_destino_id no existe",
                        "status": 422,
                    },
                )
            # Cotejo (nuevo, solo rama A -- sin tenencia localizada no hay
            # ubicación actual contra la cual cotejar). Va ANTES de la
            # coherencia de tipo: "no es una ubicación actual del
            # expediente" es un error fáctico más fuerte y específico que
            # "es del tipo equivocado" -- el cotejo gana.
            if tiene_tenencia_localizada and origen_id not in current_ubicaciones:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "ubicacion_origen_no_actual",
                        "detail": (
                            "ubicacion_origen_id no es una ubicación actual del "
                            "expediente (vínculo expediente_unidad)"
                        ),
                        "status": 422,
                    },
                )
            esperado_origen, esperado_destino = _TIPO_ARCHIVO_ESPERADO[payload.tipo]
            if (
                ub_origen["tipo_archivo"] != esperado_origen
                or ub_destino["tipo_archivo"] != esperado_destino
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "ubicacion_incoherente_con_tipo",
                        "detail": (
                            f"Transferencia '{payload.tipo}' exige origen de tipo "
                            f"'{esperado_origen}' y destino de tipo '{esperado_destino}' "
                            "(Ley 594/2000 art. 23, ciclo vital del documento)"
                        ),
                        "status": 422,
                    },
                )

    async def _require(self, transferencia_id: uuid.UUID, target: str):
        existing = await self._repo.get(transferencia_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "transferencia_not_found",
                    "detail": str(transferencia_id),
                    "status": 404,
                },
            )
        if target not in _VALID.get(existing["estado"], set()):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "invalid_status_transition",
                    "detail": f"No se puede pasar de '{existing['estado']}' a '{target}'",
                    "status": 409,
                },
            )
        return existing

    async def _expediente_code(self, expediente_id: uuid.UUID) -> str | None:
        """Código legible del expediente, para `numero_expediente=` (hoja de
        ruta) y `payload.expediente_code` (auditoría) -- mismo criterio
        `CLEARANCE_SIN_RESTRICCION` que `create` (pre-condición interna, no
        una lectura expuesta, RF-SEG-08 bloque (c))."""
        exp = await self._archive.get_expediente_by_id(
            expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
        )
        return exp.code if exp is not None else None

    async def enviar(self, transferencia_id: uuid.UUID, actor: Actor) -> TransferenciaResponse:
        """Remite la transferencia (preparada → enviada): el remitente se
        desprende de la custodia y abre la responsabilidad del destino (H-B/H-C).

        Antes NO dejaba asiento (0 llamadas a `audit.append` en `enviar`) ni
        recibía `actor` -- el router ni siquiera declaraba la cabecera (H-C)."""
        existing: asyncpg.Record | None = None
        try:
            existing = await self._require(transferencia_id, "enviada")
            expediente_code = await self._expediente_code(existing["expediente_id"])
            async with self._conn.transaction():
                row = await self._repo.update_estado(
                    transferencia_id,
                    expected_estado="preparada",
                    estado="enviada",
                    assignments={
                        "enviada_at": datetime.now(tz=UTC),
                        "enviada_por": actor.audit_actor,
                    },
                )
                # H-A, fail-closed DENTRO de la tx: `update_estado` cierra el
                # `WHERE` con `AND estado = $N` -- un `None` aquí significa que
                # OTRA transición (p. ej. un `enviar` concurrente) ganó la
                # carrera entre `_require` (arriba) y este UPDATE.
                if row is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error": "invalid_status_transition",
                            "detail": (
                                f"No se puede pasar de '{existing['estado']}' a 'enviada'"
                            ),
                            "status": 409,
                        },
                    )
                await self._archive.insert_expediente_evento(
                    existing["expediente_id"],
                    numero_expediente=expediente_code,
                    accion="transferencia_enviada",
                    actor=actor.id,
                    detalles={"transferencia_id": str(transferencia_id), "tipo": existing["tipo"]},
                )
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action="archive.transferencia_enviada",
                    object_type="transferencia",
                    object_ref=str(transferencia_id),
                    actor=actor.audit_actor,
                    payload={
                        "tipo": existing["tipo"],
                        "expediente_id": str(existing["expediente_id"]),
                        "expediente_code": expediente_code,
                        "estado_anterior": "preparada",
                        "estado_nuevo": "enviada",
                        "ubicacion_origen_id": str(existing["ubicacion_origen_id"])
                        if existing["ubicacion_origen_id"]
                        else None,
                        "ubicacion_destino_id": str(existing["ubicacion_destino_id"])
                        if existing["ubicacion_destino_id"]
                        else None,
                    },
                    canal=audit.CANAL_PETICION,
                )
        except HTTPException as exc:
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action="archive.transferencia_enviada_failed",
                    object_type="transferencia",
                    object_ref=str(transferencia_id),
                    actor=actor.audit_actor,
                    payload={
                        "estado_actual": existing["estado"] if existing is not None else None,
                        "motivo": motivo_de(exc),
                    },
                    canal=audit.CANAL_PETICION,
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                logger.warning(
                    "audit.append(archive.transferencia_enviada_failed) failed", exc_info=True
                )
            raise
        return _resp(row)

    async def recibir(self, transferencia_id: uuid.UUID, actor: Actor) -> TransferenciaResponse:
        """Recibe la transferencia (enviada → recibida) y congela el expediente
        (`transferred`, ya no admite documentos): cambio de custodia
        IRREVERSIBLE.

        `actor` (H-D): identidad unívoca de quien recibe -- `Actor.id` puebla
        `expediente_eventos.actor` (antes NULL, H-E) y `Actor.audit_actor`
        puebla `transferencias.decidida_por`/`audit_log.actor` (antes podía
        ser un username, H-D).

        H-I: también congela el ACTA DE ENTREGA (FUID, Ac. AGN 001/2024 Anexo
        FUID) en `transferencia_acta`, dentro de esta MISMA tx -- ver
        `TransferenciaService.acta` para su lectura (no-read-up)."""
        existing: asyncpg.Record | None = None
        # Detalle por-documento de una fixity fallida: se captura aquí para el
        # asiento forense indeleble (`*_failed`), NUNCA para la respuesta HTTP
        # (ver el 409 `fixity_mismatch` más abajo) — la recepción es custodia
        # ortogonal al clearance de lectura, así que enumerar los radicados
        # alterados de un expediente reservado en el cuerpo del error filtraría
        # contenido a quien no puede leerlo.
        fixity_fallidos: list[str] = []
        # Solo conteos (sin UUID de radicados, que pueden ser reservados) de la fixity
        # fallida, para el evento PREMIS `fallo` post-rollback (AREA-07-05 / Ac. AGN 003/2015).
        fixity_fallo: dict | None = None
        try:
            existing = await self._require(transferencia_id, "recibida")
            # El índice electrónico firmado es el instrumento que da fe de
            # integridad del expediente para su transferencia (Acuerdo AGN
            # 001/2024 art. 4.3.2.4) — recibir la transferencia congela el
            # expediente (`transferred`), así que exige el mismo requisito que
            # `ArchiveService.transfer_expediente` (E06 Inc.1).
            current_indice = await self._index_repo.get_current(existing["expediente_id"])
            if current_indice is None or current_indice["estado"] != "firmado":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "indice_no_firmado",
                        "detail": (
                            "El expediente no puede transferirse sin índice electrónico "
                            "firmado (Acuerdo AGN 001/2024 art. 4.3.2.4)"
                        ),
                        "status": 409,
                    },
                )
            # Atomicidad (H1/H-A): recibir() congela el expediente (`transferred`,
            # ya no admite documentos) — el UPDATE de la transferencia (con guarda
            # H-A), la transición del expediente (con guarda H3), su evento en la
            # hoja de ruta y los DOS asientos de auditoría (transferencia +
            # expediente: el acto muta DOS objetos) son UN ÚNICO acto de custodia
            # dentro de una sola `conn.transaction()`, mismo patrón que
            # `ArchiveService.transfer_expediente`.
            #
            # Granularidad de la tx: UNA por recepción, no una-por-ítem al estilo
            # `batch_service`. `transferencias.expediente_id` es una columna simple
            # (1 transferencia = 1 expediente, ver `TransferenciaRepository._COLS`) —
            # no hay aquí una colección de ítems independientes que aislar entre sí
            # (el patrón de `batch_service` existe para que el fallo de UN ítem no
            # arrastre a los demás de una LISTA). Los UPDATE de este método
            # pertenecen al MISMO acto de custodia sobre el MISMO expediente: si
            # alguno fallara, dejar comprometido solo el anterior sería el estado
            # incoherente que la atomicidad busca evitar.
            async with self._conn.transaction():
                # H-G (Alta, Ac. AGN 001/2024 Tít. 4.4; Ley 594/2000 art. 16;
                # ISO 16363): `estado="firmado"` (gate de arriba) atesta que
                # el índice se selló AL CIERRE -- NO re-verifica que el acervo
                # siga intacto entre el cierre y esta recepción. La fixity
                # comparte el snapshot de la tx con la congelación para que la
                # integridad atestada sea la del MOMENTO del cambio de
                # custodia, no una comprobación previa desacoplada.
                # `CLEARANCE_SIN_RESTRICCION`: la recepción es un acto de
                # custodia ortogonal al clearance de LECTURA (mismo criterio
                # que `create`/`_expediente_code`, `core.clearance` bloque
                # (c)) -- un archivista de depósito que recibe legítimamente
                # un expediente reservado que no puede LEER no debe recibir
                # un 404 espurio.
                fx = await self._index_service.fixity(
                    existing["expediente_id"],
                    version=None,
                    max_clearance=CLEARANCE_SIN_RESTRICCION,
                )
                # AREA-07-04: la fila de `expediente_indice` que la fixity acaba de
                # comparar (misma `version`), para atar al acta SU firma. Si el índice
                # vigente cambió desde el pre-flight de arriba (otra versión), se lee
                # la que usó la fixity: el acta debe señalar el sello de LO QUE SE
                # VERIFICÓ, no el de lo que había antes. Una versión que no está
                # firmada no se entrega (409), igual que en el pre-flight.
                if current_indice.get("version") == fx.version:
                    fila_indice = current_indice
                else:
                    fila_indice = await self._index_repo.get_version(
                        existing["expediente_id"], fx.version
                    )
                if (
                    fila_indice is None
                    or fila_indice["estado"] != "firmado"
                    or fila_indice.get("firma_id") is None
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error": "indice_no_firmado",
                            "detail": (
                                "El expediente no puede transferirse sin índice electrónico "
                                "firmado (Acuerdo AGN 001/2024 art. 4.3.2.4)"
                            ),
                            "status": 409,
                        },
                    )
                if not (fx.documentos_ok and fx.huella_indice_ok):
                    # El detalle por-documento (UUIDs) va al asiento forense
                    # indeleble, no a la respuesta: al actor de custodia (que
                    # puede no tener clearance de LECTURA del expediente) solo
                    # se le devuelve un CONTEO opaco, sin enumerar qué radicados
                    # del expediente reservado se alteraron.
                    # Solo cuentan las huellas DECLARADAS que discrepan. Un
                    # documento sin huella —radicado de metadatos, sin anexos—
                    # no tiene integridad que verificar, y contarlo aquí hacía
                    # intransferible cualquier expediente de ventanilla.
                    fixity_fallidos = [
                        str(d.document_id)
                        for d in fx.documentos
                        if not d.coincide and not d.sin_huella_declarada
                    ]
                    fixity_fallo = {
                        "origen": "recepcion_transferencia",
                        "indice_version": fx.version,
                        "huella_indice_ok": fx.huella_indice_ok,
                        "num_documentos": len(fx.documentos),
                        "num_documentos_fallidos": len(fixity_fallidos),
                        "documentos_sin_huella": fx.documentos_sin_huella,
                        "algoritmo": "SHA-256",
                    }
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error": "fixity_mismatch",
                            "detail": (
                                "La verificación de fixity del acervo falló en la "
                                "recepción (Ley 594/2000 art. 16; ISO 16363)"
                            ),
                            "num_documentos_fallidos": len(fixity_fallidos),
                            "huella_indice_ok": fx.huella_indice_ok,
                            "status": 409,
                        },
                    )
                row = await self._repo.update_estado(
                    transferencia_id,
                    expected_estado="enviada",
                    estado="recibida",
                    assignments={
                        "recibida_at": datetime.now(tz=UTC),
                        "decidida_por": actor.audit_actor,
                    },
                )
                # H-A, fail-closed DENTRO de la tx: caso letal cerrado -- si un
                # `rechazar()` concurrente commiteó primero, este `WHERE` no casa
                # ('enviada' ya cambió a 'rechazada') y AQUÍ se revierte, sin
                # llegar a congelar el expediente.
                if row is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error": "invalid_status_transition",
                            "detail": (
                                f"No se puede pasar de '{existing['estado']}' a 'recibida'"
                            ),
                            "status": 409,
                        },
                    )
                # Congelación: el expediente pasa a transferred (no admite documentos).
                # `expected_status="closed"` (H3): cierra el `WHERE` de la transición
                # contra la misma carrera que `ArchiveService.transfer_expediente`.
                transitioned = await self._archive.transition_expediente(
                    existing["expediente_id"], status="transferred", expected_status="closed"
                )
                if transitioned is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error": "expediente_transition_failed",
                            "detail": (
                                "La transición de transferencia no devolvió el "
                                "expediente actualizado."
                            ),
                            "status": 409,
                        },
                    )
                # H-E: `actor=actor.id` puebla la columna UUID (antes NULL en
                # TODAS las rutas de este evento); "transferido" es el hecho
                # jurídico sobre el EXPEDIENTE -- la transferencia que lo origina
                # queda referenciada en `detalles.transferencia_id`, no repetida
                # como un segundo evento. Vocabulario (H-E'): quien RECIBE no
                # "firma" -- se retira `comentario=f"firmante={...}"`, la
                # identidad va en la columna tipada `actor`, no en texto libre.
                await self._archive.insert_expediente_evento(
                    existing["expediente_id"],
                    numero_expediente=transitioned.code,
                    accion="transferido",
                    actor=actor.id,
                    detalles={
                        "transferencia_id": str(transferencia_id),
                        "tipo": existing["tipo"],
                        "ubicacion_destino_id": str(existing["ubicacion_destino_id"])
                        if existing["ubicacion_destino_id"]
                        else None,
                        "status_anterior": "closed",
                    },
                )
                # H-I (Ac. AGN 001/2024 Anexo FUID; Ley 594/2000 arts. 15/26):
                # el FUID es el INSTRUMENTO FORMAL de la entrega -- se congela
                # AQUÍ, dentro de la MISMA tx que la fixity y la congelación
                # del expediente, para que el acta atestigüe lo efectivamente
                # entregado/recibido en ESTE instante, no un estado posterior
                # regenerado al vuelo (antes `TransferenciaService.fuid` lo
                # generaba con `generado_ts=now()` sobre el estado ACTUAL,
                # perdiendo su valor de acta tan pronto el expediente se
                # tocara de nuevo). `max_clearance=CLEARANCE_SIN_RESTRICCION`
                # (custodia, ortogonal a lectura -- mismo criterio que `fixity`
                # arriba): un archivista de depósito que recibe legítimamente
                # un expediente reservado que no puede LEER debe poder
                # producir el acta del traspaso.
                # Cabecera del Anexo FUID (migración 052): los tres campos con
                # los que el formato abre. Salen del SNAPSHOT tomado al
                # preparar, no de quien recibe: el Anexo pide la oficina que
                # PRODUJO los documentos. Antes iban los tres en blanco y el
                # acta se congelaba, se le calculaba el SHA-256 y se sellaba en
                # XAdES-T igual — un sello exacto sobre un documento que no
                # identificaba la entrega.
                fuid = await FisicoService(self._conn, self._tenant).generate_fuid(
                    ubicacion_id=None,
                    trd_serie_id=None,
                    expediente_id=existing["expediente_id"],
                    max_clearance=CLEARANCE_SIN_RESTRICCION,
                    unidad_administrativa=existing["unidad_administrativa_nombre"],
                    oficina_productora=existing["oficina_productora_nombre"],
                    objeto=existing["objeto"],
                )
                # AREA-07-04: la firma del índice entregado va DENTRO del XML que se
                # hashea y se sella, no solo en una columna: el SHA anclado en
                # `audit_log` y el XAdES del acta la cubren.
                acta_xml = _fuid_to_xml(
                    fuid,
                    indice_version=fx.version,
                    indice_firma_id=fila_indice["firma_id"],
                    indice_sha256=fila_indice.get("xml_sha256"),
                )
                acta_sha = hashlib.sha256(acta_xml.encode("utf-8")).hexdigest()
                await self._repo.insert_acta(
                    {
                        "transferencia_id": transferencia_id,
                        "acta_fuid_xml": acta_xml,
                        "acta_fuid_sha256": acta_sha,
                        # Tres responsables del Anexo FUID: snapshot de las
                        # columnas de identidad YA fijadas de `transferencias`
                        # (Elaborado=`creada_por`, Entregado=`enviada_por`,
                        # Recibido=`row["decidida_por"]` -- el UPDATE de
                        # arriba, no `actor.audit_actor` directo, para que el
                        # acta lea SIEMPRE de la fila persistida).
                        "elaborado_por": existing["creada_por"],
                        "entregado_por": existing["enviada_por"],
                        "recibido_por": row["decidida_por"],
                        "elaborado_at": existing["created_at"],
                        "entregado_at": existing["enviada_at"],
                        "recibido_at": row["recibida_at"],
                        # H-H (parcial, gratis): `fx.version` ya lo resolvió
                        # `fixity()` DENTRO de esta misma tx (mismo `row` de
                        # `expediente_indice` cuya huella se comparó) -- sin
                        # consulta extra ni TOCTOU frente a la fixity congelada.
                        "indice_version": fx.version,
                        # AREA-07-04 (migración 053): el sello y la huella de esa versión.
                        "indice_firma_id": fila_indice["firma_id"],
                        "indice_sha256": fila_indice.get("xml_sha256"),
                        # = el `generado` embebido en `acta_xml` (mismo
                        # instante sobre el que se calculó `acta_sha`) -- ya
                        # NO se regenera al vuelo con `now()`.
                        "congelada_at": fuid.generado_ts,
                    }
                )
                # H-H (E06/F4): fila de bookkeeping del sellado del acta,
                # DENTRO de la MISMA tx que `insert_acta` -- escritura LOCAL
                # de BD (sin HTTP, preserva la atomicidad H-A/H-I). Garantiza
                # que TODA acta recibida tenga su fila `transferencia_acta_firma`
                # aunque el proceso muera entre este commit y el intento de
                # sellado post-commit (`_sellar_acta_post_commit`, más abajo,
                # FUERA de la tx) -- el job de reconciliación converge igual.
                # AREA-07-05: el detalle de la fixity de esta recepción queda en la fila de
                # bookkeeping para poder registrar (y reintentar) su evento PREMIS.
                fixity_detalle = {
                    "origen": "recepcion_transferencia",
                    "transferencia_id": str(transferencia_id),
                    "indice_version": fx.version,
                    "documentos_ok": fx.documentos_ok,
                    "huella_indice_ok": fx.huella_indice_ok,
                    "num_documentos": len(fx.documentos),
                    "documentos_sin_huella": fx.documentos_sin_huella,
                    "documentos_verificados": len(fx.documentos) - fx.documentos_sin_huella,
                    # Una verificación con documentos sin huella NO cubre todo el acervo:
                    # el evento PREMIS lo dice, no lo deja leer como fixity total.
                    "verificacion": "parcial" if fx.documentos_sin_huella > 0 else "completa",
                    "algoritmo": "SHA-256",
                }
                await self._repo.insert_acta_firma_pendiente(
                    transferencia_id, fixity_premis_detalle=fixity_detalle
                )
                # `audit.append` LO ÚLTIMO dentro de la tx -- DOS asientos porque
                # el acto muta DOS objetos (transferencia + expediente); dejar
                # solo uno incompletaría la serie de auditoría del `object_type`
                # que faltara.
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action="archive.transferencia_recibida",
                    object_type="transferencia",
                    object_ref=str(transferencia_id),
                    actor=actor.audit_actor,
                    payload={
                        "tipo": existing["tipo"],
                        "expediente_id": str(existing["expediente_id"]),
                        "expediente_code": transitioned.code,
                        "estado_anterior": "enviada",
                        "estado_nuevo": "recibida",
                        "ubicacion_origen_id": str(existing["ubicacion_origen_id"])
                        if existing["ubicacion_origen_id"]
                        else None,
                        "ubicacion_destino_id": str(existing["ubicacion_destino_id"])
                        if existing["ubicacion_destino_id"]
                        else None,
                        # H-G: la atestación del fixity-check de recepción queda
                        # capturada en `audit_log` (base PREMIS -- el objeto/evento
                        # `fixity-check` dedicado queda diferido a E10, ya en
                        # roadmap).
                        "fixity": {
                            "documentos_ok": fx.documentos_ok,
                            "huella_indice_ok": fx.huella_indice_ok,
                            "num_documentos": len(fx.documentos),
                            # De cuántos documentos NO se está dando fe de
                            # integridad, porque no tienen huella que comparar
                            # (registros de metadatos, sin contenido binario).
                            # Va en el asiento porque una atestación que no
                            # dice de qué NO responde no es una atestación:
                            # `documentos_ok` sin este número se leería como
                            # «se verificaron los N documentos», y no es cierto.
                            "documentos_sin_huella": fx.documentos_sin_huella,
                            "documentos_verificados": len(fx.documentos)
                            - fx.documentos_sin_huella,
                            "algoritmo": "SHA-256",
                        },
                        # Ancla de la fixity del ACTA en la cadena de hash
                        # INDELEBLE de `audit_log`: `transferencia_acta` es
                        # append-only forzado por el motor (migración 023), pero
                        # -- a diferencia de `audit_log` -- no tiene hash-chain
                        # propia y su `acta_fuid_sha256` vive en la MISMA fila que
                        # protege. Anclarlo aquí hace que una manipulación del
                        # acta que saltara el trigger (sólo un superusuario:
                        # `DISABLE TRIGGER`/`session_replication_role=replica`,
                        # mismo caveat que `audit_log`) sea DETECTABLE: el SHA
                        # encadenado no se puede reescribir sin romper la cadena.
                        "acta_fuid_sha256": acta_sha,
                    },
                    canal=audit.CANAL_PETICION,
                )
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action="archive.expediente_transferred",
                    object_type="expediente",
                    object_ref=transitioned.code,
                    actor=actor.audit_actor,
                    canal=audit.CANAL_PETICION,
                )
        except HTTPException as exc:
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action="archive.transferencia_recibida_failed",
                    object_type="transferencia",
                    object_ref=str(transferencia_id),
                    actor=actor.audit_actor,
                    payload={
                        "estado_actual": existing["estado"] if existing is not None else None,
                        "motivo": motivo_de(exc),
                        # Traza forense completa de una fixity fallida (los UUIDs
                        # que NO se exponen en el 409) — solo en el registro
                        # indeleble, cuya lectura exige permiso de auditoría.
                        **(
                            {"documentos_fallidos": fixity_fallidos}
                            if fixity_fallidos
                            else {}
                        ),
                    },
                    canal=audit.CANAL_PETICION,
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                logger.warning(
                    "audit.append(archive.transferencia_recibida_failed) failed", exc_info=True
                )
            if fixity_fallo is not None and existing is not None:
                await self._registrar_fixity_fallo_premis(
                    transferencia_id,
                    expediente_id=existing["expediente_id"],
                    detalle=fixity_fallo,
                    agente=actor.audit_actor,
                )
            raise
        # H-H (E06/F4): sellado SÍNCRONO ACOTADO del acta, SIEMPRE post-commit
        # (fuera de la tx de arriba, mismo patrón que
        # `IndexService.firmar_indice_close`) -- best-effort, atribuido al
        # RECEPTOR (`row["decidida_por"]`, ya congelado en el acta). Nunca
        # bloquea `recibir()`: el acta ya quedó íntegra y su SHA anclado en
        # `audit_log` dentro de la tx que acaba de comprometerse.
        await self._sellar_acta_post_commit(
            transferencia_id, acta_xml=acta_xml, indice_version=fx.version, actor=actor
        )
        # AREA-07-05: el fixity-check de la recepción como evento PREMIS, también
        # post-commit y best-effort (la reconciliación lo repite si falla).
        await self.registrar_fixity_premis(transferencia_id, origen="close")
        return _resp(row)

    async def _registrar_fixity_fallo_premis(
        self,
        transferencia_id: uuid.UUID,
        *,
        expediente_id: uuid.UUID,
        detalle: dict,
        agente: str | None,
    ) -> None:
        """Evento PREMIS `FIJACION` con `resultado="fallo"` de una recepción cuya fixity
        discrepó. Se llama FUERA de la tx ya revertida; best-effort (NUNCA lanza): el 409 y el
        asiento `*_failed` ya quedaron. Cada intento fallido es un evento propio (el
        `dedup_key` lleva un sello temporal), porque la recepción puede reintentarse."""
        from app.clients import storage_client

        intento = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%f")
        try:
            await storage_client.registrar_evento_internal(
                tenant_slug=self._tenant,
                object_ref=str(expediente_id),
                tipo="FIJACION",
                resultado="fallo",
                detalle={**detalle, "evento": "fixity check", "disparo": "recibir"},
                agente=agente,
                dedup_key=f"fixity-recepcion-fallo:{transferencia_id}:{intento}",
            )
        except Exception:  # noqa: BLE001 — best-effort: jamás degrada el 409.
            logger.warning(
                "No se pudo registrar el evento PREMIS de fixity fallida (transferencia_id=%s)",
                transferencia_id,
                exc_info=True,
            )

    async def registrar_fixity_premis(
        self, transferencia_id: uuid.UUID, *, origen: str
    ) -> Literal["registrado", "pendiente", "agotado", "omitido"]:
        """Registra en storage-service el evento PREMIS `FIJACION` de la fixity de la recepción
        (AREA-07-05), para que aparezca en `GET /expedientes/{id}/preservacion/eventos`.

        NUNCA lanza ni toca el resultado de la recepción: el acta ya es íntegra y su asiento
        anclado. Idempotente: el `dedup_key` es la transferencia, así un reintento de la
        reconciliación no duplica el evento. `omitido` = no hay detalle guardado (recepción
        anterior a la migración 056) o ya estaba registrado. Un fallo reprograma con backoff
        (mismo patrón que el WORM del acta) y, agotado, deja asiento."""
        from app.clients import storage_client

        try:
            fila = await self._repo.get_fixity_premis(transferencia_id)
            if fila is None or fila["fixity_premis_detalle"] is None or fila["fixity_premis_at"]:
                return "omitido"
            detalle = fila["fixity_premis_detalle"]
            if isinstance(detalle, str):
                import json

                detalle = json.loads(detalle)
            ok = await storage_client.registrar_evento_internal(
                tenant_slug=self._tenant,
                object_ref=str(fila["expediente_id"]),
                tipo="FIJACION",
                resultado="ok",
                detalle={**detalle, "evento": "fixity check", "disparo": origen},
                agente=fila["decidida_por"],
                dedup_key=f"fixity-recepcion:{transferencia_id}",
            )
            if ok:
                await self._repo.mark_fixity_premis_registrado(transferencia_id)
                return "registrado"
            intentos = int(fila["fixity_premis_intentos"]) + 1
            agotado = intentos >= settings.indice_reconciliation_max_intentos
            proximo_at = (
                None
                if agotado
                else datetime.now(tz=UTC)
                + timedelta(
                    seconds=next_backoff_seconds(
                        intentos,
                        base=settings.indice_reconciliation_backoff_base_seconds,
                        cap=settings.indice_reconciliation_backoff_cap_seconds,
                    )
                )
            )
            if agotado:
                async with self._conn.transaction():
                    await self._repo.bump_fixity_premis_intento(
                        transferencia_id,
                        proximo_at=None,
                        ultimo_error="storage_evento_no_registrado",
                        agotado=True,
                    )
                    await audit.append(
                        self._conn,
                        tenant_slug=self._tenant,
                        service=_SERVICE,
                        action="archive.fixity_premis_agotado",
                        object_type="transferencia",
                        object_ref=str(transferencia_id),
                        actor=str(SYSTEM_RECONCILER_ID),
                        canal="system",
                        payload={"intentos": intentos, "motivo": "storage_evento_no_registrado"},
                    )
                return "agotado"
            await self._repo.bump_fixity_premis_intento(
                transferencia_id,
                proximo_at=proximo_at,
                ultimo_error="storage_evento_no_registrado",
                agotado=False,
            )
            return "pendiente"
        except Exception:  # noqa: BLE001 — best-effort: jamás degrada la recepción.
            logger.warning(
                "No se pudo registrar el evento PREMIS de la fixity (transferencia_id=%s)",
                transferencia_id,
                exc_info=True,
            )
            return "pendiente"

    async def _sellar_acta_post_commit(
        self,
        transferencia_id: uuid.UUID,
        *,
        acta_xml: str,
        indice_version: int,
        actor: Actor,
    ) -> None:
        """Intento SÍNCRONO ACOTADO del sellado XAdES del acta (H-H, E06/F4)
        -- gemelo de `IndexService.firmar_indice_close`, pero atribuido al
        humano que RECIBE la transferencia (no al que cierra el expediente).
        `objeto_id=transferencia_id`, `version=indice_version` (la versión
        del índice PINNEADA dentro de la tx de recepción -- el acta no tiene
        versión propia), `indice_id=None`.

        SIEMPRE llamado FUERA de cualquier `conn.transaction()` de
        `recibir()` -- el sellado es HTTP y nunca debe vivir dentro de una tx
        (preserva H-A/H-I). `actor_id` nunca está ausente aquí: `require_actor`
        ya lo exigió para alcanzar `recibir()` (a diferencia del cierre de
        expediente, que sí puede llegar sin `actor_id` -- ver
        `IndexService._current_signable_or_none`).

        Desenlaces (idénticos a `firmar_indice_close`):
        - éxito → `transferencia_acta_firma` pasa a 'firmado', asiento
          `archive.acta_firmada` (`origen='manual'`).
        - `seal_absent` → degrada de inmediato, SIN reintentar (firmar es
          estructuralmente imposible ahora mismo).
        - `transient` tras agotar los intentos síncronos → degrada.
        Ambas degradaciones convergen vía el job de reconciliación
        (segunda fase de `app/jobs/indice_reconciliation.py`) o el reintento
        manual `POST /transferencias/{id}/acta/firmar`.

        El XML del acta (`acta_xml`) NUNCA se altera aquí -- el sello cubre
        exactamente el `acta_fuid_xml` ya anclado por `recibir()`; el XML
        FIRMADO es un artefacto DERIVADO en MinIO (enveloped, la referencia
        del `<ds:Signature>` resuelve al mismo contenido).
        """
        from app.clients import signature_client

        max_attempts = max(1, settings.acta_sign_max_sync_attempts)
        for attempt in range(1, max_attempts + 1):
            outcome = await signature_client.sign_xml_outcome(
                tenant_slug=self._tenant,
                actor_id=actor.audit_actor,
                payload_xml=acta_xml,
                objeto_tipo="acta_transferencia",
                objeto_id=str(transferencia_id),
                indice_id=None,
                version=indice_version,
            )
            if outcome.signed is not None:
                async with self._conn.transaction():
                    affected = await self._repo.mark_acta_firmada(
                        transferencia_id,
                        firma_id=outcome.signed.firma_id,
                        minio_key=outcome.signed.minio_key_firmado,
                        xades_level=outcome.signed.xades_level,
                        acreditado=outcome.signed.acreditado,
                    )
                    # S1 (RF-FIR-14, gemelo de `firmar_indice_close`):
                    # `affected=0` -> otro proceso (típicamente el job de
                    # reconciliación) ya marcó 'firmado' esta misma fila entre
                    # la resolución de `acta_xml` y este UPDATE -- el sello
                    # recién obtenido queda huérfano; NO se escribe el asiento
                    # fantasma.
                    if affected:
                        await audit.append(
                            self._conn,
                            tenant_slug=self._tenant,
                            service=_SERVICE,
                            action="archive.acta_firmada",
                            object_type="transferencia_acta",
                            object_ref=str(transferencia_id),
                            actor=actor.audit_actor,
                            payload={
                                "transferencia_id": str(transferencia_id),
                                "firma_id": str(outcome.signed.firma_id),
                                "origen": "manual",
                            },
                            canal=audit.CANAL_PETICION,
                        )
                if affected:
                    # E10 Inc.2 wiring: cableado WORM del acta recién firmada,
                    # FUERA de la tx que acaba de comprometerse arriba (mismo
                    # motivo que el propio sellado corre fuera de la tx de
                    # recepción/cierre -- ver `IndexService.
                    # firmar_indice_close`, cuyo cableado del índice sigue
                    # exactamente este mismo patrón). Cubre AMBOS llamadores de
                    # este método (`recibir()` y `firmar_acta_retry()`), ambos
                    # con un actor humano real. Si `affected` es 0 (carrera
                    # perdida, ver arriba), NO se dispara aquí:
                    # `run_once_actas_worm` (4ª fase de reconciliación)
                    # converge de todos modos.
                    await self.proteger_worm_acta(
                        transferencia_id, actor_id=actor.audit_actor, origen="close"
                    )
                return
            if outcome.seal_absent:
                await self._marcar_acta_pendiente_audited(
                    transferencia_id, actor_id=actor.audit_actor, motivo="sello_ausente"
                )
                return
            # `transient`: reintenta solo si quedan intentos -- sin sleep tras
            # el ÚLTIMO intento.
            if attempt < max_attempts:
                await asyncio.sleep(settings.acta_sign_sync_delay_seconds)

        await self._marcar_acta_pendiente_audited(
            transferencia_id, actor_id=actor.audit_actor, motivo="sellado_xades_no_obtenido"
        )

    async def _marcar_acta_pendiente_audited(
        self, transferencia_id: uuid.UUID, *, actor_id: str | None, motivo: str
    ) -> None:
        """Marca 'pendiente_firma' (re-armando el backoff) y su asiento de
        auditoría como un ÚNICO acto (H5, gemela de
        `IndexService._mark_pendiente_audited`)."""
        async with self._conn.transaction():
            await self._repo.mark_acta_firma_pendiente(transferencia_id, last_error=motivo)
            await audit.append(
                self._conn,
                tenant_slug=self._tenant,
                service=_SERVICE,
                action="archive.acta_firma_pendiente",
                object_type="transferencia_acta",
                object_ref=str(transferencia_id),
                actor=actor_id,
                payload={"transferencia_id": str(transferencia_id), "motivo": motivo},
                canal=audit.CANAL_PETICION,
            )

    async def rechazar(
        self,
        transferencia_id: uuid.UUID,
        payload: TransferenciaRechazo,
        actor: Actor,
    ) -> TransferenciaResponse:
        """Rechaza la transferencia (enviada → rechazada), estado TERMINAL:
        decisión de fondo del archivo destino sobre el acervo, no un fallo
        técnico -- devuelve la responsabilidad de custodia al remitente y deja
        el motivo como insumo de la subsanación (spec E12 §8 R5). Antes NO
        dejaba NINGÚN asiento (H-B) -- el único acto de custodia de todo el
        ciclo que quedaba invisible.

        H-K (motivo obligatorio y NO destructivo): `payload` es
        `TransferenciaRechazo` (`motivo` obligatorio, validado por Pydantic --
        antes `TransferenciaDecision.observacion` admitía `None`, un rechazo
        INMOTIVADO). El motivo va a la columna propia `motivo_rechazo`; este
        método YA NO toca `observacion` (antes `payload.observacion or
        existing["observacion"]` pisaba el dato de PREPARACIÓN con el motivo
        del rechazo, una mutación destructiva de un dato de registro).
        H-J: `rechazada_at` sella el acto (gemelo de `enviada_at`/
        `recibida_at`, migración 021)."""
        existing: asyncpg.Record | None = None
        try:
            existing = await self._require(transferencia_id, "rechazada")
            expediente_code = await self._expediente_code(existing["expediente_id"])
            async with self._conn.transaction():
                row = await self._repo.update_estado(
                    transferencia_id,
                    expected_estado="enviada",
                    estado="rechazada",
                    assignments={
                        "decidida_por": actor.audit_actor,
                        "motivo_rechazo": payload.motivo,
                        "rechazada_at": datetime.now(tz=UTC),
                    },
                )
                # H-A, fail-closed DENTRO de la tx: mismo razonamiento que
                # `enviar`/`recibir` -- un `recibir()` concurrente que ya
                # commiteó (transferencia 'recibida') hace que este `WHERE` no
                # case ('enviada' ya cambió), evitando el estado imposible
                # "rechazada" + expediente ya congelado.
                if row is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error": "invalid_status_transition",
                            "detail": (
                                f"No se puede pasar de '{existing['estado']}' a 'rechazada'"
                            ),
                            "status": 409,
                        },
                    )
                await self._archive.insert_expediente_evento(
                    existing["expediente_id"],
                    numero_expediente=expediente_code,
                    accion="transferencia_rechazada",
                    actor=actor.id,
                    comentario=payload.motivo,
                    detalles={
                        "transferencia_id": str(transferencia_id),
                        "tipo": existing["tipo"],
                        "motivo": payload.motivo,
                    },
                )
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action="archive.transferencia_rechazada",
                    object_type="transferencia",
                    object_ref=str(transferencia_id),
                    actor=actor.audit_actor,
                    payload={
                        "tipo": existing["tipo"],
                        "expediente_id": str(existing["expediente_id"]),
                        "expediente_code": expediente_code,
                        "estado_anterior": "enviada",
                        "estado_nuevo": "rechazada",
                        "motivo": payload.motivo,
                    },
                    canal=audit.CANAL_PETICION,
                )
        except HTTPException as exc:
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action="archive.transferencia_rechazada_failed",
                    object_type="transferencia",
                    object_ref=str(transferencia_id),
                    actor=actor.audit_actor,
                    payload={
                        "estado_actual": existing["estado"] if existing is not None else None,
                        "motivo": motivo_de(exc),
                    },
                    canal=audit.CANAL_PETICION,
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                logger.warning(
                    "audit.append(archive.transferencia_rechazada_failed) failed", exc_info=True
                )
            raise
        return _resp(row)

    async def list(self, *, estado: str | None, tipo: str | None) -> list[TransferenciaResponse]:
        rows = await self._repo.list(estado=estado, tipo=tipo)
        return [_resp(r) for r in rows]

    async def get(self, transferencia_id: uuid.UUID) -> TransferenciaResponse:
        row = await self._repo.get(transferencia_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "transferencia_not_found",
                    "detail": str(transferencia_id),
                    "status": 404,
                },
            )
        return _resp(row)

    async def fuid(
        self, transferencia_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> FuidResponse:
        row = await self._repo.get(transferencia_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "transferencia_not_found",
                    "detail": str(transferencia_id),
                    "status": 404,
                },
            )
        # No-read-up (RF-SEG-08): el FUID es descripción de CONTENIDO del
        # expediente (asunto/serie/signatura, Ley 1712/2014 arts. 18-19; Ley
        # 594/2000 art. 27) — a diferencia de la transferencia en sí (acto de
        # custodia, ortogonal, ver `core.clearance.CLEARANCE_SIN_RESTRICCION`
        # bloque (c)). 404 total, indistinguible de un expediente inexistente:
        # no revela ni existencia ni estado a quien no puede leerlo.
        caller_clearance = await self._archive.caller_clearance(user_id)
        exp = await self._archive.get_expediente_by_id(
            row["expediente_id"], max_clearance=caller_clearance
        )
        if exp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": "No existe el expediente solicitado",
                    "status": 404,
                },
            )
        # Misma cabecera que congelará el acta (migración 052): la vista previa
        # del FUID y el acta definitiva tienen que decir lo mismo, o la previa
        # no sirve para revisar antes de recibir.
        return await FisicoService(self._conn, self._tenant).generate_fuid(
            ubicacion_id=None,
            trd_serie_id=None,
            expediente_id=row["expediente_id"],
            user_id=user_id,
            unidad_administrativa=row["unidad_administrativa_nombre"],
            oficina_productora=row["oficina_productora_nombre"],
            objeto=row["objeto"],
        )

    async def acta(
        self,
        transferencia_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        *,
        verify: bool = False,
    ) -> TransferenciaActaResponse:
        """Devuelve el ACTA DE ENTREGA ya CONGELADA (E12 H-I) -- NUNCA la
        regenera; a diferencia de `fuid()` (que sí genera al vuelo sobre el
        estado ACTUAL), esta lectura sirve el snapshot escrito por `recibir()`
        dentro de su tx.

        No-read-up (RF-SEG-08, mismo patrón que `fuid()`): el acta expone
        asunto/serie/signatura del expediente (Ley 1712/2014 arts. 18-19; Ley
        594/2000 art. 27) -- CONTENIDO, no metadato de custodia -- así que la
        LECTURA aplica el clearance REAL del llamante. Orden fail-closed
        deliberado: primero el no-read-up sobre el EXPEDIENTE (404 total,
        indistinguible de inexistente, a quien no puede leerlo), y solo
        DESPUÉS la existencia del acta -- así un no-lector nunca distingue
        "expediente inexistente" de "expediente reservado sin acta todavía",
        que sí sería un oráculo nuevo sobre el ciclo de vida."""
        row = await self._repo.get(transferencia_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "transferencia_not_found",
                    "detail": str(transferencia_id),
                    "status": 404,
                },
            )
        caller_clearance = await self._archive.caller_clearance(user_id)
        exp = await self._archive.get_expediente_by_id(
            row["expediente_id"], max_clearance=caller_clearance
        )
        if exp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": "No existe el expediente solicitado",
                    "status": 404,
                },
            )
        acta = await self._repo.get_acta(transferencia_id)
        if acta is None:
            # Un lector legítimo YA sabe por `GET /{id}` que la transferencia
            # no está 'recibida' (o fue 'rechazada', que nunca produce acta)
            # -- este 404 no abre un oráculo nuevo para quien sí puede leer.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "acta_not_found",
                    "detail": (
                        "La transferencia aún no tiene acta de entrega "
                        "(se congela al recibirla)"
                    ),
                    "status": 404,
                },
            )
        firma_row = await self._repo.get_acta_firma(transferencia_id)
        firma_info = await self._build_firma_info(firma_row, verify=verify)
        excepciones = await self._repo.list_acta_firma_excepciones(transferencia_id)
        # El fundamento es dato sensible: NUNCA viaja en el acta (ni a administración nivel 3);
        # se lee a petición por `GET .../{excepcion_id}/fundamento` (con asiento de lectura).
        ver_fundamento = False
        # La referencia de soporte se recorta por el clearance del LECTOR (un lote).
        visibles = await self._referencias_visibles(excepciones, caller_clearance)
        return TransferenciaActaResponse(
            **dict(acta),
            expediente_id=row["expediente_id"],
            firma=firma_info,
            excepciones_firma=[
                self._vista_excepcion(
                    e,
                    ver_fundamento=ver_fundamento,
                    referencia_visible=str(e.get("referencia_radicado_id")) in visibles,
                )
                for e in excepciones
            ],
            cabecera_completa=_cabecera_completa(acta["acta_fuid_xml"]),
        )

    async def _build_firma_info(
        self, firma_row: asyncpg.Record | None, *, verify: bool
    ) -> TransferenciaActaFirmaInfo:
        """Bloque `firma` de `GET /transferencias/{id}/acta` (H-H, E06/F4).

        `presente`/`xades_level`/`acreditado`/`motivo(=last_error)` son
        campos BARATOS, leídos directamente de `transferencia_acta_firma`
        (sin llamada externa). `valida` es `None` salvo `verify=True`: en ese
        caso se descarga el XML YA FIRMADO de MinIO (mismo patrón mediado,
        D-02, que `IndexService.verify`) y se pide a signature-service
        `/verify` -- best-effort, nunca declara conforme lo que no pudo
        verificar (RF-FIR-15)."""
        if firma_row is None or firma_row["estado"] != "firmado":
            return TransferenciaActaFirmaInfo(
                presente=False,
                xades_level=firma_row["xades_level"] if firma_row else None,
                acreditado=bool(firma_row["acreditado"]) if firma_row else False,
                motivo=firma_row["last_error"] if firma_row else None,
            )
        info = TransferenciaActaFirmaInfo(
            presente=True,
            xades_level=firma_row["xades_level"],
            acreditado=bool(firma_row["acreditado"]),
        )
        if not verify:
            return info
        from app.clients import signature_client, storage_client

        signed_xml: str | None = None
        if firma_row["minio_key_firmado"]:
            signed_xml = await storage_client.download_signed_xml(
                tenant_slug=self._tenant,
                minio_key_firmado=firma_row["minio_key_firmado"],
            )
        if signed_xml is None:
            info.valida = False
            info.motivo = "signed_xml_unavailable"
            return info
        resultado = await signature_client.verify(
            tenant_slug=self._tenant,
            firma_id=str(firma_row["firma_id"]),
            payload_xml=signed_xml,
        )
        if resultado is None:
            info.valida = False
            info.motivo = "signature_service_unreachable"
            return info
        info.valida = resultado.valida
        info.motivo = resultado.motivo
        info.advertencias = list(resultado.advertencias)
        return info

    async def firmar_acta_retry(
        self, transferencia_id: uuid.UUID, actor: Actor
    ) -> TransferenciaActaResponse:
        """Reintento MANUAL explícito del sellado del acta (H-H, Parte 5) --
        `POST /transferencias/{id}/acta/firmar`, gemelo de
        `IndexService.firmar_indice_retry`:
        - 404 si la transferencia no existe, o si aún no tiene acta congelada
          (nunca se recibió, o fue rechazada).
        - 409 si el acta ya está sellada (inmutable) -- idempotente, no un
          error de negocio.
        - 503 si el sellado sigue sin poder obtenerse (el acta permanece o
          vuelve a 'pendiente_firma', reintentable de nuevo más tarde).

        RE-ARMA el backoff (`reconcile_agotado` -> False) SIEMPRE antes de
        reintentar -- vía `_sellar_acta_post_commit`/
        `_marcar_acta_pendiente_audited` si degrada de nuevo, mismo criterio
        C1 que el índice: un acta agotada cuyo reintento manual vuelve a
        fallar no debe quedar invisible ni para el job ni para el operador.
        """
        row = await self._repo.get(transferencia_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "transferencia_not_found",
                    "detail": str(transferencia_id),
                    "status": 404,
                },
            )
        acta = await self._repo.get_acta(transferencia_id)
        if acta is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "acta_not_found",
                    "detail": (
                        "La transferencia aún no tiene acta de entrega "
                        "(se congela al recibirla)"
                    ),
                    "status": 404,
                },
            )
        firma_row = await self._repo.get_acta_firma(transferencia_id)
        if firma_row is not None and firma_row["estado"] == "firmado":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "acta_ya_firmada",
                    "detail": "El acta de entrega ya está sellada (inmutable)",
                    "status": 409,
                },
            )
        await self._sellar_acta_post_commit(
            transferencia_id,
            acta_xml=acta["acta_fuid_xml"],
            indice_version=acta["indice_version"] or 0,
            actor=actor,
        )
        firma_row = await self._repo.get_acta_firma(transferencia_id)
        if firma_row is None or firma_row["estado"] != "firmado":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "acta_signing_failed",
                    "detail": (
                        "signature-service no pudo sellar el acta; queda pendiente_firma"
                    ),
                    "status": 503,
                },
            )
        return await self.acta(transferencia_id, actor.id)

    # ------------------------------------------------------------------
    # Firma PERSONAL PKI por-usuario del acta (E17/F4, Incremento 1)
    # ------------------------------------------------------------------

    async def firmar_personal(
        self,
        transferencia_id: uuid.UUID,
        actor: Actor,
        totp_code: str,
        authorization: str | None,
        rol_solicitado: str | None = None,
    ) -> TransferenciaActaFirmaPersonalResponse:
        """`POST /transferencias/{id}/acta/firmar-personal` — firma PERSONAL
        PKI de una PARTE del acta: el REMITENTE (rol `'entrega'`) o el
        RECEPTOR (rol `'recibe'`). El rol se DERIVA de la identidad congelada
        (`enviada_por`/`decidida_por`), nunca lo declara el cliente
        (`rol_solicitado` solo SELECCIONA entre los roles que la identidad ya
        otorga — p. ej. desempata el auto-traslado en que la misma persona
        envió y recibió). Paso EXPLÍCITO posterior a `recibir()` — NUNCA lo
        bloquea ni lo sustituye; ambos roles se firman en cualquier orden e
        independientemente (Inc.2 generaliza el Inc.1 de solo-receptor).

        `authorization` es el header `Authorization` TAL CUAL llegó a esta
        petición — reenviado a signature-service (D1: el TOTP se
        verifica-y-consume contra el bearer del propio receptor, nunca
        contra un `user_id` que este servicio pudiera declarar).

        Autoriza IGUAL que `recibir` (mismo scoping/permiso,
        `USUA_PERM_EXPEDIENTE min_crud=3`, router) MÁS un gate no-read-up
        real sobre el expediente (a diferencia de `recibir`, que trata la
        custodia como ortogonal a lectura — aquí SÍ se resuelve el
        clearance real del actor, mismo criterio que `acta()`/`fuid()`):
        404 neutro, indistinguible de una transferencia inexistente, para un
        actor que no puede LEER el expediente referenciado.

        Idempotencia (sin re-firmar, sin re-quemar TOTP): si YA existe una
        fila `(transferencia_id, rol='recibe')`, se devuelve tal cual —
        NUNCA se llama de nuevo a signature-service para ese rol.

        Riesgo firma-huérfana/TOTP-quemado (misma clase S1 que el sello
        institucional, ver `_sellar_acta_post_commit`): signature-service
        minta `firma_id` y CONSUME el TOTP ANTES de que este método
        persista la fila local — si la petición muriera entre esos dos
        pasos, la firma quedaría íntegra y auditada en `audit_log` de
        signature-service, pero SIN fila local aquí. `insert_acta_firma_personal`
        es idempotente sobre `(transferencia_id, rol)`: un reintento del
        MISMO usuario, al no encontrar fila local, volvería a llamar a
        signature-service (nuevo TOTP, nueva `firma_id`) — el `firma_id`
        huérfano anterior queda solo como traza forense en signature-service
        (log de advertencia aquí), nunca bloquea el reintento.
        """
        row = await self._repo.get(transferencia_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "transferencia_not_found",
                    "detail": str(transferencia_id),
                    "status": 404,
                },
            )
        # No-read-up (RF-SEG-08): la firma personal del receptor es un acto
        # deliberadamente ligado a la capacidad de LEER el expediente que se
        # está firmando (a diferencia de `recibir`, custodia ortogonal) —
        # mismo criterio que `acta()`/`fuid()`.
        caller_clearance = await self._archive.caller_clearance(actor.id)
        exp = await self._archive.get_expediente_by_id(
            row["expediente_id"], max_clearance=caller_clearance
        )
        if exp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": "No existe el expediente solicitado",
                    "status": 404,
                },
            )
        if row["estado"] != "recibida":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "transferencia_no_recibida",
                    "detail": (
                        "El acta de entrega solo puede firmarse personalmente una vez "
                        "recibida la transferencia"
                    ),
                    "status": 409,
                },
            )
        acta = await self._repo.get_acta(transferencia_id)
        if acta is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "acta_not_found",
                    "detail": (
                        "La transferencia aún no tiene acta de entrega "
                        "(se congela al recibirla)"
                    ),
                    "status": 404,
                },
            )

        # Derivación del rol desde la IDENTIDAD congelada (hallazgo Alta de la
        # auditoría de seguridad de Inc.1, generalizado a AMBOS roles en
        # Inc.2). El rol es función de `enviada_por`/`decidida_por` (columnas
        # que `enviar()`/`recibir()` fijaron atómicamente), NUNCA un dato de
        # autoridad que aporte el cliente: `rol_solicitado` solo SELECCIONA
        # entre los roles que la identidad ya otorga, jamás concede uno. Un
        # tercero (ni remitente ni receptor) recibe 403 pase lo que pase en el
        # param. Sin este cotejo, cualquier archivista con USUA_PERM_EXPEDIENTE
        # crud>=3 + clearance podría firmar un rol y, por
        # UNIQUE(transferencia_id, rol) + la idempotencia de lectura de arriba,
        # bloquear PERMANENTEMENTE a la parte legítima (tabla append-only, sin
        # revocación de fila). Colocado DESPUÉS de `acta is None` y ANTES de la
        # idempotencia/`sign_personal`: un no-parte recibe 403 sin filtrarse la
        # firma ajena ni quemarse un TOTP. Comparación TEXT `str(uuid)` vs.
        # `actor.audit_actor` (`str(actor.id)`), misma representación que
        # puebla las columnas en `enviar()`/`recibir()`.
        roles: set[str] = set()
        if actor.audit_actor == row["enviada_por"]:
            roles.add("entrega")
        if actor.audit_actor == row["decidida_por"]:
            roles.add("recibe")
        if actor.audit_actor == row["creada_por"]:
            # Elaborador ("Elaborado por" del FUID, Inc.3) = quien CREÓ la
            # transferencia. Firmante ADITIVO (no parte del traspaso); la
            # comparación es segura ante `creada_por` NULL porque
            # `actor.audit_actor` es siempre un UUID canónico no vacío
            # (`require_actor` fail-closed) y `str == None` es False.
            roles.add("elabora")
        if not roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "firmante_no_es_parte",
                    "detail": (
                        "Solo el elaborador (rol 'elabora'), el remitente (rol "
                        "'entrega') o el receptor (rol 'recibe') de la "
                        "transferencia pueden firmar el acta"
                    ),
                    "status": 403,
                },
            )
        if rol_solicitado is not None and rol_solicitado not in roles:
            # El param NO puede conceder un rol que la identidad no otorga.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "firmante_no_es_parte",
                    "detail": (
                        f"La identidad del firmante no corresponde al rol "
                        f"'{rol_solicitado}' en esta transferencia"
                    ),
                    "status": 403,
                },
            )
        if len(roles) == 1:
            rol = next(iter(roles))
        else:
            # Auto-traslado: la MISMA persona envió y recibió
            # (`enviada_por == decidida_por`) — su identidad da derecho a AMBOS
            # roles, firmados como dos actos independientes (el UNIQUE por rol
            # lo admite). Aquí `rol_solicitado` es OBLIGATORIO para desambiguar
            # cuál se firma. Consecuencia declarada (RF-FIR-15): firmante único
            # en ambos lados ⇒ SIN control dual (separación de funciones),
            # coherente con el perfil art. 7 no acreditado.
            if rol_solicitado is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": "rol_requerido",
                        "detail": (
                            "El firmante es a la vez remitente y receptor de "
                            "esta transferencia; indique 'rol' ('entrega' o "
                            "'recibe') a firmar"
                        ),
                        "status": 400,
                    },
                )
            rol = rol_solicitado
        existente = await self._repo.get_acta_firma_personal(transferencia_id, rol=rol)
        if existente is not None:
            # Idempotente: NUNCA se re-firma ni se re-quema el TOTP para un
            # rol ya firmado.
            return TransferenciaActaFirmaPersonalResponse(**dict(existente))

        # AREA-07-10: una parte con excepción de firma declarada no firma después. Va ANTES
        # de `sign_personal` para no quemar un TOTP en una firma que la base rechazaría
        # (el trigger de la migración 059 cubre la carrera).
        # Solo una excepción VIGENTE (sin revocar) impide firmar: la revocada es historia.
        if await self._repo.get_excepcion_vigente(transferencia_id, rol=rol) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "firma_excepcion_declarada",
                    "detail": (
                        f"El rol '{rol}' tiene una excepción de firma declarada para esta "
                        "acta; ya no puede firmarla personalmente"
                    ),
                    "status": 409,
                },
            )

        from app.clients import signature_client

        outcome = await signature_client.sign_personal(
            tenant_slug=self._tenant,
            authorization=authorization,
            actor_id=actor.audit_actor,
            payload_xml=acta["acta_fuid_xml"],
            objeto_tipo="acta_transferencia_personal",
            objeto_id=str(transferencia_id),
            totp_code=totp_code,
            firmante_id=actor.audit_actor,
        )
        if outcome.unavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "canal_firma_personal_no_disponible",
                    "detail": "signature-service no está disponible para la firma personal",
                    "status": 503,
                },
            )
        if outcome.remote_failure is not None:
            # El servicio de firma SÍ respondió, y falló. Decir aquí «no está
            # disponible» mandaba a diagnosticar el servicio equivocado: las dos
            # veces que ocurrió de verdad la causa estaba dos servicios más
            # abajo (MinIO sin disco; y un CHECK que no admitía el propio valor
            # que escribe signature-service). Fail-closed igual — 502 y no 503
            # porque la puerta contestó.
            codigo_remoto, causa = outcome.remote_failure
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "firma_personal_fallo_remoto",
                    "detail": (
                        f"El servicio de firma respondió HTTP {codigo_remoto} al firmar "
                        f"el acta: {causa}. La firma NO se registró."
                    ),
                    "status": 502,
                },
            )
        if outcome.rejected is not None:
            status_code, detail = outcome.rejected
            raise HTTPException(status_code=status_code, detail=detail)

        signed = outcome.signed
        assert signed is not None  # exactamente uno de los tres queda poblado

        try:
            return await self._persistir_firma_personal(
                transferencia_id, actor, rol, signed
            )
        except asyncpg.CheckViolationError as exc:
            # Carrera con una excepción declarada entre el chequeo previo y el INSERT
            # (trigger de la 059). La firma ya se produjo en signature-service: queda
            # huérfana como traza forense (misma clase S1 que la carrera perdida).
            logger.warning(
                "firma_id=%s huérfana: excepción de firma declarada en carrera "
                "(transferencia_id=%s rol=%s)",
                signed.firma_id,
                transferencia_id,
                rol,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "firma_excepcion_declarada",
                    "detail": f"El rol '{rol}' tiene una excepción de firma declarada",
                    "status": 409,
                },
            ) from exc

    async def _persistir_firma_personal(
        self,
        transferencia_id: uuid.UUID,
        actor: Actor,
        rol: str,
        signed: SignedPersonal,
    ) -> TransferenciaActaFirmaPersonalResponse:
        """Tramo de persistencia de `firmar_personal` (INSERT + bookkeeping WORM + asiento en
        UNA tx, WORM post-commit). Separado para poder mapear el `CheckViolationError` del
        trigger de exclusión firma<->excepción sin envolver la llamada HTTP a signature."""
        async with self._conn.transaction():
            inserted = await self._repo.insert_acta_firma_personal(
                {
                    "transferencia_id": transferencia_id,
                    "rol": rol,
                    "firmante_id": actor.id,
                    "firma_id": signed.firma_id,
                    "minio_key_firmado": signed.minio_key_firmado,
                    "xades_level": signed.xades_level,
                    "ca_fingerprint": None,
                    "acreditado": signed.acreditado,
                }
            )
            if inserted is not None:
                # AREA-07-10: la fila de bookkeeping WORM, en la MISMA tx que la firma.
                await self._repo.insert_firma_personal_worm_pendiente(inserted["id"])
                # H5 (atomicidad efecto<->asiento): solo la escritura GANADORA
                # de la carrera (ver docstring de `insert_acta_firma_personal`)
                # deja asiento — nunca un `archive.acta_firmada_personal`
                # fantasma sobre un efecto que esta llamada no produjo.
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action="archive.acta_firmada_personal",
                    object_type="transferencia_acta",
                    object_ref=str(transferencia_id),
                    actor=actor.audit_actor,
                    payload={
                        "transferencia_id": str(transferencia_id),
                        "rol": rol,
                        "firma_id": str(signed.firma_id),
                        "xades_level": signed.xades_level,
                    },
                    canal=audit.CANAL_PETICION,
                )

        if inserted is not None:
            # AREA-07-10: Object-Lock del XML de la firma, post-commit y best-effort (nunca
            # lanza); la reconciliación lo repite si falla.
            await self.proteger_worm_firma_personal(
                inserted["id"], actor_id=actor.audit_actor, origen="close"
            )

        if inserted is None:
            # Carrera perdida (mismo rol firmado concurrentemente por OTRA
            # petición que sí ganó la escritura local) — el `firma_id` recién
            # obtenido queda huérfano en signature-service (traza forense,
            # misma clase S1 que el sello institucional); NUNCA se re-lanza
            # como error, se devuelve la fila GANADORA (idéntica semántica
            # de negocio: el rol ya está firmado).
            logger.warning(
                "transferencia_acta_firma_personal: firma_id=%s quedó huérfana "
                "(carrera perdida) para transferencia_id=%s rol=%s",
                signed.firma_id,
                transferencia_id,
                rol,
            )
            inserted = await self._repo.get_acta_firma_personal(transferencia_id, rol=rol)
            assert inserted is not None

        return TransferenciaActaFirmaPersonalResponse(**dict(inserted))

    # -- AREA-07-10: «firma inobtenible» -- la constancia como PRUEBA ----------------------

    @staticmethod
    def _partes_de_la_transferencia(row) -> set[uuid.UUID]:
        """Identidades de las TRES partes de la transferencia (`enviada_por`, `decidida_por`,
        `creada_por`); las que no parsean como UUID se ignoran (no son una identidad)."""
        partes: set[uuid.UUID] = set()
        for columna in ("enviada_por", "decidida_por", "creada_por"):
            try:
                partes.add(uuid.UUID(str(row[columna])))
            except (ValueError, TypeError):
                continue
        return partes

    async def _puede_ver_fundamento(self, user_id: uuid.UUID | None) -> bool:
        """El `fundamento` completo es dato sensible (salud, fallecimiento): solo
        `USUA_PERM_ADMIN` nivel >= 3. Sin identidad, fail-closed."""
        if user_id is None:
            return False
        await self._repo.set_search_path()
        return bool(await check_permission(self._conn, user_id, "USUA_PERM_ADMIN", 3))

    @staticmethod
    def _vista_excepcion(
        row,
        *,
        ver_fundamento: bool,
        referencia_visible: bool = True,
        sello_valida: bool | None = None,
        firma_personal_valida: bool | None = None,
        revocacion_sello_valida: bool | None = None,
        revocacion_firma_personal_valida: bool | None = None,
        enlace_valido: bool | None = None,
        enlace_motivo: str | None = None,
        advertencias: builtins.list[str] | None = None,
    ) -> TransferenciaActaFirmaExcepcion:
        """Fila -> respuesta. El texto del fundamento (y su sal) solo sale a quien puede verlo; a
        todos los demás les quedan el compromiso, el motivo y la referencia.

        `referencia_visible=False` (el lector no ve el radicado de soporte, o no se pudo
        comprobar) recorta TODOS los campos `referencia_*` a `None`: no existe, no lo puede ver
        y document-service no respondió son indistinguibles."""
        revocada_en = row.get("revocada_en")
        revocacion_fundamento = row.get("revocacion_fundamento")
        ref = (lambda v: v) if referencia_visible else (lambda _v: None)
        return TransferenciaActaFirmaExcepcion(
            id=row["id"],
            transferencia_id=row["transferencia_id"],
            rol=row["rol"],
            parte_id=row["parte_id"],
            motivo=row["motivo"],
            fundamento=row["fundamento"] if ver_fundamento else None,
            ordinal=row.get("ordinal") or 1,
            sustituye_a=row.get("sustituye_a"),
            constancia_version=row.get("constancia_version") or 1,
            fundamento_compromiso=row.get("fundamento_compromiso"),
            fundamento_compromiso_algoritmo=row.get("fundamento_compromiso_algoritmo"),
            # La sal NO sale por ninguna vía de lectura: solo viaja dentro del sobre sellado.
            fundamento_sal=None,
            referencia_documental=ref(row["referencia_documental"]),
            referencia_radicado_id=ref(row.get("referencia_radicado_id")),
            referencia_radicado_numero=ref(row.get("referencia_radicado_numero")),
            referencia_anexo_id=ref(row.get("referencia_anexo_id")),
            referencia_anexo_sha256=ref(row.get("referencia_anexo_sha256")),
            declarada_por=row["declarada_por"],
            declarada_en=row["declarada_en"],
            declarante_nombre=row.get("declarante_nombre"),
            declarante_cargo=row.get("declarante_cargo"),
            declarante_fuente=row.get("declarante_fuente"),
            parte_nombre=row.get("parte_nombre"),
            parte_cargo=row.get("parte_cargo"),
            parte_fuente=row.get("parte_fuente"),
            firmada=row.get("firma_id") is not None,
            constancia_sha256=row.get("constancia_sha256"),
            xades_level=row.get("xades_level"),
            firma_personal=row.get("firma_personal_id") is not None,
            xades_level_personal=row.get("xades_level_personal"),
            firma_personal_requerida=bool(row.get("firma_personal_requerida")),
            worm_protegido=row.get("worm_protegido"),
            worm_personal_protegido=row.get("worm_personal_protegido"),
            worm_retain_until=row.get("worm_retain_until"),
            sobre=row.get("sobre_minio_key") is not None,
            sobre_sha256=row.get("sobre_sha256"),
            sobre_xades_level=row.get("sobre_xades_level"),
            worm_sobre_protegido=row.get("worm_sobre_protegido"),
            enlace_valido=enlace_valido,
            enlace_motivo=enlace_motivo,
            sello_valida=sello_valida,
            firma_personal_valida=firma_personal_valida,
            advertencias=advertencias or [],
            revocada=revocada_en is not None,
            revocacion_id=row.get("revocacion_id"),
            revocada_por=row.get("revocada_por"),
            revocada_en=revocada_en,
            revocacion_fundamento=revocacion_fundamento if ver_fundamento else None,
            revocacion_causa=row.get("revocacion_causa"),
            revocacion_fundamento_compromiso=row.get("revocacion_fundamento_compromiso"),
            revocacion_fundamento_compromiso_algoritmo=row.get(
                "revocacion_fundamento_compromiso_algoritmo"
            ),
            revocacion_fundamento_sal=None,
            revocante_nombre=row.get("revocante_nombre"),
            revocante_cargo=row.get("revocante_cargo"),
            revocante_fuente=row.get("revocante_fuente"),
            revocacion_firmada=row.get("revocacion_firma_id") is not None,
            revocacion_sha256=row.get("revocacion_sha256"),
            revocacion_xades_level=row.get("revocacion_xades_level"),
            revocacion_firma_personal=row.get("revocacion_firma_personal_id") is not None,
            revocacion_worm_protegido=row.get("revocacion_worm_protegido"),
            revocacion_worm_personal_protegido=row.get("revocacion_worm_personal_protegido"),
            revocacion_worm_retain_until=row.get("revocacion_worm_retain_until"),
            revocacion_sobre=row.get("revocacion_sobre_minio_key") is not None,
            revocacion_sobre_sha256=row.get("revocacion_sobre_sha256"),
            revocacion_sobre_xades_level=row.get("revocacion_sobre_xades_level"),
            revocacion_worm_sobre_protegido=row.get("revocacion_worm_sobre_protegido"),
            revocacion_sello_valida=revocacion_sello_valida,
            revocacion_firma_personal_valida=revocacion_firma_personal_valida,
        )

    async def _referencias_visibles(
        self, excepciones: builtins.list, caller_clearance: int
    ) -> set[str]:
        """Radicados de soporte que el LECTOR puede ver, en UN lote contra document-service con su
        clearance (no-read-up, RF-SEG-08). Cualquier fallo devuelve vacío: ante un error la
        referencia se oculta igual que ante un radicado que no se puede ver (un 503 distinto
        sería un oráculo sobre su existencia). Sin referencias no hay llamada."""
        ids = sorted(
            {
                e["referencia_radicado_id"]
                for e in excepciones
                if e.get("referencia_radicado_id") is not None
            },
            key=str,
        )
        if not ids:
            return set()
        from app.clients import document_client

        try:
            visibles = await document_client.fetch_radicado_metadata_internal(
                tenant_slug=self._tenant, radicado_ids=ids, max_clearance=caller_clearance
            )
        except Exception:  # noqa: BLE001 — error == «no visible» (sin oráculo).
            logger.warning("no se pudo resolver la visibilidad de las referencias", exc_info=True)
            return set()
        return {str(k) for k in visibles}

    async def _denegar_conflicto_de_interes(
        self,
        transferencia_id: uuid.UUID,
        actor: Actor,
        *,
        rol: str,
        accion: str,
    ) -> HTTPException:
        """403 `declarante_es_parte_de_la_transferencia` con su asiento de denegación (la
        denegación no depende del asiento: si falla, igual se rechaza). Devuelve la excepción
        para que el llamador la lance."""
        try:
            await audit.append(
                self._conn,
                tenant_slug=self._tenant,
                service=_SERVICE,
                action=accion,
                object_type="transferencia_acta",
                object_ref=str(transferencia_id),
                actor=actor.audit_actor,
                payload={
                    "transferencia_id": str(transferencia_id),
                    "rol": rol,
                    "motivo_denegacion": _CONFLICTO_DE_INTERES,
                },
                canal=audit.CANAL_PETICION,
            )
        except Exception:  # noqa: BLE001 — la denegación no depende del asiento.
            logger.warning("audit.append(%s) failed", accion, exc_info=True)
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": _CONFLICTO_DE_INTERES,
                "detail": (
                    "Quien declara o revoca la excepción de firma no puede ser parte de la "
                    "transferencia (quien la elaboró, la envió o la recibió): lo hace otra "
                    "persona con permiso de administración"
                ),
                "status": 403,
            },
        )

    async def _cargar_acta_recibida(self, transferencia_id: uuid.UUID, user_id: uuid.UUID):
        """Precondiciones comunes de declarar/revocar: la transferencia existe, el expediente es
        legible con el clearance del llamante (404 neutro) y el acta ya está congelada."""
        row = await self._repo.get(transferencia_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "transferencia_not_found",
                    "detail": str(transferencia_id),
                    "status": 404,
                },
            )
        caller_clearance = await self._archive.caller_clearance(user_id)
        exp = await self._archive.get_expediente_by_id(
            row["expediente_id"], max_clearance=caller_clearance
        )
        if exp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": "No existe el expediente solicitado",
                    "status": 404,
                },
            )
        acta = await self._repo.get_acta(transferencia_id) if row["estado"] == "recibida" else None
        if acta is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "transferencia_no_recibida",
                    "detail": (
                        "La excepción de firma solo se gestiona sobre un acta ya congelada "
                        "(transferencia recibida)"
                    ),
                    "status": 409,
                },
            )
        return row, exp, acta, caller_clearance

    async def _resolver_referencia(
        self,
        payload: FirmaInobtenibleRequest,
        *,
        caller_clearance: int,
    ) -> tuple[str, str]:
        """El soporte documental debe EXISTIR y ser visible para el declarante: lo resuelve el
        servidor contra document-service (token interno), no lo afirma el cliente. Devuelve
        `(número del radicado, SHA-256 del anexo)`, que la constancia v2 lleva dentro para ser
        autosuficiente. 422 `referencia_documental_invalida` tanto si no existen como si el
        declarante no los puede leer (mismo cuerpo: no es un oráculo sobre radicados
        reservados); 503 si document-service o storage no responden (fail-closed: no se asienta
        una referencia que no se pudo comprobar)."""
        from app.clients import document_client, storage_client

        invalida = HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "referencia_documental_invalida",
                "detail": (
                    "El radicado o anexo de soporte no existe o no está disponible para usted"
                ),
                "status": 422,
            },
        )
        radicado = payload.referencia_radicado_id
        anexo = payload.referencia_anexo_id
        try:
            visibles = await document_client.fetch_radicado_metadata_internal(
                tenant_slug=self._tenant,
                radicado_ids=[radicado],
                max_clearance=caller_clearance,
            )
            meta = visibles.get(str(radicado))
            if meta is None:
                raise invalida
            anexos = await document_client.fetch_anexos_internal(
                tenant_slug=self._tenant,
                radicado_ids=[radicado],
                max_clearance=caller_clearance,
            )
            ids = {str(a.get("file_id")) for a in anexos.get(str(radicado), [])}
            if str(anexo) not in ids:
                raise invalida
        except document_client.DocumentClientError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "document_service_no_disponible",
                    "detail": (
                        "No se pudo comprobar el soporte documental en document-service; "
                        "la excepción no se declaró"
                    ),
                    "status": 503,
                },
            ) from exc
        numero = meta.get("tracking_number") if isinstance(meta, dict) else None
        huellas = await storage_client.fetch_file_hashes_internal(
            tenant_slug=self._tenant, file_ids=[str(anexo)]
        )
        sha = huellas.get(str(anexo))
        if not numero or not sha:
            # Sin número de radicado o sin huella del anexo la constancia no sería
            # autosuficiente: no se declara con un hueco (best-effort del vínculo NO aplica aquí).
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "soporte_documental_sin_huella",
                    "detail": (
                        "No se pudo obtener el número del radicado o la huella SHA-256 del "
                        "anexo de soporte; la excepción no se declaró"
                    ),
                    "status": 503,
                },
            )
        return str(numero), str(sha)

    async def _resolver_identidades(
        self, *ids: uuid.UUID
    ) -> dict[uuid.UUID, Identidad]:
        """Instantánea de identidad de cada persona, resuelta por el SERVIDOR: nombre = CN del
        certificado de firma activo si lo tiene, si no el username del catálogo; cargo del
        certificado. Quien no está en el catálogo queda como `no_resuelta` (se declara el hueco,
        no se inventa un nombre). Solo columnas públicas."""
        filas = await self._repo.get_identidades(list(ids))
        out: dict[uuid.UUID, Identidad] = {}
        for pid in ids:
            fila = filas.get(pid)
            if fila is None:
                out[pid] = Identidad(id=pid, nombre=None, cargo=None, fuente="no_resuelta")
                continue
            cn = _cn_de(fila["subject_dn"])
            out[pid] = Identidad(
                id=pid,
                nombre=cn or fila["username"],
                cargo=fila["cargo"],
                fuente="certificado" if cn else "usuario",
                certificado_activo=bool(fila["certificado_activo"]),
            )
        return out

    @staticmethod
    def _revocacion_protegida(ultima) -> bool:
        """¿La revocación de este eslabón está SELLADA y con TODOS sus artefactos bajo
        Object-Lock confirmado? Solo entonces la cadena admite una nueva constancia."""
        if ultima.get("revocada_en") is None or ultima.get("revocacion_firma_id") is None:
            return False
        if ultima.get("revocacion_worm_protegido") is not True:
            return False
        if (
            ultima.get("revocacion_sobre_minio_key") is not None
            and ultima.get("revocacion_worm_sobre_protegido") is not True
        ):
            return False
        return (
            ultima.get("revocacion_firma_personal_id") is None
            or ultima.get("revocacion_worm_personal_protegido") is True
        )

    @staticmethod
    def _fallo_firma_personal(outcome, *, rol_opcional: bool) -> bool:
        """Traduce un desenlace NO exitoso de `sign_personal` a su HTTPException, igual que
        `firmar_personal`. Devuelve `True` solo cuando el declarante NO tiene credencial activa
        y la firma personal es opcional (se sigue sin ella); en todo otro caso lanza."""
        if outcome.unavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "canal_firma_personal_no_disponible",
                    "detail": "signature-service no está disponible para la firma personal",
                    "status": 503,
                },
            )
        if outcome.remote_failure is not None:
            codigo_remoto, causa = outcome.remote_failure
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": "firma_personal_fallo_remoto",
                    "detail": (
                        f"El servicio de firma respondió HTTP {codigo_remoto} al firmar la "
                        f"constancia: {causa}. La excepción NO se declaró."
                    ),
                    "status": 502,
                },
            )
        assert outcome.rejected is not None
        status_code, detail = outcome.rejected
        if (
            rol_opcional
            and status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
            and isinstance(detail, dict)
            and detail.get("error") == "sin_certificado_activo"
        ):
            return True
        raise HTTPException(status_code=status_code, detail=detail)

    async def _sellar_sobre(
        self,
        *,
        acto: Literal["constancia", "revocacion"],
        acto_id: uuid.UUID,
        texto: str,
        huella,
        actor: Actor,
        indice_version: int,
    ) -> tuple[dict, datetime]:
        """FIRMA-2 ronda 3 — genera y SELLA el sobre de revelación del fundamento (texto + sal +
        compromiso) de una constancia o una revocación. Va en el mismo flujo que el sello de la
        constancia: ANTES de la transacción y de consumir el TOTP de la firma personal; si no se
        puede sellar, 503 y NADA queda escrito (un sobre sin sello no es copia preservable). Devuelve
        las columnas `sobre_*` de la fila del acto y el instante desde el que se subió (cota de la
        purga de huérfanos)."""
        from app.clients import signature_client

        try:
            sobre_xml = build_sobre_xml(acto_id=acto_id, acto=acto, texto=texto, huella=huella)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "fundamento_caracteres_invalidos",
                    "detail": "El fundamento contiene caracteres de control no admitidos",
                    "status": 422,
                },
            ) from None
        # `objeto_id` con prefijo `sobre-`: el nombre del fichero en el almacén
        # (`acta-transferencia-sobre-<uuid>-v<n>-<firma>-firmado.xml`) permite que el barrido
        # reconozca un sobre sin referencia (`purgar_sobres_huerfanos`).
        sobre_id = f"sobre-{uuid.uuid4()}"
        desde = datetime.now(tz=UTC) - timedelta(seconds=30)
        sello = await signature_client.sign_xml_outcome(
            tenant_slug=self._tenant,
            actor_id=actor.audit_actor,
            payload_xml=sobre_xml,
            objeto_tipo="acta_transferencia",
            objeto_id=sobre_id,
            indice_id=None,
            version=indice_version,
        )
        if sello.signed is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "sello_institucional_no_disponible"
                    if sello.seal_absent
                    else "sellado_sobre_no_obtenido",
                    "detail": (
                        "No se pudo sellar el sobre de revelación del fundamento; el acto NO se "
                        "registró (sin el sobre, el texto viviría solo en la base)"
                    ),
                    "status": 503,
                },
            )
        return (
            {
                "sobre_firma_id": sello.signed.firma_id,
                "sobre_minio_key": sello.signed.minio_key_firmado,
                "sobre_sha256": sha256_c14n(sobre_xml),
                "sobre_xades_level": sello.signed.xades_level,
            },
            desde,
        )

    async def _purgar_sobre_huerfano(self, sobre: dict, desde: datetime, *, contexto: str) -> None:
        """Best-effort, NUNCA lanza: el sobre ya está en el almacén pero la fila que lo referencia
        no se escribió (TOTP ya validado, trigger, carrera perdida, fallo del asiento). Lo purga
        por `/internal/purgar-huerfanos` de storage (que decide por referencias, `subido_desde` y
        WORM, y deja su propio asiento `file.purgado_huerfano`). Si falla, el barrido
        `purgar_sobres_huerfanos` lo recoge por el nombre del fichero."""
        try:
            from app.clients import storage_client

            file_id = uuid.UUID(str(sobre["sobre_minio_key"]).split("/", 1)[0])
            resultados = await storage_client.purgar_huerfanos_internal(
                tenant_slug=self._tenant,
                file_ids=[file_id],
                referencia=f"sobre-fundamento:{contexto}",
                motivo="sobre de revelacion sin fila que lo referencie",
                subido_desde=desde,
            )
            if resultados is None or any(r.get("resultado") == "error" for r in resultados):
                logger.warning("purga del sobre huérfano %s no confirmada (%s)", file_id, resultados)
        except Exception:  # noqa: BLE001 — la reconciliación lo recoge.
            logger.warning("purga del sobre huérfano falló (best-effort)", exc_info=True)

    async def purgar_sobres_huerfanos(self, *, limite: int = 50) -> dict[str, int]:
        """Barrido de reconciliación: ficheros del almacén con el nombre de un sobre
        (`acta-transferencia-sobre-…-firmado.xml`), más viejos que la gracia y que NINGUNA
        constancia ni revocación referencia en `sobre_minio_key`. La purga la decide storage
        (referencias del tenant, WORM); aquí solo se recogen los candidatos."""
        from app.clients import storage_client

        filas = await self._repo.list_sobres_huerfanos(
            limit=limite, gracia_horas=settings.sobre_huerfano_gracia_horas
        )
        resumen = {"candidatos": len(filas), "purgados": 0, "fallidos": 0}
        if not filas:
            return resumen
        try:
            resultados = await storage_client.purgar_huerfanos_internal(
                tenant_slug=self._tenant,
                file_ids=[f["id"] for f in filas],
                referencia="sobre-fundamento:barrido",
                motivo="sobre de revelacion huerfano (barrido de reconciliacion)",
                subido_desde=min(f["uploaded_at"] for f in filas),
            )
        except Exception:  # noqa: BLE001
            logger.warning("barrido de sobres huérfanos falló", exc_info=True)
            resultados = None
        for r in resultados or []:
            if r.get("resultado") == "error":
                resumen["fallidos"] += 1
            elif r.get("resultado") == "purgado":
                resumen["purgados"] += 1
        if resultados is None:
            resumen["fallidos"] = len(filas)
        return resumen

    async def declarar_firma_inobtenible(
        self,
        transferencia_id: uuid.UUID,
        actor: Actor,
        payload: FirmaInobtenibleRequest,
        authorization: str | None = None,
    ) -> TransferenciaActaFirmaExcepcion:
        """`POST /transferencias/{id}/acta/firma-inobtenible` (AREA-07-10, ADR-030 + addendum
        FIRMA-2) — acto administrativo que declara que UNA parte del acta (elabora/entrega/recibe)
        no puede firmar (fallecimiento, retiro, incapacidad). Deja una CONSTANCIA inmutable y
        FIRMADA (`transferencia_acta_firma_excepcion`, migraciones 059/061/062) y un asiento en
        `audit_log` en la MISMA tx; el acta pasa a `firmada_con_excepcion` (o
        `sellada_con_excepcion` si ninguna de las dos partes firmó), nunca a `firmada_completa`.

        Autorización (router): `USUA_PERM_ADMIN` crud>=3. Aquí, lo que el RBAC no dice:
        - no-read-up real sobre el expediente (404 neutro), como `firmar_personal`;
        - la parte que no firma se DERIVA del rol (`enviada_por`/`decidida_por`/`creada_por`),
          nunca viene del cliente;
        - conflicto de interés: quien declara NO es ninguna de las tres partes de la
          transferencia (403 `declarante_es_parte_de_la_transferencia`, con asiento de
          denegación; el motor lo refuerza con un trigger);
        - el soporte es un radicado y un anexo EXISTENTES y visibles para el declarante,
          resueltos contra document-service (422 / 503); la constancia lleva el número del
          radicado y el SHA-256 del anexo;
        - CADENA: sobre una excepción VIGENTE es 409 `excepcion_vigente`; tras revocarla se
          puede declarar de nuevo (`ordinal` n+1, `sustituye_a`) solo si la revocación está
          sellada y TODOS sus artefactos protegidos WORM (409 `revocacion_sin_proteger`); y si
          la parte firmó personalmente (también en la ventana revocada) la cadena está cerrada
          (409 `parte_ya_firmo`: la firma prevalece). El motor lo repite con un trigger;
        - el fundamento se huella con un compromiso con sal por registro, SHA-256(sal || texto),
          calculado aquí (A1); el texto y la sal nunca viajan en el artefacto ni en el asiento;
        - la constancia es PRUEBA: XML canónico v3 (C14N exclusiva, A2) sellado con el sello institucional XAdES-T y,
          si el declarante tiene un certificado de firma activo (o la política XAdES de la
          ENTIDAD lo exige para el nivel del expediente), firmado también por él. Si no se puede
          sellar, NO se declara nada (503): una constancia sin firma es justo lo que no sirve
          ante el AGN."""
        row, exp, acta, caller_clearance = await self._cargar_acta_recibida(
            transferencia_id, actor.id
        )

        columna = {"entrega": "enviada_por", "recibe": "decidida_por", "elabora": "creada_por"}[
            payload.rol
        ]
        try:
            parte_id = uuid.UUID(str(row[columna]))
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "parte_sin_identidad",
                    "detail": (
                        f"La transferencia no tiene identidad registrada para el rol "
                        f"'{payload.rol}'; no se puede declarar su excepción de firma"
                    ),
                    "status": 409,
                },
            ) from None

        if actor.id in self._partes_de_la_transferencia(row):
            raise await self._denegar_conflicto_de_interes(
                transferencia_id,
                actor,
                rol=payload.rol,
                accion="archive.acta_firma_inobtenible_denegada",
            )

        ultima = await self._repo.get_ultima_excepcion(transferencia_id, rol=payload.rol)
        if ultima is not None and ultima.get("revocada_en") is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "excepcion_vigente",
                    "detail": (
                        f"El rol '{payload.rol}' ya tiene una excepción de firma vigente: "
                        "revóquela antes de declarar otra"
                    ),
                    "status": 409,
                },
            )
        if await self._repo.get_acta_firma_personal(transferencia_id, rol=payload.rol) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "parte_ya_firmo",
                    "detail": (
                        f"El rol '{payload.rol}' ya firmó personalmente el acta: la firma "
                        "prevalece y no admite una nueva excepción"
                    ),
                    "status": 409,
                },
            )
        if ultima is not None and not self._revocacion_protegida(ultima):
            raise self._revocacion_sin_proteger(payload.rol)
        ordinal = (ultima["ordinal"] + 1) if ultima is not None else 1
        sustituye_a = ultima["id"] if ultima is not None else None

        # El sello se estampa a nombre del declarante (`POST /sign-xml` exige PERM_FIRMA >= 2): se
        # comprueba ANTES de llamar, porque signature-service responde 403 y el cliente de sello
        # lo clasifica como «transitorio» -> el declarante recibiría un 503 que no puede arreglar.
        await self._repo.set_search_path()
        if not await check_permission(self._conn, actor.id, "PERM_FIRMA", 2):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "declarante_sin_permiso_de_firma",
                    "detail": (
                        "Sellar la constancia exige el permiso PERM_FIRMA (nivel 2 o superior) "
                        "además de la administración: solicítelo o pida que declare otra persona"
                    ),
                    "status": 403,
                },
            )

        referencia_numero, anexo_sha256 = await self._resolver_referencia(
            payload, caller_clearance=caller_clearance
        )

        # Política XAdES de la ENTIDAD (parámetro del tenant, entorno de respaldo): ¿la firma
        # personal del declarante es obligatoria para el nivel de ESTE expediente? Además, quien
        # tiene un certificado de firma activo SIEMPRE firma (FIRMA-2): poder firmar y no hacerlo
        # dejaría una constancia más débil de lo que el declarante podía dar.
        await self._repo.set_search_path()
        modo = await modo_xades_de_la_entidad(self._conn)
        identidades = await self._resolver_identidades(actor.id, parte_id)
        declarante, parte = identidades[actor.id], identidades[parte_id]
        por_politica = gate_xades_activo(exp.nivel_seguridad, modo)
        personal_requerida = por_politica or declarante.certificado_activo
        if personal_requerida and not payload.totp_code:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "firma_personal_declarante_requerida",
                    "detail": (
                        "El declarante debe firmar personalmente la constancia (tiene un "
                        "certificado de firma activo o la política de la entidad lo exige para "
                        "este expediente): envíe 'totp_code'"
                    ),
                    "status": 422,
                },
            )

        # Compromiso con sal por registro (A1), local: SHA-256(sal || texto). La sal solo vive en la
        # base; el compromiso viaja en el XML, la columna y el asiento.
        huella = huella_de_fundamento(payload.fundamento)

        constancia_id = uuid.uuid4()
        declarada_en = datetime.now(tz=UTC)
        constancia_xml = build_constancia_xml(
            constancia_id=constancia_id,
            transferencia_id=transferencia_id,
            expediente_id=row["expediente_id"],
            acta_fuid_sha256=acta["acta_fuid_sha256"],
            ordinal=ordinal,
            sustituye_a=sustituye_a,
            revocacion_sustituida_sha256=ultima.get("revocacion_sha256") if ultima else None,
            rol=payload.rol,
            parte=parte,
            motivo=payload.motivo,
            fundamento=huella,
            referencia_radicado_id=payload.referencia_radicado_id,
            referencia_radicado_numero=referencia_numero,
            referencia_anexo_id=payload.referencia_anexo_id,
            referencia_anexo_sha256=anexo_sha256,
            declarante=declarante,
            declarada_en=declarada_en,
        )

        # --- firmas (HTTP): SIEMPRE fuera de la transacción y ANTES de escribir nada ---
        from app.clients import signature_client

        sello = await signature_client.sign_xml_outcome(
            tenant_slug=self._tenant,
            actor_id=actor.audit_actor,
            payload_xml=constancia_xml,
            objeto_tipo="acta_transferencia",
            objeto_id=str(constancia_id),
            indice_id=None,
            version=int(acta["indice_version"] or 1),
        )
        if sello.signed is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "sello_institucional_no_disponible"
                    if sello.seal_absent
                    else "sellado_constancia_no_obtenido",
                    "detail": (
                        "No se pudo sellar la constancia con el sello institucional; la "
                        "excepción NO se declaró (una constancia sin firma no es prueba)"
                    ),
                    "status": 503,
                },
            )
        personal: SignedPersonal | None = None
        if payload.totp_code:
            outcome = await signature_client.sign_personal(
                tenant_slug=self._tenant,
                authorization=authorization,
                actor_id=actor.audit_actor,
                payload_xml=constancia_xml,
                objeto_tipo="acta_firma_excepcion",
                objeto_id=str(constancia_id),
                totp_code=payload.totp_code,
                firmante_id=actor.audit_actor,
            )
            if outcome.signed is not None:
                personal = outcome.signed
            else:
                self._fallo_firma_personal(outcome, rol_opcional=not personal_requerida)

        # Sobre de revelación (texto + sal): se sube DESPUÉS de validar la firma personal (un TOTP
        # inválido no deja una copia del texto en el almacén). Desde aquí, todo fallo antes de
        # que la fila lo referencie lo purga (`_purgar_sobre_huerfano`).
        sobre, sobre_desde = await self._sellar_sobre(
            acto="constancia",
            acto_id=constancia_id,
            texto=payload.fundamento,
            huella=huella,
            actor=actor,
            indice_version=int(acta["indice_version"] or 1),
        )

        data = {
            "id": constancia_id,
            "transferencia_id": transferencia_id,
            "rol": payload.rol,
            "parte_id": parte_id,
            "motivo": payload.motivo,
            "fundamento": payload.fundamento,
            "referencia_documental": referencia_canonica(
                payload.referencia_radicado_id, payload.referencia_anexo_id
            ),
            "referencia_radicado_id": payload.referencia_radicado_id,
            "referencia_anexo_id": payload.referencia_anexo_id,
            "referencia_radicado_numero": referencia_numero,
            "referencia_anexo_sha256": anexo_sha256,
            "declarada_por": actor.id,
            "declarada_en": declarada_en,
            "ordinal": ordinal,
            "sustituye_a": sustituye_a,
            "constancia_version": CONSTANCIA_VERSION,
            "declarante_nombre": declarante.nombre,
            "declarante_cargo": declarante.cargo,
            "declarante_fuente": declarante.fuente,
            "parte_nombre": parte.nombre,
            "parte_cargo": parte.cargo,
            "parte_fuente": parte.fuente,
            "fundamento_sal": huella.sal,
            "fundamento_compromiso": huella.compromiso,
            "fundamento_compromiso_algoritmo": huella.algoritmo,
            "constancia_xml": constancia_xml,
            # `constancia_xml` YA es la C14N exclusiva: es lo que se firma y lo que se huella (A2).
            "constancia_sha256": sha256_c14n(constancia_xml),
            "firma_id": sello.signed.firma_id,
            "minio_key_firmado": sello.signed.minio_key_firmado,
            "xades_level": sello.signed.xades_level,
            "firma_personal_id": personal.firma_id if personal else None,
            "minio_key_firmado_personal": personal.minio_key_firmado if personal else None,
            "xades_level_personal": personal.xades_level if personal else None,
            "firma_personal_requerida": personal_requerida,
            **sobre,
        }
        try:
            async with self._conn.transaction():
                inserted = await self._repo.insert_acta_firma_excepcion(data)
                if inserted is not None:
                    # Bookkeeping WORM en la MISMA tx: toda constancia tiene su fila aunque el
                    # proceso muera antes del disparo post-commit y la reconciliación la recoge.
                    await self._repo.insert_firma_excepcion_worm_pendiente(
                        inserted["id"], "sello"
                    )
                    await self._repo.insert_firma_excepcion_worm_pendiente(
                        inserted["id"], "sobre"
                    )
                    if personal is not None:
                        await self._repo.insert_firma_excepcion_worm_pendiente(
                            inserted["id"], "personal"
                        )
                    # H5: asiento atómico con la constancia, solo de la escritura ganadora. El
                    # texto del fundamento NO viaja al asiento (el audit_log es permanente y
                    # puede referir salud/fallecimiento): va su compromiso y la referencia.
                    await audit.append(
                        self._conn,
                        tenant_slug=self._tenant,
                        service=_SERVICE,
                        action="archive.acta_firma_inobtenible_declarada",
                        object_type="transferencia_acta",
                        object_ref=str(transferencia_id),
                        actor=actor.audit_actor,
                        payload={
                            "transferencia_id": str(transferencia_id),
                            "expediente_id": str(row["expediente_id"]),
                            "excepcion_id": str(constancia_id),
                            "ordinal": ordinal,
                            "sustituye_a": str(sustituye_a) if sustituye_a else None,
                            "rol": payload.rol,
                            "parte_id": str(parte_id),
                            # El audit_log es permanente: el motivo viaja en su forma neutra
                            # (Ley 1581 art. 5) y NO el número del radicado de soporte (S4):
                            # quedan el id y la huella del anexo. Nunca la sal.
                            "motivo": motivo_publico(payload.motivo),
                            "referencia_documental": data["referencia_documental"],
                            "referencia_anexo_sha256": anexo_sha256,
                            "fundamento_compromiso": huella.compromiso,
                            "fundamento_compromiso_algoritmo": huella.algoritmo,
                            "constancia_sha256": data["constancia_sha256"],
                            "firma_id": str(sello.signed.firma_id),
                            "firma_personal_id": str(personal.firma_id) if personal else None,
                            "firma_personal_requerida": personal_requerida,
                            "sobre_sha256": sobre["sobre_sha256"],
                            "sobre_firma_id": str(sobre["sobre_firma_id"]),
                            "politica_xades": modo,
                        },
                        canal=audit.CANAL_PETICION,
                    )
        except asyncpg.CheckViolationError as exc:
            await self._purgar_sobre_huerfano(sobre, sobre_desde, contexto="declarar")
            # Carrera con una firma personal del mismo rol, conflicto de interés o cadena sin
            # proteger que el motor atajó: los triggers señalan su causa con un nombre estable.
            if exc.constraint_name in (
                "ck_firma_excepcion_declarante_es_parte",
                "ck_firma_excepcion_revocante_es_parte",
            ):
                raise await self._denegar_conflicto_de_interes(
                    transferencia_id,
                    actor,
                    rol=payload.rol,
                    accion="archive.acta_firma_inobtenible_denegada",
                ) from None
            if exc.constraint_name == "ck_firma_excepcion_revocacion_sin_proteger":
                raise self._revocacion_sin_proteger(payload.rol) from None
            if exc.constraint_name == "ck_firma_excepcion_cadena_invalida":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "cadena_de_constancias_invalida",
                        "detail": f"La constancia no continúa la cadena del rol '{payload.rol}'",
                        "status": 409,
                    },
                ) from None
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "parte_ya_firmo",
                    "detail": f"El rol '{payload.rol}' ya firmó personalmente el acta",
                    "status": 409,
                },
            ) from None
        except Exception:
            await self._purgar_sobre_huerfano(sobre, sobre_desde, contexto="declarar")
            raise
        if inserted is None:
            await self._purgar_sobre_huerfano(sobre, sobre_desde, contexto="declarar")
            # Carrera perdida: los sellos recién obtenidos quedan huérfanos en signature-service
            # (traza forense, misma clase S1 que el sello del acta); no se asienta nada.
            logger.warning(
                "constancia de firma inobtenible: firma_id=%s huérfana (carrera perdida) "
                "transferencia_id=%s rol=%s",
                sello.signed.firma_id,
                transferencia_id,
                payload.rol,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "excepcion_vigente",
                    "detail": (
                        f"El rol '{payload.rol}' ya tiene una excepción de firma vigente "
                        "(la constancia es inmutable)"
                    ),
                    "status": 409,
                },
            )

        # Object-Lock post-commit y best-effort (nunca lanza); la reconciliación lo repite. La
        # respuesta dice el estado REAL de cada artefacto al terminar (`None` = no existe).
        vista = dict(inserted)
        vista["worm_protegido"] = (
            await self.proteger_worm_firma_excepcion(
                inserted["id"], "sello", actor_id=actor.audit_actor, origen="close"
            )
            == "protegido"
        )
        if personal is not None:
            vista["worm_personal_protegido"] = (
                await self.proteger_worm_firma_excepcion(
                    inserted["id"], "personal", actor_id=actor.audit_actor, origen="close"
                )
                == "protegido"
            )
        vista["worm_sobre_protegido"] = (
            await self.proteger_worm_firma_excepcion(
                inserted["id"], "sobre", actor_id=actor.audit_actor, origen="close"
            )
            == "protegido"
        )
        # El declarante acaba de ver el soporte (se resolvió con su clearance): sin segunda ronda.
        return self._vista_excepcion(vista, ver_fundamento=True)

    @staticmethod
    def _revocacion_sin_proteger(rol: str) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "revocacion_sin_proteger",
                "detail": (
                    f"La revocación anterior del rol '{rol}' aún no está sellada y protegida "
                    "(Object-Lock): la constancia sustituida es prueba y su baja también. "
                    "Reintente cuando la protección se confirme"
                ),
                "status": 409,
            },
        )

    async def revocar_firma_inobtenible(
        self,
        transferencia_id: uuid.UUID,
        actor: Actor,
        payload: RevocarFirmaInobtenibleRequest,
        authorization: str | None = None,
    ) -> TransferenciaActaFirmaExcepcion:
        """`POST /transferencias/{id}/acta/firma-inobtenible/revocar` (AREA-07-10, FIRMA-2) — deja
        sin efecto la excepción VIGENTE de un rol (se declaró por error, o la parte sí puede
        firmar).

        La revocación es PRUEBA, igual que la constancia: XML canónico (C14N exclusiva) ligado a la
        huella de la constancia que revoca, con su `causa` de vocabulario cerrado, sello
        institucional XAdES-T y —si quien revoca tiene un certificado de firma activo— su firma
        personal, con instantánea de su identidad y compromiso con sal del fundamento. Es una fila NUEVA append-only (`..._excepcion_revocacion`), nunca un
        UPDATE de la constancia, con su asiento en la MISMA tx y su Object-Lock post-commit. Sin
        sello no hay revocación (503 y NADA escrito: ni fila ni asiento). Con ella el trigger de
        exclusión mutua deja de contar la excepción y la parte puede volver a firmar; o se puede
        declarar de nuevo cuando la revocación esté protegida.

        Mismas reglas de acto administrativo que declarar: admin nivel 3 (router), PERM_FIRMA >= 2
        (el sello se estampa a su nombre), no-read-up 404 neutro, y quien revoca no puede ser
        parte de la transferencia (403)."""
        row, _exp, acta, caller_clearance = await self._cargar_acta_recibida(
            transferencia_id, actor.id
        )
        if actor.id in self._partes_de_la_transferencia(row):
            raise await self._denegar_conflicto_de_interes(
                transferencia_id,
                actor,
                rol=payload.rol,
                accion="archive.acta_firma_inobtenible_revocacion_denegada",
            )
        existente = await self._repo.get_ultima_excepcion(transferencia_id, rol=payload.rol)
        if existente is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "excepcion_not_found",
                    "detail": f"El rol '{payload.rol}' no tiene excepción de firma declarada",
                    "status": 404,
                },
            )
        if existente.get("revocada_en") is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "excepcion_ya_revocada",
                    "detail": f"La excepción del rol '{payload.rol}' ya fue revocada",
                    "status": 409,
                },
            )
        await self._repo.set_search_path()
        if not await check_permission(self._conn, actor.id, "PERM_FIRMA", 2):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "revocante_sin_permiso_de_firma",
                    "detail": (
                        "Sellar la revocación exige el permiso PERM_FIRMA (nivel 2 o superior) "
                        "además de la administración: solicítelo o pida que revoque otra persona"
                    ),
                    "status": 403,
                },
            )
        revocante = (await self._resolver_identidades(actor.id))[actor.id]
        if revocante.certificado_activo and not payload.totp_code:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "firma_personal_revocante_requerida",
                    "detail": (
                        "Quien revoca tiene un certificado de firma activo y debe firmar "
                        "personalmente la revocación: envíe 'totp_code'"
                    ),
                    "status": 422,
                },
            )
        huella = huella_de_fundamento(payload.fundamento)

        revocacion_id = uuid.uuid4()
        revocada_en = datetime.now(tz=UTC)
        revocacion_xml = build_revocacion_xml(
            revocacion_id=revocacion_id,
            constancia_id=existente["id"],
            constancia_sha256=existente.get("constancia_sha256"),
            transferencia_id=transferencia_id,
            expediente_id=row["expediente_id"],
            acta_fuid_sha256=acta["acta_fuid_sha256"],
            ordinal=existente["ordinal"],
            rol=payload.rol,
            parte_id=existente["parte_id"],
            causa=payload.causa,
            fundamento=huella,
            revocante=revocante,
            revocada_en=revocada_en,
        )

        from app.clients import signature_client

        sello = await signature_client.sign_xml_outcome(
            tenant_slug=self._tenant,
            actor_id=actor.audit_actor,
            payload_xml=revocacion_xml,
            objeto_tipo="acta_transferencia",
            objeto_id=str(revocacion_id),
            indice_id=None,
            version=int(acta["indice_version"] or 1),
        )
        if sello.signed is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "sello_institucional_no_disponible"
                    if sello.seal_absent
                    else "sellado_revocacion_no_obtenido",
                    "detail": (
                        "No se pudo sellar la revocación con el sello institucional; la "
                        "excepción NO se revocó (una revocación sin firma no es prueba)"
                    ),
                    "status": 503,
                },
            )
        personal: SignedPersonal | None = None
        if payload.totp_code:
            outcome = await signature_client.sign_personal(
                tenant_slug=self._tenant,
                authorization=authorization,
                actor_id=actor.audit_actor,
                payload_xml=revocacion_xml,
                objeto_tipo="acta_firma_excepcion_revocacion",
                objeto_id=str(revocacion_id),
                totp_code=payload.totp_code,
                firmante_id=actor.audit_actor,
            )
            if outcome.signed is not None:
                personal = outcome.signed
            else:
                self._fallo_firma_personal(
                    outcome, rol_opcional=not revocante.certificado_activo
                )

        # Igual que al declarar: el sobre se sube tras validar la firma personal y se purga si la
        # fila no llega a referenciarlo.
        sobre, sobre_desde = await self._sellar_sobre(
            acto="revocacion",
            acto_id=revocacion_id,
            texto=payload.fundamento,
            huella=huella,
            actor=actor,
            indice_version=int(acta["indice_version"] or 1),
        )

        data = {
            "id": revocacion_id,
            "excepcion_id": existente["id"],
            "revocada_por": actor.id,
            "fundamento": payload.fundamento,
            "revocada_en": revocada_en,
            "revocacion_version": REVOCACION_VERSION,
            "revocacion_xml": revocacion_xml,
            "revocacion_sha256": sha256_c14n(revocacion_xml),
            "causa": payload.causa,
            "firma_id": sello.signed.firma_id,
            "minio_key_firmado": sello.signed.minio_key_firmado,
            "xades_level": sello.signed.xades_level,
            "firma_personal_id": personal.firma_id if personal else None,
            "minio_key_firmado_personal": personal.minio_key_firmado if personal else None,
            "xades_level_personal": personal.xades_level if personal else None,
            "revocante_nombre": revocante.nombre,
            "revocante_cargo": revocante.cargo,
            "revocante_fuente": revocante.fuente,
            "fundamento_sal": huella.sal,
            "fundamento_compromiso": huella.compromiso,
            "fundamento_compromiso_algoritmo": huella.algoritmo,
            **sobre,
        }
        try:
            async with self._conn.transaction():
                revocacion = await self._repo.insert_acta_firma_excepcion_revocacion(data)
                if revocacion is not None:
                    await self._repo.insert_firma_excepcion_worm_pendiente(
                        revocacion_id, "sello", acto="revocacion"
                    )
                    await self._repo.insert_firma_excepcion_worm_pendiente(
                        revocacion_id, "sobre", acto="revocacion"
                    )
                    if personal is not None:
                        await self._repo.insert_firma_excepcion_worm_pendiente(
                            revocacion_id, "personal", acto="revocacion"
                        )
                    await audit.append(
                        self._conn,
                        tenant_slug=self._tenant,
                        service=_SERVICE,
                        action="archive.acta_firma_inobtenible_revocada",
                        object_type="transferencia_acta",
                        object_ref=str(transferencia_id),
                        actor=actor.audit_actor,
                        payload={
                            "transferencia_id": str(transferencia_id),
                            "expediente_id": str(row["expediente_id"]),
                            "excepcion_id": str(existente["id"]),
                            "revocacion_id": str(revocacion_id),
                            "ordinal": existente["ordinal"],
                            "rol": payload.rol,
                            "parte_id": str(existente["parte_id"]),
                            "causa": payload.causa,
                            "fundamento_compromiso": huella.compromiso,
                            "fundamento_compromiso_algoritmo": huella.algoritmo,
                            "revocacion_sha256": data["revocacion_sha256"],
                            "sobre_sha256": sobre["sobre_sha256"],
                            "sobre_firma_id": str(sobre["sobre_firma_id"]),
                            "firma_id": str(sello.signed.firma_id),
                            "firma_personal_id": str(personal.firma_id) if personal else None,
                        },
                        canal=audit.CANAL_PETICION,
                    )
        except asyncpg.CheckViolationError as exc:
            await self._purgar_sobre_huerfano(sobre, sobre_desde, contexto="revocar")
            if exc.constraint_name == "ck_firma_excepcion_revocante_es_parte":
                raise await self._denegar_conflicto_de_interes(
                    transferencia_id,
                    actor,
                    rol=payload.rol,
                    accion="archive.acta_firma_inobtenible_revocacion_denegada",
                ) from None
            raise
        except Exception:
            await self._purgar_sobre_huerfano(sobre, sobre_desde, contexto="revocar")
            raise
        if revocacion is None:
            await self._purgar_sobre_huerfano(sobre, sobre_desde, contexto="revocar")
            logger.warning(
                "revocación de firma inobtenible: firma_id=%s huérfana (carrera perdida) "
                "transferencia_id=%s rol=%s",
                sello.signed.firma_id,
                transferencia_id,
                payload.rol,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "excepcion_ya_revocada",
                    "detail": f"La excepción del rol '{payload.rol}' ya fue revocada",
                    "status": 409,
                },
            )

        merged = dict(existente)
        merged.update(
            {
                "revocacion_id": revocacion["id"],
                "revocada_por": revocacion["revocada_por"],
                "revocada_en": revocacion["revocada_en"],
                "revocacion_fundamento": revocacion["fundamento"],
                "revocacion_sha256": revocacion["revocacion_sha256"],
                "revocacion_firma_id": revocacion["firma_id"],
                "revocacion_xades_level": revocacion["xades_level"],
                "revocacion_firma_personal_id": revocacion["firma_personal_id"],
                "revocante_nombre": revocacion["revocante_nombre"],
                "revocante_cargo": revocacion["revocante_cargo"],
                "revocante_fuente": revocacion["revocante_fuente"],
                "revocacion_causa": revocacion["causa"],
                "revocacion_fundamento_sal": revocacion["fundamento_sal"],
                "revocacion_fundamento_compromiso": revocacion["fundamento_compromiso"],
                "revocacion_fundamento_compromiso_algoritmo": revocacion[
                    "fundamento_compromiso_algoritmo"
                ],
                "revocacion_sobre_firma_id": revocacion["sobre_firma_id"],
                "revocacion_sobre_minio_key": revocacion["sobre_minio_key"],
                "revocacion_sobre_sha256": revocacion["sobre_sha256"],
                "revocacion_sobre_xades_level": revocacion["sobre_xades_level"],
                # Item 2 (Baja, seguridad): la respuesta de REVOCAR no devuelve el fundamento ni
                # la sal de la constancia ORIGINAL (texto de OTRO declarante, leído sin asiento de
                # lectura). La vista completa queda en `GET /acta`, que sí asienta.
                "fundamento": None,
                "fundamento_sal": None,
            }
        )
        # Object-Lock post-commit y best-effort; la respuesta dice el estado REAL.
        merged["revocacion_worm_protegido"] = (
            await self.proteger_worm_revocacion(
                revocacion_id, "sello", actor_id=actor.audit_actor, origen="close"
            )
            == "protegido"
        )
        if personal is not None:
            merged["revocacion_worm_personal_protegido"] = (
                await self.proteger_worm_revocacion(
                    revocacion_id, "personal", actor_id=actor.audit_actor, origen="close"
                )
                == "protegido"
            )
        merged["revocacion_worm_sobre_protegido"] = (
            await self.proteger_worm_revocacion(
                revocacion_id, "sobre", actor_id=actor.audit_actor, origen="close"
            )
            == "protegido"
        )
        visibles = await self._referencias_visibles([merged], caller_clearance)
        return self._vista_excepcion(
            merged,
            ver_fundamento=True,
            referencia_visible=str(merged.get("referencia_radicado_id")) in visibles,
        )

    async def acta_verificacion(
        self, transferencia_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> TransferenciaActaVerificacionResponse:
        """`GET /transferencias/{id}/acta/verificacion` (E17/F4, Incremento
        1) — compone la verificación cripto+revocación del sello
        institucional (`verify(sello)`) y de cada firma personal presente
        (`verify(firma personal)`), ON-DEMAND (nunca cacheada). No-read-up
        idéntico a `TransferenciaService.acta` (contenido, no custodia)."""
        row = await self._repo.get(transferencia_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "transferencia_not_found",
                    "detail": str(transferencia_id),
                    "status": 404,
                },
            )
        caller_clearance = await self._archive.caller_clearance(user_id)
        exp = await self._archive.get_expediente_by_id(
            row["expediente_id"], max_clearance=caller_clearance
        )
        if exp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": "No existe el expediente solicitado",
                    "status": 404,
                },
            )
        acta = await self._repo.get_acta(transferencia_id)
        if acta is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "acta_not_found",
                    "detail": (
                        "La transferencia aún no tiene acta de entrega "
                        "(se congela al recibirla)"
                    ),
                    "status": 404,
                },
            )

        firma_row = await self._repo.get_acta_firma(transferencia_id)
        sello_info = await self._build_firma_info(firma_row, verify=True)

        personal_rows = await self._repo.list_acta_firma_personal(transferencia_id)
        firmas_personales: list[TransferenciaActaFirmaPersonalVerif] = []
        for pr in personal_rows:
            valida: bool | None = None
            revocacion_status: str | None = None
            avisos: list[str] = []
            if pr["minio_key_firmado"]:
                from app.clients import signature_client, storage_client

                signed_xml = await storage_client.download_signed_xml(
                    tenant_slug=self._tenant, minio_key_firmado=pr["minio_key_firmado"]
                )
                if signed_xml is not None:
                    resultado = await signature_client.verify(
                        tenant_slug=self._tenant,
                        firma_id=str(pr["firma_id"]),
                        payload_xml=signed_xml,
                    )
                    if resultado is not None:
                        valida = resultado.valida
                        revocacion_status = resultado.revocacion_status
                        avisos = list(resultado.advertencias)
            firmas_personales.append(
                TransferenciaActaFirmaPersonalVerif(
                    rol=pr["rol"],
                    firmante_id=pr["firmante_id"],
                    valida=valida,
                    revocacion_status=revocacion_status,
                    advertencias=avisos,
                )
            )

        # Derivación FACTUAL de la completitud personal (Inc.2): se reporta la
        # presencia real de cada rol — NUNCA se colapsa una firma ya producida
        # a `firma_personal_pendiente` (eso ocultaría un hecho y qué parte
        # firmó). `firma_personal_pendiente` solo aplica cuando NO hay firma
        # alguna y el gate exige alguna para este nivel. Los dos roles se
        # firman en cualquier orden (order-independence), de ahí el espejo
        # `firmada_remitente`/`firmada_receptor`.
        tiene_recibe = any(pr["rol"] == "recibe" for pr in personal_rows)
        tiene_entrega = any(pr["rol"] == "entrega" for pr in personal_rows)
        # Elaborador (Inc.3): firmante ADITIVO, ORTOGONAL a `completitud` (que
        # sigue siendo el eje de las dos PARTES del traspaso, entrega+recibe) —
        # se expone como booleano aparte para no explotar el Literal ni romper
        # el contrato de 5 valores de Inc.2.
        tiene_elabora = any(pr["rol"] == "elabora" for pr in personal_rows)
        # AREA-07-10: constancias de «firma inobtenible». Una PARTE (entrega/recibe) queda
        # CUBIERTA si firmó o tiene una excepción VIGENTE (una revocada ya no cuenta: la parte
        # puede volver a firmar). Con alguna excepción vigente —de cualquier rol, también la del
        # elaborador— el acta NUNCA es `firmada_completa`. Y si ninguna de las dos partes firmó,
        # no se dice `firmada_*`: es `sellada_con_excepcion` (solo conserva el sello de la entidad).
        excepciones = await self._repo.list_acta_firma_excepciones(transferencia_id)
        vigentes = [e for e in excepciones if e.get("revocada_en") is None]
        exc_roles = {e["rol"] for e in vigentes}
        entrega_cubierta = tiene_entrega or "entrega" in exc_roles
        recibe_cubierta = tiene_recibe or "recibe" in exc_roles
        # La política XAdES es la de la ENTIDAD (parámetro del tenant); el entorno solo la
        # respalda cuando la entidad no la definió.
        await self._repo.set_search_path()
        modo_xades = await modo_xades_de_la_entidad(self._conn)
        if tiene_recibe and tiene_entrega and not exc_roles:
            completitud: str = "firmada_completa"
        elif entrega_cubierta and recibe_cubierta:
            completitud = (
                "firmada_con_excepcion"
                if (tiene_entrega or tiene_recibe)
                else "sellada_con_excepcion"
            )
        elif tiene_recibe:
            completitud = "firmada_receptor"
        elif tiene_entrega:
            completitud = "firmada_remitente"
        elif gate_xades_activo(exp.nivel_seguridad, modo_xades):
            completitud = "firma_personal_pendiente"
        else:
            completitud = "sellada"

        # La constancia es PRUEBA y la cadena se verifica COMPLETA: el sello y la firma personal de
        # CADA constancia (también las revocadas: son la evidencia de lo que se declaró) y de CADA
        # revocación. Una constancia legada VIGENTE (sin firma) no se da por válida; una legada ya
        # revocada, ni cuenta (no hay nada que verificar y su baja es un hecho cerrado).
        ver_fundamento = False  # el texto solo sale por `GET .../{excepcion_id}/fundamento`
        visibles = await self._referencias_visibles(excepciones, caller_clearance)
        xmls_cadena = (
            await self._repo.list_xml_cadena(transferencia_id) if excepciones else {}
        )
        por_id = {e["id"]: e for e in excepciones}
        vistas: list[TransferenciaActaFirmaExcepcion] = []
        constancias_ok = True
        constancias_sin_firma = 0
        avisos_constancias: list[str] = []
        cadena_motivos: list[str] = []
        for e in excepciones:
            sello_ok: bool | None = None
            personal_ok: bool | None = None
            rev_sello_ok: bool | None = None
            rev_personal_ok: bool | None = None
            avisos_e: list[str] = []
            huellas_firmadas: dict[str, str | None] = {}
            vigente = e.get("revocada_en") is None
            if e.get("firma_id") is None:
                if vigente:
                    constancias_sin_firma += 1
                    constancias_ok = False
            else:
                sello_ok, aviso_s, huellas_firmadas["sello"] = await self._verificar_xml_firmado(
                    e.get("minio_key_firmado"), e["firma_id"]
                )
                avisos_e += aviso_s
                constancias_ok = constancias_ok and bool(sello_ok)
                if e.get("firma_personal_id") is not None:
                    (
                        personal_ok,
                        aviso_p,
                        huellas_firmadas["personal"],
                    ) = await self._verificar_xml_firmado(
                        e.get("minio_key_firmado_personal"), e["firma_personal_id"]
                    )
                    avisos_e += aviso_p
                    constancias_ok = constancias_ok and bool(personal_ok)
            if e.get("revocacion_firma_id") is not None:
                (
                    rev_sello_ok,
                    aviso_rs,
                    huellas_firmadas["rev_sello"],
                ) = await self._verificar_xml_firmado(
                    e.get("revocacion_minio_key_firmado"), e["revocacion_firma_id"]
                )
                avisos_e += aviso_rs
                constancias_ok = constancias_ok and bool(rev_sello_ok)
                if e.get("revocacion_firma_personal_id") is not None:
                    (
                        rev_personal_ok,
                        aviso_rp,
                        huellas_firmadas["rev_personal"],
                    ) = await self._verificar_xml_firmado(
                        e.get("revocacion_minio_key_firmado_personal"),
                        e["revocacion_firma_personal_id"],
                    )
                    avisos_e += aviso_rp
                    constancias_ok = constancias_ok and bool(rev_personal_ok)
            # A2: los enlaces de la cadena se cotejan contra las filas y los artefactos firmados.
            enlace_ok, motivos_e = self._cotejar_enlaces(
                e, por_id, xmls_cadena.get(e["id"]), huellas_firmadas
            )
            if motivos_e:
                constancias_ok = False
                cadena_motivos += [f"{e['rol']}#{e['ordinal']}: {m}" for m in motivos_e]
            avisos_constancias += avisos_e
            vistas.append(
                self._vista_excepcion(
                    e,
                    ver_fundamento=ver_fundamento,
                    referencia_visible=str(e.get("referencia_radicado_id")) in visibles,
                    sello_valida=sello_ok,
                    firma_personal_valida=personal_ok,
                    revocacion_sello_valida=rev_sello_ok,
                    revocacion_firma_personal_valida=rev_personal_ok,
                    enlace_valido=enlace_ok,
                    enlace_motivo=", ".join(motivos_e) or None,
                    advertencias=avisos_e,
                )
            )

        # `valida` compone la cripto+revocación de TODO lo presente, incluida la
        # firma ADITIVA del elaborador: los dos ejes de ortogonalidad DIFIEREN —
        # el elaborador NO cuenta para `completitud` (traspaso = entrega+recibe)
        # pero SÍ para `valida` (un cert de elaborador revocado → `valida=false`
        # aun con `firmada_completa`). Es fail-closed deliberado: si una firma
        # presente no valida, el veredicto agregado del acta no puede ser True.
        valida_total = (
            bool(sello_info.presente and sello_info.valida)
            and all(f.valida for f in firmas_personales)
            and constancias_ok
        )

        # Auto-traslado (observación de conformidad, dictamen del auditor
        # archivístico): la MISMA identidad envió y recibió → si llega a
        # `firmada_completa`, ambas firmas personales son de la misma persona,
        # SIN control dual (separación de funciones). Se EXPONE el hecho para
        # que un auditor lo vea sin cotejar UUIDs; el par de roles del FUID no
        # exige personas naturales distintas (Ac. AGN 042/2002; el control dual
        # es best-practice MECI/ISO 15489, no prohibición norma-nivel), así que
        # NO se impide — solo se señala. `enviada_por`/`decidida_por` son
        # no-NULL en un acta ya congelada (estado 'recibida').
        auto_traslado = (
            row["enviada_por"] is not None
            and row["enviada_por"] == row["decidida_por"]
        )

        return TransferenciaActaVerificacionResponse(
            sello=sello_info,
            firmas_personales=firmas_personales,
            completitud=completitud,
            valida=valida_total,
            acreditado=False,
            auto_traslado=auto_traslado,
            elaborador_firmado=tiene_elabora,
            excepciones=vistas,
            advertencias=_sin_repetir(
                [
                    *sello_info.advertencias,
                    *(a for f in firmas_personales for a in f.advertencias),
                    *avisos_constancias,
                ]
            ),
            constancias_sin_firma=constancias_sin_firma,
            cadena_motivos=cadena_motivos,
            politica_xades=modo_xades,
        )

    async def _verificar_xml_firmado(
        self, minio_key_firmado: str | None, firma_id: uuid.UUID
    ) -> tuple[bool, builtins.list[str], str | None]:
        """Descarga el XML firmado y lo verifica contra signature-service (on-demand). Devuelve
        `(valida, advertencias, huella)`; `huella` es la C14N-SHA256 del contenido firmado sin la
        firma envolvente (A2), `None` si no se pudo obtener. Todo lo que no se pudo comprobar (sin
        XML, servicio caído) es `valida=False`: nunca se declara conforme lo que no se verificó
        (RF-FIR-15)."""
        if not minio_key_firmado:
            return False, [], None
        from app.clients import signature_client, storage_client

        signed_xml = await storage_client.download_signed_xml(
            tenant_slug=self._tenant, minio_key_firmado=minio_key_firmado
        )
        if signed_xml is None:
            return False, [], None
        huella = huella_de_firmado(signed_xml)
        resultado = await signature_client.verify(
            tenant_slug=self._tenant, firma_id=str(firma_id), payload_xml=signed_xml
        )
        if resultado is None:
            return False, [], huella
        return bool(resultado.valida), list(resultado.advertencias), huella

    @staticmethod
    def _cotejar_enlaces(
        e,
        por_id: dict,
        xmls,
        huellas_firmadas: dict[str, str | None],
    ) -> tuple[bool | None, builtins.list[str]]:
        """A2 — cotejo de los enlaces de UN eslabón de la cadena contra las filas y los
        artefactos. Devuelve `(enlace_valido, motivos)`: `None` = no comprobable (constancia
        anterior a la v3 o sin XML guardado); `False` y motivos si algo no cuadra.

        Cotejos: huella guardada == C14N-SHA256 del XML guardado; el contenido de cada artefacto
        firmado (sin la firma envolvente) == esa huella; el XML nombra la constancia, el ordinal y
        el compromiso de la fila; `sustituyeA`/`revocacionSha256` == el eslabón anterior y la
        huella de SU revocación; la revocación nombra la constancia y su huella. Un enlace roto es
        un motivo que fuerza `valida=false` en el acta."""
        version = int(e.get("constancia_version") or 1)
        cxml = xmls["constancia_xml"] if xmls else None
        rxml = xmls["revocacion_xml"] if xmls else None
        if version < 3 or not cxml:
            return None, []
        motivos: builtins.list[str] = []

        def _c14n(xml: str) -> str | None:
            try:
                return sha256_c14n(xml)
            except Exception:  # noqa: BLE001 — un XML ilegible es un motivo, no un 500.
                return None

        huella = _c14n(cxml)
        if huella is None:
            return False, ["constancia_xml_ilegible"]
        if huella != e.get("constancia_sha256"):
            motivos.append("constancia_huella_distinta")
        for clave in ("sello", "personal"):
            h = huellas_firmadas.get(clave)
            if h is not None and h != e.get("constancia_sha256"):
                motivos.append(f"artefacto_{clave}_no_firma_el_xml_guardado")
        enl = enlaces_de_constancia(cxml)
        if enl is None:
            return False, [*motivos, "constancia_xml_ilegible"]
        if enl["constancia_id"] != str(e["id"]):
            motivos.append("constancia_id_distinto")
        if enl["ordinal"] != str(e["ordinal"]):
            motivos.append("ordinal_distinto")
        if enl["compromiso"] != e.get("fundamento_compromiso"):
            motivos.append("compromiso_xml_distinto")
        previo_id = e.get("sustituye_a")
        if (enl["sustituye_a"] or None) != (str(previo_id) if previo_id else None):
            motivos.append("sustituyeA_no_coincide")
        if previo_id is not None:
            previo = por_id.get(previo_id)
            if previo is None or previo.get("rol") != e["rol"]:
                motivos.append("sustituida_inexistente")
            else:
                if previo.get("ordinal") != e["ordinal"] - 1:
                    motivos.append("ordinal_no_consecutivo")
                if previo.get("revocada_en") is None:
                    motivos.append("sustituida_no_revocada")
                elif enl["revocacion_sha256"] != previo.get("revocacion_sha256"):
                    motivos.append("revocacionSha256_no_coincide")
                if previo.get("revocacion_worm_protegido") is not True:
                    motivos.append("revocacion_sustituida_sin_worm")
        if e.get("revocada_en") is not None and int(e.get("revocacion_version") or 1) >= 3:
            if not rxml:
                motivos.append("revocacion_xml_ausente")
            else:
                rhuella = _c14n(rxml)
                renl = enlaces_de_revocacion(rxml)
                if rhuella is None or renl is None:
                    motivos.append("revocacion_xml_ilegible")
                else:
                    if rhuella != e.get("revocacion_sha256"):
                        motivos.append("revocacion_huella_distinta")
                    for clave in ("rev_sello", "rev_personal"):
                        h = huellas_firmadas.get(clave)
                        if h is not None and h != e.get("revocacion_sha256"):
                            motivos.append(f"artefacto_{clave}_no_firma_el_xml_guardado")
                    if renl["constancia_id"] != str(e["id"]):
                        motivos.append("revocacion_no_nombra_la_constancia")
                    if renl["constancia_sha256"] != e.get("constancia_sha256"):
                        motivos.append("constanciaSha256_no_coincide")
                    if renl["causa"] != e.get("revocacion_causa"):
                        motivos.append("causa_xml_distinta")
                    if renl["compromiso"] != e.get("revocacion_fundamento_compromiso"):
                        motivos.append("compromiso_revocacion_xml_distinto")
        return (not motivos), motivos

    # -- E10 Inc.2 wiring: cableado WORM/Object-Lock del acta -----------
    #
    # Espejo EXACTO de `IndexService.proteger_worm_indice` (E10 Inc.1, ver ese
    # docstring para el contrato completo) -- fast-follow declarado en el
    # diseño de Inc.1 ("Acta = generalizar"). Reusa la ruta generalizada de
    # storage-service (`POST /proteger-artefacto[-internal]`, `tipo=
    # 'acta_transferencia'`) vía `app.clients.storage_client.
    # proteger_artefacto_worm[_internal]`.

    async def _resolve_worm_retention_acta(
        self,
        transferencia_id: uuid.UUID,
        expediente_id: uuid.UUID,
        *,
        firmado_at: datetime | None,
    ) -> tuple[int, date] | None:
        """Deriva ``(retencion_anios_total, fecha_inicio)`` de la TRD del
        EXPEDIENTE de la transferencia para el cableado WORM del acta (E10
        Inc.2 wiring, AGN 001/2024 Art. 4.3.2.6) -- MISMA fórmula que
        `IndexService._resolve_worm_retention` (suma de las dos fases,
        `archivo_gestion_years` + `archivo_central_years`), pero con
        ``fecha_inicio`` = la fecha de FIRMA del acta (``firmado_at``), NO la
        de cierre del expediente: el acta puede sellarse mucho después del
        cierre (transferencia primaria/secundaria, Ley 594/2000 art. 23) --
        anclar la retención al cierre del expediente subestimaría el plazo
        real de custodia de ESTE artefacto derivado.

        Fail-closed (``None``) en cualquier caso dudoso: ``firmado_at``
        ausente (no debería ocurrir con `estado='firmado'`, ver
        `TransferenciaRepository.mark_acta_firmada`, pero no se asume),
        expediente inexistente, sin `trd_serie_id`, serie no encontrada, o el
        total fuera del rango ``[1, 100]`` años que exige `RetentionMixin` del
        lado de storage-service -- misma defensa en profundidad que su gemela
        del índice.

        ADR-026 (Increment 3, D4, AREA-06-04): para `exp.origen == 'fondo_acumulado'`
        la base es su `fecha_extrema_final` con el clamp de
        `retention_math.base_worm`, no `firmado_at`. Antes devolvía `None` y el acta
        de un fondo acumulado no tenía WORM nunca.
        """
        if firmado_at is None:
            return None
        exp = await self._archive.get_expediente_by_id(
            expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
        )
        if exp is None or exp.trd_serie_id is None:
            return None
        serie = await self._archive.get_trd_serie_by_id(exp.trd_serie_id)
        if serie is None:
            return None
        total = total_trd_years(serie.archivo_gestion_years, serie.archivo_central_years)
        if total is None:
            return None
        # Corriente: la fecha de FIRMA del acta (ver arriba). Fondo acumulado: su
        # `fecha_extrema_final` con el clamp de D4.4 (AREA-06-04), nunca `firmado_at`.
        inicio = base_worm(
            origen=exp.origen,
            closed_at=firmado_at,
            fecha_extrema_final=exp.fecha_extrema_final,
            total_years=total,
            margen_dias=settings.worm_fondo_acumulado_margen_dias,
        )
        if inicio is None:
            return None
        return total, inicio

    async def _resolve_ct_retencion_anios_acta(self, expediente_id: uuid.UUID) -> int | None:
        """Total TRD (ambas fases) del EXPEDIENTE de una transferencia --
        versión de RENOVACIÓN (E10 debt) de `_resolve_worm_retention_acta`:
        la ventana rodante de renovación SIEMPRE usa `fecha_inicio=hoy` (no
        `firmado_at`), así que este helper no exige ese parámetro.

        ADR-026 (Increment 3, D4): sin guard para fondo acumulado (la ventana
        rodante parte de hoy)."""
        exp = await self._archive.get_expediente_by_id(
            expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
        )
        if exp is None or exp.trd_serie_id is None:
            return None
        serie = await self._archive.get_trd_serie_by_id(exp.trd_serie_id)
        if serie is None:
            return None
        return total_trd_years(serie.archivo_gestion_years, serie.archivo_central_years)

    async def _bump_worm_backoff_acta(
        self, transferencia_id: uuid.UUID, *, intentos_previos: int, motivo: str
    ) -> Literal["pendiente", "agotado"]:
        """Registra un intento fallido de cableado WORM del acta (backoff
        exponencial, MISMO umbral `indice_reconciliation_max_intentos` que el
        índice -- eje independiente, ver migración 030) -- gemelo de
        `IndexService._bump_worm_backoff`. `intentos_previos` viene del `row`
        YA leído por el llamador (`worm_reconcile_intentos`) -- no se
        re-consulta."""
        intentos_tras_este = intentos_previos + 1
        agotado = intentos_tras_este >= settings.indice_reconciliation_max_intentos
        proximo_at = (
            None
            if agotado
            else datetime.now(tz=UTC)
            + timedelta(
                seconds=next_backoff_seconds(
                    intentos_tras_este,
                    base=settings.indice_reconciliation_backoff_base_seconds,
                    cap=settings.indice_reconciliation_backoff_cap_seconds,
                )
            )
        )
        await self._repo.bump_acta_worm_reconcile_intento(
            transferencia_id, proximo_at=proximo_at, ultimo_error=motivo, agotado=agotado
        )
        if agotado:
            # Best-effort (mismo patrón que `audit.append_denial`): la
            # visibilidad operacional de un agotamiento nunca debe convertir
            # un fallo de cableado (ya de por sí best-effort) en un 500.
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action="archive.acta_worm_reconcile_agotado",
                    object_type="transferencia_acta",
                    object_ref=str(transferencia_id),
                    # Acto de sistema (reconciliación): principal de sistema, no
                    # `None` (deuda E10, unificación de actor).
                    actor=str(SYSTEM_RECONCILER_ID),
                    canal="system",
                    payload={"reconcile_intentos": intentos_tras_este, "motivo": motivo},
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca degrada el resultado.
                logger.warning(
                    "audit.append(archive.acta_worm_reconcile_agotado) failed", exc_info=True
                )
            return "agotado"
        return "pendiente"

    async def proteger_worm_acta(
        self,
        transferencia_id: uuid.UUID,
        *,
        actor_id: str | None,
        origen: Literal["close", "reconciliation"],
    ) -> Literal["protegido", "pendiente", "agotado", "omitido"]:
        """Cableado WORM/Object-Lock del acta de transferencia YA FIRMADA (E10
        Inc.2 wiring, AGN 001/2024 Art. 4.3.2.6) — espejo EXACTO de
        `IndexService.proteger_worm_indice` (ver ese docstring para el
        contrato completo): tras confirmarse el sellado XAdES del acta —
        síncronamente dentro de `_sellar_acta_post_commit` (`origen="close"`,
        cubre TANTO el camino automático de `recibir()` como el reintento
        manual `firmar_acta_retry()`, que comparten ese único método) o vía
        la 4ª fase de reconciliación (`run_once_actas_worm`,
        `app/jobs/indice_reconciliation.py`, `origen="reconciliation"`).

        Best-effort, NUNCA lanza — mismo patrón que su gemelo del índice: el
        sellado del acta ya es un hecho consumado, este cableado NUNCA lo
        bloquea ni lo revierte. Fuera de cualquier `conn.transaction()` del
        llamador — la llamada HTTP a storage-service no debe correr dentro de
        una tx.

        Frontera de confianza (D-02, igual que el índice): `file_id` se
        deriva SIEMPRE de `transferencia_acta_firma.minio_key_firmado`
        (formato ``"{file_id}/{filename}"``), nunca de una entrada externa.

        Devuelve el desenlace para que el llamador (job de reconciliación)
        lleve su propio resumen; el disparo síncrono ignora el valor de
        retorno (mismo criterio que `firmar_indice_close`).
        """
        try:
            firma_row = await self._repo.get_acta_firma_worm(transferencia_id)
            if firma_row is None or firma_row["estado"] != "firmado":
                return "omitido"
            if firma_row["worm_protegido_at"] is not None:
                return "omitido"
            if not firma_row["minio_key_firmado"]:
                logger.warning(
                    "Cableado WORM: acta de transferencia_id=%s sin minio_key_firmado, "
                    "se omite",
                    transferencia_id,
                )
                return "omitido"
            file_id = firma_row["minio_key_firmado"].split("/", 1)[0]

            transferencia = await self._repo.get(transferencia_id)
            if transferencia is None:
                return "omitido"

            retention = await self._resolve_worm_retention_acta(
                transferencia_id,
                transferencia["expediente_id"],
                firmado_at=firma_row["firmado_at"],
            )
            if retention is None:
                return await self._bump_worm_backoff_acta(
                    transferencia_id,
                    intentos_previos=int(firma_row["worm_reconcile_intentos"]),
                    motivo="trd_sin_retencion",
                )
            retencion_anios, fecha_inicio = retention

            from app.clients import storage_client

            expediente_id_str = str(transferencia["expediente_id"])
            if origen == "close" and actor_id:
                outcome = await storage_client.proteger_artefacto_worm(
                    tenant_slug=self._tenant,
                    actor_id=actor_id,
                    file_id=file_id,
                    expediente_id=expediente_id_str,
                    tipo="acta_transferencia",
                    retencion_anios=retencion_anios,
                    fecha_inicio=fecha_inicio,
                )
            else:
                outcome = await storage_client.proteger_artefacto_worm_internal(
                    tenant_slug=self._tenant,
                    file_id=file_id,
                    expediente_id=expediente_id_str,
                    tipo="acta_transferencia",
                    retencion_anios=retencion_anios,
                    fecha_inicio=fecha_inicio,
                )

            if outcome.ok:
                async with self._conn.transaction():
                    affected = await self._repo.mark_acta_worm_protegido(
                        transferencia_id,
                        outcome.protected_object_id,
                        worm_retain_until=outcome.retain_until,
                    )
                    # Idempotencia real (mismo patrón S1 que `mark_acta_firmada`):
                    # `affected == 0` -> otra llamada concurrente (el disparo
                    # síncrono post-commit corriendo en paralelo con esta
                    # barrida, o dos barridas solapadas) ya marcó protegido
                    # ESTA misma acta. NO se escribe un asiento fantasma sobre
                    # un efecto que esta llamada no produjo.
                    if affected:
                        await audit.append(
                            self._conn,
                            tenant_slug=self._tenant,
                            service=_SERVICE,
                            action="archive.acta_worm_protegido",
                            object_type="transferencia_acta",
                            object_ref=str(transferencia_id),
                            actor=actor_id,
                            canal="system" if origen == "reconciliation" else "api",
                            payload={
                                "transferencia_id": str(transferencia_id),
                                "expediente_id": expediente_id_str,
                                "worm_object_id": (
                                    str(outcome.worm_object_id)
                                    if outcome.worm_object_id
                                    else None
                                ),
                                "retencion_anios": retencion_anios,
                                "origen": origen,
                            },
                        )
                return "protegido" if affected else "omitido"

            motivo = "worm_disabled" if outcome.disabled else "worm_no_confirmado"
            return await self._bump_worm_backoff_acta(
                transferencia_id,
                intentos_previos=int(firma_row["worm_reconcile_intentos"]),
                motivo=motivo,
            )
        except Exception:
            logger.warning(
                "Cableado WORM del acta falló (best-effort), transferencia_id=%s origen=%s",
                transferencia_id,
                origen,
                exc_info=True,
            )
            return "pendiente"

    # -- AREA-07-10: WORM de las firmas PERSONALES del acta ----------------

    async def _bump_worm_backoff_firma_personal(
        self, firma_personal_id: uuid.UUID, *, intentos_previos: int, motivo: str
    ) -> Literal["pendiente", "agotado"]:
        """Gemelo de `_bump_worm_backoff_acta` para el eje de la firma personal."""
        intentos_tras_este = intentos_previos + 1
        agotado = intentos_tras_este >= settings.indice_reconciliation_max_intentos
        proximo_at = (
            None
            if agotado
            else datetime.now(tz=UTC)
            + timedelta(
                seconds=next_backoff_seconds(
                    intentos_tras_este,
                    base=settings.indice_reconciliation_backoff_base_seconds,
                    cap=settings.indice_reconciliation_backoff_cap_seconds,
                )
            )
        )
        await self._repo.bump_firma_personal_worm_intento(
            firma_personal_id, proximo_at=proximo_at, ultimo_error=motivo, agotado=agotado
        )
        if agotado:
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action="archive.acta_firma_personal_worm_reconcile_agotado",
                    object_type="transferencia_acta_firma_personal",
                    object_ref=str(firma_personal_id),
                    actor=str(SYSTEM_RECONCILER_ID),
                    canal="system",
                    payload={"reconcile_intentos": intentos_tras_este, "motivo": motivo},
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca degrada el resultado.
                logger.warning(
                    "audit.append(acta_firma_personal_worm_reconcile_agotado) failed",
                    exc_info=True,
                )
            return "agotado"
        return "pendiente"

    async def proteger_worm_firma_personal(
        self,
        firma_personal_id: uuid.UUID,
        *,
        actor_id: str | None,
        origen: Literal["close", "reconciliation"],
    ) -> Literal["protegido", "pendiente", "agotado", "omitido"]:
        """Object-Lock del XML de UNA firma personal del acta (AREA-07-10) — gemelo de
        `proteger_worm_acta`: best-effort, NUNCA lanza, fuera de toda `conn.transaction()`
        (HTTP), `file_id` derivado SIEMPRE de `minio_key_firmado` (nunca de una entrada
        externa) y `tipo` = `acta_firma_<rol>` (storage, migración 018: un tipo por rol, porque
        la protección es singleton por (expediente, tipo)).

        La retención se resuelve como la del acta (misma serie TRD, mismo clamp para un fondo
        acumulado) con la fecha de la propia firma (`creada_en`) como base.

        No toca la excepción «firma inobtenible» (`declarar_firma_inobtenible`): esto solo
        protege lo que SÍ se firmó."""
        try:
            fila = await self._repo.get_firma_personal_worm(firma_personal_id)
            if fila is None or fila["worm_protegido_at"] is not None:
                return "omitido"
            if not fila["minio_key_firmado"]:
                logger.warning(
                    "WORM de firma personal %s sin minio_key_firmado, se omite", firma_personal_id
                )
                return "omitido"
            file_id = fila["minio_key_firmado"].split("/", 1)[0]
            retention = await self._resolve_worm_retention_acta(
                fila["transferencia_id"], fila["expediente_id"], firmado_at=fila["creada_en"]
            )
            intentos = int(fila["worm_reconcile_intentos"] or 0)
            if retention is None:
                return await self._bump_worm_backoff_firma_personal(
                    firma_personal_id, intentos_previos=intentos, motivo="trd_sin_retencion"
                )
            retencion_anios, fecha_inicio = retention

            from app.clients import storage_client

            expediente_id_str = str(fila["expediente_id"])
            tipo = f"acta_firma_{fila['rol']}"
            if origen == "close" and actor_id:
                outcome = await storage_client.proteger_artefacto_worm(
                    tenant_slug=self._tenant,
                    actor_id=actor_id,
                    file_id=file_id,
                    expediente_id=expediente_id_str,
                    tipo=tipo,
                    retencion_anios=retencion_anios,
                    fecha_inicio=fecha_inicio,
                )
            else:
                outcome = await storage_client.proteger_artefacto_worm_internal(
                    tenant_slug=self._tenant,
                    file_id=file_id,
                    expediente_id=expediente_id_str,
                    tipo=tipo,
                    retencion_anios=retencion_anios,
                    fecha_inicio=fecha_inicio,
                )

            if outcome.ok:
                async with self._conn.transaction():
                    affected = await self._repo.mark_firma_personal_worm_protegido(
                        firma_personal_id,
                        outcome.protected_object_id,
                        worm_retain_until=outcome.retain_until,
                    )
                    if affected:
                        await audit.append(
                            self._conn,
                            tenant_slug=self._tenant,
                            service=_SERVICE,
                            action="archive.acta_firma_personal_worm_protegido",
                            object_type="transferencia_acta_firma_personal",
                            object_ref=str(firma_personal_id),
                            actor=actor_id,
                            canal="system" if origen == "reconciliation" else "api",
                            payload={
                                "transferencia_id": str(fila["transferencia_id"]),
                                "expediente_id": expediente_id_str,
                                "rol": fila["rol"],
                                "worm_object_id": (
                                    str(outcome.worm_object_id) if outcome.worm_object_id else None
                                ),
                                "retencion_anios": retencion_anios,
                                "origen": origen,
                            },
                        )
                return "protegido" if affected else "omitido"

            motivo = "worm_disabled" if outcome.disabled else "worm_no_confirmado"
            return await self._bump_worm_backoff_firma_personal(
                firma_personal_id, intentos_previos=intentos, motivo=motivo
            )
        except Exception:
            logger.warning(
                "WORM de la firma personal falló (best-effort), firma_personal_id=%s origen=%s",
                firma_personal_id,
                origen,
                exc_info=True,
            )
            return "pendiente"

    # -- E10 debt: renovación de retención WORM del acta para series CT --
    #
    # Espejo EXACTO de `IndexService._bump_worm_renovacion_backoff`/
    # `renovar_worm_indice` -- ver esos docstrings para el contrato completo.

    async def _bump_worm_renovacion_backoff_acta(
        self, transferencia_id: uuid.UUID, *, intentos_previos: int, motivo: str
    ) -> Literal["pendiente", "agotado"]:
        intentos_tras_este = intentos_previos + 1
        agotado = intentos_tras_este >= settings.indice_reconciliation_max_intentos
        proximo_at = (
            None
            if agotado
            else datetime.now(tz=UTC)
            + timedelta(
                seconds=next_backoff_seconds(
                    intentos_tras_este,
                    base=settings.indice_reconciliation_backoff_base_seconds,
                    cap=settings.indice_reconciliation_backoff_cap_seconds,
                )
            )
        )
        await self._repo.bump_acta_worm_renovacion_intento(
            transferencia_id, proximo_at=proximo_at, ultimo_error=motivo, agotado=agotado
        )
        if agotado:
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action="archive.acta_worm_renovacion_agotado",
                    object_type="transferencia_acta",
                    object_ref=str(transferencia_id),
                    actor=str(SYSTEM_RECONCILER_ID),
                    canal="system",
                    payload={"renovacion_intentos": intentos_tras_este, "motivo": motivo},
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca degrada el resultado.
                logger.warning(
                    "audit.append(archive.acta_worm_renovacion_agotado) failed", exc_info=True
                )
            return "agotado"
        return "pendiente"

    async def renovar_worm_acta(
        self,
        transferencia_id: uuid.UUID,
        expediente_id: uuid.UUID,
        *,
        intentos_previos: int,
    ) -> Literal["renovado", "omitido", "pendiente", "agotado"]:
        """Extiende (E10 debt) la retención WORM del acta de un expediente de
        serie CT ANTES de que su `retain_until` vigente venza -- gemelo
        EXACTO de `IndexService.renovar_worm_indice`. `expediente_id` llega
        YA resuelto por el llamador (`TransferenciaRepository.
        list_worm_renovacion_pending` lo proyecta vía el JOIN a
        `transferencias`) -- no se re-consulta aquí."""
        try:
            retencion_anios = await self._resolve_ct_retencion_anios_acta(expediente_id)
            if retencion_anios is None:
                return await self._bump_worm_renovacion_backoff_acta(
                    transferencia_id, intentos_previos=intentos_previos, motivo="trd_sin_retencion"
                )

            from app.clients import storage_client

            outcome = await storage_client.renovar_retencion_worm_internal(
                tenant_slug=self._tenant,
                expediente_id=str(expediente_id),
                tipo="acta_transferencia",
                retencion_anios=retencion_anios,
                fecha_inicio=datetime.now(tz=UTC).date(),
            )

            if outcome.ok:
                assert outcome.retain_until is not None
                async with self._conn.transaction():
                    await self._repo.mark_acta_worm_renovado(
                        transferencia_id, worm_retain_until=outcome.retain_until
                    )
                    if outcome.renovado:
                        await audit.append(
                            self._conn,
                            tenant_slug=self._tenant,
                            service=_SERVICE,
                            action="archive.acta_worm_retencion_renovada",
                            object_type="transferencia_acta",
                            object_ref=str(transferencia_id),
                            actor=str(SYSTEM_RECONCILER_ID),
                            canal="system",
                            payload={
                                "transferencia_id": str(transferencia_id),
                                "expediente_id": str(expediente_id),
                                "retencion_anios": retencion_anios,
                                "retain_until": outcome.retain_until.isoformat(),
                            },
                        )
                return "renovado" if outcome.renovado else "omitido"

            motivo = (
                "worm_disabled"
                if outcome.disabled
                else "worm_object_not_found"
                if outcome.not_found
                else "worm_no_confirmado"
            )
            return await self._bump_worm_renovacion_backoff_acta(
                transferencia_id, intentos_previos=intentos_previos, motivo=motivo
            )
        except Exception:
            logger.warning(
                "Renovación de retención WORM del acta falló (best-effort), "
                "transferencia_id=%s expediente_id=%s",
                transferencia_id,
                expediente_id,
                exc_info=True,
            )
            return "pendiente"
    async def _bump_worm_renovacion_backoff_firma_personal(
        self, firma_personal_id: uuid.UUID, *, intentos_previos: int, motivo: str
    ) -> Literal["pendiente", "agotado"]:
        intentos_tras_este = intentos_previos + 1
        agotado = intentos_tras_este >= settings.indice_reconciliation_max_intentos
        proximo_at = (
            None
            if agotado
            else datetime.now(tz=UTC)
            + timedelta(
                seconds=next_backoff_seconds(
                    intentos_tras_este,
                    base=settings.indice_reconciliation_backoff_base_seconds,
                    cap=settings.indice_reconciliation_backoff_cap_seconds,
                )
            )
        )
        await self._repo.bump_firma_personal_worm_renovacion_intento(
            firma_personal_id, proximo_at=proximo_at, ultimo_error=motivo, agotado=agotado
        )
        if agotado:
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action="archive.acta_firma_personal_worm_renovacion_agotado",
                    object_type="transferencia_acta_firma_personal",
                    object_ref=str(firma_personal_id),
                    actor=str(SYSTEM_RECONCILER_ID),
                    canal="system",
                    payload={"renovacion_intentos": intentos_tras_este, "motivo": motivo},
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca degrada el resultado.
                logger.warning(
                    "audit.append(archive.acta_firma_personal_worm_renovacion_agotado) failed",
                    exc_info=True,
                )
            return "agotado"
        return "pendiente"

    async def renovar_worm_firma_personal(
        self,
        firma_personal_id: uuid.UUID,
        transferencia_id: uuid.UUID,
        expediente_id: uuid.UUID,
        rol: str,
        *,
        intentos_previos: int,
    ) -> Literal["renovado", "omitido", "pendiente", "agotado"]:
        """Extiende (E10 debt, AREA-07-10) la retención WORM del XML de UNA firma personal del
        acta de un expediente de serie CT antes de que venza — gemelo de `renovar_worm_acta`
        con `tipo='acta_firma_<rol>'` (storage identifica la fila por `(expediente_id, tipo)`;
        la protección es singleton por ese par, migración 018 de storage). Monotónica: storage
        solo extiende si la fecha resuelta supera la vigente (si no, `omitido`) y el espejo local
        usa `GREATEST`. Cada extensión real deja asiento (actor SISTEMA) atómico con el espejo."""
        try:
            retencion_anios = await self._resolve_ct_retencion_anios_acta(expediente_id)
            if retencion_anios is None:
                return await self._bump_worm_renovacion_backoff_firma_personal(
                    firma_personal_id,
                    intentos_previos=intentos_previos,
                    motivo="trd_sin_retencion",
                )

            from app.clients import storage_client

            outcome = await storage_client.renovar_retencion_worm_internal(
                tenant_slug=self._tenant,
                expediente_id=str(expediente_id),
                tipo=f"acta_firma_{rol}",
                retencion_anios=retencion_anios,
                fecha_inicio=datetime.now(tz=UTC).date(),
            )

            if outcome.ok:
                assert outcome.retain_until is not None
                async with self._conn.transaction():
                    await self._repo.mark_firma_personal_worm_renovado(
                        firma_personal_id, worm_retain_until=outcome.retain_until
                    )
                    if outcome.renovado:
                        await audit.append(
                            self._conn,
                            tenant_slug=self._tenant,
                            service=_SERVICE,
                            action="archive.acta_firma_personal_worm_retencion_renovada",
                            object_type="transferencia_acta_firma_personal",
                            object_ref=str(firma_personal_id),
                            actor=str(SYSTEM_RECONCILER_ID),
                            canal="system",
                            payload={
                                "transferencia_id": str(transferencia_id),
                                "expediente_id": str(expediente_id),
                                "rol": rol,
                                "retencion_anios": retencion_anios,
                                "retain_until": outcome.retain_until.isoformat(),
                            },
                        )
                return "renovado" if outcome.renovado else "omitido"

            motivo = (
                "worm_disabled"
                if outcome.disabled
                else "worm_object_not_found"
                if outcome.not_found
                else "worm_no_confirmado"
            )
            return await self._bump_worm_renovacion_backoff_firma_personal(
                firma_personal_id, intentos_previos=intentos_previos, motivo=motivo
            )
        except Exception:
            logger.warning(
                "Renovación de retención WORM de la firma personal falló (best-effort), "
                "firma_personal_id=%s expediente_id=%s",
                firma_personal_id,
                expediente_id,
                exc_info=True,
            )
            return "pendiente"

    # -- AREA-07-10 / FIRMA-2: WORM de la CONSTANCIA de «firma inobtenible» y de su REVOCACIÓN --
    #
    # Dos artefactos por acto (`artefacto`): el XML sellado por la entidad ('sello') y el firmado
    # por quien actúa ('personal'). Tipos en storage (migraciones 019/020):
    # `acta_firma_excepcion[_personal]_<rol>` y `acta_firma_revocacion[_personal]_<rol>`. Espejo
    # EXACTO de `proteger_worm_firma_personal` / `renovar_worm_firma_personal`, con una diferencia
    # que importa: son tipos MULTI-ARTEFACTO (una cadena de constancias y revocaciones por rol),
    # así que en storage se identifican por `(expediente, tipo, file_id)` y el `file_id` viaja
    # siempre —también en la renovación—, derivado de `minio_key_firmado`.

    @staticmethod
    def _tipo_worm_excepcion(artefacto: str, rol: str, acto: str = "excepcion") -> str:
        prefijo = f"acta_firma_{acto}_" + {"personal": "personal_", "sobre": "sobre_"}.get(
            artefacto, ""
        )
        return f"{prefijo}{rol}"

    @staticmethod
    def _meta_acto(acto: str) -> dict[str, str]:
        """Nombres de asiento y tipo de objeto por acto (constancia / revocación)."""
        if acto == "revocacion":
            return {
                "object_type": "transferencia_acta_firma_excepcion_revocacion",
                "prefijo": "archive.acta_firma_revocacion_worm",
            }
        return {
            "object_type": "transferencia_acta_firma_excepcion",
            "prefijo": "archive.acta_firma_excepcion_worm",
        }

    async def _bump_worm_backoff_firma_excepcion(
        self,
        acto_id: uuid.UUID,
        artefacto: str,
        *,
        intentos_previos: int,
        motivo: str,
        acto: str = "excepcion",
    ) -> Literal["pendiente", "agotado"]:
        intentos_tras_este = intentos_previos + 1
        agotado = intentos_tras_este >= settings.indice_reconciliation_max_intentos
        proximo_at = (
            None
            if agotado
            else datetime.now(tz=UTC)
            + timedelta(
                seconds=next_backoff_seconds(
                    intentos_tras_este,
                    base=settings.indice_reconciliation_backoff_base_seconds,
                    cap=settings.indice_reconciliation_backoff_cap_seconds,
                )
            )
        )
        kw = {} if acto == "excepcion" else {"acto": acto}
        await self._repo.bump_firma_excepcion_worm_intento(
            acto_id, artefacto, proximo_at=proximo_at, ultimo_error=motivo, agotado=agotado, **kw
        )
        if agotado:
            meta = self._meta_acto(acto)
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action=f"{meta['prefijo']}_reconcile_agotado",
                    object_type=meta["object_type"],
                    object_ref=str(acto_id),
                    actor=str(SYSTEM_RECONCILER_ID),
                    canal="system",
                    payload={
                        "artefacto": artefacto,
                        "reconcile_intentos": intentos_tras_este,
                        "motivo": motivo,
                    },
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca degrada el resultado.
                logger.warning(
                    "audit.append(%s_reconcile_agotado) failed", meta["prefijo"], exc_info=True
                )
            return "agotado"
        return "pendiente"

    async def proteger_worm_firma_excepcion(
        self,
        excepcion_id: uuid.UUID,
        artefacto: Literal["sello", "personal", "sobre"],
        *,
        actor_id: str | None,
        origen: Literal["close", "reconciliation"],
    ) -> Literal["protegido", "pendiente", "agotado", "omitido"]:
        """Object-Lock del XML firmado de UNA constancia (sello o firma personal del
        declarante). Ver `_proteger_worm_acto`."""
        return await self._proteger_worm_acto(
            "excepcion", excepcion_id, artefacto, actor_id=actor_id, origen=origen
        )

    async def proteger_worm_revocacion(
        self,
        revocacion_id: uuid.UUID,
        artefacto: Literal["sello", "personal", "sobre"],
        *,
        actor_id: str | None,
        origen: Literal["close", "reconciliation"],
    ) -> Literal["protegido", "pendiente", "agotado", "omitido"]:
        """Object-Lock del XML firmado de UNA revocación (FIRMA-2). Ver `_proteger_worm_acto`."""
        return await self._proteger_worm_acto(
            "revocacion", revocacion_id, artefacto, actor_id=actor_id, origen=origen
        )

    async def _proteger_worm_acto(
        self,
        acto: Literal["excepcion", "revocacion"],
        acto_id: uuid.UUID,
        artefacto: Literal["sello", "personal", "sobre"],
        *,
        actor_id: str | None,
        origen: Literal["close", "reconciliation"],
    ) -> Literal["protegido", "pendiente", "agotado", "omitido"]:
        """Gemelo de `proteger_worm_firma_personal`: best-effort, NUNCA lanza, fuera de toda
        `conn.transaction()` (HTTP), `file_id` derivado SIEMPRE de `minio_key_firmado`. La
        retención es la del acta (misma serie TRD, mismo clamp para un fondo acumulado) con la
        fecha del acto (declaración o revocación) como base."""
        kw = {} if acto == "excepcion" else {"acto": acto}
        meta = self._meta_acto(acto)
        try:
            fila = await self._repo.get_firma_excepcion_worm(acto_id, artefacto, **kw)
            if fila is None or fila["worm_protegido_at"] is not None:
                return "omitido"
            if not fila["minio_key_firmado"]:
                logger.warning(
                    "WORM de %s %s (%s) sin minio_key_firmado, se omite", acto, acto_id, artefacto
                )
                return "omitido"
            file_id = fila["minio_key_firmado"].split("/", 1)[0]
            intentos = int(fila["worm_reconcile_intentos"] or 0)
            # A3: ancla COMÚN de la cadena del rol (el mayor entre el sellado del acta y el último
            # acto de la cadena), no la fecha de este acto: así ningún eslabón vence antes que el
            # último de los que lo siguen.
            ancla = await self._ancla_cadena(
                fila["transferencia_id"], fila["rol"], fila["actuada_en"]
            )
            retention = await self._resolve_worm_retention_acta(
                fila["transferencia_id"], fila["expediente_id"], firmado_at=ancla
            )
            if retention is None:
                return await self._bump_worm_backoff_firma_excepcion(
                    acto_id,
                    artefacto,
                    intentos_previos=intentos,
                    motivo="trd_sin_retencion",
                    acto=acto,
                )
            retencion_anios, fecha_inicio = retention

            from app.clients import storage_client

            expediente_id_str = str(fila["expediente_id"])
            tipo = self._tipo_worm_excepcion(artefacto, fila["rol"], acto)
            if origen == "close" and actor_id:
                outcome = await storage_client.proteger_artefacto_worm(
                    tenant_slug=self._tenant,
                    actor_id=actor_id,
                    file_id=file_id,
                    expediente_id=expediente_id_str,
                    tipo=tipo,
                    retencion_anios=retencion_anios,
                    fecha_inicio=fecha_inicio,
                )
            else:
                outcome = await storage_client.proteger_artefacto_worm_internal(
                    tenant_slug=self._tenant,
                    file_id=file_id,
                    expediente_id=expediente_id_str,
                    tipo=tipo,
                    retencion_anios=retencion_anios,
                    fecha_inicio=fecha_inicio,
                )

            if outcome.ok:
                async with self._conn.transaction():
                    affected = await self._repo.mark_firma_excepcion_worm_protegido(
                        acto_id,
                        artefacto,
                        outcome.protected_object_id,
                        worm_retain_until=outcome.retain_until,
                        **kw,
                    )
                    if affected:
                        await audit.append(
                            self._conn,
                            tenant_slug=self._tenant,
                            service=_SERVICE,
                            action=f"{meta['prefijo']}_protegido",
                            object_type=meta["object_type"],
                            object_ref=str(acto_id),
                            actor=actor_id,
                            canal="system" if origen == "reconciliation" else "api",
                            payload={
                                "transferencia_id": str(fila["transferencia_id"]),
                                "expediente_id": expediente_id_str,
                                "rol": fila["rol"],
                                "artefacto": artefacto,
                                "tipo": tipo,
                                "file_id": file_id,
                                "worm_object_id": (
                                    str(outcome.worm_object_id) if outcome.worm_object_id else None
                                ),
                                "retencion_anios": retencion_anios,
                                "origen": origen,
                            },
                        )
                if affected:
                    # Un eslabón nuevo (o recién protegido) sube la ancla de la cadena: los
                    # anteriores se alinean a ella (monotónico, con asiento).
                    await self._alinear_cadena(
                        fila["transferencia_id"],
                        fila["expediente_id"],
                        fila["rol"],
                        retencion_anios=retencion_anios,
                        fecha_inicio=fecha_inicio,
                        retain_until=outcome.retain_until,
                        excluir=(acto, acto_id, artefacto),
                        actor_id=actor_id if origen == "close" else None,
                    )
                return "protegido" if affected else "omitido"

            motivo = "worm_disabled" if outcome.disabled else "worm_no_confirmado"
            return await self._bump_worm_backoff_firma_excepcion(
                acto_id, artefacto, intentos_previos=intentos, motivo=motivo, acto=acto
            )
        except Exception:
            logger.warning(
                "WORM de %s falló (best-effort), id=%s artefacto=%s origen=%s",
                acto,
                acto_id,
                artefacto,
                origen,
                exc_info=True,
            )
            return "pendiente"

    async def reintentar_worm_firma_excepcion(
        self,
        transferencia_id: uuid.UUID,
        excepcion_id: uuid.UUID,
        actor: Actor,
        *,
        acto: Literal["excepcion", "revocacion"],
    ) -> WormReintentoResponse:
        """A4 — salida de `agotado` del Object-Lock de la constancia (`acto='excepcion'`) o de su
        revocación (`acto='revocacion'`). Admin nivel 3 (router); 404 neutro por no-read-up del
        expediente. Repone a pendiente los artefactos agotados (protección o renovación) y deja
        su asiento en la MISMA tx; después reintenta la protección de los que siguen sin proteger
        (post-commit, best-effort) y devuelve el estado REAL de cada artefacto. Si el almacén
        sigue sin responder (p. ej. preservación apagada) vuelven a quedar pendientes/agotados:
        es lo correcto."""
        await self._cargar_acta_recibida(transferencia_id, actor.id)
        e = await self._repo.get_excepcion_por_id(transferencia_id, excepcion_id)
        if e is None or (acto == "revocacion" and e.get("revocacion_id") is None):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "excepcion_not_found" if e is None else "revocacion_not_found",
                    "detail": str(excepcion_id),
                    "status": 404,
                },
            )
        acto_id = e["revocacion_id"] if acto == "revocacion" else e["id"]
        meta = self._meta_acto(acto)
        async with self._conn.transaction():
            repuestos = await self._repo.reset_firma_excepcion_worm_agotado(acto_id, acto=acto)
            await audit.append(
                self._conn,
                tenant_slug=self._tenant,
                service=_SERVICE,
                action=f"{meta['prefijo']}_reintento_manual",
                object_type=meta["object_type"],
                object_ref=str(acto_id),
                actor=actor.audit_actor,
                payload={
                    "transferencia_id": str(transferencia_id),
                    "excepcion_id": str(excepcion_id),
                    "rol": e["rol"],
                    "acto": acto,
                    "repuestos": sorted(r["artefacto"] for r in repuestos),
                },
                canal=audit.CANAL_PETICION,
            )
        proteger = (
            self.proteger_worm_revocacion if acto == "revocacion" else self.proteger_worm_firma_excepcion
        )
        estados: dict[str, str] = {}
        for fila in await self._repo.list_firma_excepcion_worm_artefactos(acto_id, acto=acto):
            if fila["protegido"]:
                estados[fila["artefacto"]] = "protegido"
                continue
            estados[fila["artefacto"]] = await proteger(
                acto_id, fila["artefacto"], actor_id=actor.audit_actor, origen="close"
            )
        return WormReintentoResponse(
            excepcion_id=excepcion_id, acto=acto, repuestos=len(repuestos), artefactos=estados
        )

    async def _ancla_cadena(
        self, transferencia_id: uuid.UUID, rol: str, propio: datetime | None
    ) -> datetime | None:
        """Ancla común de retención de la cadena del rol: el MAYOR entre el sellado del acta, el
        instante del último acto de la cadena (declaración o revocación) y el propio. Sin esto cada
        eslabón vencería según su fecha y el primero caducaría antes que su revocación."""
        candidatos: builtins.list[datetime] = []
        if isinstance(propio, datetime):
            candidatos.append(propio)
        ultimo = await self._repo.get_ancla_cadena(transferencia_id, rol)
        if isinstance(ultimo, datetime):
            candidatos.append(ultimo)
        acta_firma = await self._repo.get_acta_firma_worm(transferencia_id)
        firmado = acta_firma.get("firmado_at") if acta_firma is not None else None
        if isinstance(firmado, datetime):
            candidatos.append(firmado)
        return max(candidatos) if candidatos else propio

    async def _alinear_cadena(
        self,
        transferencia_id: uuid.UUID,
        expediente_id: uuid.UUID,
        rol: str,
        *,
        retencion_anios: int,
        fecha_inicio: date,
        retain_until: datetime | None,
        excluir: tuple[str, uuid.UUID, str],
        actor_id: str | None,
    ) -> None:
        """A3 — alinea la retención WORM de TODOS los artefactos ya protegidos de la cadena del
        rol (constancias y revocaciones, sello y personal) con la del eslabón recién protegido:
        mismos años y misma fecha de inicio que él. Storage solo extiende (nunca acorta) y el
        espejo local usa `GREATEST`; cada extensión real deja su asiento. Best-effort: nunca
        lanza (la reconciliación de renovación CT sigue siendo la red de seguridad)."""
        try:
            for p in await self._repo.list_cadena_worm_protegidos(transferencia_id, rol):
                if (p["acto"], p["acto_id"], p["artefacto"]) == excluir:
                    continue
                if (
                    retain_until is not None
                    and p["worm_retain_until"] is not None
                    and p["worm_retain_until"] >= retain_until
                ):
                    continue  # ya llega al menos tan lejos
                await self._renovar_worm_acto(
                    p["acto"],
                    p["acto_id"],
                    p["artefacto"],
                    transferencia_id,
                    expediente_id,
                    rol,
                    intentos_previos=int(p["worm_renovacion_intentos"] or 0),
                    minio_key_firmado=p["minio_key_firmado"],
                    alineacion=(retencion_anios, fecha_inicio),
                    actor_id=actor_id,
                )
        except Exception:  # noqa: BLE001 — best-effort, la protección ya es un hecho.
            logger.warning(
                "alineación de retención de la cadena falló (best-effort), transferencia_id=%s "
                "rol=%s",
                transferencia_id,
                rol,
                exc_info=True,
            )

    async def _bump_worm_renovacion_backoff_firma_excepcion(
        self,
        acto_id: uuid.UUID,
        artefacto: str,
        *,
        intentos_previos: int,
        motivo: str,
        acto: str = "excepcion",
    ) -> Literal["pendiente", "agotado"]:
        intentos_tras_este = intentos_previos + 1
        agotado = intentos_tras_este >= settings.indice_reconciliation_max_intentos
        proximo_at = (
            None
            if agotado
            else datetime.now(tz=UTC)
            + timedelta(
                seconds=next_backoff_seconds(
                    intentos_tras_este,
                    base=settings.indice_reconciliation_backoff_base_seconds,
                    cap=settings.indice_reconciliation_backoff_cap_seconds,
                )
            )
        )
        kw = {} if acto == "excepcion" else {"acto": acto}
        await self._repo.bump_firma_excepcion_worm_renovacion_intento(
            acto_id, artefacto, proximo_at=proximo_at, ultimo_error=motivo, agotado=agotado, **kw
        )
        if agotado:
            meta = self._meta_acto(acto)
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action=f"{meta['prefijo']}_renovacion_agotado",
                    object_type=meta["object_type"],
                    object_ref=str(acto_id),
                    actor=str(SYSTEM_RECONCILER_ID),
                    canal="system",
                    payload={
                        "artefacto": artefacto,
                        "renovacion_intentos": intentos_tras_este,
                        "motivo": motivo,
                    },
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca degrada el resultado.
                logger.warning(
                    "audit.append(%s_renovacion_agotado) failed", meta["prefijo"], exc_info=True
                )
            return "agotado"
        return "pendiente"

    async def renovar_worm_firma_excepcion(
        self,
        excepcion_id: uuid.UUID,
        artefacto: str,
        transferencia_id: uuid.UUID,
        expediente_id: uuid.UUID,
        rol: str,
        *,
        intentos_previos: int,
        minio_key_firmado: str | None,
    ) -> Literal["renovado", "omitido", "pendiente", "agotado"]:
        """Extiende la retención WORM CT del XML de UNA constancia antes de que venza. Ver
        `_renovar_worm_acto`."""
        return await self._renovar_worm_acto(
            "excepcion",
            excepcion_id,
            artefacto,
            transferencia_id,
            expediente_id,
            rol,
            intentos_previos=intentos_previos,
            minio_key_firmado=minio_key_firmado,
        )

    async def renovar_worm_revocacion(
        self,
        revocacion_id: uuid.UUID,
        artefacto: str,
        transferencia_id: uuid.UUID,
        expediente_id: uuid.UUID,
        rol: str,
        *,
        intentos_previos: int,
        minio_key_firmado: str | None,
    ) -> Literal["renovado", "omitido", "pendiente", "agotado"]:
        """Extiende la retención WORM CT del XML de UNA revocación. Ver `_renovar_worm_acto`."""
        return await self._renovar_worm_acto(
            "revocacion",
            revocacion_id,
            artefacto,
            transferencia_id,
            expediente_id,
            rol,
            intentos_previos=intentos_previos,
            minio_key_firmado=minio_key_firmado,
        )

    async def _renovar_worm_acto(
        self,
        acto: Literal["excepcion", "revocacion"],
        acto_id: uuid.UUID,
        artefacto: str,
        transferencia_id: uuid.UUID,
        expediente_id: uuid.UUID,
        rol: str,
        *,
        intentos_previos: int,
        minio_key_firmado: str | None,
        alineacion: tuple[int, date] | None = None,
        actor_id: str | None = None,
    ) -> Literal["renovado", "omitido", "pendiente", "agotado"]:
        """Gemelo de `renovar_worm_firma_personal` con `tipo='acta_firma_<acto>[_personal]_<rol>'`.
        Monotónica; cada extensión real deja asiento (actor SISTEMA, o el de la persona cuyo acto
        provocó la alineación de la cadena, A3) atómico con el espejo. Con `alineacion=(años,
        fecha_inicio)` usa ESA retención (la del eslabón recién protegido) en vez de la ventana
        rodante desde hoy. El
        `file_id` (de `minio_key_firmado`) es OBLIGATORIO: hay N objetos por (expediente, tipo)
        y sin él storage extendería solo el más reciente, dejando las demás constancias vencer."""
        kw = {} if acto == "excepcion" else {"acto": acto}
        meta = self._meta_acto(acto)
        try:
            file_id = (minio_key_firmado or "").split("/", 1)[0]
            if not file_id:
                return await self._bump_worm_renovacion_backoff_firma_excepcion(
                    acto_id,
                    artefacto,
                    intentos_previos=intentos_previos,
                    motivo="sin_minio_key_firmado",
                    acto=acto,
                )
            anios: int | None
            if alineacion is not None:
                anios, fecha_inicio_renovacion = alineacion
            else:
                anios = await self._resolve_ct_retencion_anios_acta(expediente_id)
                fecha_inicio_renovacion = datetime.now(tz=UTC).date()
            if anios is None:
                return await self._bump_worm_renovacion_backoff_firma_excepcion(
                    acto_id,
                    artefacto,
                    intentos_previos=intentos_previos,
                    motivo="trd_sin_retencion",
                    acto=acto,
                )
            retencion_anios = anios

            from app.clients import storage_client

            tipo = self._tipo_worm_excepcion(artefacto, rol, acto)
            outcome = await storage_client.renovar_retencion_worm_internal(
                tenant_slug=self._tenant,
                expediente_id=str(expediente_id),
                tipo=tipo,
                retencion_anios=retencion_anios,
                fecha_inicio=fecha_inicio_renovacion,
                file_id=file_id,
            )

            if outcome.ok:
                assert outcome.retain_until is not None
                async with self._conn.transaction():
                    await self._repo.mark_firma_excepcion_worm_renovado(
                        acto_id, artefacto, worm_retain_until=outcome.retain_until, **kw
                    )
                    if outcome.renovado:
                        await audit.append(
                            self._conn,
                            tenant_slug=self._tenant,
                            service=_SERVICE,
                            action=f"{meta['prefijo']}_retencion_renovada",
                            object_type=meta["object_type"],
                            object_ref=str(acto_id),
                            actor=actor_id or str(SYSTEM_RECONCILER_ID),
                            canal=audit.CANAL_PETICION if actor_id else "system",
                            payload={
                                "transferencia_id": str(transferencia_id),
                                "expediente_id": str(expediente_id),
                                "rol": rol,
                                "artefacto": artefacto,
                                "tipo": tipo,
                                "file_id": file_id,
                                "retencion_anios": retencion_anios,
                                "retain_until": outcome.retain_until.isoformat(),
                                "origen": (
                                    "alineacion_cadena" if alineacion is not None else "ventana_ct"
                                ),
                            },
                        )
                return "renovado" if outcome.renovado else "omitido"

            motivo = (
                "worm_disabled"
                if outcome.disabled
                else "worm_object_not_found"
                if outcome.not_found
                else "worm_no_confirmado"
            )
            return await self._bump_worm_renovacion_backoff_firma_excepcion(
                acto_id, artefacto, intentos_previos=intentos_previos, motivo=motivo, acto=acto
            )
        except Exception:
            logger.warning(
                "Renovación de retención WORM de %s falló (best-effort), id=%s artefacto=%s "
                "expediente_id=%s",
                acto,
                acto_id,
                artefacto,
                expediente_id,
                exc_info=True,
            )
            return "pendiente"

    # -- FIRMA-2: verificación del fundamento -------------------------------------------------

    async def _asentar_lectura_fundamento(
        self,
        transferencia_id: uuid.UUID,
        user_id: uuid.UUID | None,
        excepciones: builtins.list,
        *,
        via: str,
        extra: dict | None = None,
    ) -> None:
        """S5: leer el fundamento EN CLARO (dato de salud/fallecimiento) deja asiento de lectura
        `transferencia.acta_fundamento_leido`: quién, cuándo, cuántas constancias y por qué
        puerta; NUNCA el texto ni la sal. Se asienta ANTES de servirlo y NO es best-effort: sin
        asiento no hay lectura (el error se propaga y la respuesta no sale)."""
        ids = [
            str(e["id"])
            for e in excepciones
            if e.get("fundamento") is not None or e.get("revocacion_fundamento") is not None
        ]
        if not ids or user_id is None:
            return
        await audit.append(
            self._conn,
            tenant_slug=self._tenant,
            service=_SERVICE,
            action="transferencia.acta_fundamento_leido",
            object_type="transferencia_acta",
            object_ref=str(transferencia_id),
            actor=str(user_id),
            payload={
                "transferencia_id": str(transferencia_id),
                "excepcion_ids": ids,
                "via": via,
                **(extra or {}),
            },
            canal=audit.CANAL_PETICION,
        )

    async def leer_fundamento(
        self,
        transferencia_id: uuid.UUID,
        excepcion_id: uuid.UUID,
        actor: Actor,
        *,
        acto: Literal["constancia", "revocacion"],
    ) -> FundamentoLectura:
        """`GET .../firma-inobtenible/{excepcion_id}/fundamento?acto=` (admin nivel 3, router) —
        el texto del fundamento de la constancia o de su revocación, a petición. 404 neutro por
        no-read-up del expediente y por todo lo que no existe (excepción de otra transferencia,
        revocación inexistente). El asiento `transferencia.acta_fundamento_leido` con
        via=`lectura` se escribe ANTES de servir y no es best-effort. La sal no se devuelve."""
        await self._cargar_acta_recibida(transferencia_id, actor.id)  # 404 neutros
        e = await self._repo.get_excepcion_por_id(transferencia_id, excepcion_id)
        texto = None
        if e is not None:
            texto = e.get("revocacion_fundamento") if acto == "revocacion" else e.get("fundamento")
        if e is None or texto is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "fundamento_not_found",
                    "detail": str(excepcion_id),
                    "status": 404,
                },
            )
        await self._asentar_lectura_fundamento(
            transferencia_id, actor.id, [e], via="lectura", extra={"acto": acto}
        )
        return FundamentoLectura(excepcion_id=excepcion_id, acto=acto, fundamento=texto)

    async def leer_sobre_fundamento(
        self,
        transferencia_id: uuid.UUID,
        excepcion_id: uuid.UUID,
        actor: Actor,
        *,
        acto: Literal["constancia", "revocacion"],
    ) -> SobreFundamentoResponse:
        """`GET .../firma-inobtenible/{excepcion_id}/sobre` (admin nivel 3, router) — sirve el sobre
        de revelación del fundamento (XML sellado con texto + sal) de la constancia o de su
        revocación. Único camino de lectura: mediado por archive, nunca por OAI/EAD/CMIS ni
        verificación pública. 404 neutro por no-read-up del expediente y por todo lo que no
        existe (constancia, revocación, sobre: v2/legadas sin sobre). El asiento de lectura
        (`transferencia.acta_fundamento_leido`, via=`sobre`) se escribe DESPUÉS de obtener el
        sobre y ANTES de servirlo, y no es best-effort: sin asiento no hay lectura."""
        await self._cargar_acta_recibida(transferencia_id, actor.id)  # 404 neutros
        e = await self._repo.get_excepcion_por_id(transferencia_id, excepcion_id)
        clave = "revocacion_sobre" if acto == "revocacion" else "sobre"
        if e is None or e.get(f"{clave}_minio_key") is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "sobre_not_found",
                    "detail": str(excepcion_id),
                    "status": 404,
                },
            )
        from app.clients import storage_client

        xml = await storage_client.download_signed_xml(
            tenant_slug=self._tenant, minio_key_firmado=e[f"{clave}_minio_key"]
        )
        if xml is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "sobre_no_disponible",
                    "detail": "No se pudo obtener el sobre del almacén; reintente",
                    "status": 503,
                },
            )
        await self._asentar_lectura_fundamento(
            transferencia_id, actor.id, [e], via="sobre", extra={"acto": acto}
        )
        protegido = e.get("revocacion_worm_sobre_protegido" if acto == "revocacion" else "worm_sobre_protegido")
        return SobreFundamentoResponse(
            excepcion_id=excepcion_id,
            acto=acto,
            sobre_sha256=e.get(f"{clave}_sha256"),
            xades_level=e.get(f"{clave}_xades_level"),
            worm_protegido=protegido,
            xml_firmado=xml,
        )

    async def _cotejar_sobre(
        self, minio_key: str | None, e, *, acto: str
    ) -> bool | None:
        """Cuarta copia del fundamento: el sobre sellado y bajo Object-Lock. `None` si el acto no
        tiene sobre (anterior a la 064) o no se pudo descargar; `True`/`False` si el texto, la sal
        y el compromiso del sobre coinciden con la fila."""
        if not minio_key:
            return None
        from app.clients import storage_client

        xml = await storage_client.download_signed_xml(
            tenant_slug=self._tenant, minio_key_firmado=minio_key
        )
        campos = leer_sobre(xml)
        if campos is None:
            return None
        pref = "revocacion_" if acto == "revocacion" else ""
        sal = e.get(f"{pref}fundamento_sal")
        return (
            campos["acto"] == acto
            and sal is not None
            and campos["sal_hex"] == _sal_hex(sal)
            and campos["texto"] == e.get(f"{pref}fundamento")
            and campos["compromiso"] == e.get(f"{pref}fundamento_compromiso")
            and compromiso_coincide(sal, campos["texto"], campos["compromiso"])
        )

    async def verificar_fundamento(
        self, transferencia_id: uuid.UUID, excepcion_id: uuid.UUID, actor: Actor
    ) -> FundamentoVerificacion:
        """`GET .../firma-inobtenible/{excepcion_id}/fundamento/verificacion` (admin nivel 3,
        router). Recalcula LOCALMENTE el compromiso SHA-256(sal || texto) del fundamento guardado
        y lo compara contra TRES copias independientes: la columna, el XML firmado y el asiento de
        auditoría; y lo mismo con el fundamento de la revocación si existe. No devuelve el texto
        ni la sal. Una constancia anterior a la v3 (sin sal ni compromiso) no es verificable por
        este método: se declara `verificable=False`, no se inventa una comprobación. Leer el
        fundamento para recalcularlo deja asiento de lectura (S5)."""
        await self._cargar_acta_recibida(transferencia_id, actor.id)  # 404 neutros
        e = await self._repo.get_excepcion_por_id(transferencia_id, excepcion_id)
        if e is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "excepcion_not_found",
                    "detail": str(excepcion_id),
                    "status": 404,
                },
            )
        if e.get("fundamento_compromiso") is None or e.get("fundamento_sal") is None:
            return FundamentoVerificacion(
                excepcion_id=excepcion_id,
                verificable=False,
                motivo="constancia_sin_compromiso_salado",
            )
        await self._asentar_lectura_fundamento(
            transferencia_id, actor.id, [e], via="fundamento_verificacion"
        )
        xmls = await self._repo.get_xml_constancia_y_revocacion(excepcion_id)

        def _cotejar(sal, texto: str | None, compromiso: str | None) -> bool | None:
            if not compromiso:
                return None  # la copia no existe: no se da por buena
            return compromiso_coincide(sal, texto, compromiso)

        async def _asiento(action: str, clave: str, valor: str) -> str | None:
            payload = await self._repo.get_asiento_payload(
                tenant_slug=self._tenant,
                action=action,
                object_ref=str(transferencia_id),
                clave=clave,
                valor=valor,
            )
            return str(payload["fundamento_compromiso"]) if payload and payload.get(
                "fundamento_compromiso"
            ) else None

        sal, texto = e["fundamento_sal"], e["fundamento"]
        xml_c = (enlaces_de_constancia(xmls["constancia_xml"] if xmls else None) or {}).get(
            "compromiso"
        )
        columna = _cotejar(sal, texto, e["fundamento_compromiso"])
        xml = _cotejar(sal, texto, xml_c)
        asiento = _cotejar(
            sal,
            texto,
            await _asiento(
                "archive.acta_firma_inobtenible_declarada", "excepcion_id", str(excepcion_id)
            ),
        )
        rev_col = rev_xml = rev_asiento = None
        comparables = [columna, xml, asiento]
        if e.get("revocacion_fundamento_compromiso") is not None:
            rsal, rtexto = e["revocacion_fundamento_sal"], e["revocacion_fundamento"]
            xml_r = (enlaces_de_revocacion(xmls["revocacion_xml"] if xmls else None) or {}).get(
                "compromiso"
            )
            rev_col = _cotejar(rsal, rtexto, e["revocacion_fundamento_compromiso"])
            rev_xml = _cotejar(rsal, rtexto, xml_r)
            rev_asiento = _cotejar(
                rsal,
                rtexto,
                await _asiento(
                    "archive.acta_firma_inobtenible_revocada",
                    "revocacion_id",
                    str(e["revocacion_id"]),
                ),
            )
            comparables += [rev_col, rev_xml, rev_asiento]
        # Cuarta copia (064): solo se exige si el acto TIENE sobre; si existe y no se pudo leer o
        # no coincide, no hay coincidencia.
        sobre = await self._cotejar_sobre(e.get("sobre_minio_key"), e, acto="constancia")
        if e.get("sobre_minio_key") is not None:
            comparables.append(sobre)
        rev_sobre = None
        if e.get("revocacion_sobre_minio_key") is not None:
            rev_sobre = await self._cotejar_sobre(
                e["revocacion_sobre_minio_key"], e, acto="revocacion"
            )
            comparables.append(rev_sobre)
        return FundamentoVerificacion(
            excepcion_id=excepcion_id,
            verificable=True,
            algoritmo=e["fundamento_compromiso_algoritmo"],
            columna=columna,
            xml=xml,
            asiento=asiento,
            revocacion_columna=rev_col,
            revocacion_xml=rev_xml,
            revocacion_asiento=rev_asiento,
            sobre=sobre,
            revocacion_sobre=rev_sobre,
            # Una copia ausente (None) o no comprobable NO cuenta como coincidencia.
            coincide=all(v is True for v in comparables),
        )
