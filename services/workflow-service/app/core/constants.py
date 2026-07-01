# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Service-identifying constants shared across modules that must NOT import
each other directly (avoids a circular import between the worker and the job).

``DLQ_GROUP`` is the single source of truth for this service's Redis consumer
group name, which doubles as the ``origin_group`` value written to (and
filtered on) the shared ``evento_dead_letter`` table — see the module-level
note in ``app/repositories/dead_letter_repository.py``. Every reader of
``evento_dead_letter`` (the admin router, the retention job, and the worker's
``_persist_dead_letter``) MUST source the value from here rather than
hardcoding the literal, so the three call sites can never drift apart.

This lives in its own module (not in ``app/workers/document_consumer.py``,
where it used to be defined) because ``document_consumer`` imports
``app.jobs.deadletter_retention`` at module scope, and the retention job needs
this same constant: importing it back from ``document_consumer`` would create
an import cycle (and ``GROUP`` would not exist yet at that point in
``document_consumer``'s own module initialisation).
"""

from __future__ import annotations

DLQ_GROUP = "workflow-service"
