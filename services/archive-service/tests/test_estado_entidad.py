# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Qué sirven las puertas públicas de archive según el estado de la entidad (ADR-035).

La eliminación pública solo miraba que el slug existiera en `public.tenants` (ignoraba
`status`) y el OAI usaba `to_regclass`. Ambos usan ahora `atencion_publica`: una entidad
dada de baja, sin aprovisionar o inexistente recibe la MISMA respuesta. Una entidad
SUSPENDIDA sigue sirviendo la publicidad de la eliminación y el OAI: el plazo de
oposición del inventario no se detiene por una suspensión (Ac. AGN 004/2019; Ley 594
arts. 14 y 27; Ley 1712 arts. 9-11). Ejercen la función real, con un doble de la
conexión que devuelve la fila de `public.tenants`.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


class _Conn:
    def __init__(self) -> None:
        self.fila: dict | None = None
        self.ejecutados: list[str] = []
        self.consultas: list[str] = []

    async def fetchrow(self, sql: str, *args: object) -> dict | None:
        return self.fila if "public.tenants" in sql else None

    async def fetch(self, sql: str, *args: object) -> list:
        self.consultas.append(sql)
        return []

    async def fetchval(self, sql: str, *args: object) -> object:
        # Lo que respondería `to_regclass`: la tabla existe si la entidad tiene fila.
        return "expedientes" if self.fila else None

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
async def publico(monkeypatch: pytest.MonkeyPatch):
    from app.core import config as cfg_module
    from app.core.database import get_db_conn

    monkeypatch.setattr(cfg_module.settings, "public_interop_enabled", True)
    conn = _Conn()
    app.dependency_overrides[get_db_conn] = lambda: conn
    app.state.db_pool = _Pool(conn)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, conn
    app.dependency_overrides.pop(get_db_conn, None)
    if hasattr(app.state, "db_pool"):
        del app.state.db_pool


def _fila(status: str = "active", prov: str = "active", schema: bool = True) -> dict:
    return {"status": status, "provisioning_status": prov, "hay_schema": schema}


_NO_OPERATIVAS = [
    _fila(status="inactive"),
    _fila(prov="pending"),
    _fila(schema=False),
]
_IDS = ["inactiva", "pendiente", "sin_schema"]


@pytest.mark.parametrize("fila", _NO_OPERATIVAS, ids=_IDS)
async def test_la_eliminacion_publica_de_una_entidad_no_operativa_es_la_lista_vacia(
    publico, fila: dict
) -> None:
    client, conn = publico

    conn.fila = None
    inexistente = await client.get("/api/v1/public/archive/eliminacion/no-existe/lotes")
    conn.fila = fila
    no_operativa = await client.get("/api/v1/public/archive/eliminacion/demo/lotes")

    assert inexistente.status_code == no_operativa.status_code == 200
    assert inexistente.json() == no_operativa.json() == []
    assert conn.consultas == [], "no se debe consultar el schema de una entidad no operativa"


@pytest.mark.parametrize("fila", _NO_OPERATIVAS, ids=_IDS)
async def test_el_oai_de_una_entidad_no_operativa_es_el_mismo_badargument(
    publico, fila: dict
) -> None:
    client, conn = publico

    conn.fila = None
    inexistente = await client.get("/api/v1/public/archive/oai/no-existe?verb=Identify")
    conn.fila = fila
    no_operativa = await client.get("/api/v1/public/archive/oai/demo?verb=Identify")

    assert inexistente.status_code == no_operativa.status_code == 400
    assert b"badArgument" in no_operativa.content
    assert inexistente.content.replace(b"no-existe", b"demo") == no_operativa.content
    assert conn.ejecutados == [], "no se debe fijar el search_path de una entidad no operativa"


async def test_la_eliminacion_publica_de_una_entidad_suspendida_SI_se_sirve(publico) -> None:
    """N1 (AUD-ARCH #1): el inventario sigue visible mientras corre el plazo de oposición."""
    client, conn = publico
    conn.fila = _fila(status="suspended")

    resp = await client.get("/api/v1/public/archive/eliminacion/demo/lotes")

    assert resp.status_code == 200
    assert resp.json() == []
    assert conn.consultas, "se debe haber consultado el schema de la entidad suspendida"


async def test_el_oai_de_una_entidad_suspendida_SI_se_sirve(publico) -> None:
    client, conn = publico
    conn.fila = _fila(status="suspended")

    async def _sin_expedientes(sql: str, *args: object) -> None:
        return None  # `earliestDatestamp`: repositorio vacío

    conn.fetchval = _sin_expedientes  # type: ignore[method-assign]
    resp = await client.get("/api/v1/public/archive/oai/demo?verb=Identify")

    assert resp.status_code == 200
    assert b"badArgument" not in resp.content
    assert any('"tenant_demo"' in e for e in conn.ejecutados)
