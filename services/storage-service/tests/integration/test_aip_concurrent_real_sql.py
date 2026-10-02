# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Carrera REAL de doble-empaquetado del AIP (E10-B, remediación de auditoría
Media) contra Postgres genuino: dos conexiones `asyncpg` INDEPENDIENTES
empaquetando el MISMO expediente CONCURRENTEMENTE (`asyncio.gather`, sin
sleeps salvo para el probe explícito del segundo test) — el advisory lock de
sesión (`_worm_lock_key(..., "aip")`, ver `AipService.empaquetar_aip`) debe
serializar de verdad `check->put_object->insert` en el servidor, no solo en
el cliente.

`aip_sha256` (el hash del ZIP completo) se monkeypatchea a un valor FIJO —
NO por pereza, sino porque `aip_builder.py` embebe timestamps reales
(`generado.isoformat()` en el PREMIS, y el mtime por-entrada de cada
`zf.writestr`, que usa `time.localtime()` del proceso, no `generado`): dos
builds reales, aunque estén perfectamente serializados, casi nunca producen
bytes idénticos (ver el comentario corregido de la migración 012). Congelar
`aip_sha256` aísla exactamente lo que este test quiere probar — la
serialización + el corte-circuito idempotente por-contenido — de esa
variabilidad de timestamps, que es una propiedad DISTINTA (y ya conocida/
aceptada) del formato del artefacto.

MinIO sigue mockeado (mismo criterio que el resto de `tests/integration/` de
este servicio): lo que se prueba aquí es la serialización real a nivel
Postgres (`pg_advisory_lock` de SESIÓN, dos conexiones reales), no la subida
física a un MinIO real.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

import app.core.config as config_module
import app.services.aip_service as aip_service_module
from app.schemas.preservacion import AipCreate
from app.services.aip_service import AipService
from app.services.preservation_worm_service import _worm_lock_key

from .conftest import DSN

pytestmark = pytest.mark.integration

_DOC_CONTENT = b"contenido real del oficio radicado (itest concurrencia AIP)"
_DOC_SHA256 = hashlib.sha256(_DOC_CONTENT).hexdigest()
_FIXED_HASH = "f" * 64


def _schema_name(slug: str) -> str:
    """`tenant_{slug}` con la MISMA normalización que
    `app.core.database.tenant_schema()`/`FileRepository` (guion -> guion
    bajo) -- duplicada aquí, sin el `HTTPException` de por medio, para poder
    verificar el schema físico desde una conexión de asyncpg cruda."""
    return f"tenant_{slug.replace('-', '_')}"


def _make_mock_minio() -> MagicMock:
    minio = MagicMock()
    minio.bucket_exists = AsyncMock(return_value=True)
    minio.make_bucket = AsyncMock()
    minio.get_object_lock_config = AsyncMock(return_value=MagicMock())

    # El doble RECUERDA la retención que se le pidió y la devuelve al releerla,
    # es decir, representa a un almacén que CUMPLE — mismo criterio que el doble
    # compartido de `tests/conftest.py`, y por la misma razón: `aip_service`
    # relee la retención tras sellar el AIP y aborta si no coincide, así que un
    # doble que devolviera lo esperado sin haberlo guardado dejaría esa
    # verificación muda justo aquí, donde se prueba la concurrencia.
    #
    # Sin esto, `get_object_retention` caía al `MagicMock` genérico, que no es
    # awaitable: `TypeError: object MagicMock can't be used in 'await'
    # expression`. Solo se veía con `RUN_INTEGRATION=1`, que es la razón de que
    # el read-back entrara al repositorio sin que nada lo delatara.
    # `None` EXPLÍCITO, y no por gusto: `minio` es un `MagicMock`, así que
    # `getattr(minio, "_ultima_retencion", None)` devuelve un mock recién
    # fabricado en vez del default — «nadie guardó nada» era indistinguible de
    # «guardó algo», y el read-back comparaba el modo contra un `MagicMock` cuyo
    # mensaje de error no dejaba ver la causa. Inicializarlo hace que la falta de
    # retención sea observable, que es lo que el fail-closed tiene que ver.
    minio._ultima_retencion = None

    def _recordar_retencion(*_args, **kwargs):
        retencion = kwargs.get("retention")
        if retencion is not None:
            minio._ultima_retencion = retencion
        return MagicMock(version_id="v1")

    async def _leer_retencion(*_args, **_kwargs):
        return getattr(minio, "_ultima_retencion", None)

    minio.put_object = AsyncMock(side_effect=_recordar_retencion)
    minio.get_object_retention = AsyncMock(side_effect=_leer_retencion)

    async def _get_object(_bucket: str, _key: str):
        obj = MagicMock()
        obj.read = AsyncMock(return_value=_DOC_CONTENT)
        obj.close = MagicMock()
        return obj

    minio.get_object = AsyncMock(side_effect=_get_object)
    return minio


async def _seed_file(conn: asyncpg.Connection, slug: str, *, file_id: uuid.UUID) -> None:
    schema = _schema_name(slug)
    await conn.execute(f'SET search_path = "{schema}", public')
    await conn.execute(
        """
        INSERT INTO files (id, filename, mime_type, file_size, sha256, bucket, object_key)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        file_id,
        "oficio.pdf",
        "application/pdf",
        len(_DOC_CONTENT),
        _DOC_SHA256,
        f"orpycamcp-{slug}-documents",
        f"{file_id}/oficio.pdf",
    )


def _payload(expediente_id: str, doc_file_id: uuid.UUID) -> AipCreate:
    return AipCreate(
        expediente_id=expediente_id,
        documentos=[{"document_id": "R-1", "nombre": "oficio.pdf", "file_id": doc_file_id}],
        retencion_anios=5,
        fecha_inicio="2026-01-01",
    )


async def _empaquetar_own_conn(slug: str, expediente_id: str, doc_file_id: uuid.UUID):
    """Conexión PROPIA por corrutina — `asyncpg.Connection` no es
    concurrency-safe y el advisory lock es POR SESIÓN: compartir una única
    `conn` entre las dos corrutinas solo serializaría en el CLIENTE, sin
    ejercer una carrera real en el servidor (mismo razonamiento que
    `archive-service/tests/integration/test_atomicity.py`)."""
    own_conn = await asyncpg.connect(dsn=DSN)
    try:
        service = AipService(own_conn, slug)
        return await service.empaquetar_aip(_payload(expediente_id, doc_file_id), actor=None)
    finally:
        await own_conn.close()


@pytest.mark.integration
async def test_two_concurrent_empaquetados_same_content_produce_single_aip(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dos empaquetados CONCURRENTES del MISMO expediente, con el MISMO
    `aip_sha256` (forzado, ver docstring del módulo), producen EXACTAMENTE
    UNA fila `aip`, UNA fila `preservacion_worm_objeto` y UNA sola llamada
    física a `put_object` — la corrutina que pierde la carrera del advisory
    lock encuentra la fila que la ganadora ya comprometió y corta-circuita en
    vez de re-subir/re-insertar."""
    slug, conn = tenant_ctx
    # `slug` CRUDO, con guion ("itest-<hex8>") -- remediación de auditoría
    # Media (E07): `FileRepository` ahora valida/normaliza con la MISMA
    # regex canónica que `app.core.database.tenant_schema()` (guion ->
    # guion bajo), igual que `PreservacionRepository`/
    # `PreservacionWormRepository` ya hacían -- ya no hace falta sanitizar
    # el slug antes de construir `AipService`.
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)
    expediente_id = "EXP-CONCURRENCIA-1"

    doc_file_id = uuid.uuid4()
    await _seed_file(conn, slug, file_id=doc_file_id)

    mock_minio = _make_mock_minio()

    with (
        patch.object(aip_service_module, "get_minio", return_value=mock_minio),
        patch.object(aip_service_module, "aip_sha256", new=lambda _zip_bytes: _FIXED_HASH),
    ):
        # Sin sleeps: `asyncio.gather` lanza las dos corrutinas y cada una
        # compite de verdad por el advisory lock de sesión del MISMO
        # expediente en el servidor Postgres real.
        results = await asyncio.gather(
            _empaquetar_own_conn(slug, expediente_id, doc_file_id),
            _empaquetar_own_conn(slug, expediente_id, doc_file_id),
        )

    # Las DOS llamadas responden con el MISMO `aip_id` -- la que perdió la
    # carrera del lock encontró la fila que la ganadora ya había
    # comprometido y no volvió a subir/insertar nada.
    ids = {r.id for r in results}
    assert len(ids) == 1, f"se crearon AIP distintos bajo contenido idéntico: {ids!r}"

    # Verificación desde una TERCERA conexión: el estado REAL comprometido en
    # el servidor, no lo que cada corrutina vio en su propia conexión.
    verify_conn = await asyncpg.connect(dsn=DSN)
    try:
        schema = _schema_name(slug)
        await verify_conn.execute(f'SET search_path = "{schema}", public')
        aip_count = await verify_conn.fetchval(
            "SELECT count(*) FROM aip WHERE expediente_id = $1", expediente_id
        )
        worm_count = await verify_conn.fetchval(
            "SELECT count(*) FROM preservacion_worm_objeto "
            "WHERE expediente_id = $1 AND tipo = 'aip'",
            expediente_id,
        )
    finally:
        await verify_conn.close()

    assert aip_count == 1
    assert worm_count == 1

    # Físicamente: `put_object` (el COMPLIANCE irreversible) solo se llamó
    # UNA vez -- la corrutina que perdió la carrera del lock jamás llegó a
    # subir nada, exactamente el hueco físico que esta remediación cierra
    # (antes de este incremento, ambas habrían subido, y solo el registro en
    # BD habría podido chocar -- y ni eso, si el contenido difería).
    assert mock_minio.put_object.await_count == 1


@pytest.mark.integration
async def test_empaquetar_aip_holds_advisory_lock_on_server_during_put_object(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prueba DIRECTA de que el lock está genuinamente retenido en el
    SERVIDOR (no solo que el resultado final sea consistente): mientras
    `empaquetar_aip` está DENTRO de `put_object` (pausado a propósito con un
    `asyncio.Event`), una TERCERA conexión que intenta el MISMO
    `pg_try_advisory_lock` (no bloqueante) para la MISMA clave debe fallar en
    adquirirlo."""
    slug, conn = tenant_ctx  # `slug` CRUDO, con guion -- ver el comentario en el test anterior
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)
    expediente_id = "EXP-CONCURRENCIA-2"

    doc_file_id = uuid.uuid4()
    await _seed_file(conn, slug, file_id=doc_file_id)

    put_object_entered = asyncio.Event()
    release_put_object = asyncio.Event()

    mock_minio = _make_mock_minio()

    async def _slow_put_object(*_args, **kwargs):
        put_object_entered.set()
        await release_put_object.wait()
        # Recuerda la retención igual que el doble base: esta prueba sustituye
        # `put_object` para poder frenarlo a mitad, y sustituirlo no puede
        # desactivar de paso el read-back que corre justo después.
        retencion = kwargs.get("retention")
        if retencion is not None:
            mock_minio._ultima_retencion = retencion
        return MagicMock(version_id="v1")

    mock_minio.put_object = AsyncMock(side_effect=_slow_put_object)

    lock_key = _worm_lock_key(slug, expediente_id, "aip")

    async def _probe_while_held() -> bool:
        await put_object_entered.wait()  # espera a que el holder ya tenga el lock
        probe_conn = await asyncpg.connect(dsn=DSN)
        try:
            acquired = await probe_conn.fetchval("SELECT pg_try_advisory_lock($1)", lock_key)
            if acquired:
                await probe_conn.execute("SELECT pg_advisory_unlock($1)", lock_key)
            return bool(acquired)
        finally:
            release_put_object.set()  # deja avanzar al holder para que el test termine
            await probe_conn.close()

    with (
        patch.object(aip_service_module, "get_minio", return_value=mock_minio),
        patch.object(aip_service_module, "aip_sha256", new=lambda _zip_bytes: _FIXED_HASH),
    ):
        _, probe_acquired = await asyncio.gather(
            _empaquetar_own_conn(slug, expediente_id, doc_file_id),
            _probe_while_held(),
        )

    assert probe_acquired is False, (
        "un tercero pudo tomar pg_advisory_lock mientras empaquetar_aip lo "
        "retenía -- el advisory lock NO está sirviendo de verdad en el servidor"
    )
