# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del catálogo de tipos documentales (E03, RF-MET-07)."""

import json
import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS


def _tipo_row(tipo_id: uuid.UUID, **kw) -> dict:
    base = {
        "id": tipo_id,
        "trd_serie_id": None,
        "code": "RES",
        "nombre": "Resolución",
        "obligatorio": True,
        "metadata_template": "Resolución",
        "activo": True,
        # Opt-in: un tipo NO se ofrece en la ventanilla pública por existir.
        "admite_ventanilla_publica": False,
        # C-4/C10: marca PROPIA de difusión, también opt-in y distinta de la anterior.
        "publicable": False,
        "created_at": datetime.now(tz=UTC),
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_create_tipo(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    tid = uuid.uuid4()
    mock_conn.fetchrow.return_value = _tipo_row(tid)

    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={
            "code": "RES",
            "nombre": "Resolución",
            "obligatorio": True,
            "metadata_template": "Resolución",
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == "RES"
    assert body["obligatorio"] is True


@pytest.mark.asyncio
async def test_list_tipos(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = [_tipo_row(uuid.uuid4()), _tipo_row(uuid.uuid4(), code="ACT")]
    resp = await client.get("/api/v1/tipos-documentales?activo=true", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_get_tipo_not_found(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None
    resp = await client.get(f"/api/v1/tipos-documentales/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_tipo_missing_tenant(client_with_conn) -> None:
    """Sin X-Tenant-Slug, el gate RBAC (`require_permission`, dependency del
    decorador) corta la petición con 403 antes de que `_require_tenant`
    (Depends del handler) pueda emitir su propio 400 — `require_permission`
    exige el tenant para poder resolver el permiso y responde fail-closed."""
    client, _ = client_with_conn
    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={"code": "RES", "nombre": "Resolución"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


# ---------------------------------------------------------------------------
# RBAC gate: USUA_PERM_TRD en las mutaciones de tipos_documentales.py
# (create/update/delete). Las lecturas (GET, list) quedan abiertas — sin
# cambio, solo _require_tenant.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_tipo_forbidden_without_permission(client_with_conn) -> None:
    """Mutación representativa: sin USUA_PERM_TRD, `require_permission` corta
    con 403 antes de tocar la lógica de negocio (no se inserta el tipo)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # permiso denegado

    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={"code": "RES", "nombre": "Resolución"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_create_tipo_forbidden_without_user_id(client_with_conn) -> None:
    """Fail-closed sobre la IDENTIDAD: sin X-User-Id no hay usuario que
    resolver → 403, aunque la BD "tuviera" el permiso."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={"code": "RES", "nombre": "Resolución"},
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_update_tipo_forbidden_without_permission(client_with_conn) -> None:
    """update_tipo (PATCH) también está gateado: sin USUA_PERM_TRD,
    `require_permission` corta con 403 antes de tocar la lógica de negocio."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # permiso denegado

    resp = await client.patch(
        f"/api/v1/tipos-documentales/{uuid.uuid4()}",
        json={"nombre": "Resolución renombrada"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_update_tipo_forbidden_without_user_id(client_with_conn) -> None:
    """Fail-closed sobre la IDENTIDAD: sin X-User-Id no hay usuario que
    resolver → 403, aunque la BD "tuviera" el permiso."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.patch(
        f"/api/v1/tipos-documentales/{uuid.uuid4()}",
        json={"nombre": "Resolución renombrada"},
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_delete_tipo_forbidden_without_permission(client_with_conn) -> None:
    """delete_tipo (DELETE) también está gateado: sin USUA_PERM_TRD,
    `require_permission` corta con 403 antes de tocar la lógica de negocio."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # permiso denegado

    resp = await client.delete(
        f"/api/v1/tipos-documentales/{uuid.uuid4()}",
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    # `require_permission` llama `execute` para SET search_path y, al ser un
    # usuario IDENTIFICADO, también dos veces más para la traza best-effort del
    # acceso denegado (advisory lock + INSERT audit_log, RF-SEG/MoReq) — nunca
    # para el DELETE de negocio, que no se ejecuta.
    assert mock_conn.execute.call_count == 3
    delete_calls = [
        c for c in mock_conn.execute.call_args_list if "DELETE FROM" in c.args[0]
    ]
    assert delete_calls == []


@pytest.mark.asyncio
async def test_delete_tipo_forbidden_without_user_id(client_with_conn) -> None:
    """Fail-closed sobre la IDENTIDAD: sin X-User-Id no hay usuario que
    resolver → 403, aunque la BD "tuviera" el permiso."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.delete(
        f"/api/v1/tipos-documentales/{uuid.uuid4()}",
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    # `require_permission` sí llama `execute` (SET search_path) antes de
    # denegar; solo debe haberse llamado esa vez, no el DELETE de negocio.
    mock_conn.execute.assert_called_once()


@pytest.mark.asyncio
async def test_create_tipo_writes_audit_entry(client_with_conn) -> None:
    """Media (RBAC follow-up): crear un tipo documental deja traza en
    `audit_log` con el actor (X-User-Id) y el código del tipo."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    tid = uuid.uuid4()
    mock_conn.fetchrow.return_value = _tipo_row(tid, code="RES")

    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={"code": "RES", "nombre": "Resolución"},
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
    assert audit_calls[0][5] == "archive.tipo_documental_created"
    assert audit_calls[0][6] == "tipo_documental"
    assert audit_calls[0][7] == "RES"


@pytest.mark.asyncio
async def test_update_tipo_writes_audit_entry(client_with_conn) -> None:
    """Media (RBAC follow-up): actualizar un tipo documental deja traza en
    `audit_log`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    tid = uuid.uuid4()
    # DOS filas distintas a propósito: la 1ª es la lectura del estado ACTUAL
    # (`before`), la 2ª el resultado del UPDATE (`after`). Con una sola fila
    # compartida, `before` y `after` coincidirían y el diff saldría vacío —
    # correctamente, porque un campo enviado con su mismo valor no es un cambio.
    mock_conn.fetchrow.side_effect = [
        _tipo_row(tid, nombre="Resolución"),
        _tipo_row(tid, nombre="Resolución renombrada"),
    ]

    resp = await client.patch(
        f"/api/v1/tipos-documentales/{tid}",
        json={"nombre": "Resolución renombrada"},
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
    assert audit_calls[0][5] == "archive.tipo_documental_updated"
    assert audit_calls[0][6] == "tipo_documental"
    # Diff con valor anterior (mismo criterio que las series TRD): el asiento
    # debe permitir reconstruir el cambio, no solo declarar el estado final.
    assert json.loads(audit_calls[0][8])["changes"] == {
        "nombre": {"before": "Resolución", "after": "Resolución renombrada"}
    }


@pytest.mark.asyncio
async def test_delete_tipo_writes_audit_entry(client_with_conn) -> None:
    """Media (RBAC follow-up): borrar un tipo documental deja traza en
    `audit_log`; sin fila borrada (404) no debe auditarse nada."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    tid = uuid.uuid4()
    mock_conn.execute.return_value = "DELETE 1"

    resp = await client.delete(
        f"/api/v1/tipos-documentales/{tid}",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 204

    audit_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0][3] == TENANT_HEADERS["X-User-Id"]
    assert audit_calls[0][5] == "archive.tipo_documental_deleted"
    assert audit_calls[0][6] == "tipo_documental"
    assert audit_calls[0][7] == str(tid)


# ---------------------------------------------------------------------------
# Mínimo privilegio (RF-SEG-03): la disposición (delete_tipo) exige crud>=3
# (Crear/Borrar); las mutaciones no-disposición (create) quedan en el default 1.
# El umbral viaja como $3 al fetchval del gate (dentro del SQL mockeado).
# ---------------------------------------------------------------------------


def _gate_min_crud(mock_conn) -> int:
    """Extrae el min_crud ($3) del fetchval del gate RBAC de entre todas las
    llamadas a fetchval (audit.append hace su propio fetchval de prev_hash, así
    que no basta con `call_args`; se filtra por la query del gate)."""
    gate_calls = [
        c.args
        for c in mock_conn.fetchval.call_args_list
        if isinstance(c.args[0], str) and "gp.crud >= $3" in c.args[0]
    ]
    assert len(gate_calls) == 1, f"esperaba 1 fetchval del gate, hubo {len(gate_calls)}"
    return gate_calls[0][3]


@pytest.mark.asyncio
async def test_delete_tipo_threads_min_crud_3(client_with_conn) -> None:
    """delete_tipo es disposición: el gate exige crud>=3 → el fetchval del
    permiso recibe min_crud=3 como 4.º argumento posicional ($3)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # permiso concedido
    mock_conn.execute.return_value = "DELETE 1"

    resp = await client.delete(
        f"/api/v1/tipos-documentales/{uuid.uuid4()}", headers=TENANT_HEADERS
    )
    assert resp.status_code == 204
    assert _gate_min_crud(mock_conn) == 3


@pytest.mark.asyncio
async def test_create_tipo_threads_default_min_crud_1(client_with_conn) -> None:
    """create_tipo NO es disposición: usa el default min_crud=1 (sin elevar)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _tipo_row(uuid.uuid4())

    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={"code": "RES", "nombre": "Resolución"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert _gate_min_crud(mock_conn) == 1


@pytest.mark.asyncio
async def test_list_tipos_open_read_no_user_id_needed(client_with_conn) -> None:
    """Confirma que la lectura sigue ABIERTA (solo `_require_tenant`): sin
    X-User-Id ni permiso mockeado, GET /tipos-documentales sigue respondiendo
    200."""
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = 0

    resp = await client.get(
        "/api/v1/tipos-documentales", headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]}
    )

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# C-4/C10 (ADR-040): `publicable`, marca PROPIA del tipo (Ley 1712 art. 12), distinta de
# `admite_ventanilla_publica` (Ley 1755). Cambiarla es una decisión de DIFUSIÓN: nivel 3.
# ---------------------------------------------------------------------------


def _niveles_del_gate(mock_conn, valores: list[bool]) -> None:
    """El gate de ruta y el de difusión comparten `fetchval`: se secuencian."""
    mock_conn.fetchval.side_effect = valores


def _asientos(mock_conn) -> list[tuple]:
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]


@pytest.mark.asyncio
async def test_un_tipo_nace_no_publicable(client_with_conn) -> None:
    """Opt-in: el silencio no significa «publicar»."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _tipo_row(uuid.uuid4())

    resp = await client.post(
        "/api/v1/tipos-documentales", json={"code": "RES", "nombre": "Resolución"}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 201
    assert resp.json()["publicable"] is False
    insert = next(c for c in mock_conn.fetchrow.call_args_list if "INSERT INTO tipos_documentales" in c.args[0])
    assert False in insert.args[1:]  # publicable=False viaja explícito
    assert not any(a[5] == "archive.tipo_documental_publicacion_cambiada" for a in _asientos(mock_conn))


@pytest.mark.asyncio
async def test_crear_un_tipo_publicable_exige_nivel_3_y_lo_asienta(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    _niveles_del_gate(mock_conn, [True, True, 0, 0])  # gate de ruta, nivel 3, prev_hash x2 asientos
    mock_conn.fetchrow.return_value = _tipo_row(uuid.uuid4(), publicable=True)

    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={"code": "RES", "nombre": "Resolución", "publicable": True},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    acciones = [a[5] for a in _asientos(mock_conn)]
    assert "archive.tipo_documental_publicacion_cambiada" in acciones
    cambio = next(a for a in _asientos(mock_conn) if a[5] == "archive.tipo_documental_publicacion_cambiada")
    assert json.loads(cambio[8])["publicable"] == {"before": False, "after": True}


@pytest.mark.asyncio
async def test_sin_nivel_3_no_se_puede_marcar_un_tipo_como_publicable(client_with_conn) -> None:
    """Las demás ediciones del catálogo siguen en el nivel de siempre; difundir a internet no."""
    client, mock_conn = client_with_conn
    _niveles_del_gate(mock_conn, [True, False, 0, 0])  # pasa el gate de ruta, falla el nivel 3
    mock_conn.fetchrow.return_value = _tipo_row(uuid.uuid4(), publicable=True)

    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={"code": "RES", "nombre": "Resolución", "publicable": True},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    assert not any("INSERT INTO tipos_documentales" in c.args[0] for c in mock_conn.fetchrow.call_args_list)


@pytest.mark.asyncio
async def test_patch_de_publicable_sin_nivel_3_es_403_pero_otros_campos_no_lo_piden(
    client_with_conn,
) -> None:
    client, mock_conn = client_with_conn
    tid = uuid.uuid4()
    # Otro campo: un solo fetchval de gate (nivel de siempre) + el prev_hash del asiento.
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_tipo_row(tid), _tipo_row(tid, nombre="Otro")]
    ok = await client.patch(
        f"/api/v1/tipos-documentales/{tid}", json={"nombre": "Otro"}, headers=TENANT_HEADERS
    )
    assert ok.status_code == 200

    # CAMBIAR la marca sí exige nivel 3: pasa el gate de ruta, falla el de difusión.
    _niveles_del_gate(mock_conn, [True, False, 0, 0])
    mock_conn.fetchrow.reset_mock()
    mock_conn.fetchrow.side_effect = None
    mock_conn.fetchrow.return_value = _tipo_row(tid, publicable=False)
    prohibido = await client.patch(
        f"/api/v1/tipos-documentales/{tid}", json={"publicable": True}, headers=TENANT_HEADERS
    )
    assert prohibido.status_code == 403
    assert not any("UPDATE tipos_documentales" in c.args[0] for c in mock_conn.fetchrow.call_args_list)


@pytest.mark.asyncio
async def test_reenviar_la_marca_sin_cambiarla_no_exige_nivel_3_ni_deja_asiento_de_difusion(
    client_with_conn,
) -> None:
    """Un formulario de edición reenvía `publicable` tal como está: quien solo corrige el nombre
    (nivel de siempre) no puede quedar bloqueado por ello. Control de la prueba anterior."""
    client, mock_conn = client_with_conn
    tid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _tipo_row(tid, publicable=True),  # lectura bloqueada del servicio
        _tipo_row(tid, publicable=True, nombre="Otro"),
    ]
    # sin nivel 3: el único fetchval «False» posible sería el de difusión, que no debe ocurrir
    resp = await client.patch(
        f"/api/v1/tipos-documentales/{tid}",
        json={"nombre": "Otro", "publicable": True},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    acciones = [a[5] for a in _asientos(mock_conn)]
    assert acciones == ["archive.tipo_documental_updated"]
    gates = [c for c in mock_conn.fetchval.call_args_list if "gp.crud >= $3" in str(c.args[0])]
    assert len(gates) == 1, "solo el gate de ruta; el de difusión no se evalúa si la marca no cambia"


@pytest.mark.asyncio
async def test_marcar_publicable_deja_un_asiento_propio_con_antes_y_despues(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    tid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_tipo_row(tid), _tipo_row(tid, publicable=True)]

    resp = await client.patch(
        f"/api/v1/tipos-documentales/{tid}", json={"publicable": True}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["publicable"] is True
    assert resp.json()["publicados_vigentes"] is None  # solo al DESmarcar
    acciones = [a[5] for a in _asientos(mock_conn)]
    assert acciones == ["archive.tipo_documental_updated", "archive.tipo_documental_publicacion_cambiada"]
    cambio = _asientos(mock_conn)[1]
    assert json.loads(cambio[8])["publicable"] == {"before": False, "after": True}


@pytest.mark.asyncio
async def test_desmarcar_publicable_informa_cuantos_radicados_siguen_publicados(client_with_conn) -> None:
    """Despublicar el tipo no despublica en silencio lo ya difundido (dejaría
    `radicados.publicable=true` sin servirse: dos verdades). Se INFORMA y el retiro es explícito."""
    client, mock_conn = client_with_conn
    tid = uuid.uuid4()
    # gate, nivel 3, (prev_hash x2 de los asientos), existencia de radicados/columnas, conteo
    mock_conn.fetchval.side_effect = [True, True, 0, 0, True, 4]
    mock_conn.fetchrow.side_effect = [
        _tipo_row(tid, publicable=True),
        _tipo_row(tid, publicable=False),
    ]

    resp = await client.patch(
        f"/api/v1/tipos-documentales/{tid}", json={"publicable": False}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["publicable"] is False
    assert resp.json()["publicados_vigentes"] == 4


@pytest.mark.asyncio
async def test_si_no_se_pueden_contar_los_publicados_se_dice_null_no_cero(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    tid = uuid.uuid4()
    mock_conn.fetchval.side_effect = [True, True, 0, 0, False]  # `radicados` no está en el schema
    mock_conn.fetchrow.side_effect = [
        _tipo_row(tid, publicable=True),
        _tipo_row(tid, publicable=False),
    ]

    resp = await client.patch(
        f"/api/v1/tipos-documentales/{tid}", json={"publicable": False}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["publicados_vigentes"] is None


@pytest.mark.asyncio
async def test_publicable_y_admite_ventanilla_son_marcas_independientes(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _tipo_row(uuid.uuid4(), admite_ventanilla_publica=True, publicable=False)

    resp = await client.get(f"/api/v1/tipos-documentales/{uuid.uuid4()}", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert (resp.json()["admite_ventanilla_publica"], resp.json()["publicable"]) == (True, False)


@pytest.mark.asyncio
async def test_la_lectura_del_tipo_en_el_patch_es_con_for_update(client_with_conn) -> None:
    """S5: la decisión «¿cambia lo que se difunde?» se toma sobre la fila BLOQUEADA; con una
    lectura suelta dos PATCH concurrentes decidían contra el valor anterior."""
    client, mock_conn = client_with_conn
    tid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_tipo_row(tid), _tipo_row(tid, nombre="Otro")]
    await client.patch(f"/api/v1/tipos-documentales/{tid}", json={"nombre": "Otro"}, headers=TENANT_HEADERS)
    lectura = mock_conn.fetchrow.call_args_list[0].args[0]
    assert "FROM tipos_documentales WHERE id = $1 FOR UPDATE" in lectura


@pytest.mark.asyncio
async def test_reactivar_un_tipo_publicable_exige_nivel_3(client_with_conn) -> None:
    """S2: `activo` se cambia con el nivel de siempre, pero reactivar un tipo que YA es publicable
    reabre la puerta de la publicación de oficio: es una decisión de difusión."""
    client, mock_conn = client_with_conn
    tid = uuid.uuid4()
    mock_conn.fetchrow.return_value = _tipo_row(tid, activo=False, publicable=True)
    _niveles_del_gate(mock_conn, [True, False, 0, 0])  # gate de ruta, nivel 3 NO
    resp = await client.patch(
        f"/api/v1/tipos-documentales/{tid}", json={"activo": True}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 403
    assert not any("UPDATE tipos_documentales" in c.args[0] for c in mock_conn.fetchrow.call_args_list)
    # la denegación SÍ queda trazada (la tx no la revierte con el 403)
    assert mock_conn.execute.called

    # controles: desactivar, o reactivar un tipo NO publicable, no piden nivel 3
    for existente, cuerpo in (
        (_tipo_row(tid, activo=True, publicable=True), {"activo": False}),
        (_tipo_row(tid, activo=False, publicable=False), {"activo": True}),
    ):
        mock_conn.fetchval.side_effect = None
        mock_conn.fetchval.return_value = True
        mock_conn.fetchrow.side_effect = [existente, {**existente, **cuerpo}]
        ok = await client.patch(
            f"/api/v1/tipos-documentales/{tid}", json=cuerpo, headers=TENANT_HEADERS
        )
        assert ok.status_code == 200, (cuerpo, ok.text)
