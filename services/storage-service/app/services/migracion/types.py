# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Interfaz de MIGRACIÓN de formato contra obsolescencia (RF-PRE-04; Decreto
2609/2012 art. 32; OAIS).

**Por qué aquí NO hay stub, a diferencia del validador PDF/A.** Un validador
puede declarar honestamente «no evaluado»: es un tercer estado real, distinto de
conforme y de no-conforme. Un migrador no tiene equivalente — un stub tendría
que **producir un archivo**, y el único archivo que puede producir sin la
herramienta es uno inventado. La ausencia de convertidor se declara devolviendo
``None`` desde la factory y respondiendo `503`, no fabricando una copia de
preservación que nadie generó. Es la misma asimetría deliberada que la
transcripción de voz (E18): un stub tendría que inventarse lo que se dijo.

**El original NUNCA se toca.** La migración produce un artefacto DERIVADO y deja
intacto el archivo de origen, con su cadena de procedencia enlazada — la norma
pide conservar el original y la procedencia, no reemplazar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# `no_disponible` NO es un fallo de la conversión: es que no había con qué
# convertir. Se distingue porque son dos problemas con dos soluciones distintas
# —instalar la herramienta vs. mirar por qué un PDF concreto no se deja
# convertir— y colapsarlos mandaría a depurar el archivo equivocado.
MigracionOutcome = Literal["migrado", "fallido", "no_disponible"]


@dataclass(slots=True)
class MigracionResult:
    """Resultado de intentar migrar un documento a un formato de preservación.

    `contenido` son los bytes del artefacto derivado, y viaja **solo** cuando
    `outcome == "migrado"`: un resultado fallido con bytes dentro invita a que
    alguien los guarde «por si acaso», y esos bytes son justamente los que no
    se pueden dar por buenos.
    """

    outcome: MigracionOutcome
    formato_destino: str
    herramienta: str
    contenido: bytes | None = None
    detalle: dict[str, Any] = field(default_factory=dict)


class FormatMigrator(Protocol):
    """Migrador de formato best-effort: NUNCA lanza, siempre devuelve un
    `MigracionResult`.

    Recibe una ruta a archivo —no los bytes— porque la implementación real
    invoca un binario externo vía subprocess, que opera sobre disco. Quien
    llama materializa el temporal a partir de los bytes YA descargados y con la
    fixity recalculada, y lo limpia después.
    """

    async def migrar(self, origen_path: str, *, perfil: str) -> MigracionResult: ...
