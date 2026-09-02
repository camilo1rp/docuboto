# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Alcance de plantillas por dependencia (E23 Inc.5, RF-PRO-06).

Contra mocks esto no se puede probar: el recorte vive en el `WHERE` de la
consulta, y con un mock quien decide qué filas vuelven es el mock. Aquí se
insertan plantillas de verdad, se asignan de verdad, y se lee.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.borrador_repository import BorradorRepository

pytestmark = pytest.mark.asyncio

JURIDICA = 100
CONTRATACION = 200


async def _repo(conn: asyncpg.Connection, slug: str) -> BorradorRepository:
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
    return BorradorRepository(conn)


async def _plantilla(repo: BorradorRepository, nombre: str, *, es_global: bool):
    return await repo.insert_plantilla(
        {
            "nombre": nombre,
            "cuerpo": "Cordial saludo.",
            "tipo_documental": "Oficio",
            "activo": True,
            "es_global": es_global,
        }
    )


async def test_cada_dependencia_ve_la_suya_y_las_globales(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo = await _repo(conn, slug)

    generica = await _plantilla(repo, "Oficio generico", es_global=True)
    de_juridica = await _plantilla(repo, "Concepto juridico", es_global=False)
    de_contratacion = await _plantilla(repo, "Acta de contrato", es_global=False)
    await repo.set_dependencias_plantilla(de_juridica["id"], [JURIDICA])
    await repo.set_dependencias_plantilla(de_contratacion["id"], [CONTRATACION])

    vistas = {r["id"] for r in await repo.list_plantillas(None, depe_codigos=[JURIDICA])}
    assert generica["id"] in vistas
    assert de_juridica["id"] in vistas
    assert de_contratacion["id"] not in vistas, (
        "una plantilla de otra dependencia no debe aparecer: con cuarenta en la "
        "lista, la que hace falta no se encuentra y el camino corto es no usar "
        "ninguna"
    )


async def test_sin_URD_se_ven_SOLO_las_globales(tenant_ctx) -> None:
    """El vacío no es un atajo a «sin restricción».

    Es la misma regla fail-closed que el clearance, donde lo dudoso cae a
    Pública. Si una lista vacía significara «todas», bastaría con quitarle la
    URD a alguien para ampliarle lo que ve.
    """
    slug, conn = tenant_ctx
    repo = await _repo(conn, slug)

    generica = await _plantilla(repo, "Oficio generico", es_global=True)
    restringida = await _plantilla(repo, "Concepto juridico", es_global=False)
    await repo.set_dependencias_plantilla(restringida["id"], [JURIDICA])

    vistas = {r["id"] for r in await repo.list_plantillas(None, depe_codigos=[])}
    assert vistas == {generica["id"]}


async def test_sin_recorte_se_ve_todo(tenant_ctx) -> None:
    # `None` = administración: hay que poder gestionar una plantilla que no se
    # usa desde la propia dependencia.
    slug, conn = tenant_ctx
    repo = await _repo(conn, slug)

    generica = await _plantilla(repo, "Oficio generico", es_global=True)
    restringida = await _plantilla(repo, "Concepto juridico", es_global=False)
    await repo.set_dependencias_plantilla(restringida["id"], [JURIDICA])

    vistas = {r["id"] for r in await repo.list_plantillas(None, depe_codigos=None)}
    assert vistas == {generica["id"], restringida["id"]}


async def test_una_plantilla_no_global_sin_dependencias_no_la_ve_nadie(tenant_ctx) -> None:
    # Estado válido a propósito: una plantilla retirada de circulación sin
    # borrar su historial. Es lo que permite dejar de ofrecer una plantilla
    # obsoleta sin perder los documentos que se produjeron con ella.
    slug, conn = tenant_ctx
    repo = await _repo(conn, slug)

    retirada = await _plantilla(repo, "Formato derogado", es_global=False)
    for codigos in ([JURIDICA], [CONTRATACION], []):
        vistas = {r["id"] for r in await repo.list_plantillas(None, depe_codigos=codigos)}
        assert retirada["id"] not in vistas
    # Pero administración sí la ve, o no se podría reactivar.
    todas = {r["id"] for r in await repo.list_plantillas(None, depe_codigos=None)}
    assert retirada["id"] in todas


async def test_el_recorte_se_combina_con_activo(tenant_ctx) -> None:
    # Los dos filtros se conjugan; si uno pisara al otro, una plantilla inactiva
    # seguiría ofreciéndose para redactar.
    slug, conn = tenant_ctx
    repo = await _repo(conn, slug)

    activa = await _plantilla(repo, "Vigente", es_global=True)
    inactiva = await _plantilla(repo, "Antigua", es_global=True)
    await conn.execute("UPDATE plantilla SET activo = false WHERE id = $1", inactiva["id"])

    vistas = {r["id"] for r in await repo.list_plantillas(True, depe_codigos=[JURIDICA])}
    assert vistas == {activa["id"]}


async def test_borrar_la_plantilla_borra_su_asignacion(tenant_ctx) -> None:
    """`ON DELETE CASCADE` con la dirección mirada.

    Lo que cuelga es una ASIGNACIÓN, no un documento de archivo: borrar la
    plantilla debe borrar a quién estaba asignada. En este proyecto una FK en
    cascada ya se llevó un acta por delante, así que la dirección se comprueba.
    """
    slug, conn = tenant_ctx
    repo = await _repo(conn, slug)

    plantilla = await _plantilla(repo, "Temporal", es_global=False)
    await repo.set_dependencias_plantilla(plantilla["id"], [JURIDICA, CONTRATACION])
    assert await repo.dependencias_de_plantilla(plantilla["id"]) == [JURIDICA, CONTRATACION]

    await repo.delete_plantilla(plantilla["id"])
    restantes = await conn.fetchval(
        "SELECT count(*) FROM plantilla_dependencia WHERE plantilla_id = $1", plantilla["id"]
    )
    assert restantes == 0


async def test_sin_las_tablas_de_identidad_se_recorta_a_las_globales(tenant_ctx) -> None:
    """Fail-closed cuando el tenant no está provisionado del todo.

    `urd_usuaroldep` la crea auth-service, no este servicio, y el tier de
    integración provisiona solo las migraciones propias — que es exactamente el
    escenario que destapó el defecto: sin capturar `UndefinedTableError`, el
    listado de plantillas respondía 500 en un tenant a medio provisionar.

    Se devuelve la lista VACÍA, que recorta a solo las globales, nunca `None`,
    que significaría «sin restricción». Mismo criterio que
    `resolve_caller_clearance`, donde lo dudoso cae a Pública.
    """
    slug, conn = tenant_ctx
    repo = await _repo(conn, slug)
    assert await repo.dependencias_del_usuario(uuid.uuid4()) == []
