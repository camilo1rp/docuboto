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
muerto y su DLQ se ven, Keycloak, el almacén y SMTP se informan sin decidir el
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


def _app(redis: object | None) -> TestClient:
    app = FastAPI()
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.state.redis = redis
    return TestClient(app)


def _redis_sano() -> MagicMock:
    redis = MagicMock()
    redis.ping = AsyncMock(return_value=True)
    redis.xlen = AsyncMock(return_value=0)
    return redis


def _informativos_caidos():  # noqa: ANN202
    fallo = AsyncMock(side_effect=RuntimeError("caído"))
    return (
        patch.object(health, "_ping_keycloak", fallo),
        patch.object(health, "_ping_storage", fallo),
        patch.object(health, "_ping_smtp", fallo),
        patch.object(health, "_audit_particiones", AsyncMock(return_value="degraded")),
    )


def test_health_no_tiene_dependencias() -> None:
    r = _app(None).get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_ready_lo_deciden_la_base_y_redis() -> None:
    conn = MagicMock(fetchval=AsyncMock(return_value=1))
    p = _informativos_caidos()
    with patch.object(health, "get_pool", return_value=_Pool(conn)), p[0], p[1], p[2], p[3]:
        r = _app(_redis_sano()).get("/health/ready")
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    # Keycloak, almacén y SMTP caídos se informan, no sacan al proceso de servicio.
    assert cuerpo["checks"]["keycloak"] != "ok"
    assert cuerpo["checks"]["storage"] != "ok"
    assert cuerpo["checks"]["smtp"] != "ok"
    assert cuerpo["degraded"] == ["audit_particiones"]


def test_ready_sin_redis_es_503() -> None:
    conn = MagicMock(fetchval=AsyncMock(return_value=1))
    p = _informativos_caidos()
    with patch.object(health, "get_pool", return_value=_Pool(conn)), p[0], p[1], p[2], p[3]:
        r = _app(None).get("/health/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "not_ready"


def test_el_consumidor_muerto_y_su_dlq_se_ven() -> None:
    conn = MagicMock(fetchval=AsyncMock(return_value=1))
    estados = {"workflow": {"corriendo": False}, "signature": {"corriendo": True}}
    informe = AsyncMock(return_value={"dead_letter": "degraded", "detalle": {"dlq": 3}})
    p = _informativos_caidos()
    with (
        patch.object(health, "get_pool", return_value=_Pool(conn)),
        patch.object(health.events, "consumer_status", return_value=estados),
        patch.object(health.health_common, "consumer_report", informe),
        p[0], p[1], p[2], p[3],
    ):
        r = _app(_redis_sano()).get("/health/ready")
    assert r.status_code == 200
    consumidores = r.json()["consumers"]
    assert consumidores["workflow"]["consumer"] == "reiniciando"
    assert consumidores["workflow"]["dead_letter"] == "degraded"
    assert consumidores["signature"]["consumer"] == "ok"
    assert "notification" not in consumidores  # no arrancó: no se inventa


def test_cada_consumidor_supervisado_tiene_su_dlq() -> None:
    assert set(health.CONSUMERS) == {"workflow", "notification", "signature"}


def test_metrics_con_cabeceras_de_proxy_es_404() -> None:
    with patch.object(metrics, "get_pool", return_value=_Pool(MagicMock())):
        r = _app(None).get("/metrics", headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 404


def test_metrics_reune_las_senales_de_los_dominios() -> None:
    nombres = {s.nombre for s in metrics._SENALES}
    assert {"dlq", "audit_chain", "worm", "intelligence"} <= nombres
