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


# Mapeo por operador de SU taxonomía de estados → la máquina de estados interna
# (`en_transito`/`entregado`/`devuelto`/`fallido`). Las claves se comparan en
# MAYÚSCULAS (`estado.strip().upper()`). Un estado no mapeado NO se adivina: el
# callback lo rechaza honestamente (RF-FIR-15) — nunca se inventa una transición.
# Un conector E11 real ampliaría/afinaría estos mapas sin tocar router/servicio.
_ESTADO_MAP: dict[str, dict[str, str]] = {
    "4-72": {
        "EN_REPARTO": "en_transito",
        "EN_TRANSITO": "en_transito",
        "ENTREGADO": "entregado",
        "DEVUELTO": "devuelto",
        "NO_ENTREGADO": "fallido",
    },
    "servientrega": {
        "EN_RUTA": "en_transito",
        "TRANSITO": "en_transito",
        "ENTREGA_EXITOSA": "entregado",
        "DEVOLUCION": "devuelto",
        "FALLIDO": "fallido",
    },
    "otro": {
        "EN_TRANSITO": "en_transito",
        "ENTREGADO": "entregado",
        "DEVUELTO": "devuelto",
        "FALLIDO": "fallido",
    },
}


def mapear_estado(operador: str, estado_operador: str) -> str | None:
    """Normaliza el estado del operador a la máquina interna; None si no mapea."""
    return _ESTADO_MAP.get(operador, {}).get(estado_operador.strip().upper())


def consultar_estado(operador: str, guia: str) -> str | None:
    """Consulta PULL del estado por guía contra el operador (contrato F5).

    Stub: devuelve None (sin conector real al operador). Un adaptador E11 real
    (4-72/Servientrega REST) se enchufa aquí — devuelve el estado crudo del
    operador, que el llamante mapea con `mapear_estado`. Honesto (RF-FIR-15): no
    fabrica un estado; None ⇒ "no hay operador conectado", no "sin novedad".
    """
    return None
