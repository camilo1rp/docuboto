# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de `Settings.preservation_startup_guard` (E10 Increment A, WORM foundation).

Guard de arranque puro (sin BD/MinIO/red): `app.main.lifespan` lo usa para
rechazar el arranque en producción si el modo de retención no es COMPLIANCE
(GOVERNANCE es evadible por un usuario con permiso bypass-governance de MinIO).
No requiere `client`/`client_with_conn` — es lógica de configuración aislada.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings


def test_startup_guard_silent_when_compliance() -> None:
    settings = Settings(preservation_retention_mode="COMPLIANCE")
    assert settings.preservation_startup_guard() == []


def test_startup_guard_warns_when_governance() -> None:
    settings = Settings(preservation_retention_mode="GOVERNANCE")
    warnings = settings.preservation_startup_guard()
    assert len(warnings) == 1
    assert "GOVERNANCE" in warnings[0]
    assert "bypass-governance" in warnings[0]


def test_retention_mode_rejects_unknown_value() -> None:
    """El validador normaliza a mayúsculas y rechaza cualquier valor fuera del
    enum cerrado (espejo de `miniopy_async.commonconfig.{GOVERNANCE,COMPLIANCE}`)."""
    with pytest.raises(ValueError, match="preservation_retention_mode"):
        Settings(preservation_retention_mode="permisivo")


def test_retention_mode_is_case_insensitive() -> None:
    settings = Settings(preservation_retention_mode="compliance")
    assert settings.preservation_retention_mode == "COMPLIANCE"


# AREA-08-02: el validador PDF/A pedido «real» sin binario es un control apagado.


def test_startup_guard_avisa_si_verapdf_no_tiene_binario() -> None:
    settings = Settings(
        app_env="production",
        pdfa_validator="verapdf",
        verapdf_binary="/no/existe/verapdf",
        preservation_pdfa_validation_enabled=True,
    )
    warnings = settings.preservation_startup_guard()
    assert len(warnings) == 1
    assert "VERAPDF_BINARY" in warnings[0]


def test_startup_guard_calla_si_verapdf_tiene_binario(tmp_path) -> None:
    binario = tmp_path / "verapdf"
    binario.write_text("#!/bin/sh\n")
    binario.chmod(0o755)
    settings = Settings(
        pdfa_validator="verapdf",
        verapdf_binary=str(binario),
        preservation_pdfa_validation_enabled=True,
    )
    assert settings.preservation_startup_guard() == []


def test_startup_guard_no_exige_binario_con_la_validacion_apagada() -> None:
    settings = Settings(
        pdfa_validator="verapdf",
        verapdf_binary="/no/existe/verapdf",
        preservation_pdfa_validation_enabled=False,
    )
    assert settings.preservation_startup_guard() == []


# AREA-08-04: un secundario que es el mismo host no es una réplica geográfica.


def test_startup_guard_avisa_si_el_secundario_es_el_primario() -> None:
    settings = Settings(
        minio_endpoint="minio:9000",
        minio_secondary_endpoint="https://MINIO:9000/",
        preservation_replica_enabled=True,
    )
    warnings = settings.preservation_startup_guard()
    assert len(warnings) == 1
    assert "MINIO_SECONDARY_ENDPOINT" in warnings[0]


def test_startup_guard_calla_con_un_secundario_distinto() -> None:
    settings = Settings(
        minio_endpoint="minio:9000",
        minio_secondary_endpoint="otro-sitio:9000",
        preservation_replica_enabled=True,
    )
    assert settings.preservation_startup_guard() == []
