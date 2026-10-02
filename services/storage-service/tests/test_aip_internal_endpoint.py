# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de `POST /api/v1/preservacion/aip-internal` (E10 debt, cableado del
AIP al cierre del expediente) — gemelo INTERNO (D-02, `X-Internal-Token`) de
`POST /aip`, único llamador legítimo: la 6ª fase de reconciliación de
archive-service (`run_once_aip` -> `AipWiringService.intentar_empaquetar` ->
`storage_client.empaquetar_aip_internal`).

El servicio subyacente (`AipService.empaquetar_aip`: BagIt real + PREMIS v3,
fixity, subida WORM) ya está exhaustivamente probado en `test_aip_real.py`
vía la ruta PÚBLICA `POST /aip` — este archivo NO repite esa cobertura.
Aquí solo se prueba lo que es DISTINTO en el gemelo interno: (a) el gate
`X-Internal-Token` en vez de `require_permission(min_crud=3)`, (b) que el
`actor` SIEMPRE es `SYSTEM_RECONCILER_ID`, NUNCA derivado de headers del
llamador (`X-User-Id`/`X-Username` no viajan en esta ruta), y (c) que
reutiliza el MISMO mapeo de errores (`_map_empaquetar_aip_error`) que la
ruta pública — verificado aquí con un solo caso representativo (409 fixity),
no los siete branches completos (ya cubiertos indirectamente vía la ruta
pública en otros archivos)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.core import config as cfg_module
from app.core.constants import SYSTEM_RECONCILER_ID
from app.schemas.preservacion import AipResponse
from app.services.preservation_worm_service import FixityMismatchError, PreservationDisabledError
from tests.conftest import TENANT_HEADERS, TENANT_SLUG, TEST_INTERNAL_TOKEN

_PATH = "/api/v1/preservacion/aip-internal"


def _payload(**overrides) -> dict:
    payload = {
        "expediente_id": "EXP-1",
        "documentos": [
            {"document_id": "R-1", "nombre": "oficio.pdf", "file_id": str(uuid.uuid4())}
        ],
        "retencion_anios": 5,
        "fecha_inicio": "2026-01-01",
    }
    payload.update(overrides)
    return payload


def _fake_aip_response(aip_id: uuid.UUID | None = None) -> AipResponse:
    return AipResponse(
        id=aip_id or uuid.uuid4(),
        expediente_id="EXP-1",
        aip_sha256="c" * 64,
        indice_sha256=None,
        num_documentos=1,
        estado="empaquetado",
        created_at=datetime.now(tz=UTC),
        premis_xml="<premis/>",
        bucket=f"orpycamcp-{TENANT_SLUG}-preservation",
        object_key="aip/EXP-1/x.zip",
        aip_bytes=999,
        formato="application/zip",
        retain_until=datetime(2031, 1, 1, tzinfo=UTC),
        retention_mode="COMPLIANCE",
        legal_hold=False,
    )


# ---------------------------------------------------------------------------
# Gate D-02: X-Internal-Token (en vez de require_permission min_crud=3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_token_is_401(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, _mock_conn, _mock_minio = client_with_conn

    resp = await client.post(_PATH, json=_payload(), headers=TENANT_HEADERS)

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "missing_internal_token"


@pytest.mark.asyncio
async def test_with_wrong_token_is_403(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, _mock_conn, _mock_minio = client_with_conn

    resp = await client.post(
        _PATH, json=_payload(), headers={**TENANT_HEADERS, "X-Internal-Token": "wrong-token"}
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "invalid_internal_token"


@pytest.mark.asyncio
async def test_token_not_configured_is_401(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _mock_conn, _mock_minio = client_with_conn
    monkeypatch.setattr(cfg_module.settings, "internal_service_token", "")

    resp = await client.post(
        _PATH, json=_payload(), headers={**TENANT_HEADERS, "X-Internal-Token": TEST_INTERNAL_TOKEN}
    )

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "internal_access_not_configured"


@pytest.mark.asyncio
async def test_without_min_crud_permission_still_succeeds_with_token(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """A diferencia de `POST /aip` (RBAC `min_crud=3`), esta ruta NUNCA
    consulta `USUA_PERM_EXPEDIENTE` — solo el token interno gatea. Se
    confirma dejando `mock_conn.fetchval` (que resolvería el permiso RBAC en
    la ruta pública) en su default `0`/falsy: si el router llamara por error
    a `require_permission`, esto haría fallar con 403 en vez de éxito."""
    client, mock_conn, _mock_minio = client_with_conn
    mock_conn.fetchval.return_value = 0  # "sin permiso" si se consultara (no debería)

    with patch(
        "app.routers.preservacion.AipService.empaquetar_aip",
        new=AsyncMock(return_value=_fake_aip_response()),
    ):
        resp = await client.post(
            _PATH, json=_payload(), headers={**TENANT_HEADERS, "X-Internal-Token": TEST_INTERNAL_TOKEN}
        )

    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Identidad: SIEMPRE SYSTEM_RECONCILER_ID, NUNCA derivada de headers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_actor_is_always_system_reconciler_id_never_from_headers(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, _mock_conn, _mock_minio = client_with_conn

    with patch(
        "app.routers.preservacion.AipService.empaquetar_aip",
        new=AsyncMock(return_value=_fake_aip_response()),
    ) as mock_empaquetar:
        resp = await client.post(
            _PATH,
            json=_payload(),
            headers={
                **TENANT_HEADERS,
                "X-Internal-Token": TEST_INTERNAL_TOKEN,
                # Un llamador que intentara forjar su propia identidad en
                # X-Username/X-User-Id NO debe influir en el actor persistido.
                "X-Username": "forjado@externo",
            },
        )

    assert resp.status_code == 201
    mock_empaquetar.assert_awaited_once()
    assert mock_empaquetar.await_args.kwargs["actor"] == str(SYSTEM_RECONCILER_ID)


@pytest.mark.asyncio
async def test_happy_path_returns_201_with_aip_response(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, _mock_conn, _mock_minio = client_with_conn
    aip_id = uuid.uuid4()

    with patch(
        "app.routers.preservacion.AipService.empaquetar_aip",
        new=AsyncMock(return_value=_fake_aip_response(aip_id)),
    ):
        resp = await client.post(
            _PATH, json=_payload(), headers={**TENANT_HEADERS, "X-Internal-Token": TEST_INTERNAL_TOKEN}
        )

    assert resp.status_code == 201
    assert resp.json()["id"] == str(aip_id)


# ---------------------------------------------------------------------------
# Mapeo de errores COMPARTIDO con la ruta pública (`_map_empaquetar_aip_error`)
# — un caso representativo basta; el mapeo completo ya se prueba en
# test_aip_real.py vía POST /aip.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fixity_mismatch_maps_to_409(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, _mock_conn, _mock_minio = client_with_conn

    with patch(
        "app.routers.preservacion.AipService.empaquetar_aip",
        new=AsyncMock(side_effect=FixityMismatchError("un anexo mutó desde que se vinculó")),
    ):
        resp = await client.post(
            _PATH, json=_payload(), headers={**TENANT_HEADERS, "X-Internal-Token": TEST_INTERNAL_TOKEN}
        )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "fixity_mismatch"


@pytest.mark.asyncio
async def test_preservation_disabled_maps_to_503(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, _mock_conn, _mock_minio = client_with_conn

    with patch(
        "app.routers.preservacion.AipService.empaquetar_aip",
        new=AsyncMock(side_effect=PreservationDisabledError("PRESERVATION_ENABLED=false")),
    ):
        resp = await client.post(
            _PATH, json=_payload(), headers={**TENANT_HEADERS, "X-Internal-Token": TEST_INTERNAL_TOKEN}
        )

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "preservation_disabled"
