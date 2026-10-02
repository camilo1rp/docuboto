# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Contrato gateway <-> auth-service de `/validate`.

El gateway lee claves del JSON de `ValidateResponse` con `payload.get("...")`. Si
una no existe en el modelo, `.get` devuelve su valor por defecto SIN error: así
`expires_in` no existió durante meses y el tope de TTL de la caché fue inerte
(AREA-02-12). Cada servicio probaba su lado con sus propios dobles y ninguno veía
el hueco entre los dos.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
GATEWAY = (RAIZ / "services/api-gateway/app/middleware/auth.py").read_text(encoding="utf-8")
ESQUEMA = (RAIZ / "services/auth-service/app/schemas/auth.py").read_text(encoding="utf-8")


def _campos_validate() -> set[str]:
    bloque = re.search(r"class ValidateResponse\(BaseModel\):\n((?:[ \t]+.*\n|\n)+)", ESQUEMA)
    assert bloque, "no se encontró ValidateResponse"
    return set(re.findall(r"^    (\w+)\s*:", bloque.group(1), re.M))


def _claves_leidas_por_el_gateway() -> set[str]:
    return set(re.findall(r'payload\.get\("(\w+)"', GATEWAY))


def test_el_barrido_mira_de_verdad() -> None:
    assert {"valid", "expires_in"} <= _claves_leidas_por_el_gateway()
    assert "valid" in _campos_validate()


def test_toda_clave_que_lee_el_gateway_la_emite_validate() -> None:
    faltan = _claves_leidas_por_el_gateway() - _campos_validate()
    assert not faltan, f"el gateway lee {sorted(faltan)} y ValidateResponse no las emite"
