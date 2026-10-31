# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Punto de paso: nada sale si el nivel vivo no está habilitado (ADR-045 §3).

El transporte de estas pruebas falla si lo llaman cuando no debía. La carrera
se prueba así: el cuerpo que pide el envío dice `public` y la base, releída en
el envío, dice nivel 2. No hay llamada.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.clients.ppi import ClientePpi
from app.core.clearance import puede_salir
from app.core.paso import enviar

RADICADO = uuid.UUID("00000000-0000-4000-8000-0000000000b2")


def _cuerpo(sensibilidad: str = "public") -> dict[str, Any]:
    return {
        "items": [
            {
                "type": "record",
                "record": {"external_ref": str(RADICADO), "sensitivity": sensibilidad},
            }
        ]
    }


class _Conn:
    def __init__(self, **datos: Any) -> None:
        self.datos = datos
        self.sql: list[str] = []

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.sql.append(sql)
        if "public.tenants" in sql:
            if self.datos.get("sin_entidad"):
                return None
            return {
                "asistente_habilitado": self.datos["bandera"],
                "intelligence_provider": self.datos["proveedor"],
                "intelligence_levels": self.datos["niveles"],
            }
        if "intelligence_state" in sql:
            return {"send_suspended": self.datos["suspendido"]}
        if "radicados" in sql:
            if self.datos.get("nivel") is None:
                return None
            return {"nivel_seguridad": self.datos["nivel"]}
        raise AssertionError(sql)


class _Prohibido:
    async def request(self, method: str, url: str, *, json: dict[str, Any]) -> Any:
        raise AssertionError(f"el punto de paso llamó al proveedor: {method} {url}")


class _Registra:
    def __init__(self) -> None:
        self.llamadas: list[tuple[str, str, dict[str, Any]]] = []

    async def request(self, method: str, url: str, *, json: dict[str, Any]) -> Any:
        for item in json["items"]:
            for clave in ("record", "attachment"):
                objeto = item.get(clave)
                if isinstance(objeto, dict) and objeto.get("sensitivity") != "public":
                    raise AssertionError(objeto.get("sensitivity"))
        self.llamadas.append((method, url, json))


def _base(**cambios: Any) -> dict[str, Any]:
    datos: dict[str, Any] = {
        "bandera": True,
        "proveedor": "ppi",
        "niveles": [1],
        "suspendido": False,
        "nivel": 1,
    }
    datos.update(cambios)
    return datos


def test_puede_salir_solo_con_interruptor_nivel_y_envio_activo() -> None:
    assert puede_salir(
        bandera=True, proveedor="ppi", niveles_habilitados=[1], suspendido=False, nivel_vivo=1
    )
    assert not puede_salir(
        bandera=False,
        proveedor="ppi",
        niveles_habilitados=[1, 2, 3],
        suspendido=False,
        nivel_vivo=1,
    )
    assert not puede_salir(
        bandera=True, proveedor="local", niveles_habilitados=[1], suspendido=False, nivel_vivo=1
    )
    assert not puede_salir(
        bandera=True, proveedor="ppi", niveles_habilitados=[1], suspendido=True, nivel_vivo=1
    )
    assert not puede_salir(
        bandera=True, proveedor="ppi", niveles_habilitados=[1], suspendido=False, nivel_vivo=2
    )
    assert not puede_salir(
        bandera=True, proveedor="ppi", niveles_habilitados=[1], suspendido=False, nivel_vivo=None
    )


@pytest.mark.asyncio
async def test_un_nivel_habilitado_sale_con_la_sensibilidad_releida() -> None:
    transporte = _Registra()
    cliente = ClientePpi(transporte, base_url="http://ppi.internal")
    decision = await enviar(_Conn(**_base()), "demo", RADICADO, cliente, _cuerpo("classified"))
    assert decision.sale is True and decision.motivo == "ok"
    assert len(transporte.llamadas) == 1
    metodo, url, cuerpo = transporte.llamadas[0]
    assert metodo == "POST" and url == "http://ppi.internal/v1/ingest"
    assert cuerpo["items"][0]["record"]["sensitivity"] == "public"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("nivel", "sensibilidad"),
    [(2, "reserved"), (3, "classified")],
)
async def test_el_nivel_habilitado_fija_su_sensibilidad(nivel: int, sensibilidad: str) -> None:
    class _Copia:
        cuerpo: dict[str, Any] | None = None

        async def request(self, method: str, url: str, *, json: dict[str, Any]) -> None:
            self.cuerpo = json

    transporte = _Copia()
    cliente = ClientePpi(transporte, base_url="http://ppi.internal")
    decision = await enviar(
        _Conn(**_base(nivel=nivel, niveles=[1, 2, 3])),
        "demo",
        RADICADO,
        cliente,
        _cuerpo("public"),
    )
    assert decision.sale is True
    assert transporte.cuerpo is not None
    assert transporte.cuerpo["items"][0]["record"]["sensitivity"] == sensibilidad


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cambios", "motivo"),
    [
        ({"bandera": False}, "bandera"),
        ({"proveedor": "local"}, "local"),
        ({"suspendido": True}, "suspendido"),
        ({"nivel": 2}, "nivel"),
        ({"nivel": None}, "ausente"),
        ({"sin_entidad": True}, "registro"),
    ],
)
async def test_sin_permiso_de_salida_no_hay_llamada(cambios: dict[str, Any], motivo: str) -> None:
    cliente = ClientePpi(_Prohibido(), base_url="http://ppi.internal")
    decision = await enviar(_Conn(**_base(**cambios)), "demo", RADICADO, cliente, _cuerpo())
    assert decision.sale is False
    assert decision.motivo == motivo


@pytest.mark.asyncio
async def test_la_reclasificacion_entre_el_evento_y_el_envio_no_sale() -> None:
    """El cuerpo pide `public`. La relectura dice nivel 2, y 2 no está habilitado."""
    conn = _Conn(**_base(nivel=2))
    cliente = ClientePpi(_Prohibido(), base_url="http://ppi.internal")
    decision = await enviar(conn, "demo", RADICADO, cliente, _cuerpo("public"))
    assert decision.sale is False and decision.motivo == "nivel"
    assert any("anulado" in sql for sql in conn.sql)


def test_sin_url_el_cliente_no_tiene_destino() -> None:
    with pytest.raises(RuntimeError):
        ClientePpi(_Prohibido(), base_url="  ")
