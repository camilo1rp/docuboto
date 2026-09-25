# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El almacén dijo que sí; aquí se comprueba que además lo hizo.

Hasta este cambio el sistema **ordenaba** la retención y no volvía a mirarla
nunca: `set_object_retention` aparecía en tres ficheros y `get_object_retention`
en **ninguno**. Después escribía `retention_mode='COMPLIANCE'` en
`preservacion_worm_objeto` y un evento PREMIS `FIJACION` con `resultado='ok'`,
es decir, **atestiguaba** una protección que jamás había comprobado.

Que eso no es teórico lo demostró SeaweedFS: aceptaba `PutObjectRetention` con
`Mode=COMPLIANCE` devolviendo 200 y el `DeleteObject` seguía funcionando. Con un
almacén así, el acta de transferencia —con su huella y su firma XAdES— afirmaría
una garantía inexistente: un documento criptográficamente impecable y falso en su
contenido, que es peor que no tener WORM.

Estas pruebas son de la LÓGICA: comprueban que el servicio aborta cuando el
almacén no confirma. Que un almacén concreto cumpla de verdad no lo puede probar
un doble —lo prueba `tests/integration/test_worm_object_lock_real_s3.py` contra
un S3 real, donde MinIO y RustFS dan 7/7 y Garage 0/7.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from miniopy_async import S3Error  # type: ignore[attr-defined]

from app.core.minio_client import (
    RetencionNoVerificadaError,
    verificar_retencion_aplicada,
)

HASTA = datetime(2031, 1, 1, tzinfo=UTC)


def _minio_que_devuelve(retencion) -> MagicMock:
    minio = MagicMock()
    minio.get_object_retention = AsyncMock(return_value=retencion)
    return minio


def _retencion(mode: str, hasta: datetime) -> MagicMock:
    r = MagicMock()
    r.mode = mode
    r.retain_until_date = hasta
    return r


async def _verificar(minio, *, modo="COMPLIANCE", hasta=HASTA):
    await verificar_retencion_aplicada(
        minio,
        "bucket-worm",
        "aip/expediente/indice.xml",
        version_id="v1",
        modo_esperado=modo,
        hasta_esperado=hasta,
    )


@pytest.mark.asyncio
async def test_el_almacen_que_confirma_lo_pedido_pasa():
    """Control positivo: sin esto, un verificador que siempre falla «aprobaría»
    todas las pruebas de abajo sin distinguir nada."""
    await _verificar(_minio_que_devuelve(_retencion("COMPLIANCE", HASTA)))


@pytest.mark.asyncio
async def test_aborta_si_el_almacen_dice_que_no_hay_retencion():
    """El caso SeaweedFS: aceptó la orden y al releer no hay nada."""
    with pytest.raises(RetencionNoVerificadaError):
        await _verificar(_minio_que_devuelve(None))


@pytest.mark.asyncio
async def test_aborta_si_el_almacen_aplico_otro_modo():
    """GOVERNANCE en vez de COMPLIANCE no es un matiz: en GOVERNANCE, quien tenga
    la credencial del almacén —que aquí es la del propio servicio que escribe—
    puede levantar la retención. Ese borrado no pasaría por el gate de
    disposición ni dejaría asiento, así que no cae en ninguna de las dos
    excepciones que el Modelo de Requisitos del AGN admite."""
    with pytest.raises(RetencionNoVerificadaError):
        await _verificar(_minio_que_devuelve(_retencion("GOVERNANCE", HASTA)))


@pytest.mark.asyncio
async def test_aborta_si_la_retencion_aplicada_es_mas_corta():
    """Una retención más corta que la pedida es un plazo TRD recortado en
    silencio, y la entidad lo fijó una sola vez."""
    corta = _retencion("COMPLIANCE", HASTA - timedelta(days=365))
    with pytest.raises(RetencionNoVerificadaError):
        await _verificar(_minio_que_devuelve(corta))


@pytest.mark.asyncio
async def test_tolera_un_redondeo_del_instante_pero_no_un_recorte():
    """La tolerancia existe porque un almacén puede redondear al guardar.

    Es deliberadamente asimétrica: se admiten segundos de diferencia, no días, y
    el test de arriba fija el límite por el otro lado.
    """
    casi = _retencion("COMPLIANCE", HASTA - timedelta(seconds=10))
    await _verificar(_minio_que_devuelve(casi))


@pytest.mark.asyncio
async def test_aborta_si_el_almacen_ni_siquiera_puede_responder():
    """Un error al releer tampoco es «probablemente esté bien»."""
    minio = MagicMock()
    minio.get_object_retention = AsyncMock(
        side_effect=S3Error(
            "NoSuchObjectLockConfiguration",
            "Object Lock configuration does not exist",
            "resource",
            "req-id",
            "host-id",
            MagicMock(),
        )
    )
    with pytest.raises(RetencionNoVerificadaError):
        await _verificar(minio)
