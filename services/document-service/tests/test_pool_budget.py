# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Presupuesto de conexiones a Postgres.

Los nueve pools del sistema se creaban con `max_size=10` **escrito a mano** y
**sin `command_timeout`**. Dos consecuencias distintas:

1. El reparto no se podía ajustar por despliegue. Con las réplicas del
   manifiesto de producción son 110 conexiones de las aplicaciones sobre un
   Postgres que concede 100 — y que además hospeda a Keycloak. El síntoma que ve
   la institución son **fallos de inicio de sesión** intermitentes, sin relación
   aparente con la carga documental que los provoca. La aritmética completa está
   en `infra/docker-compose.prod.yml`.
2. Sin tope por consulta, una consulta atascada retiene su conexión
   indefinidamente: el pool se agota y las peticiones se quedan esperando en vez
   de fallar, que es la forma más difícil de diagnosticar.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.core import database
from app.core.config import settings

pytestmark = pytest.mark.asyncio


async def test_el_pool_usa_los_valores_de_configuracion() -> None:
    with patch("asyncpg.create_pool", new=AsyncMock()) as crear:
        await database.create_pool("postgresql://x/y")

    kwargs = crear.await_args.kwargs
    assert kwargs["min_size"] == settings.db_pool_min_size
    assert kwargs["max_size"] == settings.db_pool_max_size


async def test_el_pool_fija_un_tope_por_consulta() -> None:
    """Sin `command_timeout` una consulta atascada nunca suelta su conexión."""
    with patch("asyncpg.create_pool", new=AsyncMock()) as crear:
        await database.create_pool("postgresql://x/y")

    assert crear.await_args.kwargs["command_timeout"] == settings.db_command_timeout
    assert settings.db_command_timeout > 0


async def test_el_llamante_puede_imponer_su_tamano() -> None:
    """Los tests y las herramientas de operación fijan el suyo."""
    with patch("asyncpg.create_pool", new=AsyncMock()) as crear:
        await database.create_pool("postgresql://x/y", min_size=1, max_size=3)

    kwargs = crear.await_args.kwargs
    assert (kwargs["min_size"], kwargs["max_size"]) == (1, 3)


async def test_el_tamano_es_configurable_por_entorno() -> None:
    """Es lo que permite cuadrar el presupuesto sin tocar código.

    El reparto depende de cuántos servicios y réplicas tenga el despliegue, así
    que no puede ser una constante del código — que es como estaba.
    """
    assert "db_pool_max_size" in type(settings).model_fields
