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

import uuid

DLQ_GROUP = "signature-service"

# Actor atribuido a las firmas del índice producidas por el job de
# reconciliación de archive-service (`app/jobs/indice_reconciliation.py`) vía
# la ruta interna `POST /internal/sign-indice` (residual Ac. AGN 001/2024 art.
# 4.3.2.4) — NUNCA un usuario humano; distingue en `audit_log`/`firma.firmante_id`
# un sellado diferido-por-el-sistema de uno manual (`/sign-xml`, X-User-Id real).
#
# MISMO valor literal que `archive-service/app/core/constants.py::SYSTEM_RECONCILER_ID`
# — ambos servicios deben coincidir byte a byte; no hay una fuente única
# compartida entre microservicios independientes (ADR-003, sin librería común
# de constantes de dominio). Cambiar este valor en un solo lado rompe la
# atribución sin que ningún tipo lo detecte — verificar ambos si se rota.
SYSTEM_RECONCILER_ID = uuid.UUID("00000000-0000-0000-0000-000000005ec1")
