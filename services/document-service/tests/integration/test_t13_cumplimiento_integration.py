# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""T13 — cumplimiento documental, contra Postgres REAL.

Lo que un mock no puede decir: que el CHECK existe y rechaza, que la clave de
idempotencia se serializa bajo concurrencia, que el evento del outbox desaparece con el
rollback del UPDATE, que el WHERE de la publicación excluye por tipo y por canal.

Mutaciones (todas verificadas, ver CHANGELOG):
- quitar `AND a.publicable` de `get_public_anexo` → `test_un_anexo_no_marcado_no_se_sirve` cae;
- quitar la exclusión por tipo / por canal del WHERE de `set_publicable` → caen sus tests;
- insertar el evento del outbox DESPUÉS del `async with transaction()` en `reclassify` →
  `test_el_rollback_del_update_se_lleva_el_evento` cae;
- sacar el INSERT de la clave de la transacción de `create` → cae
  `test_dos_envios_simultaneos_con_la_misma_clave_dan_un_solo_radicado`.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.schemas.document import DocumentCreate
from app.services.document_service import DocumentService

pytestmark = pytest.mark.integration


def _clearance_3(monkeypatch) -> None:
    async def _c(conn, user_id):
        return 3

    monkeypatch.setattr("app.services.document_service.resolve_caller_clearance", _c)


async def _en(tenant_ctx) -> tuple[str, asyncpg.Connection]:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    return slug, conn


async def _radicado(conn, slug, **kw) -> uuid.UUID:
    base = {
        "doc_type": "E",
        "subject": "Prueba T13",
        "doc_class": "Petición",
        "dest_dept_code": 1,
    }
    base.update(kw)
    creado = await DocumentService(conn, slug).create(DocumentCreate(**base), registered_by=None)
    return creado.id


async def _anexo(conn, radicado_id) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO anexos (radicado_id, file_id, filename) VALUES ($1, $2, 'a.pdf') RETURNING id",
        radicado_id,
        uuid.uuid4(),
    )


# --------------------------------------------------------------------- AREA-07-12


@pytest.mark.asyncio
async def test_un_anexo_no_marcado_no_se_sirve(tenant_ctx) -> None:
    from app.repositories.document_repository import DocumentRepository
    from app.repositories.oai_repository import OaiRepository

    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn, slug)
    tn = await conn.fetchval("SELECT tracking_number FROM radicados WHERE id = $1", rid)
    anexo = await _anexo(conn, rid)
    repo = DocumentRepository(conn, slug)

    # Radicado publicado, anexo SIN marcar: los metadatos salen, los bytes no.
    assert await repo.set_publicable(rid, True, 3, None) is not None
    assert await OaiRepository(conn).get_public_anexo(tn) is None

    # Marcado por el acto de publicación: ahora sí.
    assert await repo.set_anexos_publicables(rid, [anexo]) == 1
    servido = await OaiRepository(conn).get_public_anexo(tn)
    assert servido is not None and servido["filename"] == "a.pdf"

    # Retirar la publicación del radicado corta el contenido aunque el anexo siga marcado.
    await repo.set_publicable(rid, False, 3, None)
    assert await OaiRepository(conn).get_public_anexo(tn) is None


@pytest.mark.asyncio
async def test_set_anexos_deja_un_estado_conocido_y_rechaza_ajenos(tenant_ctx) -> None:
    from app.repositories.document_repository import DocumentRepository

    slug, conn = await _en(tenant_ctx)
    a, b = await _radicado(conn, slug), await _radicado(conn, slug)
    a1, a2, b1 = await _anexo(conn, a), await _anexo(conn, a), await _anexo(conn, b)
    repo = DocumentRepository(conn, slug)
    assert await repo.set_anexos_publicables(a, [a1, a2]) == 2
    # Publicar de nuevo con solo uno DESMARCA el otro (no acumula marcas viejas).
    assert await repo.set_anexos_publicables(a, [a1]) == 1
    marcas = {r["id"]: r["publicable"] for r in await conn.fetch("SELECT id, publicable FROM anexos")}
    assert marcas[a1] is True and marcas[a2] is False and marcas[b1] is False
    # Un anexo de OTRO radicado cuenta como no perteneciente.
    assert await repo.set_anexos_publicables(a, [a1, b1]) == 1


@pytest.mark.asyncio
async def test_el_acto_de_publicar_con_un_anexo_ajeno_revierte_entero(tenant_ctx) -> None:
    from app.services.document_service import AnexoNoDisponibleError

    slug, conn = await _en(tenant_ctx)
    a, b = await _radicado(conn, slug), await _radicado(conn, slug)
    ajeno = await _anexo(conn, b)
    with pytest.raises(AnexoNoDisponibleError):
        await DocumentService(conn, slug).set_publicacion(
            a, True, "Esquema de publicación", 3, anexos_publicables=[ajeno]
        )
    assert await conn.fetchval("SELECT publicable FROM radicados WHERE id = $1", a) is False
    assert await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE action = 'document.publicacion_activada' "
        "AND object_ref = (SELECT tracking_number FROM radicados WHERE id = $1)",
        a,
    ) == 0


@pytest.mark.asyncio
async def test_un_radicado_de_ventanilla_no_se_publica_de_oficio(tenant_ctx) -> None:
    from app.repositories.document_repository import DocumentRepository

    slug, conn = await _en(tenant_ctx)
    # Nace en nivel 1 por el DEFAULT, pero es información de un tercero (Ley 1581).
    rid = await _radicado(conn, slug, metadata={"canal": "ventanilla_publica"})
    assert await DocumentRepository(conn, slug).set_publicable(rid, True, 3, None) is None
    assert await conn.fetchval("SELECT publicable FROM radicados WHERE id = $1", rid) is False
    # Control positivo: el mismo radicado SIN la marca de canal sí se publica.
    ok = await _radicado(conn, slug)
    assert await DocumentRepository(conn, slug).set_publicable(ok, True, 3, None) is not None


@pytest.mark.asyncio
async def test_un_tipo_admitido_en_ventanilla_no_se_publica_de_oficio(tenant_ctx) -> None:
    from app.repositories.document_repository import DocumentRepository

    slug, conn = await _en(tenant_ctx)
    await conn.execute(
        "INSERT INTO tipos_documentales (code, nombre, admite_ventanilla_publica) "
        "VALUES ('PQRS', 'PQRS', true), ('OFICIO', 'Oficio', false)"
    )
    pqrs = await _radicado(conn, slug, doc_class="PQRS")
    oficio = await _radicado(conn, slug, doc_class="OFICIO")
    repo = DocumentRepository(conn, slug)
    assert await repo.set_publicable(pqrs, True, 3, None) is None  # 404 neutro
    assert await repo.set_publicable(oficio, True, 3, None) is not None
    # Retirar nunca se bloquea: un fail-safe en la dirección de dejar de difundir.
    assert await repo.set_publicable(oficio, False, 3, None) is not None


@pytest.mark.asyncio
async def test_clasificar_retira_tambien_la_marca_de_los_anexos(tenant_ctx) -> None:
    from app.repositories.document_repository import DocumentRepository

    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn, slug)
    anexo = await _anexo(conn, rid)
    repo = DocumentRepository(conn, slug)
    await repo.set_anexos_publicables(rid, [anexo])
    await repo.set_classification(rid, 2, fundamento="Ley 1712/2014 art. 19")
    assert await conn.fetchval("SELECT publicable FROM anexos WHERE id = $1", anexo) is False


@pytest.mark.asyncio
async def test_la_migracion_040_conserva_lo_ya_publicado_y_es_idempotente(tenant_ctx) -> None:
    from pathlib import Path

    slug, conn = await _en(tenant_ctx)
    pub, priv = await _radicado(conn, slug), await _radicado(conn, slug)
    a_pub, a_priv = await _anexo(conn, pub), await _anexo(conn, priv)
    await conn.execute("UPDATE radicados SET publicable = TRUE WHERE id = $1", pub)
    # Simula el estado ANTERIOR a la migración: sin la columna.
    await conn.execute("ALTER TABLE anexos DROP COLUMN publicable")
    sql = (Path(__file__).parents[2] / "migrations/tenant/040_anexo_publicable.sql").read_text()
    await conn.execute(sql)
    assert await conn.fetchval("SELECT publicable FROM anexos WHERE id = $1", a_pub) is True
    assert await conn.fetchval("SELECT publicable FROM anexos WHERE id = $1", a_priv) is False
    await conn.execute(sql)  # idempotente: no vuelve a tocar nada


# --------------------------------------------------------------------- AREA-09-04


@pytest.mark.asyncio
async def test_un_insert_directo_clasificado_sin_fundamento_viola_el_check(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn, slug)
    for fund in (None, "", "   "):
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "UPDATE radicados SET nivel_seguridad = 2, clasificacion_fundamento = $2 "
                "WHERE id = $1",
                rid,
                fund,
            )
    # Con fundamento sí, y el nivel 1 no lo necesita.
    await conn.execute(
        "UPDATE radicados SET nivel_seguridad = 2, clasificacion_fundamento = 'Ley 1712' "
        "WHERE id = $1",
        rid,
    )


@pytest.mark.asyncio
async def test_la_migracion_041_rellena_lo_heredado_y_valida(tenant_ctx) -> None:
    from pathlib import Path

    slug, conn = await _en(tenant_ctx)
    rid = await _radicado(conn, slug)
    await conn.execute("ALTER TABLE radicados DROP CONSTRAINT radicados_clasificado_con_fundamento")
    await conn.execute("UPDATE radicados SET nivel_seguridad = 3 WHERE id = $1", rid)  # heredado
    sql = (
        Path(__file__).parents[2] / "migrations/tenant/041_clasificado_con_fundamento.sql"
    ).read_text()
    await conn.execute(sql)
    texto = await conn.fetchval("SELECT clasificacion_fundamento FROM radicados WHERE id = $1", rid)
    assert texto.startswith("PENDIENTE DE MOTIVAR")
    assert await conn.fetchval(
        "SELECT convalidated FROM pg_constraint WHERE conname = 'radicados_clasificado_con_fundamento' "
        "AND conrelid = 'radicados'::regclass"
    ) is True
    await conn.execute(sql)  # idempotente


@pytest.mark.asyncio
async def test_radicar_clasificado_nace_motivado_y_el_indice_trae_dependencia(
    tenant_ctx, monkeypatch
) -> None:
    import uuid as _uuid

    slug, conn = await _en(tenant_ctx)
    _clearance_3(monkeypatch)
    rid = await _radicado(
        conn,
        slug,
        nivel_seguridad=2,
        clasificacion_fundamento="Ley 1712/2014 art. 19 lit. a",
    )
    assert rid is not None
    indice = await DocumentService(conn, slug).indice_clasificados(_uuid.uuid4())
    (item,) = indice.items
    assert item.fundamento_juridico.startswith("Ley 1712")
    assert item.dependencia == "Dirección General"  # E: la que RECIBE (dest_dept)
    assert item.registrado_por is None


# --------------------------------------------------------------------- AREA-09-05


class _RedisQueSeCae:
    """Redis que falla justo tras el commit — el caso que el best-effort perdía."""

    async def xadd(self, *a, **k):
        raise ConnectionError("redis caído")


class _RedisBueno:
    def __init__(self) -> None:
        self.enviados: list[tuple] = []

    async def xadd(self, stream, campos, *a, **k):
        self.enviados.append((stream, campos))
        return b"1-0"


async def _pendientes(conn) -> list[asyncpg.Record]:
    return list(
        await conn.fetch(
            "SELECT tipo, payload, enviado_at FROM eventos_salientes "
            "WHERE tipo = 'document.radicado.reclassified' ORDER BY id"
        )
    )


@pytest.mark.asyncio
async def test_reclasificar_con_redis_caido_deja_el_evento_pendiente_y_el_rele_lo_envia(
    tenant_ctx, monkeypatch
) -> None:
    from app.services import outbox

    slug, conn = await _en(tenant_ctx)
    _clearance_3(monkeypatch)
    rid = await _radicado(conn, slug)
    actor = uuid.uuid4()

    res = await DocumentService(conn, slug, redis=_RedisQueSeCae()).reclassify(
        rid,
        nuevo_nivel=3,
        motivo="Contiene datos sensibles",
        actor=actor,
        causal_reserva="Art. 18",
        fundamento_juridico="Ley 1712/2014 art. 18 lit. a",
    )
    assert res["nivel_seguridad"] == 3
    (fila,) = await _pendientes(conn)
    assert fila["enviado_at"] is None, "el evento 1→3 no puede perderse con Redis caído"

    # El relé lo envía al volver Redis y lo marca; no lo repite.
    bueno = _RedisBueno()
    # Dos pendientes: el alta del radicado (sin Redis) y la reclasificación.
    assert await outbox.retransmitir(conn, bueno, gracia_segundos=0) == 2
    assert len(bueno.enviados) == 2
    assert "document.radicado.reclassified" in repr(bueno.enviados)
    assert (await _pendientes(conn))[0]["enviado_at"] is not None
    assert await outbox.retransmitir(conn, bueno, gracia_segundos=0) == 0


@pytest.mark.asyncio
async def test_el_rele_no_baja_el_nivel_con_un_evento_rancio(tenant_ctx, monkeypatch) -> None:
    """A (1→2) queda pendiente por Redis caído; B (2→3) sale. El relé publica A DESPUÉS:
    debe llevar el nivel VIGENTE (3), no el 2 original — knowledge fija el nivel exacto y
    un 2 tardío bajaría el fragmento de un clasificado.

    Mutación verificada: quitar la llamada a `_con_nivel_vivo` en `retransmitir` → cae.
    """
    from app.services import outbox

    slug, conn = await _en(tenant_ctx)
    _clearance_3(monkeypatch)
    rid = await _radicado(conn, slug)
    kw = dict(
        motivo="Contiene datos sensibles",
        actor=uuid.uuid4(),
        causal_reserva="Art. 18",
        fundamento_juridico="Ley 1712/2014 art. 18 lit. a",
    )
    await DocumentService(conn, slug, redis=_RedisQueSeCae()).reclassify(rid, nuevo_nivel=2, **kw)
    await DocumentService(conn, slug, redis=_RedisBueno()).reclassify(rid, nuevo_nivel=3, **kw)

    rele = _RedisBueno()
    await outbox.retransmitir(conn, rele, gracia_segundos=0)
    niveles = [
        json.loads(c["payload"] if isinstance(c["payload"], str) else c["payload"].decode())["nivel_nuevo"]
        for _, c in rele.enviados
        if "document.radicado.reclassified" in repr(c)
    ]
    assert niveles and set(niveles) == {3}, f"el relé publicó niveles rancios: {niveles}"


@pytest.mark.asyncio
async def test_el_rollback_del_update_se_lleva_el_evento(tenant_ctx, monkeypatch) -> None:
    """Si falla el asiento (misma tx), el nivel NO cambia y el evento NO existe."""
    slug, conn = await _en(tenant_ctx)
    _clearance_3(monkeypatch)
    rid = await _radicado(conn, slug)

    async def _falla(*a, **k):
        raise RuntimeError("auditoría caída")

    monkeypatch.setattr("app.services.document_service.audit.append", _falla)
    with pytest.raises(RuntimeError):
        await DocumentService(conn, slug).reclassify(
            rid, nuevo_nivel=3, motivo="x", actor=uuid.uuid4(),
            fundamento_juridico="Ley 1712/2014 art. 18",
        )
    assert await conn.fetchval("SELECT nivel_seguridad FROM radicados WHERE id = $1", rid) == 1
    assert await _pendientes(conn) == []


@pytest.mark.asyncio
async def test_si_no_se_puede_encolar_el_evento_el_update_revierte(tenant_ctx, monkeypatch) -> None:
    """El evento y el cambio son UN acto: si el INSERT del evento falla, el UPDATE del nivel
    revierte. Distingue «dentro de la tx» de «justo después del commit», donde el nivel ya
    quedó confirmado y el evento no existe (la ventana que el outbox viene a cerrar).
    Mutación: mover `outbox.encolar` fuera del `async with transaction()` → este test cae."""
    slug, conn = await _en(tenant_ctx)
    _clearance_3(monkeypatch)
    rid = await _radicado(conn, slug)

    async def _no_se_puede(*a, **k):
        raise RuntimeError("no se pudo escribir el evento")

    monkeypatch.setattr("app.services.document_service.outbox.encolar", _no_se_puede)
    with pytest.raises(RuntimeError):
        await DocumentService(conn, slug).reclassify(
            rid, nuevo_nivel=3, motivo="x", actor=uuid.uuid4(),
            fundamento_juridico="Ley 1712/2014 art. 18",
        )
    assert await conn.fetchval("SELECT nivel_seguridad FROM radicados WHERE id = $1", rid) == 1


@pytest.mark.asyncio
async def test_el_camino_rapido_publica_y_marca_enviado(tenant_ctx, monkeypatch) -> None:
    slug, conn = await _en(tenant_ctx)
    _clearance_3(monkeypatch)
    rid = await _radicado(conn, slug)
    redis = _RedisBueno()
    await DocumentService(conn, slug, redis=redis).reclassify(
        rid, nuevo_nivel=2, motivo="x", actor=uuid.uuid4(),
        fundamento_juridico="Ley 1712/2014 art. 19",
    )
    assert len(redis.enviados) == 1
    assert (await _pendientes(conn))[0]["enviado_at"] is not None


@pytest.mark.asyncio
async def test_alta_y_anulacion_tambien_pasan_por_el_outbox(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    await _radicado(conn, slug)
    tipos = {r["tipo"] for r in await conn.fetch("SELECT tipo FROM eventos_salientes")}
    assert "document.radicado.created" in tipos


# --------------------------------------------------------------------- AREA-02-07 / 09-08


async def _ventanilla_lista(conn) -> None:
    await conn.execute(
        "INSERT INTO dependencias (codigo, nombre, activo, depe_rad_tp2) "
        "VALUES (901, 'Ventanilla Única', true, true) ON CONFLICT DO NOTHING"
    )
    await conn.execute(
        "INSERT INTO tipos_documentales (code, nombre, activo, admite_ventanilla_publica) "
        "VALUES ('DEM-PET', 'Petición', true, true) ON CONFLICT DO NOTHING"
    )


def _payload(**kw):
    from app.schemas.public import VentanillaRadicacion

    base = {
        "tipo_documental": "DEM-PET",
        "nombre": "Ana Ruiz",
        "asunto": "Solicitud",
        "descripcion": "Texto",
        "acepta_tratamiento": True,
        "politica_version": "2026-01",
    }
    base.update(kw)
    return VentanillaRadicacion(**base)


@pytest.mark.asyncio
async def test_dos_envios_simultaneos_con_la_misma_clave_dan_un_solo_radicado(tenant_ctx) -> None:
    """Bajo concurrencia REAL: dos conexiones, la misma clave. El índice único serializa:
    la segunda espera el commit de la primera y devuelve su radicado SIN gastar número."""
    from app.services.ventanilla_service import VentanillaService
    from tests.integration.conftest import _require_dsn

    slug, conn = await _en(tenant_ctx)
    await _ventanilla_lista(conn)
    schema = tenant_schema(slug)
    clave = "clave-" + uuid.uuid4().hex

    async def _enviar() -> str:
        c = await asyncpg.connect(dsn=_require_dsn())
        try:
            await c.execute(f'SET search_path TO "{schema}", public')
            r = await VentanillaService(c, slug).radicar(
                _payload(), DocumentService(c, slug), idempotency_key=clave, ip="203.0.113.5"
            )
            return r.tracking_number
        finally:
            await c.close()

    numeros = await asyncio.gather(*[_enviar() for _ in range(4)])
    assert len(set(numeros)) == 1, numeros
    assert await conn.fetchval("SELECT count(*) FROM radicados") == 1
    # Un solo consecutivo gastado.
    assert await conn.fetchval("SELECT last_seq FROM tracking_sequences WHERE doc_type = 'E'") == 1


@pytest.mark.asyncio
async def test_claves_distintas_dan_radicados_distintos(tenant_ctx) -> None:
    from app.services.ventanilla_service import VentanillaService

    slug, conn = await _en(tenant_ctx)
    await _ventanilla_lista(conn)
    servicio = DocumentService(conn, slug)
    a = await VentanillaService(conn, slug).radicar(
        _payload(), servicio, idempotency_key="a" * 20
    )
    b = await VentanillaService(conn, slug).radicar(
        _payload(), servicio, idempotency_key="b" * 20
    )
    assert a.tracking_number != b.tracking_number
    assert await conn.fetchval("SELECT count(*) FROM radicados") == 2


@pytest.mark.asyncio
async def test_la_clave_con_otro_contenido_es_409_y_no_radica(tenant_ctx) -> None:
    from fastapi import HTTPException

    from app.services.ventanilla_service import VentanillaService

    slug, conn = await _en(tenant_ctx)
    await _ventanilla_lista(conn)
    servicio = DocumentService(conn, slug)
    await VentanillaService(conn, slug).radicar(_payload(), servicio, idempotency_key="c" * 20)
    with pytest.raises(HTTPException) as exc:
        await VentanillaService(conn, slug).radicar(
            _payload(asunto="OTRO asunto"), servicio, idempotency_key="c" * 20
        )
    assert exc.value.status_code == 409
    assert await conn.fetchval("SELECT count(*) FROM radicados") == 1


@pytest.mark.asyncio
async def test_sin_clave_no_hay_deduplicacion_y_se_conserva_el_comportamiento_previo(tenant_ctx) -> None:
    from app.services.ventanilla_service import VentanillaService

    slug, conn = await _en(tenant_ctx)
    await _ventanilla_lista(conn)
    servicio = DocumentService(conn, slug)
    await VentanillaService(conn, slug).radicar(_payload(), servicio)
    await VentanillaService(conn, slug).radicar(_payload(), servicio)
    assert await conn.fetchval("SELECT count(*) FROM radicados") == 2


@pytest.mark.asyncio
async def test_la_constancia_de_consentimiento_queda_en_el_radicado_y_en_el_asiento(tenant_ctx) -> None:
    import json

    from app.services.ventanilla_service import VentanillaService

    slug, conn = await _en(tenant_ctx)
    await _ventanilla_lista(conn)
    r = await VentanillaService(conn, slug).radicar(
        _payload(politica_version="2026-07"), DocumentService(conn, slug), ip="198.51.100.7"
    )
    meta = await conn.fetchval("SELECT metadata FROM radicados WHERE id = $1", r.radicado_id)
    meta = json.loads(meta) if isinstance(meta, str) else meta
    assert meta["consentimiento"]["politica_version"] == "2026-07"
    assert meta["consentimiento"]["ip_declarada"] == "198.51.100.7"
    asiento = await conn.fetchval(
        "SELECT payload FROM public.audit_log WHERE action = 'document.radicado_created' "
        "AND object_ref = $1",
        r.tracking_number,
    )
    asiento = json.loads(asiento) if isinstance(asiento, str) else asiento
    assert asiento["consentimiento"]["politica_version"] == "2026-07"


@pytest.mark.asyncio
async def test_el_estado_de_la_ventanilla_contra_las_tablas_reales(tenant_ctx) -> None:
    from app.services.ventanilla_service import VentanillaService

    slug, conn = await _en(tenant_ctx)
    e = await VentanillaService(conn, slug).estado()
    assert (e.habilitada, e.dependencia_receptora, e.tipos_admitidos) == (False, False, 0)
    await _ventanilla_lista(conn)
    e = await VentanillaService(conn, slug).estado()
    assert (e.habilitada, e.dependencia_receptora, e.tipos_admitidos) == (True, True, 1)
    # Inactiva la dependencia → deja de estar habilitada (misma condición que radicar).
    await conn.execute("UPDATE dependencias SET activo = false WHERE codigo = 901")
    assert (await VentanillaService(conn, slug).estado()).habilitada is False


# --------------------------------------------------------------------- AREA-09-02


@pytest.mark.asyncio
async def test_el_lote_con_dependencia_inexistente_falla_el_item_sin_gastar_numero(tenant_ctx) -> None:
    from app.schemas.batch import BatchDocumentCreate, BatchDocumentsRequest
    from app.services.batch_service import BatchService
    from tests.integration.test_lote_documentos_integration import _tenant_registrado

    slug, conn = await _en(tenant_ctx)
    async with _tenant_registrado(conn, slug):
        servicio = BatchService(conn, slug)
        job = await servicio.create_batch_documents(
            BatchDocumentsRequest(
                documents=[
                    BatchDocumentCreate(
                        doc_type="E", subject="Dependencia fantasma", doc_class="Petición",
                        dest_dept_code=777,
                    ),
                    BatchDocumentCreate(
                        doc_type="S", subject="Salida con origen", doc_class="Petición",
                        origin_dept_code=1,
                    ),
                ]
            ),
            uuid.uuid4(),
        )
        await servicio.process_batch_documents(job)
        estados = [
            r["status"]
            for r in await conn.fetch(
                "SELECT status FROM batch_job_items WHERE job_id = $1 ORDER BY item_index", job
            )
        ]
        assert estados == ["failed", "success"]
        fila = await conn.fetchrow(
            "SELECT origin_dept, origin_dept_code, doc_class FROM radicados WHERE doc_type = 'S'"
        )
        assert (fila["origin_dept"], fila["origin_dept_code"], fila["doc_class"]) == (
            "Dirección General", 1, "Petición",
        )
        # Solo la Salida gastó consecutivo; la Entrada fallida ni lo pidió.
        assert await conn.fetchval(
            "SELECT count(*) FROM tracking_sequences WHERE doc_type = 'E'"
        ) == 0


@pytest.mark.asyncio
async def test_la_derivacion_del_borrador_contra_las_tablas_reales(tenant_ctx) -> None:
    from app.schemas.borrador import BorradorRadicar
    from app.services.borrador_service import BorradorService

    slug, conn = await _en(tenant_ctx)
    autor = uuid.uuid4()
    await conn.execute(
        "INSERT INTO dependencias (codigo, nombre) VALUES (20, 'Jurídica'), (9, 'Archivo')"
    )
    await conn.execute(
        "INSERT INTO urd_usuaroldep (user_id, depe_id) VALUES ($1, 20), ($1, 9)", autor
    )
    plantilla = await conn.fetchval(
        "INSERT INTO plantilla (nombre, tipo_documental, cuerpo) VALUES ('P', 'Oficio', 'x') "
        "RETURNING id"
    )
    padre = await _radicado(conn, slug, dest_dept_code=20)
    svc = BorradorService(conn, slug)
    row = {"doc_type": "S", "plantilla_id": plantilla, "dest_dept": "Archivo", "radicado_padre_id": padre}
    tipo, origen, destino = await svc._datos_minimos_del_borrador(row, autor)
    assert (tipo, origen, destino) == ("Oficio", 20, 9)  # el padre está en SU dependencia
    row["radicado_padre_id"] = None
    assert (await svc._datos_minimos_del_borrador(row, autor))[1] == 9  # la de menor código
    # Lo explícito manda sobre lo derivado.
    datos = BorradorRadicar(tipo_documental="Memorando", origin_dept_code=20, dest_dept_code=1)
    assert await svc._datos_minimos_del_borrador(row, autor, datos) == ("Memorando", 20, 1)
    # Sin plantilla, sin URD y sin destino: nada que derivar.
    vacio = {"doc_type": "S", "plantilla_id": None, "dest_dept": None, "radicado_padre_id": None}
    assert await svc._datos_minimos_del_borrador(vacio, uuid.uuid4()) == (None, None, None)
