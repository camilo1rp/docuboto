# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""orpycamcp_common — utilidades compartidas entre microservicios de OrpycaMCP.

Fuente única de verdad para lógica transversal delicada (ADR-010):

- ``audit``  — escritura en ``public.audit_log`` con cadena de hash por tenant
               (ADR-008). Único punto de escritura de auditoría.
- ``events`` — sobre canónico de eventos Redis Streams + publicación/consumo
               idempotente (00-contrato-eventos.md).

Se instala en cada imagen Docker vía ``pip install /shared`` (no se publica a
ningún registry; se versiona con el monorepo).
"""

from orpycamcp_common import audit, events

__version__ = "0.1.0"
__all__ = ["audit", "events", "__version__"]
