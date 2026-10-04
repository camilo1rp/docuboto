# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La matriz de CI se deriva del árbol, no de una lista a mano (AREA-13-02, AREA-13-01).

`whisper-service` tiene tests (entre ellos `test_transcribir_no_esta_abierto_a_la_red`,
que cierra una puerta) y no figuraba en la matriz de `test-unit`; `signature-service`
tenía `tests/integration/` versionado y no figuraba en la de `test-integration`.
Una lista escrita a mano ya dejó fuera tres de once servicios del gate de tipos:
es el mismo modo de fallo.

Vigila, parseando `.gitlab-ci.yml`:

* cada `services/*/` con `tests/` está en `test-unit.parallel.matrix`;
* cada `services/*/` con `tests/integration/test_*.py` está en
  `test-integration.parallel.matrix`, o figura en `FUERA_DE_INTEGRACION` con su
  motivo (y esa excepción no caduca: si entra a la matriz, se quita de aquí);
* el job `test-security-smoke` corre automáticamente (sin `when: manual`) y cubre
  los gates de ruta interna.
"""

from __future__ import annotations

from typing import Any

from tests._compose import GITLAB_CI, RAIZ, cargar

#: Servicios con `tests/integration/` que NO están en la matriz, y por qué. Declarados,
#: no aprobados.
FUERA_DE_INTEGRACION = {
    "notification-service": "su tier no trae bootstrap propio de public.audit_log ni se ha validado en un Postgres virgen de CI",
    "tenant-service": "un único test (rol de aplicación) que exige el rol orpycamcp_app y su credencial, ausentes en un Postgres virgen de CI",
}


def _matriz(job: str) -> set[str]:
    cfg: dict[str, Any] = cargar(GITLAB_CI)[job]
    servicios: set[str] = set()
    for entrada in cfg["parallel"]["matrix"]:
        valor = entrada["SERVICE"]
        servicios |= set(valor) if isinstance(valor, list) else {valor}
    return servicios


def _con_tests() -> set[str]:
    return {p.parent.name for p in (RAIZ / "services").glob("*/tests") if p.is_dir()}


def _con_integracion() -> set[str]:
    return {p.parents[2].name for p in (RAIZ / "services").glob("*/tests/integration/test_*.py")}


def test_se_encontraron_servicios_con_tests() -> None:
    assert len(_con_tests()) >= 11, "el censo de servicios con tests se quedó corto: guardián ciego"
    assert len(_con_integracion()) >= 8


def test_toda_suite_unitaria_esta_en_la_matriz() -> None:
    faltan = sorted(_con_tests() - _matriz("test-unit"))
    assert not faltan, (
        f"servicios con tests/ fuera de test-unit.parallel.matrix (nunca corren en CI): {faltan}"
    )


def test_toda_suite_de_integracion_esta_en_la_matriz_o_declarada() -> None:
    en_matriz = _matriz("test-integration")
    faltan = sorted(_con_integracion() - en_matriz - FUERA_DE_INTEGRACION.keys())
    assert not faltan, (
        f"servicios con tests/integration/ ni en test-integration.parallel.matrix ni en "
        f"FUERA_DE_INTEGRACION (salen `skipped` en CI): {faltan}"
    )


def test_la_lista_de_integracion_fuera_no_caduca() -> None:
    en_matriz = _matriz("test-integration")
    caducas = sorted(k for k in FUERA_DE_INTEGRACION if k in en_matriz or k not in _con_integracion())
    assert not caducas, f"ya entraron a la matriz (o dejaron de tener tests): quítalos: {caducas}"


def test_la_matriz_no_nombra_servicios_inexistentes() -> None:
    existentes = {p.name for p in (RAIZ / "services").iterdir() if p.is_dir()}
    for job in ("test-unit", "test-integration"):
        assert _matriz(job) <= existentes, f"{job} nombra servicios que no existen"


def test_el_humo_de_seguridad_corre_solo_y_cubre_las_puertas_internas() -> None:
    job = cargar(GITLAB_CI)["test-security-smoke"]
    assert "rules" not in job or all(r.get("when") != "manual" for r in job["rules"]), (
        "el humo de seguridad no puede depender de un clic"
    )
    entradas = {e["SERVICE"]: e["TESTS"] for e in job["parallel"]["matrix"]}
    assert {"api-gateway", "storage-service", "whisper-service"} <= set(entradas)
    assert "test_transcribir_no_esta_abierto_a_la_red" in entradas["whisper-service"]
    assert "test_internal_token_gate" in entradas["storage-service"]
    for svc, tests in entradas.items():
        for token in tests.split():
            if token.startswith("tests/"):
                assert (RAIZ / "services" / svc / token).is_file(), f"{svc}: {token} no existe"
