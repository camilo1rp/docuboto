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
es un ZIP de USUARIO: se arma con los bytes reales de los anexos descargados de
MinIO + los archivos que archive genera (índice XML, manifiesto CSV) y se
devuelve para streaming — NO se persiste nada. El control de acceso (no-read-up
a nivel de expediente) lo impone archive-service ANTES de llamar aquí; este
servicio solo materializa el inventario que recibe (acto de sistema, D-02).
"""

from __future__ import annotations

import base64
import hashlib

import asyncpg

from app.core.minio_client import get_minio
from app.repositories.file_repository import FileRepository
from app.schemas.preservacion import ExportZipCreate
from app.services.aip_builder import build_zip
from app.services.aip_service import _download
from app.services.preservation_worm_service import FileNotFoundForPreservationError

#: Tope del paquete de exportación, en bytes.
#:
#: El ZIP se arma ÍNTEGRO en memoria, así que el límite tiene que ser de bytes.
#: Contar radicados —como hace el otro exportador— no acota nada: lo que llena la
#: memoria es el peso de los anexos, no cuántos son.
MAX_EXPORT_BYTES = 512 * 1024 * 1024


class ExportDemasiadoGrandeError(Exception):
    """El paquete supera el tope de bytes; no se arma a medias."""

    def __init__(self, acumulado: int) -> None:
        self.acumulado = acumulado
        super().__init__(
            f"el paquete alcanzó {acumulado} bytes y supera el tope {MAX_EXPORT_BYTES}"
        )


class ExportService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._file_repo = FileRepository(conn, tenant_slug)

    async def build_export_zip(self, payload: ExportZipCreate) -> bytes:
        """Descarga los bytes de cada `file_id` de MinIO + incrusta los archivos
        extra, y arma el ZIP. Un `file_id` inexistente → error (el expediente
        referenciaría un anexo que no está en storage — inconsistencia real)."""
        files: dict[str, bytes] = {}
        acumulado = 0

        def _acumular(nombre: str, datos: bytes) -> None:
            """Acumula un fichero al paquete, con tope por BYTES.

            El tope del otro exportador cuenta RADICADOS, que no es lo que llena
            la memoria: cinco mil radicados de una línea caben, y doscientos con
            un plano escaneado cada uno no. Aquí se arma el ZIP entero en memoria,
            así que lo que hay que acotar son los bytes.

            Se corta con un error explícito en vez de recortar el paquete: un
            expediente exportado a medias, con aspecto de completo, es peor que
            no exportarlo — quien lo recibe no tiene forma de saber qué falta.
            """
            nonlocal acumulado
            acumulado += len(datos)
            if acumulado > MAX_EXPORT_BYTES:
                raise ExportDemasiadoGrandeError(acumulado)
            files[nombre] = datos
        # Archivos generados por archive (índice XML, manifiesto CSV).
        for extra in payload.extra_files:
            _acumular(extra.arcname, base64.b64decode(extra.contenido_b64))
        if payload.items:
            minio = get_minio()
            for item in payload.items:
                record = await self._file_repo.get_by_id_any_state(item.file_id)
                if record is None:
                    raise FileNotFoundForPreservationError(str(item.file_id))
                _acumular(
                    item.arcname, await _download(minio, record.bucket, record.object_key)
                )
        # checksums.txt (SHA-256 por miembro) → el receptor puede validar la
        # integridad de cada archivo entregado (constitución §5.8, Ley 594 art. 19).
        # Formato `sha256sum`: "<hex>  <arcname>". Cubre TODOS los miembros salvo
        # sí mismo; se ordena para ser determinista.
        checksums = "".join(
            f"{hashlib.sha256(data).hexdigest()}  {name}\n"
            for name, data in sorted(files.items())
        )
        files["checksums.txt"] = checksums.encode("utf-8")
        return build_zip(files)
