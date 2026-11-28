# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Nombre del grupo consumidor de workflow en el bus de eventos (ADR-049).

``DLQ_GROUP`` es también el ``origin_group`` con que sus dead-letters se escriben
en (y se filtran de) la tabla compartida ``evento_dead_letter``: lo leen el
worker, la consola única (``docuboto.routers.admin_deadletter``) y las métricas,
nunca un literal repetido.
"""

from __future__ import annotations

DLQ_GROUP = "workflow-service"
