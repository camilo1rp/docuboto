# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Guardián: un parámetro que el código LEE tiene que poder configurarse.

── El defecto que este guardián existe para impedir ─────────────────────────

`document-service` resuelve los siete marcadores del grupo «Entidad» —el
membrete de todo documento producido— desde siete claves de `sgd_param_admin`.
Los nombres de esas claves existían ÚNICAMENTE dentro del diccionario
`PARAMS_ENTIDAD`: ninguna migración de `tenant-service` las creaba, y
`/admin/parametros` enseña las filas que HAY. Resultado: el membrete salía en
blanco en todos los oficios y no había ninguna pantalla desde la que
rellenarlo.

El comentario que encabeza `PARAMS_ENTIDAD` ya lo advertía —«un parámetro cuyo
nombre solo vive en la cabeza de quien lo configuró no se puede configurar»— y
el código hacía exactamente eso. Es un cruce entre dos servicios, así que
ninguna suite por-servicio podía verlo: el contenedor de tests de
document-service no contiene las migraciones de tenant-service.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
FUENTE = RAIZ / "services" / "document-service" / "app" / "services" / "contexto_combinacion.py"
MIGRACION = RAIZ / "services" / "tenant-service" / "migrations" / "tenant" / "009_params_entidad.sql"


def _claves_que_lee_el_codigo() -> set[str]:
    """Extrae `PARAMS_ENTIDAD` del fuente sin importar el módulo.

    Se lee con `ast` en vez de importarlo porque este test corre fuera del
    contenedor de document-service, donde sus dependencias no están instaladas.
    """
    arbol = ast.parse(FUENTE.read_text(encoding="utf-8"))
    for nodo in arbol.body:
        destinos = getattr(nodo, "targets", []) or [getattr(nodo, "target", None)]
        for destino in destinos:
            if isinstance(destino, ast.Name) and destino.id == "PARAMS_ENTIDAD":
                valor = ast.literal_eval(nodo.value)
                assert isinstance(valor, dict)
                return set(valor.values())
    raise AssertionError(f"PARAMS_ENTIDAD no encontrado en {FUENTE}")


def _claves_que_siembra_la_migracion() -> set[str]:
    return set(re.findall(r"\('(entidad_[a-z_]+)'", MIGRACION.read_text(encoding="utf-8")))


def test_toda_clave_del_membrete_se_siembra() -> None:
    sin_sembrar = _claves_que_lee_el_codigo() - _claves_que_siembra_la_migracion()
    assert not sin_sembrar, (
        "claves que document-service lee y ninguna migración crea, así que "
        f"/admin/parametros no las ofrece y el membrete no se puede rellenar: {sorted(sin_sembrar)}"
    )


def test_no_se_siembran_parametros_que_nadie_lee() -> None:
    # Anti-vacuidad por el otro lado: la comparación tiene que ser en las dos
    # direcciones o sembrar de más pasaría inadvertido, llenando la pantalla de
    # administración de parámetros que no hacen nada.
    de_mas = _claves_que_siembra_la_migracion() - _claves_que_lee_el_codigo()
    assert not de_mas, f"parámetros sembrados que nadie consume: {sorted(de_mas)}"


def test_el_guardian_ve_las_siete_claves() -> None:
    # Si el `ast` dejara de encontrar el diccionario, los dos tests de arriba
    # compararían dos conjuntos vacíos y pasarían sin comprobar nada.
    assert len(_claves_que_lee_el_codigo()) == 7
