# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from miniopy_async import Minio  # type: ignore[attr-defined]

_minio: Minio | None = None


def get_minio() -> Minio:
    if _minio is None:
        raise RuntimeError("MinIO client not initialised")
    return _minio


def init_minio(endpoint: str, access_key: str, secret_key: str, secure: bool) -> Minio:
    global _minio
    _minio = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
    return _minio
