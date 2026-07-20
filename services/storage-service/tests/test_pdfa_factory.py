# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de `app.services.pdfa.factory` (E10 Increment C2b): resolución del
binario `VERAPDF_BINARY` y wiring de `SubprocessVeraPdfValidator`.

Complementa `test_aip_pdfa.py::test_get_pdfa_validator_falls_back_to_stub_when_verapdf_requested`
(que cubre "sin `verapdf_binary` en absoluto") con el resto de la matriz:
ruta relativa, ruta inexistente, ruta no ejecutable, directorio en vez de
archivo, y el wiring exitoso de los parámetros de tuning
(`verapdf_timeout_seconds`/`verapdf_max_concurrency`/`verapdf_jvm_max_heap_mb`)
hacia `SubprocessVeraPdfValidator`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from app.services.pdfa.factory import _resolve_verapdf_binary, get_pdfa_validator
from app.services.pdfa.stub import StubPdfaValidator
from app.services.pdfa.subprocess_verapdf import SubprocessVeraPdfValidator


class _FakeSettings:
    """Duck-type mínimo de `Settings` para la factory -- solo declara los
    atributos que cada test necesita, igual que haría un despliegue legacy
    sin este incremento (ver comentario de `getattr` en la propia factory)."""

    def __init__(self, *, pdfa_validator: str = "verapdf", **overrides) -> None:
        self.pdfa_validator = pdfa_validator
        for key, value in overrides.items():
            setattr(self, key, value)


def _make_executable(path: Path, *, executable: bool = True) -> Path:
    path.write_text("#!/bin/sh\necho fake-verapdf\n")
    path.chmod(0o755 if executable else 0o644)
    return path


# --------------------------------------------------------------------------
# `_resolve_verapdf_binary`: isabs + isfile + X_OK.
# --------------------------------------------------------------------------


def test_resolve_returns_none_when_binary_not_configured() -> None:
    assert _resolve_verapdf_binary(_FakeSettings()) is None


def test_resolve_returns_none_when_attribute_entirely_absent() -> None:
    """Despliegues/tests que no declaran `verapdf_binary` -- `getattr` con
    default, jamás `AttributeError`."""

    class _NoBinaryAttr:
        pdfa_validator = "verapdf"

    assert _resolve_verapdf_binary(_NoBinaryAttr()) is None


def test_resolve_rejects_relative_path() -> None:
    settings = _FakeSettings(verapdf_binary="bin/verapdf")
    assert _resolve_verapdf_binary(settings) is None


def test_resolve_rejects_nonexistent_absolute_path(tmp_path: Path) -> None:
    ghost = tmp_path / "no-existe" / "verapdf"
    settings = _FakeSettings(verapdf_binary=str(ghost))
    assert _resolve_verapdf_binary(settings) is None


def test_resolve_rejects_non_executable_file(tmp_path: Path) -> None:
    f = _make_executable(tmp_path / "verapdf", executable=False)
    settings = _FakeSettings(verapdf_binary=str(f))
    assert _resolve_verapdf_binary(settings) is None


def test_resolve_rejects_directory_even_if_executable_bit_set(tmp_path: Path) -> None:
    d = tmp_path / "verapdf-dir"
    d.mkdir()
    os.chmod(d, 0o755)  # "ejecutable" para os.access, pero NO es un archivo
    settings = _FakeSettings(verapdf_binary=str(d))
    assert _resolve_verapdf_binary(settings) is None


def test_resolve_accepts_absolute_executable_file(tmp_path: Path) -> None:
    f = _make_executable(tmp_path / "verapdf")
    settings = _FakeSettings(verapdf_binary=str(f))
    assert _resolve_verapdf_binary(settings) == str(f)


# --------------------------------------------------------------------------
# `get_pdfa_validator`: degradación honesta a stub, sin crash.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pdfa_validator_degrades_to_stub_for_relative_path() -> None:
    settings = _FakeSettings(verapdf_binary="verapdf")
    validator = get_pdfa_validator(settings)

    assert isinstance(validator, StubPdfaValidator)
    result = await validator.validate("/tmp/cualquiera.pdf", profile="2b")
    assert result.outcome == "not_evaluated"


@pytest.mark.asyncio
async def test_get_pdfa_validator_degrades_to_stub_for_nonexistent_path(tmp_path: Path) -> None:
    settings = _FakeSettings(verapdf_binary=str(tmp_path / "no-existe" / "verapdf"))
    validator = get_pdfa_validator(settings)

    assert isinstance(validator, StubPdfaValidator)


@pytest.mark.asyncio
async def test_get_pdfa_validator_degrades_to_stub_for_non_executable(tmp_path: Path) -> None:
    f = _make_executable(tmp_path / "verapdf", executable=False)
    settings = _FakeSettings(verapdf_binary=str(f))
    validator = get_pdfa_validator(settings)

    assert isinstance(validator, StubPdfaValidator)


def test_get_pdfa_validator_logs_warning_when_degrading(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="app.services.pdfa.factory")
    settings = _FakeSettings(verapdf_binary="relative/path/verapdf")

    validator = get_pdfa_validator(settings)

    assert isinstance(validator, StubPdfaValidator)
    assert any("VERAPDF_BINARY" in r.message for r in caplog.records)
    assert any("degradando" in r.message for r in caplog.records)


def test_get_pdfa_validator_unknown_validator_name_falls_back_to_stub(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="app.services.pdfa.factory")
    settings = _FakeSettings(pdfa_validator="not-a-real-validator")

    assert isinstance(get_pdfa_validator(settings), StubPdfaValidator)
    assert any("desconocido" in r.message for r in caplog.records)


def test_get_pdfa_validator_stub_explicit_never_touches_binary_resolution(tmp_path: Path) -> None:
    """`pdfa_validator="stub"` no debe ni mirar `verapdf_binary` -- aunque
    apunte a un ejecutable válido, el stub gana porque es lo que se pidió."""
    f = _make_executable(tmp_path / "verapdf")
    settings = _FakeSettings(pdfa_validator="stub", verapdf_binary=str(f))

    assert isinstance(get_pdfa_validator(settings), StubPdfaValidator)


# --------------------------------------------------------------------------
# `get_pdfa_validator`: wiring exitoso hacia `SubprocessVeraPdfValidator`.
# --------------------------------------------------------------------------


def test_get_pdfa_validator_wires_configured_tuning_params(tmp_path: Path) -> None:
    f = _make_executable(tmp_path / "verapdf")
    settings = _FakeSettings(
        verapdf_binary=str(f),
        verapdf_timeout_seconds=42,
        verapdf_max_concurrency=7,
        verapdf_jvm_max_heap_mb=333,
    )

    validator = get_pdfa_validator(settings)

    assert isinstance(validator, SubprocessVeraPdfValidator)
    assert validator._binary == str(f)
    assert validator._timeout_seconds == 42
    assert validator._jvm_max_heap_mb == 333
    # El límite de concurrencia se materializa como el valor de arranque del
    # semáforo asyncio -- verificación de comportamiento, no solo de que se
    # "pasó" el número.
    assert validator._semaphore._value == 7


def test_get_pdfa_validator_defaults_missing_tuning_params(tmp_path: Path) -> None:
    """Settings-like que declara `verapdf_binary` pero NINGÚN parámetro de
    tuning (despliegue legacy parcial) -- la factory usa los defaults
    documentados (60s / concurrency=1 / 512MB) vía `getattr`, sin lanzar."""
    f = _make_executable(tmp_path / "verapdf")
    settings = _FakeSettings(verapdf_binary=str(f))

    validator = get_pdfa_validator(settings)

    assert isinstance(validator, SubprocessVeraPdfValidator)
    assert validator._timeout_seconds == 60
    assert validator._jvm_max_heap_mb == 512
    assert validator._semaphore._value == 1
