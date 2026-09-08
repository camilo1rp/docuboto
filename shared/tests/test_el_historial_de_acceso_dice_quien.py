# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un historial de acceso tiene que contestar QUIÉN, no un identificador.

`audit_log` guarda el id interno del actor, y así debe seguir: un `username`
cambia de dueño y un asiento no se toca nunca (Ley 594/2000, append-only). Pero
una pantalla titulada «quién ha visto esto» que responde
`50b64803-568d-49f7-9299-cb140279d947` no responde nada — es
[[patron-dato-cargado-que-nadie-lee]] otra vez, con el agravante de que el
nombre está a un JOIN de distancia.

**Por qué hace falta un guardián y no bastaba con arreglarlo.** Hay DOS lectores
de la pista de acceso, gemelos y escritos a partir del mismo diseño:

* `document-service/app/repositories/audit_read.py` — «¿quién vio este
  radicado?»
* `archive-service/app/repositories/audit_read.py` — «¿quién vio este
  expediente?»

El 2026-09-06 se corrigió el primero. El 2026-09-07, recorriendo B5 del plan de
pruebas, el segundo seguía mostrando el UUID crudo en la pestaña «Acceso» del
expediente: se arregló un gemelo y se dejó el otro. Dos módulos que responden la
misma pregunta y evolucionan por separado acaban respondiendo distinto — es el
mismo patrón que ya obligó a compartir la lista de acciones de acceso entre
ambos, escrito en sus propios docstrings.

El censo se DERIVA del árbol: un tercer lector entra el día que exista.
"""

from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"

#: El JOIN que resuelve el identificador a un nombre. Se exige por FORMA y no
#: por texto exacto: lo que importa es que se una con `auth_users` y que el
#: nombre viaje en la proyección.
_RESUELVE = re.compile(r"JOIN\s+auth_users", re.I)
_PROYECTA = re.compile(r"\bactor_nombre\b")

#: Comparar por TEXTO y no castear a `uuid` es parte del contrato: `actor` no
#: siempre es un usuario (hay asientos del propio sistema, y migraciones), y un
#: `::uuid` haría fallar la consulta ENTERA por una fila que ni se preguntaba.
_CASTEA_A_UUID = re.compile(r"a\.actor\s*::\s*uuid|actor\s*=\s*u\.id\b")

#: Lectores que NO resuelven el nombre, con su motivo. Vacía a propósito.
EXCEPCIONES: dict[str, str] = {}


def _lectores_de_la_pista() -> dict[str, str]:
    """Derivado del árbol: cualquier `audit_read.py` de cualquier servicio."""
    hallados: dict[str, str] = {}
    for ruta in sorted(SERVICIOS.glob("*/app/repositories/audit_read.py")):
        hallados[str(ruta.relative_to(SERVICIOS))] = ruta.read_text(encoding="utf-8")
    return hallados


def test_todo_lector_de_la_pista_resuelve_el_nombre() -> None:
    culpables = [
        rel
        for rel, texto in _lectores_de_la_pista().items()
        if rel not in EXCEPCIONES
        and not (_RESUELVE.search(texto) and _PROYECTA.search(texto))
    ]
    assert not culpables, (
        "Estos lectores de la pista de acceso devuelven el identificador del "
        "actor y no su nombre, así que la pantalla que pregunta «quién» "
        "contesta con un UUID que nadie puede resolver. Añade el "
        "`LEFT JOIN auth_users` y proyecta `actor_nombre` —como sus gemelos— o "
        "declara el lector en `EXCEPCIONES` con su motivo:\n  "
        + "\n  ".join(sorted(culpables))
    )


def test_el_join_no_castea_el_actor_a_uuid() -> None:
    """El actor no siempre es un usuario, y un `::uuid` tumbaría la consulta.

    Un asiento escrito por el propio sistema lleva un actor que no es un
    identificador —«system», «proceso-de-migracion»—, y castearlo haría fallar
    el historial ENTERO por una fila que ni se estaba preguntando. Sin nombre
    resoluble se cae al identificador: decir menos es aceptable, perder el
    historial no.
    """
    culpables = [
        rel for rel, texto in _lectores_de_la_pista().items() if _CASTEA_A_UUID.search(texto)
    ]
    assert not culpables, (
        "Estos lectores comparan el actor casteándolo a `uuid`: un asiento del "
        "sistema haría fallar la consulta entera. Únelos por texto "
        "(`u.id::text = a.actor`):\n  " + "\n  ".join(sorted(culpables))
    )


def test_la_lista_de_excepciones_no_caduca() -> None:
    actuales = _lectores_de_la_pista()
    sobran = [
        rel
        for rel in EXCEPCIONES
        if rel not in actuales
        or (_RESUELVE.search(actuales[rel]) and _PROYECTA.search(actuales[rel]))
    ]
    assert not sobran, (
        "Estas excepciones ya no hacen falta: quítalas para que la lista siga "
        "contando la deuda real:\n  " + "\n  ".join(sorted(sobran))
    )


def test_el_censo_encuentra_los_dos_gemelos() -> None:
    """Anti-vacuidad: si el patrón deja de casar, el guardián deja de guardar.

    Ya ocurrió dos veces en este repositorio con censos que salían vacíos o
    casaban con una subcadena demasiado laxa. Son DOS lectores hoy; si el censo
    encuentra menos, es que el patrón dejó de casar, no que el defecto se
    arreglara solo.
    """
    assert len(_lectores_de_la_pista()) >= 2, (
        "El censo de lectores de la pista de acceso se quedó corto: el patrón "
        "de rutas dejó de casar y este guardián ya no vigila nada."
    )
