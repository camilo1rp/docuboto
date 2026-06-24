# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Proveedor de operador postal pluggable (E20).

Genera la guía del operador. El stub por defecto produce una guía determinista
(sin llamar a ningún operador real); un proveedor real (4-72/Servientrega API)
se conecta aquí sin cambiar repositorio ni endpoints. Coherente con el patrón
pluggable del proyecto (ADR-006 para IA; mismo principio aquí).
"""

from __future__ import annotations

import hashlib
import uuid

_PREFIX = {"4-72": "C472", "servientrega": "SE", "otro": "PG"}


def generar_guia(operador: str, radicado_id: uuid.UUID) -> str:
    h = hashlib.sha256(f"{operador}:{radicado_id}".encode()).hexdigest()[:10].upper()
    return f"{_PREFIX.get(operador, 'PG')}-{h}"
