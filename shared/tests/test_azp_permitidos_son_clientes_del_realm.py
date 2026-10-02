# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`ALLOWED_AZP` de auth-service tiene que casar con los clientes REALES del realm.

`validate_token` rechaza todo token cuyo `azp` no esté en esa lista (AREA-02-04).
Es fail-closed a propósito, y por eso mismo un valor por defecto que no case con
el realm no se ve como un fallo de seguridad: se ve como «nadie puede entrar».
Se fija aquí: (a) el cliente del login ROPC del gateway y el del front PKCE están
admitidos; (b) todo lo admitido por defecto existe en `realm-dev.json`.
"""

import json
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
CONFIG = (RAIZ / "services/auth-service/app/core/config.py").read_text(encoding="utf-8")
REALM = json.loads((RAIZ / "infra/keycloak/realm-dev.json").read_text(encoding="utf-8"))


def _default(campo: str) -> str:
    m = re.search(rf'^\s*{campo}:\s*str\s*=\s*"([^"]*)"', CONFIG, re.M)
    assert m, f"no se encontró el default de {campo} en config.py"
    return m.group(1)


def _admitidos() -> set[str]:
    return {c.strip() for c in _default("allowed_azp").split(",") if c.strip()}


def test_el_cliente_ropc_del_gateway_esta_admitido() -> None:
    assert _default("keycloak_gateway_client_id") in _admitidos()


def test_el_cliente_pkce_del_frontend_esta_admitido() -> None:
    assert "orpycamcp-frontend" in _admitidos()


def test_todo_azp_admitido_por_defecto_existe_en_el_realm() -> None:
    clientes = {c["clientId"] for c in REALM["clients"]}
    assert clientes, "el barrido no vio ningún cliente (falso verde)"
    assert _admitidos() <= clientes, _admitidos() - clientes
