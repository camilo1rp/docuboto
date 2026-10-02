# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
File repository — all direct asyncpg calls live here.

Tenant isolation: every public method calls _set_search_path() first to
switch the PostgreSQL search_path to the tenant's schema. The slug is
validated by regex before interpolation.

Versionado (E07): cada fila es una versión inmutable; `root_id` agrupa la
cadena, `is_current` marca la vigente, `replaces_id` enlaza a la anterior.

Soft-delete (D-04, constitución inv. 12): las operaciones normales de usuario
marcan `deleted_at` con la hora actual (y `deleted_by` con el actor). Todos
los listados/descargas normales filtran `AND deleted_at IS NULL`. El borrado
físico de la fila solo ocurre mediante `purge()`, que se llama exclusivamente
desde el proceso de disposición final del TRD.
"""

import uuid

import asyncpg

from app.core.database import TENANT_SLUG_RE
from app.models.file_record import FileRecord

_COLS = (
    "id, filename, mime_type, file_size, sha256, bucket, object_key, "
    "uploaded_by, uploaded_at, version, is_current, root_id, replaces_id, "
    "deleted_at, deleted_by, pdfa_status, pdfa_profile, pdfa_checked_at"
)


# Quién puede REFERENCIAR un `files.id` desde fuera de esta tabla (no hay FK
# entre servicios). Constantes, nunca entrada de usuario: se interpolan en el SQL.
# Cada par es (tabla, columna); una tabla ausente en el tenant (a medio provisionar)
# se trata como DESCONOCIDO y no como «nadie»: `referencias()` falla cerrado. Es la lista que
# decide si un objeto puede borrarse sin dejar un puntero colgando: toda columna
# nueva que guarde un `file_id` debe añadirse aquí (el test de integración lo fija).
REFERENCIAS_DE_ARCHIVO: tuple[tuple[str, str], ...] = (
    ("anexos", "file_id"),  # document-service: anexo de cualquier radicado
    ("email_mensaje", "crudo_file_id"),  # document-service: correo original
    ("borrador", "documento_file_id"),  # document-service: PDF/A del borrador
    ("preservacion_worm_objeto", "file_id"),  # protegido bajo WORM (Object-Lock)
    ("preservacion_migracion", "file_id_origen"),
    ("preservacion_migracion", "file_id_destino"),
)


class InvalidTenantSlugError(Exception):
    pass


class FileRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        # Misma regex/normalización que `app.core.database.tenant_schema()`
        # (fuente única canónica) y `PreservacionRepository`: acepta guiones
        # (forma canónica y habitual de un `tenant_slug`) y los normaliza a
        # guion bajo en el nombre de schema. NO acepta `_` crudo en el slug
        # de entrada — el guion bajo solo aparece tras la normalización.
        if not tenant_slug or not TENANT_SLUG_RE.match(tenant_slug):
            raise InvalidTenantSlugError(f"Invalid tenant slug: {tenant_slug!r}")
        self._conn = conn
        self._slug = tenant_slug
        self._schema = f"tenant_{tenant_slug.replace('-', '_')}"

    async def _set_search_path(self) -> None:
        await self._conn.execute(f'SET search_path = "{self._schema}", public')

    async def find_by_sha256(self, sha256: str) -> FileRecord | None:
        # Deduplicación de subidas nuevas: solo contra versiones vigentes y activas.
        await self._set_search_path()
        row = await self._conn.fetchrow(
            f"SELECT {_COLS} FROM files "
            "WHERE sha256 = $1 AND is_current AND deleted_at IS NULL LIMIT 1",
            sha256,
        )
        return _row_to_file_record(row) if row is not None else None

    async def mark_dedup_hit(self, file_id: uuid.UUID) -> FileRecord | None:
        """Deja constancia de que un `upload` reutilizó este objeto por deduplicación.

        El UPDATE toma el bloqueo de FILA, así que se SERIALIZA con el `FOR UPDATE` de
        la purga de huérfanos (`lock_any_state`): o la marca llega primero (la purga la
        ve y conserva el objeto) o la purga llega primero (borra la fila y este UPDATE
        no encuentra nada: `None`, y quien llama sube el contenido de nuevo en vez de
        devolver un `file_id` que ya no existe). Sin esto la purga solo veía
        referencias commiteadas y podía borrar lo que otra operación acababa de
        recibir por dedup y aún no commiteaba."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "UPDATE files SET dedup_hit_at = now() "
            f"WHERE id = $1 AND is_current AND deleted_at IS NULL RETURNING {_COLS}",
            file_id,
        )
        return _row_to_file_record(row) if row is not None else None

    async def fue_reutilizado_por_dedup(self, file_id: uuid.UUID) -> bool:
        """`True` si algún `upload` devolvió ya este objeto por deduplicación. Llamar
        con la fila bloqueada (`lock_any_state`) para que la respuesta no caduque."""
        await self._set_search_path()
        return bool(
            await self._conn.fetchval(
                "SELECT dedup_hit_at IS NOT NULL FROM files WHERE id = $1", file_id
            )
        )

    async def create(
        self,
        file_id: uuid.UUID,
        filename: str,
        mime_type: str | None,
        file_size: int,
        sha256: str,
        bucket: str,
        object_key: str,
        uploaded_by: uuid.UUID | None,
    ) -> FileRecord:
        await self._set_search_path()
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO files (id, filename, mime_type, file_size, sha256,
                               bucket, object_key, uploaded_by,
                               version, is_current, root_id, replaces_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 1, true, $1, NULL)
            RETURNING {_COLS}
            """,
            file_id,
            filename,
            mime_type,
            file_size,
            sha256,
            bucket,
            object_key,
            uploaded_by,
        )
        return _row_to_file_record(row)

    async def create_version(
        self,
        *,
        previous: FileRecord,
        file_id: uuid.UUID,
        filename: str,
        mime_type: str | None,
        file_size: int,
        sha256: str,
        bucket: str,
        object_key: str,
        uploaded_by: uuid.UUID | None,
    ) -> FileRecord:
        """Crea una nueva versión sobre la cadena de `previous` (atómico)."""
        await self._set_search_path()
        async with self._conn.transaction():
            await self._conn.execute(
                "UPDATE files SET is_current = false WHERE id = $1", previous.id
            )
            row = await self._conn.fetchrow(
                f"""
                INSERT INTO files (id, filename, mime_type, file_size, sha256,
                                   bucket, object_key, uploaded_by,
                                   version, is_current, root_id, replaces_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, true, $10, $11)
                RETURNING {_COLS}
                """,
                file_id,
                filename,
                mime_type,
                file_size,
                sha256,
                bucket,
                object_key,
                uploaded_by,
                previous.version + 1,
                previous.root_id or previous.id,
                previous.id,
            )
        return _row_to_file_record(row)

    async def get_by_id(self, file_id: uuid.UUID) -> FileRecord | None:
        """Returns the record only if it is NOT soft-deleted."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            f"SELECT {_COLS} FROM files WHERE id = $1 AND deleted_at IS NULL",
            file_id,
        )
        return _row_to_file_record(row) if row is not None else None

    async def hashes_for_ids(self, file_ids: list[uuid.UUID]) -> dict[str, str]:
        """SHA-256 de un LOTE de ficheros, por id — la huella que el índice
        electrónico del expediente necesita por documento (E15 T-08, RT-15
        art. 4.3.2.3).

        Excluye los borrados (`deleted_at`), igual que `get_by_id`: la huella
        de un fichero retirado no describe lo que hoy custodia el expediente.
        Un id ausente del resultado significa exactamente eso —no hay huella
        que dar—, y el llamante lo declara en vez de inventarla.
        """
        if not file_ids:
            return {}
        await self._set_search_path()
        rows = await self._conn.fetch(
            "SELECT id, sha256 FROM files WHERE id = ANY($1::uuid[]) AND deleted_at IS NULL",
            file_ids,
        )
        return {str(r["id"]): r["sha256"] for r in rows if r["sha256"]}

    async def get_by_id_any_state(self, file_id: uuid.UUID) -> FileRecord | None:
        """Returns the record regardless of its deleted_at state.

        Used exclusively by the TRD disposition purge path, which must be able
        to physically remove files that were already soft-deleted.
        """
        await self._set_search_path()
        row = await self._conn.fetchrow(
            f"SELECT {_COLS} FROM files WHERE id = $1",
            file_id,
        )
        return _row_to_file_record(row) if row is not None else None

    async def list_versions(self, root_id: uuid.UUID) -> list[FileRecord]:
        """Lists the version chain excluding soft-deleted entries."""
        await self._set_search_path()
        rows = await self._conn.fetch(
            f"SELECT {_COLS} FROM files "
            "WHERE root_id = $1 AND deleted_at IS NULL ORDER BY version",
            root_id,
        )
        return [_row_to_file_record(r) for r in rows]

    async def soft_delete(
        self, file_id: uuid.UUID, deleted_by: uuid.UUID | None
    ) -> bool:
        """Marks the file as logically deleted.

        Does NOT remove the row or the MinIO object. Returns True if exactly
        one active row was updated (False means file was already soft-deleted
        or never existed).
        """
        await self._set_search_path()
        result = await self._conn.execute(
            "UPDATE files SET deleted_at = now(), deleted_by = $2 "
            "WHERE id = $1 AND deleted_at IS NULL",
            file_id,
            deleted_by,
        )
        # asyncpg returns "UPDATE N" where N is the affected row count.
        return result.endswith("1")

    async def purge(self, file_id: uuid.UUID) -> bool:
        """Physically deletes the row from the database.

        Only call from the TRD final-disposition path, AFTER the MinIO object
        has been removed. Returns True if a row was deleted.
        """
        await self._set_search_path()
        result = await self._conn.execute(
            "DELETE FROM files WHERE id = $1",
            file_id,
        )
        # asyncpg returns "DELETE N"
        return result.endswith("1")

    async def lock_any_state(self, file_id: uuid.UUID) -> FileRecord | None:
        """`get_by_id_any_state` con `FOR UPDATE`: serializa dos purgas del mismo
        objeto y la purga frente a quien actualice la fila. Solo dentro de una tx."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            f"SELECT {_COLS} FROM files WHERE id = $1 FOR UPDATE", file_id
        )
        return _row_to_file_record(row) if row is not None else None

    async def referencias(self, file_id: uuid.UUID) -> list[str]:
        """Fuentes (`tabla.columna`) que referencian `file_id`; vacío = nadie.

        Mira TODO el tenant —los anexos de cualquier radicado, no solo los de una
        importación—, porque storage deduplica por SHA-256 y un mismo objeto puede
        sostener anexos de radicados que no tienen nada que ver entre sí. FAIL-CLOSED
        con una tabla ausente: si `anexos` (o cualquier otra de la lista) no existe en
        el tenant, no se puede saber si algo la referencia, y «no sé» no es «nadie»:
        se devuelve `tabla(ausente)` como fuente y el llamante conserva el objeto.
        Incluye también las versiones encadenadas de la propia `files`
        (`replaces_id`/`root_id`)."""
        await self._set_search_path()
        fuentes: list[str] = []
        for tabla, columna in REFERENCIAS_DE_ARCHIVO:
            if await self._conn.fetchval("SELECT to_regclass($1)", tabla) is None:
                fuentes.append(f"{tabla}(ausente)")
                continue
            if await self._conn.fetchval(
                f"SELECT 1 FROM {tabla} WHERE {columna} = $1 LIMIT 1", file_id
            ):
                fuentes.append(f"{tabla}.{columna}")
        if await self._conn.fetchval(
            "SELECT 1 FROM files WHERE replaces_id = $1 OR (root_id = $1 AND id <> $1) LIMIT 1",
            file_id,
        ):
            fuentes.append("files.version")
        return fuentes

    async def mark_pdfa_pending(self, file_id: uuid.UUID, *, profile: str) -> None:
        """Marca el objeto como encolado para la validación PDF/A asíncrona
        (E10 C2b follow-up, modo advisory) -- se llama SÍNCRONAMENTE dentro
        de `StorageService.upload`, antes de encolar el `BackgroundTask`, para
        que `pdfa_status` distinga "aún no evaluado" (NULL, gate apagado o no
        es PDF) de "evaluación en curso" (pendiente) mientras el worker
        best-effort corre. `pdfa_profile` queda fijado desde YA (el perfil
        con el que se validará), no cuando el worker termine -- así
        `AipService.empaquetar_aip` puede comparar perfiles aunque el worker
        todavía no haya corrido (un `pendiente` nunca es TERMINAL, así que
        de todos modos no se reusa, pero el perfil queda trazado).
        """
        await self._set_search_path()
        await self._conn.execute(
            "UPDATE files SET pdfa_status = 'pendiente', pdfa_profile = $2 WHERE id = $1",
            file_id,
            profile,
        )

    async def update_pdfa_result(
        self, file_id: uuid.UUID, *, status: str, profile: str
    ) -> None:
        """Fija el veredicto TERMINAL de la validación PDF/A (E10 C2b
        follow-up) -- llamado tanto por el camino síncrono estricto
        (`StorageService.upload`) como por el worker asíncrono best-effort
        (`app/services/pdfa_ingest_service.py`). `pdfa_checked_at = now()`
        marca el instante en que el estado se volvió terminal.
        """
        await self._set_search_path()
        await self._conn.execute(
            "UPDATE files SET pdfa_status = $2, pdfa_profile = $3, pdfa_checked_at = now() "
            "WHERE id = $1",
            file_id,
            status,
            profile,
        )


def _row_to_file_record(row: asyncpg.Record) -> FileRecord:
    return FileRecord(
        id=row["id"],
        filename=row["filename"],
        mime_type=row["mime_type"],
        file_size=row["file_size"],
        sha256=row["sha256"],
        bucket=row["bucket"],
        object_key=row["object_key"],
        uploaded_by=row["uploaded_by"],
        uploaded_at=row["uploaded_at"],
        version=row["version"],
        is_current=row["is_current"],
        root_id=row["root_id"],
        replaces_id=row["replaces_id"],
        deleted_at=row["deleted_at"],
        deleted_by=row["deleted_by"],
        pdfa_status=row["pdfa_status"],
        pdfa_profile=row["pdfa_profile"],
        pdfa_checked_at=row["pdfa_checked_at"],
    )
