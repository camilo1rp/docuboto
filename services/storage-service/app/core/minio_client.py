# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import logging

from miniopy_async import Minio, S3Error  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

_minio: Minio | None = None


class ObjectLockNotEnabledError(Exception):
    """El bucket existe pero NO fue creado con MinIO Object Lock habilitado.

    S3/MinIO no permite activar Object Lock retroactivamente sobre un bucket
    existente: la única forma de tener la garantía WORM es que el bucket se haya
    creado con ``object_lock=True`` desde el principio. Si esto ocurre, seguir
    adelante daría una falsa sensación de inmutabilidad — se aborta en su lugar.
    """


async def ensure_object_lock_bucket(minio: Minio, bucket: str) -> None:
    """Aprovisiona (idempotente) un bucket con MinIO Object Lock (WORM) habilitado.

    Casos:
      (a) el bucket NO existe        → se crea con ``object_lock=True`` (implica
                                        versionado, requisito de S3 Object Lock).
      (b) existe CON Object Lock     → no-op (ya cumple la garantía).
      (c) existe SIN Object Lock     → ``ObjectLockNotEnabledError`` — no se puede
                                        re-crear ni "activar" el lock sobre un
                                        bucket ya existente sin perder sus objetos.

    La retención se fija SIEMPRE por objeto (``Retention`` en cada ``put_object``),
    nunca como regla por defecto del bucket, porque cada expediente tiene su propio
    plazo TRD.
    """
    exists = await minio.bucket_exists(bucket)
    if not exists:
        await minio.make_bucket(bucket, object_lock=True)
        logger.info("Bucket WORM creado con Object Lock: %s", bucket)
        return

    try:
        await minio.get_object_lock_config(bucket)
    except S3Error as exc:
        logger.error(
            "Bucket %s existe SIN Object Lock habilitado — no se puede garantizar "
            "WORM; abortando en vez de continuar con una garantía falsa.",
            bucket,
        )
        raise ObjectLockNotEnabledError(
            f"El bucket {bucket!r} existe pero no tiene Object Lock habilitado. "
            "No es posible activarlo retroactivamente (limitación de S3/MinIO)."
        ) from exc


def get_minio() -> Minio:
    if _minio is None:
        raise RuntimeError("MinIO client not initialised")
    return _minio


def init_minio(endpoint: str, access_key: str, secret_key: str, secure: bool) -> Minio:
    global _minio
    _minio = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
    return _minio
