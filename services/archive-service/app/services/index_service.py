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

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.core.clearance import CLEARANCE_SIN_RESTRICCION
from app.models.archive import Expediente
from app.repositories.archive_repository import ArchiveRepository
from app.repositories.index_repository import IndexRepository
from app.schemas.archive import (
    IndiceFixityResult,
    IndiceItemResponse,
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
            await self._index.mark_pendiente(indice_id)
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
        """
        from app.clients import signature_client

        current = await self._index.get_current(expediente_id)
        if current is None or current["estado"] == "firmado":
            return None
        if not actor_id:
            await self._mark_pendiente_audited(
                current["id"], expediente_id, tenant_slug=tenant_slug,
                actor_id=actor_id, motivo="sin_actor_id",
            )
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
            await self._index.mark_signed(current["id"], signed.firma_id, signed.minio_key_firmado)
            await audit.append(
                self._conn,
                tenant_slug=tenant_slug,
                service=_SERVICE,
                action="archive.indice_firmado",
                object_type="expediente_indice",
                object_ref=str(expediente_id),
                actor=actor_id,
                payload={"expediente_id": str(expediente_id), "firma_id": str(signed.firma_id)},
            )
        return signed

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
