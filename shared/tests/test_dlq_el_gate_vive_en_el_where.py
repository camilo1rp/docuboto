# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""AREA-10-08: el no-read-up de la DLQ compartida vive en UN solo sitio, dentro del WHERE.

Cuatro servicios copiaban `_extract_ref`/`resolve_deadletter_level[s]` y filtraban en
Python sobre un tope de 1000 candidatas. Estos guardianes impiden que la copia vuelva.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from orpycamcp_common import deadletter

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"
DLQ = ("workflow-service", "signature-service", "notification-service")

pytestmark = pytest.mark.skipif(
    not SERVICIOS.is_dir(), reason="los directorios de servicios no están montados"
)

_PROHIBIDO = re.compile(
    r"resolve_deadletter_level|assert_can_read_deadletter|list_scoped_for_clearance"
    r"|DEADLETTER_CANDIDATE_CAP|_extract_ref|X-Truncated"
)


@pytest.mark.parametrize("svc", DLQ)
def test_ninguna_copia_del_filtro_en_python(svc: str) -> None:
    for fuente in (SERVICIOS / svc / "app").rglob("*.py"):
        assert not _PROHIBIDO.search(fuente.read_text()), (
            f"{fuente.relative_to(RAIZ)} vuelve a filtrar la DLQ por clearance fuera de "
            "orpycamcp_common.deadletter"
        )


@pytest.mark.parametrize("svc", DLQ)
def test_el_repositorio_usa_la_fuente_unica(svc: str) -> None:
    texto = (SERVICIOS / svc / "app" / "repositories" / "dead_letter_repository.py").read_text()
    assert "from orpycamcp_common import deadletter" in texto
    # Ninguna lectura de una fila de la DLQ por id se hace sin pasar por el gate.
    assert not re.search(r"FROM evento_dead_letter\s+WHERE\s+event_id", texto)


def test_el_constructor_siempre_anade_el_gate() -> None:
    sql = deadletter.visible_deadletter_sql(
        "d.*", schema="tenant_x", where="d.event_id = $1", clearance_ph="$2"
    )
    assert "<= $2" in sql and "LEFT JOIN LATERAL" in sql
    assert "GREATEST(rv.nivel" in sql
    assert '"tenant_x".radicados' in sql and "FROM radicados" not in sql, "radicados va calificada"


def test_el_indice_del_listado_es_identico_en_los_cuatro() -> None:
    cuerpos = set()
    for svc in DLQ:
        hallados = [
            f
            for f in (SERVICIOS / svc / "migrations" / "tenant").glob("*.sql")
            if "ix_edl_keyset" in f.read_text()
        ]
        assert len(hallados) == 1, f"{svc}: debe haber UNA migración del índice ix_edl_keyset"
        sentencia = re.search(
            r"CREATE INDEX IF NOT EXISTS ix_edl_keyset.*?;", hallados[0].read_text(), re.S
        )
        assert sentencia
        cuerpos.add(re.sub(r"\s+", " ", sentencia.group(0)))
    assert len(cuerpos) == 1, "el índice compartido debe ser idéntico en los cuatro servicios"


# ---------------------------------------------------------------------------
# Toda mutación de la DLQ pasa por el gate (lista por ADMISIÓN)
# ---------------------------------------------------------------------------
#
# El gate de clearance protege las LECTURAS (`get_visible`). Una mutación escrita
# aparte —un `UPDATE ... WHERE event_id = $1` en otro método— lo esquivaría: el que no
# puede ver la fila podría descartarla. Por eso el único UPDATE admitido es
# `transition_status` (que el router llama tras `get_for_update`, que sí lleva el gate) y
# el único DELETE es `purge_terminal` (retención por tenant+grupo, solo terminales). Lo
# que no esté en la lista se rechaza: una función nueva con su propio UPDATE/DELETE obliga
# a decidir aquí si es legítima.

_ADMITIDOS = {"transition_status", "purge_terminal"}
_MUTA = re.compile(r"\b(UPDATE|DELETE\s+FROM)\s+evento_dead_letter\b", re.I)


def _mutaciones_fuera_de_lista(fuente: str) -> list[str]:
    import ast

    hallazgos: list[str] = []
    arbol = ast.parse(fuente)
    for fn in ast.walk(arbol):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for nodo in ast.walk(fn):
            if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str) and _MUTA.search(nodo.value):
                if fn.name not in _ADMITIDOS:
                    hallazgos.append(fn.name)
    return sorted(set(hallazgos))


@pytest.mark.parametrize("svc", DLQ)
def test_solo_transition_status_y_purge_mutan_la_dlq(svc: str) -> None:
    for fuente in (SERVICIOS / svc / "app").rglob("*.py"):
        fuera = _mutaciones_fuera_de_lista(fuente.read_text())
        assert not fuera, (
            f"{fuente.relative_to(RAIZ)}: {fuera} mutan evento_dead_letter fuera de "
            f"{sorted(_ADMITIDOS)}; un UPDATE/DELETE propio esquiva el gate de clearance"
        )


@pytest.mark.parametrize("svc", DLQ)
def test_quien_transiciona_antes_bloquea_con_el_gate(svc: str) -> None:
    """Todo llamador de `transition_status` pide antes `get_for_update` (lectura con gate)."""
    import ast

    visto = 0
    for fuente in (SERVICIOS / svc / "app" / "routers").rglob("*.py"):
        for fn in ast.walk(ast.parse(fuente.read_text())):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            llamadas = [
                n.func.attr
                for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            ]
            if "transition_status" in llamadas:
                visto += 1
                assert "get_for_update" in llamadas, f"{fuente.name}::{fn.name}"
    assert visto >= 2, "anti-vacuidad: replay y descarte"


def test_el_detector_ve_una_mutacion_ajena_a_la_lista() -> None:
    """Mutación: un método nuevo con su propio UPDATE debe ser detectado."""
    rogue = (
        "class R:\n"
        "    async def forzar(self, c):\n"
        "        await c.execute(\"UPDATE evento_dead_letter SET status = 'resolved' WHERE event_id = $1\")\n"
        "    async def transition_status(self, c):\n"
        "        await c.execute(\"UPDATE evento_dead_letter SET status = $1\")\n"
        "    async def purge_terminal(self, c):\n"
        "        await c.execute(\"DELETE FROM evento_dead_letter WHERE x\")\n"
    )
    assert _mutaciones_fuera_de_lista(rogue) == ["forzar"]
