# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""T12 (cumplimiento archivístico en archive) contra Postgres REAL: lo que un mock de conexión
no puede probar —la forma del SQL, los CHECK, los privilegios, los WHERE gateados— y la cadena
de auditoría tras cada acto.

Un test por garantía (ids AREA-xx-yy en el nombre). Los helpers de seed son los de
`test_atomicity.py`; el `registrar_fixity_premis` y el `proteger_worm_firma_personal` de los
tests unitarios están sustituidos por un doble autouse (no hay storage-service aquí): cuando un
test necesita el método REAL usa `REGISTRAR_FIXITY_PREMIS_REAL` / `PROTEGER_WORM_FIRMA_PERSONAL_REAL`.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException
from orpycamcp_common import audit

from app.clients.signature_client import SignPersonalOutcome
from app.clients.storage_client import WormProtectionOutcome
from app.core.actor import Actor
from app.core.database import tenant_schema
from app.repositories.aip_repository import AipRepository
from app.schemas.transferencia import TransferenciaCreate
from app.services.archive_service import ArchiveService
from app.services.index_service import IndexService
from app.services.transferencia_service import TransferenciaService
from tests.conftest import (
    PROTEGER_WORM_FIRMA_PERSONAL_REAL,
    REGISTRAR_FIXITY_PREMIS_REAL,
    registrar_radicado,
)
from tests.test_transferencia_acta_firma_personal import _signed_personal

from .test_atomicity import (
    _seed_expediente_closed,
    _seed_transferencia_lista_para_recibir_con_documento,
)

pytestmark = pytest.mark.integration


async def _cadena_integra(conn: asyncpg.Connection, slug: str) -> None:
    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas
    assert res.primera_rota is None


# --- AREA-07-03: no se prepara una transferencia con el índice sin firmar -------


async def test_area_07_03_create_con_indice_pendiente_da_409_y_deja_asiento(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    created = await _seed_expediente_closed(conn, slug, "itest 07-03 indice pendiente")
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET estado = \'pendiente_firma\', firma_id = NULL '
        "WHERE expediente_id = $1",
        created.id,
    )

    with pytest.raises(HTTPException) as exc:
        await TransferenciaService(conn, slug).create(
            TransferenciaCreate(
                tipo="primaria", expediente_id=created.id,
                objeto="Transferencia primaria de la serie de contratos",
            ),
            Actor(id=uuid.uuid4(), username="itest"),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "indice_no_firmado"
    assert await conn.fetchval(
        f'SELECT count(*) FROM "{schema}".transferencias WHERE expediente_id = $1', created.id
    ) == 0
    fila = await conn.fetchrow(
        "SELECT payload FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = 'archive.transferencia_preparada_failed' ORDER BY id DESC LIMIT 1",
        slug,
    )
    assert fila is not None and json.loads(fila["payload"])["motivo"] == "indice_no_firmado"
    await _cadena_integra(conn, slug)


# --- AREA-07-06: par sin tenencia --------------------------------------------


async def test_area_07_06_par_sin_tenencia_se_rechaza_con_422(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    from tests.integration.test_transferencia_creacion_integridad import _make_ubicacion

    slug, conn = tenant_ctx
    created = await _seed_expediente_closed(conn, slug, "itest 07-06 sin tenencia")
    origen = await _make_ubicacion(conn, slug, tipo_archivo="gestion")
    destino = await _make_ubicacion(conn, slug, tipo_archivo="central")

    with pytest.raises(HTTPException) as exc:
        await TransferenciaService(conn, slug).create(
            TransferenciaCreate(
                tipo="primaria", expediente_id=created.id,
                objeto="Transferencia primaria de la serie de contratos",
                ubicacion_origen_id=origen.id, ubicacion_destino_id=destino.id,
            ),
            Actor(id=uuid.uuid4(), username="itest"),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "ubicacion_sin_tenencia"
    await _cadena_integra(conn, slug)


# --- AREA-07-04: el acta ata la firma del índice -------------------------------


async def test_area_07_04_el_acta_ata_la_firma_y_la_huella_del_indice(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    created, transferencia, _rid, _hash = await _seed_transferencia_lista_para_recibir_con_documento(
        conn, slug, "itest 07-04 acta ata firma"
    )
    indice = await conn.fetchrow(
        f'SELECT version, firma_id, xml_sha256 FROM "{schema}".expediente_indice '
        "WHERE expediente_id = $1 AND estado = 'firmado' ORDER BY version DESC LIMIT 1",
        created.id,
    )

    await TransferenciaService(conn, slug).recibir(
        transferencia.id, Actor(id=uuid.uuid4(), username="itest-recibe")
    )

    acta = await conn.fetchrow(
        f'SELECT indice_version, indice_firma_id, indice_sha256, acta_fuid_xml '
        f'FROM "{schema}".transferencia_acta WHERE transferencia_id = $1',
        transferencia.id,
    )
    assert acta["indice_version"] == indice["version"]
    assert acta["indice_firma_id"] == indice["firma_id"]
    assert acta["indice_sha256"].strip() == indice["xml_sha256"].strip()
    # y dentro de lo que se hashea y se sella
    assert str(indice["firma_id"]) in acta["acta_fuid_xml"]
    await _cadena_integra(conn, slug)


# --- AREA-07-09: objeto obligatorio en la base (sin romper el histórico) -------


async def test_area_07_09_objeto_obligatorio_en_bd_y_el_historico_sigue_operable(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    created = await _seed_expediente_closed(conn, slug, "itest 07-09 objeto")
    resp = await TransferenciaService(conn, slug).create(
        TransferenciaCreate(
            tipo="primaria", expediente_id=created.id,
            objeto="Transferencia primaria de la serie de contratos",
        ),
        Actor(id=uuid.uuid4(), username="itest"),
    )

    # fila NUEVA (created_at = ahora) sin objeto: la base la rechaza
    for malo in (None, "  ", "ab"):
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                f'UPDATE "{schema}".transferencias SET objeto = $2 WHERE id = $1', resp.id, malo
            )

    # fila ANTERIOR a la migración: sin objeto, y sigue pudiendo cambiar de estado (un
    # CHECK ... NOT VALID a secas la dejaría sin poder avanzar nunca)
    await conn.execute(
        f'UPDATE "{schema}".transferencias SET objeto = NULL, '
        "created_at = '2026-01-01T00:00:00+00' WHERE id = $1",
        resp.id,
    )
    await conn.execute(
        f"UPDATE \"{schema}\".transferencias SET estado = 'enviada' WHERE id = $1", resp.id
    )
    assert await conn.fetchval(
        f'SELECT estado FROM "{schema}".transferencias WHERE id = $1', resp.id
    ) == "enviada"


# --- AREA-07-08: capa de privilegios sobre el acta -----------------------------


async def test_area_07_08_el_rol_de_la_app_no_puede_actualizar_el_acta(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Con el REVOKE, el rol de la app falla por PRIVILEGIOS antes de llegar al trigger de
    inmutabilidad; sin él, la mutación llegaría al trigger (`RaiseError`). Se usa el rol REAL
    `orpycamcp_app` pero solo con grants sobre el schema desechable (no se altera el rol)."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    if not await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app'"):
        pytest.skip("el rol orpycamcp_app no existe en esta base")
    created, transferencia, _rid, _hash = await _seed_transferencia_lista_para_recibir_con_documento(
        conn, slug, "itest 07-08 revoke"
    )
    await TransferenciaService(conn, slug).recibir(
        transferencia.id, Actor(id=uuid.uuid4(), username="itest-recibe")
    )
    # Estado equivalente al de producción (auth 008: UPDATE/DELETE sobre todo el tenant) y,
    # encima, la migración 054 (idempotente) que retira lo concedido sobre el acta.
    await conn.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO orpycamcp_app')
    await conn.execute(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO orpycamcp_app'
    )
    from migrations import runner

    revoke = next(p for p in runner._sql_files(runner.TENANT_DIR) if "054_" in p.name)
    await conn.execute(f'SET search_path = "{schema}", public')
    await conn.execute(revoke.read_text())

    try:
        await conn.execute("SET ROLE orpycamcp_app")
    except asyncpg.PostgresError:
        pytest.skip("la conexión de pruebas no puede hacer SET ROLE orpycamcp_app")
    try:
        # control: sobre una tabla SIN revocar el rol sí puede escribir (el rechazo es específico)
        await conn.execute(
            f'UPDATE "{schema}".transferencias SET observacion = $2 WHERE id = $1',
            transferencia.id, "ok",
        )
        for tabla_sql in (
            f'UPDATE "{schema}".transferencia_acta SET acta_fuid_sha256 = \'x\'',
            f'DELETE FROM "{schema}".transferencia_acta',
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(tabla_sql)
    finally:
        await conn.execute("RESET ROLE")


# --- AREA-07-14: reintento manual del AIP agotado ------------------------------


async def test_area_07_14_el_reintento_solo_re_arma_un_agotado_y_respeta_el_clearance(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    created = await _seed_expediente_closed(conn, slug, "itest 07-14 reintento")
    repo = AipRepository(conn)

    async def _estado() -> str:
        return await conn.fetchval(
            f'SELECT estado FROM "{schema}".expediente_aip WHERE expediente_id = $1', created.id
        )

    # empaquetado: NO se toca (re-sellar un paquete ya inmutable)
    await conn.execute(
        f'INSERT INTO "{schema}".expediente_aip (expediente_id, estado) VALUES ($1, \'empaquetado\')',
        created.id,
    )
    assert await repo.reintentar_manual(created.id, max_clearance=3) is None
    assert await _estado() == "empaquetado"  # mutación: quitar AND estado='agotado' lo cambiaría

    # agotado pero el expediente es CLASIFICADO y el llamante no lo alcanza: el mismo None
    await conn.execute(
        f"UPDATE \"{schema}\".expediente_aip SET estado = 'agotado', intentos = 8, "
        "ultimo_error = 'storage_unavailable' WHERE expediente_id = $1",
        created.id,
    )
    await conn.execute(
        f'UPDATE "{schema}".expedientes SET nivel_seguridad = 3 WHERE id = $1', created.id
    )
    assert await repo.reintentar_manual(created.id, max_clearance=1) is None
    assert await _estado() == "agotado"

    # con clearance suficiente: pasa a pendiente, intentos a 0, y devuelve lo previo
    fila = await repo.reintentar_manual(created.id, max_clearance=3)
    assert fila is not None
    assert (fila["estado"], fila["intentos_previos"], fila["error_previo"]) == (
        "pendiente", 8, "storage_unavailable",
    )
    assert await _estado() == "pendiente"
    assert await conn.fetchval(
        f'SELECT intentos FROM "{schema}".expediente_aip WHERE expediente_id = $1', created.id
    ) == 0


async def test_area_07_14_el_servicio_deja_asiento_y_la_cadena_sigue_integra(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    created = await _seed_expediente_closed(conn, slug, "itest 07-14 servicio")
    await conn.execute(
        f'INSERT INTO "{schema}".expediente_aip (expediente_id, estado, intentos) '
        "VALUES ($1, 'agotado', 8)",
        created.id,
    )

    resultado = await ArchiveService(conn, slug).reintentar_aip(
        created.id, actor=str(uuid.uuid4()), user_id=None
    )

    assert resultado["estado"] == "pendiente"
    n = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = 'archive.aip_reintento_manual'",
        slug,
    )
    assert n == 1
    # segunda vez: ya no está agotado -> 404 neutro y NO hay un segundo asiento
    with pytest.raises(HTTPException) as exc:
        await ArchiveService(conn, slug).reintentar_aip(
            created.id, actor=str(uuid.uuid4()), user_id=None
        )
    assert exc.value.status_code == 404
    await _cadena_integra(conn, slug)


# --- AREA-07-01: el XSD se valida antes de sellar --------------------------------


async def test_area_07_01_un_indice_que_no_valida_no_se_sella(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    created = await _seed_expediente_closed(conn, slug, "itest 07-01 xsd")
    await conn.execute(
        f"UPDATE \"{schema}\".expediente_indice SET estado = 'vigente', firma_id = NULL "
        "WHERE expediente_id = $1",
        created.id,
    )
    # el XML REAL del índice, sin el `<cabecera>` obligatorio
    xml = await conn.fetchval(
        f'SELECT xml FROM "{schema}".expediente_indice WHERE expediente_id = $1 '
        "ORDER BY version DESC LIMIT 1",
        created.id,
    )
    roto = xml.replace("ns0:cabecera", "ns0:otracosa")
    assert roto != xml
    await conn.execute(
        f'UPDATE "{schema}".expediente_indice SET xml = $2 WHERE expediente_id = $1',
        created.id, roto,
    )

    with patch("app.clients.signature_client.sign_xml_outcome", new=AsyncMock()) as sellar:
        resultado = await IndexService(conn, slug).firmar_indice_close(
            created.id, tenant_slug=slug, actor_id=str(uuid.uuid4())
        )

    assert resultado is None
    sellar.assert_not_awaited()
    fila = await conn.fetchrow(
        f'SELECT estado, reconcile_ultimo_error FROM "{schema}".expediente_indice '
        "WHERE expediente_id = $1 ORDER BY version DESC LIMIT 1",
        created.id,
    )
    assert (fila["estado"], fila["reconcile_ultimo_error"]) == ("pendiente_firma", "xsd_invalido")
    await _cadena_integra(conn, slug)


# --- AREA-07-01: backfill del origen de vínculos legados -----------------------------


async def test_area_07_01_el_backfill_rellena_solo_nulls_en_abiertos_y_no_toca_los_cerrados(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    from app.schemas.archive import ExpedienteCreate, RadicadoLink

    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    archive = ArchiveService(conn, slug)

    # radicado vinculado cuando document-service aún NO sabía su origen ni formato
    tracking = f"2026-ITEST-E-{uuid.uuid4().hex[:6]}"
    rid = registrar_radicado(tracking)
    abierto = await archive.create_expediente(
        payload=ExpedienteCreate(name="itest 07-01 abierto", nivel_seguridad=1), opened_by=None
    )
    await archive.link_radicado(
        abierto.id, RadicadoLink(radicado_id=rid, tracking_number=tracking), user_id=None
    )
    rid2 = registrar_radicado(f"2026-ITEST-E-{uuid.uuid4().hex[:6]}")
    cerrado = await archive.create_expediente(
        payload=ExpedienteCreate(name="itest 07-01 cerrado", nivel_seguridad=1), opened_by=None
    )
    await archive.link_radicado(
        cerrado.id,
        RadicadoLink(radicado_id=rid2, tracking_number=_tracking_de(rid2)),
        user_id=None,
    )
    await archive.close_expediente(cerrado.id)
    # y un valor YA declarado que el backfill no debe pisar
    await conn.execute(
        f'UPDATE "{schema}".expediente_radicados SET formato = \'application/declarado\' '
        "WHERE expediente_id = $1",
        abierto.id,
    )

    # ahora document-service SÍ lo sabe
    registrar_radicado(
        tracking, radicado_id=rid, origin_dept_code=120, origin_dept="Oficina de Contratacion",
        formato="application/pdf", tamano_bytes=4096,
    )
    registrar_radicado(
        _tracking_de(rid2), radicado_id=rid2, origin_dept_code=130, origin_dept="Otra",
        formato="application/pdf", tamano_bytes=10,
    )
    primera = await archive.completar_origen_legado(50)
    segunda = await archive.completar_origen_legado(50)

    fila = await conn.fetchrow(
        f'SELECT dependencia_productora_codigo, dependencia_productora_nombre, formato, '
        f'tamano_bytes, origen_completado_at FROM "{schema}".expediente_radicados '
        "WHERE expediente_id = $1",
        abierto.id,
    )
    assert (fila["dependencia_productora_codigo"], fila["dependencia_productora_nombre"]) == (
        120, "Oficina de Contratacion",
    )
    assert fila["tamano_bytes"] == 4096
    assert fila["formato"] == "application/declarado", "pisó un valor ya declarado"
    assert fila["origen_completado_at"] is not None
    # el expediente CERRADO (probatorio) no se toca: ni columnas ni marca
    cerrado_fila = await conn.fetchrow(
        f'SELECT dependencia_productora_codigo, origen_completado_at '
        f'FROM "{schema}".expediente_radicados WHERE expediente_id = $1',
        cerrado.id,
    )
    assert cerrado_fila["dependencia_productora_codigo"] is None
    assert cerrado_fila["origen_completado_at"] is None
    assert primera["completados"] == 1
    assert segunda == {"vistos": 0, "completados": 0, "sin_cambios": 0}, "reeligió lo ya intentado"
    n = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = 'archive.vinculo_origen_completado'",
        slug,
    )
    assert n == 1
    await _cadena_integra(conn, slug)


def _tracking_de(radicado_id: uuid.UUID) -> str:
    from tests.conftest import _EN_DOCUMENT_SERVICE

    return _EN_DOCUMENT_SERVICE[str(radicado_id)]["tracking_number"]


# --- AREA-07-05: el fixity-check de la recepción como evento PREMIS ----------------


async def test_area_07_05_la_recepcion_guarda_el_detalle_y_el_evento_se_registra_una_vez(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    created, transferencia, _rid, _hash = await _seed_transferencia_lista_para_recibir_con_documento(
        conn, slug, "itest 07-05 fixity premis"
    )
    actor = Actor(id=uuid.uuid4(), username="itest-recibe")
    svc = TransferenciaService(conn, slug)
    await svc.recibir(transferencia.id, actor)

    fila = await conn.fetchrow(
        f'SELECT fixity_premis_detalle, fixity_premis_at FROM "{schema}".transferencia_acta_firma '
        "WHERE transferencia_id = $1",
        transferencia.id,
    )
    detalle = json.loads(fila["fixity_premis_detalle"])
    assert detalle["documentos_ok"] is True and detalle["num_documentos"] == 1
    assert fila["fixity_premis_at"] is None  # el doble autouse no registró nada
    pendientes = await svc._repo.list_fixity_premis_pending(10)
    assert [p["transferencia_id"] for p in pendientes] == [transferencia.id]

    with patch(
        "app.clients.storage_client.registrar_evento_internal", new=AsyncMock(return_value=True)
    ) as post:
        assert await REGISTRAR_FIXITY_PREMIS_REAL(svc, transferencia.id, origen="reconciliation") == "registrado"
        # idempotente: una segunda llamada no vuelve a registrar
        assert await REGISTRAR_FIXITY_PREMIS_REAL(svc, transferencia.id, origen="reconciliation") == "omitido"

    post.assert_awaited_once()
    assert post.await_args.kwargs["object_ref"] == str(created.id)
    assert await svc._repo.list_fixity_premis_pending(10) == []
    await _cadena_integra(conn, slug)


# --- AREA-07-10: WORM de las firmas personales del acta -------------------------------


async def test_area_07_10_la_firma_personal_nace_pendiente_y_queda_protegida(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    created, transferencia, _rid, _hash = await _seed_transferencia_lista_para_recibir_con_documento(
        conn, slug, "itest 07-10 firma personal worm"
    )
    actor = Actor(id=uuid.uuid4(), username="itest-recibe")
    svc = TransferenciaService(conn, slug)
    await svc.recibir(transferencia.id, actor)

    with patch(
        "app.clients.signature_client.sign_personal",
        new=AsyncMock(return_value=SignPersonalOutcome(signed=_signed_personal())),
    ):
        firma = await svc.firmar_personal(transferencia.id, actor, "123456", "Bearer x")

    assert firma.rol == "recibe"
    pendientes = await svc._repo.list_firma_personal_worm_pending(10)
    assert len(pendientes) == 1
    firma_id = pendientes[0]["firma_personal_id"]

    svc._resolve_worm_retention_acta = AsyncMock(return_value=(10, date(2026, 10, 1)))  # type: ignore[method-assign]
    salida = WormProtectionOutcome(
        ok=True, worm_object_id=uuid.uuid4(), retain_until=datetime(2036, 10, 1, tzinfo=UTC)
    )
    with patch(
        "app.clients.storage_client.proteger_artefacto_worm", new=AsyncMock(return_value=salida)
    ) as proteger:
        resultado = await PROTEGER_WORM_FIRMA_PERSONAL_REAL(
            svc, firma_id, actor_id=actor.audit_actor, origen="close"
        )

    assert resultado == "protegido"
    assert proteger.await_args.kwargs["tipo"] == "acta_firma_recibe"
    assert proteger.await_args.kwargs["expediente_id"] == str(created.id)
    marca = await conn.fetchval(
        f'SELECT worm_protegido_at FROM "{schema}".transferencia_acta_firma_personal_worm '
        "WHERE firma_personal_id = $1",
        firma_id,
    )
    assert marca is not None
    assert await svc._repo.list_firma_personal_worm_pending(10) == []
    n = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = 'archive.acta_firma_personal_worm_protegido'",
        slug,
    )
    assert n == 1
    await _cadena_integra(conn, slug)
