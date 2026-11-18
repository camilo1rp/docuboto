# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`SET NX EX` real del registro de nonces de la identidad firmada (ADR-036, addendum C-7).

El registro en memoria de los unit tests no prueba lo que importa: que Redis hace de
verdad atómico el «primer uso». Aquí, contra el Redis real y en la base /15 con el
prefijo `itest:` para no tocar las claves del sistema vivo. Mutaciones que debe cazar:
quitar `nx=True` (el segundo `reclamar` daría True) o quitar `ex=` (la clave sin TTL).
"""

from __future__ import annotations

import os
import secrets
from urllib.parse import urlparse, urlunparse

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from orpycamcp_common import identidad
from orpycamcp_common.identidad import RegistroNoDisponible, RegistroNoncesRedis


def _url_itest() -> str:
    base = os.environ.get("REDIS_URL", "")
    if not base:
        pytest.skip("REDIS_URL no está definida: sin Redis real no hay qué probar")
    p = urlparse(base)
    return urlunparse(p._replace(path="/15"))


@pytest_asyncio.fixture
async def cliente():
    c = aioredis.from_url(_url_itest())
    try:
        yield c
    finally:
        await c.aclose()


def _clave() -> str:
    return f"itest:{identidad.clave_nonce('demo', secrets.token_urlsafe(16))}"


@pytest.mark.asyncio
async def test_el_segundo_reclamar_es_false_y_la_clave_tiene_ttl(cliente) -> None:
    registro = RegistroNoncesRedis(_url_itest())
    clave = _clave()
    try:
        assert await registro.reclamar(clave, 150) is True
        assert await registro.reclamar(clave, 150) is False
        ttl = await cliente.ttl(clave)
        assert 0 < ttl <= 180, "la clave tiene que caducar: sin TTL el registro crece sin fin"
    finally:
        await cliente.delete(clave)


@pytest.mark.asyncio
async def test_un_puerto_cerrado_es_registro_no_disponible() -> None:
    with pytest.raises(RegistroNoDisponible):
        await RegistroNoncesRedis("redis://127.0.0.1:1/15").reclamar(_clave(), 5)
