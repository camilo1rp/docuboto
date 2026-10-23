# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-6: `POST /api/v1/signature/internal/artefactos-vigentes` y su admisión para una entidad suspendida.

El endpoint lo llama archive-service (fase de reempaquetado del AIP). La conservación no se detiene porque
la entidad esté suspendida (ADR-035 §8), pero la excepción es UNA ruta de una lista, con token de servicio
y solo para `suspended` (no `inactive`): se prueba la ruta admitida Y las vetadas.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core import conservacion
from app.core import database as db
from app.core.config import settings

RUTA = "/api/v1/signature/internal/artefactos-vigentes"
TOKEN = "token-interno-de-prueba"
F1 = uuid.UUID("11111111-aaaa-bbbb-cccc-000000000001")
F2 = uuid.UUID("11111111-aaaa-bbbb-cccc-000000000002")
FILE1 = uuid.UUID("22222222-aaaa-bbbb-cccc-000000000001")


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "signature_internal_token", TOKEN)


def _h(token: str | None = TOKEN) -> dict[str, str]:
    h = {"X-Tenant-Slug": "demo"}
    if token is not None:
        h["X-Internal-Token"] = token
    return h


@pytest.mark.asyncio
async def test_devuelve_el_xml_vigente_de_las_firmas_que_existen(client_with_conn) -> None:  # noqa: ANN001
    client, conn = client_with_conn
    conn.fetch.return_value = [
        {"firma_id": F1, "minio_key": f"{FILE1}/indice-v1-firmado-lta2.xml", "xml_sha256": "a" * 64, "ordinal": 2}
    ]
    resp = await client.post(RUTA, json={"firma_ids": [str(F1), str(F2)]}, headers=_h())
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "items": [{
            "firma_id": str(F1), "file_id": str(FILE1),
            "minio_key": f"{FILE1}/indice-v1-firmado-lta2.xml", "xml_sha256": "a" * 64, "ordinal": 2,
        }]
    }
    # solo se pidió lo pedido: la consulta recibe los dos ids
    assert [str(x) for x in conn.fetch.await_args.args[1]] == [str(F1), str(F2)]


@pytest.mark.asyncio
async def test_sin_token_o_con_otro_no_se_atiende(client_with_conn) -> None:  # noqa: ANN001
    client, conn = client_with_conn
    cuerpo = {"firma_ids": [str(F1)]}
    assert (await client.post(RUTA, json=cuerpo, headers=_h(None))).status_code == 401
    assert (await client.post(RUTA, json=cuerpo, headers=_h("otro"))).status_code == 403
    conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_token_de_servicio_no_basta_sin_estar_configurado(client_with_conn, monkeypatch) -> None:  # noqa: ANN001
    client, _conn = client_with_conn
    monkeypatch.setattr(settings, "signature_internal_token", "")
    resp = await client.post(RUTA, json={"firma_ids": [str(F1)]}, headers=_h("cualquiera"))
    assert resp.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [[], [str(uuid.uuid4())] * 201, ["no-es-uuid"]])
async def test_el_lote_se_acota_en_200(client_with_conn, ids: list[str]) -> None:  # noqa: ANN001
    client, conn = client_with_conn
    resp = await client.post(RUTA, json={"firma_ids": ids}, headers=_h())
    assert resp.status_code == 422
    conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_un_tenant_sin_la_migracion_26_no_tiene_nada_que_renovar(client_with_conn) -> None:  # noqa: ANN001
    import asyncpg

    client, conn = client_with_conn
    conn.fetch.side_effect = asyncpg.UndefinedTableError("firma_artefacto_vigente")
    resp = await client.post(RUTA, json={"firma_ids": [str(F1)]}, headers=_h())
    assert resp.status_code == 200 and resp.json() == {"items": []}


# ---------------------------------------------------------------------------
# La admisión para una entidad suspendida
# ---------------------------------------------------------------------------


def test_la_lista_de_conservacion_es_exactamente_esa_ruta() -> None:
    assert conservacion.es_ruta_de_conservacion("POST", RUTA)
    assert conservacion.es_ruta_de_conservacion("post", RUTA)
    for metodo, ruta in (
        ("GET", RUTA),
        ("POST", RUTA + "\n"),  # `$` admitiría el salto de línea; `fullmatch` no
        ("POST", RUTA + "/"),
        ("POST", "/api/v1/signature/internal/sign-indice"),  # otra ruta interna: sigue vetada
        ("POST", "/api/v1/signature/internal/circuitos/radicado/x/invalidar"),
        ("POST", "/api/v1/signature/verify"),
        ("POST", "/api/v1/signature/sign-xml"),
    ):
        assert not conservacion.es_ruta_de_conservacion(metodo, ruta), (metodo, ruta)


class _Conn:
    def __init__(self, fila: dict) -> None:
        self.fila = fila
        self.ejecutados: list[str] = []

    async def fetchrow(self, sql: str, *args: object) -> dict:
        return self.fila

    async def execute(self, sql: str, *args: object) -> None:
        self.ejecutados.append(sql)

    def transaction(self):  # noqa: ANN201
        class _CM:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *a: object) -> bool:
                return False

        return _CM()


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def acquire(self):  # noqa: ANN201
        conn = self.conn

        class _CM:
            async def __aenter__(self) -> _Conn:
                return conn

            async def __aexit__(self, *a: object) -> bool:
                return False

        return _CM()


def _fila(status: str) -> dict:
    return {"status": status, "provisioning_status": "active", "hay_schema": True}


async def _abrir(status: str, ruta: str, token: str | None, monkeypatch, asientos: list):  # noqa: ANN001, ANN202
    conn = _Conn(_fila(status))
    cab = {"X-Tenant-Slug": "demo"}
    if token is not None:
        cab["X-Internal-Token"] = token
    req = SimpleNamespace(
        headers=cab, method="POST", scope={"path": ruta},
        app=SimpleNamespace(state=SimpleNamespace(db_pool=_Pool(conn))),
    )
    monkeypatch.setattr(conservacion.audit, "append", AsyncMock(side_effect=lambda *a, **k: asientos.append(k)))
    monkeypatch.setattr(conservacion, "_YA_ASENTADAS", set())
    gen = db.get_tenant_conn(req)  # type: ignore[arg-type]
    return conn, gen


@pytest.mark.asyncio
async def test_una_entidad_suspendida_si_conserva_por_esa_ruta_y_deja_asiento(monkeypatch) -> None:  # noqa: ANN001
    asientos: list = []
    conn, gen = await _abrir("suspended", RUTA, TOKEN, monkeypatch, asientos)
    assert await gen.__anext__() is conn
    assert conn.ejecutados == ['SET search_path = "tenant_demo"']
    assert [a["action"] for a in asientos] == ["entidad.conservacion_admitida"]
    assert asientos[0]["payload"] == {"estado_entidad": "suspended", "metodo": "POST", "ruta": RUTA}
    await gen.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "ruta", "token"),
    [
        ("suspended", "/api/v1/signature/internal/sign-indice", TOKEN),  # otra ruta, mismo token
        ("suspended", "/api/v1/signature/verify", TOKEN),
        ("suspended", RUTA, None),  # sin token
        ("suspended", RUTA, "equivocado"),
        ("inactive", RUTA, TOKEN),  # dada de baja: ni con ruta ni con token
    ],
    ids=["otra_ruta_interna", "verify", "sin_token", "token_ajeno", "inactiva"],
)
async def test_lo_demas_sigue_vetado_para_una_entidad_no_operativa(
    status: str, ruta: str, token: str | None, monkeypatch  # noqa: ANN001
) -> None:
    """Control negativo de la excepción: ensanchar la lista o el estado admitido tumba una de estas."""
    asientos: list = []
    conn, gen = await _abrir(status, ruta, token, monkeypatch, asientos)
    with pytest.raises(HTTPException) as exc:
        await gen.__anext__()
    assert exc.value.status_code == 403 and exc.value.detail["error"] == "tenant_inactivo"
    assert conn.ejecutados == [] and asientos == []
