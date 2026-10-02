# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Identidad de sistema para actos disparados SIN un usuario detrás.

MISMO valor literal que `storage-service`, `archive-service` y `signature-service`
(`app/core/constants.py::SYSTEM_RECONCILER_ID`): un único UUID convencional para
atribuir, en `audit_log`, lo que ejecuta un barrido o una reconciliación y no una
persona. NO es una fila de `auth_users`: jamás se usa para autorizar, solo para
trazabilidad.

Lo usa la reanudación AUTOMÁTICA de un lote de radicados interrumpido (el barrido, no
quien lo encoló, es quien radica lo que faltaba): el asiento va a nombre de este actor y
lleva `en_nombre_de` = quien encoló el lote, para que nadie lea que esa persona hizo el
acto en el momento de la reanudación.
"""

import uuid

SYSTEM_RECONCILER_ID = uuid.UUID("00000000-0000-0000-0000-000000005ec1")
