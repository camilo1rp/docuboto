# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""C4/C12 (ADR-039) contra Postgres REAL: lo que un mock no puede decir.

Que el trigger de frontera existe y rechaza, que los CHECK son los de la migración 050, que
dos aprobaciones concurrentes dan UNA ejecución, que un fallo del asiento o del outbox se
lleva el nivel, la aprobación y el evento, y que los `WHERE` de la solicitud (separación de
funciones, vigencia, clearance) son los que deciden — no el diagnóstico.

Mutaciones del diseño (verificadas una por una, ver el informe de la tanda):
 1. quitar `s.solicitado_por <> $2` de `aprobar`        -> `test_aprobar_la_propia_...`
 2. quitar el CHECK `rs_separacion_funciones`            -> `test_los_check_de_la_solicitud_...`
 3. quitar `r.nivel_seguridad <= $3` de `aprobar`        -> `test_aprobar_exige_clearance_...`
 4. quitar `s.vence_at > now()` de `aprobar`             -> `test_una_solicitud_vencida_...`
 5. quitar el 409 del PATCH desde 3 / quitar el trigger  -> `test_el_patch_...` / `test_el_trigger_...`
 6. mover `outbox.encolar` fuera de la tx                -> `test_si_no_se_puede_encolar_...`
 7. `clasificado_at = now()` incondicional               -> `test_remotivar_...` / `test_set_classification_...`
 8. añadir `tracking_number`/`fundamento` al SELECT      -> `test_el_select_interno_...`
11. quitar el clearance del WHERE del bloqueo            -> `test_el_patch_con_clearance_2_...`
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.database import tenant_schema
from app.repositories.document_repository import DocumentRepository
from app.repositories.reclasificacion_repository import ReclasificacionRepository
from app.schemas.desclasificacion import DesclasificacionRequest
from app.services.desclasificacion_service import DesclasificacionService
from app.services.document_service import DocumentService
from tests.integration import conftest as ic

pytestmark = pytest.mark.integration

_MIGRACION = (
    Path(__file__).parents[2] / "migrations" / "tenant" / "050_reserva_catalogo_y_desclasificacion.sql"
)

ANA = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")  # solicita
BETO = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")  # aprueba
CARLA = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")  # segundo aprobador

_PEDIDO = DesclasificacionRequest(nivel_seguridad=1, motivo="Cesó la causa de la clasificación")


@pytest.fixture
def clearance(monkeypatch) -> dict[uuid.UUID, int]:
    """Clearance por persona, sin tablas RBAC: el SQL bajo prueba es el de la solicitud."""
    por_persona: dict[uuid.UUID, int] = {ANA: 3, BETO: 3, CARLA: 3}

    async def _resolver(conn, user_id):
        return por_persona.get(user_id, 1)

    monkeypatch.setattr("app.services.desclasificacion_service.resolve_caller_clearance", _resolver)
    monkeypatch.setattr("app.services.document_service.resolve_caller_clearance", _resolver)
    return por_persona


async def _en(tenant_ctx) -> tuple[str, asyncpg.Connection]:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    return slug, conn


async def _radicado(
    conn, *, nivel: int = 3, causal: str | None = None, plazo: int | None = None
) -> uuid.UUID:
    if causal is None and nivel == 3:
        causal = "art18_a_intimidad"
    if causal is None and nivel == 2:
        causal, plazo = "art19_i_salud_publica", 12
    return await conn.fetchval(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, tenant_code, "
        "nivel_seguridad, clasificacion_fundamento, clasificacion_causal, "
        "clasificacion_plazo_meses, clasificado_at) "
        "VALUES ($1, 'E', 2026, 1, 'ASUNTO RESERVADO', 'DEMO', $2, "
        "CASE WHEN $2::smallint >= 2 THEN 'Ley 1581 de 2012' END, $3, $4, "
        "CASE WHEN $2::smallint >= 2 THEN now() - interval '30 days' END) RETURNING id",
        f"2026-DEMO-E-{uuid.uuid4().hex[:6]}",
        nivel,
        causal if nivel >= 2 else None,
        plazo,
    )


async def _solicitud(
    conn, rid: uuid.UUID, *, por: uuid.UUID = ANA, vence: str | None = None, destino: int = 1
) -> uuid.UUID:
    vence_sql = vence or "now() + interval '15 days'"
    return await conn.fetchval(
        "INSERT INTO reclasificacion_solicitudes (radicado_id, nivel_anterior, nivel_nuevo, motivo, "
        "causal_codigo, fundamento_juridico, plazo_meses, solicitado_por, vence_at) "
        "VALUES ($1, 3, $2, 'Cesó la causa de la clasificación', "
        "CASE WHEN $2::smallint = 2 THEN 'art19_i_salud_publica' END, "
        "CASE WHEN $2::smallint = 2 THEN 'Art. 19 lit. i' END, "
        f"CASE WHEN $2::smallint = 2 THEN 12 END, $3, {vence_sql}) "
        "RETURNING id",
        rid,
        destino,
        por,
    )


def _svc(conn, slug, redis=None) -> DesclasificacionService:
    return DesclasificacionService(conn, slug, redis)


async def _nivel(conn, rid) -> int:
    return await conn.fetchval("SELECT nivel_seguridad FROM radicados WHERE id = $1", rid)


async def _asientos(conn, slug, rid) -> list[str]:
    tracking = await conn.fetchval("SELECT tracking_number FROM radicados WHERE id = $1", rid)
    return [
        r["action"]
        for r in await conn.fetch(
            "SELECT action FROM public.audit_log WHERE tenant_slug = $1 AND object_ref = $2 ORDER BY id",
            slug,
            tracking,
        )
    ]


async def _eventos(conn) -> list[asyncpg.Record]:
    return list(await conn.fetch("SELECT tipo, dedup_key, payload FROM eventos_salientes ORDER BY id"))


class _Redis:
    async def xadd(self, *a, **k):
        return b"1-0"


# --------------------------------------------------------------------- la frontera (trigger)


@pytest.mark.asyncio
async def test_el_trigger_impide_bajar_desde_3_por_cualquier_ruta(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    for destino in (1, 2):
        with pytest.raises(asyncpg.PostgresError) as exc:
            await conn.execute(
                "UPDATE radicados SET nivel_seguridad = $2 WHERE id = $1", rid, destino
            )
        assert exc.value.hint == "desclasificacion_requiere_aprobacion"
    assert await _nivel(conn, rid) == 3


@pytest.mark.asyncio
async def test_una_aprobacion_vieja_no_es_pase_para_bajar_despues(tenant_ctx) -> None:
    """`resuelto_at = now()` ata la aprobación a LA transacción: aprobada en otra, no sirve."""
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    sid = await _solicitud(conn, rid)
    await conn.execute(
        "UPDATE reclasificacion_solicitudes SET estado = 'aprobada', resuelto_por = $2, "
        "resuelto_at = now() WHERE id = $1", sid, BETO,
    )  # su propia transacción (autocommit)
    with pytest.raises(asyncpg.PostgresError) as exc:
        await conn.execute("UPDATE radicados SET nivel_seguridad = 1 WHERE id = $1", rid)
    assert exc.value.hint == "desclasificacion_requiere_aprobacion"


@pytest.mark.asyncio
async def test_aprobada_en_la_misma_transaccion_si_deja_bajar(tenant_ctx) -> None:
    """Control positivo: sin él, el trigger podría estar rechazando TODO y los tests de
    arriba saldrían verdes por la razón equivocada."""
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    sid = await _solicitud(conn, rid)
    async with conn.transaction():
        await conn.execute(
            "UPDATE reclasificacion_solicitudes SET estado = 'aprobada', resuelto_por = $2, "
            "resuelto_at = now() WHERE id = $1", sid, BETO,
        )
        await conn.execute(
            "UPDATE radicados SET nivel_seguridad = 1, clasificacion_causal = NULL, "
            "clasificacion_fundamento = NULL WHERE id = $1", rid,
        )
    assert await _nivel(conn, rid) == 1


@pytest.mark.asyncio
async def test_el_trigger_no_estorba_lo_que_no_baja_de_3(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    nivel2 = await _radicado(conn, nivel=2)
    await conn.execute("UPDATE radicados SET nivel_seguridad = 1, clasificacion_causal = NULL, "
                       "clasificacion_plazo_meses = NULL, clasificacion_fundamento = NULL "
                       "WHERE id = $1", nivel2)
    assert await _nivel(conn, nivel2) == 1
    sube = await _radicado(conn, nivel=1)
    await conn.execute("UPDATE radicados SET nivel_seguridad = 3, clasificacion_causal = 'art18_a_intimidad', "
                       "clasificacion_fundamento = 'F' WHERE id = $1", sube)
    assert await _nivel(conn, sube) == 3


# --------------------------------------------------------------------- los CHECK


@pytest.mark.asyncio
async def test_los_check_de_la_solicitud_son_los_de_la_migracion(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    sid = await _solicitud(conn, rid, por=ANA)

    async def _debe_fallar(sql: str, *args) -> None:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(sql, *args)

    # separación de funciones: quien pide no decide (ni con un UPDATE directo)
    await _debe_fallar(
        "UPDATE reclasificacion_solicitudes SET estado='aprobada', resuelto_por=solicitado_por, "
        "resuelto_at=now() WHERE id=$1", sid)
    await _debe_fallar(
        "UPDATE reclasificacion_solicitudes SET estado='rechazada', resuelto_por=solicitado_por, "
        "resuelto_at=now(), resolucion_motivo='un motivo suficiente' WHERE id=$1", sid)
    # retirar solo lo hace quien pidió
    await _debe_fallar(
        "UPDATE reclasificacion_solicitudes SET estado='retirada', resuelto_por=$2, resuelto_at=now() "
        "WHERE id=$1", sid, BETO)
    # rechazo motivado
    await _debe_fallar(
        "UPDATE reclasificacion_solicitudes SET estado='rechazada', resuelto_por=$2, resuelto_at=now(), "
        "resolucion_motivo='no' WHERE id=$1", sid, BETO)
    # destino coherente y desde 3 únicamente
    for destino in ("(3, 3, NULL, NULL, NULL)", "(3, 2, NULL, NULL, NULL)",
                    "(3, 2, 'art18_a_intimidad', 'F', 12)", "(3, 2, 'art19_i_salud_publica', 'F', 181)",
                    "(3, 1, 'art19_i_salud_publica', NULL, 12)", "(2, 1, NULL, NULL, NULL)"):
        await _debe_fallar(
            "INSERT INTO reclasificacion_solicitudes (radicado_id, nivel_anterior, nivel_nuevo, motivo, "
            "causal_codigo, fundamento_juridico, plazo_meses, solicitado_por) "
            f"SELECT $1, v.a, v.b, 'motivo suficiente', v.c::text, v.d::text, v.e::int, $2 FROM (VALUES {destino}) "
            "AS v(a, b, c, d, e)", rid, ANA)


@pytest.mark.asyncio
async def test_el_catalogo_de_radicados_lo_defiende_la_base(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn, nivel=1)

    async def _fija(nivel, causal, plazo):
        await conn.execute(
            "UPDATE radicados SET nivel_seguridad=$2, clasificacion_causal=$3, "
            "clasificacion_plazo_meses=$4, clasificacion_fundamento='F' WHERE id=$1",
            rid, nivel, causal, plazo)

    for malo in ((2, "Defensa y seguridad nacional", 12), (2, "art18_a_intimidad", None),
                 (2, "art19_i_salud_publica", None), (2, "art19_i_salud_publica", 181),
                 (3, "art19_i_salud_publica", 12), (3, "art18_a_intimidad", 12),
                 (3, None, None), (2, None, 12)):
        with pytest.raises(asyncpg.CheckViolationError):
            await _fija(*malo)
    for bueno in ((2, "por_clasificar", None), (2, "art19_i_salud_publica", 180),
                  (3, "art18_c_secretos", None), (3, "por_clasificar", 7)):
        await _fija(*bueno)
    with pytest.raises(asyncpg.CheckViolationError):  # nivel 1 no lleva causal
        await conn.execute(
            "UPDATE radicados SET nivel_seguridad = 1 WHERE id = $1", await _radicado(conn, nivel=2))


@pytest.mark.asyncio
async def test_a_lo_sumo_una_solicitud_pendiente_por_radicado(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    await _solicitud(conn, rid)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _solicitud(conn, rid, por=CARLA)


@pytest.mark.asyncio
async def test_la_solicitud_resuelta_no_se_reescribe_ni_se_borra(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    sid = await _solicitud(conn, rid)
    await conn.execute(
        "UPDATE reclasificacion_solicitudes SET estado='rechazada', resuelto_por=$2, resuelto_at=now(), "
        "resolucion_motivo='Sigue vigente la causa' WHERE id=$1", sid, BETO)
    for sql in (
        "UPDATE reclasificacion_solicitudes SET estado='aprobada' WHERE id=$1",
        "UPDATE reclasificacion_solicitudes SET motivo='reescrito por alguien' WHERE id=$1",
        "DELETE FROM reclasificacion_solicitudes WHERE id=$1",
    ):
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute(sql, sid)
    # el pedido tampoco se reescribe mientras está pendiente
    otro = await _radicado(conn)
    pid = await _solicitud(conn, otro)
    with pytest.raises(asyncpg.PostgresError):
        await conn.execute("UPDATE reclasificacion_solicitudes SET motivo='otro motivo distinto' WHERE id=$1", pid)
    with pytest.raises(asyncpg.PostgresError):
        await conn.execute("UPDATE reclasificacion_solicitudes SET vence_at = now() + interval '1 year' WHERE id=$1", pid)


@pytest.mark.asyncio
async def test_la_purga_del_radicado_se_lleva_sus_solicitudes(tenant_ctx) -> None:
    """El único DELETE legítimo de radicados es la purga por disposición final; el trigger
    que prohíbe borrar solicitudes tiene que dejarlo pasar en cascada (y solo así)."""
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    await _solicitud(conn, rid)
    await conn.execute("DELETE FROM radicados WHERE id = $1", rid)
    assert await conn.fetchval("SELECT count(*) FROM reclasificacion_solicitudes") == 0


@pytest.mark.asyncio
async def test_la_migracion_es_idempotente_y_pasa_lo_viejo_a_por_clasificar(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn, nivel=1)
    await conn.execute("ALTER TABLE radicados DROP CONSTRAINT radicados_causal_catalogo")
    # La 051 (posterior) también impide guardar un plazo fuera de tope: este test es de la 050.
    await conn.execute("ALTER TABLE radicados DROP CONSTRAINT radicados_reserva_tope_15_anios")
    await conn.execute(
        "UPDATE radicados SET nivel_seguridad = 2, clasificacion_causal = 'Defensa y seguridad nacional', "
        "clasificacion_plazo_meses = 600, clasificacion_fundamento = 'F' WHERE id = $1", rid)
    bien = await _radicado(conn, nivel=2)
    sql = _MIGRACION.read_text()
    await conn.execute(sql)
    await conn.execute(sql)  # idempotente
    fila = await conn.fetchrow(
        "SELECT clasificacion_causal, clasificacion_plazo_meses FROM radicados WHERE id = $1", rid)
    assert fila["clasificacion_causal"] == "por_clasificar"
    assert await conn.fetchval(
        "SELECT clasificacion_causal FROM radicados WHERE id = $1", bien) == "art19_i_salud_publica"
    assert await conn.fetchval(
        "SELECT convalidated FROM pg_constraint WHERE conname = 'radicados_causal_catalogo' "
        "AND conrelid = 'radicados'::regclass")


# --------------------------------------------------------------------- aprobar ejecuta


@pytest.mark.asyncio
async def test_solicitar_y_aprobar_baja_el_nivel_asienta_dos_veces_y_encola_un_evento(
    tenant_ctx, clearance
) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    svc = _svc(conn, slug, _Redis())

    sol = await svc.solicitar(rid, _PEDIDO, ANA)
    assert await _nivel(conn, rid) == 3, "solicitar no baja nada"
    assert await _eventos(conn) == [], "solicitar no mueve el acceso: sin evento"

    ok = await svc.aprobar(sol.id, BETO)

    assert ok.nivel_seguridad == 1 and ok.estado == "aprobada" and ok.resuelto_por == BETO
    assert await _nivel(conn, rid) == 1
    fila = await conn.fetchrow(
        "SELECT clasificacion_causal, clasificacion_fundamento, clasificado_at, publicable "
        "FROM radicados WHERE id = $1", rid)
    assert fila["clasificacion_causal"] is None and fila["clasificado_at"] is None
    acciones = await _asientos(conn, slug, rid)
    assert acciones == [
        "document.desclasificacion_solicitada",
        "document.desclasificacion_aprobada",
        "document.radicado_reclassified",
    ]
    (evento,) = await _eventos(conn)
    assert evento["tipo"] == "document.radicado.reclassified"
    assert str(sol.id) in evento["dedup_key"]
    carga = json.loads(evento["payload"])
    assert carga["nivel_anterior"] == 3 and carga["nivel_nuevo"] == 1
    assert carga["solicitado_por"] == str(ANA) and carga["aprobado_por"] == str(BETO)


@pytest.mark.asyncio
async def test_bajar_a_nivel_2_lleva_la_causal_art19_y_reinicia_el_reloj(tenant_ctx, clearance) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    svc = _svc(conn, slug, _Redis())
    pedido = DesclasificacionRequest(
        nivel_seguridad=2, motivo="Pasa a reserva temporal", causal_reserva="art19_i_salud_publica",
        fundamento_juridico="Art. 19 lit. i", plazo_reserva_meses=24)
    sol = await svc.solicitar(rid, pedido, ANA)
    await svc.aprobar(sol.id, BETO)
    fila = await conn.fetchrow(
        "SELECT nivel_seguridad, clasificacion_causal, clasificacion_plazo_meses, "
        "clasificado_at > now() - interval '1 minute' AS reciente FROM radicados WHERE id = $1", rid)
    assert (fila["nivel_seguridad"], fila["clasificacion_causal"], fila["clasificacion_plazo_meses"]) == (
        2, "art19_i_salud_publica", 24)
    assert fila["reciente"], "cambiar de categoría (3 -> 2) reinicia el reloj: declarado en el ADR"


@pytest.mark.asyncio
async def test_aprobar_la_propia_es_409_y_no_cambia_nada(tenant_ctx, clearance) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    svc = _svc(conn, slug, _Redis())
    sol = await svc.solicitar(rid, _PEDIDO, ANA)
    with pytest.raises(HTTPException) as exc:
        await svc.aprobar(sol.id, ANA)
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "separacion_de_funciones"
    assert await _nivel(conn, rid) == 3 and await _eventos(conn) == []
    # La guarda es el WHERE, no el diagnóstico (mutación 1): el repositorio no devuelve fila.
    repo = ReclasificacionRepository(conn)
    assert await repo.aprobar(sol.id, ANA, 3) is None
    assert await repo.rechazar(sol.id, ANA, "un motivo suficiente", 3) is None


@pytest.mark.asyncio
async def test_aprobar_exige_clearance_en_el_where_y_responde_404(tenant_ctx, clearance) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    sol = await _svc(conn, slug).solicitar(rid, _PEDIDO, ANA)
    clearance[CARLA] = 2
    with pytest.raises(HTTPException) as exc:
        await _svc(conn, slug).aprobar(sol.id, CARLA)
    assert exc.value.status_code == 404
    repo = ReclasificacionRepository(conn)
    assert await repo.aprobar(sol.id, CARLA, 2) is None, "el WHERE del UPDATE lleva el clearance"
    assert await repo.diagnostico(sol.id, 2) is None, "y el diagnóstico también: no delata nada"
    assert await _nivel(conn, rid) == 3


@pytest.mark.asyncio
async def test_una_solicitud_vencida_no_se_aprueba_y_pedir_de_nuevo_la_caduca(
    tenant_ctx, clearance
) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    vieja = await _solicitud(conn, rid, vence="now() - interval '1 day'")
    repo = ReclasificacionRepository(conn)
    assert await repo.aprobar(vieja, BETO, 3) is None, "mutación 4: el WHERE lleva la vigencia"
    with pytest.raises(HTTPException) as exc:
        await _svc(conn, slug).aprobar(vieja, BETO)
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "solicitud_caducada"

    nueva = await _svc(conn, slug).solicitar(rid, _PEDIDO, CARLA)
    assert nueva.estado == "pendiente"
    assert await conn.fetchval("SELECT estado FROM reclasificacion_solicitudes WHERE id = $1", vieja) == "caducada"
    acciones = await _asientos(conn, slug, rid)
    assert "document.desclasificacion_caducada" in acciones
    actor = await conn.fetchval(
        "SELECT actor FROM public.audit_log WHERE tenant_slug = $1 AND action = 'document.desclasificacion_caducada'", slug)
    assert actor is None, "caducar no lo hace una persona"


@pytest.mark.asyncio
async def test_rechazar_y_retirar_no_emiten_evento_ni_bajan_el_nivel(tenant_ctx, clearance) -> None:
    slug, conn = await _en(tenant_ctx)
    r1, r2 = await _radicado(conn), await _radicado(conn)
    svc = _svc(conn, slug, _Redis())
    s1 = await svc.solicitar(r1, _PEDIDO, ANA)
    rech = await svc.rechazar(s1.id, BETO, "Sigue vigente la causa")
    assert rech.estado == "rechazada" and rech.resolucion_motivo == "Sigue vigente la causa"
    s2 = await svc.solicitar(r2, _PEDIDO, ANA)
    with pytest.raises(HTTPException) as exc:
        await svc.retirar(s2.id, BETO, None)
    assert exc.value.detail["error"] == "solo_el_solicitante"
    ret = await svc.retirar(s2.id, ANA, "Me equivoqué de radicado")
    assert ret.estado == "retirada"
    assert await _nivel(conn, r1) == 3 and await _nivel(conn, r2) == 3
    assert await _eventos(conn) == []
    # una solicitud resuelta libera el radicado para pedir de nuevo
    assert (await svc.solicitar(r1, _PEDIDO, CARLA)).estado == "pendiente"


@pytest.mark.asyncio
async def test_dos_aprobaciones_concurrentes_dan_una_ejecucion(tenant_ctx, clearance) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    sol = await _svc(conn, slug, _Redis()).solicitar(rid, _PEDIDO, ANA)

    otra = await asyncpg.connect(dsn=ic.DSN)
    try:
        await otra.execute(f'SET search_path TO "{tenant_schema(slug)}"')
        resultados = await asyncio.gather(
            _svc(conn, slug, _Redis()).aprobar(sol.id, BETO),
            _svc(otra, slug, _Redis()).aprobar(sol.id, CARLA),
            return_exceptions=True,
        )
    finally:
        await otra.close()

    bien = [r for r in resultados if not isinstance(r, Exception)]
    mal = [r for r in resultados if isinstance(r, Exception)]
    assert len(bien) == 1 and len(mal) == 1, resultados
    assert isinstance(mal[0], HTTPException) and mal[0].status_code == 409
    assert mal[0].detail["error"] == "solicitud_ya_resuelta"
    assert len(await _eventos(conn)) == 1, "una sola ejecución = un solo evento"
    assert (await _asientos(conn, slug, rid)).count("document.radicado_reclassified") == 1


@pytest.mark.asyncio
async def test_dos_solicitudes_concurrentes_dan_una(tenant_ctx, clearance) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    otra = await asyncpg.connect(dsn=ic.DSN)
    try:
        await otra.execute(f'SET search_path TO "{tenant_schema(slug)}"')
        resultados = await asyncio.gather(
            _svc(conn, slug).solicitar(rid, _PEDIDO, ANA),
            _svc(otra, slug).solicitar(rid, _PEDIDO, CARLA),
            return_exceptions=True,
        )
    finally:
        await otra.close()
    assert sum(isinstance(r, HTTPException) for r in resultados) == 1
    assert await conn.fetchval("SELECT count(*) FROM reclasificacion_solicitudes") == 1


# --------------------------------------------------------------------- atomicidad


@pytest.mark.asyncio
async def test_si_falla_el_segundo_asiento_no_queda_ni_nivel_ni_aprobacion_ni_evento(
    tenant_ctx, clearance, monkeypatch
) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    svc = _svc(conn, slug, _Redis())
    sol = await svc.solicitar(rid, _PEDIDO, ANA)

    from app.services import desclasificacion_service as mod

    real = mod.audit.append
    llamadas = {"n": 0}

    async def _falla_en_el_segundo(*a, **k):
        llamadas["n"] += 1
        if llamadas["n"] == 2:
            raise RuntimeError("auditoría caída")
        return await real(*a, **k)

    monkeypatch.setattr(mod.audit, "append", _falla_en_el_segundo)
    with pytest.raises(RuntimeError):
        await svc.aprobar(sol.id, BETO)

    assert await _nivel(conn, rid) == 3
    assert await conn.fetchval("SELECT estado FROM reclasificacion_solicitudes WHERE id = $1", sol.id) == "pendiente"
    assert await _eventos(conn) == []
    assert (await _asientos(conn, slug, rid)) == ["document.desclasificacion_solicitada"]


@pytest.mark.asyncio
async def test_si_no_se_puede_encolar_el_evento_se_revierte_la_aprobacion(
    tenant_ctx, clearance, monkeypatch
) -> None:
    """Mutación 6: `outbox.encolar` fuera de la transacción deja el nivel bajado sin evento."""
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    svc = _svc(conn, slug, _Redis())
    sol = await svc.solicitar(rid, _PEDIDO, ANA)

    async def _no_se_puede(*a, **k):
        raise RuntimeError("no se pudo escribir el evento")

    monkeypatch.setattr("app.services.desclasificacion_service.outbox.encolar", _no_se_puede)
    with pytest.raises(RuntimeError):
        await svc.aprobar(sol.id, BETO)
    assert await _nivel(conn, rid) == 3
    assert await conn.fetchval("SELECT estado FROM reclasificacion_solicitudes WHERE id = $1", sol.id) == "pendiente"


# --------------------------------------------------------------------- el PATCH


@pytest.mark.asyncio
async def test_el_patch_desde_3_es_409_y_la_base_no_llega_a_ver_el_intento(tenant_ctx, clearance) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    with pytest.raises(HTTPException) as exc:
        await DocumentService(conn, slug).reclassify(
            rid, nuevo_nivel=1, motivo="x", actor=BETO)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "desclasificacion_requiere_aprobacion"
    assert await _nivel(conn, rid) == 3


@pytest.mark.asyncio
async def test_el_patch_con_clearance_2_sobre_nivel_3_es_404_y_no_409(tenant_ctx, clearance) -> None:
    from app.services.document_service import DocumentNotFoundError

    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn)
    clearance[CARLA] = 2
    with pytest.raises(DocumentNotFoundError):
        await DocumentService(conn, slug).reclassify(rid, nuevo_nivel=1, motivo="x", actor=CARLA)
    # y el bloqueo mismo lleva el clearance en el WHERE
    repo = DocumentRepository(conn, slug)
    assert await repo.get_security_context_for_update(rid, 2) is None
    async with conn.transaction():
        assert await repo.get_security_context_for_update(rid, 3) is not None


@pytest.mark.asyncio
async def test_bajar_de_2_a_1_es_directo_con_evento(tenant_ctx, clearance) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn, nivel=2)
    res = await DocumentService(conn, slug, redis=_Redis()).reclassify(
        rid, nuevo_nivel=1, motivo="Cesó la reserva", actor=BETO)
    assert res["nivel_seguridad"] == 1 and await _nivel(conn, rid) == 1
    (evento,) = await _eventos(conn)
    assert evento["dedup_key"].count(":") == 2


@pytest.mark.asyncio
async def test_remotivar_no_reinicia_el_reloj_ni_emite_evento(tenant_ctx, clearance) -> None:
    """Mutación 7: con `clasificado_at = now()` incondicional este test cae."""
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn, nivel=3, causal="por_clasificar")
    antes = await conn.fetchval("SELECT clasificado_at FROM radicados WHERE id = $1", rid)
    res = await DocumentService(conn, slug, redis=_Redis()).reclassify(
        rid, nuevo_nivel=3, motivo="Se motiva lo pendiente", actor=BETO,
        causal_reserva="art18_a_intimidad", fundamento_juridico="Ley 1581 de 2012")
    assert res["nivel_seguridad"] == 3
    fila = await conn.fetchrow(
        "SELECT clasificacion_causal, clasificado_at FROM radicados WHERE id = $1", rid)
    assert fila["clasificacion_causal"] == "art18_a_intimidad"
    assert fila["clasificado_at"] == antes
    assert await _eventos(conn) == []
    assert (await _asientos(conn, slug, rid)) == ["document.radicado_remotivado"]
    # repetir lo mismo es un no-op sin asiento
    await DocumentService(conn, slug, redis=_Redis()).reclassify(
        rid, nuevo_nivel=3, motivo="Se motiva lo pendiente", actor=BETO,
        causal_reserva="art18_a_intimidad", fundamento_juridico="Ley 1581 de 2012")
    assert len(await _asientos(conn, slug, rid)) == 1


@pytest.mark.asyncio
async def test_set_classification_conserva_clasificado_at_y_respeta_el_nivel_esperado(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn, nivel=2)
    antes = await conn.fetchval("SELECT clasificado_at FROM radicados WHERE id = $1", rid)
    repo = DocumentRepository(conn, slug)
    assert await repo.set_classification(
        rid, 2, nivel_esperado=2, fundamento="Otro", causal="art19_b_seguridad_publica", plazo_meses=6)
    assert await conn.fetchval("SELECT clasificado_at FROM radicados WHERE id = $1", rid) == antes
    # si la fila ya no está en el nivel sobre el que se decidió, no se pisa
    assert not await repo.set_classification(
        rid, 3, nivel_esperado=1, fundamento="F", causal="art18_a_intimidad")
    assert await _nivel(conn, rid) == 2


# --------------------------------------------------------------------- cola e interno


@pytest.mark.asyncio
async def test_la_cola_filas_y_total_con_clearance(tenant_ctx, clearance) -> None:
    slug, conn = await _en(tenant_ctx)
    svc = _svc(conn, slug)
    for _ in range(3):
        await svc.solicitar(await _radicado(conn), _PEDIDO, ANA)
    await _solicitud(conn, await _radicado(conn), vence="now() - interval '1 day'")  # vencida: no sale
    clearance[CARLA] = 2

    filas, total = await svc.cola(BETO, page=1, size=2)
    assert (len(filas), total) == (2, 3)
    assert all(f.tracking_number and f.propia is False for f in filas)
    propias, _ = await svc.cola(ANA, page=1, size=10)
    assert all(f.propia for f in propias)
    assert await svc.cola(CARLA, page=1, size=10) == ([], 0), "clearance 2: ni filas ni total"


@pytest.mark.asyncio
async def test_el_select_interno_es_content_free(tenant_ctx) -> None:
    """Mutación 8: añadir `tracking_number`/`subject`/`fundamento` al SELECT lo delata."""
    slug, conn = await _en(tenant_ctx)
    await _radicado(conn, nivel=3)
    await _radicado(conn, nivel=2)
    await _radicado(conn, nivel=1)
    filas = await DocumentRepository(conn, slug).clasificados_internal()
    assert len(filas) == 2
    for f in filas:
        assert set(f.keys()) == {
            "radicado_id", "nivel_seguridad", "clasificacion_causal",
            "clasificacion_plazo_meses", "clasificado_at",
            # 051: la cita normativa PUBLICABLE y la fecha de generación (Decreto 103/2015
            # art. 40). El fundamento libre, el número y el asunto siguen fuera.
            "clasificacion_norma", "fecha_generacion",
        }


# --------------------------------------------------------- 051: tope de 15 años desde la generación

_MIGRACION_051 = _MIGRACION.parent / "051_reserva_tope_y_norma.sql"


def _utc(texto: str):
    from datetime import UTC, datetime

    return datetime.fromisoformat(texto).replace(tzinfo=UTC)


async def _reservado(conn, *, plazo: int, generado: str, clasificado: str, norma: str | None = None):
    return await conn.fetchval(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, tenant_code, "
        "nivel_seguridad, clasificacion_fundamento, clasificacion_causal, "
        "clasificacion_plazo_meses, clasificacion_norma, registered_at, clasificado_at) "
        "VALUES ($1, 'E', 2026, 1, 'S', 'DEMO', 2, 'F', 'art19_i_salud_publica', $2, $3, "
        "$4::timestamptz, $5::timestamptz) RETURNING id",
        f"2026-DEMO-E-{uuid.uuid4().hex[:6]}",
        plazo,
        norma,
        _utc(generado),
        _utc(clasificado),
    )


@pytest.mark.asyncio
async def test_el_check_impide_pasar_de_15_anios_desde_la_generacion(tenant_ctx) -> None:
    """Reiniciar `clasificado_at` (2→3→2) no alarga la reserva: se mide contra la GENERACIÓN."""
    import asyncpg

    slug, conn = await _en(tenant_ctx)
    # Generado en 2016, reservado hoy: 10 años (120 meses) ya corridos; quedan 60.
    ok = await _reservado(conn, plazo=60, generado="2016-10-01", clasificado="2026-10-01")
    assert ok is not None
    with pytest.raises(asyncpg.CheckViolationError):
        await _reservado(conn, plazo=61, generado="2016-10-01", clasificado="2026-10-01")
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "UPDATE radicados SET clasificacion_plazo_meses = 180, clasificado_at = now() WHERE id = $1",
            ok,
        )


@pytest.mark.asyncio
async def test_la_norma_publicable_es_una_cita_no_una_frase(tenant_ctx) -> None:
    import asyncpg

    slug, conn = await _en(tenant_ctx)
    await _reservado(conn, plazo=12, generado="2026-01-01", clasificado="2026-01-02",
                     norma="Ley 1581 de 2012, art. 5")
    with pytest.raises(asyncpg.CheckViolationError):
        await _reservado(conn, plazo=12, generado="2026-01-01", clasificado="2026-01-02",
                         norma="el expediente de Juan Pérez")


@pytest.mark.asyncio
async def test_la_051_es_idempotente_y_pasa_lo_que_excede_el_tope_a_por_clasificar(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _reservado(conn, plazo=24, generado="2016-10-01", clasificado="2026-10-01")
    await conn.execute("ALTER TABLE radicados DROP CONSTRAINT radicados_reserva_tope_15_anios")
    await conn.execute("UPDATE radicados SET clasificacion_plazo_meses = 170 WHERE id = $1", rid)
    sql = _MIGRACION_051.read_text()
    await conn.execute(sql)
    await conn.execute(sql)  # idempotente
    assert await conn.fetchval(
        "SELECT clasificacion_causal FROM radicados WHERE id = $1", rid) == "por_clasificar"
    assert await conn.fetchval(
        "SELECT convalidated FROM pg_constraint WHERE conname = 'radicados_reserva_tope_15_anios' "
        "AND conrelid = 'radicados'::regclass")
