# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Contrato `UsuarioActualMiddleware` <-> `AuthService.validate` (ADR-047).

El middleware lee claves del resultado de `validate` con `payload.get("...")`. Si
una no existe en `ValidateResponse`, `.get` devuelve su valor por defecto SIN
error: así `expires_in` no existió durante meses en el contrato que leía el
gateway (AREA-02-12). Cada lado se probaba con sus propios dobles.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
MIDDLEWARE = (RAIZ / "shared/orpycamcp_common/usuario.py").read_text(encoding="utf-8")
ESQUEMA = (RAIZ / "services/docuboto/auth/schemas/auth.py").read_text(encoding="utf-8")


def _campos_validate() -> set[str]:
    bloque = re.search(r"class ValidateResponse\(BaseModel\):\n((?:[ \t]+.*\n|\n)+)", ESQUEMA)
    assert bloque, "no se encontró ValidateResponse"
    return set(re.findall(r"^    (\w+)\s*:", bloque.group(1), re.M))


def _claves_leidas_por_el_middleware() -> set[str]:
    return set(re.findall(r'payload\.get\("(\w+)"', MIDDLEWARE))


def test_el_barrido_mira_de_verdad() -> None:
    assert {"user_id", "tenant_slug"} <= _claves_leidas_por_el_middleware()
    assert "valid" in _campos_validate()


def test_toda_clave_que_lee_el_middleware_la_emite_validate() -> None:
    faltan = _claves_leidas_por_el_middleware() - _campos_validate()
    assert not faltan, f"el middleware lee {sorted(faltan)} y ValidateResponse no las emite"
