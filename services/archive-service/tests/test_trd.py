# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del modelo TRD/CCD: subseries y cálculo de retención (E04, ADR-015)."""

import json
import uuid

import pytest

from tests.conftest import TENANT_HEADERS, make_trd_serie_record


@pytest.mark.asyncio
async def test_create_subserie_with_two_phase_retention(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    parent = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_trd_serie_record(
        code="100.1",
        name="Contratos de obra",
        disposition="CT",
        parent_id=parent,
        archivo_gestion_years=3,
        archivo_central_years=7,
    )

    resp = await client.post(
        "/api/v1/trd",
        json={
            "code": "100.1",
            "name": "Contratos de obra",
            "disposition": "CT",
            "parent_id": str(parent),
            "archivo_gestion_years": 3,
            "archivo_central_years": 7,
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["parent_id"] == str(parent)
    assert body["archivo_gestion_years"] == 3
    assert body["disposition"] == "CT"


@pytest.mark.asyncio
async def test_compute_retention_schedule(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    # serie: 3 años gestión + 7 central, disposición conserve→CT.
    mock_conn.fetchrow.return_value = make_trd_serie_record(
        code="100",
        disposition="conserve",
        archivo_gestion_years=3,
        archivo_central_years=7,
    )
    serie_id = make_trd_serie_record()["id"]

    resp = await client.get(
        f"/api/v1/trd/{serie_id}/retention?closed_at=2024-01-15", headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fin_archivo_gestion"] == "2027-01-15"  # +3
    assert body["fin_archivo_central"] == "2034-01-15"  # +3+7
    assert body["disposition_code"] == "CT"
    assert body["disposition_label"] == "Conservación Total"


@pytest.mark.asyncio
async def test_compute_retention_serie_not_found(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None

    resp = await client.get(
        f"/api/v1/trd/{uuid.uuid4()}/retention?closed_at=2024-01-15", headers=TENANT_HEADERS
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# RBAC gate: USUA_PERM_TRD en las mutaciones de trd.py (create/update series).
# Las lecturas (GET /trd, /{id}, /{id}/retention) las consume el dropdown de
# radicación y quedan abiertas — sin cambio, solo _require_tenant.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_trd_serie_forbidden_without_permission(client_with_conn) -> None:
    """Mutación representativa: sin USUA_PERM_TRD, `require_permission` corta
    con 403 antes de tocar la lógica de negocio (no se inserta la serie)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # permiso denegado

    resp = await client.post(
        "/api/v1/trd",
        json={"code": "100.1", "name": "Contratos de obra", "disposition": "CT"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_create_trd_serie_forbidden_without_user_id(client_with_conn) -> None:
    """Fail-closed sobre la IDENTIDAD: sin X-User-Id no hay usuario que
    resolver → 403, aunque la BD "tuviera" el permiso."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # aunque "hubiera" permiso, sin identidad no aplica

    resp = await client.post(
        "/api/v1/trd",
        json={"code": "100.1", "name": "Contratos de obra", "disposition": "CT"},
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_update_trd_serie_forbidden_without_permission(client_with_conn) -> None:
    """update_trd_serie (PATCH) también está gateado: sin USUA_PERM_TRD,
    `require_permission` corta con 403 antes de tocar la lógica de negocio."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # permiso denegado

    resp = await client.patch(
        f"/api/v1/trd/{uuid.uuid4()}",
        json={"name": "Contratos renombrados"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_update_trd_serie_forbidden_without_user_id(client_with_conn) -> None:
    """Fail-closed sobre la IDENTIDAD: sin X-User-Id no hay usuario que
    resolver → 403, aunque la BD "tuviera" el permiso."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # aunque "hubiera" permiso, sin identidad no aplica

    resp = await client.patch(
        f"/api/v1/trd/{uuid.uuid4()}",
        json={"name": "Contratos renombrados"},
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_create_trd_serie_writes_audit_entry(client_with_conn) -> None:
    """Media (RBAC follow-up): crear una serie TRD deja traza en `audit_log`
    con el actor (X-User-Id) y el código de la serie, igual que el alta de un
    expediente (Ley 594/2000 art. 24 — la TRD gobierna retención/disposición)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    mock_conn.fetchrow.return_value = make_trd_serie_record(code="100.1")

    resp = await client.post(
        "/api/v1/trd",
        json={"code": "100.1", "name": "Contratos de obra", "disposition": "CT"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201

    audit_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    # args: (sql, tenant, service, actor, canal, action, object_type, object_ref, payload, prev_hash, hash)
    assert len(audit_calls) == 1
    assert audit_calls[0][3] == TENANT_HEADERS["X-User-Id"]
    assert audit_calls[0][5] == "archive.trd_serie_created"
    assert audit_calls[0][6] == "trd_serie"
    assert audit_calls[0][7] == "100.1"


@pytest.mark.asyncio
async def test_update_trd_serie_writes_audit_entry(client_with_conn) -> None:
    """Media (RBAC follow-up): actualizar una serie TRD deja traza en
    `audit_log`, incluyendo los campos modificados en el payload."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    serie_id = uuid.uuid4()
    # ADR-025 (Increment 1): PATCH con cambio sustantivo (`name`) solo es
    # legítimo sobre `borrador` -- ruptura buscada del nuevo DEFAULT (ver
    # ADR-025 §"Impacto en lo existente", punto 9).
    existing = make_trd_serie_record(serie_id=serie_id, code="100", estado="borrador")
    updated = make_trd_serie_record(
        serie_id=serie_id, code="100", name="Contratos renombrados", estado="borrador"
    )
    mock_conn.fetchrow.side_effect = [existing, updated]

    resp = await client.patch(
        f"/api/v1/trd/{serie_id}",
        json={"name": "Contratos renombrados"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200

    audit_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0][3] == TENANT_HEADERS["X-User-Id"]
    assert audit_calls[0][5] == "archive.trd_serie_updated"
    assert audit_calls[0][6] == "trd_serie"
    assert audit_calls[0][7] == "100"
    # El asiento guarda un diff con el valor ANTERIOR, no solo el nuevo: sin el
    # `before`, un auditor que vea "retention_years: 2" no puede saber que antes
    # eran 20, y tampoco reconstruirlo encadenando asientos (el de `created` no
    # guardaba el estado inicial). Ese era el hallazgo Alto de conformidad.
    assert json.loads(audit_calls[0][8])["changes"] == {
        "name": {"before": "Contratos", "after": "Contratos renombrados"}
    }


@pytest.mark.asyncio
async def test_list_trd_series_open_read_no_user_id_needed(client_with_conn) -> None:
    """Confirma que la lectura sigue ABIERTA (solo `_require_tenant`): sin
    X-User-Id ni permiso mockeado, GET /trd sigue respondiendo 200."""
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = 0

    resp = await client.get(
        "/api/v1/trd", headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]}
    )

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# ADR-025 (Increment 1): PATCH restringido a `borrador`; `POST /versiones`;
# gate de cierre (`convalidada`/`migrada`). Ver docs/es/adr/ADR-025-*.md D1/D5.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_trd_serie_non_borrador_substantive_change_rejected(
    client_with_conn,
) -> None:
    """D1: una modificación SUSTANTIVA (`retention_years`) sobre una versión
    que no está en `borrador` es rechazada -- es el `UPDATE` retroactivo que
    causó el hallazgo Crítico. `migrada` (backfill) es el estado por defecto
    de `make_trd_serie_record`, así que esto también cubre el caso más común
    en producción: la mayoría de las series preexistentes."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted
    serie_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_trd_serie_record(
        serie_id=serie_id, code="100", estado="migrada"
    )

    resp = await client.patch(
        f"/api/v1/trd/{serie_id}",
        json={"retention_years": 99},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "trd_version_inmutable"
    # El gate cortó ANTES del UPDATE: solo una llamada a fetchrow (la lectura
    # de `existing`), nunca una segunda para el `RETURNING *` del UPDATE.
    assert mock_conn.fetchrow.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["aprobada", "convalidada", "derogada"])
async def test_patch_trd_serie_rejected_on_every_non_borrador_estado(
    client_with_conn, estado: str
) -> None:
    """D5/tabla de estados: `aprobada`/`convalidada`/`derogada` tampoco
    admiten edición en sitio de contenido sustantivo -- solo `borrador` lo
    hace. `migrada` se cubre aparte (es el default de la fixture)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    serie_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_trd_serie_record(
        serie_id=serie_id, code="100", estado=estado
    )

    resp = await client.patch(
        f"/api/v1/trd/{serie_id}",
        json={"name": "Contratos renombrados"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "trd_version_inmutable"


@pytest.mark.asyncio
async def test_patch_trd_serie_borrador_substantive_change_edits_in_place(
    client_with_conn,
) -> None:
    """Contraparte del rechazo: sobre `borrador`, la MISMA modificación
    sustantiva sí se aplica -- edición en sitio (`UPDATE`), no versión nueva.
    `borrador` es el único estado editable (D1)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    serie_id = uuid.uuid4()
    existing = make_trd_serie_record(
        serie_id=serie_id, code="100", retention_years=5, estado="borrador"
    )
    updated = make_trd_serie_record(
        serie_id=serie_id, code="100", retention_years=99, estado="borrador"
    )
    mock_conn.fetchrow.side_effect = [existing, updated]

    resp = await client.patch(
        f"/api/v1/trd/{serie_id}",
        json={"retention_years": 99},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["retention_years"] == 99
    assert resp.json()["estado"] == "borrador"
    # Dos fetchrow: lectura de `existing` + `RETURNING *` del UPDATE real --
    # confirma que SÍ llegó a escribir (a diferencia del caso rechazado).
    assert mock_conn.fetchrow.call_count == 2
    update_call = next(
        c for c in mock_conn.fetchrow.call_args_list if "UPDATE trd_series" in c.args[0]
    )
    assert "retention_years" in update_call.args[0]


@pytest.mark.asyncio
async def test_patch_trd_serie_pdfa_profile_only_bypasses_gate_on_convalidada(
    client_with_conn,
) -> None:
    """Excepción explícita D1: `pdfa_profile` es editable en sitio en
    CUALQUIER estado -- incluida `convalidada`, donde cualquier otro campo
    sería rechazado. No es contenido convalidado de la TRD."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    serie_id = uuid.uuid4()
    existing = make_trd_serie_record(
        serie_id=serie_id,
        code="100",
        estado="convalidada",
        acto_administrativo="Resolución 001",
        fecha_convalidacion="2026-01-01",
        instancia_convalidante="Consejo Departamental",
        pdfa_profile=None,
    )
    updated = make_trd_serie_record(
        serie_id=serie_id,
        code="100",
        estado="convalidada",
        acto_administrativo="Resolución 001",
        fecha_convalidacion="2026-01-01",
        instancia_convalidante="Consejo Departamental",
        pdfa_profile="2u",
    )
    mock_conn.fetchrow.side_effect = [existing, updated]

    resp = await client.patch(
        f"/api/v1/trd/{serie_id}",
        json={"pdfa_profile": "2u"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["pdfa_profile"] == "2u"
    assert resp.json()["estado"] == "convalidada"


@pytest.mark.asyncio
async def test_create_trd_serie_version_clones_vigente_as_new_borrador(
    client_with_conn,
) -> None:
    """`POST /trd/{code}/versiones` (D1, nuevo): clona la vigente como fila
    NUEVA `version+1` en `borrador` -- nunca un `UPDATE` de la vigente."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    vigente = make_trd_serie_record(code="100", version=1, estado="migrada")
    nueva = make_trd_serie_record(
        code="100",
        version=2,
        estado="borrador",
        retention_years=8,
        supersede_a=vigente["id"],
    )
    mock_conn.fetchrow.side_effect = [vigente, nueva]

    resp = await client.post(
        "/api/v1/trd/100/versiones",
        json={"retention_years": 8},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["version"] == 2
    assert body["estado"] == "borrador"
    assert body["retention_years"] == 8
    assert body["supersede_a"] == str(vigente["id"])
    insert_call = next(
        c for c in mock_conn.fetchrow.call_args_list if "INSERT INTO trd_series" in c.args[0]
    )
    assert insert_call.args[0].count("$") >= 11  # incluye version/supersede_a/created_by


@pytest.mark.asyncio
async def test_create_trd_serie_version_no_vigente_returns_404(client_with_conn) -> None:
    """Sin versión vigente (convalidada/migrada) para el código, no hay nada
    que clonar."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = None

    resp = await client.post(
        "/api/v1/trd/999/versiones",
        json={},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error"] == "trd_code_not_found"


@pytest.mark.asyncio
async def test_create_trd_serie_version_conflict_when_already_en_tramite(
    client_with_conn,
) -> None:
    """`ux_trd_series_en_tramite` (037) impide dos versiones en trámite
    simultáneas para el mismo código -- el service traduce la
    `UniqueViolationError` cruda de Postgres a un 409 legible."""
    import asyncpg

    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    vigente = make_trd_serie_record(code="100", version=1, estado="migrada")

    # 1ª llamada a fetchrow: `get_trd_serie_vigente_by_code` (SELECT). 2ª:
    # el `INSERT ... RETURNING *` de la versión nueva, que revienta con la
    # violación de índice único -- simula que YA existe una versión
    # `borrador`/`aprobada` en trámite para el mismo código.
    calls = [vigente]

    async def _fetchrow_side_effect(query, *params, **kwargs):
        if calls and query.lstrip().startswith("SELECT * FROM trd_series"):
            return calls.pop(0)
        raise asyncpg.UniqueViolationError("duplicate key value violates unique constraint")

    mock_conn.fetchrow.side_effect = _fetchrow_side_effect

    resp = await client.post(
        "/api/v1/trd/100/versiones",
        json={"retention_years": 8},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "trd_version_en_tramite"


@pytest.mark.asyncio
async def test_create_trd_serie_version_forbidden_without_permission(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # permiso denegado

    resp = await client.post(
        "/api/v1/trd/100/versiones",
        json={},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()
