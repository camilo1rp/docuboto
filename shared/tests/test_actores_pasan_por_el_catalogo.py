# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Quien radica desde una pantalla no puede inventarse el remitente.

`sender_name` es texto libre en la fila del radicado. Es necesario —lo leen la
búsqueda, los reportes, la exportación y el índice electrónico— pero **no
identifica a nadie**: dos «José Pérez» ahí son dos cadenas, no dos personas ni
una. La identidad vive en el catálogo de terceros, y el vínculo en
`radicado_actor` con su contacto congelado.

**Qué destapó esto.** En septiembre de 2026, `radicado_actor` estaba VACÍA sobre
182 radicados mientras el catálogo tenía 40 terceros: ninguna pantalla escribía
el vínculo. El remitente de una Entrada era texto libre, así que el directorio
solo podía aprender de las Salidas — y de esas tampoco, porque el vínculo no se
persistía. De 182 radicados, 104 eran Entradas. De ahí los duplicados que el
Orfeo legado convirtió en treinta fichas del mismo señor.

**Qué vigila este test.** Que ninguna pantalla NUEVA vuelva a componer un
radicado con `sender_name` sin mandar también `actores`. No prohíbe
`sender_name`: prohíbe que sea lo ÚNICO.

La lista de excepciones se declara abajo, con el motivo de cada una. Una
excepción sin motivo es una puerta que alguien dejó abierta y nadie recuerda por
qué.
"""

from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
RUTAS = RAIZ / "frontend" / "src" / "routes"

#: Composición de un cuerpo de radicación: `sender_name:` como clave de objeto.
#: No basta con que la cadena aparezca —una pantalla puede LEER `sender_name` de
#: un radicado para mostrarlo, y eso es legítimo—: lo que se busca es
#: escribirlo, que en JavaScript se ve como `sender_name:` seguido de un valor.
_ESCRIBE_SENDER = re.compile(r"\bsender_name\s*:")

#: Y el contrapeso, con el MISMO criterio: `actores` como clave de objeto, no la
#: palabra suelta. Buscar la subcadena era vacuo — el archivo de `/radicar`
#: menciona «actores» en sus comentarios y al importar `ActorPicker`, así que
#: quitarle el envío real dejaba el guardián en verde. Lo cazó su propia
#: verificación por mutación, que es para lo que sirve hacerla.
_MANDA_ACTORES = re.compile(r"\bactores\s*:")

#: Pantallas que componen un radicado SIN actores, con su motivo. Cada línea es
#: una decisión, no una omisión.
EXCEPCIONES: dict[str, str] = {
    # La ingesta resuelve el tercero en el BACKEND, por la dirección de correo
    # (`EmailBandejaService._componer` → `TerceroService.resolver_por_email`).
    # La pantalla solo manda el nombre editable; añadir aquí un selector
    # obligaría a quien vacía un buzón a elegir ficha correo por correo.
    "(app)/correo/[cuentaId]/+page.svelte": (
        "el tercero lo resuelve el backend por la dirección del remitente"
    ),
    # Carga MASIVA (E11 INT-05): una tabla donde se pegan N filas de radicados
    # que vienen de otro sistema. Obligar a buscar en el directorio fila por fila
    # convertiría una importación de doscientos documentos en doscientas
    # decisiones, y el payload de origen no trae identificadores del catálogo de
    # ESTA entidad.
    #
    # CONSECUENCIA DECLARADA, no resuelta: lo importado entra con el remitente
    # como texto libre y no alimenta el directorio. Es la única puerta que sigue
    # así, y la vía para consolidarlo después existe —`/admin/terceros` detecta
    # los duplicados por nombre—. Cerrarla de verdad pide una resolución en el
    # BACKEND al importar, como la que ya hace la ingesta de correo.
    "(app)/admin/interoperabilidad/+page.svelte": (
        "carga masiva desde otro sistema: se declara que NO alimenta el directorio"
    ),
}


def _archivos_que_escriben_sender() -> dict[str, str]:
    """Pantallas que componen un radicado, derivadas del árbol.

    Se deriva y no se escribe a mano: una pantalla nueva entra en el censo el día
    que existe. Escribir la lista a mano ya dejó fuera tres de once servicios en
    otro guardián de este repositorio.
    """
    hallados: dict[str, str] = {}
    for ruta in sorted(RUTAS.rglob("*.svelte")):
        texto = ruta.read_text(encoding="utf-8")
        if _ESCRIBE_SENDER.search(texto):
            hallados[str(ruta.relative_to(RUTAS))] = texto
    return hallados


def test_toda_pantalla_que_radica_manda_tambien_los_actores() -> None:
    """Lo que importa: `sender_name` no puede ser lo único."""
    culpables = [
        rel
        for rel, texto in _archivos_que_escriben_sender().items()
        if rel not in EXCEPCIONES and not _MANDA_ACTORES.search(texto)
    ]
    assert not culpables, (
        "Estas pantallas componen un radicado con `sender_name` y NO mandan "
        "`actores`: el remitente queda como texto libre y el directorio no "
        "aprende de él, que es como se acaba con el mismo señor treinta veces en "
        "el catálogo. Usa `ActorPicker` y manda `actores`, o declara la pantalla "
        "en `EXCEPCIONES` con su motivo:\n  " + "\n  ".join(sorted(culpables))
    )


def test_la_lista_de_excepciones_no_caduca() -> None:
    """Una excepción que ya no aplica es una puerta abierta sin motivo.

    Sin esto la lista se vuelve un cementerio: seguiría en verde describiendo
    pantallas que ya mandan actores, o que ya ni existen.
    """
    actuales = _archivos_que_escriben_sender()
    sobran = [
        rel
        for rel in EXCEPCIONES
        if rel not in actuales or _MANDA_ACTORES.search(actuales.get(rel, ""))
    ]
    assert not sobran, (
        "Estas excepciones ya no hacen falta (la pantalla manda actores, o "
        "dejó de componer radicados, o no existe): quítalas de `EXCEPCIONES` "
        "para que la lista siga contando la deuda real:\n  " + "\n  ".join(sorted(sobran))
    )


def test_el_censo_encuentra_algo() -> None:
    """Anti-vacuidad: si el patrón deja de casar, los otros dos pasan sobre nada.

    Es la forma silenciosa de que un guardián deje de guardar, y ya ocurrió en
    este repositorio con un censo que salía vacío al ejecutarlo desde otra raíz.
    """
    assert len(_archivos_que_escriben_sender()) >= 2, (
        "El censo de pantallas que componen un radicado se quedó casi vacío: el "
        "patrón dejó de casar y este guardián ya no vigila nada."
    )
