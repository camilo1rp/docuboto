# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Constantes de servicio compartidas entre módulos (residual Ac. AGN 001/2024
art. 4.3.2.4, reconciliación del sellado del índice electrónico)."""

from __future__ import annotations

import uuid

# Actor atribuido a las firmas del índice producidas por
# `app/jobs/indice_reconciliation.py` vía la ruta interna de signature-service
# (`POST /internal/sign-indice`) — NUNCA un usuario humano. Se escribe en
# `expediente_eventos.actor`/`audit_log.actor` cuando el sellado lo completa
# el job, en vez del `actor_id` (X-User-Id) real de quien cerró el expediente.
#
# MISMO valor literal que
# `signature-service/app/core/constants.py::SYSTEM_RECONCILER_ID` — ambos
# servicios deben coincidir byte a byte; no hay una fuente única compartida
# entre microservicios independientes (ADR-003, sin librería común de
# constantes de dominio). Cambiar este valor en un solo lado rompe la
# atribución sin que ningún tipo lo detecte — verificar ambos si se rota.
SYSTEM_RECONCILER_ID = uuid.UUID("00000000-0000-0000-0000-000000005ec1")
