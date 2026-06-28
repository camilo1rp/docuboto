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

El `valor_huella` por documento se poblará desde storage-service (fixity, E15 T-08);
la firma XAdES al cierre es E15 F3 (depende de signature-service). La validación XSD
estricta (lxml) queda como evolutivo.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status

from app.models.archive import Expediente
from app.repositories.archive_repository import ArchiveRepository
from app.repositories.index_repository import IndexRepository
from app.schemas.archive import (
    IndiceItemResponse,
    IndiceResponse,
    IndiceVerifyDoc,
    IndiceVerifyFirma,
    IndiceVerifyResponse,
    IndiceVersionSummary,
    RebuildResult,
)
from app.services.index_builder import (
    IndiceData,
    IndiceItemData,
    build_xml,
    content_hash,
    xml_sha256,
)


class IndexService:
    def __init__(self, conn, tenant_slug: str) -> None:
        self._index = IndexRepository(conn)
        self._archive = ArchiveRepository(conn, tenant_slug)
        self._tenant_slug = tenant_slug

    # -- construcción --------------------------------------------------

    def _items_from(self, exp: Expediente) -> list[IndiceItemData]:
        return [
            IndiceItemData(
                document_id=str(r.radicado_id),
                orden=i,
                titulo=r.tracking_number,
                fecha_archivado=r.added_at,
                valor_huella=r.content_hash,  # SHA-256 aportado al vincular (T-08)
            )
            for i, r in enumerate(exp.radicados, start=1)
        ]

    def _data(self, exp: Expediente, items: list[IndiceItemData]) -> IndiceData:
        return IndiceData(
            expediente_id=str(exp.id),
            code=exp.code,
            name=exp.name,
            serie_id=str(exp.trd_serie_id) if exp.trd_serie_id else None,
            fecha_creacion=exp.opened_at,
            items=items,
        )

    # -- creación / regeneración --------------------------------------

    async def create_version_zero(self, exp: Expediente) -> None:
        """Índice versión 0 (sin documentos) al crear el expediente."""
        items: list[IndiceItemData] = []
        xml = build_xml(self._data(exp, items))
        await self._index.insert_version(
            expediente_id=exp.id,
            version=0,
            xml=xml,
            xml_sha256=xml_sha256(xml),
            content_hash=content_hash(items),
            num_documentos=0,
            generado_por="system",
            items=items,
        )

    async def regenerate(self, expediente_id: uuid.UUID, *, generado_por: str) -> RebuildResult:
        exp = await self._archive.get_expediente_by_id(expediente_id)
        if exp is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "expediente_not_found",
                    "detail": str(expediente_id),
                    "status": 404,
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
        ch = content_hash(items)
        if current is not None and current["content_hash"] == ch:
            return RebuildResult(
                version=current["version"],
                xml_sha256=current["xml_sha256"],
                num_documentos=current["num_documentos"],
                unchanged=True,
            )
        version = (current["version"] + 1) if current is not None else 0
        xml = build_xml(self._data(exp, items))
        await self._index.insert_version(
            expediente_id=expediente_id,
            version=version,
            xml=xml,
            xml_sha256=xml_sha256(xml),
            content_hash=ch,
            num_documentos=len(items),
            generado_por=generado_por,
            items=items,
        )
        return RebuildResult(version=version, xml_sha256=xml_sha256(xml), num_documentos=len(items))

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
                    fecha_archivado=it["fecha_archivado"],
                    valor_huella=it["valor_huella"],
                    funcion_resumen=it["funcion_resumen"],
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

    async def get_indice(self, expediente_id: uuid.UUID, version: int | None) -> IndiceResponse:
        return await self._to_response(await self._row_or_404(expediente_id, version))

    async def get_xml(self, expediente_id: uuid.UUID, version: int | None) -> str:
        return (await self._row_or_404(expediente_id, version))["xml"]

    async def firmar_indice(
        self, expediente_id: uuid.UUID, *, tenant_slug: str, firmante_id: str | None
    ) -> uuid.UUID | None:
        """Firma la versión vigente del índice vía signature-service (E15-F3, E06).
        Best-effort: si la firma no se obtiene, el índice queda vigente (no firmado)."""
        import uuid as _uuid

        from app.clients import signature_client

        current = await self._index.get_current(expediente_id)
        if current is None or current["estado"] == "firmado":
            return None
        firma_id = await signature_client.sign_xml(
            tenant_slug=tenant_slug,
            payload_xml=current["xml"],
            objeto_id=str(expediente_id),
            firmante_id=firmante_id,
        )
        if firma_id:
            await self._index.mark_signed(current["id"], _uuid.UUID(firma_id))
            return _uuid.UUID(firma_id)
        return None

    async def verify(self, expediente_id: uuid.UUID, version: int | None) -> IndiceVerifyResponse:
        """Contrasta el valor_huella del índice con el hash actual de cada documento
        (detección de alteración) e informa el estado de la firma (E15 T-14)."""
        row = await self._row_or_404(expediente_id, version)
        items = await self._index.get_items(row["id"])
        exp = await self._archive.get_expediente_by_id(expediente_id)
        current = {
            str(r.radicado_id): r.content_hash for r in (exp.radicados if exp is not None else [])
        }
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
        # Acoplamiento firma↔índice (E15 → E06): si hay firma, se verifica contra
        # el signature-service recomputando el HMAC sobre el XML almacenado. Es
        # best-effort: si el servicio no responde, `valida` queda en False (no se
        # declara conforme lo que no se pudo verificar, RF-FIR-15).
        firma_presente = row["firma_id"] is not None
        valida_firma = False
        if firma_presente:
            from app.clients import signature_client

            resultado = await signature_client.verify(
                tenant_slug=self._tenant_slug,
                firma_id=str(row["firma_id"]),
                payload_xml=row["xml"],
            )
            valida_firma = bool(resultado)
        firma = IndiceVerifyFirma(presente=firma_presente, valida=valida_firma)
        valido = all_ok and (not firma_presente or firma.valida)
        return IndiceVerifyResponse(
            version=row["version"],
            valido=valido,
            documentos=docs,
            firma=firma,
            fecha_verificacion=datetime.now(tz=UTC),
        )

    async def list_versions(
        self, expediente_id: uuid.UUID, *, page: int, size: int
    ) -> tuple[list[IndiceVersionSummary], int]:
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
