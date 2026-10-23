# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""AREA-07-10 contra Postgres REAL: lo que un mock de conexión no puede probar.

* «firma inobtenible» (migraciones 059/061): constancia FIRMADA + asiento en UNA tx, conflicto de
  interés con ninguna de las tres partes (servicio Y motor), exclusión mutua firma<->excepción
  en el MOTOR en ambos sentidos, revocación append-only que devuelve a la parte el derecho a
  firmar, y el verificador que no promueve nunca a `firmada_completa`.
* WORM de la constancia (sello y firma personal del declarante) y su renovación CT.
* Renovación CT de las firmas personales (migración 060): el filtro CT y el no-read-up
  viven en el `WHERE`, y el espejo local es monotónico.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException
from orpycamcp_common import audit

from app.clients.signature_client import (
    SignedIndice,
    SignOutcome,
    SignPersonalOutcome,
    VerifyResult,
)
from app.clients.storage_client import WormProtectionOutcome, WormRenewalOutcome
from app.core.actor import Actor
from app.core.database import tenant_schema
from app.repositories.transferencia_repository import TransferenciaRepository
from app.schemas.archive import TrdSerieCreate
from app.schemas.transferencia import FirmaInobtenibleRequest, RevocarFirmaInobtenibleRequest
from app.services.archive_service import ArchiveService
from app.services.constancia_firma_excepcion import canonicalizar, sha256_c14n, sha256_hex
from app.services.transferencia_service import TransferenciaService
from tests.test_transferencia_acta_firma_personal import _signed_personal

from .test_atomicity import _seed_transferencia_lista_para_recibir_con_documento

pytestmark = pytest.mark.integration

FUNDAMENTO = "Certificado de defuncion del receptor, radicado en la entidad el 2026-09-30."
FUNDAMENTO_REV = "Se declaro por error: el receptor esta vivo y firmara el acta."
RAD = uuid.uuid4()
ANEXO = uuid.uuid4()
NUMERO_RAD = "2026-ITEST-E-000001"
SHA_ANEXO = "e" * 64
_MIGRACIONES = Path(__file__).resolve().parents[2] / "migrations" / "tenant"
_MIGRACION_061 = _MIGRACIONES / "061_transferencia_acta_firma_excepcion_constancia.sql"
_MIGRACION_062 = _MIGRACIONES / "062_firma_excepcion_cadena_revocacion_firmada.sql"


def _req(rol: str = "recibe", **kw) -> FirmaInobtenibleRequest:
    base = dict(
        rol=rol, motivo="fallecimiento", fundamento=FUNDAMENTO, referencia_radicado_id=RAD,
        referencia_anexo_id=ANEXO,
    )
    base.update(kw)
    return FirmaInobtenibleRequest(**base)


def _rev(rol: str = "recibe", **kw) -> RevocarFirmaInobtenibleRequest:
    kw.setdefault("causa", "error_de_declaracion")
    return RevocarFirmaInobtenibleRequest(rol=rol, fundamento=FUNDAMENTO_REV, **kw)


#: Almacén simulado: lo que se SELLÓ, por clave. Es lo que «descarga» storage al leer el sobre.
_ALMACEN: dict[str, str] = {}


async def _descarga(*, tenant_slug: str, minio_key_firmado: str) -> str | None:
    return _ALMACEN.get(minio_key_firmado)


def _sello(*_a, **k) -> SignOutcome:
    clave = f"{uuid.uuid4()}/constancia-firmada.xml"
    _ALMACEN[clave] = k.get("payload_xml", "")
    return SignOutcome(
        signed=SignedIndice(
            firma_id=uuid.uuid4(),
            minio_key_firmado=clave,
            nivel_conformidad="xades_t_no_acreditado",
            xades_level="XAdES-T",
            tipo_firma="xades_local",
            acreditado=False,
            certificado_fingerprint="ab:cd",
        )
    )


@contextmanager
def _externos(*, worm: bool = False):
    """document-service (soporte) y signature-service (sello/firma personal) simulados; el
    Object-Lock post-commit también salvo que el test quiera el método real (`worm=True`)."""
    with (
        # el declarante del test no es un usuario del catálogo: se le da PERM_FIRMA (el sello se
        # estampa a su nombre); el RBAC real se prueba en las pruebas del router y las de unidad
        patch(
            "app.services.transferencia_service.check_permission", new=AsyncMock(return_value=True)
        ),
        patch(
            "app.clients.document_client.fetch_radicado_metadata_internal",
            new=AsyncMock(return_value={str(RAD): {"subject": "x", "tracking_number": NUMERO_RAD}}),
        ),
        patch(
            "app.clients.document_client.fetch_anexos_internal",
            new=AsyncMock(return_value={str(RAD): [{"file_id": str(ANEXO), "filename": "s.pdf"}]}),
        ),
        patch(
            "app.clients.storage_client.fetch_file_hashes_internal",
            new=AsyncMock(return_value={str(ANEXO): SHA_ANEXO}),
        ),
        patch("app.clients.signature_client.sign_xml_outcome", new=AsyncMock(side_effect=_sello)) as sx,
        patch("app.clients.storage_client.download_signed_xml", new=AsyncMock(side_effect=_descarga)),
        patch(
            "app.clients.signature_client.sign_personal",
            new=AsyncMock(return_value=SignPersonalOutcome(signed=_signed_personal())),
        ) as sp,
    ):
        if worm:
            yield sx, sp, None
        else:
            with (
                patch(
                    "app.services.transferencia_service.TransferenciaService.proteger_worm_firma_excepcion",
                    new=AsyncMock(return_value="protegido"),
                ) as w,
                patch(
                    "app.services.transferencia_service.TransferenciaService.proteger_worm_revocacion",
                    new=AsyncMock(return_value="protegido"),
                ),
            ):
                yield sx, sp, w


async def _verificar(svc: TransferenciaService, tid, eid, actor):
    """`verificar_fundamento` con el almacén simulado: la cuarta copia (el sobre sellado) se baja."""
    with _externos():
        return await svc.verificar_fundamento(tid, eid, actor)


async def _declarar(svc: TransferenciaService, tid, admin: Actor, req=None, **externos):
    with _externos(**externos):
        return await svc.declarar_firma_inobtenible(tid, admin, req or _req())


async def _revocar(svc: TransferenciaService, tid, actor: Actor, req=None, auth=None):
    """Revoca con los colaboradores externos simulados (el sello se estampa a nombre de quien
    revoca: signature-service y el RBAC no están en este tier)."""
    with _externos():
        return await svc.revocar_firma_inobtenible(tid, actor, req or _rev(), auth)


async def _acta_recibida(conn: asyncpg.Connection, slug: str, nombre: str, **seed_kw):
    """Transferencia recibida con dos identidades DISTINTAS: elabora (= entrega en este seed) y
    recibe. Devuelve (created, transferencia_id, elabora, recibe)."""
    elabora = Actor(id=uuid.uuid4(), username="itest-elabora")
    recibe = Actor(id=uuid.uuid4(), username="itest-recibe")
    created, transferencia, _rid, _hash = await _seed_transferencia_lista_para_recibir_con_documento(
        conn, slug, nombre, elabora, **seed_kw
    )
    await TransferenciaService(conn, slug).recibir(transferencia.id, recibe)
    return created, transferencia.id, elabora, recibe


async def _cadena_integra(conn: asyncpg.Connection, slug: str) -> None:
    res = await audit.verify_chain(conn, slug)
    assert res.ok is True, res.roturas


async def _firmar(svc: TransferenciaService, tid: uuid.UUID, actor: Actor, rol: str | None = None):
    with patch(
        "app.clients.signature_client.sign_personal",
        new=AsyncMock(return_value=SignPersonalOutcome(signed=_signed_personal())),
    ):
        return await svc.firmar_personal(tid, actor, "123456", "Bearer x", rol)


async def _n_asientos(conn: asyncpg.Connection, slug: str, accion: str) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2", slug, accion
    )


def _insert_sql(schema: str) -> str:
    return (
        f'INSERT INTO "{schema}".transferencia_acta_firma_excepcion '
        "(transferencia_id, rol, parte_id, motivo, fundamento, referencia_documental, declarada_por) "
    )


# --- el acto ----------------------------------------------------------------------------


async def test_declarar_deja_constancia_firmada_y_asiento_atomicos_y_la_cadena_integra(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _elabora, recibe = await _acta_recibida(conn, slug, "itest 07-10 declarar")
    admin = Actor(id=uuid.uuid4(), username="itest-admin")

    resp = await _declarar(TransferenciaService(conn, slug), tid, admin)

    assert resp.parte_id == recibe.id and resp.declarada_por == admin.id and resp.firmada
    fila = await conn.fetchrow(
        f'SELECT * FROM "{schema}".transferencia_acta_firma_excepcion WHERE transferencia_id = $1', tid
    )
    assert fila["parte_id"] == recibe.id and fila["fundamento"] == FUNDAMENTO
    assert fila["referencia_documental"] == f"radicado:{RAD}#anexo:{ANEXO}" and fila["referencia_radicado_id"] == RAD
    # lo persistido es exactamente lo que se firmó, y su huella
    assert fila["firma_id"] is not None and fila["minio_key_firmado"]
    assert fila["constancia_sha256"] == sha256_c14n(fila["constancia_xml"])
    assert fila["constancia_xml"] == canonicalizar(fila["constancia_xml"])  # lo firmado ES la C14N
    # el fundamento viaja como compromiso con sal por registro: ni su texto, ni su SHA-256 plano,
    # ni la sal
    sal = bytes(fila["fundamento_sal"])
    assert len(sal) == 32
    assert fila["fundamento_compromiso"] == hashlib.sha256(sal + FUNDAMENTO.encode()).hexdigest()
    assert fila["fundamento_compromiso"] in fila["constancia_xml"]
    assert "defuncion" not in fila["constancia_xml"] and sal.hex() not in fila["constancia_xml"]
    assert sha256_hex(FUNDAMENTO) not in fila["constancia_xml"]
    assert fila["fundamento_hmac"] is None and fila["constancia_version"] == 3
    assert fila["ordinal"] == 1 and fila["sustituye_a"] is None
    assert fila["referencia_radicado_numero"] == NUMERO_RAD and fila["referencia_anexo_sha256"] == SHA_ANEXO
    # bookkeeping WORM del sello en la MISMA tx (sin firma personal: un solo artefacto)
    assert await conn.fetchval(
        f'SELECT array_agg(artefacto) FROM "{schema}".transferencia_acta_firma_excepcion_worm '
        "WHERE excepcion_id = $1", fila["id"],
    ) == ["sello", "sobre"]
    asiento = await conn.fetchrow(
        "SELECT actor, payload FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = 'archive.acta_firma_inobtenible_declarada'", slug,
    )
    assert asiento is not None and asiento["actor"] == str(admin.id)
    payload = json.loads(asiento["payload"])
    assert payload["parte_id"] == str(recibe.id) and payload["rol"] == "recibe"
    assert payload["fundamento_compromiso"] == fila["fundamento_compromiso"]
    assert "fundamento_sha256" not in payload and "fundamento_sal" not in payload
    assert sal.hex() not in json.dumps(payload) and NUMERO_RAD not in json.dumps(payload)
    assert payload["constancia_sha256"] == fila["constancia_sha256"]
    assert "defuncion" not in json.dumps(payload) and sha256_hex(FUNDAMENTO) not in json.dumps(payload)
    await _cadena_integra(conn, slug)


async def test_con_la_firma_personal_del_declarante_quedan_dos_artefactos_para_el_worm(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, _r = await _acta_recibida(conn, slug, "itest 07-10 dos artefactos")
    admin = Actor(id=uuid.uuid4())
    svc = TransferenciaService(conn, slug)
    with _externos():
        resp = await svc.declarar_firma_inobtenible(tid, admin, _req(totp_code="123456"), "Bearer x")
    assert resp.firma_personal is True
    assert await conn.fetchval(
        f'SELECT array_agg(artefacto ORDER BY artefacto) FROM "{schema}".transferencia_acta_firma_excepcion_worm',
    ) == ["personal", "sello", "sobre"]


async def test_si_el_asiento_falla_no_queda_ni_constancia_ni_bookkeeping_worm(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, _r = await _acta_recibida(conn, slug, "itest 07-10 atomico")

    with patch(
        "app.services.transferencia_service.audit.append", new=AsyncMock(side_effect=RuntimeError("boom"))
    ):
        with pytest.raises(RuntimeError):
            await _declarar(TransferenciaService(conn, slug), tid, Actor(id=uuid.uuid4()))

    assert await conn.fetchval(
        f'SELECT count(*) FROM "{schema}".transferencia_acta_firma_excepcion WHERE transferencia_id = $1', tid,
    ) == 0
    assert await conn.fetchval(
        f'SELECT count(*) FROM "{schema}".transferencia_acta_firma_excepcion_worm'
    ) == 0


# --- conflicto de interés ---------------------------------------------------------------


async def test_quien_declara_no_es_ninguna_parte_ni_por_servicio_ni_por_el_motor(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, elabora, recibe = await _acta_recibida(conn, slug, "itest 07-10 conflicto")
    svc = TransferenciaService(conn, slug)

    # la parte afectada, y TAMBIÉN quien elaboró/envió la transferencia
    for declarante, rol in ((recibe, "recibe"), (elabora, "recibe"), (recibe, "entrega")):
        with pytest.raises(HTTPException) as exc:
            await _declarar(svc, tid, declarante, _req(rol))
        assert exc.value.status_code == 403
        assert exc.value.detail["error"] == "declarante_es_parte_de_la_transferencia"
    assert await conn.fetchval(f'SELECT count(*) FROM "{schema}".transferencia_acta_firma_excepcion') == 0
    assert await _n_asientos(conn, slug, "archive.acta_firma_inobtenible_denegada") == 3

    # el motor lo ataja aunque alguien se salte el servicio: la parte que no firma...
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            _insert_sql(schema) + "VALUES ($1, 'recibe', $2, 'retiro', $3, 'ref', $2)",
            tid, recibe.id, FUNDAMENTO,
        )
    # ...y cualquier otra parte (el elaborador declarando la excepción del receptor)
    with pytest.raises(asyncpg.CheckViolationError) as exc_db:
        await conn.execute(
            _insert_sql(schema) + "VALUES ($1, 'recibe', $2, 'retiro', $3, 'ref', $4)",
            tid, recibe.id, FUNDAMENTO, elabora.id,
        )
    assert exc_db.value.constraint_name == "ck_firma_excepcion_declarante_es_parte"
    await _cadena_integra(conn, slug)


# --- exclusión mutua y revocación -------------------------------------------------------


async def test_exclusion_mutua_firma_y_excepcion_en_el_motor_en_los_dos_sentidos(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, elabora, recibe = await _acta_recibida(conn, slug, "itest 07-10 exclusion")
    svc = TransferenciaService(conn, slug)
    admin = Actor(id=uuid.uuid4())

    # sentido 1: ya firmó -> no admite excepción (servicio 409 y motor CHECK)
    await _firmar(svc, tid, recibe)
    with pytest.raises(HTTPException) as exc:
        await _declarar(svc, tid, admin, _req("recibe"))
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "parte_ya_firmo"
    with pytest.raises(asyncpg.CheckViolationError) as exc_db:
        await conn.execute(
            _insert_sql(schema) + "VALUES ($1, 'recibe', $2, 'retiro', $3, 'ref', $4)",
            tid, recibe.id, FUNDAMENTO, admin.id,
        )
    assert exc_db.value.constraint_name == "ck_firma_excepcion_parte_ya_firmo"

    # sentido 2: con excepción -> no firma (servicio 409 sin quemar TOTP y motor CHECK)
    await _declarar(svc, tid, admin, _req("elabora", motivo="retiro"))
    with patch("app.clients.signature_client.sign_personal", new=AsyncMock()) as sign:
        with pytest.raises(HTTPException) as exc:
            await svc.firmar_personal(tid, elabora, "123456", "Bearer x", "elabora")
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "firma_excepcion_declarada"
    sign.assert_not_awaited()
    with pytest.raises(asyncpg.CheckViolationError) as exc_db:
        await conn.execute(
            f'INSERT INTO "{schema}".transferencia_acta_firma_personal '
            "(transferencia_id, rol, firmante_id, firma_id) VALUES ($1, 'elabora', $2, $3)",
            tid, elabora.id, uuid.uuid4(),
        )
    assert exc_db.value.constraint_name == "ck_firma_personal_excepcion_vigente"
    await _cadena_integra(conn, slug)


async def test_revocar_es_una_fila_nueva_y_la_parte_vuelve_a_poder_firmar(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, recibe = await _acta_recibida(conn, slug, "itest 07-10 revocar")
    svc = TransferenciaService(conn, slug)
    admin = Actor(id=uuid.uuid4())
    exc_dec = await _declarar(svc, tid, admin)

    # mientras la excepción rige, el motor impide la firma
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            f'INSERT INTO "{schema}".transferencia_acta_firma_personal '
            "(transferencia_id, rol, firmante_id, firma_id) VALUES ($1, 'recibe', $2, $3)",
            tid, recibe.id, uuid.uuid4(),
        )

    otro_admin = Actor(id=uuid.uuid4())
    rev = await _revocar(svc, tid, otro_admin, _rev())
    assert rev.revocada and rev.revocada_por == otro_admin.id and rev.id == exc_dec.id
    # la constancia original NO se tocó; la revocación es otra fila
    assert await conn.fetchval(
        f'SELECT count(*) FROM "{schema}".transferencia_acta_firma_excepcion'
    ) == 1
    assert await conn.fetchval(
        f'SELECT count(*) FROM "{schema}".transferencia_acta_firma_excepcion_revocacion'
    ) == 1
    assert await _n_asientos(conn, slug, "archive.acta_firma_inobtenible_revocada") == 1

    # ahora SÍ puede firmar: servicio y motor
    firma = await _firmar(svc, tid, recibe)
    assert firma.rol == "recibe"
    # y el estado lo refleja: la excepción revocada ya no cubre a la parte
    v = await svc.acta_verificacion(tid, None)
    assert v.completitud == "firmada_receptor"
    assert [e.revocada for e in v.excepciones] == [True]

    # no se revoca dos veces; y como la parte ya firmó, la cadena está cerrada: la firma prevalece
    with pytest.raises(HTTPException) as e1:
        await _revocar(svc, tid, otro_admin, _rev())
    assert e1.value.status_code == 409 and e1.value.detail["error"] == "excepcion_ya_revocada"
    with pytest.raises(HTTPException) as e2:
        await _declarar(svc, tid, admin)
    assert e2.value.status_code == 409 and e2.value.detail["error"] == "parte_ya_firmo"
    await _cadena_integra(conn, slug)


async def test_la_revocacion_es_append_only_y_quien_revoca_no_puede_ser_parte(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, elabora, recibe = await _acta_recibida(conn, slug, "itest 07-10 revocacion append")
    svc = TransferenciaService(conn, slug)
    admin = Actor(id=uuid.uuid4())
    await _declarar(svc, tid, admin)

    # una parte no revoca: ni el afectado ni el elaborador (servicio) ni por el motor
    for revocante in (recibe, elabora):
        with pytest.raises(HTTPException) as exc:
            await _revocar(svc, tid, revocante, _rev())
        assert exc.value.status_code == 403
        assert exc.value.detail["error"] == "declarante_es_parte_de_la_transferencia"
    assert await _n_asientos(conn, slug, "archive.acta_firma_inobtenible_revocacion_denegada") == 2
    eid = await conn.fetchval(f'SELECT id FROM "{schema}".transferencia_acta_firma_excepcion')
    with pytest.raises(asyncpg.CheckViolationError) as exc_db:
        await conn.execute(
            f'INSERT INTO "{schema}".transferencia_acta_firma_excepcion_revocacion '
            "(excepcion_id, revocada_por, fundamento) VALUES ($1, $2, $3)", eid, elabora.id, FUNDAMENTO_REV,
        )
    assert exc_db.value.constraint_name == "ck_firma_excepcion_revocante_es_parte"

    await _revocar(svc, tid, Actor(id=uuid.uuid4()), _rev())
    for sql in (
        f'UPDATE "{schema}".transferencia_acta_firma_excepcion_revocacion SET fundamento = repeat(\'x\', 30)',
        f'DELETE FROM "{schema}".transferencia_acta_firma_excepcion_revocacion',
        # CASCADE: desde la 062 la tabla WORM de la revocación la referencia y un TRUNCATE plano
        # lo frena la FK antes que el trigger; con CASCADE llega el trigger de cada tabla.
        f'TRUNCATE "{schema}".transferencia_acta_firma_excepcion_revocacion CASCADE',
    ):
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await conn.execute(sql)
    # revocar lo que no existe
    with pytest.raises(HTTPException) as e404:
        await _revocar(svc, tid, Actor(id=uuid.uuid4()), _rev("entrega"))
    assert e404.value.status_code == 404 and e404.value.detail["error"] == "excepcion_not_found"
    await _cadena_integra(conn, slug)


async def test_la_constancia_es_inmutable_y_no_se_declara_dos_veces(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, _r = await _acta_recibida(conn, slug, "itest 07-10 inmutable")
    svc = TransferenciaService(conn, slug)
    admin = Actor(id=uuid.uuid4())
    await _declarar(svc, tid, admin)

    with pytest.raises(HTTPException) as exc:
        await _declarar(svc, tid, admin, _req(fundamento="Otro fundamento distinto, igualmente largo."))
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "excepcion_vigente"
    assert await _n_asientos(conn, slug, "archive.acta_firma_inobtenible_declarada") == 1

    for sql in (
        f'UPDATE "{schema}".transferencia_acta_firma_excepcion SET fundamento = repeat(\'x\', 30)',
        f'DELETE FROM "{schema}".transferencia_acta_firma_excepcion',
        # CASCADE: desde la 061 la revocación referencia a la constancia y un TRUNCATE plano
        # lo frena la FK antes que el trigger; con CASCADE llega el trigger de cada tabla.
        f'TRUNCATE "{schema}".transferencia_acta_firma_excepcion CASCADE',
    ):
        with pytest.raises(asyncpg.PostgresError, match="append-only"):
            await conn.execute(sql)


async def test_la_base_rechaza_fundamento_corto_motivo_fuera_de_catalogo_y_firmas_a_medias(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, recibe = await _acta_recibida(conn, slug, "itest 07-10 checks")
    for fundamento, motivo, ref in (("corto", "retiro", "ref"), (FUNDAMENTO, "porque si", "ref"), (FUNDAMENTO, "retiro", " ")):
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                _insert_sql(schema) + "VALUES ($1, 'recibe', $2, $3, $4, $5, $6)",
                tid, recibe.id, motivo, fundamento, ref, uuid.uuid4(),
            )
    # un sello sin su clave, o una firma personal «requerida» sin firma: incoherentes
    for columna, valor in (
        ("firma_id", "gen_random_uuid()"),
        ("firma_personal_requerida", "TRUE"),
        ("constancia_sha256", "repeat('a', 64)"),
    ):
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                f'INSERT INTO "{schema}".transferencia_acta_firma_excepcion '
                f"(transferencia_id, rol, parte_id, motivo, fundamento, referencia_documental, "
                f"declarada_por, {columna}) VALUES ($1, 'recibe', $2, 'retiro', $3, 'ref', $4, {valor})",
                tid, recibe.id, FUNDAMENTO, uuid.uuid4(),
            )


async def test_la_061_es_idempotente_y_no_toca_lo_ya_declarado(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, _r = await _acta_recibida(conn, slug, "itest 07-10 idempotente")
    await _declarar(TransferenciaService(conn, slug), tid, Actor(id=uuid.uuid4()))
    antes = await conn.fetchrow(f'SELECT * FROM "{schema}".transferencia_acta_firma_excepcion')
    await conn.execute(f'SET search_path TO "{schema}"')
    for migracion in (_MIGRACION_061, _MIGRACION_062):
        await conn.execute(migracion.read_text(encoding="utf-8"))
        await conn.execute(migracion.read_text(encoding="utf-8"))
    assert dict(antes) == dict(await conn.fetchrow(f'SELECT * FROM "{schema}".transferencia_acta_firma_excepcion'))


# --- el verificador ----------------------------------------------------------------------


@contextmanager
def _verificacion(ok: bool = True):
    res = VerifyResult(
        valida=ok, nivel_conformidad="xades_personal_no_acreditado", acreditado=False,
        motivo=None, revocacion_status="vigente", advertencias=["tsa_sin_esscertid_anterior_al_corte"],
    )
    with (
        patch("app.clients.storage_client.download_signed_xml", new=AsyncMock(return_value="<xml/>")),
        patch("app.clients.signature_client.verify", new=AsyncMock(return_value=res)),
    ):
        yield


async def test_el_verificador_nunca_promueve_a_firmada_completa_y_es_honesto_con_la_etiqueta(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    _c, tid, elabora, recibe = await _acta_recibida(conn, slug, "itest 07-10 verificacion")
    svc = TransferenciaService(conn, slug)
    admin = Actor(id=uuid.uuid4())

    with _verificacion():
        antes = await svc.acta_verificacion(tid, None)
        assert antes.completitud == "sellada" and antes.excepciones == []

        # NINGUNA parte firmó: las dos cubiertas solo por excepción -> NO se dice «firmada_*»
        await _declarar(svc, tid, admin, _req("recibe"))
        await _declarar(svc, tid, admin, _req("entrega", motivo="retiro"))
        sin_firmas = await svc.acta_verificacion(tid, None)
        assert sin_firmas.completitud == "sellada_con_excepcion"
        assert sin_firmas.constancias_sin_firma == 0
        assert sin_firmas.advertencias == ["tsa_sin_esscertid_anterior_al_corte"]  # sin repetir
        assert all(e.sello_valida is True and e.firmada for e in sin_firmas.excepciones)

    # una parte firma (tras revocar su excepción) y la otra sigue con excepción
    await _revocar(svc, tid, Actor(id=uuid.uuid4()), _rev("entrega"))
    await _firmar(svc, tid, elabora, "entrega")
    with _verificacion():
        despues = await svc.acta_verificacion(tid, None)
        assert despues.completitud == "firmada_con_excepcion"
        assert sorted(e.rol for e in despues.excepciones) == ["entrega", "recibe"]
        acta = await svc.acta(tid, None)
    assert sorted(e.rol for e in acta.excepciones_firma) == ["entrega", "recibe"]
    # sin permiso (anónimo), el fundamento completo no sale pero sí su huella
    assert all(
        e.fundamento is None and e.fundamento_sal is None and e.fundamento_compromiso
        for e in acta.excepciones_firma
    )
    # el acta congelada no se tocó: su huella sigue siendo la del instante del traspaso
    assert acta.acta_fuid_sha256


async def test_firma_personal_pendiente_lee_la_politica_de_la_entidad_del_tenant(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """`sgd_param_admin` la crea tenant-service; aquí se crea en el schema desechable."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, _r = await _acta_recibida(conn, slug, "itest 07-10 politica")
    await conn.execute(
        f'CREATE TABLE IF NOT EXISTS "{schema}".sgd_param_admin (key TEXT PRIMARY KEY, value JSONB)'
    )
    svc = TransferenciaService(conn, slug)
    with _verificacion():
        sin_param = await svc.acta_verificacion(tid, None)
        assert sin_param.completitud == "sellada" and sin_param.politica_xades == "off"
        await conn.execute(
            f'INSERT INTO "{schema}".sgd_param_admin (key, value) VALUES '
            "('firma_personal_require_xades', '\"todos\"'::jsonb)"
        )
        con_param = await svc.acta_verificacion(tid, None)
        assert con_param.completitud == "firma_personal_pendiente"
        assert con_param.politica_xades == "todos"


async def test_la_politica_de_la_entidad_hace_obligatoria_la_firma_personal_del_declarante(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, _r = await _acta_recibida(conn, slug, "itest 07-10 obligatoria")
    await conn.execute(
        f'CREATE TABLE IF NOT EXISTS "{schema}".sgd_param_admin (key TEXT PRIMARY KEY, value JSONB)'
    )
    await conn.execute(
        f'INSERT INTO "{schema}".sgd_param_admin (key, value) VALUES '
        "('firma_personal_require_xades', '\"todos\"'::jsonb)"
    )
    svc = TransferenciaService(conn, slug)
    admin = Actor(id=uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        await _declarar(svc, tid, admin)
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "firma_personal_declarante_requerida"
    assert await conn.fetchval(f'SELECT count(*) FROM "{schema}".transferencia_acta_firma_excepcion') == 0
    with _externos():
        ok = await svc.declarar_firma_inobtenible(tid, admin, _req(totp_code="123456"), "Bearer x")
    assert ok.firma_personal and ok.firma_personal_requerida


# --- WORM de la constancia (migración 061) ------------------------------------------------


async def _serie_y_constancia(conn, slug, nombre, *, disposition: str, con_personal: bool = False):
    created, tid, _elabora, _recibe = await _acta_recibida(conn, slug, nombre)
    schema = tenant_schema(slug)
    serie = await ArchiveService(conn, slug).create_trd_serie(
        TrdSerieCreate(
            code=f"IT-{uuid.uuid4().hex[:6]}", name="itest serie", disposition=disposition,
            archivo_gestion_years=2,
            archivo_central_years=3,
        ),
        actor=None,
    )
    await conn.execute(f'ALTER TABLE "{schema}".expedientes DISABLE TRIGGER USER')
    await conn.execute(
        f'UPDATE "{schema}".expedientes SET trd_serie_id = $2 WHERE id = $1', created.id, serie.id
    )
    await conn.execute(f'ALTER TABLE "{schema}".expedientes ENABLE TRIGGER USER')
    svc = TransferenciaService(conn, slug)
    with _externos():
        exc = await svc.declarar_firma_inobtenible(
            tid, Actor(id=uuid.uuid4()), _req(totp_code="123456" if con_personal else None), "Bearer x"
        )
    return svc, created, tid, exc.id


async def test_la_proteccion_worm_de_la_constancia_espeja_y_asienta_y_la_barrida_la_recoge(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    svc, created, _tid, eid = await _serie_y_constancia(
        conn, slug, "itest 07-10 worm", disposition="CT", con_personal=True
    )
    repo = TransferenciaRepository(conn, slug)
    pendientes = await repo.list_firma_excepcion_worm_pending(10)
    assert sorted(p["artefacto"] for p in pendientes) == ["personal", "sello", "sobre"]

    retain = datetime.now(tz=UTC) + timedelta(days=365 * 7)
    salida = WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4(), retain_until=retain)
    with patch(
        "app.clients.storage_client.proteger_artefacto_worm_internal",
        new=AsyncMock(return_value=salida),
    ) as st:
        r1 = await svc.proteger_worm_firma_excepcion(eid, "sello", actor_id=None, origen="reconciliation")
        r2 = await svc.proteger_worm_firma_excepcion(eid, "personal", actor_id=None, origen="reconciliation")
        r3 = await svc.proteger_worm_firma_excepcion(eid, "sobre", actor_id=None, origen="reconciliation")
        again = await svc.proteger_worm_firma_excepcion(eid, "sello", actor_id=None, origen="reconciliation")
    assert (r1, r2, r3, again) == ("protegido", "protegido", "protegido", "omitido")
    assert [c.kwargs["tipo"] for c in st.await_args_list] == [
        "acta_firma_excepcion_recibe", "acta_firma_excepcion_personal_recibe",
        "acta_firma_excepcion_sobre_recibe",
    ]
    # el sobre es un objeto propio: su file_id sale de SU clave, no de la del sello
    llave_sobre = await conn.fetchval(
        f'SELECT sobre_minio_key FROM "{schema}".transferencia_acta_firma_excepcion WHERE id = $1', eid
    )
    assert st.await_args_list[2].kwargs["file_id"] == llave_sobre.split("/", 1)[0]
    # el file_id sale de la clave del XML guardado, nunca de una entrada externa
    llave = await conn.fetchval(
        f'SELECT minio_key_firmado FROM "{schema}".transferencia_acta_firma_excepcion WHERE id = $1', eid
    )
    assert st.await_args_list[0].kwargs["file_id"] == llave.split("/", 1)[0]
    assert await repo.list_firma_excepcion_worm_pending(10) == []
    assert await _n_asientos(conn, slug, "archive.acta_firma_excepcion_worm_protegido") == 3

    # ...y la visibilidad/renovación solo ve CT, por vencer y con el clearance en el WHERE
    await conn.execute(
        f'UPDATE "{schema}".transferencia_acta_firma_excepcion_worm '
        "SET worm_retain_until = now() + interval '30 days'"
    )
    ren = await repo.list_firma_excepcion_renovacion_pending(ventana_dias=400, ct_codes=["CT"], limit=10)
    assert sorted(r["artefacto"] for r in ren) == ["personal", "sello", "sobre"]
    # la renovación del sobre usa SU file_id (el del objeto sellado del sobre)
    assert next(r for r in ren if r["artefacto"] == "sobre")["minio_key_firmado"] == llave_sobre
    assert await repo.list_firma_excepcion_renovacion_pending(ventana_dias=400, ct_codes=["E"], limit=10) == []
    assert await repo.list_firma_excepcion_renovacion_pending(ventana_dias=10, ct_codes=["CT"], limit=10) == []
    await conn.execute(f'UPDATE "{schema}".expedientes SET nivel_seguridad = 3')
    assert await repo.list_firma_excepcion_renovacion_visibilidad(
        max_clearance=1, ct_codes=["CT"], ventana_dias=400) == []
    assert len(await repo.list_firma_excepcion_renovacion_visibilidad(
        max_clearance=3, ct_codes=["CT"], ventana_dias=400)) == 3
    assert created.id  # el expediente de la constancia


async def test_renovar_la_constancia_extiende_asienta_y_nunca_retrocede_el_espejo(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    svc, created, tid, eid = await _serie_y_constancia(
        conn, slug, "itest 07-10 renueva constancia", disposition="CT"
    )
    await conn.execute(
        f'UPDATE "{schema}".transferencia_acta_firma_excepcion_worm '
        "SET worm_protegido_at = now(), worm_retain_until = now() + interval '30 days'"
    )
    nueva = datetime.now(tz=UTC) + timedelta(days=365 * 5)
    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=True, retain_until=nueva, renovado=True)),
    ) as st:
        llave = await conn.fetchval(
            f'SELECT minio_key_firmado FROM "{schema}".transferencia_acta_firma_excepcion WHERE id = $1', eid
        )
        res = await svc.renovar_worm_firma_excepcion(
            eid, "sello", tid, created.id, "recibe", intentos_previos=0, minio_key_firmado=llave
        )
    assert res == "renovado" and st.await_args.kwargs["tipo"] == "acta_firma_excepcion_recibe"
    assert st.await_args.kwargs["file_id"] == llave.split("/", 1)[0]
    espejo = await conn.fetchval(
        f'SELECT worm_retain_until FROM "{schema}".transferencia_acta_firma_excepcion_worm '
        "WHERE excepcion_id = $1 AND artefacto = 'sello'", eid,
    )
    assert abs((espejo - nueva).total_seconds()) < 1
    assert await _n_asientos(conn, slug, "archive.acta_firma_excepcion_worm_retencion_renovada") == 1
    # una respuesta tardía con una fecha ANTERIOR no hace retroceder el espejo
    await TransferenciaRepository(conn, slug).mark_firma_excepcion_worm_renovado(
        eid, "sello", worm_retain_until=nueva - timedelta(days=200)
    )
    espejo2 = await conn.fetchval(
        f'SELECT worm_retain_until FROM "{schema}".transferencia_acta_firma_excepcion_worm '
        "WHERE excepcion_id = $1 AND artefacto = 'sello'", eid,
    )
    assert abs((espejo2 - nueva).total_seconds()) < 1
    await _cadena_integra(conn, slug)


# --- renovación CT de las firmas personales (migración 060) ------------------------------


async def _acta_ct_con_firma_protegida(conn, slug, nombre, *, disposition: str, nivel: int = 1):
    created, tid, elabora, recibe = await _acta_recibida(conn, slug, nombre, nivel_seguridad=nivel)
    schema = tenant_schema(slug)
    serie = await ArchiveService(conn, slug).create_trd_serie(
        TrdSerieCreate(
            code=f"IT-{uuid.uuid4().hex[:6]}", name="itest serie", disposition=disposition,
            archivo_gestion_years=2,
            archivo_central_years=3,
        ),
        actor=None,
    )
    # La versión TRD de un expediente transferido es inmutable (ADR-025): el seed ya lo
    # transfirió, así que se desactiva el trigger SOLO en este schema desechable para
    # asignarle la serie CT/no-CT cuyo filtro se prueba.
    await conn.execute(f'ALTER TABLE "{schema}".expedientes DISABLE TRIGGER USER')
    await conn.execute(
        f'UPDATE "{schema}".expedientes SET trd_serie_id = $2 WHERE id = $1', created.id, serie.id
    )
    await conn.execute(f'ALTER TABLE "{schema}".expedientes ENABLE TRIGGER USER')
    svc = TransferenciaService(conn, slug)
    firma = await _firmar(svc, tid, recibe)
    fid = await conn.fetchval(
        f'SELECT id FROM "{schema}".transferencia_acta_firma_personal WHERE transferencia_id = $1 AND rol = $2',
        tid, firma.rol,
    )
    await conn.execute(
        f'UPDATE "{schema}".transferencia_acta_firma_personal_worm '
        "SET worm_protegido_at = now(), worm_retain_until = now() + interval '30 days' "
        "WHERE firma_personal_id = $1", fid,
    )
    return svc, created, tid, fid


async def test_la_renovacion_solo_ve_firmas_de_series_ct_protegidas_y_por_vencer(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    svc, created, tid, fid = await _acta_ct_con_firma_protegida(conn, slug, "itest 07-10 ct", disposition="CT")
    repo = TransferenciaRepository(conn, slug)

    filas = await repo.list_firma_personal_renovacion_pending(ventana_dias=400, ct_codes=["CT"], limit=10)
    assert [f["firma_personal_id"] for f in filas] == [fid]
    assert filas[0]["rol"] == "recibe" and filas[0]["expediente_id"] == created.id
    # fuera de la ventana, de otra disposición, o agotada: no entra
    assert await repo.list_firma_personal_renovacion_pending(ventana_dias=10, ct_codes=["CT"], limit=10) == []
    assert await repo.list_firma_personal_renovacion_pending(ventana_dias=400, ct_codes=["E", "S", "M"], limit=10) == []
    await conn.execute(
        f'UPDATE "{schema}".transferencia_acta_firma_personal_worm SET worm_renovacion_agotado = TRUE'
    )
    assert await repo.list_firma_personal_renovacion_pending(ventana_dias=400, ct_codes=["CT"], limit=10) == []
    # ...pero la agotada SÍ la ve la visibilidad operacional
    vis = await repo.list_firma_personal_renovacion_visibilidad(max_clearance=1, ct_codes=["CT"], ventana_dias=400)
    assert len(vis) == 1 and vis[0]["worm_renovacion_agotado"] is True


async def test_la_visibilidad_de_renovacion_respeta_el_clearance_en_el_where(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    _svc, _created, _tid, _fid = await _acta_ct_con_firma_protegida(
        conn, slug, "itest 07-10 clearance", disposition="CT"
    )
    schema = tenant_schema(slug)
    await conn.execute(f'UPDATE "{schema}".expedientes SET nivel_seguridad = 3')
    repo = TransferenciaRepository(conn, slug)
    assert await repo.list_firma_personal_renovacion_visibilidad(max_clearance=1, ct_codes=["CT"], ventana_dias=400) == []
    assert len(await repo.list_firma_personal_renovacion_visibilidad(max_clearance=3, ct_codes=["CT"], ventana_dias=400)) == 1


async def test_renovar_extiende_marca_el_espejo_asienta_y_nunca_retrocede(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.clients.storage_client import WormRenewalOutcome

    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    svc, created, tid, fid = await _acta_ct_con_firma_protegida(conn, slug, "itest 07-10 renueva", disposition="CT")
    nueva = datetime.now(tz=UTC) + timedelta(days=365 * 5)

    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=True, retain_until=nueva, renovado=True)),
    ) as st:
        res = await svc.renovar_worm_firma_personal(fid, tid, created.id, "recibe", intentos_previos=0)
    assert res == "renovado"
    assert st.await_args.kwargs["tipo"] == "acta_firma_recibe"
    espejo = await conn.fetchval(
        f'SELECT worm_retain_until FROM "{schema}".transferencia_acta_firma_personal_worm WHERE firma_personal_id = $1', fid
    )
    assert abs((espejo - nueva).total_seconds()) < 1
    assert await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = 'archive.acta_firma_personal_worm_retencion_renovada'", slug,
    ) == 1

    # una respuesta tardía con una fecha ANTERIOR no hace retroceder el espejo
    vieja = nueva - timedelta(days=200)
    await TransferenciaRepository(conn, slug).mark_firma_personal_worm_renovado(fid, worm_retain_until=vieja)
    espejo2 = await conn.fetchval(
        f'SELECT worm_retain_until FROM "{schema}".transferencia_acta_firma_personal_worm WHERE firma_personal_id = $1', fid
    )
    assert abs((espejo2 - nueva).total_seconds()) < 1
    await _cadena_integra(conn, slug)


# --- FIRMA-2 (migración 062): cadena de constancias y revocación como prueba ---------------------


async def _proteger_revocaciones(conn: asyncpg.Connection, schema: str, *, solo: str | None = None) -> None:
    """Marca el Object-Lock de las revocaciones como confirmado (lo que hace
    `proteger_worm_revocacion` con storage vivo); `solo` limita a un artefacto."""
    filtro = f" WHERE artefacto = '{solo}'" if solo else ""
    await conn.execute(
        f'UPDATE "{schema}".transferencia_acta_firma_excepcion_revocacion_worm '
        f"SET worm_protegido_at = now(), worm_retain_until = now() + interval '400 days'{filtro}"
    )


async def _insert_v2(conn, schema, tid, *, parte, declarante, ordinal, sustituye_a, rol="recibe"):
    """INSERT crudo de una constancia v2 válida: lo que el motor debe aceptar o rechazar por sí
    solo, sin pasar por el servicio."""
    xml = f"<constancia ordinal='{ordinal}'/>"
    return await conn.fetchval(
        f'INSERT INTO "{schema}".transferencia_acta_firma_excepcion '
        "(transferencia_id, rol, parte_id, motivo, fundamento, referencia_documental, declarada_por, "
        " ordinal, sustituye_a, constancia_version, referencia_radicado_id, referencia_anexo_id, "
        " referencia_radicado_numero, referencia_anexo_sha256, fundamento_sal, "
        " fundamento_compromiso, fundamento_compromiso_algoritmo, declarante_fuente, parte_fuente, "
        " constancia_xml, constancia_sha256, firma_id, minio_key_firmado) "
        "VALUES ($1, $2, $3, 'retiro', $4, 'ref', $5, $6, $7, 3, $8, $9, $10, $11, $12, $15, "
        "        'SHA-256-salted', 'usuario', 'usuario', $13, $14, gen_random_uuid(), 'k/x.xml') "
        "RETURNING id",
        tid, rol, parte, FUNDAMENTO, declarante, ordinal, sustituye_a, RAD, ANEXO, NUMERO_RAD,
        SHA_ANEXO, bytes(32), xml, sha256_hex(xml), "ab" * 32,
    )


async def test_la_cadena_llega_al_ordinal_3_con_la_revocacion_protegida_en_cada_vuelta(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, recibe = await _acta_recibida(conn, slug, "itest FIRMA-2 cadena")
    svc = TransferenciaService(conn, slug)
    admin, otro = Actor(id=uuid.uuid4()), Actor(id=uuid.uuid4())
    repo = TransferenciaRepository(conn, slug)

    c1 = await _declarar(svc, tid, admin)
    assert c1.ordinal == 1 and c1.sustituye_a is None
    assert (await repo.get_excepcion_vigente(tid, rol="recibe"))["id"] == c1.id
    r1 = await _revocar(svc, tid, otro, _rev())
    assert r1.revocada and r1.revocacion_firmada and r1.revocacion_id
    # revocada: ya no hay vigente, pero sí último eslabón
    assert await repo.get_excepcion_vigente(tid, rol="recibe") is None
    assert (await repo.get_ultima_excepcion(tid, rol="recibe"))["id"] == c1.id

    # revocación sellada pero SIN Object-Lock confirmado: el servicio y el motor se niegan
    with pytest.raises(HTTPException) as e0:
        await _declarar(svc, tid, admin)
    assert e0.value.status_code == 409 and e0.value.detail["error"] == "revocacion_sin_proteger"

    await _proteger_revocaciones(conn, schema)
    c2 = await _declarar(svc, tid, admin)
    assert c2.ordinal == 2 and c2.sustituye_a == c1.id
    # sobre la vigente no se declara otra
    with pytest.raises(HTTPException) as e1:
        await _declarar(svc, tid, admin)
    assert e1.value.status_code == 409 and e1.value.detail["error"] == "excepcion_vigente"

    await _revocar(svc, tid, otro, _rev())
    await _proteger_revocaciones(conn, schema)
    c3 = await _declarar(svc, tid, admin)
    assert (c3.ordinal, c3.sustituye_a) == (3, c2.id)

    filas = await conn.fetch(
        f'SELECT ordinal, sustituye_a, id FROM "{schema}".transferencia_acta_firma_excepcion '
        "WHERE transferencia_id = $1 ORDER BY ordinal", tid,
    )
    assert [f["ordinal"] for f in filas] == [1, 2, 3]
    assert [f["sustituye_a"] for f in filas] == [None, c1.id, c2.id]
    # la cadena lineal la sostiene el motor: ni el mismo ordinal ni la misma sustituida dos veces
    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_v2(conn, schema, tid, parte=recibe.id, declarante=admin.id, ordinal=3, sustituye_a=c2.id)
    # el verificador recorre las TRES constancias y las DOS revocaciones
    with _verificacion():
        v = await svc.acta_verificacion(tid, None)
    assert [(e.ordinal, e.revocada) for e in v.excepciones] == [(1, True), (2, True), (3, False)]
    assert [e.revocacion_sello_valida for e in v.excepciones] == [True, True, None]
    assert v.completitud == "sellada"  # solo `recibe` tiene vigente; `entrega` sin cubrir
    # una asiento por acto, atómico; y la cadena de auditoría sigue íntegra
    assert await _n_asientos(conn, slug, "archive.acta_firma_inobtenible_declarada") == 3
    assert await _n_asientos(conn, slug, "archive.acta_firma_inobtenible_revocada") == 2
    await _cadena_integra(conn, slug)


async def test_el_motor_exige_la_revocacion_sellada_y_protegida_para_encadenar(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El trigger lo repite aunque el servicio se equivoque: sin sello, sin Object-Lock del sello
    o sin el del artefacto personal, un INSERT crudo de ordinal 2 es rechazado con el nombre
    estable `ck_firma_excepcion_revocacion_sin_proteger`."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, recibe = await _acta_recibida(conn, slug, "itest FIRMA-2 motor cadena")
    svc = TransferenciaService(conn, slug)
    admin, otro = Actor(id=uuid.uuid4()), Actor(id=uuid.uuid4())
    c1 = await _declarar(svc, tid, admin)
    # una constancia ordinal 2 sin haber revocado
    with pytest.raises(asyncpg.CheckViolationError) as e_a:
        await _insert_v2(conn, schema, tid, parte=recibe.id, declarante=admin.id, ordinal=2, sustituye_a=c1.id)
    assert e_a.value.constraint_name == "ck_firma_excepcion_revocacion_sin_proteger"

    with _externos():
        await _revocar(svc, tid, otro, _rev(totp_code="123456"), "Bearer x")
    assert await conn.fetchval(
        f'SELECT array_agg(artefacto ORDER BY artefacto) FROM "{schema}".transferencia_acta_firma_excepcion_revocacion_worm'
    ) == ["personal", "sello", "sobre"]
    # revocada, con firma personal, sin NINGÚN Object-Lock confirmado
    for solo in (None, "sello", "personal"):
        if solo == "sello":
            await _proteger_revocaciones(conn, schema, solo="sello")
        with pytest.raises(asyncpg.CheckViolationError) as e_b:
            await _insert_v2(conn, schema, tid, parte=recibe.id, declarante=admin.id, ordinal=2, sustituye_a=c1.id)
        assert e_b.value.constraint_name == "ck_firma_excepcion_revocacion_sin_proteger"
    # sello y personal protegidos pero el SOBRE de la revocación no: el motor tampoco la admite
    await _proteger_revocaciones(conn, schema, solo="personal")
    with pytest.raises(asyncpg.CheckViolationError) as e_s:
        await _insert_v2(conn, schema, tid, parte=recibe.id, declarante=admin.id, ordinal=2, sustituye_a=c1.id)
    assert e_s.value.constraint_name == "ck_firma_excepcion_revocacion_sin_proteger"
    # con los TRES artefactos protegidos, el motor la admite
    await _proteger_revocaciones(conn, schema, solo="sobre")
    assert await _insert_v2(
        conn, schema, tid, parte=recibe.id, declarante=admin.id, ordinal=2, sustituye_a=c1.id
    )
    # y una sustituida que no es la del rol/ordinal correcto no continúa la cadena
    with pytest.raises(asyncpg.CheckViolationError) as e_c:
        await _insert_v2(conn, schema, tid, parte=recibe.id, declarante=admin.id, ordinal=5, sustituye_a=c1.id)
    assert e_c.value.constraint_name in (
        "ck_firma_excepcion_cadena_invalida", "ck_firma_excepcion_revocacion_sin_proteger"
    )


async def test_una_revocacion_legada_sin_sello_no_admite_redeclaracion(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, recibe = await _acta_recibida(conn, slug, "itest FIRMA-2 legada")
    admin = Actor(id=uuid.uuid4())
    eid = await conn.fetchval(
        _insert_sql(schema) + "VALUES ($1, 'recibe', $2, 'retiro', $3, 'ref', $4) RETURNING id",
        tid, recibe.id, FUNDAMENTO, admin.id,
    )
    # revocación al estilo 061: sin XML ni sello
    await conn.execute(
        f'INSERT INTO "{schema}".transferencia_acta_firma_excepcion_revocacion '
        "(excepcion_id, revocada_por, fundamento) VALUES ($1, $2, $3)", eid, uuid.uuid4(), FUNDAMENTO_REV,
    )
    with pytest.raises(HTTPException) as exc:
        await _declarar(TransferenciaService(conn, slug), tid, admin)
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "revocacion_sin_proteger"


async def test_si_la_parte_firmo_en_la_ventana_revocada_la_cadena_queda_cerrada_en_servicio_y_motor(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, recibe = await _acta_recibida(conn, slug, "itest FIRMA-2 ventana")
    svc = TransferenciaService(conn, slug)
    admin, otro = Actor(id=uuid.uuid4()), Actor(id=uuid.uuid4())
    c1 = await _declarar(svc, tid, admin)
    await _revocar(svc, tid, otro, _rev())
    await _proteger_revocaciones(conn, schema)
    await _firmar(svc, tid, recibe)  # la parte firma durante la ventana revocada

    with pytest.raises(HTTPException) as exc:
        await _declarar(svc, tid, admin)
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "parte_ya_firmo"
    with pytest.raises(asyncpg.CheckViolationError) as e_db:
        await _insert_v2(conn, schema, tid, parte=recibe.id, declarante=admin.id, ordinal=2, sustituye_a=c1.id)
    assert e_db.value.constraint_name == "ck_firma_excepcion_parte_ya_firmo"


async def test_sin_sello_la_declaracion_y_la_revocacion_dejan_cero_filas_y_cero_asientos(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, _r = await _acta_recibida(conn, slug, "itest FIRMA-2 503")
    svc = TransferenciaService(conn, slug)
    caido = SignOutcome(signed=None, transient=True)
    admin, otro = Actor(id=uuid.uuid4()), Actor(id=uuid.uuid4())
    with _externos(), patch("app.clients.signature_client.sign_xml_outcome", new=AsyncMock(return_value=caido)):
        with pytest.raises(HTTPException) as e1:
            await svc.declarar_firma_inobtenible(tid, admin, _req())
    assert e1.value.status_code == 503
    assert await conn.fetchval(f'SELECT count(*) FROM "{schema}".transferencia_acta_firma_excepcion') == 0
    assert await _n_asientos(conn, slug, "archive.acta_firma_inobtenible_declarada") == 0

    await _declarar(svc, tid, admin)
    with _externos(), patch("app.clients.signature_client.sign_xml_outcome", new=AsyncMock(return_value=caido)):
        with pytest.raises(HTTPException) as e2:
            await svc.revocar_firma_inobtenible(tid, otro, _rev())
    assert e2.value.status_code == 503
    assert await conn.fetchval(f'SELECT count(*) FROM "{schema}".transferencia_acta_firma_excepcion_revocacion') == 0
    assert await conn.fetchval(f'SELECT count(*) FROM "{schema}".transferencia_acta_firma_excepcion_revocacion_worm') == 0
    assert await _n_asientos(conn, slug, "archive.acta_firma_inobtenible_revocada") == 0
    await _cadena_integra(conn, slug)


async def test_el_object_lock_de_la_revocacion_se_confirma_y_destraba_la_cadena_de_verdad(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Extremo a extremo con el método REAL `proteger_worm_revocacion` (storage simulado): el
    tipo es de revocación, el `file_id` sale del XML de la revocación, la marca queda en SU tabla
    y solo entonces el motor admite la nueva constancia."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    svc, _created, tid, c1 = await _serie_y_constancia(conn, slug, "itest FIRMA-2 worm rev", disposition="CT")
    otro = Actor(id=uuid.uuid4())
    with _externos():
        rev = await _revocar(svc, tid, otro, _rev())
        with pytest.raises(HTTPException):
            await svc.declarar_firma_inobtenible(tid, Actor(id=uuid.uuid4()), _req())
    rid = rev.revocacion_id
    repo = TransferenciaRepository(conn, slug)
    assert sorted(p["artefacto"] for p in await repo.list_firma_excepcion_worm_pending(10, acto="revocacion")) == ["sello", "sobre"]
    # la constancia NO figura como pendiente por la revocación (tablas distintas)
    retain = datetime.now(tz=UTC) + timedelta(days=365 * 7)
    salida = WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4(), retain_until=retain)
    with patch(
        "app.clients.storage_client.proteger_artefacto_worm_internal", new=AsyncMock(return_value=salida)
    ) as st:
        res = await svc.proteger_worm_revocacion(rid, "sello", actor_id=None, origen="reconciliation")
    assert res == "protegido"
    llave = await conn.fetchval(
        f'SELECT minio_key_firmado FROM "{schema}".transferencia_acta_firma_excepcion_revocacion WHERE id = $1', rid
    )
    assert st.await_args.kwargs["tipo"] == "acta_firma_revocacion_recibe"
    assert st.await_args.kwargs["file_id"] == llave.split("/", 1)[0]
    assert await _n_asientos(conn, slug, "archive.acta_firma_revocacion_worm_protegido") == 1
    # el sobre de la revocación sigue pendiente y SIN él el motor no admite la nueva constancia
    assert [p["artefacto"] for p in await repo.list_firma_excepcion_worm_pending(10, acto="revocacion")] == ["sobre"]
    with _externos(), pytest.raises(HTTPException) as sin_sobre:
        await svc.declarar_firma_inobtenible(tid, Actor(id=uuid.uuid4()), _req())
    assert sin_sobre.value.detail["error"] == "revocacion_sin_proteger"
    with patch(
        "app.clients.storage_client.proteger_artefacto_worm_internal", new=AsyncMock(return_value=salida)
    ) as st_sobre:
        assert await svc.proteger_worm_revocacion(rid, "sobre", actor_id=None, origen="reconciliation") == "protegido"
    assert st_sobre.await_args.kwargs["tipo"] == "acta_firma_revocacion_sobre_recibe"
    assert await repo.list_firma_excepcion_worm_pending(10, acto="revocacion") == []

    with _externos():
        c2 = await svc.declarar_firma_inobtenible(tid, Actor(id=uuid.uuid4()), _req())
    assert c2.ordinal == 2 and c2.sustituye_a == c1
    await _cadena_integra(conn, slug)


async def test_la_identidad_sale_del_catalogo_y_el_certificado_activo_obliga_a_firmar(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _elabora, recibe = await _acta_recibida(conn, slug, "itest FIRMA-2 identidad")
    admin = Actor(id=uuid.uuid4(), username="adm")
    await conn.execute(
        f'CREATE TABLE "{schema}".auth_users (id UUID PRIMARY KEY, username TEXT, is_root BOOLEAN DEFAULT FALSE)'
    )
    await conn.execute(
        f'CREATE TABLE "{schema}".auth_user_signing_key (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), '
        "user_id UUID, subject_dn TEXT, cargo TEXT, estado TEXT, cert_pem TEXT, not_after TIMESTAMPTZ)"
    )
    await conn.execute(
        f'INSERT INTO "{schema}".auth_users (id, username) VALUES ($1, \'adm\'), ($2, \'recibe.user\')',
        admin.id, recibe.id,
    )
    await conn.execute(
        f'INSERT INTO "{schema}".auth_user_signing_key (user_id, subject_dn, cargo, estado, cert_pem, not_after) '
        "VALUES ($1, 'CN=Ana Admin,O=Entidad,C=CO', 'Jefe de archivo', 'activo', 'PEM', now() + interval '1 year')",
        admin.id,
    )
    svc = TransferenciaService(conn, slug)
    with pytest.raises(HTTPException) as exc:
        await _declarar(svc, tid, admin)  # tiene certificado activo y no mandó TOTP
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "firma_personal_declarante_requerida"
    assert await conn.fetchval(f'SELECT count(*) FROM "{schema}".transferencia_acta_firma_excepcion') == 0

    with _externos():
        resp = await svc.declarar_firma_inobtenible(tid, admin, _req(totp_code="123456"), "Bearer x")
    assert resp.firma_personal and resp.firma_personal_requerida
    fila = await conn.fetchrow(f'SELECT * FROM "{schema}".transferencia_acta_firma_excepcion')
    assert (fila["declarante_nombre"], fila["declarante_cargo"], fila["declarante_fuente"]) == (
        "Ana Admin", "Jefe de archivo", "certificado",
    )
    assert (fila["parte_nombre"], fila["parte_fuente"]) == ("recibe.user", "usuario")
    assert "Ana Admin" in fila["constancia_xml"] and "recibe.user" in fila["constancia_xml"]


async def test_el_motor_exige_la_v2_completa_y_la_coherencia_del_ordinal(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, recibe = await _acta_recibida(conn, slug, "itest FIRMA-2 checks")
    admin = Actor(id=uuid.uuid4())
    base = dict(
        constancia_version=3, referencia_anexo_id=uuid.uuid4(), referencia_radicado_numero="n",
        referencia_anexo_sha256="s", fundamento_sal=bytes(32), fundamento_compromiso="h",
        fundamento_compromiso_algoritmo="SHA-256-salted", declarante_fuente="usuario",
        parte_fuente="usuario",
        constancia_xml="<x/>", constancia_sha256="a", firma_id=uuid.uuid4(), minio_key_firmado="k/x",
    )

    async def _crudo(rol: str, **valores) -> None:
        cols = ["transferencia_id", "rol", "parte_id", "motivo", "fundamento",
                "referencia_documental", "declarada_por", *valores]
        marcas = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        await conn.execute(
            f'INSERT INTO "{schema}".transferencia_acta_firma_excepcion ({", ".join(cols)}) '
            f"VALUES ({marcas})",
            tid, rol, recibe.id, "retiro", FUNDAMENTO, "ref", admin.id, *valores.values(),
        )

    # la v3 completa entra...
    await _crudo("entrega", **base)
    # ...pero sin anexo / sal / compromiso / huella del anexo / sello, el CHECK la rechaza
    for faltante in (
        "referencia_anexo_id", "fundamento_sal", "fundamento_compromiso",
        "referencia_anexo_sha256", "firma_id",
    ):
        with pytest.raises(asyncpg.CheckViolationError):
            await _crudo("elabora", **{**base, faltante: None})
    # la sal mide exactamente 32 bytes y el algoritmo es el declarado
    with pytest.raises(asyncpg.CheckViolationError):
        await _crudo("elabora", **{**base, "fundamento_sal": bytes(16)})
    with pytest.raises(asyncpg.CheckViolationError):
        await _crudo("elabora", **{**base, "fundamento_compromiso_algoritmo": "SHA-256"})
    # una v2 LEGADA (HMAC de plataforma, 062) sigue siendo válida: se conserva, no se reescribe
    await _crudo(
        "elabora",
        **{
            **base, "constancia_version": 2, "fundamento_sal": None, "fundamento_compromiso": None,
            "fundamento_compromiso_algoritmo": None, "fundamento_hmac": "h",
            "fundamento_hmac_key_version": 1, "fundamento_hmac_algoritmo": "HMAC-SHA256",
        },
    )

    # una re-declaración (ordinal > 1) solo existe en v2 y con su sustituida
    eid = await conn.fetchval(
        _insert_sql(schema) + "VALUES ($1, 'recibe', $2, 'retiro', $3, 'ref', $4) RETURNING id",
        tid, recibe.id, FUNDAMENTO, admin.id,
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await _crudo("recibe", ordinal=2, sustituye_a=eid)  # ordinal 2 sobre una v1
    with pytest.raises(asyncpg.CheckViolationError):
        await _crudo("elabora", ordinal=2)  # ordinal 2 sin sustituida
    with pytest.raises(asyncpg.CheckViolationError):
        await _crudo("elabora", ordinal=1, sustituye_a=eid)  # ordinal 1 con sustituida


async def test_verificar_fundamento_recalcula_en_local_y_coteja_columna_xml_y_asiento(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, recibe = await _acta_recibida(conn, slug, "itest FIRMA-2 verificar fundamento")
    svc = TransferenciaService(conn, slug)
    admin, otro = Actor(id=uuid.uuid4()), Actor(id=uuid.uuid4())
    c1 = await _declarar(svc, tid, admin)

    ok = await _verificar(svc, tid, c1.id, admin)  # sin signature-service: es local
    assert ok.verificable and ok.coincide is True and ok.algoritmo == "SHA-256-salted"
    assert (ok.columna, ok.xml, ok.asiento) == (True, True, True)
    assert ok.revocacion_columna is None  # aún no se revocó
    assert "defuncion" not in ok.model_dump_json()  # no devuelve el texto
    sal = bytes(await conn.fetchval(
        f'SELECT fundamento_sal FROM "{schema}".transferencia_acta_firma_excepcion WHERE id = $1', c1.id
    ))
    assert sal.hex() not in ok.model_dump_json()  # ni la sal

    # S5: leer el fundamento para recalcularlo deja asiento de lectura, sin el texto ni la sal
    assert await _n_asientos(conn, slug, "transferencia.acta_fundamento_leido") == 1
    lectura = await conn.fetchval(
        "SELECT payload FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = 'transferencia.acta_fundamento_leido'", slug,
    )
    assert "defuncion" not in lectura and sal.hex() not in lectura
    assert json.loads(lectura)["via"] == "fundamento_verificacion"

    # tras revocar, la revocación se coteja también contra sus tres copias
    await _revocar(svc, tid, otro, _rev())
    con_rev = await _verificar(svc, tid, c1.id, admin)
    assert con_rev.coincide is True
    assert (con_rev.revocacion_columna, con_rev.revocacion_xml, con_rev.revocacion_asiento) == (True, True, True)

    # un compromiso que NO es el del texto guardado (otra sal, o texto cambiado) no coincide y
    # las copias que no existen (xml/asiento) nunca cuentan como coincidencia
    malo = await _insert_v2(
        conn, schema, tid, parte=recibe.id, declarante=admin.id, ordinal=1, sustituye_a=None, rol="entrega"
    )
    mal = await _verificar(svc, tid, malo, admin)
    assert mal.coincide is False and mal.columna is False and mal.xml is None and mal.asiento is None

    # una constancia v1 (anterior a FIRMA-2) no tiene compromiso: se declara, no se inventa
    v1 = await conn.fetchval(
        _insert_sql(schema) + "VALUES ($1, 'elabora', $2, 'retiro', $3, 'ref', $4) RETURNING id",
        tid, recibe.id, FUNDAMENTO, admin.id,
    )
    legada = await _verificar(svc, tid, v1, admin)
    assert legada.verificable is False and legada.motivo == "constancia_sin_compromiso_salado"
    with pytest.raises(HTTPException) as e404:
        await _verificar(svc, tid, uuid.uuid4(), admin)
    assert e404.value.status_code == 404


# --- Ronda de auditoría FIRMA-2 (migración 063, A3/A4) ---------------------------------------------


async def test_reintentar_el_worm_repone_de_agotado_con_asiento_y_dice_el_estado_real(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """A4 contra Postgres real: `agotado` no tenía salida. El reintento repone el artefacto a
    pendiente, deja su asiento en la misma tx, reintenta la protección y devuelve el estado REAL."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    svc, _created, tid, eid = await _serie_y_constancia(conn, slug, "itest A4 reintento", disposition="CT")
    repo = TransferenciaRepository(conn, slug)
    await conn.execute(
        f'UPDATE "{schema}".transferencia_acta_firma_excepcion_worm SET worm_reconcile_agotado = TRUE, '
        "worm_reconcile_intentos = 5, worm_reconcile_ultimo_error = 'worm_disabled' WHERE excepcion_id = $1",
        eid,
    )
    assert await repo.list_firma_excepcion_worm_pending(10) == []  # agotado: la barrida ya no lo recoge
    admin = Actor(id=uuid.uuid4(), username="itest-admin")

    # el almacén sigue sin responder (p. ej. preservación apagada): vuelve a PENDIENTE, no agotado
    with patch(
        "app.clients.storage_client.proteger_artefacto_worm",
        new=AsyncMock(return_value=WormProtectionOutcome(ok=False, disabled=True)),
    ):
        primero = await svc.reintentar_worm_firma_excepcion(tid, eid, admin, acto="excepcion")
    assert primero.repuestos == 2 and primero.artefactos == {"sello": "pendiente", "sobre": "pendiente"}
    estado = await conn.fetchrow(
        f'SELECT worm_reconcile_agotado, worm_reconcile_intentos FROM "{schema}".'
        "transferencia_acta_firma_excepcion_worm WHERE excepcion_id = $1", eid,
    )
    assert estado["worm_reconcile_agotado"] is False and estado["worm_reconcile_intentos"] == 1
    assert await _n_asientos(conn, slug, "archive.acta_firma_excepcion_worm_reintento_manual") == 1

    # con el almacén de vuelta, el reintento protege
    salida = WormProtectionOutcome(
        ok=True, worm_object_id=uuid.uuid4(), retain_until=datetime.now(tz=UTC) + timedelta(days=365 * 7)
    )
    with patch("app.clients.storage_client.proteger_artefacto_worm", new=AsyncMock(return_value=salida)):
        segundo = await svc.reintentar_worm_firma_excepcion(tid, eid, admin, acto="excepcion")
    assert segundo.repuestos == 0 and segundo.artefactos == {"sello": "protegido", "sobre": "protegido"}
    assert await _n_asientos(conn, slug, "archive.acta_firma_excepcion_worm_reintento_manual") == 2

    # la revocación aún no existe: 404 neutro, y un id ajeno también
    with pytest.raises(HTTPException) as e1:
        await svc.reintentar_worm_firma_excepcion(tid, eid, admin, acto="revocacion")
    assert e1.value.status_code == 404 and e1.value.detail["error"] == "revocacion_not_found"
    with pytest.raises(HTTPException) as e2:
        await svc.reintentar_worm_firma_excepcion(tid, uuid.uuid4(), admin, acto="excepcion")
    assert e2.value.status_code == 404
    await _cadena_integra(conn, slug)


async def test_proteger_la_revocacion_alinea_la_retencion_de_la_constancia_anterior(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """A3 contra Postgres real: todos los eslabones de la cadena llegan hasta la misma ancla. Al
    protegerse la revocación, la constancia (protegida antes, con retención más corta) se renueva,
    su espejo local sube (monotónico) y la extensión deja asiento."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    svc, _created, tid, c1 = await _serie_y_constancia(conn, slug, "itest A3 ancla", disposition="CT")
    corta = datetime.now(tz=UTC) + timedelta(days=365 * 7)
    larga = corta + timedelta(days=200)
    with patch(
        "app.clients.storage_client.proteger_artefacto_worm_internal",
        new=AsyncMock(return_value=WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4(), retain_until=corta)),
    ):
        assert await svc.proteger_worm_firma_excepcion(c1, "sello", actor_id=None, origen="reconciliation") == "protegido"
    rev = await _revocar(svc, tid, Actor(id=uuid.uuid4()), _rev())
    llave = await conn.fetchval(
        f'SELECT minio_key_firmado FROM "{schema}".transferencia_acta_firma_excepcion WHERE id = $1', c1
    )

    with (
        patch(
            "app.clients.storage_client.proteger_artefacto_worm_internal",
            new=AsyncMock(return_value=WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4(), retain_until=larga)),
        ),
        patch(
            "app.clients.storage_client.renovar_retencion_worm_internal",
            new=AsyncMock(return_value=WormRenewalOutcome(ok=True, retain_until=larga, renovado=True)),
        ) as renovar,
    ):
        assert await svc.proteger_worm_revocacion(
            rev.revocacion_id, "sello", actor_id=None, origen="reconciliation"
        ) == "protegido"

    renovar.assert_awaited_once()  # la revocación recién protegida NO se renueva a sí misma
    kw = renovar.await_args.kwargs
    assert kw["tipo"] == "acta_firma_excepcion_recibe" and kw["file_id"] == llave.split("/", 1)[0]
    espejo = await conn.fetchval(
        f'SELECT worm_retain_until FROM "{schema}".transferencia_acta_firma_excepcion_worm '
        "WHERE excepcion_id = $1 AND artefacto = 'sello'", c1,
    )
    assert espejo == larga  # subió hasta la misma ancla
    assert await _n_asientos(conn, slug, "archive.acta_firma_excepcion_worm_retencion_renovada") == 1
    await _cadena_integra(conn, slug)


# --- Ronda 3 FIRMA-2 (migración 064): sobre de revelación del fundamento -------------------------


async def test_el_sobre_se_sella_se_guarda_aparte_y_solo_se_lee_mediado_con_asiento(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    from app.services.constancia_firma_excepcion import leer_sobre

    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, _r = await _acta_recibida(conn, slug, "itest FIRMA-2 sobre")
    svc = TransferenciaService(conn, slug)
    admin, otro = Actor(id=uuid.uuid4()), Actor(id=uuid.uuid4())
    c1 = await _declarar(svc, tid, admin)

    fila = await conn.fetchrow(
        "SELECT sobre_firma_id, sobre_minio_key, sobre_sha256, sobre_xades_level, minio_key_firmado, "
        f'constancia_xml, fundamento_sal FROM "{schema}".transferencia_acta_firma_excepcion WHERE id = $1',
        c1.id,
    )
    assert fila["sobre_firma_id"] and fila["sobre_xades_level"] == "XAdES-T"
    assert fila["sobre_minio_key"] and fila["sobre_minio_key"] != fila["minio_key_firmado"]  # objeto propio
    xml = _ALMACEN[fila["sobre_minio_key"]]
    assert fila["sobre_sha256"] == sha256_c14n(xml)
    campos = leer_sobre(xml)
    sal = bytes(fila["fundamento_sal"])
    assert campos["texto"] == FUNDAMENTO and campos["sal_hex"] == sal.hex()
    assert campos["compromiso"] == hashlib.sha256(sal + FUNDAMENTO.encode()).hexdigest()
    assert campos["acto"] == "constancia" and campos["id"] == str(c1.id)
    # el texto y la sal viven SOLO en el sobre (y en las columnas): jamás en la constancia
    assert "defuncion" not in fila["constancia_xml"] and sal.hex() not in fila["constancia_xml"]
    assert c1.sobre is True and c1.sobre_sha256 == fila["sobre_sha256"]
    # el motor solo admite los tres artefactos conocidos
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            f'INSERT INTO "{schema}".transferencia_acta_firma_excepcion_worm (excepcion_id, artefacto) '
            "VALUES ($1, 'otro')", c1.id,
        )

    # lectura mediada: el asiento de lectura queda ANTES de servirlo, con via=sobre, sin texto ni sal
    assert await _n_asientos(conn, slug, "transferencia.acta_fundamento_leido") == 0
    with _externos():
        leido = await svc.leer_sobre_fundamento(tid, c1.id, admin, acto="constancia")
    assert leido.xml_firmado == xml and leido.sobre_sha256 == fila["sobre_sha256"]
    assert await _n_asientos(conn, slug, "transferencia.acta_fundamento_leido") == 1
    payload = json.loads(await conn.fetchval(
        "SELECT payload FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = 'transferencia.acta_fundamento_leido'", slug,
    ))
    assert payload["via"] == "sobre" and payload["acto"] == "constancia"
    assert "defuncion" not in json.dumps(payload) and sal.hex() not in json.dumps(payload)
    # una revocación inexistente no tiene sobre: 404 sin asiento nuevo
    with _externos(), pytest.raises(HTTPException) as e404:
        await svc.leer_sobre_fundamento(tid, c1.id, admin, acto="revocacion")
    assert e404.value.status_code == 404
    assert await _n_asientos(conn, slug, "transferencia.acta_fundamento_leido") == 1

    # revocar: sobre propio de la revocación; la respuesta NO devuelve el fundamento ajeno
    rev = await _revocar(svc, tid, otro, _rev())
    assert rev.fundamento is None and rev.fundamento_sal is None
    assert rev.revocacion_sobre is True and rev.revocacion_fundamento == FUNDAMENTO_REV
    rfila = await conn.fetchrow(
        f'SELECT sobre_minio_key, sobre_sha256, fundamento_sal FROM "{schema}".'
        "transferencia_acta_firma_excepcion_revocacion WHERE id = $1", rev.revocacion_id,
    )
    rcampos = leer_sobre(_ALMACEN[rfila["sobre_minio_key"]])
    assert rcampos["acto"] == "revocacion" and rcampos["texto"] == FUNDAMENTO_REV
    assert rcampos["id"] == str(rev.revocacion_id) and rcampos["sal_hex"] == bytes(rfila["fundamento_sal"]).hex()
    with _externos():
        rleido = await svc.leer_sobre_fundamento(tid, c1.id, admin, acto="revocacion")
    assert leido.xml_firmado != rleido.xml_firmado
    await _cadena_integra(conn, slug)


async def test_el_barrido_recoge_solo_los_sobres_sin_referencia_y_vencida_la_gracia(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """SQL real del barrido: un fichero con nombre de sobre que NINGUNA fila referencia y más
    viejo que la gracia es candidato; el referenciado, el reciente y el de otro nombre no."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    _c, tid, _e, _r = await _acta_recibida(conn, slug, "itest FIRMA-2 barrido sobres")
    svc = TransferenciaService(conn, slug)
    c1 = await _declarar(svc, tid, Actor(id=uuid.uuid4()))
    ref = await conn.fetchval(
        f'SELECT sobre_minio_key FROM "{schema}".transferencia_acta_firma_excepcion WHERE id = $1', c1.id
    )
    # `files` es de storage-service: aquí solo las columnas que lee el barrido
    await conn.execute(
        f'CREATE TABLE "{schema}".files (id UUID PRIMARY KEY, filename TEXT, uploaded_at TIMESTAMPTZ)'
    )
    huerfano, reciente, otro = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    nombre = lambda: f"acta-transferencia-sobre-{uuid.uuid4()}-v1-{uuid.uuid4()}-firmado.xml"  # noqa: E731
    for fid, fname, edad in (
        (uuid.UUID(ref.split("/", 1)[0]), nombre(), timedelta(hours=3)),  # referenciado por la constancia
        (huerfano, nombre(), timedelta(hours=3)),
        (reciente, nombre(), timedelta(minutes=5)),  # dentro de la gracia
        (otro, f"acta-transferencia-{uuid.uuid4()}-v1-{uuid.uuid4()}-firmado.xml", timedelta(hours=3)),
    ):
        await conn.execute(
            f'INSERT INTO "{schema}".files VALUES ($1, $2, now() - $3::interval)', fid, fname, edad
        )
    with patch(
        "app.clients.storage_client.purgar_huerfanos_internal",
        new=AsyncMock(return_value=[{"file_id": str(huerfano), "resultado": "purgado"}]),
    ) as purga:
        res = await svc.purgar_sobres_huerfanos()
    assert res == {"candidatos": 1, "purgados": 1, "fallidos": 0}
    assert purga.await_args.kwargs["file_ids"] == [huerfano]
