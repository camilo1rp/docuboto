# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Ensamblado del ZIP de exportación de expediente (E02/export).

A diferencia del AIP (paquete de preservación BagIt+PREMIS subido a WORM), este
es un ZIP de USUARIO: se arma con los bytes reales de los anexos descargados del
almacén de objetos + los archivos que archive genera (índice XML, manifiesto CSV)
y NO se persiste. El control de acceso (no-read-up por radicado y por expediente)
lo impone archive-service ANTES de llamar aquí; este servicio solo materializa el
inventario que recibe (acto de sistema, D-02).

B4-5: el ZIP se escribe en un FICHERO TEMPORAL en disco, anexo a anexo y por
trozos, y se sirve desde ahí. Nunca se arma entero en memoria. Alternativa
descartada: zip «en vuelo» sin fichero, porque un fallo a mitad de camino
entregaría un 200 truncado con aspecto de completo; con el fichero cerrado,
todo fallo ocurre ANTES de servir el primer byte.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import errno
import hashlib
import os
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass

import asyncpg
from miniopy_async.error import S3Error
from orpycamcp_common import audit

from app.core.config import settings
from app.core.constants import canal_del_acto
from app.core.minio_client import get_minio
from app.models.file_record import FileRecord
from app.repositories.file_repository import FileRepository
from app.schemas.preservacion import ExportZipCreate
from app.services.preservation_worm_service import FileNotFoundForPreservationError

#: Tamaño del trozo con el que se lee del almacén y se escribe al ZIP.
CHUNK_SIZE = 1024 * 1024


class ExportFixityError(Exception):
    """El binario descargado no coincide con el SHA-256 registrado (B4 media).

    Se aborta antes de servir el primer byte: un paquete con `checksums.txt`
    calculado sobre bytes corruptos haría pasar la corrupción por integridad.
    """

    def __init__(self, file_id: uuid.UUID, esperado: str, obtenido: str) -> None:
        self.file_id = file_id
        self.esperado = esperado
        self.obtenido = obtenido
        super().__init__(f"fixity del anexo {file_id}: esperado {esperado}, obtenido {obtenido}")


async def _en_hilo(fn, *args):
    """`to_thread` que, si lo cancelan, ESPERA a que el hilo termine.

    Sin esto, la cancelación deja al hilo escribiendo en el miembro mientras el
    `finally` lo cierra y el temporal se borra (carrera sobre el fichero).
    """
    tarea = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(tarea)
    except asyncio.CancelledError:
        while not tarea.done():
            try:
                await asyncio.wait({tarea})
            except asyncio.CancelledError:
                continue
        raise


class ExportDemasiadoGrandeError(Exception):
    """El paquete supera `export_max_bytes`; no se arma a medias."""

    def __init__(self, acumulado: int, tope: int) -> None:
        self.acumulado = acumulado
        self.tope = tope
        super().__init__(f"el paquete alcanzó {acumulado} bytes y supera el tope {tope}")


class ExportEspacioInsuficienteError(Exception):
    """No hay disco para armar el paquete (más la reserva configurada)."""

    def __init__(self, necesario: int, libre: int) -> None:
        self.necesario = necesario
        self.libre = libre
        super().__init__(f"se necesitan {necesario} bytes y hay {libre} libres")


@dataclass(slots=True)
class ExportArtifact:
    """ZIP ya cerrado en disco; quien lo recibe es dueño de borrarlo."""

    path: str
    size: int


def remove_quietly(path: str) -> None:
    """Borra el temporal; idempotente (el generador y la tarea de fondo lo llaman)."""
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)


class ExportService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._file_repo = FileRepository(conn, tenant_slug)

    async def build_export_zip(self, payload: ExportZipCreate) -> ExportArtifact:
        """Valida el inventario, arma el ZIP en un temporal y devuelve su ruta.

        Un `file_id` inexistente → `FileNotFoundForPreservationError` ANTES de
        crear el fichero. Cualquier fallo posterior borra el temporal.
        """
        tope = settings.export_max_bytes
        extras = [(e.arcname, base64.b64decode(e.contenido_b64)) for e in payload.extra_files]
        extras_bytes = sum(len(d) for _, d in extras)

        # Prevalidación: UNA consulta para todo el inventario. Un anexo ausente
        # (inconsistencia real) o un paquete que ya por sus tamaños declarados
        # no cabe se rechazan sin descargar nada ni tocar el disco.
        ids = list({item.file_id for item in payload.items})
        records = await self._file_repo.get_many_any_state(ids) if ids else {}
        for item in payload.items:
            if item.file_id not in records:
                raise FileNotFoundForPreservationError(str(item.file_id))
        declarado = extras_bytes + sum(records[item.file_id].file_size for item in payload.items)
        if declarado > tope:
            raise ExportDemasiadoGrandeError(declarado, tope)

        tmp_dir = settings.export_tmp_dir or tempfile.gettempdir()
        libre = shutil.disk_usage(tmp_dir).free
        if libre < declarado + settings.export_min_free_bytes:
            raise ExportEspacioInsuficienteError(declarado + settings.export_min_free_bytes, libre)

        fd, path = tempfile.mkstemp(prefix="orpyca-export-", suffix=".zip", dir=tmp_dir)
        os.close(fd)
        try:
            await self._write_zip(path, payload, extras, records, tope)
            return ExportArtifact(path=path, size=os.path.getsize(path))
        except OSError as exc:
            remove_quietly(path)
            # Disco lleno a mitad de escritura: mismo veredicto que la reserva previa.
            if exc.errno in (errno.ENOSPC, errno.EDQUOT):
                raise ExportEspacioInsuficienteError(declarado, 0) from exc
            raise
        except BaseException:
            remove_quietly(path)
            raise

    async def _write_zip(
        self,
        path: str,
        payload: ExportZipCreate,
        extras: list[tuple[str, bytes]],
        records: dict[uuid.UUID, FileRecord],
        tope: int,
    ) -> None:
        """Escribe miembro a miembro (extras ya en memoria; anexos por trozos de
        1 MiB con sha256 incremental) y cierra con `checksums.txt`."""
        total = 0
        digests: dict[str, str] = {}
        zf = zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True)
        try:
            for arcname, datos in extras:
                total += len(datos)
                if total > tope:
                    raise ExportDemasiadoGrandeError(total, tope)
                await _en_hilo(zf.writestr, arcname, datos)
                digests[arcname] = hashlib.sha256(datos).hexdigest()
            if payload.items:
                minio = get_minio()
                for item in payload.items:
                    record = records[item.file_id]
                    try:
                        obj = await minio.get_object(record.bucket, record.object_key)
                    except S3Error as exc:
                        if exc.code in ("NoSuchKey", "NoSuchBucket"):
                            # La fila existe pero el binario no: inconsistencia real,
                            # mismo veredicto que un `file_id` ausente (404, no 500).
                            raise FileNotFoundForPreservationError(str(item.file_id)) from exc
                        raise
                    try:
                        sha = hashlib.sha256()
                        member = zf.open(item.arcname, "w", force_zip64=True)
                        try:
                            async for chunk in obj.content.iter_chunked(CHUNK_SIZE):
                                total += len(chunk)
                                if total > tope:
                                    raise ExportDemasiadoGrandeError(total, tope)
                                sha.update(chunk)
                                await _en_hilo(member.write, chunk)
                        finally:
                            member.close()
                    finally:
                        close = getattr(obj, "close", None)
                        if close is not None:
                            close()
                    obtenido = sha.hexdigest()
                    if obtenido != record.sha256:
                        await self._asentar_fixity(item.file_id, record.sha256, obtenido)
                        raise ExportFixityError(item.file_id, record.sha256, obtenido)
                    digests[item.arcname] = obtenido
            # checksums.txt (SHA-256 por miembro, formato `sha256sum`) → el receptor
            # valida la integridad de cada archivo (Ley 594 art. 19). Cubre todos los
            # miembros salvo sí mismo; ordenado para ser determinista.
            checksums = "".join(f"{d}  {n}\n" for n, d in sorted(digests.items()))
            await _en_hilo(zf.writestr, "checksums.txt", checksums.encode("utf-8"))
        finally:
            zf.close()

    async def _asentar_fixity(self, file_id: uuid.UUID, esperado: str, obtenido: str) -> None:
        """Incidente de fixity en la cadena inmutable (acto de sistema, D-02)."""
        await audit.append(
            self._conn,
            tenant_slug=self._tenant_slug,
            service="storage-service",
            action="file.fixity_fallo",
            object_type="file",
            object_ref=str(file_id),
            actor=None,
            payload={"origen": "export_zip", "hash_esperado": esperado, "hash_obtenido": obtenido},
            canal=canal_del_acto(None),
        )
