# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Acceso a datos para la exportación interoperable (E11, RF-INT-01). SQL crudo;
search_path fijado al schema del tenant por get_tenant_conn.

**No-read-up (RF-SEG-08, Ley 1712/2014 arts. 18-19, spec §5 inv. 11):** el export
solo incluye radicados con `nivel_seguridad <= max_clearance` del llamante; los
sobre-clearance se OMITEN (y se reportan content-free en el manifiesto, sin asunto
ni número). El `COUNT` de excluidos usa el MISMO filtro que el `SELECT` → el conteo
del manifiesto no delata datos de un reservado (solo su cantidad, agregada)."""

import uuid

import asyncpg

_RAD_COLS = (
    "id, tracking_number, doc_type, year, sequence, subject, doc_class, "
    "origin_dept, dest_dept, sender_name, sender_entity, status, pages, "
    "observations, registered_at, updated_at, tenant_code, nivel_seguridad, "
    "metadata, disposition, clasificacion_fundamento, clasificacion_causal, "
    "clasificacion_plazo_meses, clasificado_at, ubicacion_dept_code, ubicacion_dept"
)


class ExportRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    def _where(self, max_clearance: int, year: int | None, doc_type: str | None) -> tuple[str, list]:
        clauses = ["nivel_seguridad <= $1", "status <> 'anulado'"]
        params: list = [max_clearance]
        if year is not None:
            params.append(year)
            clauses.append(f"year = ${len(params)}")
        if doc_type is not None:
            params.append(doc_type)
            clauses.append(f"doc_type = ${len(params)}")
        return " AND ".join(clauses), params

    async def list_radicados(
        self, *, max_clearance: int, year: int | None, doc_type: str | None
    ) -> list[asyncpg.Record]:
        where, params = self._where(max_clearance, year, doc_type)
        # `antecedente` = tracking_number del radicado al que ESTE responde (relación
        # E↔S, mig 014 `responde_a` UUID). Se exporta el NÚMERO (identidad portable),
        # no el UUID interno, para que INT-05 pueda reconstruir el vínculo (totalidad
        # referencial, spec §5 RN-3). Subconsulta correlacionada; el `r.responde_a`
        # apunta a un radicado del MISMO schema del tenant (search_path fijado).
        return await self._conn.fetch(
            f"SELECT {_RAD_COLS}, "
            "(SELECT ant.tracking_number FROM radicados ant WHERE ant.id = r.responde_a) "
            "AS antecedente "
            f"FROM radicados r WHERE {where} ORDER BY registered_at, tracking_number",
            *params,
        )

    async def count_incluidos(
        self, *, max_clearance: int, year: int | None, doc_type: str | None
    ) -> int:
        """Cuántos radicados entrarían (mismo filtro que `list_radicados`) — para el
        tope defensivo antes de armar el ZIP en memoria (Seg #2, anti-DoS)."""
        where, params = self._where(max_clearance, year, doc_type)
        return await self._conn.fetchval(
            f"SELECT count(*) FROM radicados WHERE {where}", *params
        )

    async def count_excluidos(
        self, *, max_clearance: int, year: int | None, doc_type: str | None
    ) -> int:
        """Cuántos radicados quedan FUERA por sobre-clearance (mismo filtro salvo el
        nivel invertido) — para el manifiesto content-free (Ac. AGN 001/2024 4.3.2.3:
        el hueco del excluido es trazable, pero sin exponer su contenido)."""
        clauses = ["nivel_seguridad > $1", "status <> 'anulado'"]
        params: list = [max_clearance]
        if year is not None:
            params.append(year)
            clauses.append(f"year = ${len(params)}")
        if doc_type is not None:
            params.append(doc_type)
            clauses.append(f"doc_type = ${len(params)}")
        return await self._conn.fetchval(
            f"SELECT count(*) FROM radicados WHERE {' AND '.join(clauses)}", *params
        )

    async def list_anexos(self, radicado_id: uuid.UUID) -> list[asyncpg.Record]:
        return await self._conn.fetch(
            "SELECT file_id, filename, file_size, mime_type FROM anexos "
            "WHERE radicado_id = $1 ORDER BY created_at",
            radicado_id,
        )

    async def history(self, *, tenant_slug: str, tracking_number: str) -> list[asyncpg.Record]:
        """Historial de eventos del radicado desde la audit_log INMUTABLE (E08). Se
        exportan action/fecha/actor/canal (la traza de eventos) SIN el `payload`
        crudo: el payload es detalle INTERNO y heterogéneo del evento (hashes,
        snapshots, contexto operativo), no un campo curado de intercambio. La
        metadata SUSTANTIVA del radicado —incluido el fundamento de clasificación—
        viaja de forma explícita en el bloque `clasificacion` del propio radicado (el
        export es un egreso AUTORIZADO: el llamante ya tiene clearance sobre esas
        filas; la difusión posterior del paquete es responsabilidad del receptor)."""
        return await self._conn.fetch(
            """
            SELECT action, ts, actor, canal
              FROM public.audit_log
             WHERE tenant_slug = $1 AND object_type = 'radicado' AND object_ref = $2
             ORDER BY id
            """,
            tenant_slug,
            tracking_number,
        )
