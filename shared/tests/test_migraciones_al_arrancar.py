# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un servicio con migraciones no arranca antes de aplicarlas (AREA-13-08).

Los doce Dockerfile tienen `CMD uvicorn` directo y la migración solo se
ejecutaba a mano o en CI: una versión nueva podía servir tráfico contra un
esquema viejo y fallar de forma parcial, ruta a ruta.

Producción lo resuelve sin tocar las imágenes: cada servicio con
`migrations/runner.py` tiene un `migrate-<servicio>` de un solo uso (misma
imagen, `command: python migrations/runner.py`, conexión del propietario) y
`depends_on: migrate-<servicio>: condition: service_completed_successfully`.
Si el runner falla, el servicio no arranca (fail-closed). Se acepta también que
el propio Dockerfile invoque `runner.py` antes de `uvicorn`.
"""

from __future__ import annotations

import re

from tests._compose import COMPOSE_PROD, RAIZ, servicios

_CON_MIGRACIONES = sorted(
    p.parent.parent.name for p in (RAIZ / "services").glob("*/migrations/runner.py")
)


def _dockerfile_migra(svc: str) -> bool:
    texto = (RAIZ / "services" / svc / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r"^(?:CMD|ENTRYPOINT)\s.*$", texto, re.MULTILINE)
    return bool(m and "runner.py" in m.group(0) and m.group(0).index("runner.py") < m.group(0).find("uvicorn"))


def _migrador_de(svc: str, todos: dict[str, dict]) -> str | None:
    for nombre, s in todos.items():
        cmd = s.get("command")
        cmd_txt = " ".join(cmd) if isinstance(cmd, list) else str(cmd or "")
        img = str(s.get("image", ""))
        if nombre.startswith("migrate-") and "migrations/runner.py" in cmd_txt and f"/{svc}:" in img:
            return nombre
    return None


def test_se_encontraron_servicios_con_migraciones() -> None:
    assert len(_CON_MIGRACIONES) >= 9, f"solo {_CON_MIGRACIONES}: guardián ciego"


def test_todo_servicio_con_migraciones_las_aplica_antes_de_servir() -> None:
    todos = servicios(COMPOSE_PROD)
    mal: list[str] = []
    for svc in _CON_MIGRACIONES:
        if _dockerfile_migra(svc):
            continue
        migrador = _migrador_de(svc, todos)
        if migrador is None:
            mal.append(f"{svc}: ni el Dockerfile ni el manifiesto de producción migran antes de servir")
            continue
        deps = todos[svc].get("depends_on") or {}
        cond = deps.get(migrador, {}).get("condition") if isinstance(deps, dict) else None
        if cond != "service_completed_successfully":
            mal.append(f"{svc}: no depende de {migrador} con service_completed_successfully")
        if "MIGRATIONS_DATABASE_URL" not in (todos[migrador].get("environment") or {}):
            mal.append(f"{migrador}: no usa la conexión del propietario (MIGRATIONS_DATABASE_URL)")
    assert not mal, "Migraciones al arrancar (fail-closed):\n  " + "\n  ".join(mal)


def test_un_migrador_no_reinicia_ni_publica_puertos() -> None:
    for nombre, s in servicios(COMPOSE_PROD).items():
        if nombre.startswith("migrate-"):
            assert s.get("restart") == "no", f"{nombre}: un migrador de un solo uso no se reinicia"
            assert "ports" not in s, f"{nombre}: no publica puertos"
