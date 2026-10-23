# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""`GET /config/asistente` (tenant-service) muestra los proveedores EFECTIVOS del asistente
(ADR-037). tenant-service no ejecuta ninguno: los lee de sus propios ajustes, así que sus
defaults tienen que ser los de knowledge-service (`embedding_provider`, `ai_provider`) y
mcp-server (`assistant_model`), y el compose de desarrollo tiene que pasarle los mismos
valores que a knowledge-service. Una tarjeta que dijera «embeddings locales» cuando el
servicio usa uno remoto sería exactamente la mentira que esta pantalla existe para evitar.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
S = RAIZ / "services"


def _defaults(ruta: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for n in ast.walk(ast.parse(ruta.read_text())):
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and isinstance(n.value, ast.Constant):
            out[n.target.id] = n.value.value
    return out


def test_los_defaults_de_tenant_coinciden_con_los_de_quien_ejecuta() -> None:
    t = _defaults(S / "tenant-service/app/core/config.py")
    k = _defaults(S / "knowledge-service/app/core/config.py")
    m = _defaults(S / "mcp-server/app/core/config.py")
    assert t["embedding_provider"] == k["embedding_provider"]
    assert t["ai_provider"] == k["ai_provider"]
    assert t["assistant_model"] == m["assistant_model"]


def test_el_compose_de_desarrollo_pasa_a_tenant_lo_mismo_que_a_knowledge() -> None:
    compose = (RAIZ / "docker-compose.yml").read_text()

    def bloque(servicio: str) -> str:
        i = compose.index(f"\n  {servicio}:\n")
        j = re.search(r"\n  [a-z0-9-]+:\n", compose[i + 1 :])
        return compose[i : i + 1 + (j.start() if j else len(compose))]

    def valor(b: str, var: str) -> str | None:
        m = re.search(rf"- {var}=(.*)", b)
        return m.group(1).strip() if m else None

    k, t = bloque("knowledge-service"), bloque("tenant-service")
    for var in ("EMBEDDING_PROVIDER", "AI_PROVIDER"):
        assert valor(k, var) is not None, f"knowledge-service ya no declara {var}"
        assert valor(t, var) == valor(k, var), f"tenant-service debe recibir {var} igual que knowledge"


def test_la_lista_de_proveedores_locales_tiene_una_sola_definicion() -> None:
    donde = [
        str(p.relative_to(RAIZ))
        for p in list((RAIZ / "services").rglob("*.py")) + list((RAIZ / "shared/orpycamcp_common").rglob("*.py"))
        if "/tests/" not in str(p) and re.search(r"^LOCAL_EMBEDDING_PROVIDERS\s*=", p.read_text(), re.M)
    ]
    assert donde == ["shared/orpycamcp_common/tenant.py"], donde
