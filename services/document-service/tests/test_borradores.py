# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de plantillas y borradores de Salida (paridad legado)."""

import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS, make_radicado_record

NOW = datetime.now(tz=UTC)


def _plantilla_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "nombre": "Oficio estándar",
        "tipo_documental": "Oficio",
        "cuerpo": "Cordial saludo, ...",
        "activo": True,
        "created_at": NOW,
    }
    base.update(kw)
    return base


def _borrador_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "doc_type": "S",
        "subject": "Respuesta a solicitud",
        "dest_dept": "Jurídica",
        "cuerpo": "Texto",
        "plantilla_id": None,
        "estado": "borrador",
        "radicado_id": None,
        "creado_por": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_crear_plantilla(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = _plantilla_row()
    resp = await client.post(
        "/api/v1/plantillas",
        json={
            "nombre": "Oficio estándar",
            "cuerpo": "Cordial saludo, ...",
            "tipo_documental": "Oficio",
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["nombre"] == "Oficio estándar"


@pytest.mark.asyncio
async def test_crear_borrador_desde_plantilla(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    pid = uuid.uuid4()
    # get_plantilla (cuerpo) → insert_borrador
    mock_conn.fetchrow.side_effect = [
        _plantilla_row(id=pid, cuerpo="Cuerpo de plantilla"),
        _borrador_row(plantilla_id=pid, cuerpo="Cuerpo de plantilla"),
    ]
    resp = await client.post(
        "/api/v1/borradores",
        json={"doc_type": "S", "subject": "Resp.", "plantilla_id": str(pid)},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["estado"] == "borrador"


@pytest.mark.asyncio
async def test_editar_borrador_radicado_409(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = _borrador_row(estado="radicado")
    resp = await client.patch(
        f"/api/v1/borradores/{uuid.uuid4()}",
        json={"subject": "Nuevo"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "borrador_radicado"


@pytest.mark.asyncio
async def test_aprobar_borrador(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    # _require_editable (get) → set_estado (RETURNING aprobado)
    mock_conn.fetchrow.side_effect = [
        _borrador_row(id=bid, estado="borrador"),
        _borrador_row(id=bid, estado="aprobado"),
    ]
    resp = await client.post(f"/api/v1/borradores/{bid}/aprobar", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["estado"] == "aprobado"


# ---------------------------------------------------------------------------
# D-11: comprobaciones de autorización (PERM_RADI / USUA_PERM_ADMIN)
# ---------------------------------------------------------------------------

_USER = str(uuid.uuid4())

# -- Plantillas: USUA_PERM_ADMIN (crear/borrar) y PERM_RADI (listar) --------


@pytest.mark.asyncio
async def test_crear_plantilla_sin_permiso_403(client_with_conn) -> None:
    """USUA_PERM_ADMIN requerido para crear plantilla; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        "/api/v1/plantillas",
        json={"nombre": "Oficio", "cuerpo": "...", "tipo_documental": "Oficio"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_crear_plantilla_con_permiso_201(client_with_conn) -> None:
    """USUA_PERM_ADMIN concedido → crear plantilla devuelve 201."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _plantilla_row()
    resp = await client.post(
        "/api/v1/plantillas",
        json={"nombre": "Oficio estándar", "cuerpo": "Cordial saludo", "tipo_documental": "Oficio"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_listar_plantillas_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI requerido para listar plantillas; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get(
        "/api/v1/plantillas",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_listar_plantillas_con_permiso_200(client_with_conn) -> None:
    """PERM_RADI concedido → listar plantillas devuelve 200."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetch.return_value = [_plantilla_row()]
    resp = await client.get(
        "/api/v1/plantillas",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_borrar_plantilla_sin_permiso_403(client_with_conn) -> None:
    """USUA_PERM_ADMIN requerido para borrar plantilla; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.delete(
        f"/api/v1/plantillas/{uuid.uuid4()}",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_borrar_plantilla_con_permiso_204(client_with_conn) -> None:
    """USUA_PERM_ADMIN concedido → borrar plantilla devuelve 204."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.execute.return_value = "DELETE 1"
    resp = await client.delete(
        f"/api/v1/plantillas/{uuid.uuid4()}",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 204


# -- Borradores: PERM_RADI (todas las operaciones) ---------------------------


@pytest.mark.asyncio
async def test_crear_borrador_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI requerido para crear borrador; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        "/api/v1/borradores",
        json={"doc_type": "S", "subject": "Prueba"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_listar_borradores_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI requerido para listar borradores; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get(
        "/api/v1/borradores",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_get_borrador_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI requerido para consultar borrador; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get(
        f"/api/v1/borradores/{uuid.uuid4()}",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_editar_borrador_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI requerido para editar borrador; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.patch(
        f"/api/v1/borradores/{uuid.uuid4()}",
        json={"subject": "Nuevo"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_aprobar_borrador_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI requerido para aprobar borrador; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/borradores/{uuid.uuid4()}/aprobar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_radicar_borrador_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI requerido para radicar; sin permiso → 403 (sin asignar tracking_number)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/borradores/{uuid.uuid4()}/radicar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_radicar_borrador_con_permiso_201(client_with_conn) -> None:
    """PERM_RADI concedido → radicar borrador asigna tracking_number oficial y devuelve 201."""
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    rid = uuid.uuid4()
    radicado = make_radicado_record(doc_id=rid, doc_type="S", tracking_number="2024-DEMO-S-000001")
    # fetchval covers: check_permission (True) + next_sequence (True=1) + audit hash (True).
    mock_conn.fetchval.return_value = True
    mock_conn.fetch.return_value = []  # _get_anexos
    mock_conn.fetchrow.side_effect = [
        _borrador_row(id=bid, doc_type="S", estado="aprobado"),  # get_borrador
        {"code": "DEMO"},                                          # get_tenant_code
        radicado,                                                  # repo.create
        _borrador_row(id=bid, doc_type="S", estado="radicado", radicado_id=rid),  # set_estado
    ]
    resp = await client.post(
        f"/api/v1/borradores/{bid}/radicar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 201
    assert "tracking_number" in resp.json()


@pytest.mark.asyncio
async def test_radicar_un_borrador_sin_aprobar_es_409(client_with_conn) -> None:
    """La aprobación era un control que solo vivía en el navegador.

    El ciclo documentado es crear → editar → aprobar → radicar, y la pantalla de
    borradores lo respeta (`canRadicar = estado === "aprobado"`). El servicio, en
    cambio, solo comprobaba que no estuviera YA radicado: un `POST` directo a
    esta ruta saltaba la aprobación y consumía el número igualmente. Un control
    que vive solo en el cliente no es un control — el cliente es la parte hostil
    (D-05).

    Se comprueba que **no se asigna número**: el daño de este hueco no es el
    estado de una fila, es un número de radicado inmutable consumido por un
    documento que nadie aprobó.
    """
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    mock_conn.fetchval.return_value = True  # permiso concedido: no es lo que se prueba
    mock_conn.fetchrow.side_effect = [_borrador_row(id=bid, doc_type="S", estado="borrador")]

    resp = await client.post(
        f"/api/v1/borradores/{bid}/radicar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "borrador_sin_aprobar"
    # Nada de la secuencia de radicación llegó a ejecutarse.
    assert mock_conn.fetchrow.await_count == 1
