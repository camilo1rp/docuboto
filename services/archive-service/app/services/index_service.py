# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Servicio del índice electrónico del expediente (E15, Acuerdo AGN 001/2024).

- `create_version_zero`: índice vacío disponible desde la creación del expediente.
- `regenerate`: nueva versión append-only del índice (idempotente: si el conjunto
  documento+huella+orden no cambió, no crea versión).
- consultas: vigente, por versión, XML, historial de versiones.

El `valor_huella` por documento se poblará desde storage-service (fixity, E15 T-08).
La firma XAdES-B real al cierre (E06 Inc.1, RT-16) vive en `firmar_indice` /
`firmar_indice_retry`: best-effort — si falla, el índice queda 'pendiente_firma'
(no bloquea el cierre) y es reintentable. La validación XSD estricta (lxml)
queda como evolutivo.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.core.clearance import CLEARANCE_SIN_RESTRICCION
from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.core.disposition import CT_DISPOSITION_CODES
from app.models.archive import Expediente
from app.repositories.aip_repository import AipRepository
from app.repositories.archive_repository import ArchiveRepository
from app.repositories.index_repository import IndexRepository
from app.repositories.transferencia_repository import TransferenciaRepository
from app.services.backoff import next_backoff_seconds
from app.services.retention_math import total_trd_years
from app.schemas.archive import (
    IndiceFixityResult,
    IndiceItemResponse,
    IndicePendienteFirma,
    IndicePendienteRenovacion,
    IndiceResponse,
    IndiceVerifyDoc,
    IndiceVerifyFirma,
    IndiceVerifyResponse,
    IndiceVersionSummary,
    RebuildResult,
)
from app.services.index_builder import (
    PISTA_ACCESO_INSTRUMENTACION_DATE,
    XSD_VERSION,
    IndiceData,
    IndiceItemData,
    build_xml,
    content_hash,
    count_vigentes,
    xml_sha256,
)

if TYPE_CHECKING:
    from app.clients.signature_client import SignedIndice

logger = logging.getLogger(__name__)

_SERVICE = "archive-service"
# Canales que representan una consulta real de un usuario (gateway/mcp-server,
# ADR-019/D-03) — 'system' (jobs/regeneraciones internas) queda excluido: no
# ensucia la pista de acceso ni amplifica escrituras (E15 perfil v4, R3-a).
_CANALES_AUDITABLES = frozenset({"api", "ui", "mcp"})


class IndexService:
    def __init__(self, conn, tenant_slug: str) -> None:
        self._conn = conn
        self._index = IndexRepository(conn)
        self._archive = ArchiveRepository(conn, tenant_slug)
        self._tenant_slug = tenant_slug

    # -- construcción --------------------------------------------------

    def _items_from(self, exp: Expediente) -> list[IndiceItemData]:
        """Construye los items del índice a partir de TODOS los vínculos del
        expediente — VINCULADOS y EXCLUIDOS (soft-delete, `exp.radicados` ya
        trae ambos, ver `ArchiveRepository._get_radicados`). El orden ya NO es
        un enumerate temporal: es `orden_documental`, asignado UNA vez al
        vincular e inmutable (E15 perfil v3, RT-15 art. 4.3.2.3) — un excluido
        conserva el suyo para que el índice muestre el hueco trazable."""
        return [
            IndiceItemData(
                document_id=str(r.radicado_id),
                orden=r.orden_documental if r.orden_documental is not None else 0,
                titulo=r.tracking_number,
                fecha_archivado=r.added_at,  # -> <fechaIncorporacion> en v2
                valor_huella=r.content_hash,  # SHA-256 aportado al vincular (T-08)
                # Metadatos RT-15 (E15 Inc.3), aportados al vincular (Opción A);
                # NULL = no declarado -> se omiten del <documento> del índice.
                formato=r.formato,
                tamano_bytes=r.tamano_bytes,
                folios=r.folios,
                # v3: <listaControlAcceso> hereda el nivel del EXPEDIENTE (no
                # hay clasificación por documento); `estado` decide si se
                # emite <documento excluido="true">.
                nivel_seguridad=exp.nivel_seguridad,
                estado=r.estado,
                fecha_exclusion=r.excluido_at,
                causal_exclusion=r.excluido_causal,
                # v4 (R1): dependencia productora aportada al vincular.
                dependencia_productora_codigo=r.dependencia_productora_codigo,
                dependencia_productora_nombre=r.dependencia_productora_nombre,
            )
            for r in exp.radicados
        ]

    async def _contexto_series(
        self, trd_serie_id: uuid.UUID | None
    ) -> tuple[str | None, str | None]:
        """Nombres de serie/subserie para el bloque <contexto> (v3, RT-15).

        Si la serie TRD del expediente tiene padre (`parent_id`), es una
        subserie: `serie` = nombre del PADRE, `subserie` = nombre de la serie
        propia. Si es de primer nivel, solo `serie`. El UUID se sigue
        emitiendo aparte (`<expediente><serie>`) como identificador — esto
        solo resuelve los NOMBRES legibles.
        """
        if trd_serie_id is None:
            return None, None
        serie = await self._archive.get_trd_serie_by_id(trd_serie_id)
        if serie is None:
            return None, None
        if serie.parent_id is not None:
            parent = await self._archive.get_trd_serie_by_id(serie.parent_id)
            if parent is not None:
                return parent.name, serie.name
        return serie.name, None

    async def _data(self, exp: Expediente, items: list[IndiceItemData]) -> IndiceData:
        serie_nombre, subserie_nombre = await self._contexto_series(exp.trd_serie_id)
        # fechaPrimerUso: primera incorporación VIGENTE (un documento excluido
        # no cuenta como "uso" activo del expediente).
        vinculados = [r.added_at for r in exp.radicados if r.estado == "vinculado"]
        fecha_primer_uso = min(vinculados) if vinculados else None
        # v4 (R1 + <conformidad>): productoraPresente=true SOLO si hay AL MENOS
        # UN documento VIGENTE (no excluido) Y TODOS declararon dependencia
        # productora. Un expediente SIN vigentes (recién creado, versión 0)
        # es 'false' — `all()` sobre una lista vacía da vacuamente `True`, lo
        # que proyectaría conformidad positiva sobre un conjunto documental
        # inexistente (hallazgo Baja, cierre v4).
        vigentes = [it for it in items if it.estado == "vinculado"]
        productora_presente = bool(vigentes) and all(
            it.dependencia_productora_codigo is not None and it.dependencia_productora_nombre is not None
            for it in vigentes
        )
        # v4 (R3 + <conformidad>): determinista — NUNCA datetime.now() aquí.
        # El mayor entre la fecha de instrumentación del historial de acceso
        # (constante) y la fecha de creación del expediente (dato ya
        # disponible): antes de instrumentar, ningún expediente tenía pista de
        # acceso verificable, sin importar cuán antiguo fuera.
        pista_acceso_desde = max(PISTA_ACCESO_INSTRUMENTACION_DATE, exp.opened_at.date())
        return IndiceData(
            expediente_id=str(exp.id),
            code=exp.code,
            name=exp.name,
            serie_id=str(exp.trd_serie_id) if exp.trd_serie_id else None,
            fecha_creacion=exp.opened_at,
            items=items,
            serie_nombre=serie_nombre,
            subserie_nombre=subserie_nombre,
            fecha_primer_uso=fecha_primer_uso,
            nivel_seguridad=exp.nivel_seguridad,
            productora_presente=productora_presente,
            pista_acceso_desde=pista_acceso_desde,
        )

    # -- creación / regeneración --------------------------------------

    async def create_version_zero(self, exp: Expediente) -> None:
        """Índice versión 0 (sin documentos) al crear el expediente."""
        items: list[IndiceItemData] = []
        xml = build_xml(await self._data(exp, items))
        await self._index.insert_version(
            expediente_id=exp.id,
            version=0,
            xml=xml,
            xml_sha256=xml_sha256(xml),
            content_hash=content_hash(xml),
            num_documentos=0,
            generado_por="system",
            items=items,
        )

    async def regenerate(
        self,
        expediente_id: uuid.UUID,
        *,
        generado_por: str,
        assert_open: bool = True,
    ) -> RebuildResult:
        """Regenera (append-only) el índice si el conjunto documental cambió.

        `assert_open` (por defecto True) rechaza con 409 la regeneración de un
        expediente que no está 'open' — evita que un rebuild ad-hoc posterior
        al cierre dispare version+1 sobre un conjunto documental ya congelado.
        `close_expediente` pasa `assert_open=False`: ES la regeneración final
        legítima, invocada justo después de transicionar el expediente a
        'closed' (por eso su propio `exp.status` ya no es 'open' en ese punto).

        Pre-condición interna de existencia/estado, no una lectura expuesta al
        llamante — la mutación (regenerar el índice) es LEGÍTIMA sin
        clearance de lectura, mismo principio que `ArchiveService.
        link_radicado`/`unlink_radicado` (M2, RF-SEG-08, resuelto: ver
        `core.clearance.CLEARANCE_SIN_RESTRICCION`). Cuando el disparador es
        el endpoint `POST /{id}/indice/rebuild`, la RESPUESTA se acota según
        el clearance real del llamante en el HANDLER de `routers/indice.py`
        (NO aquí: este método también lo reutiliza `close_expediente` en
        canal 'system', que necesita los valores reales para su auditoría).
        """
        exp = await self._archive.get_expediente_by_id(
            expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
        )
        if exp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": str(expediente_id),
                    "status": 404,
                },
            )
        if assert_open and exp.status != "open":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "expediente_not_open",
                    "detail": f"Expediente en estado '{exp.status}': no admite regeneración del índice",
                    "status": 409,
                },
            )
        current = await self._index.get_current(expediente_id)
        if current is not None and current["estado"] == "firmado":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "expediente_closed",
                    "detail": "Índice firmado e inmutable",
                    "status": 409,
                },
            )
        items = self._items_from(exp)
        # v4: `content_hash` cubre TODO el documento canónico (PRINCIPIO
        # RECTOR) — hay que construir el XML completo ANTES de poder decidir
        # si "unchanged" (a diferencia de v1-v3, donde el hash por-item se
        # calculaba sin tocar `build_xml`). Coste aceptado: una regeneración
        # idempotente ahora también resuelve `<contexto>` (TRD) igual que una
        # que sí cambia.
        data = await self._data(exp, items)
        xml = build_xml(data)
        ch = content_hash(xml)
        if current is not None and current["content_hash"] == ch:
            return RebuildResult(
                version=current["version"],
                xml_sha256=current["xml_sha256"],
                num_documentos=current["num_documentos"],
                unchanged=True,
                xsd_version=current["xsd_version"],
            )
        version = (current["version"] + 1) if current is not None else 0
        # num_documentos = solo VIGENTES (perfil v3): un excluido sigue en el
        # XML como hueco trazable pero no cuenta en el total documental.
        num_documentos = count_vigentes(items)
        xh = xml_sha256(xml)
        await self._index.insert_version(
            expediente_id=expediente_id,
            version=version,
            xml=xml,
            xml_sha256=xh,
            content_hash=ch,
            num_documentos=num_documentos,
            generado_por=generado_por,
            items=items,
        )
        # Trazabilidad del salto v1->v2 (E15 Inc.3, RT-15, ADR-010): TODO
        # disparador de regeneración (rebuild admin, link/unlink/batch, cierre)
        # deja aquí su propio asiento — antes solo `close_expediente` auditaba,
        # dejando el camino rebuild/link invisible en la auditoría inmutable.
        await audit.append(
            self._conn,
            tenant_slug=self._tenant_slug,
            service=_SERVICE,
            action="archive.indice_regenerated",
            object_type="expediente_indice",
            object_ref=exp.code,
            actor=generado_por,
            payload={
                "expediente_id": str(expediente_id),
                "version": version,
                "xsd_version": XSD_VERSION,
            },
        )
        return RebuildResult(
            version=version,
            xml_sha256=xh,
            num_documentos=num_documentos,
            xsd_version=XSD_VERSION,
        )

    # -- consultas -----------------------------------------------------

    async def _to_response(self, row) -> IndiceResponse:
        items = await self._index.get_items(row["id"])
        return IndiceResponse(
            id=row["id"],
            expediente_id=row["expediente_id"],
            version=row["version"],
            estado=row["estado"],
            num_documentos=row["num_documentos"],
            xml_sha256=row["xml_sha256"],
            algoritmo=row["algoritmo"],
            firma_id=row["firma_id"],
            generado_ts=row["generado_ts"],
            items=[
                IndiceItemResponse(
                    document_id=it["document_id"],
                    orden=it["orden"],
                    titulo=it["titulo"],
                    tipologia=it["tipologia"],
                    # Alineado con <fechaIncorporacion> del XML v2 (RT-15 art.
                    # 4.3.2.3, mismo valor que `added_at`/`fecha_archivado`
                    # internos) para no divergir entre el artefacto probatorio
                    # y la vista consultable.
                    fecha_incorporacion=it["fecha_archivado"],
                    valor_huella=it["valor_huella"],
                    funcion_resumen=it["funcion_resumen"],
                    # Metadatos RT-15 (E15 Inc.3): `.get` tolera filas que no
                    # los proyecten (tests unitarios con dicts recortados).
                    formato=it.get("formato"),
                    tamano_bytes=it.get("tamano_bytes"),
                    folios=it.get("folios"),
                    estado=it.get("estado"),
                    nivel_seguridad=it.get("nivel_seguridad_snapshot"),
                    # v4 (E15 perfil v4, R1): snapshot de la dependencia
                    # productora; `.get` tolera filas anteriores a v4 que no
                    # proyectan estas columnas.
                    dependencia_productora_codigo=it.get("dependencia_productora_codigo"),
                    dependencia_productora_nombre=it.get("dependencia_productora_nombre"),
                )
                for it in items
            ],
        )

    async def _row_or_404(self, expediente_id: uuid.UUID, version: int | None):
        if version is None:
            row = await self._index.get_current(expediente_id)
        else:
            row = await self._index.get_version(expediente_id, version)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_indice_not_found",
                    "detail": str(expediente_id),
                    "status": 404,
                },
            )
        return row

    async def _assert_visible(self, expediente_id: uuid.UUID, max_clearance: int) -> Expediente:
        """No-read-up (RF-SEG-08): 404 si el expediente no existe o su
        `nivel_seguridad` supera el clearance del llamante, ANTES de servir
        cualquier lectura del índice electrónico. Sin este guard, un usuario
        con solo USUA_PERM_EXPEDIENTE (sin clearance suficiente) podía leer el
        índice XML completo — incl. `<listaControlAcceso>` y documentos
        excluidos — de un expediente CLASIFICADA (mismo criterio que
        `ArchiveService.get_expediente`, replicado aquí porque las lecturas de
        índice NO pasan por `ArchiveService`). Devuelve el `Expediente` (su
        `code` es el `object_ref` que necesita la pista de acceso, R3-a)."""
        exp = await self._archive.get_expediente_by_id(
            expediente_id, max_clearance=max_clearance
        )
        if exp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": str(expediente_id),
                    "status": 404,
                },
            )
        return exp

    async def _audit_read_access(
        self,
        code: str,
        *,
        version: int,
        user_id: uuid.UUID | None,
        canal: str,
    ) -> None:
        """Pista de acceso (E15 perfil v4, R3-a): una lectura EXITOSA del
        índice deja traza en `audit_log` con el actor real y el canal de
        origen — atestada (sin contenido) en `<pistaAuditoria>` del XML
        (`index_builder.build_xml`) y consultable vía
        `GET /expedientes/{id}/historial-acceso`.

        Solo para canales de un usuario real (`_CANALES_AUDITABLES`) — 'system'
        (regeneración/rebuild, verificación de fixity) NO escribe pista: no es
        una consulta de contenido por un usuario, y amplificaría cada
        rebuild/verify como si alguien hubiera "leído" el índice.

        Best-effort: envuelto en su propio `try/except` (mismo patrón que
        `audit.append_denial` en `core/clearance.py`) — un fallo al auditar
        NUNCA debe degradar una lectura exitosa a 500.

        `user_id is None` también descarta el registro: el contrato R3-a exige
        un actor REAL en la pista de acceso — sin `user_id` (p. ej. canal 'api'
        sin `X-User-Id`) no hay a quién atribuir la lectura, y escribir
        `actor=None` en `audit_log` dejaría una traza sin valor forense.
        """
        if canal not in _CANALES_AUDITABLES or user_id is None:
            return
        try:
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.indice_consultado",
                object_type="expediente_indice",
                object_ref=code,
                actor=str(user_id) if user_id else None,
                canal=canal,
                payload={"version": version},
            )
        except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
            logger.warning("audit.append (indice_consultado) failed", exc_info=True)

    async def get_indice(
        self,
        expediente_id: uuid.UUID,
        version: int | None,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> IndiceResponse:
        max_clearance = await self._archive.caller_clearance(user_id)
        exp = await self._assert_visible(expediente_id, max_clearance)
        row = await self._row_or_404(expediente_id, version)
        response = await self._to_response(row)
        await self._audit_read_access(
            exp.code, version=row["version"], user_id=user_id, canal=canal
        )
        return response

    async def get_xml(
        self,
        expediente_id: uuid.UUID,
        version: int | None,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> str:
        max_clearance = await self._archive.caller_clearance(user_id)
        exp = await self._assert_visible(expediente_id, max_clearance)
        row = await self._row_or_404(expediente_id, version)
        await self._audit_read_access(
            exp.code, version=row["version"], user_id=user_id, canal=canal
        )
        return row["xml"]

    async def _mark_pendiente_audited(
        self, indice_id: uuid.UUID, expediente_id: uuid.UUID, *, tenant_slug: str,
        actor_id: str | None, motivo: str,
    ) -> None:
        """Marca 'pendiente_firma' y su asiento de auditoría como un ÚNICO acto (H5):
        antes vivían en dos commits distintos (autocommit aquí + `audit.append` de
        vuelta en `ArchiveService.close_expediente`) — un fallo entre ambos dejaba el
        índice marcado sin que quedara traza alguna del porqué."""
        async with self._conn.transaction():
            await self._index.mark_pendiente(indice_id, motivo=motivo)
            await audit.append(
                self._conn,
                tenant_slug=tenant_slug,
                service=_SERVICE,
                action="archive.indice_firma_pendiente",
                object_type="expediente_indice",
                object_ref=str(expediente_id),
                actor=actor_id,
                payload={"expediente_id": str(expediente_id), "motivo": motivo},
            )

    async def _current_signable_or_none(
        self, expediente_id: uuid.UUID, *, tenant_slug: str, actor_id: str | None
    ) -> object | None:
        """Prelude común a `firmar_indice`/`firmar_indice_close`: resuelve la
        versión vigente del índice y aplica el gate de `actor_id`
        (RF-FIR-11/12). Devuelve `None` en dos casos que el llamante debe
        tratar idénticamente (sin más que hacer):
        - nada que firmar (índice inexistente, o ya `'firmado'` — inmutable).
        - falta `actor_id`: se corta ANTES de intentar ninguna llamada HTTP
          (signature-service exige X-User-Id y respondería 401), degradando
          de inmediato a 'pendiente_firma' (motivo='sin_actor_id', auditado).

        Factorizado para que el control compensatorio del cierre
        (`firmar_indice_close`) comparta EXACTAMENTE esta resolución con el
        camino de un único intento (`firmar_indice`, usado por el reintento
        manual `POST /indice/firmar`) sin duplicar la lógica ni el orden de
        las llamadas a BD que ya asumen los tests existentes.
        """
        current = await self._index.get_current(expediente_id)
        if current is None or current["estado"] == "firmado":
            return None
        if not actor_id:
            await self._mark_pendiente_audited(
                current["id"], expediente_id, tenant_slug=tenant_slug,
                actor_id=actor_id, motivo="sin_actor_id",
            )
            return None
        return current

    async def firmar_indice(
        self, expediente_id: uuid.UUID, *, tenant_slug: str, actor_id: str | None
    ) -> SignedIndice | None:
        """Sella XAdES-B la versión vigente del índice vía signature-service (E06 Inc.1, RT-16).

        Decisión del usuario: best-effort. Si la firma no se obtiene por
        cualquier motivo (sin `actor_id`, sello del tenant no aprovisionado,
        servicio inalcanzable...) el índice queda 'pendiente_firma' — el
        cierre del expediente NO se bloquea — y es reintentable vía
        `firmar_indice_retry` / `POST /indice/firmar`.

        `actor_id` es el X-User-Id (UUID interno de `auth_users.id`) de quien
        cierra el expediente: signature-service lo exige para atribuir el
        sello (RF-FIR-11/12/14) y responde 401 sin él, por lo que aquí se
        cortocircuita a 'pendiente_firma' sin siquiera intentar la llamada.

        Atomicidad efecto<->asiento (H5): el desenlace del sellado (marcar
        'firmado'/'pendiente_firma') y su propio asiento de auditoría
        ('archive.indice_firmado' / 'archive.indice_firma_pendiente') se
        comprometen juntos, en una única `conn.transaction()` por rama — antes
        el UPDATE de estado corría en autocommit aquí y el `audit.append`
        vivía aparte, en `ArchiveService.close_expediente`, en OTRA
        transacción: un fallo entre ambos dejaba el índice 'firmado' (con
        `firma_id` ya persistido) SIN asiento en la cadena inmutable. La
        llamada HTTP a signature-service ya ocurrió (o se decidió no
        intentarla) ANTES de cada bloque, así que este tramo es 100% DB — no
        hay HTTP dentro de la tx.

        Asiento fantasma bajo carrera (S1, RF-FIR-14): `mark_signed` casa 0
        filas si OTRO proceso (el job de reconciliación, u otra instancia de
        este mismo reintento) ya marcó 'firmado' esta fila entre la lectura de
        `current` y este UPDATE. En ese caso el `signed` obtenido aquí es un
        sello REAL pero HUÉRFANO (nunca queda persistido como el `firma_id`
        vigente del índice): NO se escribe el asiento (atribuiría al humano
        un sello del sistema, o viceversa) y se reporta 409 — el mismo
        desenlace idempotente que `firmar_indice_retry` ya reporta cuando el
        índice YA estaba firmado antes de intentar nada.
        """
        from app.clients import signature_client

        current = await self._current_signable_or_none(
            expediente_id, tenant_slug=tenant_slug, actor_id=actor_id
        )
        if current is None:
            return None
        signed = await signature_client.sign_xml(
            tenant_slug=tenant_slug,
            actor_id=actor_id,
            payload_xml=current["xml"],
            objeto_id=str(expediente_id),
            indice_id=str(current["id"]),
            version=current["version"],
        )
        if signed is None:
            await self._mark_pendiente_audited(
                current["id"], expediente_id, tenant_slug=tenant_slug,
                actor_id=actor_id, motivo="sellado_xades_no_obtenido",
            )
            return None
        async with self._conn.transaction():
            affected = await self._index.mark_signed(
                current["id"], signed.firma_id, signed.minio_key_firmado
            )
            if affected:
                await audit.append(
                    self._conn,
                    tenant_slug=tenant_slug,
                    service=_SERVICE,
                    action="archive.indice_firmado",
                    object_type="expediente_indice",
                    object_ref=str(expediente_id),
                    actor=actor_id,
                    payload={
                        "expediente_id": str(expediente_id),
                        "firma_id": str(signed.firma_id),
                        "origen": "manual",
                    },
                )
        if not affected:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "indice_ya_firmado",
                    "detail": "El índice vigente ya está firmado (inmutable)",
                    "status": 409,
                },
            )
        return signed

    async def firmar_indice_close(
        self, expediente_id: uuid.UUID, *, tenant_slug: str, actor_id: str | None
    ) -> SignedIndice | None:
        """Control compensatorio del sellado en el CIERRE (residual Ac. AGN
        001/2024 art. 4.3.2.4) — invocado por `ArchiveService.close_expediente`,
        post-commit (RT-16), EN VEZ de `firmar_indice`.

        A diferencia de `firmar_indice` (un único intento, best-effort, usado
        por el reintento manual `POST /indice/firmar` vía `firmar_indice_retry`),
        aquí se intenta el sellado SÍNCRONAMENTE hasta
        `settings.close_sign_max_sync_attempts` veces (default 2, con
        `settings.close_sign_sync_delay_seconds` ~1s entre intentos) ANTES de
        degradar — el cierre del expediente NUNCA se bloquea (el UPDATE de
        transición ya se comprometió, ver `close_expediente`), pero un fallo
        transitorio de red no debe degradar de inmediato si un segundo
        intento un segundo después habría bastado. 2×~1s es el tope por
        diseño: este control NO debe alargar peligrosamente el request.

        Dictamen (archival-compliance-auditor) — distingue el MOTIVO del
        fallo, vía `signature_client.sign_xml_outcome` (resultado
        ESTRUCTURADO, a diferencia de `sign_xml` que colapsa todo fallo a
        `None`):
        - éxito → índice 'firmado' EN el cierre (art. 4.3.2.4 síncrono).
        - `seal_absent` (sello institucional AUSENTE) → degrada de inmediato,
          SIN reintentar: firmar es estructuralmente imposible en este
          instante — un segundo intento un segundo después no cambia nada.
          Motivo del asiento 'sello_ausente'; converge vía el job de
          reconciliación (`app/jobs/indice_reconciliation.py`) o el reintento
          manual, cuando el sello se aprovisione.
        - `transient` tras agotar los intentos síncronos → degrada. Motivo
          'sellado_xades_no_obtenido' (el mismo que `firmar_indice`) — el
          job de reconciliación converge con backoff exponencial.

        Atomicidad efecto<->asiento (H5, igual que `firmar_indice`): cada
        desenlace (firmado / pendiente_firma) se marca junto con su propio
        asiento en una única `conn.transaction()`. El sellado HTTP en sí
        (incluidos los reintentos síncronos de este método) sigue siendo
        100% posterior al commit de `close_expediente` — no hay HTTP dentro
        de ninguna transacción.
        """
        from app.clients import signature_client

        current = await self._current_signable_or_none(
            expediente_id, tenant_slug=tenant_slug, actor_id=actor_id
        )
        if current is None:
            return None

        max_attempts = max(1, settings.close_sign_max_sync_attempts)
        for attempt in range(1, max_attempts + 1):
            outcome = await signature_client.sign_xml_outcome(
                tenant_slug=tenant_slug,
                actor_id=actor_id,
                payload_xml=current["xml"],
                objeto_id=str(expediente_id),
                indice_id=str(current["id"]),
                version=current["version"],
            )
            if outcome.signed is not None:
                async with self._conn.transaction():
                    affected = await self._index.mark_signed(
                        current["id"], outcome.signed.firma_id, outcome.signed.minio_key_firmado
                    )
                    if affected:
                        await audit.append(
                            self._conn,
                            tenant_slug=tenant_slug,
                            service=_SERVICE,
                            action="archive.indice_firmado",
                            object_type="expediente_indice",
                            object_ref=str(expediente_id),
                            actor=actor_id,
                            payload={
                                "expediente_id": str(expediente_id),
                                "firma_id": str(outcome.signed.firma_id),
                                "origen": "close",
                            },
                        )
                # S1 (RF-FIR-14): `affected=0` -> otro proceso (típicamente
                # el job de reconciliación, ver `app/jobs/indice_reconciliation.py`)
                # ya había marcado 'firmado' esta misma fila entre la lectura
                # de `current` y este UPDATE -- el sello recién obtenido queda
                # huérfano y NO se escribe el asiento fantasma. El cierre del
                # expediente NUNCA se bloquea por esto (best-effort, RT-16):
                # se degrada a `None`, idéntico a cualquier otra rama de este
                # método -- `ArchiveService.close_expediente` ya ignora el
                # valor de retorno.
                if affected:
                    # E10 Inc.1: cableado WORM del índice recién firmado, FUERA
                    # de la tx que acaba de comprometerse arriba (mismo motivo
                    # que el propio sellado corre fuera de la tx del cierre —
                    # ver el docstring de `firmar_indice_close`). Si `affected`
                    # es 0 (carrera perdida, ver arriba), NO se dispara aquí:
                    # `run_once_worm` (3ra fase de reconciliación) converge de
                    # todos modos, ese índice ya quedó 'firmado' con
                    # `worm_protegido_at IS NULL`.
                    await self.proteger_worm_indice(
                        expediente_id, current["id"], actor_id=actor_id, origen="close"
                    )
                return outcome.signed if affected else None
            if outcome.seal_absent:
                await self._mark_pendiente_audited(
                    current["id"], expediente_id, tenant_slug=tenant_slug,
                    actor_id=actor_id, motivo="sello_ausente",
                )
                return None
            # `transient`: reintenta solo si quedan intentos — sin sleep tras
            # el ÚLTIMO intento (no alargar el request para nada).
            if attempt < max_attempts:
                await asyncio.sleep(settings.close_sign_sync_delay_seconds)

        await self._mark_pendiente_audited(
            current["id"], expediente_id, tenant_slug=tenant_slug,
            actor_id=actor_id, motivo="sellado_xades_no_obtenido",
        )
        return None

    async def firmar_indice_retry(
        self, expediente_id: uuid.UUID, *, tenant_slug: str, actor_id: str | None
    ) -> SignedIndice:
        """Reintento explícito del sellado del índice (E06 Inc.1, RT-16).

        A diferencia de `firmar_indice` (best-effort, invocado silenciosamente
        al cierre), este método SÍ reporta el resultado al llamador — pensado
        para `POST /expedientes/{id}/indice/firmar`:
        - 404 si el expediente no tiene índice.
        - 409 si el índice vigente ya está firmado (inmutable) — idempotente:
          reintentar sobre un índice ya sellado no es un error de negocio, es
          un no-op reportado explícitamente.
        - 503 si la firma sigue sin poder obtenerse (el índice permanece o
          vuelve a 'pendiente_firma', reintentable de nuevo más tarde).
        """
        current = await self._index.get_current(expediente_id)
        if current is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_indice_not_found",
                    "detail": str(expediente_id),
                    "status": 404,
                },
            )
        if current["estado"] == "firmado":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "indice_ya_firmado",
                    "detail": "El índice vigente ya está firmado (inmutable)",
                    "status": 409,
                },
            )
        signed = await self.firmar_indice(expediente_id, tenant_slug=tenant_slug, actor_id=actor_id)
        if signed is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "index_signing_failed",
                    "detail": "signature-service no pudo sellar el índice; queda pendiente_firma",
                    "status": 503,
                },
            )
        return signed

    async def list_pendientes_firma(
        self, *, page: int, size: int, user_id: uuid.UUID | None
    ) -> tuple[list[IndicePendienteFirma], int]:
        """Visibilidad operacional de índices 'pendiente_firma' (residual Ac.
        AGN 001/2024 art. 4.3.2.4, `GET /expedientes/indices/pendientes-firma`)
        — para que ningún índice sin firmar quede silenciado en `audit_log`:
        un archivista puede consultar directamente lo que el job de
        reconciliación (`app/jobs/indice_reconciliation.py`) todavía no ha
        convergido, incluidos los `reconcile_agotado=TRUE` que requieren
        intervención manual.

        No-read-up (RF-SEG-08): resuelve el clearance real del llamante y
        delega el filtrado en `IndexRepository.list_pending_signature_page`
        — mismo criterio (`nivel_seguridad <= clearance`) que
        `ArchiveRepository.list_expedientes`.
        """
        clearance = await self._archive.caller_clearance(user_id)
        rows, total = await self._index.list_pending_signature_page(
            max_clearance=clearance, offset=(page - 1) * size, limit=size
        )
        items = [
            IndicePendienteFirma(
                indice_id=r["indice_id"],
                expediente_id=r["expediente_id"],
                expediente_code=r["expediente_code"],
                version=r["version"],
                reconcile_intentos=r["reconcile_intentos"],
                reconcile_agotado=r["reconcile_agotado"],
                reconcile_ultimo_error=r["reconcile_ultimo_error"],
            )
            for r in rows
        ]
        return items, total

    async def list_pendientes_renovacion(
        self, *, page: int, size: int, user_id: uuid.UUID | None
    ) -> tuple[list[IndicePendienteRenovacion], int]:
        """Visibilidad operacional de artefactos WORM de series CT cuya
        renovación de retención está AGOTADA o EN RIESGO de vencer sin
        renovar (E10 debt, `GET /expedientes/indices/pendientes-renovacion`)
        -- gemela de `list_pendientes_firma` para el eje de RENOVACIÓN en vez
        del de SELLADO: complementa al job de reconciliación
        (`run_once_worm_renovacion`, `app/jobs/indice_reconciliation.py`) con
        visibilidad directa, incluidos los `worm_renovacion_agotado=TRUE` que
        requieren intervención manual URGENTE (un lock COMPLIANCE de
        conservación PERMANENTE a punto de expirar).

        No-read-up (RF-SEG-08): resuelve el clearance real del llamante y lo
        pasa a las TRES consultas (índices, actas y AIP) -- mismo criterio
        que `list_pendientes_firma`.

        UN endpoint combinado (`tipo` distingue `indice`/`acta_transferencia`/
        `aip` -- este último, E10 debt, fast-follow del cableado del AIP, ver
        `IndicePendienteRenovacion`) en vez de tres gemelos: el volumen
        esperado por tenant es pequeño (solo artefactos CT en riesgo), así que
        se combinan y paginan en MEMORIA en vez de un UNION ALL SQL entre tres
        grafos de JOIN distintos (`expediente_indice` vs.
        `transferencia_acta_firma`/`transferencias` vs. `expediente_aip`)."""
        clearance = await self._archive.caller_clearance(user_id)
        ct_codes = list(CT_DISPOSITION_CODES)
        ventana_dias = settings.worm_renovacion_ventana_dias

        indice_rows = await self._index.list_worm_renovacion_visibilidad(
            max_clearance=clearance, ct_codes=ct_codes, ventana_dias=ventana_dias
        )
        acta_rows = await TransferenciaRepository(
            self._conn, self._tenant_slug
        ).list_worm_renovacion_visibilidad(
            max_clearance=clearance, ct_codes=ct_codes, ventana_dias=ventana_dias
        )
        aip_rows = await AipRepository(self._conn).list_worm_renovacion_visibilidad(
            max_clearance=clearance, ct_codes=ct_codes, ventana_dias=ventana_dias
        )

        items = [
            IndicePendienteRenovacion(
                expediente_id=r["expediente_id"],
                expediente_code=r["expediente_code"],
                tipo="indice",
                worm_retain_until=r["worm_retain_until"],
                worm_renovacion_intentos=r["worm_renovacion_intentos"],
                worm_renovacion_agotado=r["worm_renovacion_agotado"],
                worm_renovacion_ultimo_error=r["worm_renovacion_ultimo_error"],
            )
            for r in indice_rows
        ] + [
            IndicePendienteRenovacion(
                expediente_id=r["expediente_id"],
                expediente_code=r["expediente_code"],
                tipo="acta_transferencia",
                worm_retain_until=r["worm_retain_until"],
                worm_renovacion_intentos=r["worm_renovacion_intentos"],
                worm_renovacion_agotado=r["worm_renovacion_agotado"],
                worm_renovacion_ultimo_error=r["worm_renovacion_ultimo_error"],
            )
            for r in acta_rows
        ] + [
            IndicePendienteRenovacion(
                expediente_id=r["expediente_id"],
                expediente_code=r["expediente_code"],
                tipo="aip",
                worm_retain_until=r["worm_retain_until"],
                worm_renovacion_intentos=r["worm_renovacion_intentos"],
                worm_renovacion_agotado=r["worm_renovacion_agotado"],
                worm_renovacion_ultimo_error=r["worm_renovacion_ultimo_error"],
            )
            for r in aip_rows
        ]
        # Más urgente primero: agotado antes que en-riesgo, y dentro de cada
        # grupo, el vencimiento más próximo primero. `datetime.max` (UTC) para
        # el `None` teórico (`worm_retain_until` no debería ser NULL aquí --
        # ambas consultas ya filtran `worm_retain_until IS NOT NULL` salvo la
        # rama `agotado=TRUE`, que sí podría cargar un NULL legado) evita que
        # ese caso se cuele al frente del orden.
        items.sort(
            key=lambda it: (
                not it.worm_renovacion_agotado,
                it.worm_retain_until or datetime.max.replace(tzinfo=UTC),
            )
        )
        total = len(items)
        start = (page - 1) * size
        return items[start : start + size], total

    def _fixity_from_row(self, exp: Expediente, row, items) -> IndiceFixityResult:
        """Comparación de huellas pura-BD sobre un `row`/`items` YA obtenidos
        (E12 H-G). Núcleo compartido por `fixity()` (autocontenido, una
        consulta propia por llamada) y `verify()` (reutiliza el `row`/`items`
        que YA necesita para el tramo de firma) -- evita que `verify()` pague
        una segunda consulta de `expediente_indice` solo por reusar esta
        comparación."""
        current = {str(r.radicado_id): r.content_hash for r in exp.radicados}
        docs: list[IndiceVerifyDoc] = []
        all_ok = True
        for it in items:
            hi = it["valor_huella"]
            ha = current.get(str(it["document_id"]))
            coincide = hi is not None and ha is not None and hi == ha
            if not coincide:
                all_ok = False
            docs.append(
                IndiceVerifyDoc(
                    document_id=it["document_id"],
                    huella_indice=hi,
                    huella_actual=ha,
                    coincide=coincide,
                )
            )
        huella_indice_ok = xml_sha256(row["xml"]) == row["xml_sha256"]
        return IndiceFixityResult(
            version=row["version"],
            documentos=docs,
            documentos_ok=all_ok,
            huella_indice_ok=huella_indice_ok,
        )

    async def fixity(
        self,
        expediente_id: uuid.UUID,
        version: int | None = None,
        *,
        max_clearance: int,
    ) -> IndiceFixityResult:
        """Fixity PURA-BD del índice electrónico (E12 H-G): `documentos[].coincide`
        (huella de cada documento del índice vs `content_hash` actual del radicado
        vigente) + `huella_indice_ok` (re-hash de la fila del índice vs
        `xml_sha256` persistido). SIN verificación de firma (HTTP) y SIN gate de
        lectura cuando se invoca con `CLEARANCE_SIN_RESTRICCION`.

        Autocontenido: resuelve `exp`/`row`/`items` con SUS PROPIAS consultas --
        pensado para `TransferenciaService.recibir` (`CLEARANCE_SIN_RESTRICCION`
        -- acto de custodia ortogonal al clearance de lectura, `core.clearance`
        bloque (c)), que no tiene ya un `row` en mano. `verify()` NO llama a este
        método (evitaría re-consultar `expediente_indice` que ya necesita para el
        tramo de firma): ambos comparten la comparación vía `_fixity_from_row`,
        un único sitio para que no diverjan si cambia el algoritmo o la semántica
        de exclusión. No llama `_audit_read_access`: no es una lectura de
        contenido por un usuario (mismo criterio que `verify` ya aplicaba antes
        de este refactor)."""
        exp = await self._archive.get_expediente_by_id(
            expediente_id, max_clearance=max_clearance
        )
        if exp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": str(expediente_id),
                    "status": 404,
                },
            )
        row = await self._row_or_404(expediente_id, version)
        items = await self._index.get_items(row["id"])
        return self._fixity_from_row(exp, row, items)

    async def verify(
        self,
        expediente_id: uuid.UUID,
        version: int | None,
        user_id: uuid.UUID | None = None,
    ) -> IndiceVerifyResponse:
        """Contrasta el valor_huella del índice con el hash actual de cada documento
        (detección de alteración) e informa el estado de la firma (E15 T-14).

        No-read-up (RF-SEG-08): resuelve el expediente con el clearance real
        del llamante ANTES de exponer nada del índice — antes se consultaba
        con el default sin restricción, dejando esta ruta como un camino
        alterno para leer un expediente CLASIFICADA sin la clearance debida
        (aunque `GET /{id}` ya lo negara con 404)."""
        max_clearance = await self._archive.caller_clearance(user_id)
        exp = await self._archive.get_expediente_by_id(
            expediente_id, max_clearance=max_clearance
        )
        if exp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": str(expediente_id),
                    "status": 404,
                },
            )
        row = await self._row_or_404(expediente_id, version)
        items = await self._index.get_items(row["id"])
        # Fixity (documentos + huella del índice): comparación pura-BD
        # compartida con `fixity()` (E12 H-G) vía `_fixity_from_row` -- mismo
        # `row`/`items` que este método YA necesitaba para el tramo de firma,
        # sin re-consultar `expediente_indice`.
        fx = self._fixity_from_row(exp, row, items)
        docs = fx.documentos
        # Acoplamiento firma↔índice (E06 Inc.1): si hay firma, se descarga el
        # XML YA FIRMADO desde MinIO (vía storage-service) y se verifica la
        # firma XAdES-B real contra ESE contenido — nunca contra `row["xml"]`
        # en claro (la canonicalización C14N 2.0 de exhibición no coincide con
        # el digest exc-c14n de XAdES, ver `app.clients.storage_client`). Es
        # best-effort en cada tramo: si el XML firmado no se puede obtener, o
        # el signature-service no responde, `valida` queda en False (no se
        # declara conforme lo que no se pudo verificar, RF-FIR-15).
        firma_presente = row["firma_id"] is not None
        valida_firma = False
        nivel_conformidad: str | None = None
        acreditado = False
        motivo: str | None = None
        if firma_presente:
            from app.clients import signature_client, storage_client

            signed_xml: str | None = None
            if row["minio_key_firmado"]:
                signed_xml = await storage_client.download_signed_xml(
                    tenant_slug=self._tenant_slug,
                    minio_key_firmado=row["minio_key_firmado"],
                )
            if signed_xml is None:
                motivo = "signed_xml_unavailable"
            else:
                resultado = await signature_client.verify(
                    tenant_slug=self._tenant_slug,
                    firma_id=str(row["firma_id"]),
                    payload_xml=signed_xml,
                )
                if resultado is None:
                    motivo = "signature_service_unreachable"
                else:
                    valida_firma = resultado.valida
                    nivel_conformidad = resultado.nivel_conformidad
                    acreditado = resultado.acreditado
                    motivo = resultado.motivo
        firma = IndiceVerifyFirma(
            presente=firma_presente,
            valida=valida_firma,
            nivel_conformidad=nivel_conformidad,
            acreditado=acreditado,
            motivo=motivo,
        )
        # Fixity del XML del índice en sí (no solo de los documentos que
        # referencia): re-hashea la columna `xml` almacenada y la contrasta
        # con `xml_sha256` persistido. Sin esto, una alteración directa del
        # XML de un índice 'vigente'/'pendiente_firma' (aún no sellado) queda
        # indetectada — el sellado XAdES solo protege la COPIA firmada en
        # MinIO, no la fila en BD (Acuerdo AGN 001/2024, E15). Ambos valores
        # vienen de `fx` (`fixity()`, E12 H-G) — ya no se recalculan aquí.
        valido = fx.documentos_ok and fx.huella_indice_ok and (not firma_presente or firma.valida)
        return IndiceVerifyResponse(
            version=row["version"],
            valido=valido,
            huella_indice_ok=fx.huella_indice_ok,
            documentos=docs,
            firma=firma,
            fecha_verificacion=datetime.now(tz=UTC),
        )

    async def list_versions(
        self,
        expediente_id: uuid.UUID,
        *,
        page: int,
        size: int,
        user_id: uuid.UUID | None = None,
    ) -> tuple[list[IndiceVersionSummary], int]:
        max_clearance = await self._archive.caller_clearance(user_id)
        await self._assert_visible(expediente_id, max_clearance)
        rows, total = await self._index.list_versions(
            expediente_id, offset=(page - 1) * size, limit=size
        )
        return [
            IndiceVersionSummary(
                version=r["version"],
                estado=r["estado"],
                num_documentos=r["num_documentos"],
                xml_sha256=r["xml_sha256"],
                generado_ts=r["generado_ts"],
                generado_por=r["generado_por"],
            )
            for r in rows
        ], total

    # -- E10 Inc.1: cableado WORM/Object-Lock del índice ----------------

    async def _resolve_worm_retention(
        self, expediente_id: uuid.UUID
    ) -> tuple[int, date] | None:
        """Deriva ``(retencion_anios_total, fecha_inicio=fecha_cierre)`` de la
        TRD del expediente para el cableado WORM (E10 Inc.1, AGN 001/2024
        Art. 4.3.2.6) — ``retencion_anios_total`` es la suma de las DOS fases
        (`archivo_gestion_years` + `archivo_central_years`), mismo cálculo
        que `ArchiveService.compute_retention` (que reporta cada fase por
        separado; aquí solo interesa el TOTAL que exige `RetentionMixin` de
        storage-service).

        Fail-closed (``None``) en cualquier caso dudoso: expediente
        inexistente, sin `closed_at` (no debería invocarse antes del cierre,
        pero no se asume), sin `trd_serie_id`, serie no encontrada, o el
        total fuera del rango ``[1, 100]`` años que exige `RetentionMixin`
        del lado de storage-service (defensa en profundidad — doble
        validación en ambos extremos del cableado, igual que
        `validate_retain_until` ya hace del lado de storage-service).

        ADR-026 (Increment 2, D4) — fail-closed TAMBIÉN para
        `exp.origen == 'fondo_acumulado'`: esta función usa `fecha_inicio=
        exp.closed_at` (hoy, en la práctica), que es EXACTAMENTE la base
        incorrecta que D4 advierte que produce un `retain_until` COMPLIANCE
        imborrable durante décadas de más. El Increment 2 relaja la Capa 4
        (migración 041) y por primera vez permite que un expediente de fondo
        acumulado se CIERRE -- pero cablear WORM con la fecha correcta
        (`fecha_extrema_final`, más el clamp de calendario ya vencido, D4.4)
        es explícitamente el Increment 3 de ese ADR. Hasta que ese incremento
        exista, ningún fondo acumulado debe alimentar Object-Lock: el `None`
        de aquí dispara el backoff habitual (`_bump_worm_backoff`,
        `motivo='trd_sin_retencion'`) -- no revienta, no cablea, solo
        pospone -- exactamente lo que D4 exige ("ningún test... exige
        integración... antes de habilitar cualquier ruta WORM para fondo
        acumulado")."""
        exp = await self._archive.get_expediente_by_id(
            expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
        )
        if exp is None or exp.closed_at is None or exp.trd_serie_id is None:
            return None
        if exp.origen == "fondo_acumulado":
            return None
        serie = await self._archive.get_trd_serie_by_id(exp.trd_serie_id)
        if serie is None:
            return None
        total = total_trd_years(serie.archivo_gestion_years, serie.archivo_central_years)
        if total is None:
            return None
        return total, exp.closed_at.date()

    async def _resolve_ct_retencion_anios(self, expediente_id: uuid.UUID) -> int | None:
        """Total TRD (ambas fases) del expediente -- versión de RENOVACIÓN
        (E10 debt) de `_resolve_worm_retention`: la ventana rodante de
        renovación siempre usa `fecha_inicio=hoy` (no `closed_at`), así que
        este helper SOLO necesita el total y no exige `exp.closed_at`
        (aunque en la práctica siempre está poblado -- un índice solo llega a
        `worm_protegido_at IS NOT NULL` tras el cierre).

        ADR-026 (Increment 2, D4): inalcanzable en la práctica hoy --
        `_resolve_worm_retention` ya nunca protege un fondo acumulado
        (fail-closed, ver esa docstring), así que ningún índice de fondo
        acumulado llega a `worm_protegido_at IS NOT NULL` y por tanto nunca
        entra en la ventana de renovación. Guard igual, por defensa en
        profundidad: no depender de esa invariante ajena para no cablear
        WORM con la fecha equivocada."""
        exp = await self._archive.get_expediente_by_id(
            expediente_id, max_clearance=CLEARANCE_SIN_RESTRICCION
        )
        if exp is None or exp.trd_serie_id is None:
            return None
        if exp.origen == "fondo_acumulado":
            return None
        serie = await self._archive.get_trd_serie_by_id(exp.trd_serie_id)
        if serie is None:
            return None
        return total_trd_years(serie.archivo_gestion_years, serie.archivo_central_years)

    async def _bump_worm_backoff(
        self, indice_id: uuid.UUID, *, intentos_previos: int, motivo: str
    ) -> Literal["pendiente", "agotado"]:
        """Registra un intento fallido de cableado WORM (backoff exponencial,
        mismo umbral `indice_reconciliation_max_intentos` que el sellado
        XAdES-B — eje independiente, ver migración 029). `intentos_previos`
        viene del `row` YA leído por el llamador (`worm_reconcile_intentos`)
        — no se re-consulta, evita una lectura extra en el camino caliente."""
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
        await self._index.bump_worm_reconcile_intento(
            indice_id, proximo_at=proximo_at, ultimo_error=motivo, agotado=agotado
        )
        if agotado:
            # Best-effort (mismo patrón que `audit.append_denial`): la
            # visibilidad operacional de un agotamiento nunca debe convertir
            # un fallo de cableado (ya de por sí best-effort) en un 500.
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.indice_worm_reconcile_agotado",
                    object_type="expediente_indice",
                    object_ref=str(indice_id),
                    # Acto de sistema (reconciliación): principal de sistema, no
                    # `None` (deuda E10, unificación de actor).
                    actor=str(SYSTEM_RECONCILER_ID),
                    canal="system",
                    payload={"reconcile_intentos": intentos_tras_este, "motivo": motivo},
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca degrada el resultado.
                logger.warning(
                    "audit.append(indice_worm_reconcile_agotado) failed", exc_info=True
                )
            return "agotado"
        return "pendiente"

    async def proteger_worm_indice(
        self,
        expediente_id: uuid.UUID,
        indice_id: uuid.UUID,
        *,
        actor_id: str | None,
        origen: Literal["close", "reconciliation"],
    ) -> Literal["protegido", "pendiente", "agotado", "omitido"]:
        """Cableado WORM/Object-Lock del índice YA FIRMADO (E10 Inc.1, AGN
        001/2024 Art. 4.3.2.6): tras confirmarse el sellado XAdES-B —
        síncronamente dentro de `firmar_indice_close` (`origen="close"`), o
        vía `mark_signed` en la 3ra fase de reconciliación
        (`origen="reconciliation"`, `app/jobs/indice_reconciliation.py::
        run_once_worm`) — dispara la protección inmutable en storage-service.

        Best-effort, NUNCA lanza (best-effort real: cualquier excepción no
        prevista queda contenida aquí, no solo los fallos HTTP esperados) —
        mismo patrón que `firmar_indice_close`: el cierre del expediente ya
        es un hecho consumado, este cableado NUNCA lo bloquea ni lo revierte.
        Fuera de cualquier `conn.transaction()` del llamador — la llamada
        HTTP a storage-service no debe correr dentro de una tx (mismo motivo
        que `firmar_indice_close`: no se puede revertir un POST con
        ROLLBACK, y retendría el advisory lock de auditoría del tenant
        durante la latencia HTTP).

        Frontera de confianza (D-02): `file_id` se deriva SIEMPRE de
        `expediente_indice.minio_key_firmado` (formato `"{file_id}/
        {filename}"`, ver `storage_client.download_signed_xml`) — nunca de
        una entrada externa al servicio.

        Devuelve el desenlace para que el llamador (job de reconciliación)
        lleve su propio resumen; el disparo síncrono del cierre ignora el
        valor de retorno (ya lo tenía así con `firmar_indice_close`)."""
        try:
            row = await self._index.get_by_id(indice_id)
            if row is None or row["estado"] != "firmado":
                return "omitido"
            if row["worm_protegido_at"] is not None:
                return "omitido"
            if not row["minio_key_firmado"]:
                logger.warning(
                    "Cableado WORM: índice %s (expediente_id=%s) sin "
                    "minio_key_firmado, se omite",
                    indice_id,
                    expediente_id,
                )
                return "omitido"
            file_id = row["minio_key_firmado"].split("/", 1)[0]

            retention = await self._resolve_worm_retention(expediente_id)
            if retention is None:
                return await self._bump_worm_backoff(
                    indice_id,
                    intentos_previos=int(row["worm_reconcile_intentos"]),
                    motivo="trd_sin_retencion",
                )
            retencion_anios, fecha_inicio = retention

            from app.clients import storage_client

            if origen == "close" and actor_id:
                outcome = await storage_client.proteger_indice_worm(
                    tenant_slug=self._tenant_slug,
                    actor_id=actor_id,
                    file_id=file_id,
                    expediente_id=str(expediente_id),
                    retencion_anios=retencion_anios,
                    fecha_inicio=fecha_inicio,
                )
            else:
                outcome = await storage_client.proteger_indice_worm_internal(
                    tenant_slug=self._tenant_slug,
                    file_id=file_id,
                    expediente_id=str(expediente_id),
                    retencion_anios=retencion_anios,
                    fecha_inicio=fecha_inicio,
                )

            if outcome.ok:
                async with self._conn.transaction():
                    affected = await self._index.mark_worm_protegido(
                        indice_id,
                        outcome.worm_object_id,
                        worm_retain_until=outcome.retain_until,
                    )
                    # Idempotencia real (mismo patrón S1 que `mark_signed`):
                    # `affected == 0` -> otra llamada concurrente (el disparo
                    # síncrono del cierre corriendo en paralelo con esta
                    # barrida, o dos barridas solapadas) ya marcó protegido
                    # este MISMO índice entre la lectura de `row` y este
                    # UPDATE. NO se escribe un asiento fantasma sobre un
                    # efecto que esta llamada no produjo.
                    if affected:
                        await audit.append(
                            self._conn,
                            tenant_slug=self._tenant_slug,
                            service=_SERVICE,
                            action="archive.indice_worm_protegido",
                            object_type="expediente_indice",
                            object_ref=str(expediente_id),
                            actor=actor_id,
                            canal="system" if origen == "reconciliation" else "api",
                            payload={
                                "expediente_id": str(expediente_id),
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
            return await self._bump_worm_backoff(
                indice_id, intentos_previos=int(row["worm_reconcile_intentos"]), motivo=motivo
            )
        except Exception:
            logger.warning(
                "Cableado WORM del índice falló (best-effort), expediente_id=%s "
                "indice_id=%s origen=%s",
                expediente_id,
                indice_id,
                origen,
                exc_info=True,
            )
            return "pendiente"

    # -- E10 debt: renovación de retención WORM para series CT ---------

    async def _bump_worm_renovacion_backoff(
        self, indice_id: uuid.UUID, *, intentos_previos: int, motivo: str
    ) -> Literal["pendiente", "agotado"]:
        """Registra un intento fallido de RENOVACIÓN de retención WORM (E10
        debt) — gemelo de `_bump_worm_backoff` (protección inicial) para el
        eje de renovación, mismo umbral/backoff (`indice_reconciliation_*`,
        reutilizados sin duplicar). `agotado=True` aquí es una alarma
        operacional MÁS SERIA que en el eje de protección/sellado: significa
        que un artefacto de conservación PERMANENTE (serie CT) podría perder
        su lock COMPLIANCE al vencer `worm_retain_until` sin haberse podido
        extender — requiere intervención manual URGENTE, no solo eventual."""
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
        await self._index.bump_worm_renovacion_intento(
            indice_id, proximo_at=proximo_at, ultimo_error=motivo, agotado=agotado
        )
        if agotado:
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.indice_worm_renovacion_agotado",
                    object_type="expediente_indice",
                    object_ref=str(indice_id),
                    actor=str(SYSTEM_RECONCILER_ID),
                    canal="system",
                    payload={"renovacion_intentos": intentos_tras_este, "motivo": motivo},
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca degrada el resultado.
                logger.warning(
                    "audit.append(indice_worm_renovacion_agotado) failed", exc_info=True
                )
            return "agotado"
        return "pendiente"

    async def renovar_worm_indice(
        self,
        expediente_id: uuid.UUID,
        indice_id: uuid.UUID,
        *,
        intentos_previos: int,
    ) -> Literal["renovado", "omitido", "pendiente", "agotado"]:
        """Extiende (E10 debt, AGN 001/2024 Art. 4.3.2.6) la retención WORM
        del índice de un expediente de serie Conservación Total (CT) ANTES de
        que su `retain_until` vigente venza — 5ª fase de reconciliación
        (`run_once_worm_renovacion`, `app/jobs/indice_reconciliation.py`).

        Solo se llama sobre filas que YA pasaron el filtro CT en SQL
        (`IndexRepository.list_worm_renovacion_pending`) — este método NO
        vuelve a comprobar disposición, solo resuelve el total TRD vigente
        (puede haber cambiado desde la protección inicial) y llama a la
        renovación. Ventana RODANTE: `fecha_inicio=hoy`, nunca una fecha
        lejana/9999 (roadmap de irreversibilidad acotada).

        Best-effort, NUNCA lanza — mismo patrón que `proteger_worm_indice`:
        un fallo de renovación NUNCA debe tumbar el job de reconciliación."""
        try:
            retencion_anios = await self._resolve_ct_retencion_anios(expediente_id)
            if retencion_anios is None:
                return await self._bump_worm_renovacion_backoff(
                    indice_id, intentos_previos=intentos_previos, motivo="trd_sin_retencion"
                )

            from app.clients import storage_client

            outcome = await storage_client.renovar_retencion_worm_internal(
                tenant_slug=self._tenant_slug,
                expediente_id=str(expediente_id),
                tipo="indice",
                retencion_anios=retencion_anios,
                fecha_inicio=datetime.now(tz=UTC).date(),
            )

            if outcome.ok:
                assert outcome.retain_until is not None
                async with self._conn.transaction():
                    await self._index.mark_worm_renovado(
                        indice_id, worm_retain_until=outcome.retain_until
                    )
                    if outcome.renovado:
                        await audit.append(
                            self._conn,
                            tenant_slug=self._tenant_slug,
                            service=_SERVICE,
                            action="archive.indice_worm_retencion_renovada",
                            object_type="expediente_indice",
                            object_ref=str(expediente_id),
                            actor=str(SYSTEM_RECONCILER_ID),
                            canal="system",
                            payload={
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
            return await self._bump_worm_renovacion_backoff(
                indice_id, intentos_previos=intentos_previos, motivo=motivo
            )
        except Exception:
            logger.warning(
                "Renovación de retención WORM del índice falló (best-effort), "
                "expediente_id=%s indice_id=%s",
                expediente_id,
                indice_id,
                exc_info=True,
            )
            return "pendiente"
