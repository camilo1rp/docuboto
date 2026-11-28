# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un `/health` y un `/metrics` para el proceso (ADR-047).

Lo que vigilaban las sondas de cada dominio sigue en la única: el consumidor
muerto y su DLQ se ven, el almacén y SMTP se informan sin decidir el
503, y `/metrics` no responde a nada que haya pasado por un proxy.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from docuboto.routers import health, metrics


class _Pool:
    def __init__(self, conn: object) -> None:
        self._conn = conn

    def acquire(self) -> _Pool:
        return self

    async def __aenter__(self) -> object:
        return self._conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _app() -> TestClient:
    app = FastAPI()
    app.include_router(health.router)
    app.include_router(metrics.router)
    return TestClient(app)


def _informativos_caidos():  # noqa: ANN202
    fallo = AsyncMock(side_effect=RuntimeError("caído"))
    return (
        patch.object(health, "_ping_storage", fallo),
        patch.object(health, "_ping_smtp", fallo),
        patch.object(health, "_audit_particiones", AsyncMock(return_value="degraded")),
    )


def test_health_no_tiene_dependencias() -> None:
    r = _app().get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_ready_lo_decide_solo_la_base() -> None:
    conn = MagicMock(fetchval=AsyncMock(return_value=1))
    p = _informativos_caidos()
    with patch.object(health, "get_pool", return_value=_Pool(conn)), p[0], p[1], p[2]:
        r = _app().get("/health/ready")
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    # ADR-049: eventos, colas y cerrojos viven en la base; no hay segundo almacén.
    assert set(cuerpo["checks"]) == {"database", "storage", "smtp"}
    # Almacén y SMTP caídos se informan, no sacan al proceso de servicio.
    assert cuerpo["checks"]["storage"] != "ok"
    assert cuerpo["checks"]["smtp"] != "ok"
    assert cuerpo["degraded"] == ["audit_particiones"]


def test_ready_sin_base_es_503() -> None:
    conn = MagicMock(fetchval=AsyncMock(side_effect=OSError("sin base")))
    p = _informativos_caidos()
    with patch.object(health, "get_pool", return_value=_Pool(conn)), p[0], p[1], p[2]:
        r = _app().get("/health/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "not_ready"


def test_el_consumidor_muerto_y_su_dlq_se_ven() -> None:
    conn = MagicMock(fetchval=AsyncMock(return_value=1))
    estados = {"workflow": {"corriendo": False}, "signature": {"corriendo": True}}
    pendientes = AsyncMock(side_effect=lambda grupo: 3 if grupo == health.GRUPO_WORKFLOW else 0)
    p = _informativos_caidos()
    with (
        patch.object(health, "get_pool", return_value=_Pool(conn)),
        patch.object(health.events, "consumer_status", return_value=estados),
        patch.object(health, "_dlq_pendientes", pendientes),
        p[0], p[1], p[2],
    ):
        r = _app().get("/health/ready")
    assert r.status_code == 200
    consumidores = r.json()["consumers"]
    assert consumidores["workflow"]["consumer"] == "reiniciando"
    assert consumidores["workflow"]["dead_letter"] == "degraded"
    assert consumidores["workflow"]["detalle"]["dead_letter_pendientes"] == 3
    assert consumidores["signature"]["consumer"] == "ok"
    assert consumidores["signature"]["dead_letter"] == "ok"
    assert "notification" not in consumidores  # no arrancó: no se inventa


def test_cada_consumidor_supervisado_tiene_su_grupo() -> None:
    assert set(health.CONSUMERS) == {
        "workflow",
        "notification",
        "signature",
        "intelligence-gateway",
    }


def test_un_conteo_que_falla_no_degrada_el_veredicto() -> None:
    informe = health.health_common.consumer_report({"corriendo": True}, None)
    assert informe["dead_letter"] == "ok"
    assert informe["detalle"]["dead_letter_pendientes"] is None


def test_metrics_con_cabeceras_de_proxy_es_404() -> None:
    with patch.object(metrics, "get_pool", return_value=_Pool(MagicMock())):
        r = _app().get("/metrics", headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 404


def test_metrics_reune_las_senales_de_los_dominios() -> None:
    nombres = {s.nombre for s in metrics._SENALES}
    assert {"dlq", "audit_chain", "worm", "intelligence"} <= nombres
