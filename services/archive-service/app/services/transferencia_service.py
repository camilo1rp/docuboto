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
import hashlib
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.core.actor import Actor
from app.core.clearance import CLEARANCE_SIN_RESTRICCION
from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.repositories.archive_repository import ArchiveRepository
from app.repositories.fisico_repository import FisicoRepository
from app.repositories.index_repository import IndexRepository
from app.repositories.transferencia_repository import TransferenciaRepository
from app.schemas.fisico import FuidResponse
from app.schemas.transferencia import (
    TransferenciaActaFirmaInfo,
    TransferenciaActaFirmaPersonalResponse,
    TransferenciaActaFirmaPersonalVerif,
    TransferenciaActaResponse,
    TransferenciaActaVerificacionResponse,
    TransferenciaCreate,
    TransferenciaRechazo,
    TransferenciaResponse,
)
from app.services.backoff import next_backoff_seconds
# `_fuid_to_xml` (E12 H-I): reusada TAL CUAL, sin duplicar la serialización
# canónica (C14N) -- es el mismo cuerpo que congela el acta de entrega.
from app.services.fisico_service import FisicoService, _fuid_to_xml
from app.services.index_service import IndexService
from app.services.retention_math import total_trd_years

logger = logging.getLogger(__name__)

_SERVICE = "archive-service"

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


def _resp(row) -> TransferenciaResponse:
    return TransferenciaResponse(**dict(row))


def _firma_personal_gate_activo(nivel_seguridad: int) -> bool:
    """Predicado del gate `acta_firma_personal_require` (E17/F4, Incremento
    1) -- espeja `signature-service.app.services.cadena_service.
    _xades_gate_activo`. Decide únicamente si la AUSENCIA de la firma
    personal del receptor degrada `completitud` a
    `firma_personal_pendiente` en `GET /acta/verificacion` -- NUNCA bloquea
    `recibir()` ni `POST /acta/firmar-personal` (la firma personal siempre
    puede producirse, gated o no; este predicado solo afecta el VEREDICTO
    de completitud reportado)."""
    modo = settings.acta_firma_personal_require
    if modo == "todos":
        return True
    if modo == "clasificados":
        return nivel_seguridad >= 2
    return False  # "off"


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
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "expediente_not_closed",
                        "detail": "Solo se transfiere un expediente cerrado",
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
            # transferencia es LÓGICA, origen/destino físico son N/A. Se
            # conserva el comportamiento H-M previo ("ambos o ninguno", SIN
            # cotejo -- no hay ubicación actual contra la cual cotejar) --
            # exigir topología física SIEMPRE haría imposible transferir un
            # expediente electrónico, el caso de uso central de un DMS
            # moderno; E17 (archivo físico) es explícitamente PARCIAL en el
            # roadmap y no todo tenant tendrá su topología de `ubicacion`
            # poblada. Residual declarado (RF-FIR-15): un expediente sin
            # tenencia localizada que SÍ aporta el par no se cotea -- no peor
            # que el status-quo previo a este incremento (antes NINGÚN origen
            # se coteaba).
            #
            # El destino NUNCA se cotea (en ninguna rama): es a DÓNDE VA, no
            # dónde está -- solo se valida por coherencia de tipo, como hasta
            # ahora.
            #
            # Lecturas PLANAS de `expediente_unidad`/`ubicacion` -- topología
            # física, no CONTENIDO del expediente: a diferencia del FUID, no
            # hay aquí concern de clearance/no-read-up. Va ANTES de la tx: son
            # solo lecturas, nada que revertir todavía.
            origen_id = payload.ubicacion_origen_id
            destino_id = payload.ubicacion_destino_id
            fisico_repo = FisicoRepository(self._conn, self._tenant)
            units = await fisico_repo.units_of_expediente(payload.expediente_id)
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
            if origen_id is not None:
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
            # Atomicidad preparación<->auditoría (mismo patrón que
            # `ArchiveService.create_expediente`): INSERT + evento de hoja de
            # ruta + asiento forense son un único acto de negocio; `audit.append`
            # va LO ÚLTIMO (retiene el advisory lock del tenant el mínimo tiempo).
            async with self._conn.transaction():
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
                        }
                    )
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
                        "motivo": exc.detail.get("error")
                        if isinstance(exc.detail, dict)
                        else str(exc.detail),
                    },
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                logger.warning(
                    "audit.append(archive.transferencia_preparada_failed) failed",
                    exc_info=True,
                )
            raise
        return _resp(row)

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
                        "motivo": exc.detail.get("error")
                        if isinstance(exc.detail, dict)
                        else str(exc.detail),
                    },
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
                if not (fx.documentos_ok and fx.huella_indice_ok):
                    # El detalle por-documento (UUIDs) va al asiento forense
                    # indeleble, no a la respuesta: al actor de custodia (que
                    # puede no tener clearance de LECTURA del expediente) solo
                    # se le devuelve un CONTEO opaco, sin enumerar qué radicados
                    # del expediente reservado se alteraron.
                    fixity_fallidos = [
                        str(d.document_id) for d in fx.documentos if not d.coincide
                    ]
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
                fuid = await FisicoService(self._conn, self._tenant).generate_fuid(
                    ubicacion_id=None,
                    trd_serie_id=None,
                    expediente_id=existing["expediente_id"],
                    max_clearance=CLEARANCE_SIN_RESTRICCION,
                )
                acta_xml = _fuid_to_xml(fuid)
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
                await self._repo.insert_acta_firma_pendiente(transferencia_id)
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
                )
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant,
                    service=_SERVICE,
                    action="archive.expediente_transferred",
                    object_type="expediente",
                    object_ref=transitioned.code,
                    actor=actor.audit_actor,
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
                        "motivo": exc.detail.get("error")
                        if isinstance(exc.detail, dict)
                        else str(exc.detail),
                        # Traza forense completa de una fixity fallida (los UUIDs
                        # que NO se exponen en el 409) — solo en el registro
                        # indeleble, cuya lectura exige permiso de auditoría.
                        **(
                            {"documentos_fallidos": fixity_fallidos}
                            if fixity_fallidos
                            else {}
                        ),
                    },
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                logger.warning(
                    "audit.append(archive.transferencia_recibida_failed) failed", exc_info=True
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
        return _resp(row)

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
                        "motivo": exc.detail.get("error")
                        if isinstance(exc.detail, dict)
                        else str(exc.detail),
                    },
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
                    "detail": f"Expediente {row['expediente_id']} not found",
                    "status": 404,
                },
            )
        return await FisicoService(self._conn, self._tenant).generate_fuid(
            ubicacion_id=None,
            trd_serie_id=None,
            expediente_id=row["expediente_id"],
            user_id=user_id,
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
                    "detail": f"Expediente {row['expediente_id']} not found",
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
        return TransferenciaActaResponse(
            **dict(acta), expediente_id=row["expediente_id"], firma=firma_info
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
                    "detail": f"Expediente {row['expediente_id']} not found",
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
        if outcome.rejected is not None:
            status_code, detail = outcome.rejected
            raise HTTPException(status_code=status_code, detail=detail)

        signed = outcome.signed
        assert signed is not None  # exactamente uno de los tres queda poblado

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
                    "detail": f"Expediente {row['expediente_id']} not found",
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
            firmas_personales.append(
                TransferenciaActaFirmaPersonalVerif(
                    rol=pr["rol"],
                    firmante_id=pr["firmante_id"],
                    valida=valida,
                    revocacion_status=revocacion_status,
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
        if tiene_recibe and tiene_entrega:
            completitud: str = "firmada_completa"
        elif tiene_recibe:
            completitud = "firmada_receptor"
        elif tiene_entrega:
            completitud = "firmada_remitente"
        elif _firma_personal_gate_activo(exp.nivel_seguridad):
            completitud = "firma_personal_pendiente"
        else:
            completitud = "sellada"

        # `valida` compone la cripto+revocación de TODO lo presente, incluida la
        # firma ADITIVA del elaborador: los dos ejes de ortogonalidad DIFIEREN —
        # el elaborador NO cuenta para `completitud` (traspaso = entrega+recibe)
        # pero SÍ para `valida` (un cert de elaborador revocado → `valida=false`
        # aun con `firmada_completa`). Es fail-closed deliberado: si una firma
        # presente no valida, el veredicto agregado del acta no puede ser True.
        valida_total = bool(sello_info.presente and sello_info.valida) and all(
            f.valida for f in firmas_personales
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
            completitud=completitud,  # type: ignore[arg-type]
            valida=valida_total,
            acreditado=False,
            auto_traslado=auto_traslado,
            elaborador_firmado=tiene_elabora,
        )

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
        return total, firmado_at.date()

    async def _resolve_ct_retencion_anios_acta(self, expediente_id: uuid.UUID) -> int | None:
        """Total TRD (ambas fases) del EXPEDIENTE de una transferencia --
        versión de RENOVACIÓN (E10 debt) de `_resolve_worm_retention_acta`:
        la ventana rodante de renovación SIEMPRE usa `fecha_inicio=hoy` (no
        `firmado_at`), así que este helper no exige ese parámetro."""
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
                        outcome.worm_object_id,
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
