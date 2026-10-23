# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Perfil PDF/A configurable por serie/TRD (E10 debt "perfil PDF/A por
serie", follow-up de Increment C2b, RF-FIR-15): cobertura del CRUD de
`trd_series.pdfa_profile` en `TrdSerieCreate`/`TrdSerieUpdate`/
`TrdSerieResponse` (`app/schemas/archive.py`).

Cubre la defensa Pydantic (422 legible, whitelist `_PDFA_PROFILES` --
MISMA whitelist que `_VALID_FLAVOURS` de storage-service, duplicada a
propósito) ANTES de tocar la BD -- el CHECK de la migración 035 es la
última línea fail-closed, probado contra Postgres real en
`tests/integration/test_trd_serie_pdfa_profile.py`.

Todo mockeado (`client_with_conn`), mismo patrón que `test_trd.py`.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.schemas.archive import _PDFA_PROFILES, TrdSerieCreate, TrdSerieUpdate
from tests.conftest import TENANT_HEADERS, make_trd_serie_record

# ---------------------------------------------------------------------------
# Pydantic puro (sin HTTP): whitelist completa, ambos schemas.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", sorted(_PDFA_PROFILES))
def test_trd_serie_create_accepts_every_valid_pdfa_profile(profile: str) -> None:
    payload = TrdSerieCreate(
        code="X1", name="Serie itest", archivo_gestion_years=2, archivo_central_years=8, pdfa_profile=profile
    )
    assert payload.pdfa_profile == profile


def test_trd_serie_create_accepts_null_pdfa_profile() -> None:
    payload = TrdSerieCreate(
        code="X1", name="Serie itest", archivo_gestion_years=2, archivo_central_years=8
    )
    assert payload.pdfa_profile is None


@pytest.mark.parametrize("bad_profile", ["9z", "2B", "", "1a ", "2b; rm -rf /", "1"])
def test_trd_serie_create_rejects_invalid_pdfa_profile(bad_profile: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        TrdSerieCreate(
            code="X1", name="Serie itest", archivo_gestion_years=2, archivo_central_years=8, pdfa_profile=bad_profile
        )
    assert "pdfa_profile" in str(exc_info.value)


@pytest.mark.parametrize("profile", sorted(_PDFA_PROFILES))
def test_trd_serie_update_accepts_every_valid_pdfa_profile(profile: str) -> None:
    payload = TrdSerieUpdate(pdfa_profile=profile)
    assert payload.pdfa_profile == profile


@pytest.mark.parametrize("bad_profile", ["9z", "2B", "", "not-a-profile"])
def test_trd_serie_update_rejects_invalid_pdfa_profile(bad_profile: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        TrdSerieUpdate(pdfa_profile=bad_profile)
    assert "pdfa_profile" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Endpoint POST /api/v1/trd — 201 feliz + 422 antes de tocar la BD.
# ---------------------------------------------------------------------------


def _grant_permission(mock_conn) -> None:
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)


@pytest.mark.asyncio
async def test_create_trd_serie_with_valid_pdfa_profile_is_persisted_and_echoed(
    client_with_conn,
) -> None:
    client, mock_conn = client_with_conn
    _grant_permission(mock_conn)
    mock_conn.fetchrow.return_value = make_trd_serie_record(
        code="200", name="Serie con perfil 2u", pdfa_profile="2u"
    )

    resp = await client.post(
        "/api/v1/trd",
        json={
            "code": "200",
            "name": "Serie con perfil 2u",
            "archivo_gestion_years": 2,
            "archivo_central_years": 8,
            "pdfa_profile": "2u",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["pdfa_profile"] == "2u"

    # El repositorio recibió el perfil tal cual (no se pierde ni se muta
    # camino a la capa de datos).
    insert_call = next(
        c for c in mock_conn.fetchrow.call_args_list if "INSERT INTO trd_series" in c.args[0]
    )
    assert insert_call.args[-1] == "2u"  # último parámetro posicional: pdfa_profile


@pytest.mark.asyncio
async def test_create_trd_serie_without_pdfa_profile_defaults_to_null(
    client_with_conn,
) -> None:
    client, mock_conn = client_with_conn
    _grant_permission(mock_conn)
    mock_conn.fetchrow.return_value = make_trd_serie_record(
        code="201", name="Serie sin perfil propio", pdfa_profile=None
    )

    resp = await client.post(
        "/api/v1/trd",
        json={
            "code": "201",
            "name": "Serie sin perfil propio",
            "archivo_gestion_years": 2,
            "archivo_central_years": 8,
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["pdfa_profile"] is None
    insert_call = next(
        c for c in mock_conn.fetchrow.call_args_list if "INSERT INTO trd_series" in c.args[0]
    )
    assert insert_call.args[-1] is None


@pytest.mark.asyncio
async def test_create_trd_serie_invalid_pdfa_profile_returns_422_before_db(
    client_with_conn,
) -> None:
    client, mock_conn = client_with_conn
    _grant_permission(mock_conn)

    resp = await client.post(
        "/api/v1/trd",
        json={"code": "202", "name": "Serie perfil inválido", "pdfa_profile": "9z"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert any("pdfa_profile" in str(err.get("loc", "")) for err in body["detail"])
    # Rechazado en la capa Pydantic -- nunca llega a construir el INSERT.
    assert not any(
        c.args and "INSERT INTO trd_series" in c.args[0]
        for c in mock_conn.fetchrow.call_args_list
    )


# ---------------------------------------------------------------------------
# Endpoint PATCH /api/v1/trd/{id} — mismo contrato de validación.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_trd_serie_with_valid_pdfa_profile(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    _grant_permission(mock_conn)
    serie_id = uuid.uuid4()
    existing = make_trd_serie_record(serie_id=serie_id, code="300", pdfa_profile=None)
    updated = make_trd_serie_record(serie_id=serie_id, code="300", pdfa_profile="3a")
    mock_conn.fetchrow.side_effect = [existing, updated]

    resp = await client.patch(
        f"/api/v1/trd/{serie_id}",
        json={"pdfa_profile": "3a"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["pdfa_profile"] == "3a"


@pytest.mark.asyncio
async def test_update_trd_serie_invalid_pdfa_profile_returns_422_before_db(
    client_with_conn,
) -> None:
    client, mock_conn = client_with_conn
    _grant_permission(mock_conn)
    serie_id = uuid.uuid4()

    resp = await client.patch(
        f"/api/v1/trd/{serie_id}",
        json={"pdfa_profile": "not-valid"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert any("pdfa_profile" in str(err.get("loc", "")) for err in body["detail"])
    # Rechazado en la capa Pydantic -- ni siquiera se leyó la serie existente.
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_update_trd_serie_omitting_pdfa_profile_does_not_clear_it(
    client_with_conn,
) -> None:
    """PATCH sin `pdfa_profile` en el body (campo por default `None` en
    `TrdSerieUpdate`) es indistinguible, a nivel de schema, de un PATCH que
    quisiera BORRAR el perfil -- mismo comportamiento "None = no tocar" que
    el resto de campos de `update_trd_serie` (ver
    `ArchiveRepository.update_trd_serie`, `if pdfa_profile is not None`).
    Confirma que una serie CON perfil propio no lo pierde al actualizar solo
    `name`."""
    client, mock_conn = client_with_conn
    _grant_permission(mock_conn)
    serie_id = uuid.uuid4()
    # ADR-025 (Increment 1): `name` es un cambio SUSTANTIVO -- solo legítimo
    # sobre `borrador` (ruptura buscada, no relacionada con lo que este test
    # aísla: la persistencia de `pdfa_profile`).
    existing = make_trd_serie_record(
        serie_id=serie_id, code="301", pdfa_profile="1b", estado="borrador"
    )
    # El repositorio real, sin `pdfa_profile` en `assignments`, devolvería la
    # fila con el perfil INTACTO -- se simula aquí devolviendo el mismo valor.
    updated = make_trd_serie_record(
        serie_id=serie_id, code="301", name="renombrada", pdfa_profile="1b", estado="borrador"
    )
    mock_conn.fetchrow.side_effect = [existing, updated]

    resp = await client.patch(
        f"/api/v1/trd/{serie_id}",
        json={"name": "renombrada"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["pdfa_profile"] == "1b"


# ---------------------------------------------------------------------------
# GET /api/v1/trd/{id} — round-trip del perfil en la respuesta.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_trd_serie_echoes_pdfa_profile(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    serie_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_trd_serie_record(serie_id=serie_id, pdfa_profile="2a")

    resp = await client.get(f"/api/v1/trd/{serie_id}", headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    assert resp.json()["pdfa_profile"] == "2a"
