# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Marco de alcance: que diga la verdad, y que diga la vía.

Lo que se fija aquí no es formato: es que el marco (a) no esconda un límite,
(b) no invente una precisión que el sistema no tiene, y (c) ofrezca la salida
cuando la hay. Un marco que solo niega deja a la persona igual de atascada que
el filtro silencioso que vino a sustituir.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.identity import Identity
from app.services.scope import build_scope


def _gateway(*, clearance: int | None = 2, contexto: dict | None = None) -> httpx.AsyncClient:
    """Doble del gateway: responde clearance y contexto, falla en lo demás."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/clearance":
            if clearance is None:
                return httpx.Response(503)
            return httpx.Response(
                200,
                json={
                    "user_id": "u1",
                    "max_level": clearance,
                    "accessible_levels": list(range(1, clearance + 1)),
                },
            )
        if request.url.path == "/api/v1/auth/context":
            return httpx.Response(
                200,
                json=contexto
                or {"user_id": "u1", "active_depe_id": 120, "available_depts": [{"id": 1}]},
            )
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gw")


def _identidad(permisos: set[str], niveles: dict[str, int]) -> Identity:
    return Identity(
        user_id="u1", tenant_slug="demo", permissions=permisos, levels=niveles, is_root=False
    )


async def _marco(permisos: set[str], niveles: dict[str, int], **kw) -> dict:
    async with _gateway(**kw) as cli:
        return await build_scope(
            cli, _identidad(permisos, niveles), authorization="Bearer t", tenant_slug="demo"
        )


@pytest.mark.asyncio
async def test_quien_puede_radicar_lo_ve_en_lo_que_puede_hacer() -> None:
    marco = await _marco({"PERM_RADI"}, {"PERM_RADI": 3})
    operaciones = [e["operacion"] for e in marco["puede_hacer"]]
    assert any("Radicar" in o for o in operaciones)


@pytest.mark.asyncio
async def test_quien_no_puede_radicar_recibe_el_motivo_y_la_via() -> None:
    """El caso que motivó todo el bloque: negar sin más no desatasca a nadie."""
    marco = await _marco(set(), {})
    fuera = {e["operacion"]: e for e in marco["no_puede"]}
    radicar = next(e for o, e in fuera.items() if "Radicar" in o)

    assert "PERM_RADI" in radicar["motivo"]
    assert radicar["via_alternativa"], "una negativa sin vía deja a la persona igual de atascada"
    assert "correspondencia" in radicar["via_alternativa"]


@pytest.mark.asyncio
async def test_el_permiso_sin_nivel_suficiente_se_distingue_del_permiso_ausente() -> None:
    """«No lo tiene» y «lo tiene solo para consultar» mandan a puertas distintas.

    Confundirlas hace que la persona pida el permiso que ya tiene.
    """
    from app.tools.catalog import CATALOG, Tool

    escritura = next(t for t in CATALOG if t.write)
    exigente = Tool(**{**escritura.__dict__, "min_crud": 3})

    marco_sin = await _marco(set(), {})
    sin_permiso = next(e for e in marco_sin["no_puede"] if e["herramienta"] == escritura.name)
    assert "que su cuenta no tiene" in sin_permiso["motivo"]

    from app.services.scope import _motivo

    con_nivel_bajo = _motivo(exigente, _identidad({escritura.permission}, {escritura.permission: 1}))
    assert "solo con nivel de consulta" in con_nivel_bajo


@pytest.mark.asyncio
async def test_el_marco_declara_lo_que_el_sistema_no_distingue() -> None:
    """La honestidad del marco: no prometer precisión que no existe.

    `PERM_RADI_ENTRADA`/`_INTERNO` están en el catálogo de permisos y ningún gate
    los usa; y no hay filtro por dependencia en las lecturas. Decir «solo
    entradas» o «solo su dependencia» sería inventarse un control.
    """
    marco = await _marco({"PERM_RADI"}, {"PERM_RADI": 3})
    limites = " ".join(marco["limites_declarados"])
    assert "no distingue el tipo de documento" in limites
    assert "no está acotado por su dependencia" in limites


@pytest.mark.asyncio
async def test_el_nivel_de_acceso_se_explica_en_castellano() -> None:
    marco = await _marco({"PERM_RADI"}, {"PERM_RADI": 3}, clearance=2)
    assert marco["acceso"]["nivel_maximo"] == 2
    assert marco["acceso"]["etiqueta"] == "Reservada"
    assert "no revela que exista" in marco["acceso"]["explicacion"]


@pytest.mark.asyncio
async def test_si_falla_una_lectura_complementaria_el_marco_se_entrega_igual() -> None:
    """Saber qué puedes hacer es útil aunque no se sepa tu nivel de acceso.

    Lo que no sería admisible es rellenar el hueco con un valor inventado.
    """
    marco = await _marco({"PERM_RADI"}, {"PERM_RADI": 3}, clearance=None)
    assert "acceso" not in marco
    assert marco["puede_hacer"], "el resto del marco sí debe entregarse"
