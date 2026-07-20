# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Smoke test de CABLEADO del subprocess veraPDF REAL (E10 Increment C2b).

ALCANCE DELIBERADAMENTE ACOTADO: esto NO es la suite exhaustiva de
`SubprocessVeraPdfValidator` (timeout/killpg/semáforo/cap de salida/env
scrubbeado en detalle, etc.) -- esa la escribe `testing-engineer` por
separado. El único propósito de este archivo es que el subprocess NUNCA
quede como código muerto sin ejercitar en CI: prueba, contra un binario
`fake_verapdf.py` real invocado como PROCESO EXTERNO (no un mock en
memoria), que:

  1. la factory (`get_pdfa_validator`) resuelve `SubprocessVeraPdfValidator`
     -- NO el stub -- cuando `PDFA_VALIDATOR=verapdf` y `VERAPDF_BINARY`
     apunta a un ejecutable real (aquí, el fake de CI);
  2. el plumbing real (create_subprocess_exec, env scrubbeado -- el fake
     JAMÁS ve variables del entorno del padre, solo PATH/JAVA_TOOL_OPTIONS
     --, parseo de JSON) produce `conformant`/`non_conformant` correctos;
  3. un binario que cuelga (`FAKE-VERAPDF-HANG`) NUNCA hace colgar el
     request -- el timeout + killpg del grupo de proceso lo acota.

GATEADO por `VERAPDF_BINARY` (mismo patrón que `RUN_INTEGRATION=1` en
`tests/integration/`): sin esa variable, todo el módulo se salta --
el carril normal (`docker compose run --rm --no-deps storage-service
pytest -q`) no la define, así que este archivo es un no-op ahí. El job de
CI dedicado (`.gitlab-ci.yml::storage-verapdf-subprocess`) SÍ la define,
apuntando a `tests/fakes/fake_verapdf.py`.
"""

from __future__ import annotations

import os
import time

import pytest

from app.core.config import settings as app_settings
from app.services.pdfa import SubprocessVeraPdfValidator, get_pdfa_validator

_VERAPDF_BINARY = os.environ.get("VERAPDF_BINARY", "")
_BINARY_USABLE = bool(_VERAPDF_BINARY) and os.path.isabs(_VERAPDF_BINARY) and os.access(_VERAPDF_BINARY, os.X_OK)

pytestmark = pytest.mark.skipif(
    not _BINARY_USABLE,
    reason=(
        "requiere VERAPDF_BINARY (ruta absoluta ejecutable) -- gateado igual que "
        "RUN_INTEGRATION=1 en tests/integration/. Ver .gitlab-ci.yml::"
        "storage-verapdf-subprocess, que la fija al fake de tests/fakes/fake_verapdf.py."
    ),
)


def test_settings_wire_pdfa_validator_from_env() -> None:
    """Sanity del job de CI en sí: si esto falla, el job está mal configurado
    (falta exportar PDFA_VALIDATOR=verapdf/VERAPDF_BINARY antes de pytest),
    no un bug del validador."""
    assert app_settings.pdfa_validator == "verapdf"
    assert app_settings.verapdf_binary == _VERAPDF_BINARY


def test_factory_resolves_real_subprocess_validator_not_stub() -> None:
    """Con el binario configurado, la factory NO debe degradar a stub."""
    validator = get_pdfa_validator(app_settings)
    assert isinstance(validator, SubprocessVeraPdfValidator)


@pytest.mark.asyncio
async def test_conformant_via_real_subprocess(tmp_path) -> None:
    validator = get_pdfa_validator(app_settings)
    pdf_path = tmp_path / "conformant.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 contenido cualquiera, el fake responde compliant por default")

    result = await validator.validate(str(pdf_path), profile="2b")

    assert result.outcome == "conformant"
    assert result.profile == "2b"
    assert "veraPDF" in result.validator_agent


@pytest.mark.asyncio
async def test_non_conformant_via_real_subprocess(tmp_path) -> None:
    validator = get_pdfa_validator(app_settings)
    pdf_path = tmp_path / "non_conformant.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 FAKE-VERAPDF-NONCONFORMANT")

    result = await validator.validate(str(pdf_path), profile="2b")

    assert result.outcome == "non_conformant"


@pytest.mark.asyncio
async def test_hung_subprocess_never_blocks_beyond_configured_timeout(tmp_path) -> None:
    """El punto más crítico del modelo de seguridad: un binario adversarial
    (aquí, simulado) que nunca termina NUNCA debe colgar el request --
    timeout duro + killpg del grupo de proceso lo acotan."""
    pdf_path = tmp_path / "hang.pdf"
    pdf_path.write_bytes(b"FAKE-VERAPDF-HANG")

    hung_validator = SubprocessVeraPdfValidator(
        binary=_VERAPDF_BINARY,
        timeout_seconds=2,
        max_concurrency=1,
        jvm_max_heap_mb=256,
    )

    started = time.monotonic()
    result = await hung_validator.validate(str(pdf_path), profile="2b")
    elapsed = time.monotonic() - started

    assert result.outcome == "not_evaluated"
    # Generoso a propósito (CI puede ser lento) -- lo que se prueba es que
    # NO es indefinido, no que sea rápido.
    assert elapsed < 30, f"el subprocess colgado tardó {elapsed:.1f}s en resolverse (se esperaba <30s)"
