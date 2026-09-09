# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El inventario lo manda el expediente, no la caja donde está guardado.

`fuid_inventory` arrancaba en `expediente_unidad`, la tabla que ata un
expediente a su unidad de conservación FÍSICA. Un expediente sin esa atadura
—el electrónico puro, que `TransferenciaService.create` admite a propósito
como «el caso de uso central de un DMS moderno»— no producía NINGUNA fila.

Lo que eso significa aguas abajo: al recibir una transferencia, `recibir()`
congela el acta de entrega llamando a `generate_fuid` para ESE expediente. Con
el inventario vacío, el acta salía con la cabecera puesta y cero registros, se
le calculaba la huella SHA-256, se le ponía un sello XAdES-T válido y la
pantalla la presentaba como «Acta de entrega — Formato Único de Inventario
Documental». La garantía criptográfica era exacta sobre un documento que no
dice qué se entregó (Ley 594/2000 arts. 15/26; Ac. AGN 001/2024 Anexo FUID) —
[[aprobar-sin-ver-el-documento]] otra vez, y [[circuito-que-no-ata]]: las dos
mitades estaban bien y la garantía vivía ENTRE ellas.

**Por qué este test es de integración y no unitario.** Los tests de `/fuid` de
`tests/test_fisico.py` mockean `conn.fetch`: le dan las filas ya hechas al
servicio, así que pasan en verde con CUALQUIER `FROM`/`JOIN`. Un defecto que
vive en la unión de dos tablas solo lo ve Postgres
([[test-que-mide-la-pieza-no-el-cableado]]).
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.repositories.fisico_repository import FisicoRepository
from app.schemas.archive import ExpedienteCreate
from app.services.archive_service import ArchiveService

pytestmark = pytest.mark.asyncio

_LIMITE = 100


async def _expediente(conn: asyncpg.Connection, slug: str, nombre: str):
    return await ArchiveService(conn, slug).create_expediente(
        payload=ExpedienteCreate(name=nombre, nivel_seguridad=1),
        opened_by=None,
    )


async def _unidad_con_ubicacion(conn: asyncpg.Connection, expediente_id: uuid.UUID) -> None:
    """Ata el expediente a una caja en una ubicación — tenencia física real."""
    ubicacion_id = await conn.fetchval(
        """
        INSERT INTO ubicacion (nombre, nivel, codigo, tipo_archivo)
             VALUES ('Depósito de prueba', 'deposito', 'DEP-ITEST', 'central')
          RETURNING id
        """
    )
    unidad_id = await conn.fetchval(
        """
        INSERT INTO unidad_conservacion (tipo, codigo, ubicacion_id, signatura, soporte)
             VALUES ('caja', 'CAJA-ITEST', $1, 'SIG-ITEST', 'papel')
          RETURNING id
        """,
        ubicacion_id,
    )
    await conn.execute(
        "INSERT INTO expediente_unidad (expediente_id, unidad_id) VALUES ($1, $2)",
        expediente_id,
        unidad_id,
    )


async def test_el_expediente_sin_caja_aparece_en_el_inventario(tenant_ctx) -> None:
    """El defecto exacto: sin fila en `expediente_unidad`, cero registros."""
    slug, conn = tenant_ctx
    exp = await _expediente(conn, slug, "itest expediente electrónico puro")

    rows = await FisicoRepository(conn, slug).fuid_inventory(
        ubicacion_id=None,
        trd_serie_id=None,
        expediente_id=exp.id,
        max_clearance=5,
        limite=_LIMITE,
    )

    assert len(rows) == 1, (
        "Un expediente sin unidad de conservación no produjo ninguna fila de "
        "inventario: el acta de entrega que se congela al recibirlo iría vacía."
    )
    fila = rows[0]
    assert fila["expediente_code"] == exp.code
    assert fila["asunto"] == "itest expediente electrónico puro"
    # Las columnas FÍSICAS van en blanco, y ese blanco es la verdad: no está en
    # ninguna caja. Inventarlas sería peor que dejarlas vacías.
    assert fila["signatura"] is None
    assert fila["unidad_codigo"] is None
    assert fila["unidad_tipo"] is None


async def test_el_expediente_con_caja_sigue_trayendo_su_signatura(tenant_ctx) -> None:
    """Control positivo: la corrección no puede vaciar la mitad que ya funcionaba."""
    slug, conn = tenant_ctx
    exp = await _expediente(conn, slug, "itest expediente en caja")
    await _unidad_con_ubicacion(conn, exp.id)

    rows = await FisicoRepository(conn, slug).fuid_inventory(
        ubicacion_id=None,
        trd_serie_id=None,
        expediente_id=exp.id,
        max_clearance=5,
        limite=_LIMITE,
    )

    assert len(rows) == 1
    assert rows[0]["signatura"] == "SIG-ITEST"
    assert rows[0]["unidad_codigo"] == "CAJA-ITEST"
    assert rows[0]["soporte"] == "papel"


async def test_filtrar_por_ubicacion_deja_fuera_al_electronico(tenant_ctx) -> None:
    """La unión pasó a ser por la izquierda: hay que comprobar que la puerta
    del archivo FÍSICO no se llenó de expedientes que no están en ningún
    depósito. El `WHERE u.ubicacion_id = $n` los descarta — pero eso es una
    consecuencia del SQL, y una consecuencia no comprobada es una suposición."""
    slug, conn = tenant_ctx
    en_caja = await _expediente(conn, slug, "itest en caja")
    await _unidad_con_ubicacion(conn, en_caja.id)
    await _expediente(conn, slug, "itest sin caja")

    ubicacion_id = await conn.fetchval("SELECT id FROM ubicacion LIMIT 1")
    rows = await FisicoRepository(conn, slug).fuid_inventory(
        ubicacion_id=ubicacion_id,
        trd_serie_id=None,
        expediente_id=None,
        max_clearance=5,
        limite=_LIMITE,
    )

    codigos = {r["expediente_code"] for r in rows}
    assert codigos == {en_caja.code}


async def test_el_inventario_completo_los_trae_a_los_dos(tenant_ctx) -> None:
    """Sin filtro, el FUID es el inventario del ARCHIVO: lo de papel y lo
    electrónico. Antes solo veía lo que estuviera en una caja."""
    slug, conn = tenant_ctx
    en_caja = await _expediente(conn, slug, "itest en caja")
    await _unidad_con_ubicacion(conn, en_caja.id)
    sin_caja = await _expediente(conn, slug, "itest sin caja")

    rows = await FisicoRepository(conn, slug).fuid_inventory(
        ubicacion_id=None,
        trd_serie_id=None,
        expediente_id=None,
        max_clearance=5,
        limite=_LIMITE,
    )

    codigos = {r["expediente_code"] for r in rows}
    assert codigos == {en_caja.code, sin_caja.code}
