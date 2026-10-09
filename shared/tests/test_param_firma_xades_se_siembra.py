# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
Guardián: la política XAdES por entidad se LEE, se SIEMBRA y se VALIDA con el mismo nombre.

signature-service lee `firma_personal_require_xades` de `sgd_param_admin`; la
clave nace por migración de tenant-service (un parámetro cuyo nombre solo vive
en el código no se puede configurar, ver `test_params_entidad_configurables`) y
tenant-service valida su vocabulario al escribir. Son tres sitios en dos
servicios: ninguna suite por-servicio los ve juntos.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
LECTOR = RAIZ / "services" / "signature-service" / "app" / "core" / "params_entidad.py"
ESCRITOR = RAIZ / "services" / "tenant-service" / "app" / "routers" / "config.py"
MIGRACION = RAIZ / "services" / "tenant-service" / "migrations" / "tenant" / "010_params_firma.sql"


def _literal(fuente: Path, nombre: str):
    for nodo in ast.parse(fuente.read_text(encoding="utf-8")).body:
        destino = getattr(nodo, "target", None) or (getattr(nodo, "targets", [None])[0])
        if isinstance(destino, ast.Name) and destino.id == nombre:
            return ast.literal_eval(nodo.value)
    raise AssertionError(f"{nombre} no encontrado en {fuente}")


def test_la_clave_que_lee_signature_se_siembra_en_null_no_en_off() -> None:
    clave = _literal(LECTOR, "PARAM_REQUIRE_XADES")
    assert clave == "firma_personal_require_xades"
    sql = MIGRACION.read_text(encoding="utf-8")
    assert re.search(rf"\('{clave}',\s*'null'::jsonb\)", sql), (
        "la clave que signature-service lee no la siembra la migración 010 de "
        "tenant-service con JSON null: /admin/parametros no la ofrece, o la siembra "
        "en \"off\" y anula el respaldo del entorno (FIRMA_PERSONAL_REQUIRE_XADES)"
    )


def test_el_vocabulario_que_lee_signature_es_el_que_valida_tenant_service() -> None:
    leido = tuple(_literal(LECTOR, "MODOS_XADES"))
    clave = _literal(LECTOR, "PARAM_REQUIRE_XADES")
    escrito = _literal(ESCRITOR, "PARAMS_VOCABULARIO_CERRADO")
    assert tuple(escrito[clave]) == leido, (
        "tenant-service admite valores que signature-service no reconoce (o al revés): "
        "un valor aceptado al escribir se aplicaría como 'todos' al leer"
    )
