# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Barrido: toda ruta POST pública que radica pasa por `_exigir_antirrobots` (C16, ADR-038).

El `conftest` sustituye el gate por un no-op en toda suite que NO se llame
`*altcha*`/`*antirrobots*`. Eso es cómodo y tiene un costo: una ruta pública nueva
que radique SIN gate pasaría todas las demás suites, porque ahí el gate no existe
para nadie. Este módulo se llama `*antirrobots*` a propósito (el autouse no lo toca)
y cierra ese hueco de dos formas:

1. **Registro por admisión.** Se enumeran las rutas POST bajo `/api/v1/public/` de la
   app MONTADA y cada una debe estar declarada aquí como «con gate» (y entonces se
   prueba que el gate corre) o como «exenta» con su motivo. Una ruta nueva no
   declarada rompe el test: obliga a decidir, no a olvidar.
2. **Cableado dinámico.** Con el gate sustituido por un centinela que lanza 418, cada
   ruta «con gate» debe devolver 418 con un cuerpo por lo demás válido: el gate se
   alcanzó, antes de política, anexos y consecutivo.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services.ventanilla_service import VentanillaService
from tests.test_ventanilla import (
    BASE,
    _cuerpo,
    _fetchrow_por_consulta,
    _fetchval_por_consulta,
    _multipart,
)

_RAIZ_APP = Path(__file__).resolve().parent.parent / "app"

#: Rutas públicas que radican y DEBEN pasar por el gate -> cómo se les envía un cuerpo válido.
_PQRS = {
    "tipo": "peticion", "nombre": "Ana", "asunto": "A", "descripcion": "D",
    "acepta_tratamiento": True, "politica_version": "2026-01", "altcha": "x" * 40,
}
_CON_GATE: dict[str, Any] = {
    "/api/v1/public/{tenant}/ventanilla": lambda c: c.post(
        BASE, **_multipart(_cuerpo(altcha="x" * 40))
    ),
    "/api/v1/public/{tenant}/pqrs": lambda c: c.post("/api/v1/public/demo/pqrs", json=_PQRS),
}

#: POST públicas que NO radican por la puerta anónima, con el porqué (se revisa a mano).
_EXENTAS = {
    # Webhook del operador postal: autentica con HMAC por tenant, no radica ciudadanía.
    "/api/v1/public/postal/callback/{tenant_slug}": "HMAC del operador; no crea radicados",
}


def _posts_publicas() -> set[str]:
    from app.main import app

    rutas: set[str] = set()
    for incluido in app.routes:
        router = getattr(incluido, "original_router", None)
        if router is None:
            continue
        for route in router.routes:
            path = getattr(route, "path", "")
            if path.startswith("/api/v1/public/") and "POST" in (getattr(route, "methods", None) or ()):
                rutas.add(path)
    return rutas


def test_el_barrido_ve_las_rutas_publicas() -> None:
    """Control positivo: si el enumerador devolviera vacío, el barrido pasaría sin mirar."""
    rutas = _posts_publicas()
    assert "/api/v1/public/{tenant}/ventanilla" in rutas
    assert "/api/v1/public/{tenant}/pqrs" in rutas


def test_toda_post_publica_esta_declarada_con_gate_o_exenta() -> None:
    declaradas = set(_CON_GATE) | set(_EXENTAS)
    sin_declarar = _posts_publicas() - declaradas
    assert not sin_declarar, (
        f"ruta(s) POST pública(s) nueva(s) sin declarar: {sorted(sin_declarar)}. "
        "Si radica, debe pasar por VentanillaService._exigir_antirrobots y entrar en "
        "_CON_GATE; si no radica, entra en _EXENTAS con su motivo."
    )
    obsoletas = declaradas - _posts_publicas()
    assert not obsoletas, f"declaradas que ya no existen (el registro envejeció): {sorted(obsoletas)}"


def test_este_modulo_corre_con_el_gate_real_no_con_el_no_op_del_conftest() -> None:
    """Si el autouse dejara de excluir este módulo, el barrido mediría el no-op."""
    assert VentanillaService._exigir_antirrobots.__name__ == "_exigir_antirrobots"
    assert VentanillaService._exigir_antirrobots.__qualname__.startswith("VentanillaService.")


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", sorted(_CON_GATE))
async def test_cada_ruta_publica_que_radica_alcanza_el_gate(client_with_conn, ruta: str) -> None:
    client, conn = client_with_conn
    conn.fetchval.side_effect = _fetchval_por_consulta("Derecho de petición", "cod")
    conn.fetchrow.side_effect = _fetchrow_por_consulta()
    visto: list[Any] = []

    async def centinela(self: VentanillaService, payload: Any) -> None:
        visto.append(payload)
        raise HTTPException(status_code=418, detail={"error": "centinela", "status": 418})

    with (
        patch.object(VentanillaService, "_exigir_antirrobots", centinela),
        patch("app.services.ventanilla_service.upload_anexo_externo", AsyncMock()) as subir,
        patch("app.services.document_service.DocumentService.create", AsyncMock()) as crear,
    ):
        resp = await _CON_GATE[ruta](client)

    assert resp.status_code == 418, f"{ruta}: el gate no se alcanzó ({resp.status_code} {resp.text})"
    assert len(visto) == 1
    subir.assert_not_awaited()
    crear.assert_not_awaited()


def test_las_exentas_no_radican_por_ventanilla() -> None:
    """Una exención se revisa: el módulo del callback no toca el servicio de ventanilla."""
    fuente = (_RAIZ_APP / "routers" / "postal.py").read_text()
    assert "VentanillaService" not in fuente
    assert "radicar_en_ventanilla" not in fuente


def test_el_gate_tiene_un_unico_punto_de_llamada() -> None:
    """`exigir_reto` solo lo invoca `_exigir_antirrobots`, y este solo `radicar`.

    Dos llamadas serían dos puertas con reglas que pueden divergir (la lección de
    PQRS legado); ninguna llamada sería una puerta abierta.
    """
    llamadas_exigir = [
        p.name for p in _RAIZ_APP.rglob("*.py") if re.search(r"\bexigir_reto\(", p.read_text())
    ]
    assert sorted(llamadas_exigir) == ["antirrobots.py", "ventanilla_service.py"]  # def + 1 uso
    fuente = (_RAIZ_APP / "services" / "ventanilla_service.py").read_text()
    assert len(re.findall(r"await self\._exigir_antirrobots\(", fuente)) == 1
    # y solo `radicar` lo llama: la llamada queda ANTES de subir anexos y de crear.
    cuerpo = fuente.split("async def radicar(", 1)[1]
    assert cuerpo.index("_exigir_antirrobots(") < cuerpo.index("_subir_anexos(")
    assert cuerpo.index("_subir_anexos(") < cuerpo.index("document_service.create(")
