# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import logging
from datetime import datetime, timedelta

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


class RetencionNoVerificadaError(Exception):
    """El almacén aceptó la orden de retención y no la confirma al releerla.

    Es el modo de fallo más grave de un almacén de objetos, y no es teórico:
    SeaweedFS aceptaba ``PutObjectRetention`` con ``Mode=COMPLIANCE`` devolviendo
    200 y permitía borrar el objeto igualmente. Con un almacén así, el sistema
    escribiría ``retention_mode='COMPLIANCE'`` en ``preservacion_worm_objeto`` y
    un evento PREMIS ``FIJACION`` en ``ok`` sobre una protección **inexistente**:
    el acta de transferencia, con su huella y su firma XAdES, afirmaría una
    garantía que el almacén no aplica. Un documento criptográficamente impecable
    y falso en su contenido es peor que no tener WORM.

    Por eso esto aborta en vez de continuar: el Ac. AGN 001/2024 art. 4.3.2.4 P1
    exige que el expediente no pueda ser «modificado, eliminado ni reemplazado»,
    y de esos tres verbos la firma solo cubre dos — «eliminado» lo cierra el
    almacén o no lo cierra nadie.
    """


async def verificar_retencion_aplicada(
    minio: Minio,
    bucket: str,
    object_key: str,
    *,
    version_id: str | None,
    modo_esperado: str,
    hasta_esperado: datetime,
    tolerancia: timedelta = timedelta(minutes=1),
) -> None:
    """Relee la retención recién escrita y aborta si no coincide.

    Hasta esta comprobación el sistema **ordenaba** la retención y no volvía a
    mirarla nunca: ``set_object_retention`` aparecía en tres ficheros y
    ``get_object_retention`` en ninguno. Era tolerable mientras el motor fuera uno
    solo y conocido; dejó de serlo al cambiar de almacén, porque los candidatos
    fallan precisamente aquí.

    Es el mismo patrón fail-closed que ya se aplica a la fixity antes de fijar:
    ante la duda, no se fija y no se asienta.

    La ``tolerancia`` existe porque un almacén puede redondear el instante al
    guardarlo; lo que no se tolera es otro **modo** ni una fecha **anterior** a la
    pedida, que sería una retención más corta que la que la entidad fijó.
    """
    try:
        aplicada = await minio.get_object_retention(bucket, object_key, version_id=version_id)
    except S3Error as exc:
        raise RetencionNoVerificadaError(
            f"El almacén no devuelve la retención de {bucket}/{object_key} que acaba "
            f"de aceptar ({exc.code}): no se puede afirmar que el objeto esté protegido."
        ) from exc
    except ValueError as exc:
        # RustFS contesta 200 SIN <Mode> cuando no hay retención y el cliente lo levanta como
        # ValueError: para este guardián es lo mismo que «no devuelve la retención».
        raise RetencionNoVerificadaError(
            f"El almacén contestó sin modo de retención para {bucket}/{object_key} que acaba "
            "de aceptar: no se puede afirmar que el objeto esté protegido."
        ) from exc

    if aplicada is None:
        raise RetencionNoVerificadaError(
            f"El almacén aceptó la retención de {bucket}/{object_key} y la reporta "
            "como inexistente al releerla."
        )

    modo_real = getattr(aplicada, "mode", None)
    if modo_real != modo_esperado:
        raise RetencionNoVerificadaError(
            f"El almacén aplicó el modo {modo_real!r} en vez del pedido "
            f"{modo_esperado!r} sobre {bucket}/{object_key}."
        )

    hasta_real = getattr(aplicada, "retain_until_date", None)
    if hasta_real is None or hasta_real < hasta_esperado - tolerancia:
        raise RetencionNoVerificadaError(
            f"El almacén aplicó una retención más corta de la pedida sobre "
            f"{bucket}/{object_key}: hasta {hasta_real}, se pidió {hasta_esperado}."
        )


async def verificar_legal_hold_aplicado(
    minio: Minio,
    bucket: str,
    object_key: str,
    *,
    version_id: str | None,
) -> None:
    """Relee que el legal hold recién escrito esté PUESTO (C-5/C7).

    Un objeto de retención diferida (``valoracion_pendiente``) NO tiene fecha que releer: lo único
    que lo protege es el hold, así que darlo por puesto sobre la palabra del almacén sería atestiguar
    una protección que nadie miró. Mismo patrón fail-closed que :func:`verificar_retencion_aplicada`."""
    try:
        puesto = await minio.is_object_legal_hold_enabled(bucket, object_key, version_id=version_id)
    except S3Error as exc:
        raise RetencionNoVerificadaError(
            f"El almacén no devuelve el legal hold de {bucket}/{object_key} que acaba de "
            f"aceptar ({exc.code}): no se puede afirmar que el objeto esté protegido."
        ) from exc
    if puesto is not True:
        raise RetencionNoVerificadaError(
            f"El almacén aceptó el legal hold de {bucket}/{object_key} y lo reporta como "
            "ausente al releerlo."
        )


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
