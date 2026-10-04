# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Identidad de sistema para actos disparados SIN un usuario real detrás.

MISMO valor literal que `archive-service/app/core/constants.py::SYSTEM_RECONCILER_ID`
y `signature-service/app/core/constants.py::SYSTEM_RECONCILER_ID` — un único UUID
convencional para atribuir, en `audit_log`, los actos que ejecuta un job de
reconciliación (o una llamada de servicio-a-servicio equivalente) en vez de un
usuario humano. NO es una fila real en `auth_users`: nunca debe usarse para
autorizar (RBAC), solo para trazabilidad.

Usado por `POST /preservacion/proteger-indice-internal` (D-02, gate
`require_internal_token`, E10 Inc.1): el disparo del cableado WORM desde la
3ra fase de reconciliación de archive-service (`app/jobs/indice_reconciliation.py
::run_once_worm`) no tiene un `X-User-Id` real que enviar.
"""

import uuid

SYSTEM_RECONCILER_ID = uuid.UUID("00000000-0000-0000-0000-000000005ec1")


def canal_del_acto(actor: str | None) -> str:
    """Canal del asiento de un acto de preservación (AREA-03-07).

    Las rutas `*-internal` las dispara el job de reconciliación de archive-service con
    `SYSTEM_RECONCILER_ID` como actor, y no llevan `X-Canal` (no hay persona detrás):
    sin esto se asentaban 'api'. Un actor humano hereda el canal de su petición.
    """
    from orpycamcp_common.audit import CANAL_PETICION

    if actor is not None and str(actor) == str(SYSTEM_RECONCILER_ID):
        return "system"
    return CANAL_PETICION
