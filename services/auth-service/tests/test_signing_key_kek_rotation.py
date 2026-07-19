# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Llavero de KEKs para la rotación de custodia (E17/F4) — resolución y
fail-closed del config. Puro (sin BD): el re-wrap end-to-end se prueba en
``tests/integration/test_signing_key_kek_rotation.py`` (Postgres real)."""

from __future__ import annotations

import base64

import pytest

from app.core.config import Settings

_V1 = base64.b64encode(b"\x11" * 32).decode()
_V2 = base64.b64encode(b"\x22" * 32).decode()


def _settings(**kw) -> Settings:
    base = {"signing_key_encryption_key": _V1}
    base.update(kw)
    return Settings(**base)


def test_keyring_v1_only_from_legacy_field() -> None:
    ring = _settings().signing_key_keyring()
    assert set(ring) == {1}
    assert ring[1] == b"\x11" * 32


def test_keyring_v1_plus_v2_from_json() -> None:
    ring = _settings(signing_key_encryption_keys={2: _V2}).signing_key_keyring()
    assert set(ring) == {1, 2}
    assert ring[1] == b"\x11" * 32
    assert ring[2] == b"\x22" * 32


def test_active_key_default_is_v1() -> None:
    version, key = _settings().signing_key_active_key()
    assert version == 1
    assert key == b"\x11" * 32


def test_active_key_selects_configured_active_version() -> None:
    s = _settings(signing_key_encryption_keys={2: _V2}, signing_key_active_version=2)
    version, key = s.signing_key_active_key()
    assert version == 2
    assert key == b"\x22" * 32


def test_for_version_returns_the_row_version_key() -> None:
    s = _settings(signing_key_encryption_keys={2: _V2}, signing_key_active_version=2)
    assert s.signing_key_for_version(1) == b"\x11" * 32
    assert s.signing_key_for_version(2) == b"\x22" * 32


def test_for_version_missing_is_fail_closed() -> None:
    with pytest.raises(RuntimeError, match="versión 3 no cargada"):
        _settings().signing_key_for_version(3)


def test_active_version_without_kek_fails_at_use() -> None:
    # activa=2 pero sin KEK v2 en el llavero -> fail-closed al resolver.
    s = _settings(signing_key_active_version=2)
    with pytest.raises(RuntimeError, match="versión 2 no cargada"):
        s.signing_key_active_key()


def test_version_1_in_json_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="reservada al campo"):
        _settings(signing_key_encryption_keys={1: _V2}).signing_key_keyring()


def test_bad_base64_in_keyring_is_fail_closed() -> None:
    with pytest.raises(RuntimeError, match=r"signing_key_encryption_keys\[2\] no es base64"):
        _settings(signing_key_encryption_keys={2: "!!!notbase64!!!"}).signing_key_keyring()


def test_wrong_length_in_keyring_is_fail_closed() -> None:
    short = base64.b64encode(b"\x22" * 16).decode()  # 16 bytes, no 32
    with pytest.raises(RuntimeError, match="32 bytes"):
        _settings(signing_key_encryption_keys={2: short}).signing_key_keyring()


def test_legacy_bytes_still_works_and_equals_v1() -> None:
    # Retrocompat: el método legacy sigue devolviendo exactamente la v1.
    s = _settings(signing_key_encryption_keys={2: _V2}, signing_key_active_version=2)
    assert s.signing_key_encryption_key_bytes() == s.signing_key_keyring()[1]


def test_purge_after_days_default_is_30() -> None:
    assert _settings().signing_key_purge_after_days == 30
