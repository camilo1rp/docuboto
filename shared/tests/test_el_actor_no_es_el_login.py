# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Quien hizo un acto es su identificador, no su login (AREA-02-08).

`X-Username` es el `preferred_username` de Keycloak: un dato DESCRIPTIVO que se
puede mostrar, no una identidad. Keycloak permite renombrar a un usuario aun con
`editUsernameAllowed=false` (el administrador sí puede), y un nombre dado de baja
puede reasignarse. Una fila de custodia física —«el préstamo lo hizo juan»— o el
`generado_por` de un índice electrónico que luego se firma, atribuidos a un
login, dejan de ser reconstruibles el día que el nombre cambia.

`archive-service/app/core/actor.py` ya lo declaraba resuelto para el resto del
servicio («`X-Username` se conserva solo como atributo DESCRIPTIVO»), pero dos
sitios seguían asignándolo como actor: `fisico._performed_by` y
`indice.rebuild_indice`. El mismo patrón que `sub` frente a `user_id`: un
identificador que se parece al otro y no lo es.

**Qué vigila.** Que ninguna variable de ATRIBUCIÓN (`performed_by`,
`generado_por`, `actor…`, `…_por`) se asigne desde `X-Username`. El censo se
deriva del árbol de código de los servicios, no de una lista a mano. Leer
`X-Username` para MOSTRARLO (`signer_name`, el `*LOGIN*` de una plantilla) es
legítimo y no se toca: se prohíbe asignarlo a quien responde del acto.
"""

from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"

#: Variable de atribución asignada desde `X-Username` (o su parámetro de FastAPI,
#: `x_username`). La RHS se mira hasta el fin de LÍNEA: la forma que se quiere
#: cazar es `performed_by = headers.get("X-Username") or ...`.
_ATRIBUCION = re.compile(
    r"\b(?P<var>performed_by|generado_por|actor\w*|\w+_por)\s*(?::[^=\n]+)?=\s*"
    r"(?P<rhs>[^\n]*(?:X-Username|x_username)[^\n]*)",
    re.IGNORECASE,
)


def _hallazgos(texto: str) -> list[str]:
    return [m.group(0).strip() for m in _ATRIBUCION.finditer(texto)]


def test_el_censo_reconoce_la_forma_que_se_prohibe() -> None:
    """Control positivo: sin él, un patrón que dejara de casar aprobaría el
    guardián de abajo sin haber mirado nada (la ausencia trivial no prueba)."""
    assert _hallazgos('generado_por = request.headers.get("X-Username") or "system"')
    assert _hallazgos('    return request.headers.get("X-Username") or None') == []
    assert _hallazgos("performed_by = x_username or x_user_id")
    # Y lo legítimo no salta: mostrar el login no es atribuirle un acto.
    assert _hallazgos('signer_name=request.headers.get("X-Username"),') == []
    assert _hallazgos('login = request.headers.get("X-Username", "").strip()') == []


def test_ninguna_variable_de_atribucion_se_asigna_desde_el_login() -> None:
    culpables: list[str] = []
    for archivo in sorted(SERVICIOS.glob("*/app/**/*.py")):
        for hallazgo in _hallazgos(archivo.read_text(encoding="utf-8")):
            culpables.append(f"{archivo.relative_to(RAIZ)}: {hallazgo}")
    assert not culpables, (
        "Estas variables de atribución se asignan desde `X-Username`. El login es "
        "mutable y reasignable: usa el UUID de `X-User-Id` (`Actor.audit_actor` en "
        "archive-service) y deja el nombre como atributo descriptivo:\n  "
        + "\n  ".join(culpables)
    )


def test_el_censo_mira_servicios_de_verdad() -> None:
    """Anti-vacuidad del árbol: si el glob dejara de encontrar código, el
    guardián aprobaría por no haber leído ningún fichero."""
    assert len(list(SERVICIOS.glob("*/app/**/*.py"))) > 100
