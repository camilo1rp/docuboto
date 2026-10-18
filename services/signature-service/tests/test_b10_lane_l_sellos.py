# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10, correcciones de auditoría (lane L): sello de archivo y huella del borrador.

  * ARCH H2: el ÚLTIMO sello de archivo se juzga a la fecha actual; con su TSA caducada la firma
    baja a XAdES-LT (`lta_vigente=false`) y `_vigencia_diferida` no rescata un firmante caducado.
  * ARCH H4: el `TimeStampValidationData` lleva la CRL de la CA de la TSA anterior; si la CA
    publica una y no se puede obtener, no se renueva.
  * SEG #4: el vencimiento del eslabón 0 sale del certificado elegido por HUELLA, no de `[0]`; un
    token sin certificados no tumba la firma.
  * SEG #2: solo `borrador_not_found` es «desaparecido»; un 404 genérico es «no verificable».

Mutaciones (aplicar, correr, revertir): quitar el bloque `if not lta_vigente` de `verify_xades`
(cae H2, las tres pruebas); que `renovar_archive_timestamp` ignore `crl_tsa` (cae
`test_el_tsvd_lleva_la_crl_de_la_ca_de_la_tsa_anterior`); volver a `certs_ats[0]` en
`signing_service` (cae `test_el_vencimiento_del_eslabon_0_sale_de_la_huella_no_del_primero`);
que `get_huella_borrador` trate todo 404 como desaparecido (cae
`test_un_404_generico_no_es_borrador_desaparecido`)."""

from __future__ import annotations

from base64 import b64decode
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import ExtendedKeyUsageOID
from lxml import etree

from app.core import http_clients, tsa
from app.core.http_clients import BorradorDesaparecidoError, HuellaNoVerificableError
from app.providers import local
from app.providers.base import SignerRef
from app.providers.local import renovar_archive_timestamp, verify_xades
from tests.test_sign_xml_xades import signature_env  # noqa: F401, F811  (fixture)
from tests.test_vigencia_del_certificado_y_sellos import _reloj_adelantado
from tests.test_xmldsig_conformidad import (  # noqa: F401  (_tsa_local es autouse)
    _NS_LTA,
    _cert,
    _firmar_lta,
    _pem_cert,
    _pem_key,
    _tsa_local,
)


def _verificar(doc, **extra):  # noqa: ANN001, ANN003, ANN202
    avisos: list[str] = []
    salida = verify_xades(
        doc.signed_xml,
        doc.certificado_fingerprint,
        doc.extra["tsa_cert_fingerprint"],
        doc.extra["ca_fingerprint"],
        advertencias=avisos,
        **extra,
    )
    return salida, avisos


# ---------------------------------------------------------------------------
# H2 — el último sello se juzga a «ahora»
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_con_la_tsa_vigente_la_firma_es_lta_y_lta_vigente(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control positivo: sin él, las dos pruebas siguientes podrían pasar por otra causa."""
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    salida, avisos = _verificar(doc)
    assert salida.valida and salida.xades_level == "XAdES-LTA"
    assert salida.lta_vigente is True and "archive_timestamp_caducado" not in avisos


@pytest.mark.asyncio
async def test_ultimo_ats_con_la_tsa_caducada_degrada_a_lt(monkeypatch: pytest.MonkeyPatch) -> None:
    """La TSA de pruebas dura 365 d y el sello institucional 825 d: a +400 d el firmante vale y la
    TSA del único ATS ya no. Sin sello posterior no hay POE (EN 319 102-1 §5.6.2)."""
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    _reloj_adelantado(monkeypatch, 400)
    salida, avisos = _verificar(doc)
    assert salida.valida, salida.motivo
    assert salida.lta_vigente is False
    assert salida.xades_level == "XAdES-LT", "no puede declararse LTA un sello de archivo que ya no prueba"
    assert "archive_timestamp_caducado" in avisos
    assert salida.archive_timestamp_present, "el sello sigue ahí y valida: lo que cambió es que ya no prueba"


@pytest.mark.asyncio
async def test_el_firmante_caducado_no_se_rescata_con_un_ats_cuya_tsa_tambien_caduco(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_vigencia_diferida` (ADR-034 §5) solo vale con una cadena de sellos que aún prueba."""
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    _reloj_adelantado(monkeypatch, 900)  # firmante (825 d) y TSA (365 d) caducados
    salida, _avisos = _verificar(doc)
    assert salida.valida is False and salida.motivo == "certificado_fuera_de_vigencia"


@pytest.mark.asyncio
async def test_renovado_a_tiempo_el_ultimo_ats_con_tsa_larga_sigue_siendo_lta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El caso sano de una cadena renovada: el último ATS lo emitió una TSA a 5 años."""
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    _tsa_de_dias(monkeypatch, 5 * 365)
    renovada = renovar_archive_timestamp(doc.signed_xml)
    huella = renovada.token.tsa_cert_fingerprint
    _reloj_adelantado(monkeypatch, 400)  # el ATS 0 ya caducó, el 1 no
    salida = verify_xades(
        renovada.signed_xml, doc.certificado_fingerprint, doc.extra["tsa_cert_fingerprint"],
        doc.extra["ca_fingerprint"], tsa_fingerprints_archivo=[huella],
    )
    assert salida.valida, salida.motivo
    assert salida.lta_vigente is True and salida.xades_level == "XAdES-LTA"


def _tsa_de_dias(monkeypatch: pytest.MonkeyPatch, dias: int, *, ca=None, cdp: str | None = None):  # noqa: ANN001, ANN202
    """Instala una TSA nueva (autofirmada, o emitida por ``ca=(key, cert)`` con un CDP)."""
    key, base = _cert("tsa-nueva", tsa_eku=True)
    ahora = datetime.now(UTC)
    emisor = ca[1].subject if ca else base.subject
    builder = (
        x509.CertificateBuilder()
        .subject_name(base.subject).issuer_name(emisor).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(days=1)).not_valid_after(ahora + timedelta(days=dias))
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
    )
    if cdp:
        builder = builder.add_extension(
            x509.CRLDistributionPoints([
                x509.DistributionPoint(
                    full_name=[x509.UniformResourceIdentifier(cdp)], relative_name=None,
                    reasons=None, crl_issuer=None,
                )
            ]),
            critical=False,
        )
    cert = builder.sign(ca[0] if ca else key, hashes.SHA256())
    monkeypatch.setattr(
        "app.core.tsa.load_tsa_seal",
        lambda: SignerRef(cert_pem=_pem_cert(cert), key_pem=_pem_key(key), key_password=None),
    )
    return key, cert


# ---------------------------------------------------------------------------
# H4 — revocación de la TSA anterior en el TimeStampValidationData
# ---------------------------------------------------------------------------

_TSVD = ".//x141:TimeStampValidationData"


def _ca_con_crl(*, vencida: bool = False, emisor_ajeno: bool = False, revocar: int | None = None, firma_ajena: bool = False):
    ca_key, ca_cert = _cert("ca-tsa", ca=True)
    otra_key, otra_cert = _cert("otra-ca", ca=True)
    firmante_key, emisor = (otra_key, otra_cert) if emisor_ajeno else (ca_key, ca_cert)
    ahora = datetime.now(UTC)
    nombre = emisor.subject
    b = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(nombre)
        .last_update(ahora - timedelta(days=3))
        .next_update(ahora - timedelta(days=1) if vencida else ahora + timedelta(days=7))
    )
    if revocar is not None:
        b = b.add_revoked_certificate(
            x509.RevokedCertificateBuilder().serial_number(revocar).revocation_date(ahora - timedelta(days=2)).build()
        )
    # `firma_ajena`: el emisor es el correcto pero la CRL la firmó OTRA clave (falsificada).
    crl = b.sign(otra_key if firma_ajena else firmante_key, hashes.SHA256())
    return (ca_key, ca_cert), crl.public_bytes(serialization.Encoding.DER)


def _declarar_ca(monkeypatch: pytest.MonkeyPatch, tmp_path, ca) -> None:  # noqa: ANN001
    """La CA de la TSA, por configuración (el token de la TSA de pruebas no la trae)."""
    f = tmp_path / "tsa-ca.pem"
    f.write_bytes(ca[1].public_bytes(serialization.Encoding.PEM))
    monkeypatch.setattr(tsa.settings, "tsa_ca_pem_paths", str(f))


def _servir_crl(monkeypatch: pytest.MonkeyPatch, resultado: bytes | Exception) -> list[str]:
    pedidas: list[str] = []

    class _Cliente:
        def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
            pass

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *a) -> None:  # noqa: ANN002
            return None

        def get(self, url: str):  # noqa: ANN202
            pedidas.append(url)
            if isinstance(resultado, Exception):
                raise resultado
            return httpx.Response(200, content=resultado, request=httpx.Request("GET", url))

    monkeypatch.setattr(tsa.httpx, "Client", _Cliente)
    return pedidas


@pytest.mark.asyncio
async def test_el_tsvd_lleva_la_crl_de_la_ca_de_la_tsa_anterior(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    ca, crl_der = _ca_con_crl()
    _declarar_ca(monkeypatch, tmp_path, ca)
    _tsa_de_dias(monkeypatch, 365, ca=ca, cdp="http://crl.test/tsa.crl")
    doc, _c, _ca = await _firmar_lta(monkeypatch)  # su ATS 0 lo sella la TSA emitida por `ca`
    pedidas = _servir_crl(monkeypatch, crl_der)
    _tsa_de_dias(monkeypatch, 5 * 365)  # el sello nuevo lo da otra TSA

    renovada = renovar_archive_timestamp(doc.signed_xml)

    assert pedidas == ["http://crl.test/tsa.crl"]
    root = etree.fromstring(renovada.signed_xml)
    crl = root.find(_TSVD + "//xades:RevocationValues/xades:CRLValues/xades:EncapsulatedCRLValue", _NS_LTA)
    assert crl is not None and b64decode(crl.text) == crl_der
    tsvd = root.find(_TSVD, _NS_LTA)
    assert [etree.QName(h).localname for h in tsvd] == ["CertificateValues", "RevocationValues"]


@pytest.mark.asyncio
async def test_una_tsa_autofirmada_no_tiene_revocacion_aplicable(monkeypatch: pytest.MonkeyPatch) -> None:
    """La TSA local es su propia ancla: ni CA ni CRL. Se declara en el ADR-034 §5; no se inventa."""
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    pedidas = _servir_crl(monkeypatch, RuntimeError("no debe pedirse nada"))
    renovada = renovar_archive_timestamp(doc.signed_xml)
    root = etree.fromstring(renovada.signed_xml)
    assert root.find(_TSVD + "//xades:RevocationValues", _NS_LTA) is None
    assert pedidas == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "caso",
    ["red_caida", "crl_vencida", "crl_de_otro_emisor", "sin_distribution_point", "crl_con_firma_ajena", "sin_la_ca"],
)
async def test_si_la_crl_de_la_tsa_no_se_obtiene_o_no_se_verifica_no_se_renueva(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caso: str  # noqa: ANN001
) -> None:
    """Mutaciones: aceptar la CRL sin verificar su firma (cae `crl_con_firma_ajena` y `sin_la_ca`)."""
    ca, crl_der = _ca_con_crl(
        vencida=caso == "crl_vencida", emisor_ajeno=caso == "crl_de_otro_emisor",
        firma_ajena=caso == "crl_con_firma_ajena",
    )
    if caso != "sin_la_ca":
        _declarar_ca(monkeypatch, tmp_path, ca)
    _tsa_de_dias(monkeypatch, 365, ca=ca, cdp=None if caso == "sin_distribution_point" else "http://crl.test/x")
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    _servir_crl(monkeypatch, httpx.ConnectError("caída") if caso == "red_caida" else crl_der)

    with pytest.raises(tsa.RevocacionTsaNoDisponibleError):
        renovar_archive_timestamp(doc.signed_xml)


@pytest.mark.asyncio
async def test_si_la_tsa_anterior_figura_revocada_no_se_renueva(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    """R2-3. Mutación: quitar la consulta `get_revoked_certificate_by_serial_number` -> cae."""
    ca, _ = _ca_con_crl()
    _declarar_ca(monkeypatch, tmp_path, ca)
    _key, cert = _tsa_de_dias(monkeypatch, 365, ca=ca, cdp="http://crl.test/x")
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca[1].subject)
        .last_update(datetime.now(UTC) - timedelta(days=3))
        .next_update(datetime.now(UTC) + timedelta(days=7))
        .add_revoked_certificate(
            x509.RevokedCertificateBuilder().serial_number(cert.serial_number)
            .revocation_date(datetime.now(UTC) - timedelta(days=2)).build()
        )
        .sign(ca[0], hashes.SHA256())
    )
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    _servir_crl(monkeypatch, crl.public_bytes(serialization.Encoding.DER))
    with pytest.raises(tsa.TsaRevocadaError):
        renovar_archive_timestamp(doc.signed_xml)


# ---------------------------------------------------------------------------
# SEG #4 — el vencimiento del eslabón 0 sale de la HUELLA
# ---------------------------------------------------------------------------


def _token_con_la_ca_delante():
    """Un token cuyo `certificates` trae primero la CA (más duradera) y después la TSA."""
    from asn1crypto import cms
    from asn1crypto import x509 as asn1_x509

    _ca_key, ca_cert = _cert("ca-delante", ca=True)
    token = tsa.build_timestamp_token(b"x")
    info = cms.ContentInfo.load(token.token_der)
    tsa_asn1 = info["content"]["certificates"][0]
    ca_asn1 = cms.CertificateChoices(
        name="certificate", value=asn1_x509.Certificate.load(ca_cert.public_bytes(serialization.Encoding.DER))
    )
    info["content"]["certificates"] = cms.CertificateSet([ca_asn1, tsa_asn1])
    return info.dump(), ca_cert, token.tsa_cert_fingerprint


def test_certificado_tsa_de_token_busca_por_sid_no_toma_el_primero(monkeypatch: pytest.MonkeyPatch) -> None:
    der, _ca_cert, huella = _token_con_la_ca_delante()
    real = tsa.certificados_de_token
    # DER ordena un SET OF por codificación, así que el orden «CA primero» se FUERZA aquí: es el
    # que una TSA remota con cadena puede entregar y el que `certs[0]` tomaría por la TSA.
    monkeypatch.setattr(
        tsa, "certificados_de_token",
        lambda d: sorted(real(d), key=lambda c: c.fingerprint(hashes.SHA256()).hex() == huella),
    )
    assert tsa.certificados_de_token(der)[0].fingerprint(hashes.SHA256()).hex() != huella
    elegido = tsa.certificado_tsa_de_token(der)
    assert elegido is not None and elegido.fingerprint(hashes.SHA256()).hex() == huella


def test_certificado_tsa_de_token_sin_certificados_devuelve_none() -> None:
    from asn1crypto import cms

    info = cms.ContentInfo.load(tsa.build_timestamp_token(b"x").token_der)
    info["content"]["certificates"] = None
    assert tsa.certificado_tsa_de_token(info.dump()) is None


@pytest.mark.asyncio
async def test_el_vencimiento_del_eslabon_0_sale_de_la_huella_no_del_primero(
    signature_env, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from app.repositories.firma_repository import FirmaRepository
    from tests.test_sign_xml_xades import (
        _build_crl,
        _ca_cert_pem,
        _generate_ca,
        _issue_seal_signed_by_ca,
        _lt_signer_ref,
        _sign_indice,
    )

    client, _conn, _table, _uploads, install_seal, _install_tsa = signature_env
    from app.core.config import settings

    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    ca_key, ca_cert = _generate_ca()
    cert_pem, key_pem, _seal = _issue_seal_signed_by_ca(ca_key, ca_cert)
    install_seal(_lt_signer_ref(cert_pem, key_pem, _ca_cert_pem(ca_cert), _build_crl(ca_key, ca_cert)))

    ahora = datetime.now(UTC)
    _k, ca_larga = _cert("ca-larga", ca=True)
    capturado: dict = {}

    async def _insert(self, **kw):  # noqa: ANN001, ANN202
        capturado.update(kw)
        return {"firma_id": kw["firma_id"], "ordinal": kw["ordinal"]}

    monkeypatch.setattr(FirmaRepository, "insert_sello_archivo", _insert)
    real = tsa.certificados_de_token

    def _con_la_ca_delante(der: bytes):  # noqa: ANN202
        certs = real(der)
        # El mismo SET OF, pero con una CA de vigencia mucho mayor DELANTE.
        larga = MagicMock(wraps=ca_larga)
        larga.not_valid_after_utc = ahora + timedelta(days=3650)
        larga.fingerprint = lambda alg: ca_larga.fingerprint(alg)
        return [larga, *certs]

    monkeypatch.setattr("app.services.signing_service.tsa_module.certificados_de_token", _con_la_ca_delante)

    resp = await _sign_indice(client)
    assert resp.status_code == 201
    assert capturado["tsa_cert_not_after"] < ahora + timedelta(days=400), (
        "el vencimiento debe ser el de la TSA (≈365 d) y no el de la CA que iba delante"
    )


@pytest.mark.asyncio
async def test_un_token_sin_certificados_no_tumba_la_firma(
    signature_env, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from app.repositories.firma_repository import FirmaRepository
    from tests.test_sign_xml_xades import (
        _build_crl,
        _ca_cert_pem,
        _generate_ca,
        _issue_seal_signed_by_ca,
        _lt_signer_ref,
        _sign_indice,
    )

    client, _conn, _table, _uploads, install_seal, _install_tsa = signature_env
    from app.core.config import settings

    monkeypatch.setattr(settings, "signer_lt_enabled", True)
    ca_key, ca_cert = _generate_ca()
    cert_pem, key_pem, _seal = _issue_seal_signed_by_ca(ca_key, ca_cert)
    install_seal(_lt_signer_ref(cert_pem, key_pem, _ca_cert_pem(ca_cert), _build_crl(ca_key, ca_cert)))

    insertado = AsyncMock()
    monkeypatch.setattr(FirmaRepository, "insert_sello_archivo", insertado)
    # `signing_service` lee SOLO el token de archivo con esta función: llega «vacío».
    monkeypatch.setattr("app.services.signing_service.tsa_module.certificados_de_token", lambda _d: [])

    resp = await _sign_indice(client)
    assert resp.status_code == 201, resp.text  # antes: IndexError -> 500
    insertado.assert_not_awaited()


# ---------------------------------------------------------------------------
# SEG #2 — solo `borrador_not_found` es «desaparecido»
# ---------------------------------------------------------------------------


def _responder(monkeypatch: pytest.MonkeyPatch, status: int, cuerpo) -> None:  # noqa: ANN001
    class _Cliente:
        def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *a) -> None:  # noqa: ANN002
            return None

        async def get(self, url, headers=None):  # noqa: ANN001, ANN202
            return httpx.Response(status, json=cuerpo, request=httpx.Request("GET", url))

    monkeypatch.setattr(http_clients.httpx, "AsyncClient", _Cliente)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cuerpo",
    [
        {"detail": {"error": "borrador_not_found", "status": 404}},  # HTTPException de document
        {"error": "borrador_not_found", "status": 404},
    ],
)
async def test_el_404_borrador_not_found_es_desaparecido(monkeypatch: pytest.MonkeyPatch, cuerpo: dict) -> None:
    _responder(monkeypatch, 404, cuerpo)
    with pytest.raises(BorradorDesaparecidoError):
        await http_clients.get_huella_borrador("b-1", "demo")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cuerpo",
    [
        {"detail": "Not Found"},  # ruta inexistente (despliegue desalineado, proxy, Mount)
        {"detail": {"error": "radicado_not_found"}},  # otro 404 legítimo de document, no es el borrador
        ["raro"],
    ],
)
async def test_un_404_generico_no_es_borrador_desaparecido(monkeypatch: pytest.MonkeyPatch, cuerpo) -> None:  # noqa: ANN001
    """`desaparecido` => `vigente=None` => se invalida TODO lo vivo, también lo firmado."""
    _responder(monkeypatch, 404, cuerpo)
    with pytest.raises(HuellaNoVerificableError) as info:
        await http_clients.get_huella_borrador("b-1", "demo")
    # (`BorradorDesaparecidoError` hereda de `HuellaNoVerificableError`: no basta el `raises`.)
    assert not isinstance(info.value, BorradorDesaparecidoError)


# ---------------------------------------------------------------------------
# El módulo local sigue exportando lo que usan los jobs
# ---------------------------------------------------------------------------


def test_verify_outcome_declara_lta_vigente() -> None:
    assert local.VerifyOutcome(True).lta_vigente is None
