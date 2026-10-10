# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
Guardián: las claves de la política de tratamiento se LEEN, se SIEMBRAN y se NOMBRAN igual.

document-service lee `politica_tratamiento_{version,url,texto}` de `sgd_param_admin`
(`app/core/politica.py`); las claves nacen por la migración 012 de tenant-service
(un parámetro cuyo nombre solo vive en el código no se puede configurar, ver
`test_params_entidad_configurables`). Son dos servicios: ninguna suite por-servicio
los ve juntos. Sembrarlas con un valor NO vacío publicaría una política inventada
en la constancia de un tratamiento de datos (Ley 1581): deben nacer vacías.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
LECTOR = RAIZ / "services" / "document-service" / "app" / "core" / "politica.py"
MIGRACION = (
    RAIZ / "services" / "tenant-service" / "migrations" / "tenant" / "012_politica_tratamiento.sql"
)
CONSTANTES = ("PARAM_VERSION", "PARAM_URL", "PARAM_TEXTO")


def _claves_que_lee_document() -> list[str]:
    valores = {}
    for nodo in ast.parse(LECTOR.read_text(encoding="utf-8")).body:
        destino = getattr(nodo, "target", None) or (getattr(nodo, "targets", [None])[0])
        if isinstance(destino, ast.Name) and destino.id in CONSTANTES:
            valores[destino.id] = ast.literal_eval(nodo.value)
    assert set(valores) == set(CONSTANTES), f"faltan constantes en {LECTOR}: {valores}"
    return [valores[c] for c in CONSTANTES]


def test_las_tres_claves_que_lee_document_las_siembra_tenant_vacias() -> None:
    sql = MIGRACION.read_text(encoding="utf-8")
    for clave in _claves_que_lee_document():
        assert re.search(rf"\('{clave}',\s*'\"\"'::jsonb\)", sql), (
            f"la clave {clave!r} que document-service lee no la siembra vacía la migración 012 "
            "de tenant-service: /admin/parametros no la ofrece, o nace con una política inventada"
        )


def test_la_siembra_no_pisa_lo_que_la_entidad_ya_publico() -> None:
    sql = MIGRACION.read_text(encoding="utf-8")
    assert "ON CONFLICT (key) DO NOTHING" in sql
