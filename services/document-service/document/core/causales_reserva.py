# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Catálogo CERRADO de causales de clasificación y reserva (C12, ADR-039).

Ley 1712/2014 arts. 18 (información CLASIFICADA, nivel 3) y 19 (información
RESERVADA, nivel 2) enumeran las únicas excepciones al acceso: ninguna entidad
inventa una causal propia. Por eso el catálogo es una constante del código y un
CHECK de la base (migración 050), no una tabla editable: un cambio de ley es UNA
migración, no un dato que alguien pueda tocar. Los tres lugares que lo repiten
(esta constante, el CHECK y el `enum` de la tool MCP) los ata el guardián
`tests/test_causales_reserva.py`.

`fundamento_juridico` (texto libre) NO es la causal: es la norma ESPECIAL concreta
que la sustenta (p. ej. Ley 1581 de 2012) y es interno — nunca sale en el índice
público, porque podría transportar contenido del documento.

`por_clasificar` es el valor honesto de lo que quedó clasificado sin causal
(filas anteriores al catálogo, paquetes importados con texto libre, herencia de un
padre sin causal). Solo lo escriben la migración, la importación INT-05 y la
herencia de borrador; la API lo rechaza (422). Sale en el índice público tal cual
—«pendiente de motivar»— y se resuelve re-motivando (PATCH al mismo nivel).

**Qué ha de estar de acuerdo con la ley [confirmar con el auditor archivístico]:**
las citas literales de abajo están redactadas de memoria de la norma; el auditor
las contrasta antes del merge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

#: Desde este nivel, BAJAR exige solicitud y aprobación de una segunda persona
#: (C4). Nivel 3 = CLASIFICADA (art. 18: intimidad, vida/salud/seguridad, secretos):
#: la protección de derechos de PERSONAS no se levanta con una sola firma. Bajar de
#: 2 a 1 (reservada art. 19, intereses públicos) sigue siendo directo. Para cambiarlo
#: hay que tocar esta constante, el CHECK `nivel_anterior = N` de la tabla y el
#: trigger de frontera de la migración 050; el guardián las compara.
NIVEL_DOBLE_FIRMA = 3

#: Tope del plazo de una reserva art. 19: 15 AÑOS contados desde la GENERACIÓN de la
#: información (Ley 1712 art. 22; Decreto 103/2015 art. 29, hoy DUR 1081/2015), no desde
#: que se clasificó = 180 meses menos los ya transcurridos (`plazo_restante_meses`).
PLAZO_MAX_MESES = 180

#: Cita normativa PUBLICABLE (Decreto 103/2015 art. 40, «fundamento constitucional o legal»),
#: controlada por formato: tipo de norma + número/artículo, sin frases. El texto libre
#: (`fundamento_juridico`) es interno y puede transportar contenido; esta cita no. Es la
#: misma regla que el CHECK `radicados_norma_controlada` (migración 051; el guardián las
#: compara).
CITA_NORMA_PATRON = (
    r"^(Constitución Política|Ley|Decreto-Ley|Decreto|Acuerdo|Resolución|Código)"
    r"[ A-Za-z0-9ÁÉÍÓÚÑáéíóúñ.,°º/()-]{1,150}$"
)
CITA_NORMA_RE = re.compile(CITA_NORMA_PATRON)

POR_CLASIFICAR = "por_clasificar"

#: Códigos de error de los 422 por el tope legal de la reserva.
RESERVA_AGOTADA = "reserva_agotada"
PLAZO_EXCEDE_TOPE = "plazo_excede_tope_legal"

CausalCodigo = Literal[
    "art18_a_intimidad",
    "art18_b_vida_salud_seguridad",
    "art18_c_secretos",
    "art19_a_defensa_seguridad_nacional",
    "art19_b_seguridad_publica",
    "art19_c_relaciones_internacionales",
    "art19_d_prevencion_investigacion_delitos",
    "art19_e_debido_proceso",
    "art19_f_administracion_justicia",
    "art19_g_infancia_adolescencia",
    "art19_h_estabilidad_macroeconomica",
    "art19_i_salud_publica",
    "art19_par_deliberativo",
]


@dataclass(frozen=True, slots=True)
class Causal:
    codigo: str
    articulo: str
    literal: str
    descripcion: str
    #: 3 = clasificada (art. 18), 2 = reservada (art. 19).
    nivel: int
    #: `None` = sin plazo (art. 18: se mantiene mientras subsista el derecho; el plazo
    #: DEBE ser NULL). Con valor = tope de meses del plazo, obligatorio (art. 22).
    plazo_max_meses: int | None

    @property
    def categoria(self) -> str:
        return "clasificada" if self.nivel == 3 else "reservada"


def _a18(codigo: str, literal: str, descripcion: str) -> Causal:
    return Causal(codigo, "Ley 1712/2014 art. 18", literal, descripcion, 3, None)


def _a19(codigo: str, literal: str, descripcion: str) -> Causal:
    return Causal(codigo, "Ley 1712/2014 art. 19", literal, descripcion, 2, PLAZO_MAX_MESES)


_LISTA: tuple[Causal, ...] = (
    _a18("art18_a_intimidad", "a", "El derecho de toda persona a la intimidad"),
    _a18("art18_b_vida_salud_seguridad", "b", "El derecho de toda persona a la vida, la salud o la seguridad"),
    _a18("art18_c_secretos", "c", "Los secretos comerciales, industriales y profesionales"),
    _a19("art19_a_defensa_seguridad_nacional", "a", "La defensa y seguridad nacional"),
    _a19("art19_b_seguridad_publica", "b", "La seguridad pública"),
    _a19("art19_c_relaciones_internacionales", "c", "Las relaciones internacionales"),
    _a19(
        "art19_d_prevencion_investigacion_delitos",
        "d",
        "La prevención, investigación y persecución de delitos y faltas disciplinarias, "
        "mientras no se haga efectiva la medida de aseguramiento o se formule pliego de cargos",
    ),
    _a19("art19_e_debido_proceso", "e", "El debido proceso y la igualdad de las partes en los procesos judiciales"),
    _a19("art19_f_administracion_justicia", "f", "La administración efectiva de la justicia"),
    _a19("art19_g_infancia_adolescencia", "g", "Los derechos de la infancia y la adolescencia"),
    _a19("art19_h_estabilidad_macroeconomica", "h", "La estabilidad macroeconómica y financiera del país"),
    _a19("art19_i_salud_publica", "i", "La salud pública"),
    _a19(
        "art19_par_deliberativo",
        "parágrafo",
        "Las opiniones o puntos de vista que formen parte del proceso deliberativo de los servidores públicos",
    ),
)

CAUSALES: dict[str, Causal] = {c.codigo: c for c in _LISTA}

#: Códigos admitidos por la API (excluye `por_clasificar`), en orden de presentación.
CODIGOS: tuple[str, ...] = tuple(CAUSALES)


def plazo_restante_meses(meses_transcurridos: int) -> int:
    """Meses que le quedan a una reserva art. 19 cuando ya pasaron `meses_transcurridos`
    desde la generación de la información. 0 = reserva AGOTADA."""
    return max(0, PLAZO_MAX_MESES - max(0, meses_transcurridos))


def tope_reserva(nivel: int, plazo_meses: int | None, meses_transcurridos: int) -> tuple[str, str] | None:
    """`(código, motivo)` si la reserva excede el tope de 15 años desde la generación.

    Solo aplica a la reservada (nivel 2) con plazo; la clasificada (art. 18) no tiene
    plazo. `reserva_agotada` = ya no queda tiempo: nada puede reservarse (la información
    pasa a ser pública, o clasificada si el art. 18 aplica).
    """
    if nivel != 2 or plazo_meses is None:
        return None
    restante = plazo_restante_meses(meses_transcurridos)
    if restante == 0:
        return (
            RESERVA_AGOTADA,
            "la información ya cumplió 15 años desde su generación: no puede reservarse "
            "(Ley 1712/2014 art. 22; Decreto 103/2015 art. 29)",
        )
    if plazo_meses > restante:
        return (
            PLAZO_EXCEDE_TOPE,
            f"plazo_reserva_meses excede el tope legal: la reserva no puede pasar de 15 años "
            f"desde la generación de la información y a esta le quedan {restante} meses",
        )
    return None


def validar_cita_norma(cita: str | None) -> str | None:
    """Motivo del rechazo de una cita normativa publicable, o `None` si es válida o está vacía."""
    if cita is None:
        return None
    if not CITA_NORMA_RE.fullmatch(cita) or not any(ch.isdigit() for ch in cita):
        return (
            "fundamento_norma debe ser una cita normativa, no texto libre: tipo de norma y "
            "número/artículo (p. ej. «Ley 1581 de 2012, art. 5»)"
        )
    return None


def validar_clasificacion(
    nivel: int,
    causal: str | None,
    plazo_meses: int | None,
    *,
    permitir_por_clasificar: bool = False,
    meses_transcurridos: int = 0,
) -> str | None:
    """Devuelve el motivo del rechazo, o `None` si la combinación es válida.

    UNA sola regla para los dos caminos que escriben la clasificación (radicar y
    reclasificar): escribirla dos veces es como un camino acaba admitiendo lo que el
    otro rechaza. Es la misma regla que el CHECK `radicados_causal_catalogo` aplica
    en la base, que es la última frontera.
    """
    if nivel <= 1:
        if causal is not None or plazo_meses is not None:
            return "nivel_seguridad 1 (pública) no lleva causal ni plazo de reserva"
        return None
    if causal is None:
        return (
            "causal_reserva es obligatoria al clasificar o reservar (Ley 1712/2014 "
            "arts. 18-19): elija una del catálogo (GET /api/v1/documents/causales-reserva)"
        )
    if causal == POR_CLASIFICAR:
        if permitir_por_clasificar:
            return None
        return "por_clasificar no se asigna: es el valor de lo pendiente de motivar"
    c = CAUSALES.get(causal)
    if c is None:
        return f"causal_reserva '{causal}' no está en el catálogo cerrado de la Ley 1712/2014"
    if c.nivel != nivel:
        etiqueta = "clasificada (nivel 3, art. 18)" if c.nivel == 3 else "reservada (nivel 2, art. 19)"
        return f"la causal {causal} corresponde a información {etiqueta}, no al nivel {nivel}"
    if c.plazo_max_meses is None:
        if plazo_meses is not None:
            return (
                "la información clasificada (art. 18) no lleva plazo: se mantiene "
                "mientras subsista el derecho que protege"
            )
        return None
    if plazo_meses is None:
        return "plazo_reserva_meses es obligatorio para la información reservada (art. 19; Ley 1712 art. 22)"
    if not 1 <= plazo_meses <= c.plazo_max_meses:
        return f"plazo_reserva_meses debe estar entre 1 y {c.plazo_max_meses} (15 años, Ley 1712 art. 22)"
    tope = tope_reserva(nivel, plazo_meses, meses_transcurridos)
    return tope[1] if tope else None


def normalizar_para_importar(
    nivel: int, causal: object, plazo_meses: object
) -> tuple[str | None, int | None]:
    """Causal y plazo con que una fila externa entra a la base (INT-05).

    Lo que ya cumple el catálogo se conserva; lo clasificado que NO (texto libre de
    otro sistema, causal ausente, plazo fuera de rango) entra como `por_clasificar`
    —no se inventa una causal— y el texto original queda en el asiento de la
    importación (`causal_original`). Nivel 1 no lleva ni causal ni plazo.
    """
    if nivel <= 1:
        return None, None
    plazo = plazo_meses if isinstance(plazo_meses, int) and not isinstance(plazo_meses, bool) else None
    codigo = causal if isinstance(causal, str) else None
    if codigo is not None and codigo != POR_CLASIFICAR and validar_clasificacion(nivel, codigo, plazo) is None:
        return codigo, plazo
    return POR_CLASIFICAR, plazo
