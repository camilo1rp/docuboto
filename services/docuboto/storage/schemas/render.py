# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de generación del documento de salida (E23, RF-PRO-03)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RenderDocumentoRequest(BaseModel):
    #: Cuerpo HTML **ya combinado y ya saneado**. Este servicio pinta; no
    #: combina ni sanea — esas dos defensas viven donde tienen el contexto.
    cuerpo_html: str = Field(..., min_length=1)
    #: Membrete, pie, número, fecha y márgenes. Todo se escapa al pintarlo: son
    #: datos de la entidad y del radicado, no marcado.
    pagina: dict[str, Any] = Field(default_factory=dict)
    #: Perfil de conservación. Vacío = el que declare el despliegue.
    perfil: str | None = None


class RenderDocumentoResponse(BaseModel):
    formato: str
    herramienta: str
    contenido_b64: str
    bytes: int
