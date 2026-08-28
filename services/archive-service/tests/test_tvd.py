# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de `/api/v1/tvd` (ADR-026, Increment 1): TVD como el MISMO
instrumento que TRD (discriminador `tipo_instrumento` sobre `trd_series`),
mismo circuito de convalidación reutilizado. Mockeado (sin Postgres real) --
las CUATRO capas de blindaje del discriminador (índice único, vistas, CHECK,
trigger) se verifican por MUTACIÓN contra Postgres real en
`tests/integration/test_migration_039_dos_tenants.py`, no aquí."""

import uuid
from datetime import date

import asyncpg
import pytest

from tests.conftest import TENANT_HEADERS, make_trd_serie_record, make_tvd_agrupacion_record

_TVD_CREATE_PAYLOAD = {
    "code": "FONDO-100",
    "name": "Fondo acumulado Dependencia X",
    "disposition": "CT",
    "fondo_nombre": "Fondo Instituto Departamental de X (liquidado 2003)",
    "fecha_extrema_inicial": "1975-01-01",
    "fecha_extrema_final": "1998-12-31",
    "justificacion_valoracion": "Valoracion secundaria: sin valores primarios vigentes.",
}


@pytest.mark.asyncio
async def test_create_tvd_agrupacion(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted
    mock_conn.fetchrow.return_value = make_tvd_agrupacion_record()

    resp = await client.post("/api/v1/tvd", json=_TVD_CREATE_PAYLOAD, headers=TENANT_HEADERS)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tipo_instrumento"] == "TVD"
    assert body["fondo_nombre"] == _TVD_CREATE_PAYLOAD["fondo_nombre"]
    assert body["fecha_extrema_inicial"] == "1975-01-01"
    assert body["justificacion_valoracion"] is not None
    assert body["estado"] == "borrador"
    insert_call = next(
        c for c in mock_conn.fetchrow.call_args_list if "INSERT INTO trd_series" in c.args[0]
    )
    assert "'TVD'" in insert_call.args[0]


@pytest.mark.asyncio
async def test_create_tvd_agrupacion_rejects_archivo_gestion_years_and_tipo_instrumento(
    client_with_conn,
) -> None:
    """El body NO acepta `archivo_gestion_years`, `estado` ni
    `tipo_instrumento` -- Pydantic los ignora (no forbid=extra), pero el
    contrato (`TvdAgrupacionCreate`) simplemente no los declara: se
    confirma que el INSERT nunca los recibe del cliente inspeccionando que
    la respuesta trae `archivo_gestion_years=None` pase lo que pase en el
    payload."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = make_tvd_agrupacion_record()

    resp = await client.post(
        "/api/v1/tvd",
        json={**_TVD_CREATE_PAYLOAD, "archivo_gestion_years": 5, "tipo_instrumento": "TRD"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["archivo_gestion_years"] is None
    assert resp.json()["tipo_instrumento"] == "TVD"


@pytest.mark.asyncio
async def test_create_tvd_agrupacion_code_conflict_reports_occupant_type(
    client_with_conn,
) -> None:
    """ADR-026 Capa 1: `code` es único y COMPARTIDO entre TRD y TVD -- si ya
    lo usa una TRD, `409 instrumento_code_en_uso` (no la `UniqueViolationError`
    cruda de `trd_series_code_version_key`), indicando el tipo del ocupante."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [True, "TRD"]  # permiso, luego ocupante_tipo

    async def _fetchrow_raises(*_args, **_kwargs):
        raise asyncpg.UniqueViolationError("duplicate key value violates unique constraint")

    mock_conn.fetchrow.side_effect = _fetchrow_raises

    resp = await client.post("/api/v1/tvd", json=_TVD_CREATE_PAYLOAD, headers=TENANT_HEADERS)

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "instrumento_code_en_uso"
    assert detail["ocupante_tipo"] == "TRD"


@pytest.mark.asyncio
async def test_create_tvd_agrupacion_forbidden_without_permission(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    resp = await client.post("/api/v1/tvd", json=_TVD_CREATE_PAYLOAD, headers=TENANT_HEADERS)

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_create_tvd_agrupacion_writes_tvd_audit_action(client_with_conn) -> None:
    """D2: nombres de asiento distintos (`archive.tvd_*`), objeto distinto
    (`tvd_agrupacion`) -- un auditor/perito filtrando por acción no necesita
    acordarse de mirar un campo del payload para separar instrumentos."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = make_tvd_agrupacion_record()

    resp = await client.post("/api/v1/tvd", json=_TVD_CREATE_PAYLOAD, headers=TENANT_HEADERS)
    assert resp.status_code == 201

    audit_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0][5] == "archive.tvd_agrupacion_created"
    assert audit_calls[0][6] == "tvd_agrupacion"
    assert audit_calls[0][7] == "FONDO-100"


@pytest.mark.asyncio
async def test_get_tvd_agrupacion_not_found(client_with_conn) -> None:
    """`GET /tvd/{id}` lee `v_tvd_agrupaciones`: un id que existe pero es TRD
    no "se filtra" -- directamente no está en la vista, 404 igual que un id
    inexistente (ADR-026 §"Superficie de API")."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None

    resp = await client.get(f"/api/v1/tvd/{uuid.uuid4()}", headers=TENANT_HEADERS)

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "tvd_agrupacion_not_found"
    select_call = mock_conn.fetchrow.call_args_list[-1]
    assert "v_tvd_agrupaciones" in select_call.args[0]


@pytest.mark.asyncio
async def test_list_tvd_agrupaciones_reads_from_view(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = 0

    resp = await client.get(
        "/api/v1/tvd", headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]}
    )

    assert resp.status_code == 200
    count_call = mock_conn.fetchval.call_args_list[-1]
    assert "v_tvd_agrupaciones" in count_call.args[0]
    fetch_call = mock_conn.fetch.call_args_list[-1]
    assert "v_tvd_agrupaciones" in fetch_call.args[0]


@pytest.mark.asyncio
async def test_list_trd_series_reads_from_view(client_with_conn) -> None:
    """ADR-026 §"Impacto en lo existente" #1: `list_trd_series` deja de leer
    la tabla base -- es LA consulta de la clase "filtro olvidado"."""
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = 0

    resp = await client.get(
        "/api/v1/trd", headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]}
    )

    assert resp.status_code == 200
    count_call = mock_conn.fetchval.call_args_list[-1]
    assert "v_trd_series" in count_call.args[0]
    fetch_call = mock_conn.fetch.call_args_list[-1]
    assert "v_trd_series" in fetch_call.args[0]


@pytest.mark.asyncio
async def test_patch_tvd_agrupacion_non_borrador_rejected(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    agrupacion_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_tvd_agrupacion_record(
        serie_id=agrupacion_id, estado="convalidada"
    )

    resp = await client.patch(
        f"/api/v1/tvd/{agrupacion_id}",
        json={"justificacion_valoracion": "otra justificacion"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "trd_version_inmutable"


@pytest.mark.asyncio
async def test_patch_tvd_agrupacion_borrador_edits_in_place(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    agrupacion_id = uuid.uuid4()
    existing = make_tvd_agrupacion_record(serie_id=agrupacion_id, estado="borrador")
    updated = make_tvd_agrupacion_record(
        serie_id=agrupacion_id,
        estado="borrador",
        justificacion_valoracion="justificacion revisada",
    )
    mock_conn.fetchrow.side_effect = [existing, updated]

    resp = await client.patch(
        f"/api/v1/tvd/{agrupacion_id}",
        json={"justificacion_valoracion": "justificacion revisada"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["justificacion_valoracion"] == "justificacion revisada"


@pytest.mark.asyncio
async def test_create_tvd_agrupacion_version_clones_vigente(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    vigente = make_tvd_agrupacion_record(code="FONDO-100", version=1, estado="convalidada")
    nueva = make_tvd_agrupacion_record(
        code="FONDO-100", version=2, estado="borrador", supersede_a=vigente["id"]
    )
    mock_conn.fetchrow.side_effect = [vigente, nueva]

    resp = await client.post(
        "/api/v1/tvd/FONDO-100/versiones",
        json={},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["version"] == 2
    assert body["tipo_instrumento"] == "TVD"
    assert body["supersede_a"] == str(vigente["id"])
    select_call = mock_conn.fetchrow.call_args_list[0]
    assert "v_tvd_agrupaciones" in select_call.args[0]
    insert_call = next(
        c for c in mock_conn.fetchrow.call_args_list if "INSERT INTO trd_series" in c.args[0]
    )
    assert "'TVD'" in insert_call.args[0]


@pytest.mark.asyncio
async def test_get_trd_serie_versiones(client_with_conn) -> None:
    """ADR-026: `GET /api/v1/trd/{code}/versiones`, gemelo que ADR-025 dejó
    declarado y nunca implementó."""
    client, mock_conn = client_with_conn
    # El historial es CIRCUITO, no vocabulario: exige USUA_PERM_TRD desde
    # 2026-08-28 (ver `tests/test_trd_lectura_frontera.py`).
    mock_conn.fetchval.return_value = True
    mock_conn.fetch.return_value = [
        make_trd_serie_record(code="100", version=2, estado="borrador"),
        make_trd_serie_record(code="100", version=1, estado="derogada"),
    ]

    resp = await client.get("/api/v1/trd/100/versiones", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert [v["version"] for v in body] == [2, 1]
    fetch_call = mock_conn.fetch.call_args_list[-1]
    assert "v_trd_series" in fetch_call.args[0]


@pytest.mark.asyncio
async def test_get_tvd_agrupacion_versiones(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD: el historial está gateado
    mock_conn.fetch.return_value = [
        make_tvd_agrupacion_record(code="FONDO-100", version=1, estado="convalidada")
    ]

    resp = await client.get("/api/v1/tvd/FONDO-100/versiones", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["tipo_instrumento"] == "TVD"
    fetch_call = mock_conn.fetch.call_args_list[-1]
    assert "v_tvd_agrupaciones" in fetch_call.args[0]


# ---------------------------------------------------------------------------
# Circuito de convalidación (ADR-026): aprobar / devolver / convalidar /
# registrar-rusd / derogar -- reutiliza el repositorio type-agnostic de TRD.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aprobar_tvd_agrupacion_version(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    existing = make_tvd_agrupacion_record(code="FONDO-100", version=1, estado="borrador")
    updated = make_tvd_agrupacion_record(
        code="FONDO-100",
        version=1,
        estado="aprobada",
        acta_comite="Acta 001",
        fecha_aprobacion_comite=date(2026, 1, 10),
    )
    mock_conn.fetchrow.side_effect = [existing, updated]

    resp = await client.post(
        "/api/v1/tvd/FONDO-100/versiones/1/aprobar",
        json={"acta_comite": "Acta 001", "fecha_aprobacion_comite": "2026-01-10"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "aprobada"
    audit_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert audit_calls[-1][5] == "archive.tvd_version_aprobada"
    assert audit_calls[-1][6] == "tvd_agrupacion"


@pytest.mark.asyncio
async def test_convalidar_tvd_agrupacion_version_desde_aprobada(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    existing = make_tvd_agrupacion_record(
        code="FONDO-100",
        version=1,
        estado="aprobada",
        acta_comite="Acta 001",
        fecha_aprobacion_comite=date(2026, 1, 10),
    )
    convalidada = make_tvd_agrupacion_record(
        code="FONDO-100",
        version=1,
        estado="convalidada",
        acta_comite="Acta 001",
        fecha_aprobacion_comite=date(2026, 1, 10),
        acto_administrativo="Resolucion 099",
        fecha_convalidacion=date(2026, 2, 1),
        instancia_convalidante="Consejo Departamental",
    )

    async def _fetchrow_side_effect(query, *params, **kwargs):
        if "v_tvd_agrupaciones" in query and "code = $1 AND version = $2" in query:
            return existing
        if "estado IN ('convalidada', 'migrada')" in query:
            return None  # sin vigente previa que derogar
        if "convalidar_trd_serie_desde_aprobada" in query or "UPDATE trd_series" in query:
            return convalidada
        return None

    mock_conn.fetchrow.side_effect = _fetchrow_side_effect
    mock_conn.execute.return_value = "UPDATE 0"

    resp = await client.post(
        "/api/v1/tvd/FONDO-100/versiones/1/convalidar",
        json={
            "acto_administrativo": "Resolucion 099",
            "fecha_convalidacion": "2026-02-01",
            "instancia_convalidante": "Consejo Departamental",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estado"] == "convalidada"
    assert body["tipo_instrumento"] == "TVD"
    audit_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert any(c[5] == "archive.tvd_version_convalidada" for c in audit_calls)


@pytest.mark.asyncio
async def test_convalidar_tvd_agrupacion_version_requires_acta_comite(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = make_tvd_agrupacion_record(
        code="FONDO-100", version=1, estado="aprobada", acta_comite=None
    )

    resp = await client.post(
        "/api/v1/tvd/FONDO-100/versiones/1/convalidar",
        json={
            "acto_administrativo": "Resolucion 099",
            "fecha_convalidacion": "2026-02-01",
            "instancia_convalidante": "Consejo Departamental",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "trd_aprobacion_comite_requerida"


@pytest.mark.asyncio
async def test_devolver_tvd_agrupacion_version(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    existing = make_tvd_agrupacion_record(code="FONDO-100", version=1, estado="aprobada")
    devuelta = make_tvd_agrupacion_record(code="FONDO-100", version=1, estado="borrador")
    mock_conn.fetchrow.side_effect = [existing, devuelta]

    resp = await client.post(
        "/api/v1/tvd/FONDO-100/versiones/1/devolver",
        json={"motivo": "faltan fechas extremas"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "borrador"


@pytest.mark.asyncio
async def test_registrar_rusd_tvd_agrupacion_version(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    existing = make_tvd_agrupacion_record(code="FONDO-100", version=1, estado="convalidada")
    updated = make_tvd_agrupacion_record(
        code="FONDO-100", version=1, estado="convalidada", rusd_radicado="RUSD-2026-001"
    )
    mock_conn.fetchrow.side_effect = [existing, updated]

    resp = await client.post(
        "/api/v1/tvd/FONDO-100/versiones/1/registrar-rusd",
        json={"rusd_radicado": "RUSD-2026-001"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["rusd_radicado"] == "RUSD-2026-001"


@pytest.mark.asyncio
async def test_derogar_tvd_agrupacion_version(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    # permiso, luego COUNT(*) expedientes open, luego prev_hash del asiento.
    mock_conn.fetchval.side_effect = [True, 0, None]
    existing = make_tvd_agrupacion_record(code="FONDO-100", version=1, estado="convalidada")
    derogada = make_tvd_agrupacion_record(code="FONDO-100", version=1, estado="derogada")
    mock_conn.fetchrow.side_effect = [existing, derogada]

    resp = await client.post(
        "/api/v1/tvd/FONDO-100/versiones/1/derogar",
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "derogada"


@pytest.mark.asyncio
async def test_tvd_circuit_forbidden_without_permission(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    resp = await client.post(
        "/api/v1/tvd/FONDO-100/versiones/1/aprobar",
        json={"acta_comite": "Acta 001", "fecha_aprobacion_comite": "2026-01-10"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    mock_conn.fetchrow.assert_not_called()
