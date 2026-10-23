# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Qué se sirve SIN autenticar según el estado de la entidad (B9-1 + N1/N2, ADR-035).

Sin JWT el slug viaja en el path. Una entidad dada de baja, una sin aprovisionar y un
slug que no existe responden el MISMO 404, con el mismo cuerpo byte a byte. Una
entidad SUSPENDIDA (con su schema intacto) sigue sirviendo las LECTURAS públicas
—verificación, OAI, CMIS—: el plazo de oposición de un inventario de eliminación no se
detiene por una suspensión (Ley 527 art. 12; Ley 594 arts. 14 y 27; Ley 1712 arts.
9-11; Ac. AGN 004/2019). Sus ESCRITURAS (PQRS, ventanilla) responden
`503 canal_no_disponible`. Ejercen la dependencia REAL, con un doble de la conexión
que devuelve la fila de `public.tenants`.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


class _Conn:
    def __init__(self, fila: dict | None) -> None:
        self.fila = fila
        self.ejecutados: list[str] = []

    async def fetchrow(self, sql: str, *args: object) -> dict | None:
        if "public.tenants" in sql:
            return self.fila
        return None

    async def fetchval(self, sql: str, *args: object) -> object:
        # Lo que respondería `to_regclass`: la tabla existe si la entidad tiene fila.
        return "radicados" if self.fila else None

    async def execute(self, sql: str, *args: object) -> None:
        self.ejecutados.append(sql)


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _CM:
            async def __aenter__(self) -> _Conn:
                return conn

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _CM()


@pytest_asyncio.fixture
async def publico():
    from app.core.database import (
        get_public_tenant_conn,
        get_public_tenant_conn_escritura,
        get_redis,
        get_tenant_client,
    )

    app.dependency_overrides.pop(get_public_tenant_conn, None)
    app.dependency_overrides.pop(get_public_tenant_conn_escritura, None)
    # La dependencia bajo prueba es la REAL; las de infraestructura no se ejercen
    # (la entidad se rechaza antes de tocarlas).
    app.dependency_overrides[get_redis] = lambda: None
    app.dependency_overrides[get_tenant_client] = lambda: None
    conn = _Conn(None)
    app.state.db_pool = _Pool(conn)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, conn
    app.dependency_overrides.pop(get_redis, None)
    app.dependency_overrides.pop(get_tenant_client, None)
    if hasattr(app.state, "db_pool"):
        del app.state.db_pool


def _fila(status: str = "active", prov: str = "active", schema: bool = True) -> dict:
    return {"status": status, "provisioning_status": prov, "hay_schema": schema}


_LECTURAS = [("GET", "/api/v1/public/{t}/verify/abc")]
_VENTANILLA = [
    ("GET", "/api/v1/public/{t}/ventanilla/tipos"),
    ("GET", "/api/v1/public/{t}/ventanilla/estado"),
]
# La politica de tratamiento (Ley 1581) es un aviso publico de LECTURA: no recibe
# datos del ciudadano y se sirve tambien con la entidad suspendida (re-auditoria B9 D).
_POLITICA = ("GET", "/api/v1/public/{t}/ventanilla/politica")
_RUTAS = _LECTURAS + _VENTANILLA + [_POLITICA]


@pytest.mark.parametrize(("metodo", "ruta"), _RUTAS)
@pytest.mark.parametrize(
    "fila",
    [_fila(status="inactive"), _fila(prov="pending"), _fila(schema=False)],
    ids=["inactiva", "pendiente", "sin_schema"],
)
async def test_baja_pendiente_e_inexistente_dan_el_mismo_404(
    publico, metodo: str, ruta: str, fila: dict
) -> None:
    client, conn = publico

    conn.fila = None
    inexistente = await client.request(metodo, ruta.format(t="no-existe"))
    conn.fila = fila
    no_operativa = await client.request(metodo, ruta.format(t="demo"))

    assert inexistente.status_code == no_operativa.status_code == 404
    assert inexistente.content == no_operativa.content
    assert no_operativa.json()["detail"]["error"] == "not_found"
    assert conn.ejecutados == [], "no se debe fijar el search_path de una entidad no operativa"


_PQRS = {
    "tipo": "peticion",
    "nombre": "Ana Pérez",
    "identificacion": "123456",
    "email": "ana@example.co",
    "asunto": "Solicito información",
    "descripcion": "Solicito información sobre mi trámite pendiente",
    "acepta_tratamiento": True,
    "politica_version": "1",
    "altcha": "altcha-de-prueba-0000",
}


async def test_pqrs_de_una_entidad_pendiente_es_el_mismo_404_que_la_inexistente(publico) -> None:
    client, conn = publico

    conn.fila = None
    inexistente = await client.post("/api/v1/public/no-existe/pqrs", json=_PQRS)
    conn.fila = _fila(prov="pending")
    pendiente = await client.post("/api/v1/public/demo/pqrs", json=_PQRS)

    assert inexistente.status_code == pendiente.status_code == 404
    assert inexistente.content == pendiente.content


async def test_pqrs_de_una_entidad_suspendida_es_503_canal_no_disponible(publico) -> None:
    """N1: la suspendida EXISTE y no es un secreto; quien radica debe saber que el canal
    está cerrado, no creer que se equivocó de dirección."""
    client, conn = publico
    conn.fila = _fila(status="suspended")

    resp = await client.post("/api/v1/public/demo/pqrs", json=_PQRS)

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "canal_no_disponible"
    assert resp.headers["retry-after"]
    assert conn.ejecutados == [], "ni siquiera se fija el schema de una entidad con el canal cerrado"


@pytest.mark.parametrize(("metodo", "ruta"), _VENTANILLA)
async def test_la_ventanilla_de_una_entidad_suspendida_es_503_canal_no_disponible(
    publico, metodo: str, ruta: str
) -> None:
    client, conn = publico
    conn.fila = _fila(status="suspended")

    resp = await client.request(metodo, ruta.format(t="demo"))

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "canal_no_disponible"


async def test_la_politica_de_tratamiento_de_una_entidad_suspendida_SI_se_sirve(
    publico, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-auditoria B9 D: es lectura (Ley 1581: el aviso se conoce antes de dar
    datos); el 503 queda para quien intenta radicar, no para quien lee el aviso."""
    from app.schemas.public import PoliticaTratamiento
    from app.services.ventanilla_service import VentanillaService

    async def _politica(self) -> PoliticaTratamiento:  # noqa: ANN001
        return PoliticaTratamiento(publicada=False)

    monkeypatch.setattr(VentanillaService, "politica", _politica)
    client, conn = publico
    conn.fila = _fila(status="suspended")

    resp = await client.get("/api/v1/public/demo/ventanilla/politica")

    assert resp.status_code == 200
    assert resp.json()["publicada"] is False


async def test_ventanilla_post_de_una_entidad_suspendida_es_503(publico) -> None:
    client, conn = publico
    conn.fila = _fila(status="suspended")

    resp = await client.post("/api/v1/public/demo/ventanilla", data={"datos": "{}"})

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "canal_no_disponible"


async def test_verify_de_una_entidad_suspendida_SI_se_sirve(publico) -> None:
    """N1 (AUD-ARCH #1): la verificación pública es lectura y sigue disponible."""
    client, conn = publico
    conn.fila = _fila(status="suspended")
    consultas: list[str] = []
    fetchrow_base = conn.fetchrow

    async def _fila_de_radicado(sql: str, *args: object) -> dict | None:
        if "FROM radicados" in sql:
            consultas.append(sql)
            return {
                "tracking_number": "2026-DEMO-E-000001",
                "doc_type": "E",
                "status": "radicado",
                "registered_at": "2026-01-01T00:00:00Z",
                "dest_dept": None,
            }
        return await fetchrow_base(sql, *args)

    conn.fetchrow = _fila_de_radicado  # type: ignore[method-assign]
    resp = await client.get("/api/v1/public/demo/verify/abc")

    assert resp.status_code == 200
    assert resp.json()["tracking_number"] == "2026-DEMO-E-000001"
    assert consultas, "se debe haber consultado el schema de la entidad suspendida"
    assert any('SET search_path = "tenant_demo"' in e for e in conn.ejecutados)


async def test_el_callback_postal_de_una_entidad_pendiente_es_404(publico) -> None:
    client, conn = publico
    conn.fila = _fila(prov="pending")

    resp = await client.post("/api/v1/public/postal/callback/demo", content=b"{}")

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "not_found"


async def test_si_el_registro_no_responde_es_503_no_404(publico) -> None:
    client, conn = publico

    async def _cae(sql: str, *args: object) -> None:
        raise RuntimeError("registro caído")

    conn.fetchrow = _cae  # type: ignore[method-assign]
    resp = await client.get("/api/v1/public/demo/verify/abc")

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "tenant_estado_no_disponible"


# -- CMIS y OAI: el predicado de existencia (`to_regclass`) pasó a ser el estado -----


@pytest.fixture
def _interop(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import config as cfg_module

    monkeypatch.setattr(cfg_module.settings, "public_interop_enabled", True)


class _PoolCmis(_Pool):
    """CMIS usa `await pool.acquire()` + `await pool.release(conn)`."""

    async def acquire(self):  # type: ignore[override]
        return self.conn

    async def release(self, conn) -> None:
        return None


@pytest.mark.parametrize(
    "fila",
    [_fila(status="inactive"), _fila(prov="pending"), _fila(schema=False)],
    ids=["inactiva", "pendiente", "sin_schema"],
)
async def test_oai_de_una_entidad_no_operativa_es_el_mismo_badargument(
    publico, _interop, fila: dict
) -> None:
    client, conn = publico

    conn.fila = None
    inexistente = await client.get("/api/v1/public/oai/no-existe?verb=Identify")
    conn.fila = fila
    no_operativa = await client.get("/api/v1/public/oai/demo?verb=Identify")

    assert inexistente.status_code == no_operativa.status_code == 400
    assert b"badArgument" in no_operativa.content
    # El cuerpo refleja el `baseURL`/tenant de la petición; lo demás es idéntico.
    assert inexistente.content.replace(b"no-existe", b"demo") == no_operativa.content
    assert conn.ejecutados == [], "no se debe fijar el search_path de una entidad no operativa"


@pytest.mark.parametrize(
    "fila",
    [_fila(status="inactive"), _fila(prov="pending"), _fila(schema=False)],
    ids=["inactiva", "pendiente", "sin_schema"],
)
async def test_cmis_de_una_entidad_no_operativa_es_object_not_found(
    publico, _interop, fila: dict
) -> None:
    client, conn = publico
    app.state.db_pool = _PoolCmis(conn)

    conn.fila = None
    inexistente = await client.get("/api/v1/public/cmis/no-existe")
    conn.fila = fila
    no_operativa = await client.get("/api/v1/public/cmis/demo")

    assert inexistente.status_code == no_operativa.status_code == 404
    assert no_operativa.json()["exception"] == "objectNotFound"
    assert inexistente.content == no_operativa.content
    assert all("tenant_demo" not in e for e in conn.ejecutados)


@pytest.mark.parametrize("ruta", [r[1] for r in _RUTAS])
async def test_un_slug_mal_formado_es_el_mismo_404_y_no_toca_la_base(publico, ruta: str) -> None:
    """Antes era un 400 `invalid_tenant` (distinguible de una entidad inexistente)."""
    client, conn = publico
    conn.fila = None

    mal_formado = await client.get(ruta.format(t="Mal_Slug!"))
    inexistente = await client.get(ruta.format(t="no-existe"))

    assert mal_formado.status_code == inexistente.status_code == 404
    assert mal_formado.content == inexistente.content
    assert conn.ejecutados == []


async def test_el_oai_de_una_entidad_suspendida_SI_se_sirve(publico, _interop) -> None:
    """N1: el OAI es lectura pública; la suspensión no lo apaga (Ley 1712 arts. 9-11)."""
    client, conn = publico
    conn.fila = _fila(status="suspended")

    async def _sin_registros(sql: str, *args: object) -> None:
        return None  # `earliestDatestamp`: repositorio vacío

    conn.fetchval = _sin_registros  # type: ignore[method-assign]
    resp = await client.get("/api/v1/public/oai/demo?verb=Identify")

    assert resp.status_code == 200
    assert b"badArgument" not in resp.content
    assert any('"tenant_demo"' in e for e in conn.ejecutados)


async def test_cmis_de_una_entidad_suspendida_SI_se_sirve(publico, _interop) -> None:
    client, conn = publico
    app.state.db_pool = _PoolCmis(conn)
    conn.fila = _fila(status="suspended")

    resp = await client.get("/api/v1/public/cmis/demo")

    assert resp.status_code == 200
    assert any('"tenant_demo"' in e for e in conn.ejecutados)
