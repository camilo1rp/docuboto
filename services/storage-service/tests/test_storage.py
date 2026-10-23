# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests for storage-service endpoints.

DB and MinIO are fully mocked — no real infrastructure required.

Repository call sequence for UPLOAD (new file):
  1. execute  — SET search_path (find_by_sha256)
  2. fetchrow — SELECT sha256 → None (no duplicate)
  3. [MinIO]  bucket_exists → True
  4. [MinIO]  put_object
  5. execute  — SET search_path (create)
  6. fetchrow — INSERT INTO files RETURNING ...

Repository call sequence for UPLOAD (duplicate):
  1. execute  — SET search_path (find_by_sha256)
  2. fetchrow — SELECT sha256 → existing record (deduplication short-circuits)

Repository call sequence for GET metadata / presigned URL:
  1. execute  — SET search_path (get_by_id)
  2. fetchrow — SELECT file

Repository call sequence for DELETE (soft-delete D-04 — NO MinIO call; C-5/C14):
  1. execute  — SET search_path (lock_any_state)
  2. fetchrow — SELECT file ... FOR UPDATE
  3. execute/fetchval/fetch — SET search_path + to_regclass + SELECT FROM v_anexo_citado_vigente
  4. execute  — SET search_path (soft_delete) + UPDATE files SET deleted_at ... → "UPDATE 1"
  5. [audit]  conn.transaction + advisory lock + fetchval + INSERT
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.core import config as cfg_module
from tests.conftest import (
    FILE_ID,
    INTERNAL_HEADERS,
    TENANT_HEADERS,
    conceder_permisos,
    make_file_record,
    minio_get_object_stream,
    vista_de_citas,
)

_FILE_CONTENT = b"PDF content here"
_MISMO_AUTOR = uuid.UUID(TENANT_HEADERS["X-User-Id"])
_FILENAME = "carta.pdf"
_MIME_TYPE = "application/pdf"


# ---------------------------------------------------------------------------
# POST /api/v1/storage/upload — success (new file)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_file_success(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI
    record = make_file_record(file_id=FILE_ID)

    # find_by_sha256 returns None (no duplicate), then create returns the record
    mock_conn.fetchrow.side_effect = [None, record]

    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, _FILE_CONTENT, _MIME_TYPE)},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["filename"] == _FILENAME
    assert body["mime_type"] == _MIME_TYPE
    assert body["file_size"] == record["file_size"]
    assert len(body["sha256"]) == 64
    assert "uploaded_at" in body
    mock_minio.put_object.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /api/v1/storage/upload — deduplication (same SHA-256)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_file_deduplication(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Uploading the same content twice returns the existing record with 200."""
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI
    existing = make_file_record(file_id=FILE_ID, uploaded_by=_MISMO_AUTOR)

    # find_by_sha256 returns the existing record → short-circuit, no MinIO call
    mock_conn.fetchrow.return_value = existing

    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, _FILE_CONTENT, _MIME_TYPE)},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(existing["id"])
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_deduplicado_deja_la_marca_de_reutilizacion(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """La rama de dedup escribe `files.dedup_hit_at` (UPDATE ... RETURNING): es lo
    que serializa con la purga de huérfanos y protege el objeto compartido."""
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)
    mock_conn.fetchrow.return_value = make_file_record(
        file_id=FILE_ID, uploaded_by=_MISMO_AUTOR
    )

    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, _FILE_CONTENT, _MIME_TYPE)},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 200
    sqls = [c.args[0] for c in mock_conn.fetchrow.await_args_list]
    assert any("SET dedup_hit_at" in q and "RETURNING" in q for q in sqls)


@pytest.mark.asyncio
async def test_upload_deduplicado_cuya_fila_se_purgo_entre_medias_sube_de_nuevo(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Si la marca no encuentra la fila (la purga la borró tras la búsqueda), el
    contenido se sube como NUEVO en vez de devolver un `file_id` inexistente."""
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)
    existing = make_file_record(file_id=FILE_ID, uploaded_by=_MISMO_AUTOR)
    nuevo = make_file_record(file_id=uuid.uuid4())
    mock_minio.bucket_exists.return_value = True
    # búsqueda -> existe; marca -> ya no está; INSERT -> fila nueva
    mock_conn.fetchrow.side_effect = [existing, None, nuevo]

    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, _FILE_CONTENT, _MIME_TYPE)},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    assert response.json()["id"] == str(nuevo["id"])
    mock_minio.put_object.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_mismo_contenido_de_otro_autor_no_se_deduplica(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """AREA-08-09 (SEC2-E03): la dedup NO cruza autores. Quien sube un contenido que
    ya existe como anexo de OTRO recibe 201, su propia fila y su propio filename; ni el
    `id` ni el nombre original del ajeno salen en la respuesta (oráculo de existencia
    sobre anexos reservados)."""
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)
    ajeno = make_file_record(file_id=FILE_ID)  # uploaded_by aleatorio != el del header
    ajeno["filename"] = "ASUNTO-RESERVADO-denuncia.pdf"
    propio = make_file_record(file_id=uuid.uuid4())
    mock_minio.bucket_exists.return_value = True
    # Aunque la búsqueda devolviera la fila ajena (consulta aflojada), el servicio la descarta.
    mock_conn.fetchrow.side_effect = [ajeno, propio]

    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, _FILE_CONTENT, _MIME_TYPE)},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] != str(ajeno["id"])
    assert "RESERVADO" not in response.text
    mock_minio.put_object.assert_awaited_once()
    sqls = [c.args[0] for c in mock_conn.fetchrow.await_args_list]
    assert not any("SET dedup_hit_at" in q for q in sqls)  # no marcó la fila ajena


@pytest.mark.asyncio
async def test_upload_la_busqueda_de_dedup_filtra_por_autor(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """La consulta de dedup lleva `uploaded_by = $2` con el autor de la petición."""
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)
    mock_minio.bucket_exists.return_value = True
    mock_conn.fetchrow.side_effect = [None, make_file_record(file_id=uuid.uuid4())]

    await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, _FILE_CONTENT, _MIME_TYPE)},
        headers=TENANT_HEADERS,
    )

    busqueda = mock_conn.fetchrow.await_args_list[0]
    assert "uploaded_by = $2" in busqueda.args[0]
    assert busqueda.args[2] == _MISMO_AUTOR


@pytest.mark.asyncio
async def test_anexo_anonimo_nunca_se_deduplica(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Ventanilla pública (sin autor): no se busca dedup -- ni lee ni marca la fila de
    otro, aunque el contenido coincida con uno existente."""
    client, mock_conn, mock_minio = client_with_conn
    mock_minio.bucket_exists.return_value = True
    mock_conn.fetchrow.side_effect = [make_file_record(file_id=uuid.uuid4())]

    response = await client.post(
        "/api/v1/storage/internal/anexo-externo",
        files={"file": (_FILENAME, _FILE_CONTENT, _MIME_TYPE)},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 201, response.text
    sqls = [c.args[0] for c in mock_conn.fetchrow.await_args_list]
    assert not any("FROM files WHERE sha256" in q for q in sqls)
    mock_minio.put_object.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /api/v1/storage/upload — missing X-Tenant-Slug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_missing_tenant_header(client: AsyncClient) -> None:
    """Sin tenant ya no es 400 sino 403, y el cambio se DECLARA.

    `/upload` pasó a exigir `PERM_RADI`, y el gate corre antes que el
    `_require_tenant` del propio endpoint: sin `X-Tenant-Slug` no hay schema
    contra el que resolver el permiso, así que niega. El resultado sigue siendo
    el correcto —la petición no se atiende— y es además el mismo código que
    devuelven los demás endpoints gateados de este servicio ante la misma
    falta, en vez de dos respuestas distintas para la misma causa.
    """
    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, _FILE_CONTENT, _MIME_TYPE)},
        # no X-Tenant-Slug header
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/v1/storage/files/{file_id} — metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_file_metadata(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    record = make_file_record(file_id=FILE_ID)
    mock_conn.fetchrow.return_value = record

    response = await client.get(
        f"/api/v1/storage/files/{FILE_ID}",
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(FILE_ID)
    assert body["filename"] == record["filename"]
    assert body["sha256"] == record["sha256"]


# ---------------------------------------------------------------------------
# GET /api/v1/storage/files/{file_id} — not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_file_not_found(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None

    response = await client.get(
        f"/api/v1/storage/files/{uuid.uuid4()}",
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "file_not_found"


# ---------------------------------------------------------------------------
# GET /api/v1/storage/files/{file_id}/download — presigned URL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_presigned_url(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI
    record = make_file_record(file_id=FILE_ID)
    mock_conn.fetchrow.return_value = record
    mock_minio.presigned_get_object.return_value = "http://minio/presigned-url"

    response = await client.get(
        f"/api/v1/storage/files/{FILE_ID}/download",
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["url"] == "http://minio/presigned-url"
    assert body["expires_in"] == 3600
    mock_minio.presigned_get_object.assert_awaited_once()


# ---------------------------------------------------------------------------
# GET /api/v1/storage/files/{file_id}/content — internal byte stream (D-02)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_file_content_streams_bytes(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """El mediador (document-service) pide los BYTES, no una presigned URL."""
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI
    record = make_file_record(file_id=FILE_ID)
    mock_conn.fetchrow.return_value = record
    mock_minio.get_object = minio_get_object_stream(_FILE_CONTENT)

    response = await client.get(
        f"/api/v1/storage/files/{FILE_ID}/content",
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 200
    assert response.content == _FILE_CONTENT
    assert response.headers["content-type"] == record["mime_type"]
    assert _FILENAME in response.headers["content-disposition"]
    mock_minio.get_object.assert_awaited_once_with(record["bucket"], record["object_key"])


@pytest.mark.asyncio
async def test_get_file_content_not_found(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None

    response = await client.get(
        f"/api/v1/storage/files/{uuid.uuid4()}/content",
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "file_not_found"


# ---------------------------------------------------------------------------
# DELETE /api/v1/storage/files/{file_id} — soft-delete (D-04)
# ---------------------------------------------------------------------------


async def _execute_con_update_1(sql, *args, **kwargs):
    """`execute` que contesta «UPDATE 1» al UPDATE de soft-delete y None al resto (SET
    search_path, advisory lock y INSERT del audit): el borrado ahora toma `FOR UPDATE` y consulta la
    vista de citas (C-5/C14), así que el número de `execute` ya no es una lista fija."""
    return "UPDATE 1" if sql.lstrip().upper().startswith("UPDATE FILES SET DELETED_AT") else None


@pytest.mark.asyncio
async def test_delete_file_soft_deletes_not_purges(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """DELETE marks the row as logically deleted — does NOT remove the MinIO object.

    execute call order:
      (el número y el orden de los `execute` dejaron de ser fijos con C-5/C14: el borrado toma
      `FOR UPDATE` y consulta la vista de citas; basta con que el UPDATE conteste «UPDATE 1»)
    """
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI
    vista_de_citas(mock_conn)  # C-5/C14: la vista contrato existe y nadie cita el anexo
    record = make_file_record(file_id=FILE_ID)

    mock_conn.fetchrow.return_value = record
    # audit.append also calls fetchval for prev_hash → already returns None by default.
    mock_conn.execute.side_effect = _execute_con_update_1

    response = await client.delete(
        f"/api/v1/storage/files/{FILE_ID}",
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 204
    # Soft-delete MUST NOT call remove_object (MinIO object is preserved until
    # TRD final disposition explicitly purges it — D-04, constitución inv. 12).
    mock_minio.remove_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_file(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Alias kept for backward compatibility; delegates to the canonical test above."""
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI
    vista_de_citas(mock_conn)
    record = make_file_record(file_id=FILE_ID)

    mock_conn.fetchrow.return_value = record
    mock_conn.execute.side_effect = _execute_con_update_1

    response = await client.delete(
        f"/api/v1/storage/files/{FILE_ID}",
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 204


# ---------------------------------------------------------------------------
# GET /api/v1/storage/files/{file_id} — soft-deleted file is not visible (D-04)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_soft_deleted_file_returns_404(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """A soft-deleted file must be invisible to normal metadata and download routes.

    The repository's get_by_id filters `AND deleted_at IS NULL`; the DB returns
    no row, so the service treats it as not found. We simulate this by making
    fetchrow return None (as the real DB would for a soft-deleted row).
    """
    client, mock_conn, _ = client_with_conn
    # Simulate that the row has deleted_at set → DB WHERE clause filters it out.
    mock_conn.fetchrow.return_value = None

    response = await client.get(
        f"/api/v1/storage/files/{FILE_ID}",
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "file_not_found"


# ---------------------------------------------------------------------------
# DELETE /api/v1/storage/files/{file_id} — not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_file_not_found(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None

    response = await client.delete(
        f"/api/v1/storage/files/{uuid.uuid4()}",
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "file_not_found"


# ---------------------------------------------------------------------------
# POST /upload — formato no admitido (RF-DIG-04)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_unsupported_format_is_415(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI

    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": ("malware.exe", b"MZ...", "application/x-msdownload")},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 415
    assert response.json()["detail"]["error"] == "unsupported_media_type"
    mock_minio.put_object.assert_not_awaited()


# ---------------------------------------------------------------------------
# GET /files/{id}/verify — reverificación de integridad (RF-DIG-02)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_integrity_ok(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    import hashlib

    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI
    content = b"verified content"
    real_sha = hashlib.sha256(content).hexdigest()
    mock_conn.fetchrow.return_value = make_file_record(file_id=FILE_ID, sha256=real_sha)

    obj = MagicMock()
    obj.read = AsyncMock(return_value=content)
    obj.close = MagicMock()
    mock_minio.get_object = AsyncMock(return_value=obj)

    response = await client.get(f"/api/v1/storage/files/{FILE_ID}/verify", headers=INTERNAL_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["integrity_ok"] is True
    assert body["expected_sha256"] == real_sha
    assert body["actual_sha256"] == real_sha


@pytest.mark.asyncio
async def test_verify_integrity_mismatch_is_409(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI
    # El registro dice "a"*64 pero el objeto en MinIO es otro contenido.
    mock_conn.fetchrow.return_value = make_file_record(file_id=FILE_ID, sha256="a" * 64)

    obj = MagicMock()
    obj.read = AsyncMock(return_value=b"tampered bytes")
    obj.close = MagicMock()
    mock_minio.get_object = AsyncMock(return_value=obj)

    response = await client.get(f"/api/v1/storage/files/{FILE_ID}/verify", headers=INTERNAL_HEADERS)

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "integrity_check_failed"


@pytest.mark.asyncio
async def test_verify_integrity_not_found(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None

    response = await client.get(
        f"/api/v1/storage/files/{uuid.uuid4()}/verify", headers=INTERNAL_HEADERS
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "file_not_found"


# ---------------------------------------------------------------------------
# POST /files/{id}/replace — versionado (RF-DIG-01)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_creates_new_version(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI
    current = make_file_record(file_id=FILE_ID)  # v1, sha = "a"*64
    v2 = make_file_record(file_id=uuid.uuid4(), version=2, root_id=FILE_ID, replaces_id=FILE_ID)
    # get_by_id → current; create_version INSERT RETURNING → v2
    mock_conn.fetchrow.side_effect = [current, v2]

    response = await client.post(
        f"/api/v1/storage/files/{FILE_ID}/replace",
        files={"file": ("carta_v2.pdf", b"nuevo contenido distinto", "application/pdf")},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 201
    assert response.json()["version"] == 2
    mock_minio.put_object.assert_awaited_once()


@pytest.mark.asyncio
async def test_replace_same_content_no_new_version(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    import hashlib

    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # `/upload` exige PERM_RADI
    content = b"contenido identico"
    sha = hashlib.sha256(content).hexdigest()
    mock_conn.fetchrow.return_value = make_file_record(file_id=FILE_ID, sha256=sha)

    response = await client.post(
        f"/api/v1/storage/files/{FILE_ID}/replace",
        files={"file": ("carta.pdf", content, "application/pdf")},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 200  # sin versión nueva
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_not_current_is_409(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = make_file_record(file_id=FILE_ID, is_current=False)

    response = await client.post(
        f"/api/v1/storage/files/{FILE_ID}/replace",
        files={"file": ("c.pdf", b"x", "application/pdf")},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "not_current_version"


@pytest.mark.asyncio
async def test_replace_not_found_is_404(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None

    response = await client.post(
        f"/api/v1/storage/files/{uuid.uuid4()}/replace",
        files={"file": ("c.pdf", b"x", "application/pdf")},
        headers=INTERNAL_HEADERS,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_versions(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    root = FILE_ID
    v1 = make_file_record(file_id=root, version=1, root_id=root)
    v2 = make_file_record(file_id=uuid.uuid4(), version=2, root_id=root, replaces_id=root)
    mock_conn.fetchrow.return_value = v2  # get_by_id
    mock_conn.fetch.return_value = [v1, v2]  # list_versions

    response = await client.get(f"/api/v1/storage/files/{root}/versions", headers=INTERNAL_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert [v["version"] for v in body] == [1, 2]


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient) -> None:
    """`/health` es VIVACIDAD: gobierna los reinicios del contenedor.

    Antes tomaba una conexión del pool en cada sondeo (cada 15 s) para preguntar
    `SELECT 1`, metiendo una dependencia externa en la sonda que decide si el
    contenedor se reinicia. Reiniciar por un Postgres caído no arregla Postgres.
    El diagnóstico de dependencias vive ahora en `/health/ready`.
    """
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "storage-service"}


# ---------------------------------------------------------------------------
# Tope de tamaño (413) — no existía ninguno
#
# `await file.read()` copiaba el fichero entero a memoria: la SEGUNDA copia,
# después de la que hacía el gateway, con `memory: 1G` por réplica. El chequeo va
# ANTES de esa lectura; hacerlo después sería rechazar la subida una vez pagado
# su coste completo.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_por_encima_del_tope_es_413(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # el tope es lo que se prueba, no el gate
    monkeypatch.setattr(cfg_module.settings, "max_upload_bytes", 1024)

    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": ("grande.pdf", b"x" * 4096, "application/pdf")},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 413
    assert response.json()["detail"]["error"] == "file_too_large"
    # Ni un byte llegó al almacén: el rechazo ocurre antes de leer y de subir.
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_dentro_del_tope_sigue_funcionando(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control positivo: sin esto, un 413 constante pasaría el test de arriba."""
    client, mock_conn, _ = client_with_conn
    conceder_permisos(mock_conn)  # el tope es lo que se prueba, no el gate
    monkeypatch.setattr(cfg_module.settings, "max_upload_bytes", 1024)
    # find_by_sha256 → None (sin duplicado), luego create devuelve el registro.
    mock_conn.fetchrow.side_effect = [None, make_file_record(file_id=FILE_ID)]

    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": ("pequeno.pdf", b"x" * 512, "application/pdf")},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 201


@pytest.mark.asyncio
async def test_upload_exige_permiso_de_radicacion(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """El control negativo del gate que `/upload` no tenía.

    Era el ÚNICO endpoint de este servicio que el gateway expone al exterior
    —los que sirven bytes, URL o metadata están sellados con `X-Internal-Token`
    (D-02)— y no pedía nada: cualquier autenticado de la entidad subía archivos
    al almacén.

    Se comprueba que la denegación ocurre **antes de tocar MinIO**: un gate que
    rechazara después de haber subido el objeto habría dejado el fichero en el
    bucket y solo se habría notado en la factura de almacenamiento.
    """
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn, conceder=False)

    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": (_FILENAME, _FILE_CONTENT, _MIME_TYPE)},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 403
    # El mensaje nombra el NIVEL exigido: faltar el permiso y tenerlo con nivel
    # insuficiente son dos problemas distintos, y el texto anterior los confundía.
    assert (
        response.json()["detail"]["detail"]
        == "Se requiere el permiso PERM_RADI (nivel 1 o superior)"
    )
    mock_minio.put_object.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/v1/storage/internal/artefacto — artefactos que produce el SISTEMA
# ---------------------------------------------------------------------------
#
# El índice electrónico firmado es XML y viajaba por `/upload`, el endpoint de
# los anexos que sube una persona, cuya lista blanca (RF-DIG-04) no admite XML.
# Storage respondía 415 y la firma XAdES —producida entera, sello de tiempo
# incluido— se perdía al guardarla: el índice quedaba `pendiente_firma` para
# siempre y ningún expediente podía cerrarse con índice firmado.
#
# Lo que estos casos fijan es la SEPARACIÓN, no el formato concreto: lo que el
# sistema produce y lo que sube un funcionario son dos cosas con dos listas y
# dos puertas. Ensanchar la de los anexos habría desbloqueado el índice
# abriendo XML a cualquiera que suba un archivo.


@pytest.mark.asyncio
async def test_artefacto_del_sistema_admite_xml(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    record = make_file_record(file_id=FILE_ID)
    mock_conn.fetchrow.side_effect = [None, record]

    response = await client.post(
        "/api/v1/storage/internal/artefacto",
        files={"file": ("indice-firmado.xml", b"<indice/>", "application/xml")},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 201
    mock_minio.put_object.assert_awaited_once()


@pytest.mark.asyncio
async def test_el_endpoint_de_anexos_sigue_rechazando_xml(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """El gemelo: la puerta de los anexos NO se ensanchó.

    Sin este caso, «arreglar» el índice añadiendo `application/xml` a la lista
    de RF-DIG-04 pasaría en verde — y habría abierto a cualquiera la subida de
    XML como anexo, que es superficie nueva para un problema que no era suyo.
    """
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)

    response = await client.post(
        "/api/v1/storage/upload",
        files={"file": ("indice-firmado.xml", b"<indice/>", "application/xml")},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 415
    assert response.json()["detail"]["error"] == "unsupported_media_type"
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_artefacto_del_sistema_exige_token_interno(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Sin el token, la ruta no existe para quien llame desde fuera.

    El gateway no la enruta, pero eso es topología, no un control: el sello es
    lo que impide que la lista de formatos del sistema sea alcanzable con una
    sesión de usuario.
    """
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)

    response = await client.post(
        "/api/v1/storage/internal/artefacto",
        files={"file": ("indice-firmado.xml", b"<indice/>", "application/xml")},
        headers=TENANT_HEADERS,  # sin X-Internal-Token
    )

    assert response.status_code in (401, 403)
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_artefacto_del_sistema_no_es_puerta_franca(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Tiene su propia lista, no «todo vale»: un ejecutable sigue siendo 415."""
    client, mock_conn, mock_minio = client_with_conn

    response = await client.post(
        "/api/v1/storage/internal/artefacto",
        files={"file": ("malware.exe", b"MZ...", "application/x-msdownload")},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 415
    mock_minio.put_object.assert_not_awaited()


# ---------------------------------------------------------------------------
# DELETE /api/v1/storage/files/{file_id} — anexo citado por una constancia (C-5/C14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_de_anexo_citado_es_409_nombra_la_constancia_y_no_borra(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Un anexo que sostiene una constancia de firma inobtenible VIGENTE no se da de baja: 409
    `anexo_citado_por_constancia` con la(s) constancia(s), asiento del rechazo, y el UPDATE de
    soft-delete NUNCA se emite.

    Mutación que debe caer: quitar la consulta a la vista (o su `if citas`) -> 204 y el UPDATE
    aparece en los `execute`."""
    client, mock_conn, _ = client_with_conn
    excepcion, transferencia = uuid.uuid4(), uuid.uuid4()
    vista_de_citas(
        mock_conn,
        citas=(
            {
                "excepcion_id": excepcion,
                "transferencia_id": transferencia,
                "rol": "recibe",
                "ordinal": 1,
            },
        ),
    )
    mock_conn.fetchrow.return_value = make_file_record(file_id=FILE_ID)
    mock_conn.execute.side_effect = _execute_con_update_1

    response = await client.delete(f"/api/v1/storage/files/{FILE_ID}", headers=INTERNAL_HEADERS)

    assert response.status_code == 409
    detalle = response.json()["detail"]
    assert detalle["error"] == "anexo_citado_por_constancia"
    assert detalle["constancias"] == [
        {
            "excepcion_id": str(excepcion),
            "transferencia_id": str(transferencia),
            "rol": "recibe",
            "ordinal": 1,
        }
    ]
    sqls = [c.args[0] for c in mock_conn.execute.call_args_list if c.args]
    assert not any(q.lstrip().upper().startswith("UPDATE FILES SET DELETED_AT") for q in sqls)
    acciones = [c.args[5] for c in mock_conn.execute.call_args_list if c.args and "INSERT INTO public.audit_log" in c.args[0]]
    assert acciones == ["file.purga_bloqueada_constancia"]


@pytest.mark.asyncio
async def test_delete_con_la_vista_ausente_es_409_fail_closed(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Control del fail-closed: sin `v_anexo_citado_vigente` no se puede afirmar que el anexo no
    esté citado, y «no sé» no es «nadie». Mutación: tratar `to_regclass` NULL como vacío -> 204."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = make_file_record(file_id=FILE_ID)
    mock_conn.execute.side_effect = _execute_con_update_1  # fetchval -> None (vista ausente)

    response = await client.delete(f"/api/v1/storage/files/{FILE_ID}", headers=INTERNAL_HEADERS)

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "anexo_citado_por_constancia"


@pytest.mark.asyncio
async def test_delete_de_un_archivo_ya_dado_de_baja_sigue_siendo_404(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """`lock_any_state` ve también los dados de baja (a diferencia de `get_by_id`): el servicio
    tiene que seguir contestando 404, no volver a darlo de baja ni consultar la vista."""
    from datetime import UTC, datetime

    client, mock_conn, _ = client_with_conn
    vista_de_citas(mock_conn)
    mock_conn.fetchrow.return_value = make_file_record(
        file_id=FILE_ID, deleted_at=datetime.now(tz=UTC)
    )
    response = await client.delete(f"/api/v1/storage/files/{FILE_ID}", headers=INTERNAL_HEADERS)
    assert response.status_code == 404
