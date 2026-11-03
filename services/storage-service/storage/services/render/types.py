# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Interfaz de GENERACIÓN de documentos de salida (E23, RF-PRO-03).

Misma forma —y por la misma razón— que la migración de formato: **aquí tampoco
hay stub**. Un validador puede declarar honestamente «no evaluado», que es un
tercer estado real. Un generador no tiene equivalente: un stub tendría que
producir un documento, y el único que puede producir sin la herramienta es uno
inventado. Sin renderizador, el endpoint responde `503` y lo dice; no entrega un
PDF vacío ni degrada a «texto plano» fingiendo que produjo un oficio.

**El documento nace en formato de conservación** (D-2). Se genera PDF/A
directamente en vez de PDF más una migración posterior: el barrido de
preservación existe para el fondo heredado, no para lo que la entidad produce
hoy. Un documento de archivo que nace no conforme es deuda creada a propósito.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# `no_disponible` NO es un fallo del render: es que no había con qué renderizar.
# Se separa porque son dos problemas con dos soluciones distintas —instalar la
# herramienta frente a mirar por qué un cuerpo concreto no se deja pintar— y
# colapsarlos manda a depurar el sitio equivocado.
RenderOutcome = Literal["generado", "fallido", "no_disponible"]


@dataclass(slots=True)
class RenderResult:
    """Resultado de intentar generar el documento de salida.

    `contenido` viaja **solo** cuando `outcome == "generado"`: un resultado
    fallido con bytes dentro invita a que alguien los guarde «por si acaso», y
    esos bytes son justamente los que no se pueden dar por buenos.
    """

    outcome: RenderOutcome
    formato: str
    herramienta: str
    contenido: bytes | None = None
    detalle: dict[str, Any] = field(default_factory=dict)


class DocumentRenderer(Protocol):
    """Renderizador best-effort: NUNCA lanza, siempre devuelve un `RenderResult`.

    Recibe el cuerpo HTML **ya combinado y ya saneado** —la combinación es de
    document-service y el saneo del proxy BFF— más el contexto de página
    (membrete, pie, número). Este servicio pinta; no decide qué dice el
    documento.
    """

    async def render(
        self, cuerpo_html: str, *, pagina: dict[str, Any], perfil: str
    ) -> RenderResult: ...
