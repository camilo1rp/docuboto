# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Datos mínimos de un radicado (Acuerdo AGN 060/2001 §5.2) — fuente única.

Una comunicación oficial se radica con el tipo documental y con la dependencia
que la origina (Salida) o la recibe (Entrada e Interno). Hasta AREA-09-02 el
schema los dejaba opcionales, así que un radicado podía nacer sin serie TRD y
sin destino —fuera de la bandeja de todos— y por la carga masiva entraba sin que
nadie lo notara.

La regla vive AQUÍ y la importan las dos puertas de radicación
(`DocumentCreate` y `BatchDocumentCreate`): dos listas escritas aparte
divergen, y la divergencia es una segunda puerta.
"""

from __future__ import annotations

from fastapi import HTTPException

#: tipo de radicado → campos obligatorios al radicar.
REQUERIDOS_POR_TIPO: dict[str, tuple[str, ...]] = {
    "E": ("doc_class", "dest_dept_code"),
    "I": ("doc_class", "dest_dept_code"),
    "S": ("doc_class", "origin_dept_code"),
}


def faltantes(
    doc_type: str,
    *,
    doc_class: str | None,
    origin_dept_code: int | None,
    dest_dept_code: int | None,
) -> list[str]:
    """Campos del §5.2 que faltan. Un `doc_type` desconocido no declara nada.

    El tipo desconocido lo rechaza el `Literal`/la validación propia del
    llamante; aquí no se inventa una regla para él.
    """
    valores: dict[str, object] = {
        "doc_class": (doc_class or "").strip() or None,
        "origin_dept_code": origin_dept_code,
        "dest_dept_code": dest_dept_code,
    }
    return [c for c in REQUERIDOS_POR_TIPO.get(doc_type, ()) if valores[c] is None]


def mensaje_faltantes(doc_type: str, campos: list[str]) -> str:
    return (
        f"Un radicado de tipo {doc_type} exige {', '.join(campos)} "
        "(Acuerdo AGN 060/2001 §5.2: tipo documental y dependencia de origen/destino)"
    )


def exigir_o_422(
    doc_type: str,
    *,
    doc_class: str | None,
    origin_dept_code: int | None,
    dest_dept_code: int | None,
    ayuda: str = "",
) -> None:
    """Para los PRODUCTORES internos (respuesta, borrador): 422 ANTES de construir
    el `DocumentCreate` y, sobre todo, antes de pedir el consecutivo.

    Sin esto, el `ValidationError` de Pydantic no es una respuesta HTTP: sale como
    500 desde dentro del servicio. El router resuelve el 422 de la entrada pública
    por sí solo; aquí no hay cuerpo que validar, hay datos derivados.
    """
    falta = faltantes(
        doc_type,
        doc_class=doc_class,
        origin_dept_code=origin_dept_code,
        dest_dept_code=dest_dept_code,
    )
    if falta:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "datos_minimos_incompletos",
                "detail": (mensaje_faltantes(doc_type, falta) + (". " + ayuda if ayuda else "")),
                "status": 422,
                "campos": falta,
            },
        )
