# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Sonda de las particiones mensuales de `audit_log` (AREA-03-06).

Las particiones las crea `public.ensure_audit_partitions(n)`, que solo ejecuta una
vez la migración 005 y después un cron EXTERNO (`scripts/audit_partitions.py`).
Ningún servicio ni compose lo invoca, así que si el cron deja de correr nadie se
entera hasta que un mes entero cae en `audit_log_default` — y ese mes ya no se
puede particionar nunca (PostgreSQL no crea una partición cuyo rango ocupan filas
del default). No se pierde nada, pero es una deuda operativa que se descubre tarde.

Esta sonda aplica la misma lógica que `scripts/audit_partitions.py --verificar` —
¿existe la partición del mes SIGUIENTE?— en solo lectura y con el rol de la app
(`pg_class` es legible por cualquiera), y la expone en `/health/ready` como
`degraded`. NO tumba la disponibilidad (503): es una alerta de operación, no una
razón para sacar el servicio del balanceador. Falta el mes que viene = el cron lleva
semanas sin correr, y eso avisa con margen.

Si la propia consulta falla, el estado es `unknown` y NO se degrada: no se acusa de
una falta que no se pudo medir.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import asyncpg

logger = logging.getLogger(__name__)

ESTADO_OK = "ok"
ESTADO_DEGRADADO = "degraded"
ESTADO_DESCONOCIDO = "unknown"


def particion_del_mes_siguiente(ahora: datetime) -> str:
    """`audit_log_AAAA_MM` del mes que sigue a `ahora` (salta de diciembre a enero)."""
    anio, mes = ahora.year, ahora.month + 1
    if mes == 13:
        anio, mes = anio + 1, 1
    return f"audit_log_{anio:04d}_{mes:02d}"


async def estado_particion_siguiente(
    conn: asyncpg.Connection, ahora: datetime | None = None
) -> str:
    """`ok` si existe la partición del mes siguiente; `degraded` si falta;
    `unknown` si no se pudo consultar."""
    nombre = particion_del_mes_siguiente(ahora or datetime.now(UTC))
    try:
        existe = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = $1)",
            nombre,
        )
    except Exception:  # noqa: BLE001 — una sonda no debe romper /health/ready
        logger.warning("No se pudo consultar la partición %s de audit_log", nombre, exc_info=True)
        return ESTADO_DESCONOCIDO
    if existe:
        return ESTADO_OK
    logger.warning(
        "Falta la partición %s de audit_log: el cron de scripts/audit_partitions.py no está corriendo",
        nombre,
    )
    return ESTADO_DEGRADADO
