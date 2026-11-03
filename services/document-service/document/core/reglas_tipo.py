# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Qué admite cada TIPO de radicado, con independencia de quién lo pida.

**Esto no es RBAC, y la diferencia es el motivo de que exista este módulo.** El
permiso responde a «¿está esta persona autorizada?». Aquí se responde a otra
pregunta, anterior: «¿es este acto posible sobre esta clase de documento?». Son
ejes distintos y no se sustituyen — un usuario puede tener todos los permisos del
sistema y aun así haber actos que sobre cierto tipo de radicado no existen.

**El caso que originó el módulo: una Entrada no se anula.** Si un documento entró
a la entidad, entró; ese es un hecho ocurrido, no una decisión revisable. La
anulación existe para lo que la entidad **produce** —una Salida o un Interno
emitidos por error— donde sí cabe dejar sin efecto lo emitido. Sobre una Entrada
no hay nada que dejar sin efecto: el documento llegó, y borrar ese registro sería
negar que llegó.

Hasta ahora el servicio de anulación solo comprobaba que el radicado existiera y
no estuviera ya anulado, así que **una Entrada se anulaba igual** con solo tener
`PERM_SOL_ANULAR`. Y el legacy sí distinguía: su flujo procesa un tipo de
radicado a la vez (`sgd_trad_codigo` en `include/tx/Anulacion.php`).

**Se escribe por ADMISIÓN, no por exclusión.** Un tipo que nadie haya declarado
no admite nada, y un tipo documental nuevo llega sin actos hasta que alguien
decida cuáles. Con una lista de exclusiones, el olvido queda del lado permisivo
y no avisa — que es como se cuelan estas cosas.

**Lo que NO va aquí: las reglas que cada entidad decide.** Este módulo es solo
para lo que es igual en toda entidad porque lo impone la naturaleza del
documento o la norma archivística. Hay otro grupo de reglas, distinto, que
depende de **cómo quiera trabajar cada tenant** y del que no se ha construido
nada todavía (2026-08-17, dicho por el usuario):

* **Quién puede responder un radicado.** Unas entidades dejan que cualquier
  usuario de cualquier dependencia responda; otras exigen el flujo tal y como se
  concibió — solo responde quien tiene el radicado asignado y pertenece a la
  dependencia que lo tramita.
* **Visibilidad.** Si un radicado lo ve un único usuario en todo el sistema,
  toda su dependencia, o cualquier usuario de la entidad.

Esas son **decisiones de la entidad, no hechos del documento**, y necesitan un
mecanismo propio: configuración por tenant, no una tabla en el código. Meterlas
aquí las convertiría en universales y le impondría a todas las entidades la
manera de trabajar de una — que es exactamente el error contrario al que este
módulo evita. Cuando se construyan, irán a su propio sitio.

**Ni ROOT lo salta.** El bypass de ROOT es sobre permisos: sirve para autorizar a
quien no estaba autorizado, no para hacer posible lo que no lo es. Por eso la
comprobación vive en el servicio de dominio y no en un `Depends` de autorización.
"""

from __future__ import annotations

from fastapi import HTTPException, status

# Acto de dominio → tipos de radicado que lo admiten.
#
# `E` (Entrada), `S` (Salida), `I` (Interno) son los tres tipos del sistema
# (`DocumentCreate.doc_type`). Añadir un acto aquí es declarar sobre qué tipos
# tiene sentido; omitir un tipo es decir que sobre él no existe.
_ADMITE: dict[str, frozenset[str]] = {
    # Anular = dejar sin efecto lo que la entidad emitió. Solo sus propios
    # documentos: `E` NO aparece, y esa ausencia es la regla.
    "anular": frozenset({"S", "I"}),
}


def admite(acto: str, doc_type: str | None) -> bool:
    """¿Tiene sentido este acto sobre esta clase de documento?

    Fail-closed en los dos vacíos: un acto no declarado no lo admite ningún
    tipo, y un `doc_type` desconocido o ausente no admite ningún acto. Ninguno
    de los dos debería ocurrir; si ocurre, negar es la respuesta correcta.
    """
    return bool(doc_type) and doc_type in _ADMITE.get(acto, frozenset())


_MOTIVOS: dict[tuple[str, str], str] = {
    ("anular", "E"): (
        "Un radicado de Entrada no se anula: el documento entró a la entidad y ese "
        "hecho no se deja sin efecto. La anulación aplica a lo que la entidad emite "
        "(Salida o Interno). Si el registro tiene un error, corríjalo; si no debía "
        "tramitarse, ciérrelo por el flujo que corresponda."
    ),
}


def exigir(acto: str, doc_type: str | None) -> None:
    """Eleva 409 si el acto no existe para este tipo. No consulta permisos.

    409 y no 403: no es que al llamante le falte autorización —dársela no
    cambiaría nada—, es que la petición choca con la naturaleza del objeto. Un
    403 mandaría a la persona a pedir un permiso que no le va a servir.
    """
    if admite(acto, doc_type):
        return
    detalle = _MOTIVOS.get(
        (acto, doc_type or ""),
        f"La acción «{acto}» no aplica a un radicado de tipo «{doc_type or '?'}».",
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": "acto_no_aplica_al_tipo", "detail": detalle, "status": 409},
    )
