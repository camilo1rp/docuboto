# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lotes: quién los ve, qué asunto se lee y qué rastro dejan (auditoría AREA-09-15).

Postgres REAL: el alcance (creador / PERM_RADI nivel 3) y el no-read-up del `subject`
viven en el `WHERE`/`CASE` del SQL, y la traza en `public.audit_log`.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.core.constants import SYSTEM_RECONCILER_ID
from app.core.database import acquire_tenant
from app.services.batch_service import BatchService, LoteError
from tests.integration.test_lote_documentos_integration import (
    _tenant_registrado,
    pool_del_proceso,  # noqa: F401 — fixture, se usa por `usefixtures`
)
from tests.integration.test_lote_reanudable_integration import (
    _crear,
    _plantilla_que_hace_fallar,
    _preparar,
    _procesar,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("pool_del_proceso")]


async def _asientos(conn, slug, accion: str) -> list[dict]:
    filas = await conn.fetch(
        "SELECT actor, canal, payload FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = $2 ORDER BY id",
        slug,
        accion,
    )
    return [
        {
            "actor": f["actor"],
            "canal": f["canal"],
            "payload": json.loads(f["payload"]) if isinstance(f["payload"], str) else f["payload"],
        }
        for f in filas
    ]


async def test_un_tercero_no_ve_ni_reprocesa_un_lote_ajeno_y_recibe_el_404_neutro(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    async with _tenant_registrado(conn, slug):
        await _preparar(conn, slug)
        await _plantilla_que_hace_fallar(conn)
        creador, otro = uuid.uuid4(), uuid.uuid4()
        job = await _crear(conn, slug, creador, [None, "TPL"])
        await _procesar(slug, job)
        svc = BatchService(conn, slug)

        # El creador lo ve (control positivo) y quien tiene nivel 3 también.
        assert (await svc.get_job_status(job, caller=creador, clearance=1, ve_ajenos=False))["job_id"] == job
        assert (await svc.get_job_status(job, caller=otro, clearance=1, ve_ajenos=True))["job_id"] == job
        # Un tercero sin nivel 3: el lote «no existe» (igual que un id inventado).
        assert await svc.get_job_status(job, caller=otro, clearance=3, ve_ajenos=False) == {}
        assert await svc.get_job_status(uuid.uuid4(), caller=otro, clearance=3, ve_ajenos=False) == {}

        with pytest.raises(LoteError) as exc:
            await svc.reprocesar(job, otro, ve_ajenos=False)
        assert (exc.value.codigo, exc.value.status) == ("lote_no_encontrado", 404)
        assert await conn.fetchval("SELECT status FROM batch_job_items WHERE job_id=$1 AND item_index=1", job) == "failed", (
            "un reproceso denegado no puede tocar el ítem"
        )

        # Y no distingue «existe» por el 409 del lock: con el lote tomado por otro
        # proceso, el tercero sigue recibiendo 404, no `lote_en_proceso`.
        async with acquire_tenant(slug) as otra_replica:
            ocupado = BatchService(otra_replica, slug)
            assert await ocupado._tomar_lock(job) is True
            with pytest.raises(LoteError) as exc2:
                await svc.reprocesar(job, otro, ve_ajenos=False)
            assert exc2.value.codigo == "lote_no_encontrado"
            with pytest.raises(LoteError) as exc3:
                await svc.reprocesar(job, creador, ve_ajenos=False)
            assert exc3.value.codigo == "lote_en_proceso", "el creador sí ve el estado real"
            await ocupado._soltar_lock(job)


async def test_el_asunto_de_un_radicado_reclasificado_por_encima_del_nivel_no_se_lee(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    async with _tenant_registrado(conn, slug):
        await _preparar(conn, slug)
        await _plantilla_que_hace_fallar(conn)
        creador = uuid.uuid4()
        job = await _crear(conn, slug, creador, [None, None, "TPL"])
        await _procesar(slug, job)
        # El radicado del ítem 1 se reclasifica DESPUÉS a reservado (nivel 3).
        await conn.execute(
            "UPDATE radicados SET nivel_seguridad = 3, "
            "clasificacion_fundamento = 'Ley 1712/2014 art. 19 (prueba)', clasificacion_causal = 'por_clasificar' WHERE id = "
            "(SELECT result_id FROM batch_job_items WHERE job_id = $1 AND item_index = 1)",
            job,
        )
        svc = BatchService(conn, slug)

        bajo = await svc.get_job_status(job, caller=creador, clearance=1, ve_ajenos=False)
        por_indice = {i["item_index"]: i["subject"] for i in bajo["items"]}
        assert por_indice[0] == "Doc 0"
        assert por_indice[1] is None, "el asunto de un radicado por encima del nivel no se devuelve"
        assert por_indice[2] == "Doc 2", "un ítem fallido (sin radicado) conserva el asunto que envió quien llama"
        niveles = {i["item_index"]: i["nivel_seguridad"] for i in bajo["items"]}
        assert niveles == {0: 1, 1: None, 2: None}, "el nivel de lo que no alcanza no se devuelve"

        alto = await svc.get_job_status(job, caller=creador, clearance=3, ve_ajenos=False)
        assert {i["item_index"]: i["subject"] for i in alto["items"]}[1] == "Doc 1", "control positivo"


async def test_reprocesar_asienta_el_motivo_y_los_intentos_que_luego_limpia(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    async with _tenant_registrado(conn, slug):
        await _preparar(conn, slug)
        await _plantilla_que_hace_fallar(conn)
        creador = uuid.uuid4()
        job = await _crear(conn, slug, creador, [None, "TPL"])
        await _procesar(slug, job)
        await BatchService(conn, slug).reprocesar(job, creador, ve_ajenos=False)

        # La fila ya no lo tiene (se limpia al devolverlo a pendiente)...
        assert await conn.fetchval(
            "SELECT error_message FROM batch_job_items WHERE job_id = $1 AND item_index = 1", job
        ) is None
        # ...pero el asiento sí.
        (asiento,) = await _asientos(conn, slug, "document.lote_reprocesado")
        (item,) = asiento["payload"]["items_fallidos"]
        assert item["item_index"] == 1 and item["intentos"] == 1
        assert "expediente_ref" in item["error_message"] and item["error_tipo"]
        assert len(item["error_message"]) <= 500


async def test_el_asiento_de_un_radicado_de_lote_dice_de_que_lote_viene(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    async with _tenant_registrado(conn, slug):
        await _preparar(conn, slug)
        creador = uuid.uuid4()
        job = await _crear(conn, slug, creador, [None, None])
        await _procesar(slug, job)
        asientos = await _asientos(conn, slug, "document.radicado_created")
        assert [a["payload"]["item_index"] for a in asientos] == [0, 1]
        for a in asientos:
            assert a["payload"]["origen"] == "lote" and a["payload"]["job_id"] == str(job)
            assert a["actor"] == str(creador) and a["canal"] == "api"
            assert "en_nombre_de" not in a["payload"]


async def test_la_reanudacion_automatica_asienta_a_nombre_del_sistema(tenant_ctx) -> None:
    """El barrido (no la persona) radica lo que faltaba: el asiento no le atribuye el
    acto a quien encoló el lote, pero dice a nombre de quién se hizo."""
    slug, conn = tenant_ctx
    async with _tenant_registrado(conn, slug):
        await _preparar(conn, slug)
        creador = uuid.uuid4()
        job = await _crear(conn, slug, creador, [None, None])
        await _procesar(slug, job, reanudado=True)

        for accion in ("document.lote_reanudado", "document.radicado_created"):
            asientos = await _asientos(conn, slug, accion)
            assert asientos, accion
            for a in asientos:
                assert a["actor"] == str(SYSTEM_RECONCILER_ID), accion
                assert a["canal"] == "system"
                assert a["payload"]["en_nombre_de"] == str(creador)
        # El radicado sigue siendo del creador del lote (de él depende su tenencia).
        assert {r["registered_by"] for r in await conn.fetch("SELECT registered_by FROM radicados")} == {creador}
