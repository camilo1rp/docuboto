# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Catálogo declarativo de tools MCP (E18, ADR-019).

Cada tool mapea 1:1 a un endpoint REST estable del api-gateway. El dispatcher
genérico traduce la invocación a una llamada HTTP. Añadir una tool = añadir una
entrada aquí (no código nuevo). `path` admite plantillas `{param}` que se rellenan
con los argumentos de la invocación; el resto de argumentos van a query (GET) o
body (escritura). `permission` (si se indica) filtra la visibilidad por los
permisos del usuario; `write=True` marca operaciones que exigen gate explícito.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    method: str  # GET | POST
    path: str  # ruta gateway, con {params}
    path_params: tuple[str, ...] = ()
    permission: str | None = None  # permiso atómico requerido (None = lectura básica)
    write: bool = False  # operación de escritura/destructiva
    epic: str = ""

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "method": self.method,
            "write": self.write,
            "epic": self.epic,
        }


CATALOG: tuple[Tool, ...] = (
    Tool(
        name="radicar_documento",
        description="Radica (registra) un documento de Entrada/Salida/Interno y devuelve su número de radicado.",
        method="POST",
        path="/api/v1/documents",
        permission="RADI_CREAR",
        write=True,
        epic="E01",
    ),
    Tool(
        name="consultar_radicado",
        description="Consulta un radicado por su id.",
        method="GET",
        path="/api/v1/documents/{document_id}",
        path_params=("document_id",),
        epic="E01",
    ),
    Tool(
        name="buscar_radicados",
        description="Búsqueda avanzada de radicados (texto + filtros).",
        method="GET",
        path="/api/v1/search",
        epic="E09",
    ),
    Tool(
        name="consultar_expediente",
        description="Consulta un expediente por su id (incluye radicados vinculados).",
        method="GET",
        path="/api/v1/expedientes/{expediente_id}",
        path_params=("expediente_id",),
        epic="E02",
    ),
    Tool(
        name="indice_expediente",
        description="Índice electrónico vigente de un expediente.",
        method="GET",
        path="/api/v1/expedientes/{expediente_id}/indice",
        path_params=("expediente_id",),
        epic="E15",
    ),
    Tool(
        name="listar_trd",
        description="Lista las series/subseries de la Tabla de Retención Documental.",
        method="GET",
        path="/api/v1/trd",
        epic="E04",
    ),
    Tool(
        name="bandeja_tramite",
        description="Bandeja de trámite (inbox) de flujos de trabajo.",
        method="GET",
        path="/api/v1/workflows/inbox",
        epic="E05",
    ),
    Tool(
        name="consultar_fuid",
        description="FUID (inventario documental) de una transferencia.",
        method="GET",
        path="/api/v1/transferencias/{transferencia_id}/fuid",
        path_params=("transferencia_id",),
        epic="E12",
    ),
)

_BY_NAME: dict[str, Tool] = {t.name: t for t in CATALOG}


def get_tool(name: str) -> Tool | None:
    return _BY_NAME.get(name)


def visible_tools(permissions: set[str], is_root: bool = False) -> list[Tool]:
    """Tools visibles para un usuario según sus permisos (ROOT ve todas)."""
    out: list[Tool] = []
    for t in CATALOG:
        if is_root or t.permission is None or t.permission in permissions:
            out.append(t)
    return out
