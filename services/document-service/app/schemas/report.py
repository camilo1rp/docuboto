# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de reportes/estadísticas (E09, F2)."""

from pydantic import BaseModel


class MonthCount(BaseModel):
    month: str  # 'YYYY-MM'
    count: int


class ReportSummary(BaseModel):
    total: int
    by_doc_type: dict[str, int]
    by_status: dict[str, int]
    by_month: list[MonthCount]
    by_dependencia: dict[str, int] = {}
