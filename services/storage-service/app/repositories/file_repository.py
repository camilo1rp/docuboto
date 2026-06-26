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

import re
import uuid

import asyncpg

from app.models.file_record import FileRecord

_SLUG_RE = re.compile(r"^[a-z0-9_]+$")

_COLS = (
    "id, filename, mime_type, file_size, sha256, bucket, object_key, "
    "uploaded_by, uploaded_at, version, is_current, root_id, replaces_id, "
    "deleted_at, deleted_by"
)


class InvalidTenantSlugError(Exception):
    pass


class FileRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        if not _SLUG_RE.match(tenant_slug):
            raise InvalidTenantSlugError(f"Invalid tenant slug: {tenant_slug!r}")
        self._conn = conn
        self._slug = tenant_slug
        self._schema = f"tenant_{tenant_slug}"

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
    )
