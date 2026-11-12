# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El umbral de la auditoría de dependencias es determinista y fail-closed (AREA-13-14).

Cubre `scripts/audit_umbral.py` y que el job `dependency-audit` ya no pueda
volver a ser fail-open: sin `|| true` sobre `pip-audit`, y con un job de
`npm audit` sobre el frontend.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from tests._compose import AUDIT_WORKFLOW, CI_WORKFLOW, RAIZ, disparadores, guion, job

_spec = importlib.util.spec_from_file_location("audit_umbral", RAIZ / "scripts" / "audit_umbral.py")
assert _spec and _spec.loader
audit_umbral = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_umbral)


def _lock(base: Path, svc: str) -> Path:
    d = base / "services" / svc
    d.mkdir(parents=True, exist_ok=True)
    f = d / "requirements.lock"
    f.write_text("x==1\n")
    return f


def _informe(base: Path, svc: str, vulns: list[dict] | None) -> None:
    deps = [{"name": "x", "version": "1", "vulns": vulns or []}]
    (base / f"audit-{svc}-requirements.json").write_text(json.dumps({"dependencies": deps}))


def test_limpio_devuelve_cero(tmp_path: Path) -> None:
    lock = _lock(tmp_path, "a-service")
    _informe(tmp_path, "a-service", None)
    assert audit_umbral.main(["x", str(tmp_path), str(lock)]) == 0


def test_un_informe_ausente_es_fallo_no_cero_paquetes(tmp_path: Path) -> None:
    """El defecto original: pip-audit sale con 2 (paquete inexistente, sin red) y
    el job imprimía «0 paquetes con vulnerabilidades»."""
    lock = _lock(tmp_path, "a-service")
    assert audit_umbral.main(["x", str(tmp_path), str(lock)]) == 1


def test_un_informe_corrupto_es_fallo(tmp_path: Path) -> None:
    lock = _lock(tmp_path, "a-service")
    (tmp_path / "audit-a-service-requirements.json").write_text("{no es json")
    assert audit_umbral.main(["x", str(tmp_path), str(lock)]) == 1


def test_vulnerabilidad_con_arreglo_falla_y_sin_arreglo_no(tmp_path: Path) -> None:
    lock = _lock(tmp_path, "a-service")
    _informe(tmp_path, "a-service", [{"id": "PYSEC-1", "fix_versions": ["2"]}])
    assert audit_umbral.main(["x", str(tmp_path), str(lock)]) == 1
    _informe(tmp_path, "a-service", [{"id": "PYSEC-2", "fix_versions": []}])
    assert audit_umbral.main(["x", str(tmp_path), str(lock)]) == 0


def test_sin_lockfiles_no_es_una_auditoria_limpia(tmp_path: Path) -> None:
    assert audit_umbral.main(["x", str(tmp_path)]) == 2
    errores, _, _ = audit_umbral.evaluar(tmp_path, [])
    assert errores


def _script_del_job(nombre: str) -> str:
    return guion(job(AUDIT_WORKFLOW, nombre))


def test_el_job_de_pip_audit_no_es_fail_open() -> None:
    script = _script_del_job("dependency-audit")
    lineas = [l for l in script.splitlines() if "pip-audit" in l and "-r" in l]
    assert lineas, "el job ya no ejecuta pip-audit sobre los lockfiles"
    assert not any("|| true" in l for l in lineas), "pip-audit con `|| true` es fail-open"
    assert re.search(r"\[ \$rc -le 1 \] \|\| \{[^}]*exit \$rc", script), (
        "el job debe distinguir 1 (hay vulnerabilidades) de >=2 (fallo de la herramienta)"
    )
    assert "scripts/audit_umbral.py" in script, "el umbral lo decide el script, no el modelo"


def test_el_veredicto_del_umbral_es_el_codigo_de_salida_del_job() -> None:
    """El umbral decide solo si su código de salida es el del paso. Tragárselo
    (`|| true`, `|| status=$?` sin `exit`, `continue-on-error`) deja el trabajo en
    verde con vulnerabilidades corregibles."""
    pasos = job(AUDIT_WORKFLOW, "dependency-audit")["steps"]
    umbral = [p for p in pasos if "audit_umbral.py" in str(p.get("run", ""))]
    assert len(umbral) == 1, "un solo paso corre el umbral"
    assert str(umbral[0]["run"]).strip().startswith("python3 scripts/audit_umbral.py")
    assert "||" not in str(umbral[0]["run"]), "el código del umbral no se captura ni se traga"
    assert not umbral[0].get("continue-on-error"), "el paso del umbral no puede fallar en silencio"
    assert "|| true" not in _script_del_job("dependency-audit")


def test_el_job_corre_tambien_en_pull_request_y_cada_semana() -> None:
    on = disparadores(AUDIT_WORKFLOW)
    assert "pull_request" in on and "schedule" in on


def test_hay_npm_audit_automatico_sobre_el_frontend() -> None:
    trabajo = job(CI_WORKFLOW, "npm-audit")
    texto = guion(trabajo)
    assert "npm audit" in texto and "--omit=dev" in texto and "--audit-level=high" in texto
    assert "|| true" not in texto
    assert "if" not in trabajo, "npm-audit corre siempre, no a petición"
