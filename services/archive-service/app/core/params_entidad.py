# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Política XAdES de la ENTIDAD (``sgd_param_admin``), leída igual que lo hace signature-service.

``firma_personal_require_xades`` (``off`` | ``clasificados`` | ``todos``) es decisión de cada
entidad y vive en la tabla de parámetros del tenant (la edita ``/admin/parametros`` de
tenant-service). El ``acta_firma_personal_require`` del entorno de archive-service queda como
RESPALDO para la entidad que no la define, no como la política: mientras fue lo único que se
leía, una entidad que había exigido XAdES a sus firmantes veía su acta ``sellada`` y no
``firma_personal_pendiente``.

Misma resolución que ``signature-service/app/core/params_entidad.py``:
1. parámetro de la entidad con un valor válido;
2. sin fila / null / sin tabla: el entorno;
3. valor NO reconocido (edición directa en SQL): el más estricto, ``todos`` (fail-closed).
"""

from __future__ import annotations

import json
import logging

import asyncpg

from app.core.config import settings

logger = logging.getLogger(__name__)

PARAM_REQUIRE_XADES = "firma_personal_require_xades"
MODOS_XADES = ("off", "clasificados", "todos")


async def leer_param(conn: asyncpg.Connection, clave: str) -> object | None:
    """Valor JSON de ``sgd_param_admin[clave]``; ``None`` si no hay fila, JSON null o tabla.

    Debe llamarse FUERA de ``conn.transaction()`` (un ``UndefinedTableError`` dentro de una
    transacción la aborta) y con el ``search_path`` del tenant ya fijado."""
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
            return crudo
    return crudo


async def modo_xades_de_la_entidad(conn: asyncpg.Connection) -> str:
    """Modo efectivo del gate XAdES personal de la entidad: ``off`` | ``clasificados`` | ``todos``."""
    valor = await leer_param(conn, PARAM_REQUIRE_XADES)
    if valor is None:
        return str(settings.acta_firma_personal_require)
    if isinstance(valor, str) and valor.strip().lower() in MODOS_XADES:
        return valor.strip().lower()
    logger.error(
        "Parámetro %s con valor no reconocido %r: se aplica 'todos' (fail-closed).",
        PARAM_REQUIRE_XADES,
        valor,
    )
    return "todos"


def gate_xades_activo(nivel_seguridad: int, modo: str) -> bool:
    """¿La política exige la firma personal XAdES para un expediente de este nivel?"""
    if modo == "todos":
        return True
    if modo == "clasificados":
        return nivel_seguridad >= 2
    return False
