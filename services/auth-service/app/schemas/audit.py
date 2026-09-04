# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de consulta y verificación de auditoría (E08, ADR-008)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditEntry(BaseModel):
    id: int
    ts: datetime
    service: str
    actor: str | None
    canal: str
    action: str
    object_type: str
    object_ref: str | None
    payload: dict[str, Any]
    hash: str


class RoturaCadenaOut(BaseModel):
    """Una fila de `audit_log` cuyo hash no cuadra. Content-free: solo el id y
    el motivo técnico — nunca el objeto ni el actor de esa entrada."""

    id: int
    motivo: str


class AuditVerifyResponse(BaseModel):
    tenant_slug: str
    ok: bool
    #: Se conserva por compatibilidad con los consumidores existentes; es
    #: `roturas[0].id`. Los campos nuevos son ADITIVOS: quien solo leía
    #: `{ok, broken_id}` sigue funcionando igual.
    broken_id: int | None = None
    #: Lo que la entidad puede AFIRMAR, que es más que «pasa o no pasa»: un
    #: registro append-only no se puede reparar, así que tras una rotura lo
    #: útil es saber cuántas hay, cuáles, y cuántos asientos SÍ verifican.
    total: int = 0
    verificados: int = 0
    total_roturas: int = 0
    roturas: list[RoturaCadenaOut] = []
    #: Roturas contadas pero no enumeradas (tope del verificador).
    roturas_omitidas: int = 0
