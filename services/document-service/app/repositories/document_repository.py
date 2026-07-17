# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Document repository — all direct asyncpg calls live here.

Tenant isolation: the connection arrives with its search_path already set to
tenant_{slug} by the get_tenant_conn dependency (ADR-002/012), so every query
hits the tenant's own tables. The slug is kept only to resolve the tenant code
from public.tenants for tracking-number formatting.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, date, datetime

import asyncpg

from app.models.document import Anexo, Radicado
from app.schemas.document import AnexoInput, DocumentCreate, DocumentUpdate

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,98}$")


class InvalidTenantSlugError(Exception):
    pass


class DocumentRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        if not _SLUG_RE.match(tenant_slug):
            raise InvalidTenantSlugError(f"Invalid tenant slug: {tenant_slug!r}")
        # El search_path al schema del tenant lo fija get_tenant_conn (ADR-012);
        # el repositorio sólo usa el slug para resolver el código en public.tenants.
        self._conn = conn
        self._slug = tenant_slug

    async def get_tenant_code(self) -> str:
        """Look up the tenant's tracking-number code from public.tenants."""
        row = await self._conn.fetchrow(
            "SELECT code FROM public.tenants WHERE slug = $1", self._slug
        )
        if row is None:
            return self._slug.upper()
        return row["code"]

    # ------------------------------------------------------------------
    # Tracking number sequence — atomic via INSERT … ON CONFLICT DO UPDATE
    # ------------------------------------------------------------------

    async def next_sequence(self, year: int, doc_type: str) -> int:
        seq: int = await self._conn.fetchval(
            """
            INSERT INTO tracking_sequences (year, doc_type, last_seq)
            VALUES ($1, $2, 1)
            ON CONFLICT (year, doc_type)
            DO UPDATE SET last_seq = tracking_sequences.last_seq + 1
            RETURNING last_seq
            """,
            year,
            doc_type,
        )
        return seq

    # ------------------------------------------------------------------
    # Radicados
    # ------------------------------------------------------------------

    async def create(
        self,
        payload: DocumentCreate,
        tracking_number: str,
        year: int,
        sequence: int,
        tenant_code: str,
        registered_by: uuid.UUID | None,
        due_date: date | None,
        metadata_template_id: uuid.UUID | None,
    ) -> Radicado:
        row = await self._conn.fetchrow(
            """
            INSERT INTO radicados (
                tracking_number, doc_type, year, sequence,
                subject, doc_class,
                origin_dept, dest_dept,
                origin_dept_code, dest_dept_code,
                sender_name, sender_entity,
                pages, observations,
                registered_by, tenant_code, due_date,
                metadata, metadata_template_id,
                nivel_seguridad
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6,
                $7, $8,
                $9, $10,
                $11, $12,
                $13, $14,
                $15, $16, $17,
                $18::jsonb, $19,
                $20
            )
            RETURNING
                id, tracking_number, doc_type, year, sequence,
                subject, doc_class, origin_dept, dest_dept,
                origin_dept_code, dest_dept_code,
                sender_name, sender_entity, status, pages, nivel_seguridad, observations,
                registered_by, registered_at, updated_at, tenant_code, due_date,
                metadata, metadata_template_id
            """,
            tracking_number,
            payload.doc_type,
            year,
            sequence,
            payload.subject,
            payload.doc_class,
            payload.origin_dept,
            payload.dest_dept,
            payload.origin_dept_code,
            payload.dest_dept_code,
            payload.sender_name,
            payload.sender_entity,
            payload.pages,
            payload.observations,
            registered_by,
            tenant_code,
            due_date,
            json.dumps(payload.metadata),
            metadata_template_id,
            payload.nivel_seguridad,
        )
        radicado = _row_to_radicado(row)

        if payload.anexos:
            radicado.anexos = await self._create_anexos(radicado.id, payload.anexos)

        return radicado

    async def _create_anexos(self, radicado_id: uuid.UUID, anexos: list[AnexoInput]) -> list[Anexo]:
        # search_path is already set by the caller (create method)
        created: list[Anexo] = []
        for a in anexos:
            row = await self._conn.fetchrow(
                """
                INSERT INTO anexos (
                    radicado_id, file_id, filename, file_size, mime_type,
                    tipo_anexo, es_principal, folios
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id, radicado_id, file_id, filename, file_size, mime_type,
                          created_at, tipo_anexo, es_principal, folios
                """,
                radicado_id,
                a.file_id,
                a.filename,
                a.file_size,
                a.mime_type,
                a.tipo_anexo,
                a.es_principal,
                a.folios,
            )
            created.append(_row_to_anexo(row))
        return created

    async def get_by_id(
        self, document_id: uuid.UUID, max_clearance: int = 3
    ) -> Radicado | None:
        # max_clearance=3 (CLASIFICADA) = sin restricción para llamantes internos/sistema;
        # los GET de usuario pasan el clearance real → un doc por encima devuelve None
        # (el router lo traduce a 404, sin revelar su existencia). RF-SEG-08 / RF-BUS-04.
        row = await self._conn.fetchrow(
            """
            SELECT
                id, tracking_number, doc_type, year, sequence,
                subject, doc_class, origin_dept, dest_dept,
                origin_dept_code, dest_dept_code,
                sender_name, sender_entity, status, pages, nivel_seguridad, observations,
                registered_by, registered_at, updated_at, tenant_code, due_date,
                metadata, metadata_template_id
            FROM radicados
            WHERE id = $1 AND nivel_seguridad <= $2
            """,
            document_id,
            max_clearance,
        )
        if row is None:
            return None
        radicado = _row_to_radicado(row)
        radicado.anexos = await self._get_anexos(radicado.id)
        return radicado

    async def get_anexo_for_download(
        self, document_id: uuid.UUID, file_id: uuid.UUID, max_clearance: int
    ) -> tuple[Anexo, str, int] | None:
        """Resuelve, en UNA sola consulta, los tres requisitos de la descarga
        mediada de anexos (D-02, RF-SEG-08): el radicado existe, es visible
        al `max_clearance` del llamante (no-read-up) y `file_id` es
        efectivamente un anexo de ESE radicado (join, no dos lookups
        separados) — así los tres caminos de denegación convergen en el
        MISMO `None` → el router responde el MISMO 404 neutro para los tres,
        sin oráculo de "existe pero no es tuyo" vs "no existe".
        """
        row = await self._conn.fetchrow(
            """
            SELECT a.id, a.radicado_id, a.file_id, a.filename, a.file_size,
                   a.mime_type, a.created_at, a.tipo_anexo, a.es_principal, a.folios,
                   r.tracking_number, r.nivel_seguridad
            FROM anexos a
            JOIN radicados r ON r.id = a.radicado_id
            WHERE r.id = $1 AND a.file_id = $2 AND r.nivel_seguridad <= $3
            """,
            document_id,
            file_id,
            max_clearance,
        )
        if row is None:
            return None
        # tracking_number + nivel_seguridad se devuelven para el asiento de
        # descarga (RF-BUS-10): auditar el acceso al CONTENIDO, no solo al metadato.
        return _row_to_anexo(row), row["tracking_number"], row["nivel_seguridad"]

    async def set_responde_a(self, respuesta_id: uuid.UUID, antecedente_id: uuid.UUID) -> None:
        """Enlaza una Salida con su antecedente de Entrada (respuesta rápida)."""
        await self._conn.execute(
            "UPDATE radicados SET responde_a = $2 WHERE id = $1",
            respuesta_id,
            antecedente_id,
        )

    async def list_respuestas(
        self, antecedente_id: uuid.UUID, max_clearance: int = 3
    ) -> list[asyncpg.Record]:
        """Salidas que responden al radicado dado (consulta ligera, sin mapper).

        No-read-up (RF-SEG-08): filtra `nivel_seguridad <= max_clearance` igual
        que todo el resto de lecturas de radicados (invariante del CLAUDE.md del
        servicio). Sin el filtro, cualquier llamante enumeraría las Salidas
        vinculadas (tracking/asunto/estado/fecha) sin importar su clasificación.
        """
        return list(
            await self._conn.fetch(
                "SELECT id, tracking_number, doc_type, subject, status, registered_at "
                "FROM radicados WHERE responde_a = $1 AND nivel_seguridad <= $2 "
                "ORDER BY registered_at",
                antecedente_id,
                max_clearance,
            )
        )

    async def get_by_tracking_number(
        self, tracking_number: str, max_clearance: int = 3
    ) -> Radicado | None:
        row = await self._conn.fetchrow(
            """
            SELECT
                id, tracking_number, doc_type, year, sequence,
                subject, doc_class, origin_dept, dest_dept,
                origin_dept_code, dest_dept_code,
                sender_name, sender_entity, status, pages, nivel_seguridad, observations,
                registered_by, registered_at, updated_at, tenant_code, due_date,
                metadata, metadata_template_id
            FROM radicados
            WHERE tracking_number = $1 AND nivel_seguridad <= $2
            """,
            tracking_number,
            max_clearance,
        )
        if row is None:
            return None
        radicado = _row_to_radicado(row)
        radicado.anexos = await self._get_anexos(radicado.id)
        return radicado

    async def find_all(
        self,
        *,
        doc_type: str | None,
        status: str | None,
        metadata_filter: dict | None,
        page: int,
        size: int,
        max_clearance: int,
    ) -> tuple[list[Radicado], int]:
        conditions: list[str] = []
        params: list[object] = []
        idx = 1

        # Control de acceso por clasificación (RF-SEG-08 / RF-BUS-04): solo radicados
        # cuyo nivel no supere el clearance del llamante (no read-up). Siempre aplica.
        conditions.append(f"nivel_seguridad <= ${idx}")
        params.append(max_clearance)
        idx += 1

        if doc_type is not None:
            conditions.append(f"doc_type = ${idx}")
            params.append(doc_type)
            idx += 1

        if status is not None:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1

        if metadata_filter:
            # Filtro por campo de metadato vía índice GIN (E03, RF-MET-02).
            conditions.append(f"metadata @> ${idx}::jsonb")
            params.append(json.dumps(metadata_filter))
            idx += 1

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        count_params = list(params)
        total: int = await self._conn.fetchval(
            f"SELECT COUNT(*) FROM radicados {where_clause}", *count_params
        )

        offset = (page - 1) * size
        params.append(size)
        params.append(offset)

        rows = await self._conn.fetch(
            f"""
            SELECT
                id, tracking_number, doc_type, year, sequence,
                subject, doc_class, origin_dept, dest_dept,
                origin_dept_code, dest_dept_code,
                sender_name, sender_entity, status, pages, nivel_seguridad, observations,
                registered_by, registered_at, updated_at, tenant_code, due_date,
                metadata, metadata_template_id
            FROM radicados
            {where_clause}
            ORDER BY registered_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params,
        )

        # Anexos are not loaded for list responses — they are included only in
        # single-document endpoints to keep list queries fast.
        return [_row_to_radicado(r) for r in rows], total

    async def update(
        self,
        document_id: uuid.UUID,
        payload: DocumentUpdate,
    ) -> Radicado | None:
        fields: list[str] = []
        params: list[object] = []
        idx = 1

        if payload.subject is not None:
            fields.append(f"subject = ${idx}")
            params.append(payload.subject)
            idx += 1

        if payload.dest_dept is not None:
            fields.append(f"dest_dept = ${idx}")
            params.append(payload.dest_dept)
            idx += 1

        if payload.observations is not None:
            fields.append(f"observations = ${idx}")
            params.append(payload.observations)
            idx += 1

        if payload.status is not None:
            fields.append(f"status = ${idx}")
            params.append(payload.status)
            idx += 1

        if payload.pages is not None:
            fields.append(f"pages = ${idx}")
            params.append(payload.pages)
            idx += 1

        if not fields:
            # Nothing to update — return current state
            return await self.get_by_id(document_id)

        fields.append(f"updated_at = ${idx}")
        params.append(datetime.now(tz=UTC))
        idx += 1

        params.append(document_id)
        set_clause = ", ".join(fields)

        row = await self._conn.fetchrow(
            f"""
            UPDATE radicados
            SET {set_clause}
            WHERE id = ${idx}
            RETURNING
                id, tracking_number, doc_type, year, sequence,
                subject, doc_class, origin_dept, dest_dept,
                origin_dept_code, dest_dept_code,
                sender_name, sender_entity, status, pages, nivel_seguridad, observations,
                registered_by, registered_at, updated_at, tenant_code, due_date,
                metadata, metadata_template_id
            """,
            *params,
        )
        if row is None:
            return None

        radicado = _row_to_radicado(row)
        radicado.anexos = await self._get_anexos(radicado.id)
        return radicado

    async def get_disposition(self, document_id: uuid.UUID) -> tuple[bool, dict]:
        """Devuelve (existe, disposition). disposition es {} si no hay nada fijado."""
        row = await self._conn.fetchrow(
            "SELECT disposition FROM radicados WHERE id = $1", document_id
        )
        if row is None:
            return False, {}
        return True, _json_obj(row["disposition"])

    async def set_disposition(self, document_id: uuid.UUID, disposition: dict) -> dict | None:
        row = await self._conn.fetchrow(
            "UPDATE radicados SET disposition = $2::jsonb WHERE id = $1 " "RETURNING disposition",
            document_id,
            json.dumps(disposition, default=str),
        )
        return _json_obj(row["disposition"]) if row is not None else None

    async def get_security_context(self, document_id: uuid.UUID) -> asyncpg.Record | None:
        """Returns id, tracking_number and nivel_seguridad for a radicado by UUID.

        Does NOT apply a clearance filter — clearance enforcement is the caller's
        responsibility (the service layer checks no-read-up before acting).
        """
        return await self._conn.fetchrow(
            "SELECT id, tracking_number, nivel_seguridad FROM radicados WHERE id = $1",
            document_id,
        )

    async def set_nivel_seguridad(self, document_id: uuid.UUID, nivel: int) -> None:
        """Updates nivel_seguridad and bumps updated_at for the given radicado."""
        await self._conn.execute(
            "UPDATE radicados SET nivel_seguridad = $2, updated_at = now() WHERE id = $1",
            document_id,
            nivel,
        )

    async def list_anexos_for_document(
        self, document_id: uuid.UUID, max_clearance: int
    ) -> list[Anexo] | None:
        """Lista los anexos de un radicado respetando no-read-up (RF-SEG-08).

        `None` si el radicado no existe o está por encima del clearance del
        llamante (el router lo traduce a 404 neutro, sin revelar existencia).
        Un radicado VISIBLE sin anexos devuelve `[]` (200) — el LEFT JOIN
        distingue "visible-sin-anexos" (una fila con `a.id` NULL) de
        "no-visible" (cero filas), sin un segundo lookup ni oráculo.
        """
        rows = await self._conn.fetch(
            """
            SELECT a.id, a.radicado_id, a.file_id, a.filename, a.file_size,
                   a.mime_type, a.created_at, a.tipo_anexo, a.es_principal, a.folios
            FROM radicados r
            LEFT JOIN anexos a ON a.radicado_id = r.id
            WHERE r.id = $1 AND r.nivel_seguridad <= $2
            ORDER BY a.created_at ASC
            """,
            document_id,
            max_clearance,
        )
        if not rows:
            return None  # radicado inexistente o sobre-clearance → 404 neutro
        # `a.id` NULL = radicado visible sin anexos → lista vacía.
        return [_row_to_anexo(r) for r in rows if r["id"] is not None]

    async def _get_anexos(self, radicado_id: uuid.UUID) -> list[Anexo]:
        rows = await self._conn.fetch(
            """
            SELECT id, radicado_id, file_id, filename, file_size, mime_type,
                   created_at, tipo_anexo, es_principal, folios
            FROM anexos
            WHERE radicado_id = $1
            ORDER BY created_at ASC
            """,
            radicado_id,
        )
        return [_row_to_anexo(r) for r in rows]


# ---------------------------------------------------------------------------
# Row → dataclass helpers
# ---------------------------------------------------------------------------


def _row_to_radicado(row: asyncpg.Record) -> Radicado:
    return Radicado(
        id=row["id"],
        tracking_number=row["tracking_number"],
        doc_type=row["doc_type"],
        year=row["year"],
        sequence=row["sequence"],
        subject=row["subject"],
        doc_class=row["doc_class"],
        origin_dept=row["origin_dept"],
        dest_dept=row["dest_dept"],
        origin_dept_code=row["origin_dept_code"],
        dest_dept_code=row["dest_dept_code"],
        sender_name=row["sender_name"],
        sender_entity=row["sender_entity"],
        status=row["status"],
        pages=row["pages"],
        nivel_seguridad=row["nivel_seguridad"],
        observations=row["observations"],
        registered_by=row["registered_by"],
        registered_at=row["registered_at"],
        updated_at=row["updated_at"],
        tenant_code=row["tenant_code"],
        due_date=row["due_date"],
        metadata=_json_obj(row["metadata"]),
        metadata_template_id=row["metadata_template_id"],
    )


def _json_obj(raw: object) -> dict:
    # asyncpg devuelve JSONB como str; toleramos dict por si hay codec configurado.
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def _row_to_anexo(row: asyncpg.Record) -> Anexo:
    return Anexo(
        id=row["id"],
        radicado_id=row["radicado_id"],
        file_id=row["file_id"],
        filename=row["filename"],
        file_size=row["file_size"],
        mime_type=row["mime_type"],
        created_at=row["created_at"],
        tipo_anexo=row["tipo_anexo"],
        es_principal=row["es_principal"],
        folios=row["folios"],
    )
