# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La carga masiva de documentos TERMINA, y crea los radicados.

Por qué este test está en el tier de integración y no junto a `test_batch.py`:
el defecto que cierra **no era de lógica, era de ciclo de vida de la conexión**.
El router encolaba el job y lanzaba `asyncio.create_task(...)` pasándole la
conexión del request — la misma que seguía ocupada terminando el handler y que
se devuelve al pool en cuanto se responde. Con Postgres de verdad, la tarea
moría en el acto:

    InterfaceError: cannot perform operation: another operation is in progress

y como `create_task` no tiene a quién devolver esa excepción, no la veía nadie.
En pantalla, el lote se quedaba en «Pendiente 0/2 procesados» para siempre.

Con una conexión simulada eso NO ocurre: un mock atiende dos operaciones a la
vez sin protestar, así que el test con dobles pasaba —y pasó— sobre un código
que no funcionaba ni una sola vez en producción. Es la misma lección de la
atomicidad: el comportamiento de una conexión real no se puede simular.

El control es de resultado, no de mecanismo: se pide el lote por el mismo camino
que la pantalla y se espera a que el job **acabe**. Si el procesamiento vuelve a
correr sobre la conexión equivocada, el job no llega a `completed` y este test
falla por tiempo de espera en vez de aprobar en silencio.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import pytest
import pytest_asyncio

from app.core.database import acquire_tenant, close_pool, create_pool
from app.schemas.batch import BatchDocumentCreate, BatchDocumentsRequest
from app.services.batch_service import BatchService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def pool_del_proceso() -> AsyncIterator[None]:
    """El pool del módulo, que en producción crea el `lifespan` de la app.

    La tarea de fondo lo necesita porque su razón de ser es NO usar la conexión
    del request: sin pool no hay de dónde sacar la suya, y esa dependencia es
    parte de lo que se está comprobando.
    """
    # El DSN ya normalizado por el conftest (`postgresql+asyncpg://` es el
    # esquema de la configuración, no el que entiende asyncpg).
    from tests.integration.conftest import DSN

    await create_pool(DSN, min_size=1, max_size=3)
    try:
        yield
    finally:
        await close_pool()


@asynccontextmanager
async def _tenant_registrado(conn: asyncpg.Connection, slug: str) -> AsyncIterator[None]:
    """Registra el tenant en `public.tenants` y lo retira al terminar.

    El lote lee de ahí el código que va dentro del número de radicado. La fila
    se borra siempre —también si el test revienta— porque `public` es el único
    sitio que el fixture de schemas desechables no puede limpiar: `code` es
    UNIQUE y un residuo haría fallar la SIGUIENTE ejecución por un motivo que
    no tiene nada que ver con lo que se está probando.
    """
    # Código derivado del slug: dos tests concurrentes no pueden chocar.
    codigo = f"IT{slug.replace('itest-', '')[:6].upper()}"
    await conn.execute(
        """
        INSERT INTO public.tenants (id, slug, name, code, status)
        VALUES ($1, $2, $3, $4, 'active')
        """,
        uuid.uuid4(),
        slug,
        f"Entidad {slug}",
        codigo,
    )
    try:
        yield
    finally:
        await conn.execute("DELETE FROM public.tenants WHERE slug = $1", slug)


def _schema(slug: str) -> str:
    return f"tenant_{slug.replace('-', '_')}"


async def _esperar_job(conn: asyncpg.Connection, job_id: uuid.UUID, segundos: float = 15.0) -> dict:
    """Sondea como lo hace la pantalla, hasta que el job termina o se agota."""
    limite = asyncio.get_running_loop().time() + segundos
    fila = None
    while asyncio.get_running_loop().time() < limite:
        fila = await conn.fetchrow(
            "SELECT status, processed_items, failed_items, error_message "
            "FROM batch_jobs WHERE id = $1",
            job_id,
        )
        if fila and fila["status"] in ("completed", "failed"):
            return dict(fila)
        await asyncio.sleep(0.2)
    return dict(fila) if fila else {}


async def test_el_lote_termina_y_crea_los_radicados(
    tenant_ctx: tuple[str, asyncpg.Connection],
    pool_del_proceso: None,
) -> None:
    """De extremo a extremo con Postgres real: el job llega a `completed`.

    La aserción sobre `status` es la que mata al defecto original: con la
    conexión del request, el job se quedaba en `pending` y `processed_items` en
    cero indefinidamente.
    """
    slug, conn = tenant_ctx
    async with _tenant_registrado(conn, slug):
        await conn.execute(f'SET search_path = "{_schema(slug)}", public')

        servicio = BatchService(conn, slug)
        peticion = BatchDocumentsRequest(
            documents=[
                BatchDocumentCreate(doc_type="E", doc_class="Petición", dest_dept_code=1, subject="Lote — primer documento"),
                BatchDocumentCreate(doc_type="E", doc_class="Petición", dest_dept_code=1, subject="Lote — segundo documento"),
            ]
        )
        job_id = await servicio.create_batch_documents(peticion, uuid.uuid4())

        # La tarea se lanza como en el router: conexión PROPIA del pool. Aquí se
        # ejecuta en línea porque el test no levanta el ciclo HTTP; lo que se
        # comprueba es que procesar el lote sobre una conexión que no es la del
        # request llega hasta el final.
        async with acquire_tenant(slug) as conn_tarea:
            await BatchService(conn_tarea, slug).process_batch_documents(job_id)

        estado = await _esperar_job(conn, job_id)
        detalle = await conn.fetch(
            "SELECT item_index, status, error_message FROM batch_job_items WHERE job_id = $1",
            job_id,
        )
        assert estado.get("status") == "completed", (
            f"items: {[dict(d) for d in detalle]} — "
            "El lote no terminó. Si se quedó en 'pending', el procesamiento "
            f"volvió a correr sobre una conexión que no es suya. Estado: {estado}"
        )
        assert estado["processed_items"] == 2
        assert estado["failed_items"] == 0

        creados = await conn.fetchval(
            "SELECT count(*) FROM radicados WHERE subject LIKE 'Lote — %'"
        )
        assert creados == 2, "el job dijo que terminó, pero los radicados no están"


async def test_los_items_quedan_con_su_numero_de_radicado(
    tenant_ctx: tuple[str, asyncpg.Connection],
    pool_del_proceso: None,
) -> None:
    """Gemelo: terminar no basta, cada ítem tiene que apuntar a lo que creó.

    Un job marcado `completed` con los ítems vacíos volvería a ser un mecanismo
    que informa de un éxito que no verificó — justo la familia de defecto que
    este archivo persigue.
    """
    slug, conn = tenant_ctx
    async with _tenant_registrado(conn, slug):
        await conn.execute(f'SET search_path = "{_schema(slug)}", public')

        actor = uuid.uuid4()
        # Límite de origen de la Salida: el responsable del lote tiene URD activa en la 1.
        await conn.execute(
            "INSERT INTO urd_usuaroldep (user_id, depe_id) VALUES ($1, 1)", actor
        )
        servicio = BatchService(conn, slug)
        job_id = await servicio.create_batch_documents(
            BatchDocumentsRequest(
                documents=[BatchDocumentCreate(doc_type="S", doc_class="Petición", origin_dept_code=1, subject="Lote — salida")]
            ),
            actor,
        )
        async with acquire_tenant(slug) as conn_tarea:
            await BatchService(conn_tarea, slug).process_batch_documents(job_id)

        await _esperar_job(conn, job_id)
        items = await conn.fetch(
            "SELECT status, result_id, error_message FROM batch_job_items WHERE job_id = $1",
            job_id,
        )
        assert len(items) == 1
        assert items[0]["status"] == "success", f"motivo: {items[0]['error_message']}"
        assert items[0]["result_id"] is not None, "ítem en éxito sin el radicado que creó"
        assert items[0]["error_message"] is None


async def test_el_lote_rechaza_una_salida_con_origen_ajeno_al_responsable(
    tenant_ctx: tuple[str, asyncpg.Connection],
    pool_del_proceso: None,
) -> None:
    """El límite de origen de la Salida también rige en el lote: el ítem falla ANTES de
    gastar el consecutivo."""
    slug, conn = tenant_ctx
    async with _tenant_registrado(conn, slug):
        await conn.execute(f'SET search_path = "{_schema(slug)}", public')
        servicio = BatchService(conn, slug)
        job_id = await servicio.create_batch_documents(
            BatchDocumentsRequest(
                documents=[
                    BatchDocumentCreate(
                        doc_type="S", doc_class="Petición", origin_dept_code=1,
                        subject="Lote — salida ajena",
                    )
                ]
            ),
            uuid.uuid4(),  # sin URD en ninguna dependencia
        )
        async with acquire_tenant(slug) as conn_tarea:
            await BatchService(conn_tarea, slug).process_batch_documents(job_id)
        await _esperar_job(conn, job_id)
        item = await conn.fetchrow(
            "SELECT status, error_message FROM batch_job_items WHERE job_id = $1", job_id
        )
        assert item["status"] == "failed"
        assert "origen_fuera_de_sus_dependencias" in item["error_message"]
        assert await conn.fetchval("SELECT count(*) FROM radicados") == 0
