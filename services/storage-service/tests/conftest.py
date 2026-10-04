# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Test fixtures for storage-service.

DB dependency (get_db_conn) and MinIO client (get_minio) are both mocked
so tests run without real infrastructure.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import config as cfg_module
from app.core.database import get_db_conn
from app.main import app

TENANT_SLUG = "demo"
TENANT_HEADERS = {
    "X-Tenant-Slug": TENANT_SLUG,
    "X-User-Id": str(uuid.uuid4()),
}

# Token compartido D-02: los endpoints que sirven bytes/URL/metadata de un
# archivo (download presigned, content, metadata) lo exigen en
# X-Internal-Token. Los fixtures de abajo lo configuran por defecto para que
# los tests existentes (que ejercen esos endpoints como si fueran el mediador
# interno) no tengan que conocer el detalle; los tests del propio gate D-02
# (`test_internal_token_gate.py`) manipulan `cfg_module.settings` directamente.
TEST_INTERNAL_TOKEN = "test-storage-internal-token-xxx"
INTERNAL_HEADERS = {**TENANT_HEADERS, "X-Internal-Token": TEST_INTERNAL_TOKEN}

FILE_ID = uuid.uuid4()
SHA256 = "a" * 64
NOW = datetime.now(tz=UTC)


def make_file_record(
    *,
    file_id: uuid.UUID | None = None,
    filename: str = "carta.pdf",
    mime_type: str | None = "application/pdf",
    file_size: int = 12345,
    sha256: str = SHA256,
    bucket: str = f"orpycamcp-{TENANT_SLUG}-documents",
    object_key: str | None = None,
    uploaded_by: uuid.UUID | None = None,
    uploaded_at: datetime | None = None,
    version: int = 1,
    is_current: bool = True,
    root_id: uuid.UUID | None = None,
    replaces_id: uuid.UUID | None = None,
    # Soft-delete fields (D-04): NULL means active; NOT NULL means logically deleted.
    deleted_at: datetime | None = None,
    deleted_by: uuid.UUID | None = None,
    # PDF/A ingest verdict (E10 C2b follow-up, migración 013): NULL by
    # default (matches a row from before this increment / a non-PDF file /
    # the gate disabled).
    pdfa_status: str | None = None,
    pdfa_profile: str | None = None,
    pdfa_checked_at: datetime | None = None,
) -> dict:
    fid = file_id or FILE_ID
    return {
        "id": fid,
        "filename": filename,
        "mime_type": mime_type,
        "file_size": file_size,
        "sha256": sha256,
        "bucket": bucket,
        "object_key": object_key or f"{fid}/carta.pdf",
        "uploaded_by": uploaded_by or uuid.uuid4(),
        "uploaded_at": uploaded_at or NOW,
        "version": version,
        "is_current": is_current,
        "root_id": root_id or fid,
        "replaces_id": replaces_id,
        "deleted_at": deleted_at,
        "deleted_by": deleted_by,
        "pdfa_status": pdfa_status,
        "pdfa_profile": pdfa_profile,
        "pdfa_checked_at": pdfa_checked_at,
    }


def make_worm_row(
    *,
    worm_id: uuid.UUID | None = None,
    expediente_id: str = "EXP-1",
    tipo: str = "indice",
    bucket: str = f"orpycamcp-{TENANT_SLUG}-preservation",
    object_key: str | None = None,
    version_id: str | None = "v1",
    sha256: str = SHA256,
    retain_until: datetime | None = None,
    retention_mode: str = "COMPLIANCE",
    legal_hold: bool = False,
    created_at: datetime | None = None,
    file_id: uuid.UUID | None = None,
    # E10 debt (renovación de retención WORM para series CT, migración 011):
    # ambos NULLABLE/con default (filas anteriores a esa migración quedan
    # `renovaciones=0`/`renovado_at=None`) — el default aquí cubre ambos
    # casos sin que cada test que NO versa sobre renovación tenga que
    # declararlos explícitamente.
    renovaciones: int = 0,
    renovado_at: datetime | None = None,
) -> dict:
    return {
        "id": worm_id or uuid.uuid4(),
        "expediente_id": expediente_id,
        "tipo": tipo,
        # Migración 014: DE QUÉ es `expediente_id`. Se deriva del `tipo` en vez
        # de ser otro parámetro con default, porque el CHECK de la base impone
        # la pareja — un doble que pudiera declarar una combinación imposible
        # dejaría pasar tests sobre filas que la base rechazaría.
        "objeto_tipo": "lote" if tipo == "acta_eliminacion" else "expediente",
        "bucket": bucket,
        "object_key": object_key or f"{uuid.uuid4()}/indice.xml",
        "version_id": version_id,
        "sha256": sha256,
        "retain_until": retain_until or (NOW + timedelta(days=1825)),
        "retention_mode": retention_mode,
        "legal_hold": legal_hold,
        "created_at": created_at or NOW,
        # E10 Inc.1 (migración 009): `file_id` NULLABLE (filas anteriores a
        # esa migración quedan NULL) — el default `None` aquí cubre AMBOS
        # casos sin que cada test tenga que declararlo explícitamente.
        "file_id": file_id,
        "renovaciones": renovaciones,
        "renovado_at": renovado_at,
    }


def make_aip_row(
    *,
    aip_id: uuid.UUID | None = None,
    expediente_id: str = "EXP-1",
    aip_sha256: str = SHA256,
    indice_sha256: str | None = SHA256,
    num_documentos: int = 1,
    estado: str = "completo",
    created_at: datetime | None = None,
    bucket: str | None = f"orpycamcp-{TENANT_SLUG}-preservation",
    object_key: str | None = None,
    aip_bytes: int | None = 4096,
    formato: str = "application/zip",
    retain_until: datetime | None = None,
    retention_mode: str | None = "COMPLIANCE",
    legal_hold: bool = False,
) -> dict:
    """Fila cruda de `aip` (mismas columnas que `_AIP_COLS`), para mockear
    `PreservacionRepository.latest_aip` en tests de `GET /preservacion/aip/{id}`
    (E10 Increment C, gate D-02)."""
    return {
        "id": aip_id or uuid.uuid4(),
        "expediente_id": expediente_id,
        "premis_xml": "<premis/>",
        "aip_sha256": aip_sha256,
        "indice_sha256": indice_sha256,
        "num_documentos": num_documentos,
        "estado": estado,
        "created_at": created_at or NOW,
        "bucket": bucket,
        "object_key": object_key or f"{expediente_id}/aip.zip",
        "aip_bytes": aip_bytes,
        "formato": formato,
        "retain_until": retain_until or (NOW + timedelta(days=1825)),
        "retention_mode": retention_mode,
        "legal_hold": legal_hold,
    }


def conceder_permisos(conn: AsyncMock, *, conceder: bool = True) -> None:
    """Hace que `check_permission` responda `conceder` sin tocar el resto.

    `POST /upload` dejó de estar sin gate: exige `PERM_RADI` (era el único
    endpoint que el gateway expone de este servicio y no pedía nada). Los tests
    lo tienen que declarar, y se declara **aquí** y no test a test para que
    `conceder=False` siga siendo escribible: un doble que concediera siempre
    dejaría el gate sin control negativo.

    Se distingue por la forma de la consulta —`check_permission` la hace con un
    SQL que menciona `auth_group_permissions`— en vez de forzar todos los
    `fetchval` a `True`, que taparía las consultas de dominio que los tests sí
    quieren ver devolver `None`.

    **No se aplica por defecto, a propósito.** La primera versión la ponía en
    `_make_mock_conn` y dejó en rojo los tres tests que deniegan adrede
    (`test_rbac_denial_audit`, `test_preservacion`): un doble que conceda
    siempre convierte cualquier gate en decorativo, y el sitio donde eso se paga
    es justo el test que comprueba la denegación. El doble falla cerrado como el
    sistema; conceder es un acto explícito del test que lo necesita.
    """
    original = conn.fetchval.side_effect

    async def _fetchval(*args, **kwargs):
        sql = args[0] if args and isinstance(args[0], str) else ""
        if "auth_group_permissions" in sql:
            return conceder
        if original is not None:
            return await original(*args, **kwargs)
        return conn.fetchval.return_value

    conn.fetchval.side_effect = _fetchval


def _make_mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.fetch.return_value = []
    # `async with conn.transaction()` (create_version atómico).
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _make_mock_minio() -> MagicMock:
    minio = MagicMock()
    minio.bucket_exists = AsyncMock(return_value=True)
    minio.make_bucket = AsyncMock()
    # Server-side copy for the LOCAL AIP replica target (E10 Increment C1b).
    minio.copy_object = AsyncMock(return_value=MagicMock(version_id="v1"))
    minio.presigned_get_object = AsyncMock(return_value="http://minio/presigned-url")
    minio.remove_object = AsyncMock()
    minio.list_buckets = AsyncMock(return_value=[])
    # WORM (E10 Increment A): default = bucket ya existe CON Object Lock (no
    # levanta S3Error) — el caso "sin lock" lo fuerza cada test que lo necesite.
    minio.get_object_lock_config = AsyncMock(return_value=MagicMock())
    # E10 debt (renovación de retención WORM para series CT): distinto de
    # `put_object` -- `PreservationWormService.renovar_retencion` SOLO llama
    # esto (nunca vuelve a subir bytes), ver tests/test_preservacion_worm_renovacion.py.
    # Registra igual que `put_object`: la renovación también se relee, así que un
    # doble que aceptara la extensión sin recordarla haría fallar al verificador
    # por una carencia del doble y no del código.
    def _recordar_extension(*args, **kwargs):
        for candidato in args[2:]:
            if hasattr(candidato, "mode"):
                minio._ultima_retencion = candidato
                break
        if kwargs.get("config") is not None:
            minio._ultima_retencion = kwargs["config"]

    minio.set_object_retention = AsyncMock(side_effect=_recordar_extension)

    # Read-back de la retención: el doble RECUERDA lo que se le pidió y lo
    # devuelve, es decir, representa a un almacén que CUMPLE.
    #
    # No es un detalle de fontanería. `verificar_retencion_aplicada` relee la
    # retención recién escrita y aborta si no coincide, porque hasta entonces el
    # sistema asentaba `retention_mode='COMPLIANCE'` y un evento PREMIS en `ok`
    # sobre la palabra del almacén. Un doble que devolviera cualquier cosa haría
    # fallar a todos los tests por un motivo que no es el suyo; y uno que
    # devolviera siempre «correcto» sin mirar lo pedido sería una afirmación
    # gratuita sobre el colaborador.
    #
    # Que el almacén REAL cumpla no lo prueba este doble —no puede—: lo prueba la
    # batería de `tests/integration/test_worm_object_lock_real_s3.py` contra un S3
    # de verdad. Aquí solo se sostiene el contrato para que los tests de la lógica
    # midan la lógica.
    # `None` EXPLÍCITO: `minio` es un `MagicMock`, así que el default de
    # `getattr` de abajo nunca se aplica y «nadie guardó retención» sería
    # indistinguible de «guardó algo». Sin esto el read-back compara el modo
    # contra un mock fantasma, y su mensaje de error no deja ver la causa —
    # medido en `tests/integration/test_aip_concurrent_real_sql.py`.
    minio._ultima_retencion = None

    def _recordar_retencion(*args, **kwargs):
        retencion = kwargs.get("retention")
        if retencion is not None:
            minio._ultima_retencion = retencion
        return MagicMock(version_id="v1")

    minio.put_object = AsyncMock(side_effect=_recordar_retencion)
    # La copia server-side de la réplica local también lleva retención, y ahora se
    # relee (AREA-08-04): el doble de un almacén que CUMPLE la recuerda igual.
    minio.copy_object = AsyncMock(side_effect=_recordar_retencion)

    async def _leer_retencion(*_args, **_kwargs):
        return getattr(minio, "_ultima_retencion", None)

    minio.get_object_retention = AsyncMock(side_effect=_leer_retencion)
    return minio


def minio_get_object(content: bytes) -> AsyncMock:
    """Mock de `Minio.get_object` que devuelve `content` al leerlo.

    Compartido entre los tests de WORM (Increment A) y AIP real (Increment
    B) — ambos flujos descargan bytes de MinIO para recalcular la fixity.
    """
    obj = MagicMock()
    obj.read = AsyncMock(return_value=content)
    obj.close = MagicMock()
    return AsyncMock(return_value=obj)


def minio_get_object_stream(content: bytes) -> AsyncMock:
    """Mock de `Minio.get_object` para el streaming real (`iter_chunked`).

    Distinto de `minio_get_object` (que mockea `.read()`, usado por
    `verify_integrity` para recalcular el hash del objeto ENTERO):
    `StorageService.get_object_stream` (D-02, `GET /files/{id}/content`)
    consume `.content.iter_chunked(...)`, la superficie real de
    `aiohttp.ClientResponse` que devuelve miniopy_async — se mockea aparte
    porque `async for chunk in response.content.iter_chunked(n)` exige que
    la llamada devuelva un async-iterator, no una corrutina.
    """

    async def _iter_chunked(_chunk_size: int):
        yield content

    obj = MagicMock()
    obj.content = MagicMock()
    obj.content.iter_chunked = MagicMock(side_effect=lambda n: _iter_chunked(n))
    obj.close = MagicMock()
    return AsyncMock(return_value=obj)


def _minio_patches(mock_minio: MagicMock):
    """
    Patch get_minio in all modules that import it at module level.

    Every module does `from app.core.minio_client import get_minio`, which binds
    its OWN local name — patching the source module alone does not affect
    already-imported references, so each consumer must be patched independently
    (WORM: `preservation_worm_service.py` is the 4th location, E10 Increment A;
    `aip_service.py` is the 5th, E10 Increment B).
    """
    return (
        patch("app.core.minio_client.get_minio", return_value=mock_minio),
        patch("app.services.storage_service.get_minio", return_value=mock_minio),
        patch("app.services.preservation_worm_service.get_minio", return_value=mock_minio),
        patch("app.services.aip_service.get_minio", return_value=mock_minio),
        patch("app.services.export_service.get_minio", return_value=mock_minio),
        patch("app.routers.health.get_minio", return_value=mock_minio),
    )


@pytest_asyncio.fixture(scope="function")
async def client_with_conn(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[AsyncClient, AsyncMock, MagicMock], None]:
    """Yields (client, mock_conn, mock_minio) so tests can configure mocks per-call.

    D-02: configura INTERNAL_SERVICE_TOKEN por defecto (TEST_INTERNAL_TOKEN) —
    la mayoría de los tests que usan este fixture ejercen endpoints ahora
    gateados (metadata/download/content) como si fueran el mediador interno;
    los tests del gate en sí lo desconfiguran/lo pisan explícitamente.
    """
    monkeypatch.setattr(cfg_module.settings, "internal_service_token", TEST_INTERNAL_TOKEN)
    mock_conn = _make_mock_conn()
    mock_minio = _make_mock_minio()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_get_db_conn

    p1, p2, p3, p4, p5, p6 = _minio_patches(mock_minio)
    with p1, p2, p3, p4, p5, p6:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac, mock_conn, mock_minio

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncClient, None]:
    """Simple client fixture — use when you don't need to configure mock internals."""
    monkeypatch.setattr(cfg_module.settings, "internal_service_token", TEST_INTERNAL_TOKEN)
    mock_conn = _make_mock_conn()
    mock_minio = _make_mock_minio()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_get_db_conn

    p1, p2, p3, p4, p5, p6 = _minio_patches(mock_minio)
    with p1, p2, p3, p4, p5, p6:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac

    app.dependency_overrides.clear()
