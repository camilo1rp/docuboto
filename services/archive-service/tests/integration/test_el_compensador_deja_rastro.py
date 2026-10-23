# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un control que repara en silencio es indistinguible de un sistema que no falla.

El sellado del índice electrónico se intenta en el acto del cierre y, si falla,
lo recoge el trabajo de reconciliación. Ese trabajo, al firmar, resetea
`reconcile_intentos` a cero **a propósito**, para no dejar basura en la cola de
pendientes. La consecuencia no estaba escrita: una vez reparado, no queda
rastro de que hubo que reparar.

Durante dos días el sellado síncrono falló en TODOS los cierres —la identidad
de persona viajaba sin firmar hacia signature-service y devolvía 401— y el
trabajo lo arreglaba minutos después. La pantalla decía «sin pendientes» con
toda la razón, la cadena de auditoría estaba íntegra, y el único residuo era
que el sello quedaba a nombre del sistema en vez de a nombre de quien cerró el
expediente. Nadie mira eso.

`count_sellado_diferido` es el instrumento que faltaba, y se deriva de QUIÉN
firmó, no de un contador que haya que mantener: un contador nuevo se olvidaría
de incrementarse igual que el otro se olvidó de no borrarse.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.constants import SYSTEM_RECONCILER_ID
from app.core.database import tenant_schema
from app.repositories.index_repository import IndexRepository
from app.schemas.archive import ExpedienteCreate
from app.services.archive_service import ArchiveService

pytestmark = pytest.mark.asyncio

_CLEARANCE_TOTAL = 5


async def _sp(conn: asyncpg.Connection, slug: str) -> None:
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')


async def _crear_tabla_firma(conn: asyncpg.Connection, slug: str) -> None:
    """`firma` la crea signature-service; este esquema solo tiene las
    migraciones de archivo. Se escribe el esquema EN la sentencia: un
    `CREATE TABLE` sin cualificar depende del `search_path` y puede aterrizar
    en `public`, que es el único esquema compartido por todas las entidades."""
    await conn.execute(
        f'''CREATE TABLE IF NOT EXISTS "{tenant_schema(slug)}".firma (
               id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
               objeto_tipo text, objeto_id text, firmante_id uuid,
               payload_sha256 text, provider text,
               sello_tiempo timestamptz)'''
    )


async def _indice_firmado_por(
    conn: asyncpg.Connection,
    slug: str,
    *,
    firmante: uuid.UUID,
    nivel: int = 1,
) -> None:
    """Un expediente con su índice vigente sellado por `firmante`."""
    # `nivel_seguridad` se sube DESPUÉS por SQL: crear el expediente ya
    # clasificado choca con el no-write-up (nadie clasifica por encima de su
    # propio clearance, Ley 1712/2014), y lo que se prueba aquí es el recorte
    # de LECTURA, que es el otro eje.
    exp = await ArchiveService(conn, slug).create_expediente(
        payload=ExpedienteCreate(name=f"itest sellado {firmante}", nivel_seguridad=1),
        opened_by=None,
    )
    await _crear_tabla_firma(conn, slug)
    await _sp(conn, slug)
    if nivel != 1:
        await conn.execute(
            "UPDATE expedientes SET nivel_seguridad = $2 WHERE id = $1", exp.id, nivel
        )
    firma_id = await conn.fetchval(
        """
        INSERT INTO firma (objeto_tipo, objeto_id, firmante_id, payload_sha256,
                           provider, sello_tiempo)
             VALUES ('indice', $1, $2, $3, 'xades_local', now())
          RETURNING id
        """,
        str(exp.id),
        firmante,
        "0" * 64,
    )
    await conn.execute(
        """
        INSERT INTO expediente_indice (expediente_id, version, xml, xml_sha256,
                                       content_hash, num_documentos, estado, firma_id)
             VALUES ($1, 1, '<fuid/>', $2, $3, 0, 'firmado', $4)
        """,
        exp.id,
        "0" * 64,
        "1" * 64,
        firma_id,
    )


async def test_cuenta_los_que_sello_el_sistema(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    persona = uuid.uuid4()
    await _indice_firmado_por(conn, slug, firmante=persona)
    await _indice_firmado_por(conn, slug, firmante=SYSTEM_RECONCILER_ID)
    await _indice_firmado_por(conn, slug, firmante=SYSTEM_RECONCILER_ID)

    total = await IndexRepository(conn).count_sellado_diferido(
        max_clearance=_CLEARANCE_TOTAL
    )

    assert total == 2, (
        "El recuento no distingue el sello del sistema del de la persona que "
        "cerró: sin esa distinción, un fallo permanente del sellado síncrono "
        "se ve igual que un sistema que nunca falla."
    )


async def test_sin_reparaciones_el_numero_es_cero(tenant_ctx) -> None:
    """Control positivo del propio control: si contara de más, el aviso saldría
    siempre y dejaría de significar nada."""
    slug, conn = tenant_ctx
    await _indice_firmado_por(conn, slug, firmante=uuid.uuid4())

    total = await IndexRepository(conn).count_sellado_diferido(
        max_clearance=_CLEARANCE_TOTAL
    )

    assert total == 0


async def test_no_read_up_recorta_el_recuento(tenant_ctx) -> None:
    """El número dice cuántos EXPEDIENTES hay, y eso ya es información sobre
    expedientes: se recorta por el clearance del llamante, igual que la cola de
    pendientes (RF-SEG-08)."""
    slug, conn = tenant_ctx
    await _indice_firmado_por(conn, slug, firmante=SYSTEM_RECONCILER_ID, nivel=1)
    await _indice_firmado_por(conn, slug, firmante=SYSTEM_RECONCILER_ID, nivel=3)

    repo = IndexRepository(conn)
    assert await repo.count_sellado_diferido(max_clearance=_CLEARANCE_TOTAL) == 2
    assert await repo.count_sellado_diferido(max_clearance=1) == 1, (
        "El recuento enseñó un expediente por encima del clearance del llamante."
    )


async def test_sin_catalogo_de_firmas_no_se_inventa_un_cero(tenant_ctx) -> None:
    """`firma` la crea otro servicio. Si no está, la respuesta es «no se sabe»
    (`None`), nunca «cero»: un cero afirmaría que el compensador nunca tuvo que
    actuar, y eso es exactamente lo que no se sabe. La cola de pendientes tiene
    que seguir cargando igual — un indicador que falta no puede tumbar la
    pantalla que lo acompaña."""
    slug, conn = tenant_ctx

    total = await IndexRepository(conn).count_sellado_diferido(
        max_clearance=_CLEARANCE_TOTAL
    )

    assert total is None
