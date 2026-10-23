# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Endpoints de la cola de conciliación (C17, ADR-038): cableado, RBAC y contrato.

La semántica (clearance en el `WHERE`, asiento atómico, estados) la fija el tier de
integración contra Postgres real. Aquí se prueba lo que un doble SÍ puede decir:

* el gate es `PERM_RADI` con `min_crud=3` en AMBOS endpoints (también la lectura:
  lista datos personales de ciudadanos que nadie ha verificado);
* el router entrega al servicio el clearance del llamante y SU identidad firmada
  (`X-User-Id` = `auth_users.id`), no un valor fijo ni otra cabecera;
* `X-Total-Count` es el total que devuelve el servicio;
* sin identidad no se concilia, y la ruta estática no la captura `/{tercero_id}`.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.services.tercero_service import TerceroService
from tests.conftest import TENANT_HEADERS

_USER = str(uuid.uuid4())
_H = {**TENANT_HEADERS, "X-User-Id": _USER}


@pytest.fixture
def gate(monkeypatch):
    """Doble de `check_permission` que registra qué se exigió y concede según `nivel`."""
    estado = {"nivel": 5, "pedidos": []}

    async def _check(conn, user_id, perm, min_crud=1):
        estado["pedidos"].append((perm, min_crud))
        return min_crud <= estado["nivel"]

    monkeypatch.setattr("app.core.clearance.check_permission", _check)

    async def _clearance(conn, user_id):
        return 2

    monkeypatch.setattr("app.routers.terceros.resolve_caller_clearance", _clearance)
    return estado


@pytest.mark.asyncio
@pytest.mark.parametrize("nivel", [1, 2])
async def test_ambos_endpoints_exigen_crud_3(client_with_conn, gate, nivel) -> None:
    client, _ = client_with_conn
    gate["nivel"] = nivel
    leer = await client.get("/api/v1/terceros/por-conciliar", headers=_H)
    actuar = await client.post(
        f"/api/v1/terceros/{uuid.uuid4()}/conciliar", json={"accion": "confirmar"}, headers=_H
    )
    assert leer.status_code == 403 and actuar.status_code == 403
    assert ("PERM_RADI", 3) in gate["pedidos"]


@pytest.mark.asyncio
async def test_la_cola_entrega_el_clearance_y_el_total(client_with_conn, gate, monkeypatch) -> None:
    client, _ = client_with_conn
    visto = {}

    async def _cola(self, *, max_clearance, limit, offset):
        visto.update(max_clearance=max_clearance, limit=limit, offset=offset)
        return [], 7

    monkeypatch.setattr(TerceroService, "por_conciliar", _cola)
    resp = await client.get("/api/v1/terceros/por-conciliar?page=3&size=10", headers=_H)

    assert resp.status_code == 200, resp.text
    assert resp.headers["x-total-count"] == "7"
    assert visto == {"max_clearance": 2, "limit": 10, "offset": 20}


@pytest.mark.asyncio
async def test_conciliar_entrega_clearance_e_identidad_firmada(
    client_with_conn, gate, monkeypatch
) -> None:
    client, _ = client_with_conn
    destino = uuid.uuid4()
    llamada = AsyncMock(
        return_value={
            "id": uuid.uuid4(), "accion": "vincular", "conciliacion": "vinculada",
            "destino_id": destino,
        }
    )
    monkeypatch.setattr(TerceroService, "conciliar", llamada)

    resp = await client.post(
        f"/api/v1/terceros/{uuid.uuid4()}/conciliar",
        json={"accion": "vincular", "destino_id": str(destino), "motivo": "es la misma persona"},
        headers=_H,
    )

    assert resp.status_code == 200, resp.text
    kw = llamada.await_args.kwargs
    assert kw["actor"] == uuid.UUID(_USER), "conciliada_por sale de X-User-Id (auth_users.id)"
    assert kw["max_clearance"] == 2
    assert (kw["accion"], kw["destino_id"]) == ("vincular", destino)


@pytest.mark.asyncio
async def test_accion_desconocida_o_cuerpo_malo_es_422(client_with_conn, gate) -> None:
    client, _ = client_with_conn
    for cuerpo in ({"accion": "borrar"}, {}, {"accion": "descartar", "motivo": "x" * 1001}):
        resp = await client.post(
            f"/api/v1/terceros/{uuid.uuid4()}/conciliar", json=cuerpo, headers=_H
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_sin_identidad_no_se_concilia() -> None:
    """El servicio mismo se niega sin actor: ni siquiera abre la transacción."""
    from fastapi import HTTPException

    from tests.conftest import _make_mock_conn

    conn = _make_mock_conn()
    with pytest.raises(HTTPException) as exc:
        await TerceroService(conn, "demo").conciliar(
            uuid.uuid4(), accion="confirmar", destino_id=None, motivo=None, actor=None,
            max_clearance=3,
        )
    assert exc.value.status_code == 403
    conn.transaction.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("visible_despues,esperado", [(False, 404), (True, 409)])
async def test_si_el_update_no_afecta_filas_se_relee_con_clearance(
    visible_despues, esperado
) -> None:
    """H3: ya no visible => 404 indistinguible (no un 409 que confirma que existe)."""
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    from tests.conftest import _make_mock_conn

    servicio = TerceroService(_make_mock_conn(), "demo")
    ficha = {"conciliacion": "pendiente", "nombre": "x", "documento_declarado_tipo": "CC",
             "documento_declarado_numero": "1"}
    repo = MagicMock()
    repo.bloquear_fichas = AsyncMock()
    repo.bloquear_para_conciliar = AsyncMock(side_effect=[ficha, ficha if visible_despues else None])
    repo.conciliar_descartar = AsyncMock(return_value=None)
    servicio._repo = repo

    with pytest.raises(HTTPException) as exc:
        await servicio.conciliar(
            uuid.uuid4(), accion="descartar", destino_id=None, motivo="motivo largo bastante",
            actor=uuid.uuid4(), max_clearance=1,
        )
    assert exc.value.status_code == esperado


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kw,codigo",
    [
        ({"accion": "vincular", "destino_id": uuid.uuid4()}, "medio_verificacion_requerido"),
        ({"accion": "confirmar"}, "medio_verificacion_requerido"),
        ({"accion": "confirmar", "medio_verificacion": "otro"}, "motivo_requerido"),
        ({"accion": "descartar", "motivo": "motivo largo bastante",
          "medio_verificacion": "otro"}, "medio_verificacion_no_aplica"),
        ({"accion": "vincular", "medio_verificacion": "otro", "motivo": "motivo largo"},
         "destino_requerido"),
        ({"accion": "confirmar", "destino_id": uuid.uuid4()}, "destino_no_aplica"),
        ({"accion": "descartar"}, "motivo_requerido"),
        ({"accion": "descartar", "motivo": "corto"}, "motivo_insuficiente"),
    ],
)
async def test_validaciones_previas_no_tocan_la_base(kw, codigo) -> None:
    from fastapi import HTTPException

    from tests.conftest import _make_mock_conn

    conn = _make_mock_conn()
    base = {"destino_id": None, "motivo": None, "actor": uuid.uuid4(), "max_clearance": 3}
    with pytest.raises(HTTPException) as exc:
        await TerceroService(conn, "demo").conciliar(uuid.uuid4(), **(base | kw))
    assert exc.value.status_code == 422 and exc.value.detail["error"] == codigo
    conn.transaction.assert_not_called()
