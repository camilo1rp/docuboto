# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 §8 — el JOB de renovación del ArchiveTimeStamp contra Postgres REAL.

Storage (descarga, subida, herencia WORM) va con dobles; la TSA es la local de pruebas. Lo que se
ejercita de verdad: la tabla `firma_sello_archivo`, la selección de candidatas, la tx del eslabón +
asiento, el reintento del paso de storage y las salidas «no se renueva».

Mutaciones: quitar la verificación previa (`verify_xades`) del job (cae
`test_no_renueva_una_firma_que_no_verifica`); quitar la comparación de plazo de la TSA (cae
`test_no_renueva_con_una_tsa_sin_mas_plazo`); reintentar TODO en la vuelta siguiente en vez de solo el
paso de storage (cae `test_pendiente_worm_reintenta_solo_storage`: sube dos veces)."""

from __future__ import annotations

import hashlib
import uuid
from base64 import b64decode
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from lxml import etree

from app.clients import preservacion_client, storage_client
from app.core import tsa
from app.core.config import settings
from app.core.database import _schema as tenant_schema
from app.jobs import renovacion_lta
from app.providers.base import SignerRef
from app.providers.local import verify_xades
from app.repositories.firma_repository import FirmaRepository
from tests.test_xmldsig_conformidad import (  # noqa: F401  (_tsa_local es autouse)
    _cert,
    _firmar_lta,
    _pem_cert,
    _pem_key,
    _tsa_local,
)

pytestmark = pytest.mark.asyncio


async def _sembrar(conn, slug, monkeypatch, *, xml_alterado=None):
    """Firma LTA real + su fila `firma` + el eslabón 0. Devuelve (doc, firma_id, minio_key, xml)."""
    doc, _cert_sello, _ca = await _firmar_lta(monkeypatch)
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    firma_id = uuid.uuid4()
    minio_key = f"{uuid.uuid4()}/indice-X-v1-{firma_id}-firmado.xml"
    xml = xml_alterado or doc.signed_xml
    xml_sha = hashlib.sha256(xml).hexdigest()
    ex = doc.extra
    await FirmaRepository(conn).insert(
        {
            "id": firma_id, "objeto_tipo": "indice", "objeto_id": "X", "formato_solicitado": "XAdES",
            "firmante_id": uuid.uuid4(), "payload_sha256": doc.payload_sha256,
            "firma_blob": doc.signature_value_b64, "provider": doc.provider,
            "sello_tiempo": datetime.now(UTC), "certificado_pem": doc.certificado_pem,
            "minio_key_firmado": minio_key, "nivel_conformidad": doc.nivel_conformidad,
            "xades_level": doc.xades_level, "tipo_firma": "sello_sistema",
            "certificado_fingerprint": doc.certificado_fingerprint, "signed_xml_sha256": xml_sha,
            "tsa_token": ex.get("tsa_token"),
            "tsa_timestamp": datetime.fromisoformat(ex["tsa_timestamp"]),
            "tsa_cert_fingerprint": ex["tsa_cert_fingerprint"], "tsa_acreditado": False,
            "lt_material_present": True, "ca_fingerprint": ex["ca_fingerprint"],
            "crl_fingerprint": ex["crl_fingerprint"],
            "archive_timestamp_present": True,
            "archive_timestamp_at": datetime.fromisoformat(ex["archive_timestamp_at"]),
            "archive_tsa_cert_fingerprint": ex["archive_tsa_cert_fingerprint"],
            "archive_timestamp_token": ex["archive_timestamp_token"],
            "revocation_provenance": "dev",
        }
    )
    cert_tsa = tsa.certificados_de_token(b64decode(ex["archive_timestamp_token"]))[0]
    await FirmaRepository(conn).insert_sello_archivo(
        firma_id=firma_id, ordinal=0, minio_key=minio_key, xml_sha256=xml_sha,
        gen_time=datetime.fromisoformat(ex["archive_timestamp_at"]),
        tsa_cert_fingerprint=ex["archive_tsa_cert_fingerprint"],
        tsa_cert_not_after=cert_tsa.not_valid_after_utc,
        tsa_cadena_not_after=None,
        tsa_revocacion_fuente="autofirmada",
        revocacion_tsa_previa=None,
    )
    return doc, firma_id, minio_key, xml


def _tsa_larga(monkeypatch) -> None:
    """Una TSA nueva con certificado a 5 años: da más plazo que la de pruebas (365 d)."""
    key, cert = _cert("tsa-larga", tsa_eku=True)
    from datetime import timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    larga = (
        x509.CertificateBuilder()
        .subject_name(cert.subject).issuer_name(cert.issuer).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=5 * 365))
        .add_extension(cert.extensions[0].value, critical=True)
        .sign(key, hashes.SHA256())
    )
    monkeypatch.setattr(
        "app.core.tsa.load_tsa_seal",
        lambda: SignerRef(cert_pem=_pem_cert(larga), key_pem=_pem_key(key), key_password=None),
    )


@pytest.fixture
def storage(monkeypatch):
    """Dobles de storage: descarga lo que el test fije, sube con una clave nueva."""
    est = type("E", (), {})()
    est.descargar = AsyncMock()
    est.subir = AsyncMock(side_effect=lambda **kw: f"{uuid.uuid4()}/{kw['filename']}")
    est.renovar = AsyncMock(return_value={"protegido": True})
    monkeypatch.setattr(preservacion_client, "descargar_xml", est.descargar)
    monkeypatch.setattr(storage_client, "upload_signed_xml", est.subir)
    monkeypatch.setattr(preservacion_client, "renovar_artefacto", est.renovar)
    monkeypatch.setattr(settings, "lta_renovacion_margen_dias", 400)  # el sello de pruebas vence en 365 d
    return est


async def _sellos(conn, firma_id):
    return await FirmaRepository(conn).sellos_de(firma_id)


async def _asientos(conn, slug, action):
    return await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 AND action = $2", slug, action
    )


async def test_renueva_y_la_cadena_verifica_con_la_tsa_nueva_anclada(tenant_ctx, monkeypatch, storage) -> None:
    slug, conn = tenant_ctx
    doc, firma_id, minio_key, xml = await _sembrar(conn, slug, monkeypatch)
    storage.descargar.return_value = xml
    _tsa_larga(monkeypatch)

    resumen = await renovacion_lta.renovar_tenant(conn, slug)

    assert resumen["renovadas"] == 1, resumen
    sellos = await _sellos(conn, firma_id)
    assert [(s["ordinal"], s["estado"]) for s in sellos] == [(0, "vigente"), (1, "vigente")]
    storage.renovar.assert_awaited_once()
    kw = storage.renovar.await_args.kwargs
    assert kw["firma_id"] == firma_id and kw["ordinal"] == 1
    assert str(kw["file_id_anterior"]) == minio_key.split("/")[0]
    assert storage.subir.await_args.kwargs["filename"].endswith("-firmado-lta1.xml")
    assert await _asientos(conn, slug, "firma.lta_renovada") == 1
    # El XML subido es una cadena LTA de 2 sellos que verifica con la TSA nueva anclada.
    subido = storage.subir.await_args.kwargs["content"]
    salida = verify_xades(
        subido, doc.certificado_fingerprint, doc.extra["tsa_cert_fingerprint"], doc.extra["ca_fingerprint"],
        tsa_fingerprints_archivo=[s["tsa_cert_fingerprint"] for s in sellos],
    )
    assert salida.valida and salida.archive_timestamp_count == 2, salida.motivo
    # La vista resuelve el artefacto del último eslabón vigente.
    vigente = await FirmaRepository(conn).artefacto_vigente(firma_id)
    assert vigente["ordinal"] == 1 and vigente["minio_key"] == sellos[1]["minio_key"]
    # Una segunda pasada no renueva: el último sello ya vence a 5 años.
    assert (await renovacion_lta.renovar_tenant(conn, slug))["renovadas"] == 0


async def test_pendiente_worm_reintenta_solo_storage(tenant_ctx, monkeypatch, storage) -> None:
    slug, conn = tenant_ctx
    _doc, firma_id, _key, xml = await _sembrar(conn, slug, monkeypatch)
    storage.descargar.return_value = xml
    _tsa_larga(monkeypatch)
    storage.renovar.side_effect = preservacion_client.PreservacionError("storage caído")

    r1 = await renovacion_lta.renovar_tenant(conn, slug)
    assert r1["pendiente_worm"] == 1
    assert [(s["ordinal"], s["estado"]) for s in await _sellos(conn, firma_id)] == [
        (0, "vigente"), (1, "pendiente_worm"),
    ]

    storage.renovar.side_effect = None
    storage.renovar.return_value = {"protegido": True}
    r2 = await renovacion_lta.renovar_tenant(conn, slug)

    assert r2["pendientes_completados"] == 1 and r2["renovadas"] == 0
    assert [(s["ordinal"], s["estado"]) for s in await _sellos(conn, firma_id)] == [
        (0, "vigente"), (1, "vigente"),
    ]
    assert storage.subir.await_count == 1, "el XML ya estaba en storage: no se sube de nuevo"
    assert storage.renovar.await_count == 2


async def test_no_renueva_una_firma_que_no_verifica(tenant_ctx, monkeypatch, storage) -> None:
    slug, conn = tenant_ctx
    doc, _c, _s = await _firmar_lta(monkeypatch)
    root = etree.fromstring(doc.signed_xml)
    sv = root.find(".//{http://www.w3.org/2000/09/xmldsig#}SignatureValue")
    sv.text = ("A" if sv.text[0] != "A" else "B") + sv.text[1:]  # firma alterada
    alterado = etree.tostring(root)
    _d, firma_id, _key, xml = await _sembrar(conn, slug, monkeypatch, xml_alterado=alterado)
    storage.descargar.return_value = xml
    _tsa_larga(monkeypatch)

    resumen = await renovacion_lta.renovar_tenant(conn, slug)

    assert resumen["rechazadas"] == 1 and resumen["renovadas"] == 0
    assert len(await _sellos(conn, firma_id)) == 1
    storage.subir.assert_not_awaited()
    assert await _asientos(conn, slug, "firma.lta_renovacion_rechazada") == 1


async def test_no_renueva_con_una_tsa_sin_mas_plazo(tenant_ctx, monkeypatch, storage) -> None:
    slug, conn = tenant_ctx
    _doc, firma_id, _key, xml = await _sembrar(conn, slug, monkeypatch)
    storage.descargar.return_value = xml  # y la TSA vigente sigue siendo la de pruebas (365 d)

    resumen = await renovacion_lta.renovar_tenant(conn, slug)

    assert resumen["tsa_por_vencer"] == 1 and resumen["renovadas"] == 0
    assert len(await _sellos(conn, firma_id)) == 1
    storage.subir.assert_not_awaited()
    assert await _asientos(conn, slug, "firma.lta_renovacion_tsa_por_vencer") == 1


async def test_no_es_candidata_si_su_sello_vence_lejos(tenant_ctx, monkeypatch, storage) -> None:
    slug, conn = tenant_ctx
    await _sembrar(conn, slug, monkeypatch)
    monkeypatch.setattr(settings, "lta_renovacion_margen_dias", 30)  # vence en ~365 d: lejos

    resumen = await renovacion_lta.renovar_tenant(conn, slug)

    assert sum(resumen.values()) == 0
    storage.descargar.assert_not_awaited()


async def test_al_quedar_vigente_publica_firma_lta_renovada(tenant_ctx, monkeypatch, storage) -> None:
    """Mutación: quitar la publicación de `_completar_worm` (cae); publicarla ANTES de que storage
    registre la herencia WORM (cae `esperado` en la rama `pendiente_worm`)."""
    slug, conn = tenant_ctx
    _doc, firma_id, _key, xml = await _sembrar(conn, slug, monkeypatch)
    storage.descargar.return_value = xml
    _tsa_larga(monkeypatch)
    publicar = AsyncMock()
    monkeypatch.setattr(renovacion_lta, "publish_lta_renovada", publicar)
    monkeypatch.setattr(renovacion_lta, "get_redis", lambda: object())

    storage.renovar.side_effect = preservacion_client.PreservacionError("caído")
    await renovacion_lta.renovar_tenant(conn, slug)
    publicar.assert_not_awaited()  # pendiente_worm: aún no es vigente

    storage.renovar.side_effect = None
    await renovacion_lta.renovar_tenant(conn, slug)
    publicar.assert_awaited_once()
    kw = publicar.await_args.kwargs
    assert kw["firma_id"] == str(firma_id) and kw["ordinal"] == 1 and kw["tenant_slug"] == slug


# --- Correcciones de auditoría B10 (lane L) -------------------------------------------------


async def _caducar_sello(conn, slug, firma_id) -> None:
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    await conn.execute(
        "UPDATE firma_sello_archivo SET tsa_cert_not_after = now() - interval '2 days' WHERE firma_id = $1",
        firma_id,
    )


async def test_con_la_tsa_ya_caducada_no_se_renueva_y_el_eslabon_pasa_a_caducado(
    tenant_ctx, monkeypatch, storage
) -> None:
    """ARCH H1: renovar con la TSA caducada produce un artefacto que se invalida a sí mismo y que
    además queda bajo COMPLIANCE. Mutación: quitar el paso 0 de `_renovar_firma` -> cae (renueva)."""
    slug, conn = tenant_ctx
    _doc, firma_id, _key, xml = await _sembrar(conn, slug, monkeypatch)
    storage.descargar.return_value = xml
    _tsa_larga(monkeypatch)  # hay una TSA con plazo: lo único que lo impide es la caducidad
    await _caducar_sello(conn, slug, firma_id)

    resumen = await renovacion_lta.renovar_tenant(conn, slug)

    assert resumen["caducadas"] == 1 and resumen["renovadas"] == 0, resumen
    assert [(s["ordinal"], s["estado"]) for s in await _sellos(conn, firma_id)] == [(0, "caducado")]
    storage.descargar.assert_not_awaited()
    storage.subir.assert_not_awaited()
    assert await _asientos(conn, slug, "firma.lta_cadena_caducada") == 1
    # Sigue siendo el artefacto vigente (se sirve), y no vuelve a ser candidata.
    assert (await FirmaRepository(conn).artefacto_vigente(firma_id))["ordinal"] == 0
    assert sum((await renovacion_lta.renovar_tenant(conn, slug)).values()) == 0
    assert await _asientos(conn, slug, "firma.lta_cadena_caducada") == 1


async def test_el_rechazo_se_asienta_una_sola_vez_por_motivo(tenant_ctx, monkeypatch, storage) -> None:
    """ARCH H10 / SEG #13: el audit_log no se puede borrar; la misma causa repetida a diario no
    añade información. Mutación: quitar `registrar_aviso` de `_aviso_unico` -> cae (3 asientos)."""
    slug, conn = tenant_ctx
    _doc, firma_id, _key, xml = await _sembrar(conn, slug, monkeypatch)
    storage.descargar.return_value = xml  # TSA de pruebas: no da más plazo

    for _ in range(3):
        r = await renovacion_lta.renovar_tenant(conn, slug)
        assert r["tsa_por_vencer"] == 1
    assert await _asientos(conn, slug, "firma.lta_renovacion_tsa_por_vencer") == 1

    # Un motivo DISTINTO sí se asienta: la deduplicación es por motivo, no por firma.
    storage.descargar.return_value = xml + b" "
    assert (await renovacion_lta.renovar_tenant(conn, slug))["rechazadas"] == 1
    assert await _asientos(conn, slug, "firma.lta_renovacion_rechazada") == 1
    await renovacion_lta.renovar_tenant(conn, slug)
    assert await _asientos(conn, slug, "firma.lta_renovacion_rechazada") == 1


async def test_el_paso_a_vigente_deja_asiento(tenant_ctx, monkeypatch, storage) -> None:
    """SEG #9: `vigente` es lo que hace que /verify acepte el XML nuevo. Mutación: quitar el
    asiento de `_completar_worm` -> cae."""
    slug, conn = tenant_ctx
    _doc, firma_id, _key, xml = await _sembrar(conn, slug, monkeypatch)
    storage.descargar.return_value = xml
    _tsa_larga(monkeypatch)
    storage.renovar.side_effect = preservacion_client.PreservacionError("caído")
    await renovacion_lta.renovar_tenant(conn, slug)
    assert await _asientos(conn, slug, "firma.lta_sello_vigente") == 0  # aún pendiente_worm

    storage.renovar.side_effect = None
    await renovacion_lta.renovar_tenant(conn, slug)
    assert await _asientos(conn, slug, "firma.lta_sello_vigente") == 1


async def test_sin_la_crl_de_la_tsa_anterior_no_se_renueva(tenant_ctx, monkeypatch, storage) -> None:
    """ARCH H4 en el job: la CA de la TSA anterior publica CRL y no se puede obtener -> no se
    renueva y queda UN asiento. Mutación: tragarse la excepción en `renovar_archive_timestamp`
    -> cae (renueva sin revocación)."""
    slug, conn = tenant_ctx
    _doc, firma_id, _key, xml = await _sembrar(conn, slug, monkeypatch)
    storage.descargar.return_value = xml
    _tsa_larga(monkeypatch)

    def _sin_crl(*_a, **_k):
        raise tsa.RevocacionTsaNoDisponibleError("crl_tsa_no_obtenida: caída")

    monkeypatch.setattr("app.providers.local.tsa.revocacion_de_tsa", _sin_crl)
    # La TSA de pruebas es autofirmada (no la consulta): se fuerza para ejercitar el camino.
    for _ in range(2):
        r = await renovacion_lta.renovar_tenant(conn, slug)
        assert r["sin_revocacion"] == 1 and r["renovadas"] == 0
    assert len(await _sellos(conn, firma_id)) == 1
    storage.subir.assert_not_awaited()
    assert await _asientos(conn, slug, "firma.lta_renovacion_sin_revocacion_tsa") == 1


async def test_la_conservacion_recorre_activas_y_suspendidas() -> None:
    """ARCH H3: la obligación de conservar no se suspende con la entidad. Mutación: volver a
    `status = 'active'` -> cae."""
    consultas: list[str] = []

    class _Conn:
        async def fetch(self, sql, *a):
            consultas.append(sql)
            return [{"slug": "a"}, {"slug": "b"}]

    class _Ctx:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *a):
            return None

    class _Pool:
        def acquire(self):
            return _Ctx()

    assert await renovacion_lta._tenants_de_conservacion(_Pool()) == ["a", "b"]
    sql = consultas[0]
    assert "'active'" in sql and "'suspended'" in sql and "inactive" not in sql
    assert "provisioning_status = 'active'" in sql


async def test_tsa_revocada_no_renueva_y_se_asienta_una_vez(tenant_ctx, monkeypatch, storage) -> None:
    """R2-3 en el job. Mutación: quitar el `except tsa.TsaRevocadaError` -> cae (error genérico)."""
    slug, conn = tenant_ctx
    _doc, firma_id, _key, xml = await _sembrar(conn, slug, monkeypatch)
    storage.descargar.return_value = xml
    _tsa_larga(monkeypatch)

    def _revocada(*_a, **_k):
        raise tsa.TsaRevocadaError("tsa_revocada: serie 1")

    monkeypatch.setattr("app.providers.local.tsa.revocacion_de_tsa", _revocada)
    for _ in range(2):
        r = await renovacion_lta.renovar_tenant(conn, slug)
        assert r["tsa_revocada"] == 1 and r["renovadas"] == 0
    assert len(await _sellos(conn, firma_id)) == 1
    assert await _asientos(conn, slug, "firma.lta_renovacion_tsa_revocada") == 1


async def test_caducada_la_cadena_de_dos_eslabones_asienta_una_sola_vez(tenant_ctx, monkeypatch, storage) -> None:
    """R2-4: con 2 eslabones, el `caducado` del último no puede hacer caducar al 0 en la pasada
    siguiente. Mutación: volver a filtrar `estado='vigente'` DENTRO del DISTINCT ON -> cae."""
    slug, conn = tenant_ctx
    _doc, firma_id, _key, xml = await _sembrar(conn, slug, monkeypatch)
    storage.descargar.return_value = xml
    _tsa_larga(monkeypatch)
    assert (await renovacion_lta.renovar_tenant(conn, slug))["renovadas"] == 1
    await _caducar_sello(conn, slug, firma_id)  # los DOS eslabones vencidos

    r1 = await renovacion_lta.renovar_tenant(conn, slug)
    r2 = await renovacion_lta.renovar_tenant(conn, slug)

    assert r1["caducadas"] == 1 and r2["caducadas"] == 0
    assert [(s["ordinal"], s["estado"]) for s in await _sellos(conn, firma_id)] == [(0, "vigente"), (1, "caducado")]
    assert await _asientos(conn, slug, "firma.lta_cadena_caducada") == 1
