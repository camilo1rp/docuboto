# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-6 (ADR-042): OCSP de la TSA cuando su CA no publica CRL.

Cubre la verificación de la respuesta (:func:`app.core.ocsp.verify_ocsp_tsa`) y su uso al renovar el
``ArchiveTimeStamp`` (``tsa.revocacion_de_tsa`` -> ``TimeStampValidationData``).

Mutaciones (aplicar, correr, revertir), cada una tumba la prueba que se nombra:
  * aceptar una respuesta SIN nonce aunque su ``producedAt`` sea viejo (quitar la rama
    ``ocsp_sin_nonce_y_no_fresca``)         -> ``test_sin_nonce_y_vieja_no_se_acepta``;
  * tomar la PRIMERA ``SingleResponse`` en vez de buscar la del certificado
                                            -> ``test_se_busca_la_single_response_del_certificado``;
  * no exigir EKU ``OCSPSigning``/``ocsp-nocheck`` al delegado
                                            -> ``test_delegado_invalido_se_rechaza``;
  * ignorar ``REVOKED``                      -> ``test_revocada_antes_del_sello_no_se_renueva``;
  * aceptar firma SHA-1                      -> ``test_ocsp_firmada_con_sha1_no_se_acepta``;
  * quitar el ``no_anterior_a`` del OCSP     -> ``test_ocsp_producida_antes_del_sello_previo_no_vale``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import hashes
from lxml import etree

from app.core import algoritmos, ocsp, tsa
from app.providers.local import renovar_archive_timestamp
from tests import c6_pki as pki
from tests.test_xmldsig_conformidad import (  # noqa: F401  (_tsa_local es autouse)
    _NS_LTA,
    _firmar_lta,
    _tsa_local,
)


def _par(**kw):  # noqa: ANN003, ANN202
    ca = pki.ca_nueva()
    tsa_k, tsa_c = pki.tsa_emitida(ca, ocsp_url=pki.URL_OCSP, **kw)
    return ca, tsa_k, tsa_c


def _verif(der, cert, ca, **kw):  # noqa: ANN001, ANN003, ANN202
    return ocsp.verify_ocsp_tsa(der, cert, ca[1], **kw)


NONCE = b"\x07" * 32


# ---------------------------------------------------------------------------
# La respuesta
# ---------------------------------------------------------------------------


def test_control_positivo_la_ca_responde_good() -> None:
    """Sin este control las pruebas negativas podrían pasar porque el armado a mano está roto."""
    ca, _k, cert = _par()
    r = _verif(pki.respuesta_ocsp(cert, ca, nonce=NONCE), cert, ca, nonce=NONCE)
    assert r.valido and r.estado == "good", r.motivo
    assert r.algoritmos == ("sha256", "rsa")


def test_delegado_valido_se_acepta() -> None:
    ca, _k, cert = _par()
    deleg = pki.delegado_ocsp(ca)
    r = _verif(
        pki.respuesta_ocsp(cert, ca, firmante=deleg, incluir_cert_firmante=True, nonce=NONCE),
        cert, ca, nonce=NONCE,
    )
    assert r.valido, r.motivo


@pytest.mark.parametrize("caso", ["sin_eku", "sin_nocheck", "otra_ca", "caducado", "sin_certificado"])
def test_delegado_invalido_se_rechaza(caso: str) -> None:
    ca, _k, cert = _par()
    if caso == "otra_ca":
        deleg = pki.delegado_ocsp(pki.ca_nueva("otra-ca"))
    elif caso == "caducado":
        deleg = pki.delegado_ocsp(ca, dias=-2)
    else:
        deleg = pki.delegado_ocsp(ca, eku=caso != "sin_eku", nocheck=caso != "sin_nocheck")
    r = _verif(
        pki.respuesta_ocsp(
            cert, ca, firmante=deleg, incluir_cert_firmante=caso != "sin_certificado", nonce=NONCE
        ),
        cert, ca, nonce=NONCE,
    )
    assert not r.valido, f"el delegado {caso} no puede atestiguar"


def test_quien_dice_ser_la_ca_pero_firma_con_otra_clave_no_vale() -> None:
    """El `responderID` nombra a la CA, la firma es de un tercero: la firma no verifica."""
    ca, _k, cert = _par()
    impostor = pki.ca_nueva("impostor")
    der = pki.respuesta_ocsp(cert, ca, firmante=(impostor[0], ca[1]), nonce=NONCE)
    r = _verif(der, cert, ca, nonce=NONCE)
    assert not r.valido and "firma_invalida" in (r.motivo or "")


def test_revoked_se_informa_con_su_fecha() -> None:
    ca, _k, cert = _par()
    cuando = datetime.now(UTC) - timedelta(days=3)
    r = _verif(pki.respuesta_ocsp(cert, ca, estado="revoked", revocado_en=cuando, nonce=NONCE), cert, ca, nonce=NONCE)
    assert r.valido and r.estado == "revoked"
    assert r.revocation_time is not None and abs((r.revocation_time - cuando).total_seconds()) < 2


def test_unknown_no_vale() -> None:
    ca, _k, cert = _par()
    r = _verif(pki.respuesta_ocsp(cert, ca, estado="unknown", nonce=NONCE), cert, ca, nonce=NONCE)
    assert not r.valido and r.motivo == "ocsp_status_desconocido"


def test_un_nonce_que_no_es_el_pedido_se_rechaza() -> None:
    ca, _k, cert = _par()
    r = _verif(pki.respuesta_ocsp(cert, ca, nonce=b"\x08" * 32), cert, ca, nonce=NONCE)
    assert not r.valido and r.motivo == "ocsp_nonce_no_coincide"


def test_sin_nonce_pero_fresca_se_acepta() -> None:
    """Muchos respondedores de TSA no devuelven nonce: se acepta solo si está fresca."""
    ca, _k, cert = _par()
    assert _verif(pki.respuesta_ocsp(cert, ca), cert, ca, nonce=NONCE).valido


def test_sin_nonce_y_vieja_no_se_acepta() -> None:
    """Una respuesta capturada hace una hora y reenviada: sin nonce nada la distingue de una fresca
    salvo su `producedAt`. Mutación: quitar la rama `ocsp_sin_nonce_y_no_fresca` -> esta cae."""
    ca, _k, cert = _par()
    vieja = datetime.now(UTC) - timedelta(hours=1)
    der = pki.respuesta_ocsp(cert, ca, produced_at=vieja, this_update=vieja, next_update=vieja + timedelta(days=2))
    r = _verif(der, cert, ca, nonce=NONCE)
    assert not r.valido and r.motivo == "ocsp_sin_nonce_y_no_fresca"


def test_una_respuesta_vencida_o_del_futuro_no_se_acepta() -> None:
    ca, _k, cert = _par()
    ahora = datetime.now(UTC)
    vencida = pki.respuesta_ocsp(
        cert, ca, nonce=NONCE, this_update=ahora - timedelta(days=2), next_update=ahora - timedelta(days=1)
    )
    assert _verif(vencida, cert, ca, nonce=NONCE).motivo == "ocsp_vencida"
    futura = pki.respuesta_ocsp(cert, ca, nonce=NONCE, this_update=ahora + timedelta(hours=2))
    assert _verif(futura, cert, ca, nonce=NONCE).motivo == "ocsp_this_update_en_el_futuro"


def test_se_busca_la_single_response_del_certificado() -> None:
    """Varias `SingleResponse`, la buena en 2.º lugar. Mutación: tomar la primera -> cae."""
    ca, _k, cert = _par()
    otro_k, otro = pki.tsa_emitida(ca, cn="otra-tsa", ocsp_url=pki.URL_OCSP)
    der = pki.respuesta_ocsp(cert, ca, nonce=NONCE, antes=(otro,))
    r = _verif(der, cert, ca, nonce=NONCE)
    assert r.valido and r.estado == "good", r.motivo
    # y pedir por el OTRO certificado, que va primero, también funciona: se busca, no se asume
    assert _verif(der, otro, ca, nonce=NONCE).valido


def test_una_respuesta_sobre_otro_certificado_no_vale() -> None:
    ca, _k, cert = _par()
    _k2, otro = pki.tsa_emitida(ca, cn="otra-tsa", ocsp_url=pki.URL_OCSP)
    r = _verif(pki.respuesta_ocsp(otro, ca, nonce=NONCE), cert, ca, nonce=NONCE)
    assert not r.valido and r.motivo == "ocsp_certid_no_coincide"


def test_el_certid_con_sha1_es_un_identificador_permitido() -> None:
    ca, _k, cert = _par()
    r = _verif(pki.respuesta_ocsp(cert, ca, nonce=NONCE, hash_certid="sha1"), cert, ca, nonce=NONCE)
    assert r.valido, r.motivo


def test_ocsp_firmada_con_sha1_no_se_acepta() -> None:
    """SHA-1 solo como identificador, nunca en una firma. La respuesta es auténtica pero su algoritmo
    no está en el catálogo. Mutación: aceptarlo -> cae la prueba de renovación de abajo."""
    ca, _k, cert = _par()
    r = _verif(pki.respuesta_ocsp(cert, ca, nonce=NONCE, hash_firma=hashes.SHA1()), cert, ca, nonce=NONCE)  # noqa: S303
    assert r.valido
    assert "sha1" in r.algoritmos and algoritmos.no_vigentes(r.algoritmos) == ["sha1"]


def test_una_respuesta_no_exitosa_no_vale() -> None:
    ca, _k, cert = _par()
    r = _verif(pki.respuesta_ocsp(cert, ca, status_respuesta="try_later"), cert, ca, nonce=NONCE)
    assert not r.valido and r.motivo == "ocsp_response_no_exitosa"


def test_modo_archivado_exige_producida_entre_los_dos_sellos() -> None:
    ca, _k, cert = _par()
    t0 = datetime.now(UTC).replace(microsecond=0)
    produced = t0 + timedelta(hours=1)
    der = pki.respuesta_ocsp(cert, ca, produced_at=produced, this_update=produced)
    assert _verif(der, cert, ca, ventana=(t0, t0 + timedelta(hours=2))).valido
    antes = _verif(der, cert, ca, ventana=(t0 + timedelta(hours=2), t0 + timedelta(hours=3)))
    assert antes.motivo == "ocsp_anterior_al_sello_previo"
    despues = _verif(der, cert, ca, ventana=(t0 - timedelta(hours=2), t0 + timedelta(minutes=30)))
    assert despues.motivo == "ocsp_posterior_al_sello_siguiente"


def test_la_peticion_lleva_certid_sha256_y_nonce_de_32_bytes() -> None:
    ca, _k, cert = _par()
    der, nonce = ocsp.build_ocsp_request_tsa(cert, ca[1])
    from cryptography.x509 import ocsp as c_ocsp

    pet = c_ocsp.load_der_ocsp_request(der)
    assert isinstance(pet.hash_algorithm, hashes.SHA256) and len(nonce) == 32
    assert pki.nonce_de(der) == nonce


# ---------------------------------------------------------------------------
# Al renovar el ArchiveTimeStamp
# ---------------------------------------------------------------------------

_TSVD = ".//x141:TimeStampValidationData"


async def _escenario(monkeypatch, tmp_path, *, cdp: str | None = None, ocsp_url: str | None = pki.URL_OCSP):  # noqa: ANN001, ANN202
    """Una firma LTA cuyo ATS 0 lo selló una TSA emitida por una CA con (o sin) CRL/OCSP."""
    ca = pki.ca_nueva()
    k, cert = pki.tsa_emitida(ca, cdp=cdp, ocsp_url=ocsp_url)
    pki.declarar_ca(monkeypatch, tmp_path, ca)
    pki.instalar_tsa(monkeypatch, k, cert)
    doc, _c, _ca = await _firmar_lta(monkeypatch)
    return ca, cert, doc


def _gen_del_ultimo_ats(xml: bytes) -> datetime:
    from base64 import b64decode

    root = etree.fromstring(xml)
    enc = root.findall(".//x141:ArchiveTimeStamp/xades:EncapsulatedTimeStamp", _NS_LTA)[-1]
    return tsa.gen_time_de_token(b64decode(enc.text))


@pytest.mark.asyncio
async def test_good_por_ocsp_va_en_el_tsvd(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    ca, cert, doc = await _escenario(monkeypatch, tmp_path)
    pedidas = pki.servir(
        monkeypatch, ocsp=lambda pet: pki.respuesta_ocsp(cert, ca, nonce=pki.nonce_de(pet))
    )
    r = renovar_archive_timestamp(doc.signed_xml)

    assert [u for u, _ in pedidas.post] == [pki.URL_OCSP] and pedidas.get == []
    root = etree.fromstring(r.signed_xml)
    ocsp_el = root.find(_TSVD + "//xades:RevocationValues/xades:OCSPValues/xades:EncapsulatedOCSPValue", _NS_LTA)
    assert ocsp_el is not None
    assert root.find(_TSVD + "//xades:RevocationValues/xades:CRLValues", _NS_LTA) is None
    assert r.revocacion_tsa_previa == "ocsp"
    # la TSA nueva (la misma, con AIA OCSP) anuncia OCSP; el TSVD lleva su CA aunque el token no
    assert r.tsa_revocacion_fuente == "ocsp"
    certs_tsvd = root.findall(_TSVD + "/xades:CertificateValues/xades:EncapsulatedX509Certificate", _NS_LTA)
    assert len(certs_tsvd) == 2


@pytest.mark.asyncio
async def test_la_peticion_ocsp_sale_con_nonce_y_sha256(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    ca, cert, doc = await _escenario(monkeypatch, tmp_path)
    pedidas = pki.servir(monkeypatch, ocsp=lambda pet: pki.respuesta_ocsp(cert, ca, nonce=pki.nonce_de(pet)))
    renovar_archive_timestamp(doc.signed_xml)
    assert pki.nonce_de(pedidas.post[0][1]) is not None
    from cryptography.x509 import ocsp as c_ocsp

    assert isinstance(c_ocsp.load_der_ocsp_request(pedidas.post[0][1]).hash_algorithm, hashes.SHA256)


@pytest.mark.asyncio
async def test_revocada_antes_del_sello_no_se_renueva(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    """Mutación: ignorar `REVOKED` -> se renovaría una cadena que ningún validador daría por buena."""
    ca, cert, doc = await _escenario(monkeypatch, tmp_path)
    pki.servir(
        monkeypatch,
        ocsp=lambda pet: pki.respuesta_ocsp(
            cert, ca, nonce=pki.nonce_de(pet), estado="revoked",
            revocado_en=datetime.now(UTC) - timedelta(days=1),
        ),
    )
    with pytest.raises(tsa.TsaRevocadaError):
        renovar_archive_timestamp(doc.signed_xml)


@pytest.mark.asyncio
async def test_revocada_despues_del_sello_si_se_renueva(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    """El sello anterior valió cuando se hizo: una revocación POSTERIOR no lo invalida (EN 319 102-1)."""
    ca, cert, doc = await _escenario(monkeypatch, tmp_path)
    pki.servir(
        monkeypatch,
        ocsp=lambda pet: pki.respuesta_ocsp(
            cert, ca, nonce=pki.nonce_de(pet), estado="revoked",
            revocado_en=datetime.now(UTC) + timedelta(seconds=30),
        ),
    )
    assert renovar_archive_timestamp(doc.signed_xml).revocacion_tsa_previa == "ocsp"


@pytest.mark.asyncio
@pytest.mark.parametrize("caso", ["unknown", "nonce_distinto", "vieja_sin_nonce", "sha1", "red_caida", "garbage"])
async def test_ocsp_inutilizable_no_se_renueva(monkeypatch: pytest.MonkeyPatch, tmp_path, caso: str) -> None:  # noqa: ANN001
    ca, cert, doc = await _escenario(monkeypatch, tmp_path)
    ahora = datetime.now(UTC)

    def _resp(pet: bytes):  # noqa: ANN202
        n = pki.nonce_de(pet)
        if caso == "unknown":
            return pki.respuesta_ocsp(cert, ca, nonce=n, estado="unknown")
        if caso == "nonce_distinto":
            return pki.respuesta_ocsp(cert, ca, nonce=b"\x01" * 32)
        if caso == "vieja_sin_nonce":
            v = ahora - timedelta(hours=1)
            return pki.respuesta_ocsp(cert, ca, produced_at=v, this_update=v, next_update=v + timedelta(days=2))
        if caso == "sha1":
            return pki.respuesta_ocsp(cert, ca, nonce=n, hash_firma=hashes.SHA1())  # noqa: S303
        if caso == "red_caida":
            import httpx

            return httpx.ConnectError("caída")
        return b"no es OCSP"

    pki.servir(monkeypatch, ocsp=_resp)
    with pytest.raises(tsa.RevocacionTsaNoDisponibleError):
        renovar_archive_timestamp(doc.signed_xml)


@pytest.mark.asyncio
async def test_ocsp_producida_antes_del_sello_previo_no_vale(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    """Fresca respecto de ahora, pero anterior al sello cuyo certificado cubre: un validador tercero la
    rechazaría. Mutación: quitar el `no_anterior_a` del OCSP -> se renueva en vano."""
    ca, cert, doc = await _escenario(monkeypatch, tmp_path)
    gen = _gen_del_ultimo_ats(doc.signed_xml)
    antes = gen - timedelta(seconds=3)
    pki.servir(
        monkeypatch,
        ocsp=lambda pet: pki.respuesta_ocsp(cert, ca, nonce=pki.nonce_de(pet), produced_at=antes, this_update=antes),
    )
    with pytest.raises(tsa.RevocacionTsaNoDisponibleError, match="anterior_al_sello_previo"):
        renovar_archive_timestamp(doc.signed_xml)


@pytest.mark.asyncio
async def test_ni_crl_ni_ocsp_no_se_renueva(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    ca, cert, doc = await _escenario(monkeypatch, tmp_path, ocsp_url=None)
    pedidas = pki.servir(monkeypatch)
    with pytest.raises(tsa.RevocacionTsaNoDisponibleError, match="sin_crl_distribution_point"):
        renovar_archive_timestamp(doc.signed_xml)
    assert pedidas.get == [] and pedidas.post == []


@pytest.mark.asyncio
async def test_con_crl_y_ocsp_manda_la_crl(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    """La prioridad es la CRL: lo que ya funcionaba no cambia, y no se pregunta de más a la CA."""
    ca, cert, doc = await _escenario(monkeypatch, tmp_path, cdp=pki.URL_CRL)
    pedidas = pki.servir(
        monkeypatch, crl=pki.crl_de(ca),
        ocsp=lambda pet: pki.respuesta_ocsp(cert, ca, nonce=pki.nonce_de(pet)),
    )
    r = renovar_archive_timestamp(doc.signed_xml)
    assert r.revocacion_tsa_previa == "crl" and r.tsa_revocacion_fuente == "crl_ocsp"
    assert pedidas.get == [pki.URL_CRL] and pedidas.post == []


@pytest.mark.asyncio
async def test_si_la_crl_falla_se_cae_al_ocsp(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    import httpx

    ca, cert, doc = await _escenario(monkeypatch, tmp_path, cdp=pki.URL_CRL)
    pedidas = pki.servir(
        monkeypatch, crl=httpx.ConnectError("CRL caída"),
        ocsp=lambda pet: pki.respuesta_ocsp(cert, ca, nonce=pki.nonce_de(pet)),
    )
    assert renovar_archive_timestamp(doc.signed_xml).revocacion_tsa_previa == "ocsp"
    assert len(pedidas.get) == 1 and len(pedidas.post) == 1


@pytest.mark.asyncio
async def test_el_kill_switch_deja_el_comportamiento_de_antes(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    ca, cert, doc = await _escenario(monkeypatch, tmp_path)
    pedidas = pki.servir(monkeypatch, ocsp=lambda pet: pki.respuesta_ocsp(cert, ca, nonce=pki.nonce_de(pet)))
    monkeypatch.setattr(tsa.settings, "tsa_ocsp_enabled", False)
    with pytest.raises(tsa.RevocacionTsaNoDisponibleError, match="tsa_ocsp_deshabilitado"):
        renovar_archive_timestamp(doc.signed_xml)
    assert pedidas.post == []


# ---------------------------------------------------------------------------
# Lo que el certificado anuncia, sin red
# ---------------------------------------------------------------------------


def test_fuente_revocacion_de_lee_las_extensiones(monkeypatch: pytest.MonkeyPatch) -> None:
    ca = pki.ca_nueva()
    _k, solo_crl = pki.tsa_emitida(ca, cdp=pki.URL_CRL)
    _k, solo_ocsp = pki.tsa_emitida(ca, ocsp_url=pki.URL_OCSP)
    _k, ambas = pki.tsa_emitida(ca, cdp=pki.URL_CRL, ocsp_url=pki.URL_OCSP)
    _k, ninguna = pki.tsa_emitida(ca)
    assert tsa.fuente_revocacion_de(ca[1]) == "autofirmada"
    assert tsa.fuente_revocacion_de(solo_crl) == "crl"
    assert tsa.fuente_revocacion_de(solo_ocsp) == "ocsp"
    assert tsa.fuente_revocacion_de(ambas) == "crl_ocsp"
    assert tsa.fuente_revocacion_de(ninguna) == "ninguna"
    monkeypatch.setattr(tsa.settings, "tsa_ocsp_enabled", False)
    assert tsa.fuente_revocacion_de(ambas) == "crl" and tsa.fuente_revocacion_de(solo_ocsp) == "ninguna"


def test_cadena_not_after_es_el_primero_que_caduca(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    corta = pki.ca_nueva("ca-corta", dias=40)
    _k, cert = pki.tsa_emitida(corta, dias=365)
    assert tsa.cadena_not_after(cert, [corta[1]]) == corta[1].not_valid_after_utc
    assert tsa.cadena_not_after(cert, []) is None  # CA no localizada
    pki.declarar_ca(monkeypatch, tmp_path, corta)  # ...salvo que esté configurada
    assert tsa.cadena_not_after(cert, []) == corta[1].not_valid_after_utc
    assert tsa.cadena_not_after(corta[1], [corta[1]]) is None  # autofirmada

