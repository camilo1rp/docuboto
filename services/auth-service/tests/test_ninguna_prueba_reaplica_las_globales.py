# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Ninguna prueba re-ejecuta `migrations/global/` sobre una base ya migrada.

El carril de integración corre contra `orpycamcp_db`, la base de la pila de
desarrollo. Aplicar las globales «porque son idempotentes» re-ejecutaba la 007
(`ALTER ROLE orpycamcp_app NOLOGIN`, sin condición) y dejaba a todos los
servicios sin poder conectar al recrearse — dos veces el 2026-10-02.

La prueba de integración `test_las_pruebas_no_tocan_la_pila.py` lo vigila contra
Postgres, pero no puede detectarlo si el rol YA está sin login (el estado no
cambia). Este guardián no depende del estado de la base: el único sitio de las
pruebas que puede leer `GLOBAL_DIR` es `ensure_audit_log`, y solo detrás de la
comprobación de que `audit_log` no existe.
"""

from __future__ import annotations

import re
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_CONFTEST = _TESTS / "integration" / "conftest.py"
_ESTE = Path(__file__).resolve()  # nombra GLOBAL_DIR para buscarlo


def _sin_comentarios_ni_docstrings(texto: str) -> str:
    texto = re.sub(r'"""[\s\S]*?"""', "", texto)
    return re.sub(r"#.*$", "", texto, flags=re.M)


def test_solo_ensure_audit_log_lee_las_migraciones_globales() -> None:
    culpables = [
        str(p.relative_to(_TESTS))
        for p in _TESTS.rglob("*.py")
        if p not in (_CONFTEST, _ESTE) and "GLOBAL_DIR" in _sin_comentarios_ni_docstrings(p.read_text())
    ]
    assert culpables == [], (
        "Estas pruebas aplican migraciones globales por su cuenta; usen "
        f"tests.integration.conftest.ensure_audit_log: {culpables}"
    )


def test_ensure_audit_log_no_reaplica_si_la_tabla_existe() -> None:
    codigo = _sin_comentarios_ni_docstrings(_CONFTEST.read_text())
    cuerpo = codigo.split("async def ensure_audit_log", 1)[1].split("\nasync def ", 1)[0]
    guarda = cuerpo.find("to_regclass('public.audit_log')")
    aplica = cuerpo.find("GLOBAL_DIR")
    assert guarda != -1, "ensure_audit_log ya no comprueba si audit_log existe"
    assert aplica != -1, "control positivo: ensure_audit_log debería seguir sabiendo crear la tabla"
    assert guarda < aplica, "la comprobación tiene que ir ANTES de aplicar las globales"
    assert "return" in cuerpo[guarda:aplica], "si la tabla existe tiene que salir sin aplicar nada"
