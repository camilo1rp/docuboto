# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Ningún sondeo puede fingir que hay alguien delante.

El cierre de sesión por inactividad vive donde debe: en Keycloak
(`ssoSessionIdleTimeout`, 30 min en la plantilla de producción). Un cierre
decidido por el navegador sería un control falso — se salta desactivando
JavaScript.

**Pero el navegador sí puede ANULARLO.** Ese contador se reinicia cada vez que
se usa el *refresh token*, y la renovación transparente de `hooks.server.js` se
dispara con CUALQUIER petición al servidor de SvelteKit, también con las que la
aplicación se hace a sí misma. Un `setInterval` que llame al servidor mantiene
viva la sesión de una pestaña olvidada sobre una mesa, con la pantalla
desbloqueada, hasta el tope absoluto de 10 h.

Ha pasado dos veces:

* **2026-08-30, `/correo`**: sondeo cada 12 s de la bandeja de correo. Se
  corrigió con `$lib/utils/inactividad.js` — no cerrando sesión desde el
  cliente, sino **dejando de fingir actividad**.
* **2026-09-06, `/admin/interoperabilidad`**: sondeo cada 2 s del estado de una
  carga masiva. Seis veces más rápido, y sin parar nunca si el sondeo fallaba o
  si el trabajo se quedaba en un estado no terminal. El README de Keycloak dejó
  escrita la pregunta —«¿esto renueva la sesión de alguien que no está
  delante?»— y nadie se la había hecho para esta pantalla.

Dos veces es un patrón, y un patrón necesita un guardián en vez de una nota.
"""

from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
RUTAS = RAIZ / "frontend" / "src" / "routes"

_SONDEA = re.compile(r"\bsetInterval\s*\(")
#: Que el sondeo llame al servidor de SvelteKit es lo que reinicia el contador.
#: Un `setInterval` que solo mueve un reloj en pantalla no renueva nada.
_LLAMA_AL_SERVIDOR = re.compile(r"\bfetch\s*\(\s*[`\"']/")
_VIGILA = re.compile(r"\bvigilarInactividad\s*\(")

#: Pantallas con sondeo que NO vigilan la inactividad, con su motivo. Vacía a
#: propósito: si alguna vez hay una, tendrá que justificarse aquí.
EXCEPCIONES: dict[str, str] = {}


def _pantallas_que_sondean_al_servidor() -> dict[str, str]:
    """Derivado del árbol: una pantalla nueva entra el día que existe."""
    hallados: dict[str, str] = {}
    for ruta in sorted(RUTAS.rglob("*.svelte")):
        texto = ruta.read_text(encoding="utf-8")
        if _SONDEA.search(texto) and _LLAMA_AL_SERVIDOR.search(texto):
            hallados[str(ruta.relative_to(RUTAS))] = texto
    return hallados


def test_todo_sondeo_al_servidor_para_sin_interaccion_real() -> None:
    """Lo que importa: sin nadie delante, el sondeo tiene que parar."""
    culpables = [
        rel
        for rel, texto in _pantallas_que_sondean_al_servidor().items()
        if rel not in EXCEPCIONES and not _VIGILA.search(texto)
    ]
    assert not culpables, (
        "Estas pantallas sondean al servidor con `setInterval` y NO vigilan la "
        "inactividad: cada sondeo reinicia el contador de Keycloak, así que una "
        "pestaña olvidada mantiene la sesión abierta con la pantalla "
        "desbloqueada. Usa `vigilarInactividad` de `$lib/utils/inactividad.js` "
        "—que NO cierra sesión, solo deja de fingir actividad— o declara la "
        "pantalla en `EXCEPCIONES` con su motivo:\n  " + "\n  ".join(sorted(culpables))
    )


def test_la_lista_de_excepciones_no_caduca() -> None:
    actuales = _pantallas_que_sondean_al_servidor()
    sobran = [
        rel
        for rel in EXCEPCIONES
        if rel not in actuales or _VIGILA.search(actuales.get(rel, ""))
    ]
    assert not sobran, (
        "Estas excepciones ya no hacen falta: quítalas para que la lista siga "
        "contando la deuda real:\n  " + "\n  ".join(sorted(sobran))
    )


def test_el_censo_encuentra_algo() -> None:
    """Anti-vacuidad: si el patrón deja de casar, el guardián deja de guardar.

    Ya ocurrió en este repositorio —dos veces— con censos que salían vacíos o
    que casaban con una subcadena demasiado laxa.
    """
    assert len(_pantallas_que_sondean_al_servidor()) >= 2, (
        "El censo de pantallas con sondeo se quedó casi vacío: el patrón dejó de "
        "casar y este guardián ya no vigila nada."
    )
