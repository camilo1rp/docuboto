# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La frontera de `/metrics` (C18, ADR-041, ADR-047): una ruta, sus señales y nada de identidad.

Un proceso, un `/metrics` (`docuboto/routers/metrics.py`). Se escribe por
ADMISIÓN: el conjunto de señales es EXACTAMENTE el esperado, el router declara
solo `GET /metrics`, `edge` no lo publica y ninguna familia lleva identidad.

Mutaciones que deben caer (descritas, no ejecutadas):

* un `metrics.py` en un dominio → `test_solo_la_app_expone_metrics`;
* una segunda ruta (o un POST) → `test_metrics_declara_solo_get_metrics`
  (de eso depende que `test_toda_ruta_lleva_gate` lo pueda eximir);
* una señal nueva sin tocar `ADMITIDAS` → `test_las_senales_son_las_admitidas`;
* un dominio que escribe `evento_dead_letter` sin entrar en la señal `dlq` →
  `test_la_senal_dlq_cubre_a_los_que_escriben_la_dlq`;
* una etiqueta `tenant`/`slug`/`user_id` en `FAMILIAS` → `test_ninguna_familia_lleva_identidad`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from orpycamcp_common.metricas import ETIQUETAS_PROHIBIDAS, FAMILIAS
from tests._compose import APP, glob_src

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"
METRICS = APP / "docuboto" / "routers" / "metrics.py"
NGINX = RAIZ / "infra" / "nginx" / "orpyca.conf.template"

#: Señales del proceso. Lista de admisión: añadir una exige tocar esto.
ADMITIDAS = {"dlq", "audit_chain", "worm", "intelligence"}


def test_solo_la_app_expone_metrics() -> None:
    encontrados = sorted(glob_src(SERVICIOS, "*/app/routers/metrics.py"))
    assert encontrados == [METRICS], encontrados


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


def test_metrics_declara_solo_get_metrics() -> None:
    """Condición de la exención de `test_toda_ruta_lleva_gate`: sin gate, una sola ruta."""
    texto = METRICS.read_text(encoding="utf-8")
    assert _rutas(texto) == [("get", "/metrics")]
    assert "include_in_schema=False" in texto


def test_control_positivo_el_detector_de_rutas_ve_una_segunda_ruta() -> None:
    sintetico = (
        "@router.get('/metrics')\nasync def a(): ...\n"
        "@router.post('/otra')\nasync def b(): ...\n"
    )
    assert _rutas(sintetico) == [("get", "/metrics"), ("post", "/otra")]


def test_las_senales_son_las_admitidas() -> None:
    texto = METRICS.read_text(encoding="utf-8")
    vistas = set(re.findall(r'metricas\.Senal\(\s*"(\w+)"', texto))
    assert vistas == ADMITIDAS, vistas


def test_la_senal_dlq_cubre_a_los_que_escriben_la_dlq() -> None:
    """Escribe la DLQ todo worker cuyo `on_dead_letter` es `dead_letter.persistir` (ADR-049 §4);
    la métrica tiene que importar el grupo de cada uno."""
    escriben = {
        p.parts[-3]
        for p in glob_src(SERVICIOS, "*/app/workers/*.py")
        if "dead_letter.persistir" in p.read_text(encoding="utf-8")
    }
    assert len(escriben) >= 4, f"censo de la DLQ vacío o incompleto: {sorted(escriben)}"
    texto = METRICS.read_text(encoding="utf-8")
    medidos = {
        d
        for d in escriben
        if f"from {d}.core.constants import DLQ_GROUP" in texto
        or f"from {d}.workers.intelligence_consumer import GROUP" in texto
    }
    assert medidos == escriben, f"escriben la DLQ: {sorted(escriben)}; medidos: {sorted(medidos)}"


def test_edge_no_publica_metrics() -> None:
    texto = NGINX.read_text(encoding="utf-8")
    assert "location = /metrics {\n        return 404;" in texto
    assert "location /metrics/ {\n        return 404;" in texto


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
