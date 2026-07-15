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

import hashlib
import logging
import uuid
from datetime import UTC, datetime

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.core.actor import Actor
from app.core.clearance import CLEARANCE_SIN_RESTRICCION
from app.repositories.archive_repository import ArchiveRepository
from app.repositories.index_repository import IndexRepository
from app.repositories.transferencia_repository import TransferenciaRepository
from app.schemas.fisico import FuidResponse
from app.schemas.transferencia import (
    TransferenciaActaResponse,
    TransferenciaCreate,
    TransferenciaRechazo,
    TransferenciaResponse,
)
# `_fuid_to_xml` (E12 H-I): reusada TAL CUAL, sin duplicar la serialización
# canónica (C14N) -- es el mismo cuerpo que congela el acta de entrega.
from app.services.fisico_service import FisicoService, _fuid_to_xml
from app.services.index_service import IndexService

logger = logging.getLogger(__name__)

_SERVICE = "archive-service"

_VALID = {
    "preparada": {"enviada"},
    "enviada": {"recibida", "rechazada"},
    "recibida": set(),
    "rechazada": set(),
}


def _resp(row) -> TransferenciaResponse:
    return TransferenciaResponse(**dict(row))


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
            # Atomicidad preparación<->auditoría (mismo patrón que
            # `ArchiveService.create_expediente`): INSERT + evento de hoja de
            # ruta + asiento forense son un único acto de negocio; `audit.append`
            # va LO ÚLTIMO (retiene el advisory lock del tenant el mínimo tiempo).
            async with self._conn.transaction():
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
        return _resp(row)

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
        self, transferencia_id: uuid.UUID, user_id: uuid.UUID | None = None
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
        return TransferenciaActaResponse(**dict(acta), expediente_id=row["expediente_id"])
