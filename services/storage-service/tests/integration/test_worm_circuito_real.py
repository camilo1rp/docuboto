# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""El circuito WORM recorrido POR LA APLICACIÓN contra Postgres real Y un almacén S3 real
(AREA-08-15).

Lo que había eran dos mitades que nunca se tocaban: `test_worm_object_lock_real_s3.py`
prueba el almacén (con un cliente suelto, sin código de la app) y
`test_worm_gate_real_sql.py` prueba el SQL (con el almacén mockeado). Ninguna recorría
subir -> proteger -> releer la retención -> renovar -> intentar purgar sobre las dos
cosas reales a la vez, que es donde vive la afirmación del acta («este objeto no se
puede borrar»).

Doble gate: `RUN_INTEGRATION=1` y `RUN_S3_WORM=1` (como la batería S3). La retención se
fija en MINUTOS (`preservation_min_retention_days=0`), no en años: COMPLIANCE no se puede
deshacer, y los objetos que deja este test en el almacén de pruebas caducan solos.

    docker compose run --rm --no-deps \
      -e RUN_INTEGRATION=1 -e RUN_S3_WORM=1 -e MINIO_ENDPOINT=minio:9000 \
      storage-service pytest tests/integration/test_worm_circuito_real.py -q

Aviso sobre la mutación «saltar `verificar_retencion_aplicada`»: este fichero NO la
detecta. `test_bucket_sin_object_lock_aborta_y_no_asienta` lo ataja antes
`ensure_object_lock_bucket` (bucket sin Object Lock), no el read-back, y un almacén real
que cumple no puede reproducir «acepta la orden y no la aplica». Esa mutación la fijan
`tests/test_la_retencion_se_relee_cableado.py` (proteger, renovar y AIP: el doble acepta
y al releer no hay retención) -- ejecutada y verificada en la revisión de la tanda T12.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import asyncpg
import pytest
from miniopy_async import Minio, S3Error  # type: ignore[attr-defined]

import app.core.config as config_module
import app.services.preservation_worm_service as worm_module
import app.services.storage_service as storage_module
from app.core.minio_client import ObjectLockNotEnabledError
from app.schemas.preservacion import ProtegerIndiceRequest, RenovarRetencionRequest
from app.services.preservation_worm_service import PreservationWormService, preservation_bucket_name
from app.services.storage_service import StorageService, WormLockActiveError

from .test_worm_object_lock_real_s3 import _endpoint

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_XML = b"<indice>contenido firmado del expediente (itest circuito WORM)</indice>"


@pytest.fixture(autouse=True)
def _gate() -> None:
    if os.environ.get("RUN_S3_WORM") != "1":
        pytest.skip("exige RUN_S3_WORM=1 y un almacén S3 real")


@pytest.fixture
async def almacen():
    c = Minio(
        _endpoint(),
        access_key=os.environ.get("MINIO_ACCESS_KEY", "orpycamcp"),
        secret_key=os.environ.get("MINIO_SECRET_KEY", "orpycamcp_dev"),
        secure=os.environ.get("MINIO_SECURE", "false").lower() == "true",
        region=os.environ.get("MINIO_REGION") or None,
    )
    try:
        with (
            patch.object(worm_module, "get_minio", return_value=c),
            patch.object(storage_module, "get_minio", return_value=c),
        ):
            yield c
    finally:
        await c.close_session()


@pytest.fixture(autouse=True)
def _config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)
    # Retención de minutos: COMPLIANCE es irreversible y esto corre contra un almacén real.
    monkeypatch.setattr(config_module.settings, "preservation_min_retention_days", 0)
    monkeypatch.setattr(config_module.settings, "preservation_retention_mode", "COMPLIANCE")


async def _subir_indice(conn: asyncpg.Connection, slug: str):
    """Sube el XML por el `StorageService.upload` REAL (bucket de documentos incluido)."""
    resp, created, _ = await StorageService(conn, slug).upload(
        filename="indice-firmado.xml",
        mime_type="application/xml",
        content=_XML,
        uploaded_by=uuid.uuid4(),
        formatos_admitidos=frozenset({"application/xml"}),
    )
    assert created
    return resp


def _hasta(segundos: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=segundos)


async def test_circuito_proteger_releer_renovar_y_purga_rechazada(
    tenant_ctx: tuple[str, asyncpg.Connection], almacen: Minio
) -> None:
    slug, conn = tenant_ctx
    expediente = f"EXP-CIRCUITO-{uuid.uuid4().hex[:8]}"
    archivo = await _subir_indice(conn, slug)
    servicio = PreservationWormService(conn, slug)

    # 1. proteger: fila + objeto con la retención RELEÍDA del almacén real
    hasta = _hasta(120)
    resp, created = await servicio.proteger_indice(
        ProtegerIndiceRequest(file_id=archivo.id, expediente_id=expediente, retain_until=hasta),
        actor=None,
    )
    assert created
    fila = await conn.fetchrow(
        "SELECT bucket, object_key, version_id, retain_until, retention_mode "
        "FROM preservacion_worm_objeto WHERE expediente_id = $1 AND tipo = 'indice'",
        expediente,
    )
    assert fila["bucket"] == preservation_bucket_name(slug)
    real = await almacen.get_object_retention(
        fila["bucket"], fila["object_key"], version_id=fila["version_id"]
    )
    assert real is not None, "el almacén no reporta retención sobre lo que la fila dice protegido"
    assert real.mode == fila["retention_mode"] == "COMPLIANCE"
    assert abs((real.retain_until_date - fila["retain_until"]).total_seconds()) < 60

    # 2. el objeto protegido no se puede borrar (la garantía que el acta afirma)
    with pytest.raises(S3Error):
        await almacen.remove_object(
            fila["bucket"], fila["object_key"], version_id=fila["version_id"]
        )

    # 3. renovar: la extensión llega al almacén y a la fila
    nueva = _hasta(240)
    renovada = await servicio.renovar_retencion(
        RenovarRetencionRequest(expediente_id=expediente, tipo="indice", retain_until=nueva),
        actor=None,
    )
    assert renovada.renovado
    real2 = await almacen.get_object_retention(
        fila["bucket"], fila["object_key"], version_id=fila["version_id"]
    )
    assert real2.retain_until_date > real.retain_until_date
    en_bd = await conn.fetchval(
        "SELECT retain_until FROM preservacion_worm_objeto "
        "WHERE expediente_id = $1 AND tipo = 'indice'",
        expediente,
    )
    assert abs((real2.retain_until_date - en_bd).total_seconds()) < 60

    # 4. la disposición se rechaza por el lock y NO toca el almacén ni la fila de files
    with pytest.raises(WormLockActiveError):
        await StorageService(conn, slug).purge_for_disposition(
            archivo.id, expediente, "itest disposición", actor=None
        )
    assert await conn.fetchval("SELECT count(*) FROM files WHERE id = $1", archivo.id) == 1
    await almacen.stat_object(fila["bucket"], fila["object_key"])
    asiento = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE action = 'file.purga_bloqueada_worm' "
        "AND object_ref = $1",
        str(archivo.id),
    )
    assert asiento == 1


async def test_bucket_sin_object_lock_aborta_y_no_asienta(
    tenant_ctx: tuple[str, asyncpg.Connection], almacen: Minio
) -> None:
    """Control negativo del circuito: si el bucket WORM del tenant existe SIN Object Lock,
    proteger ABORTA y no deja fila. Sin el guard (o con el read-back saltado) la fila
    diría COMPLIANCE sobre un objeto que se puede borrar -- el acta firmaría esa mentira."""
    slug, conn = tenant_ctx
    expediente = f"EXP-SINLOCK-{uuid.uuid4().hex[:8]}"
    archivo = await _subir_indice(conn, slug)
    await almacen.make_bucket(preservation_bucket_name(slug))  # SIN object_lock

    with pytest.raises(ObjectLockNotEnabledError):
        await PreservationWormService(conn, slug).proteger_indice(
            ProtegerIndiceRequest(
                file_id=archivo.id, expediente_id=expediente, retain_until=_hasta(120)
            ),
            actor=None,
        )
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM preservacion_worm_objeto WHERE expediente_id = $1", expediente
        )
        == 0
    )
