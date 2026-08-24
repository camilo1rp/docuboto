# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de ingesta de correo (E19, F2)."""

from pydantic import BaseModel


class PollResult(BaseModel):
    ingested: int
    tracking_numbers: list[str]


class EstadoIngesta(BaseModel):
    """Estado de la ingesta de correo **sin ejecutarla**.

    Existía el poll y nada más: la única forma de saber si la ingesta estaba
    configurada era dispararla — y el poll CREA radicados oficiales y marca los
    correos como leídos, así que averiguar el estado costaba números de radicado
    y un acto irreversible. Este endpoint es la consulta que faltaba.

    Cuando la ingesta no está disponible responde **200 con `habilitada=false`**,
    no un error: "esta entidad no tiene ingesta de correo" es una respuesta
    correcta a la pregunta, no una avería.

    `servidor`, `buzon` y `cuenta` van **solo** si la entidad que pregunta es la
    dueña del buzón. Con `buzon_de_otra_entidad` se devuelven vacíos por la misma
    razón que el 403 del poll no dice de quién es: el nombre de las demás
    instituciones del despliegue no es asunto de quien pregunta.
    """

    habilitada: bool
    motivo: str | None = None
    servidor: str | None = None
    buzon: str | None = None
    cuenta: str | None = None
