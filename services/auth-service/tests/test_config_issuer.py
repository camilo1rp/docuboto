# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Unit tests del contrato de issuer (expected_issuer + fail-fast visible)."""

import pytest

from app.core.config import Settings


def test_expected_issuer_fallback_to_keycloak_url() -> None:
    """Sin override, expected_issuer se deriva de keycloak_url."""
    s = Settings.model_construct(
        keycloak_url="http://keycloak:8080",
        keycloak_realm="orpycamcp",
        keycloak_issuer=None,
    )
    assert s.expected_issuer == "http://keycloak:8080/realms/orpycamcp"


def test_expected_issuer_override_wins() -> None:
    """Con KEYCLOAK_ISSUER definido, gana sobre la URL interna."""
    s = Settings.model_construct(
        keycloak_url="http://keycloak:8080",
        keycloak_realm="orpycamcp",
        keycloak_issuer="http://localhost:19180/realms/orpycamcp",
    )
    assert s.expected_issuer == "http://localhost:19180/realms/orpycamcp"


def test_issuer_warnings_fire_on_internal_host_without_override() -> None:
    """Host interno de Docker y sin KEYCLOAK_ISSUER → aviso fail-fast."""
    s = Settings.model_construct(
        keycloak_url="http://keycloak:8080",
        keycloak_realm="orpycamcp",
        keycloak_issuer=None,
    )
    warnings = s.issuer_config_warnings()
    assert len(warnings) == 1
    assert "KEYCLOAK_ISSUER" in warnings[0]


def test_issuer_warnings_silent_with_override() -> None:
    """Split dev/prod con override explícito → sin aviso."""
    s = Settings.model_construct(
        keycloak_url="http://keycloak:8080",
        keycloak_realm="orpycamcp",
        keycloak_issuer="http://localhost:19180/realms/orpycamcp",
    )
    assert s.issuer_config_warnings() == []


def test_issuer_warnings_silent_on_public_fqdn() -> None:
    """Prod single-host (FQDN público) sin override no es error → sin aviso."""
    s = Settings.model_construct(
        keycloak_url="https://keycloak.example.com",
        keycloak_realm="orpycamcp",
        keycloak_issuer=None,
    )
    assert s.issuer_config_warnings() == []


@pytest.mark.parametrize("campo,valor", [
    ("signing_key_purge_after_days", 0),
    ("signing_key_purge_after_days", -1),
    ("signing_key_maintenance_interval_seconds", 0),
    ("signing_key_maintenance_interval_seconds", 59),
])
def test_ventanas_del_mantenimiento_de_claves_no_arrancan_en_cero(campo: str, valor: int) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(**{campo: valor})
