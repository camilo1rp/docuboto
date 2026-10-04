# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Lotes de radicados reanudables y reprocesables (AREA-09-15), con Postgres REAL.

Lo que solo existe en el motor: el rollback por ítem, el advisory lock de sesión entre
dos conexiones, el `FOR UPDATE` del reclamo y la numeración sin huecos ni repeticiones.

Escenario base: un ítem con `doc_class` que tiene plantilla de metadatos con campo
obligatorio falla (el lote no trae metadatos); al retirar la plantilla, el reproceso lo
radica. Es un fallo determinista y que se arregla desde fuera, como en la vida real.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from app.core.database import acquire_tenant
from app.schemas.batch import BatchDocumentCreate, BatchDocumentsRequest
from app.services.batch_service import BatchService, LoteError
from tests.integration.test_lote_documentos_integration import (
    _schema,
    _tenant_registrado,
    pool_del_proceso,  # noqa: F401 — fixture, se usa por `usefixtures`
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("pool_del_proceso")]

_PLANTILLA = '{"type": "object", "required": ["expediente_ref"]}'


async def _preparar(conn: asyncpg.Connection, slug: str) -> None:
    await conn.execute(f'SET search_path = "{_schema(slug)}", public')


async def _plantilla_que_hace_fallar(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "INSERT INTO metadata_templates (tipo_documental, json_schema) VALUES ('TPL', $1::jsonb)",
        _PLANTILLA,
    )


async def _crear(conn, slug, actor, clases: list[str | None]) -> uuid.UUID:
    docs = [
        BatchDocumentCreate(
            doc_type="E", dest_dept_code=1, subject=f"Doc {i}", doc_class=c or "Petición"
        )
        for i, c in enumerate(clases)
    ]
    return await BatchService(conn, slug).create_batch_documents(
        BatchDocumentsRequest(documents=docs), actor
    )


async def _procesar(slug, job_id, **kw) -> bool:
    async with acquire_tenant(slug) as c:
        return await BatchService(c, slug).process_batch_documents(job_id, **kw)


async def _job(conn, job_id):
    return await conn.fetchrow(
        "SELECT status, processed_items, failed_items, reanudaciones, reprocesado_por, completed_at "
        "FROM batch_jobs WHERE id = $1",
        job_id,
    )


async def _tracking(conn) -> list[str]:
    return [r["tracking_number"] for r in await conn.fetch(
        "SELECT tracking_number FROM radicados ORDER BY sequence")]


async def _acciones(conn, slug) -> list[str]:
    return [r["action"] for r in await conn.fetch(
        "SELECT action FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action IN ('document.lote_reprocesado', 'document.lote_reanudado') ORDER BY id", slug)]


async def test_el_lote_con_un_item_fallido_termina_con_errores_y_marca_el_item(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    async with _tenant_registrado(conn, slug):
        await _preparar(conn, slug)
        await _plantilla_que_hace_fallar(conn)
        creador = uuid.uuid4()
        job = await _crear(conn, slug, creador, [None, "TPL", None])
        await _procesar(slug, job)

        j = await _job(conn, job)
        assert (j["status"], j["processed_items"], j["failed_items"]) == ("completado_con_errores", 2, 1)
        assert j["completed_at"] is not None
        estado = await BatchService(conn, slug).get_job_status(
            job, caller=creador, clearance=1, ve_ajenos=False
        )
        assert estado["pending_items"] == 0 and estado["reprocesable"] is True
        (err,) = estado["errores"]
        assert err["item_index"] == 1 and err["status"] == "failed"
        assert "expediente_ref" in err["error_message"] and err["error_tipo"]
        assert err["intentos"] == 1 and err["subject"] == "Doc 1"
        assert {i["status"] for i in estado["items"]} == {"success", "failed"}
        # El ítem fallido no quemó número: sin huecos.
        assert [t[-6:] for t in await _tracking(conn)] == ["000001", "000002"]


async def test_reprocesar_radica_solo_el_fallido_sin_renumerar_los_ya_radicados(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    async with _tenant_registrado(conn, slug):
        await _preparar(conn, slug)
        await _plantilla_que_hace_fallar(conn)
        creador, quien_reprocesa = uuid.uuid4(), uuid.uuid4()
        job = await _crear(conn, slug, creador, [None, "TPL", None])
        await _procesar(slug, job)
        antes = await conn.fetch(
            "SELECT item_index, result_id FROM batch_job_items WHERE job_id = $1 AND status = 'success'", job)
        numeros_antes = await _tracking(conn)

        await conn.execute("DELETE FROM metadata_templates")  # se arregla la causa
        n = await BatchService(conn, slug).reprocesar(job, quien_reprocesa, ve_ajenos=True)
        assert n == 1
        assert (await _job(conn, job))["status"] == "pending"
        assert await _procesar(slug, job) is True

        j = await _job(conn, job)
        assert (j["status"], j["processed_items"], j["failed_items"]) == ("completed", 3, 0)
        assert j["reprocesado_por"] == quien_reprocesa
        despues = await conn.fetch(
            "SELECT item_index, result_id FROM batch_job_items WHERE job_id = $1 AND status = 'success' "
            "AND item_index <> 1", job)
        assert [(r["item_index"], r["result_id"]) for r in despues] == [
            (r["item_index"], r["result_id"]) for r in antes], "los ya radicados conservan su radicado"
        numeros = await _tracking(conn)
        assert numeros[:2] == numeros_antes and len(numeros) == 3 and len(set(numeros)) == 3
        # El radicado nuevo es de quien reprocesó, y el reproceso dejó su asiento.
        quien = await conn.fetchval("SELECT registered_by FROM radicados WHERE subject = 'Doc 1'")
        assert quien == quien_reprocesa
        asiento = await conn.fetchrow(
            "SELECT actor, payload FROM public.audit_log WHERE tenant_slug = $1 "
            "AND action = 'document.lote_reprocesado'", slug)
        assert asiento["actor"] == str(quien_reprocesa)
        # Idempotente: ya no hay nada que reprocesar.
        with pytest.raises(LoteError) as exc:
            await BatchService(conn, slug).reprocesar(job, quien_reprocesa, ve_ajenos=True)
        assert (exc.value.codigo, exc.value.status) == ("nada_que_reprocesar", 409)
        assert len(await _tracking(conn)) == 3
        assert await _acciones(conn, slug) == ["document.lote_reprocesado"]


async def test_un_lote_que_otro_proceso_tiene_no_se_procesa_ni_se_reprocesa(tenant_ctx) -> None:
    """Dos réplicas, un lote: el que llega segundo no hace nada (sin doble numeración)."""
    slug, conn = tenant_ctx
    async with _tenant_registrado(conn, slug):
        await _preparar(conn, slug)
        creador = uuid.uuid4()
        job = await _crear(conn, slug, creador, [None, None])
        async with acquire_tenant(slug) as otro:
            otra = BatchService(otro, slug)
            assert await otra._tomar_lock(job) is True  # «la otra réplica» lo está procesando
            assert await _procesar(slug, job) is False
            assert await conn.fetchval("SELECT count(*) FROM radicados") == 0
            with pytest.raises(LoteError) as exc:
                await BatchService(conn, slug).reprocesar(job, creador, ve_ajenos=False)
            assert exc.value.codigo == "lote_en_proceso" and exc.value.status == 409
            await otra._soltar_lock(job)
        assert await _procesar(slug, job) is True
        assert await conn.fetchval("SELECT count(*) FROM radicados") == 2


async def test_el_reclamo_del_item_evita_el_doble_radicado_aun_sin_el_lock(tenant_ctx, monkeypatch) -> None:
    """Defensa en profundidad: aunque dos procesos entraran a la vez (lock saltado),
    cada ítem se radica UNA vez. Mutación: quitar el `FOR UPDATE` + comprobación → 2N."""
    slug, conn = tenant_ctx
    async with _tenant_registrado(conn, slug):
        await _preparar(conn, slug)
        job = await _crear(conn, slug, uuid.uuid4(), [None, None, None])

        async def _siempre(self, job_id):
            return True

        monkeypatch.setattr(BatchService, "_tomar_lock", _siempre)
        await asyncio.gather(_procesar(slug, job), _procesar(slug, job))
        assert await conn.fetchval("SELECT count(*) FROM radicados") == 3
        assert len(set(await _tracking(conn))) == 3
        assert (await _job(conn, job))["processed_items"] == 3


async def test_el_barrido_reanuda_el_lote_interrumpido_sin_renumerar_lo_ya_radicado(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    async with _tenant_registrado(conn, slug):
        await _preparar(conn, slug)
        creador = uuid.uuid4()
        job = await _crear(conn, slug, creador, [None, None, None])
        await _procesar(slug, job)
        antes = await _tracking(conn)
        # Simula un reinicio a media carga: el último ítem no llegó a procesarse.
        await conn.execute(
            "DELETE FROM radicados WHERE id = (SELECT result_id FROM batch_job_items "
            "WHERE job_id = $1 AND item_index = 2)", job)
        await conn.execute(
            "UPDATE batch_job_items SET status = 'pending', result_id = NULL WHERE job_id = $1 "
            "AND item_index = 2", job)
        await conn.execute(
            "UPDATE batch_jobs SET status = 'processing', completed_at = NULL, "
            "created_at = now() - interval '1 hour' WHERE id = $1", job)

        async with acquire_tenant(slug) as c:
            assert await BatchService(c, slug).reanudar_interrumpidos(60) == 1
        j = await _job(conn, job)
        assert (j["status"], j["processed_items"], j["failed_items"], j["reanudaciones"]) == ("completed", 3, 0, 1)
        numeros = await _tracking(conn)
        assert numeros[:2] == antes[:2] and len(set(numeros)) == 3
        assert await _acciones(conn, slug) == ["document.lote_reanudado"]
        # Una segunda pasada no encuentra nada que retomar.
        async with acquire_tenant(slug) as c:
            assert await BatchService(c, slug).reanudar_interrumpidos(60) == 0


async def test_un_lote_recien_creado_no_se_reanuda_aun(tenant_ctx) -> None:
    """Lo está arrancando su propia petición: el barrido espera `despues_de_segundos`."""
    slug, conn = tenant_ctx
    async with _tenant_registrado(conn, slug):
        await _preparar(conn, slug)
        job = await _crear(conn, slug, uuid.uuid4(), [None])
        async with acquire_tenant(slug) as c:
            assert await BatchService(c, slug).reanudar_interrumpidos(3600) == 0
        assert (await _job(conn, job))["status"] == "pending"


async def test_la_retencion_borra_los_completados_antiguos_y_conserva_los_que_tienen_errores(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    async with _tenant_registrado(conn, slug):
        await _preparar(conn, slug)
        await _plantilla_que_hace_fallar(conn)
        ok = await _crear(conn, slug, uuid.uuid4(), [None])
        con_errores = await _crear(conn, slug, uuid.uuid4(), ["TPL"])
        reciente = await _crear(conn, slug, uuid.uuid4(), [None])
        for j in (ok, con_errores, reciente):
            await _procesar(slug, j)
        await conn.execute(
            "UPDATE batch_jobs SET completed_at = now() - interval '200 days' WHERE id = ANY($1::uuid[])",
            [ok, con_errores])
        servicio = BatchService(conn, slug)
        assert await servicio.purgar_lotes_antiguos(0) == 0, "0 días = retención desactivada"
        assert await servicio.purgar_lotes_antiguos(90) == 1
        quedan = {r["id"] for r in await conn.fetch("SELECT id FROM batch_jobs")}
        assert quedan == {con_errores, reciente}
        assert await conn.fetchval("SELECT count(*) FROM batch_job_items WHERE job_id = $1", ok) == 0
        assert await conn.fetchval("SELECT count(*) FROM radicados") == 2, "los radicados no se tocan"
