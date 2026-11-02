# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La reconciliación borra lo que ya no debe estar y no llama con el interruptor apagado."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.clients.ppi import ClientePpi
from app.core.reconciliacion import reconciliar

pytestmark = pytest.mark.asyncio

RADICADO = uuid.UUID("00000000-0000-4000-8000-0000000000b1")
ANEXO = uuid.UUID("00000000-0000-4000-8000-0000000000b2")


class _Conn:
    def __init__(
        self,
        *,
        proveedor: str = "ppi",
        bandera: bool = True,
        suspendido: bool = False,
        niveles: list[int] | None = None,
        libro: list[str] | None = None,
        estados: dict[uuid.UUID, dict[str, Any] | None] | None = None,
        anexos: dict[uuid.UUID, uuid.UUID] | None = None,
        cursor: str | None = None,
    ) -> None:
        self.proveedor = proveedor
        self.bandera = bandera
        self.suspendido = suspendido
        self.niveles = [1] if niveles is None else niveles
        self.libro = [] if libro is None else libro
        self.estados = {} if estados is None else estados
        self.anexos = {} if anexos is None else anexos
        self.cursor = cursor
        self.escrituras: list[tuple[str, tuple[Any, ...]]] = []
        self.consultas: list[str] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.escrituras.append((sql, args))
        return "OK"

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.consultas.append(sql)
        if "public.tenants" in sql:
            return {
                "asistente_habilitado": self.bandera,
                "intelligence_provider": self.proveedor,
                "intelligence_levels": self.niveles,
                "intelligence_send_metadata": False,
            }
        if "reconciliacion:cursor" in sql:
            return {"inventory_cursor": self.cursor}
        if "intelligence_state" in sql:
            return {"send_suspended": self.suspendido}
        if "reconciliacion:anexo" in sql:
            padre = self.anexos.get(args[0])
            return None if padre is None else {"radicado_id": padre}
        if "radicados" in sql:
            return self.estados.get(args[0])
        raise AssertionError(sql)

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, str]]:
        self.consultas.append(sql)
        if "reconciliacion:libro" not in sql:
            raise AssertionError(sql)
        return [{"record_ref": ref} for ref in self.libro]


class _Transporte:
    def __init__(
        self, pagina: dict[str, Any] | None = None, error: BaseException | None = None
    ) -> None:
        self.pagina = {"items": [], "next_cursor": None} if pagina is None else pagina
        self.error = error
        self.llamadas: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        self.llamadas.append((method, url, json, params))
        if method == "GET" and self.error is not None:
            raise self.error
        if method == "GET":
            return self.pagina
        return None


def _cliente(transporte: _Transporte) -> ClientePpi:
    return ClientePpi(transporte, base_url="http://ppi.internal")


def _posts(transporte: _Transporte) -> list[dict[str, Any]]:
    return [
        cuerpo
        for metodo, _url, cuerpo, _params in transporte.llamadas
        if metodo == "POST" and cuerpo
    ]


def _borrado(cuerpo: dict[str, Any]) -> dict[str, Any]:
    return cuerpo["items"][0]["deletion"]


async def test_con_el_interruptor_apagado_no_hay_llamada() -> None:
    transporte = _Transporte()
    conn = _Conn(proveedor="local")
    resultado = await reconciliar(conn, "demo", _cliente(transporte))
    assert resultado.borrados == 0
    assert transporte.llamadas == []
    assert not any("reconciliacion:libro" in sql for sql in conn.consultas)


async def test_el_libro_borra_un_push_cuyo_nivel_ya_no_esta_habilitado() -> None:
    transporte = _Transporte()
    conn = _Conn(
        libro=[str(RADICADO)],
        estados={RADICADO: {"nivel_seguridad": 2, "status": "radicado"}},
    )
    resultado = await reconciliar(conn, "demo", _cliente(transporte))
    assert resultado.borrados == 1
    [cuerpo] = _posts(transporte)
    assert _borrado(cuerpo)["external_ref"] == str(RADICADO)
    assert _borrado(cuerpo)["reason"] == "reclassified"
    assert transporte.llamadas[0][0] == "POST"
    assert transporte.llamadas[1][0] == "GET"


async def test_un_nivel_habilitado_no_se_borra_y_el_cursor_avanza() -> None:
    transporte = _Transporte(
        {
            "items": [
                {
                    "external_ref": str(RADICADO),
                    "type": "record",
                    "sensitivity": "public",
                    "version": "1",
                    "updated_at": "2026-10-27T14:05:09Z",
                }
            ],
            "next_cursor": "pagina-2",
        }
    )
    conn = _Conn(
        cursor="pagina-1",
        estados={RADICADO: {"nivel_seguridad": 1, "status": "radicado"}},
    )
    resultado = await reconciliar(conn, "demo", _cliente(transporte))
    assert resultado.borrados == 0
    assert resultado.revisados == 1
    assert _posts(transporte) == []
    gets = [llamada for llamada in transporte.llamadas if llamada[0] == "GET"]
    assert gets[0][1].endswith("/v1/ingest/inventory")
    assert gets[0][3] == {"limit": 100, "cursor": "pagina-1"}
    guardados = [args[0] for sql, args in conn.escrituras if "inventory_cursor" in sql]
    assert guardados == ["pagina-2"]


async def test_el_inventario_en_un_nivel_no_habilitado_se_expulsa() -> None:
    transporte = _Transporte(
        {
            "items": [
                {
                    "external_ref": str(RADICADO),
                    "type": "record",
                    "sensitivity": "classified",
                    "version": "1",
                    "updated_at": "2026-10-27T14:05:09Z",
                }
            ],
            "next_cursor": None,
        }
    )
    conn = _Conn(estados={RADICADO: {"nivel_seguridad": 1, "status": "radicado"}})
    resultado = await reconciliar(conn, "demo", _cliente(transporte))
    assert resultado.borrados == 1
    [cuerpo] = _posts(transporte)
    assert _borrado(cuerpo)["reason"] == "reclassified"
    assert _borrado(cuerpo)["external_ref"] == str(RADICADO)


async def test_un_anulado_del_inventario_se_borra() -> None:
    transporte = _Transporte(
        {
            "items": [
                {
                    "external_ref": str(RADICADO),
                    "type": "record",
                    "sensitivity": "public",
                    "version": "1",
                    "updated_at": "2026-10-27T14:05:09Z",
                }
            ],
            "next_cursor": None,
        }
    )
    conn = _Conn(estados={RADICADO: {"nivel_seguridad": 1, "status": "anulado"}})
    await reconciliar(conn, "demo", _cliente(transporte))
    [cuerpo] = _posts(transporte)
    assert _borrado(cuerpo)["reason"] == "annulled"
    assert _borrado(cuerpo)["include_children"] is True


async def test_un_anexo_de_un_anulado_borra_al_padre() -> None:
    transporte = _Transporte(
        {
            "items": [
                {
                    "external_ref": str(ANEXO),
                    "type": "attachment",
                    "sensitivity": "public",
                    "version": "1",
                    "updated_at": "2026-10-27T14:05:09Z",
                }
            ],
            "next_cursor": None,
        }
    )
    conn = _Conn(
        anexos={ANEXO: RADICADO},
        estados={RADICADO: {"nivel_seguridad": 1, "status": "anulado"}},
    )
    await reconciliar(conn, "demo", _cliente(transporte))
    [cuerpo] = _posts(transporte)
    assert _borrado(cuerpo)["external_ref"] == str(RADICADO)
    assert _borrado(cuerpo)["reason"] == "annulled"


async def test_si_el_inventario_falla_el_cursor_no_avanza() -> None:
    transporte = _Transporte(error=TimeoutError("plazo"))
    conn = _Conn(cursor="pagina-1")
    resultado = await reconciliar(conn, "demo", _cliente(transporte))
    assert resultado.borrados == 0
    assert not any("inventory_cursor" in sql for sql, _args in conn.escrituras)


async def test_suspendido_no_consulta_el_inventario() -> None:
    transporte = _Transporte()
    resultado = await reconciliar(conn := _Conn(suspendido=True), "demo", _cliente(transporte))
    assert resultado.borrados == 0
    assert transporte.llamadas == []
    assert not any("reconciliacion:libro" in sql for sql in conn.consultas)
