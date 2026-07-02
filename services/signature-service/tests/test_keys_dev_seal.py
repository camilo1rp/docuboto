# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de ``app.core.keys.load_seal`` (RT-16): kill-switch del fallback `_dev/`.

Media: sin este flag, un tenant sin sello propio montado firmaba silenciosamente
con el par de desarrollo COMPARTIDO entre todos los tenants (aislamiento
criptográfico roto). Ver la memoria del proyecto / hallazgo de la auditoría E06 Inc.1.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.keys import SealNotFoundError, load_seal


def _write_pair(dir_path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "seal.crt").write_bytes(b"fake-cert")
    (dir_path / "seal.key").write_bytes(b"fake-key")


def test_load_seal_uses_tenant_pair_when_present(tmp_path, monkeypatch) -> None:
    """Con sello propio montado, nunca se toca `_dev/` (independiente del flag)."""
    monkeypatch.setattr(settings, "signer_cert_dir", str(tmp_path))
    monkeypatch.setattr(settings, "signer_allow_dev_seal", False)
    _write_pair(tmp_path / "acme")

    ref = load_seal("acme")
    assert ref.cert_pem == b"fake-cert"
    assert ref.key_pem == b"fake-key"


def test_load_seal_dev_fallback_disabled_by_default_fails_closed(tmp_path, monkeypatch) -> None:
    """Sin sello propio y `signer_allow_dev_seal=False` (default): SealNotFoundError,
    NUNCA cae al par `_dev/` compartido — aunque exista montado."""
    monkeypatch.setattr(settings, "signer_cert_dir", str(tmp_path))
    monkeypatch.setattr(settings, "signer_allow_dev_seal", False)
    _write_pair(tmp_path / "_dev")

    with pytest.raises(SealNotFoundError):
        load_seal("acme")


def test_load_seal_dev_fallback_enabled_explicitly(tmp_path, monkeypatch) -> None:
    """Con el kill-switch activado explícitamente, sí cae al par `_dev/` compartido
    (comportamiento de desarrollo, `docker-compose.yml` lo activa por defecto)."""
    monkeypatch.setattr(settings, "signer_cert_dir", str(tmp_path))
    monkeypatch.setattr(settings, "signer_allow_dev_seal", True)
    _write_pair(tmp_path / "_dev")

    ref = load_seal("acme")
    assert ref.cert_pem == b"fake-cert"
    assert ref.acreditado is False


def test_load_seal_no_pair_anywhere_fails_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "signer_cert_dir", str(tmp_path))
    monkeypatch.setattr(settings, "signer_allow_dev_seal", True)

    with pytest.raises(SealNotFoundError):
        load_seal("acme")
