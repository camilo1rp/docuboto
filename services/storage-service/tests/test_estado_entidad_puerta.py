# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""storage-service no atiende a una entidad no operativa en su puerta (B9-N3, ADR-035).

storage-service no tiene `get_tenant_conn`: la conexión la entrega `get_db_conn`, por la que
pasa toda petición con tenant (también las lecturas que no piden `require_permission`,
p. ej. `/api/v1/preservacion/estado`). Sin este gate, un juego de cabeceras de identidad reproducido
dentro de la red evadía la suspensión: el gateway la hace cumplir con hasta 15 s de caché
y hablarle directo al servicio no pasa por él. Se ejerce el veredicto REAL con un doble
de la conexión que devuelve la fila de `public.tenants`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db_conn
from app.main import app


class _Conn:
    def __init__(self, fila: dict | None = None, error: Exception | None = None) -> None:
        self.fila, self.error = fila, error
        self.consultas = 0
        self.ejecutados: list[str] = []

    async def fetchrow(self, sql: str, *args: object) -> dict | None:
        self.consultas += 1
        if self.error:
            raise self.error
        return self.fila if "public.tenants" in sql else None

    async def execute(self, sql: str, *args: object) -> None:
        self.ejecutados.append(sql)


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _CM:
            async def __aenter__(self) -> _Conn:
                return conn

            async def __aexit__(self, *exc: object) -> bool:
                return False

        return _CM()


def _fila(status: str = "active", prov: str = "active", schema: bool = True) -> dict:
    return {"status": status, "provisioning_status": prov, "hay_schema": schema}


def _request(
    conn: _Conn, cabeceras: dict[str, str], *, metodo: str = "GET", ruta: str = "/api/v1/storage/files"
) -> SimpleNamespace:
    return SimpleNamespace(
        headers=cabeceras, method=metodo, scope={"path": ruta},
        app=SimpleNamespace(state=SimpleNamespace(db_pool=_Pool(conn))),
    )


_NO_OPERATIVAS = [
    (_fila(status="suspended"), 403, "tenant_inactivo"),
    (_fila(status="inactive"), 403, "tenant_inactivo"),
    (None, 403, "tenant_inactivo"),
    (_fila(prov="pending"), 503, "tenant_no_provisionado"),
    (_fila(schema=False), 503, "tenant_no_provisionado"),
]
_IDS = ["suspendida", "inactiva", "inexistente", "pendiente", "sin_schema"]


@pytest.mark.parametrize(("fila", "http", "error"), _NO_OPERATIVAS, ids=_IDS)
async def test_la_conexion_de_una_entidad_no_operativa_se_niega(
    fila: dict | None, http: int, error: str
) -> None:
    conn = _Conn(fila)
    gen = get_db_conn(_request(conn, {"X-Tenant-Slug": "demo"}))  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc:
        await gen.__anext__()

    assert exc.value.status_code == http
    assert exc.value.detail["error"] == error
    assert conn.ejecutados == [], "no se debe tocar el search_path de una entidad no operativa"


async def test_si_el_registro_no_responde_se_niega_fail_closed() -> None:
    conn = _Conn(error=RuntimeError("registro caído"))
    gen = get_db_conn(_request(conn, {"X-Tenant-Slug": "demo"}))  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc:
        await gen.__anext__()

    assert (exc.value.status_code, exc.value.detail["error"]) == (503, "tenant_estado_no_disponible")


async def test_una_entidad_operativa_recibe_su_conexion() -> None:
    conn = _Conn(_fila())
    gen = get_db_conn(_request(conn, {"X-Tenant-Slug": "demo"}))  # type: ignore[arg-type]

    assert await gen.__anext__() is conn
    await gen.aclose()
    assert conn.ejecutados == ["SET search_path = public"]


@pytest.mark.parametrize("cabeceras", [{}, {"X-Tenant-Slug": ""}, {"X-Tenant-Slug": 'x"; --'}])
async def test_sin_tenant_valido_no_se_consulta_el_estado(cabeceras: dict[str, str]) -> None:
    """Rutas públicas (el slug va en el path) y slug mal formado (conserva su 400 de
    `tenant_schema`): el gate no se inventa un veredicto para lo que no es una entidad."""
    conn = _Conn(_fila(status="suspended"))
    gen = get_db_conn(_request(conn, cabeceras))  # type: ignore[arg-type]

    assert await gen.__anext__() is conn
    assert conn.consultas == 0
    await gen.aclose()


async def test_una_ruta_real_sin_require_permission_tambien_queda_cerrada() -> None:
    """`/api/v1/preservacion/estado` no pide `require_permission`: el gate de `get_db_conn` es lo único que
    la protege de una entidad suspendida (en vivo respondía 200 hasta que se probó)."""
    conn = _Conn(_fila(status="suspended"))
    app.state.db_pool = _Pool(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/preservacion/estado", headers={"X-Tenant-Slug": "demo"})
    finally:
        del app.state.db_pool

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "tenant_inactivo"
    assert conn.consultas == 1, "ninguna consulta de negocio llegó a ejecutarse"
