# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de vistos buenos secuenciales (paridad legado)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.core.database import tenant_schema
from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)


# --- hardening de slug (defensa en profundidad ante inyección de schema) -----
def test_tenant_schema_valido() -> None:
    assert tenant_schema("demo") == "tenant_demo"
    assert tenant_schema("bogota-limpia") == "tenant_bogota_limpia"  # normaliza guiones


@pytest.mark.parametrize(
    "bad",
    ["", "Demo", "demo; DROP", 'a" OR "1', "tenant public", "../x", "demo,public"],
)
def test_tenant_schema_invalido_400(bad: str) -> None:
    with pytest.raises(HTTPException) as exc:
        tenant_schema(bad)
    assert exc.value.status_code == 400
RAD = uuid.uuid4()
REV1 = uuid.uuid4()
REV2 = uuid.uuid4()


def _row(orden, revisor, estado="pendiente") -> dict:
    return {
        "id": uuid.uuid4(),
        "orden": orden,
        "revisor_id": revisor,
        "estado": estado,
        "comentario": None,
        "decidido_at": None,
    }


@pytest.mark.asyncio
async def test_crear_cadena(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    # D-09: require_permission consume el 1.er fetchval (RBAC → True); luego el
    # servicio comprueba que el radicado es visible (2.º → 1, no-read-up), consulta
    # exists (3.º → None), crea (execute) y get (fetch).
    mock_conn.fetchval.side_effect = [True, 1, None, 0]
    mock_conn.fetch.return_value = [_row(1, REV1), _row(2, REV2)]

    # La auditoría forense (audit.append) se ejercita en sus propios tests; aquí se
    # aísla para no acoplar el test al detalle interno (advisory lock + prev_hash).
    with patch(
        "app.services.visto_bueno_service.audit.append", new_callable=AsyncMock
    ) as mock_audit:
        resp = await client.post(
            f"/api/v1/workflows/{RAD}/vistos-buenos",
            json={"revisores": [str(REV1), str(REV2)]},
            headers=TENANT_HEADERS,
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["estado"] == "en_revision"
    assert len(body["items"]) == 2
    # La creación de la cadena queda auditada (Canal A) con la acción esperada.
    mock_audit.assert_awaited_once()
    assert mock_audit.await_args.kwargs["action"] == "vobo.cadena_creada"


@pytest.mark.asyncio
async def test_crear_cadena_duplicada_409(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    # RBAC (True) + radicado visible (1) + exists (1) → 409
    mock_conn.fetchval.side_effect = [True, 1, 1]
    resp = await client.post(
        f"/api/v1/workflows/{RAD}/vistos-buenos",
        json={"revisores": [str(REV1)]},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "cadena_existente"


@pytest.mark.asyncio
async def test_crear_cadena_sin_permiso_403(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """D-09: sin PERM_RADI, crear_cadena devuelve 403 (fail-closed)."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None  # check_permission → False
    resp = await client.post(
        f"/api/v1/workflows/{RAD}/vistos-buenos",
        json={"revisores": [str(REV1), str(REV2)]},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_crear_cadena_sin_user_id_403(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """D-09: sin X-User-Id no hay identidad → check_permission fail-closed → 403."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 1  # aunque el RBAC "permitiría", no hay user_id
    resp = await client.post(
        f"/api/v1/workflows/{RAD}/vistos-buenos",
        json={"revisores": [str(REV1)]},
        headers={"X-Tenant-Slug": "demo"},  # sin X-User-Id
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_decidir_sin_user_id_400(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """D-06: X-User-Id es obligatorio en decidir; sin él el router devuelve 400."""
    client, _, _ = client_with_conn
    resp = await client.post(
        f"/api/v1/workflows/{RAD}/vistos-buenos/decidir",
        json={"aprobar": True},
        headers={"X-Tenant-Slug": "demo"},  # sin X-User-Id
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "missing_user"


@pytest.mark.asyncio
async def test_decidir_not_reviewer_403(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """D-06: el actor debe pertenecer a la cadena; si no → 403 not_a_reviewer."""
    client, mock_conn, _ = client_with_conn
    stranger = uuid.uuid4()
    mock_conn.fetchval.return_value = None  # is_reviewer → False (no está en la cadena)
    resp = await client.post(
        f"/api/v1/workflows/{RAD}/vistos-buenos/decidir",
        json={"aprobar": True},
        headers={"X-Tenant-Slug": "demo", "X-User-Id": str(stranger)},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "not_a_reviewer"


@pytest.mark.asyncio
async def test_decidir_fuera_de_turno_403(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    # active → paso de REV1; actor distinto
    mock_conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "orden": 1,
        "revisor_id": REV1,
        "estado": "pendiente",
    }
    resp = await client.post(
        f"/api/v1/workflows/{RAD}/vistos-buenos/decidir",
        json={"aprobar": True},
        headers={**TENANT_HEADERS, "X-User-Id": str(REV2)},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "no_es_su_turno"


@pytest.mark.asyncio
async def test_decidir_aprueba_su_turno(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    # active (fetchrow) → REV1; decide (execute); get → list (fetch) con paso 1 aprobado
    mock_conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "orden": 1,
        "revisor_id": REV1,
        "estado": "pendiente",
    }
    mock_conn.fetch.return_value = [_row(1, REV1, "aprobado"), _row(2, REV2)]

    with patch(
        "app.services.visto_bueno_service.audit.append", new_callable=AsyncMock
    ) as mock_audit:
        resp = await client.post(
            f"/api/v1/workflows/{RAD}/vistos-buenos/decidir",
            json={"aprobar": True, "comentario": "Ok"},
            headers={**TENANT_HEADERS, "X-User-Id": str(REV1)},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["estado"] == "en_revision"  # falta REV2
    assert body["items"][0]["estado"] == "aprobado"
    # La DECISIÓN también deja traza forense, no solo la conformación de la cadena:
    # sin ella `audit_log` sabría a quién se le pidió el visto bueno pero no quién lo
    # dio, cuándo ni por qué.
    mock_audit.assert_awaited_once()
    kwargs = mock_audit.await_args.kwargs
    assert kwargs["action"] == "vobo.decidido"
    assert kwargs["object_type"] == "radicado"
    assert kwargs["object_ref"] == str(RAD)
    assert kwargs["actor"] == str(REV1)
    assert kwargs["payload"] == {"orden": 1, "estado": "aprobado", "comentario": "Ok"}


@pytest.mark.asyncio
async def test_decidir_rechazo_auditado(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Un rechazo detiene la cadena — y es justo la decisión que más importa trazar."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "orden": 2,
        "revisor_id": REV2,
        "estado": "pendiente",
    }
    mock_conn.fetch.return_value = [_row(1, REV1, "aprobado"), _row(2, REV2, "rechazado")]

    with patch(
        "app.services.visto_bueno_service.audit.append", new_callable=AsyncMock
    ) as mock_audit:
        resp = await client.post(
            f"/api/v1/workflows/{RAD}/vistos-buenos/decidir",
            json={"aprobar": False, "comentario": "Falta el anexo de cédula"},
            headers={**TENANT_HEADERS, "X-User-Id": str(REV2)},
        )
    assert resp.status_code == 200
    assert resp.json()["estado"] == "rechazado"
    kwargs = mock_audit.await_args.kwargs
    assert kwargs["payload"] == {
        "orden": 2,
        "estado": "rechazado",
        "comentario": "Falta el anexo de cédula",
    }


@pytest.mark.asyncio
async def test_decidir_sin_traza_no_decide(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Atomicidad: decisión y traza commitean juntas (mismo criterio que `crear`).

    `AsyncClient(transport=ASGITransport(...))` re-lanza las excepciones no manejadas
    por la app en vez de convertirlas en un 500 HTTP: se comprueba que la excepción
    llega hasta aquí, es decir, que ninguna respuesta 200 confirma al revisor un visto
    bueno que quedó sin asiento.
    """
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "orden": 1,
        "revisor_id": REV1,
        "estado": "pendiente",
    }

    with (
        patch(
            "app.services.visto_bueno_service.audit.append",
            new=AsyncMock(side_effect=RuntimeError("audit_log no disponible")),
        ),
        pytest.raises(RuntimeError, match="audit_log no disponible"),
    ):
        await client.post(
            f"/api/v1/workflows/{RAD}/vistos-buenos/decidir",
            json={"aprobar": True, "comentario": "Ok"},
            headers={**TENANT_HEADERS, "X-User-Id": str(REV1)},
        )


# --- AREA-01-N1 / AREA-01-06: el no-read-up va en el SQL de la cadena ----------


def _sqls(mock_conn: AsyncMock) -> list[str]:
    return [
        c.args[0]
        for meth in (mock_conn.fetch, mock_conn.fetchrow, mock_conn.fetchval)
        for c in meth.call_args_list
        if c.args and isinstance(c.args[0], str)
    ]


@pytest.mark.asyncio
async def test_get_cadena_lleva_el_clearance_en_el_join(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = [_row(1, REV1)]
    resp = await client.get(f"/api/v1/workflows/{RAD}/vistos-buenos", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    listados = [q for q in _sqls(mock_conn) if "FROM visto_bueno" in q]
    assert listados, "no se consultó la cadena: la prueba no mide nada"
    assert all("nivel_seguridad <=" in q for q in listados)


@pytest.mark.asyncio
async def test_get_cadena_de_un_reservado_para_otro_es_404_neutro(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """El JOIN no devuelve filas para el clearance del llamante: mismo 404 que un
    radicado sin cadena; el SQL recibe el clearance resuelto (PUBLICA=1)."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetch.return_value = []
    resp = await client.get(
        f"/api/v1/workflows/{RAD}/vistos-buenos",
        headers={**TENANT_HEADERS, "X-User-Id": str(REV1)},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "sin_cadena"
    llamada = next(c for c in mock_conn.fetch.call_args_list if "FROM visto_bueno" in c.args[0])
    assert llamada.args[1:] == (RAD, 1)


@pytest.mark.asyncio
async def test_decidir_sobre_radicado_que_no_puede_leer_es_404(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Un revisor designado sin clearance suficiente no aprueba lo que no puede
    leer: `active()` (con el JOIN) no devuelve el paso → 404, y no se decide."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchrow.return_value = None
    mock_conn.fetchval.return_value = 1  # is_reviewer
    resp = await client.post(
        f"/api/v1/workflows/{RAD}/vistos-buenos/decidir",
        json={"aprobar": True},
        headers={**TENANT_HEADERS, "X-User-Id": str(REV1)},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "sin_pendiente"
    assert any("nivel_seguridad <=" in q for q in _sqls(mock_conn) if "pendiente" in q)
    assert not any("UPDATE visto_bueno" in str(c) for c in mock_conn.execute.call_args_list)
    # El servicio pasa el clearance DEL LLAMANTE (1), no uno amplio: sin esto el JOIN
    # existe pero el parámetro lo vuelve inerte.
    consulta = [c for c in mock_conn.fetchrow.call_args_list if "pendiente" in str(c.args[:1])]
    assert consulta and consulta[0].args[-1] == 1


@pytest.mark.asyncio
async def test_crear_cadena_de_radicado_no_visible_es_404(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [True, None]  # RBAC ok; radicado no visible
    resp = await client.post(
        f"/api/v1/workflows/{RAD}/vistos-buenos",
        json={"revisores": [str(REV1)]},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
    assert not any("INSERT INTO visto_bueno" in str(c) for c in mock_conn.execute.call_args_list)


@pytest.mark.asyncio
async def test_crear_cadena_con_revisor_sin_habilitacion_es_422(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """Un revisor sin clearance para el radicado bloquearía la cadena (decidir daría 404)."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.side_effect = [True, 1, None, 1]  # RBAC, visible, sin cadena, 1 sin nivel
    resp = await client.post(
        f"/api/v1/workflows/{RAD}/vistos-buenos",
        json={"revisores": [str(REV1)]},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "revisor_sin_habilitacion"
    assert not any("INSERT INTO visto_bueno" in str(c) for c in mock_conn.execute.call_args_list)
