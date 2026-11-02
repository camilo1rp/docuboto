# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""¿Este localizador sigue pudiendo salir? (ADR-045 §3).

La firma ya se comprobó. Aquí se vuelve a leer el interruptor y el nivel
vivo del radicado dueño del anexo. Si no puede salir, el proveedor debe
borrar lo que derivó de ese localizador.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from app.core import medidor, paso
from app.core.clearance import motivo_salida

_LOCALIZADOR = re.compile(
    r"^att:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):([0-9a-f]{64})$"
)
_SHA = re.compile(r"^[0-9a-f]{64}$")

_ANEXO = """
-- retorno:anexo
SELECT f.sha256, r.nivel_seguridad, r.status
  FROM anexos a
  JOIN files f ON f.id = a.file_id
  JOIN radicados r ON r.id = a.radicado_id
 WHERE a.id = $1
"""


@dataclass(frozen=True)
class Pieza:
    """El anexo pedido, cuando la barrera lo deja salir."""

    anexo_id: uuid.UUID
    sha256: str


@dataclass(frozen=True)
class Rechazo:
    status: int
    code: str
    title: str


def pedido(datos: Any) -> tuple[str, str] | Rechazo:
    """Localizador y huella, o el rechazo si el cuerpo no es el pedido."""
    if not isinstance(datos, dict) or set(datos) != {"locator", "sha256"}:
        return Rechazo(400, "invalid_request", "El pedido no tiene la forma acordada")
    locator = datos["locator"]
    sha = datos["sha256"]
    if not isinstance(locator, str) or not isinstance(sha, str):
        return Rechazo(400, "invalid_request", "El pedido no tiene la forma acordada")
    if len(locator) > 1024 or not _SHA.fullmatch(sha):
        return Rechazo(400, "invalid_request", "El pedido no tiene la forma acordada")
    hallado = _LOCALIZADOR.fullmatch(locator)
    if hallado is None or hallado.group(2) != sha:
        return Rechazo(400, "invalid_request", "El pedido no tiene la forma acordada")
    return locator, sha


async def resolver(conn: Any, slug: str, locator: str) -> Pieza | Rechazo:
    """La pieza si puede salir ahora. 404 si no existe. 410 si ya no sale."""
    hallado = _LOCALIZADOR.fullmatch(locator)
    if hallado is None:
        return Rechazo(400, "invalid_request", "El pedido no tiene la forma acordada")
    politica = await paso.leer_politica(conn, slug)
    if not politica.completa or politica.motivo is not None:
        if politica.completa and politica.motivo in medidor.RAZONES_BARRERA:
            await medidor.anotar(conn, medidor.BARRERA, politica.motivo)
        return Rechazo(410, "gone", "Ya no se transmite")
    fila = await conn.fetchrow(_ANEXO, uuid.UUID(hallado.group(1)))
    if fila is None:
        return Rechazo(404, "scope_not_found", "No hay ese localizador")
    huella = str(fila["sha256"] or "").strip().lower()
    if huella != hallado.group(2):
        return Rechazo(410, "gone", "Ya no se transmite")
    nivel = None if str(fila["status"]) == "anulado" else fila["nivel_seguridad"]
    motivo = motivo_salida(
        bandera=politica.bandera,
        proveedor=politica.proveedor,
        niveles_habilitados=politica.niveles,
        suspendido=politica.suspendido,
        nivel_vivo=nivel,
    )
    if motivo is not None:
        if motivo in medidor.RAZONES_BARRERA:
            await medidor.anotar(conn, medidor.BARRERA, motivo)
        return Rechazo(410, "gone", "Ya no se transmite")
    return Pieza(uuid.UUID(hallado.group(1)), huella)
