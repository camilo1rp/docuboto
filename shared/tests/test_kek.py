# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: AGPL v3 o posterior. Se entrega SIN GARANTÍA ALGUNA.

"""`orpycamcp_common.kek`: el esquema de custodia común a auth y notification."""

from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from orpycamcp_common import kek

K1 = base64.b64encode(b"\x01" * 32).decode()
K2 = base64.b64encode(b"\x02" * 32).decode()


def test_seal_unseal_ida_y_vuelta_y_formato_aesgcm_estandar() -> None:
    clave = b"\x01" * 32
    ct, nonce = kek.seal(b"hola", key=clave, aad=b"a")
    assert len(nonce) == 12 and b"hola" not in ct
    assert kek.unseal(ct, nonce=nonce, key=clave, aad=b"a") == b"hola"
    # Compatibilidad byte a byte con lo que auth-service ya tenía en reposo:
    # AES-256-GCM estándar, nonce aparte, AAD.
    assert AESGCM(clave).decrypt(nonce, ct, b"a") == b"hola"


def test_aad_distinto_o_clave_distinta_no_descifran() -> None:
    ct, nonce = kek.seal(b"hola", key=b"\x01" * 32, aad=b"a")
    with pytest.raises(InvalidTag):
        kek.unseal(ct, nonce=nonce, key=b"\x01" * 32, aad=b"b")
    with pytest.raises(InvalidTag):
        kek.unseal(ct, nonce=nonce, key=b"\x02" * 32, aad=b"a")


def test_nonce_nuevo_por_cifrado() -> None:
    a = kek.seal(b"x", key=b"\x01" * 32, aad=b"")
    b = kek.seal(b"x", key=b"\x01" * 32, aad=b"")
    assert a[1] != b[1]


@pytest.mark.parametrize("valor,msg", [("###", "base64"), ("AAAA", "32 bytes"), ("", "32 bytes")])
def test_decode_aes256_fail_closed_sin_filtrar_el_valor(valor: str, msg: str) -> None:
    with pytest.raises(RuntimeError, match=msg) as exc:
        kek.decode_aes256(valor, "MI_VAR")
    assert "MI_VAR" in str(exc.value)
    if valor:
        assert valor not in str(exc.value).replace("MI_VAR", "")


def test_keyring_v1_mas_extras_y_reglas() -> None:
    ring = kek.build_keyring(K1, {2: K2}, v1_label="K", extra_label="KS")
    assert set(ring) == {1, 2} and ring[2] == b"\x02" * 32
    assert kek.key_for_version(ring, 2) == b"\x02" * 32
    with pytest.raises(RuntimeError, match="no cargada"):
        kek.key_for_version(ring, 3)
    with pytest.raises(RuntimeError, match="no configurada"):
        kek.build_keyring("", {}, v1_label="K", extra_label="KS")
    with pytest.raises(RuntimeError, match="reservada"):
        kek.build_keyring(K1, {1: K2}, v1_label="K", extra_label="KS")
    with pytest.raises(RuntimeError, match=r"KS\[2\]"):
        kek.build_keyring(K1, {2: "AAAA"}, v1_label="K", extra_label="KS")
