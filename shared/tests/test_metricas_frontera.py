# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La frontera de `/metrics` (C18, ADR-041): qué servicios lo exponen y qué no pueden decir.

Una frontera se escribe por ADMISIÓN: el conjunto de servicios con `metrics.py` es
EXACTAMENTE el esperado, cada uno declara solo `GET /metrics`, el gateway no lo
enruta y ninguna familia puede llevar identidad.

Mutaciones que deben caer (descritas, no ejecutadas):

* añadir `metrics.py` a un séptimo servicio → `test_el_conjunto_de_servicios_con_metrics_es_el_admitido`;
* una segunda ruta (o un POST) en un `metrics.py` → `test_cada_metrics_declara_solo_get_metrics`
  (de eso depende que `test_toda_ruta_lleva_gate` lo pueda eximir);
* una etiqueta `tenant`/`slug`/`user_id` en `FAMILIAS` → `test_ninguna_familia_lleva_identidad`;
* una ruta `/metrics` en el gateway → `test_el_gateway_no_enruta_metrics`;
* un servicio que escribe `evento_dead_letter` sin señal `dlq` (o al revés) →
  `test_la_senal_dlq_la_tienen_los_que_escriben_la_dlq`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from orpycamcp_common.metricas import ETIQUETAS_PROHIBIDAS, FAMILIAS

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"

#: Señal(es) que expone cada servicio. Lista de admisión: añadir uno exige tocar esto.
ADMITIDOS = {
    "auth-service": {"audit_chain"},
    "archive-service": {"worm"},
    "workflow-service": {"dlq"},
    "signature-service": {"dlq"},
    "notification-service": {"dlq"},
    "knowledge-service": {"dlq"},
}


def _con_metrics() -> dict[str, Path]:
    return {p.parts[-4]: p for p in sorted(SERVICIOS.glob("*/app/routers/metrics.py"))}


def test_el_conjunto_de_servicios_con_metrics_es_el_admitido() -> None:
    encontrados = _con_metrics()
    assert len(encontrados) >= 6, "censo de metrics.py vacío: guardián ciego"  # anti-vacuidad
    assert set(encontrados) == set(ADMITIDOS), (
        f"servicios con /metrics: {sorted(encontrados)}; admitidos: {sorted(ADMITIDOS)}"
    )


def _rutas(fuente: str) -> list[tuple[str, str]]:
    """[(método, ruta)] de los decoradores `@<router>.<método>("…")`."""
    out: list[tuple[str, str]] = []
    for nodo in ast.walk(ast.parse(fuente)):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in nodo.decorator_list:
                if (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr in {"get", "post", "put", "patch", "delete", "api_route", "route"}
                ):
                    ruta = d.args[0].value if d.args and isinstance(d.args[0], ast.Constant) else "?"
                    out.append((d.func.attr, str(ruta)))
    return out


def test_cada_metrics_declara_solo_get_metrics() -> None:
    """Condición de la exención de `test_toda_ruta_lleva_gate`: sin gate, una sola ruta."""
    for svc, ruta in _con_metrics().items():
        assert _rutas(ruta.read_text(encoding="utf-8")) == [("get", "/metrics")], svc
        assert "include_in_schema=False" in ruta.read_text(encoding="utf-8"), svc


def test_control_positivo_el_detector_de_rutas_ve_una_segunda_ruta() -> None:
    sintetico = (
        "@router.get('/metrics')\nasync def a(): ...\n"
        "@router.post('/otra')\nasync def b(): ...\n"
    )
    assert _rutas(sintetico) == [("get", "/metrics"), ("post", "/otra")]


def test_cada_servicio_expone_la_senal_que_le_toca() -> None:
    for svc, ruta in _con_metrics().items():
        texto = ruta.read_text(encoding="utf-8")
        esperadas = ADMITIDOS[svc]
        vistas = set(re.findall(r'metricas\.Senal\(\s*"(\w+)"', texto))
        if "dlq" in esperadas:
            assert "metricas.senal_dlq(" in texto, svc
            vistas.add("dlq")
        assert vistas == esperadas, f"{svc}: señales {vistas} != {esperadas}"


def test_la_senal_dlq_la_tienen_los_que_escriben_la_dlq() -> None:
    escriben = {p.parts[-4] for p in SERVICIOS.glob("*/app/repositories/dead_letter_repository.py")}
    assert len(escriben) >= 4, f"censo de la DLQ vacío o incompleto: {sorted(escriben)}"
    con_senal = {s for s, r in _con_metrics().items() if "metricas.senal_dlq(" in r.read_text(encoding="utf-8")}
    assert con_senal == escriben


def test_el_gateway_no_enruta_metrics() -> None:
    fuentes = list((SERVICIOS / "api-gateway" / "app").rglob("*.py"))
    assert fuentes, "no se encontró el gateway"
    for f in fuentes:
        assert "/metrics" not in f.read_text(encoding="utf-8"), f"{f}: el gateway no debe tocar /metrics"
    proxy = (SERVICIOS / "api-gateway" / "app" / "routers" / "proxy.py").read_text(encoding="utf-8")
    # su única ruta de reenvío cuelga de /api/v1/: nada de /metrics se alcanza por ahí
    assert '"/api/v1/{path:path}"' in proxy


def test_ninguna_familia_lleva_identidad() -> None:
    assert FAMILIAS and ETIQUETAS_PROHIBIDAS
    for nombre, fam in FAMILIAS.items():
        for sospechoso in ETIQUETAS_PROHIBIDAS:
            assert sospechoso not in nombre, f"{nombre}: el nombre lleva '{sospechoso}'"
        for etiqueta, valores in fam.etiquetas.items():
            assert valores, f"{nombre}.{etiqueta}: sin conjunto cerrado de valores"
            assert etiqueta != "id" and not etiqueta.endswith("_id"), f"{nombre}.{etiqueta}"
            for sospechoso in ETIQUETAS_PROHIBIDAS:
                assert sospechoso not in etiqueta, f"{nombre}.{etiqueta}"
            assert all(len(v) <= 32 and re.fullmatch(r"[a-z_]+", v) for v in valores), (
                f"{nombre}.{etiqueta}: un valor no parece un enumerado cerrado"
            )


def test_control_positivo_el_detector_de_identidad_vería_una_etiqueta_tenant() -> None:
    # el criterio de arriba, aplicado a una familia inventada, la rechazaría
    assert any(s in "tenant_slug" for s in ETIQUETAS_PROHIBIDAS)
    assert "user_id".endswith("_id")
