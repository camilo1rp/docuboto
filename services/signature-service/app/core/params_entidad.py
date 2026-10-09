# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Parámetros de la ENTIDAD que gobiernan la firma (``sgd_param_admin``).

La política «¿puede un acto clasificado firmarse con HMAC nativo?» es decisión
de CADA entidad —depende de si ya enroló los certificados personales de sus
firmantes—, no del operador del despliegue. Por eso vive en la tabla de
parámetros del tenant (la misma que edita ``/admin/parametros`` en
tenant-service) y el ``FIRMA_PERSONAL_REQUIRE_XADES`` del entorno queda como
RESPALDO para la entidad que no lo define.

La clave nace por migración de tenant-service (``010_params_firma.sql``): un
parámetro cuyo nombre solo vive en el código no se puede configurar.
"""

from __future__ import annotations

import json
import logging

import asyncpg

from app.core.config import settings

logger = logging.getLogger("signature.params")

PARAM_REQUIRE_XADES = "firma_personal_require_xades"
MODOS_XADES = ("off", "clasificados", "todos")


async def leer_param(conn: asyncpg.Connection, clave: str) -> object | None:
    """Valor JSON de ``sgd_param_admin[clave]``; ``None`` si no hay fila o JSON null.

    Una tabla inexistente (schema de un tenant que aún no migró tenant-service)
    se trata como «la entidad no lo define». Cualquier otro fallo de base
    PROPAGA: no poder leer la política no es «la política es la laxa».

    Debe llamarse FUERA de ``conn.transaction()``: ``UndefinedTableError``
    dentro de una transacción la aborta.
    """
    try:
        crudo = await conn.fetchval("SELECT value FROM sgd_param_admin WHERE key = $1", clave)
    except asyncpg.UndefinedTableError:
        return None
    if crudo is None:
        return None
    if isinstance(crudo, (str, bytes)):
        try:
            return json.loads(crudo)
        except ValueError:
            return crudo  # texto no JSON: lo valida quien lo usa
    return crudo


async def modo_xades_de_la_entidad(conn: asyncpg.Connection) -> str:
    """Modo efectivo del gate XAdES personal: ``off`` | ``clasificados`` | ``todos``.

    1. Parámetro de la entidad, si lo define con un valor válido.
    2. Si no lo define (sin fila / null / sin tabla): el entorno
       (``settings.firma_personal_require_xades``, default ``off``).
    3. Si lo define con un valor NO reconocido (edición directa en SQL; la API
       de tenant-service lo valida): el más estricto, ``todos``, y un error en
       el log. Un valor corrupto no puede relajar en silencio una política de
       seguridad: bloquear la firma es visible y se arregla; degradarla no.
    """
    valor = await leer_param(conn, PARAM_REQUIRE_XADES)
    if valor is None:
        return str(settings.firma_personal_require_xades)
    if isinstance(valor, str) and valor.strip().lower() in MODOS_XADES:
        return valor.strip().lower()
    logger.error(
        "Parámetro %s con valor no reconocido %r: se aplica 'todos' (fail-closed).",
        PARAM_REQUIRE_XADES,
        valor,
    )
    return "todos"
