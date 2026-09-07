# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Los streams de dominio llevan contenido y tienen que caducar.

Los de dead-letter ya se recortaban; los de dominio no los recortaba nadie, y son
justamente los que llevan el asunto y el nombre del ciudadano de cada radicación.
Quedaban en claro, sin caducar, fuera del schema de la entidad y fuera de la
Tabla de Retención Documental que gobierna todo lo demás del sistema.
"""

from unittest.mock import AsyncMock

import pytest

from orpycamcp_common.events import trim_stream

pytestmark = pytest.mark.asyncio


async def test_recorta_por_ANTIGUEDAD_y_no_por_numero_de_entradas():
    """Con un tope de entradas la ventana dependería del volumen del despliegue:
    una entidad con mucho tráfico conservaría minutos y una tranquila, meses.
    Por tiempo la promesa es la misma para todas."""
    redis = AsyncMock()
    redis.xtrim = AsyncMock(return_value=7)

    eliminadas = await trim_stream(redis, "orpycamcp.document.events", max_age_ms=86_400_000)

    assert eliminadas == 7
    _, kwargs = redis.xtrim.await_args
    assert "minid" in kwargs, "se recortó por número de entradas, no por antigüedad"
    assert "maxlen" not in kwargs


async def test_el_corte_deja_fuera_lo_mas_viejo_que_la_ventana():
    import datetime

    redis = AsyncMock()
    redis.xtrim = AsyncMock(return_value=0)
    ventana_ms = 30 * 86_400_000

    await trim_stream(redis, "orpycamcp.document.events", max_age_ms=ventana_ms)

    corte_ms = int(redis.xtrim.await_args.kwargs["minid"].split("-")[0])
    ahora_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    # El corte cae en la ventana pedida, con holgura para el tiempo de ejecución.
    assert ahora_ms - ventana_ms - 5000 <= corte_ms <= ahora_ms - ventana_ms + 5000


async def test_una_ventana_absurda_no_borra_el_stream_entero():
    """Una configuración disparatada no puede convertirse en pérdida de datos."""
    redis = AsyncMock()
    redis.xtrim = AsyncMock(return_value=0)

    await trim_stream(redis, "s", max_age_ms=10**18)

    assert redis.xtrim.await_args.kwargs["minid"] == "0-0"
