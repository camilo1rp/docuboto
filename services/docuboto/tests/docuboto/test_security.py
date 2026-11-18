# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lo que el api-gateway hacía en cada petición, ahora en el proceso (ADR-047).

Sin gateway delante, ninguna cabecera de identidad escrita por el cliente puede
llegar a un router: `UsuarioActualMiddleware` las borra y repone el slug del JWT.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from orpycamcp_common.usuario import UsuarioActualMiddleware

from docuboto.core.security import SecurityHeadersMiddleware, sanitize_request_id


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/eco")
    async def eco(request: Request) -> dict:
        h = request.headers
        return {
            k: h.get(k)
            for k in ("x-user-id", "x-username", "x-canal", "x-tenant-slug", "x-request-id")
        }

    app.add_middleware(UsuarioActualMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    return app


def test_las_cabeceras_de_identidad_del_cliente_no_llegan_al_router() -> None:
    r = TestClient(_app()).get(
        "/api/v1/eco",
        headers={
            "X-User-Id": "00000000-0000-0000-0000-000000000001",
            "X-Username": "admin",
            "X-Canal": "ui",
            "X-Tenant-Slug": "otra",
        },
    )
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo.pop("x-request-id")
    assert set(cuerpo.values()) == {None}


def test_la_respuesta_lleva_las_cabeceras_de_endurecimiento() -> None:
    r = TestClient(_app()).get("/api/v1/eco")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_el_request_id_sano_se_conserva_y_el_malo_se_sustituye() -> None:
    cliente = TestClient(_app())
    r = cliente.get("/api/v1/eco", headers={"X-Request-Id": "ssr-123.a"})
    assert r.headers["x-request-id"] == "ssr-123.a" == r.json()["x-request-id"]
    r = cliente.get("/api/v1/eco", headers={"X-Request-Id": "a\nb"})
    assert r.headers["x-request-id"] != "a\nb"
    assert sanitize_request_id("x" * 65) is None


def test_la_app_monta_el_endurecimiento_por_fuera() -> None:
    from docuboto.main import app

    clases = [m.cls for m in app.user_middleware]
    assert clases[0] is SecurityHeadersMiddleware, clases
    assert UsuarioActualMiddleware in clases
