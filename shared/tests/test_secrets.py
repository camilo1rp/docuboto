# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Comprobación de arranque de secretos (11.5)."""

from __future__ import annotations

import pytest
from orpycamcp_common.secrets import (
    SecretoNoConfigurado,
    reject_published_values,
    require_secrets,
)


def test_secreto_definido_pasa():
    require_secrets({"SIGNATURE_SECRET": "8f3a1c...-real"}, servicio="signature-service")


def test_secreto_vacio_aborta():
    with pytest.raises(SecretoNoConfigurado) as exc:
        require_secrets({"SIGNATURE_SECRET": ""}, servicio="signature-service")
    assert "SIGNATURE_SECRET" in str(exc.value)
    assert "signature-service" in str(exc.value)


def test_solo_espacios_cuenta_como_vacio():
    """`SIGNATURE_SECRET=" "` en un `.env` es un olvido, no una configuración."""
    with pytest.raises(SecretoNoConfigurado):
        require_secrets({"SIGNATURE_SECRET": "   "}, servicio="signature-service")


def test_default_publicado_del_repositorio_aborta():
    """El modo de fallo real: heredar el valor que está en GitHub."""
    with pytest.raises(SecretoNoConfigurado):
        require_secrets(
            {"SIGNATURE_SECRET": "change-me-in-production"}, servicio="signature-service"
        )


def test_marcador_de_posicion_insensible_a_mayusculas():
    with pytest.raises(SecretoNoConfigurado):
        require_secrets({"SIGNATURE_SECRET": "CHANGE-ME"}, servicio="signature-service")


def test_credenciales_de_desarrollo_del_proyecto_no_se_prohiben():
    """`docker-compose.yml` local las define a propósito.

    Prohibirlas dejaría el entorno de desarrollo sin arrancar; lo que impide
    heredarlas en producción es el guard `${VAR:?}` del manifiesto de producción.
    """
    require_secrets(
        {"MINIO_ACCESS_KEY": "orpycamcp", "MINIO_SECRET_KEY": "orpycamcp_dev"},
        servicio="storage-service",
    )


def test_el_mensaje_nombra_todos_los_que_faltan_y_no_filtra_valores():
    with pytest.raises(SecretoNoConfigurado) as exc:
        require_secrets(
            {
                "MINIO_ACCESS_KEY": "",
                "MINIO_SECRET_KEY": "change-me",
                "OTRO": "valor-bueno-y-secreto",
            },
            servicio="storage-service",
        )
    mensaje = str(exc.value)
    assert "MINIO_ACCESS_KEY" in mensaje
    assert "MINIO_SECRET_KEY" in mensaje
    # El que sí estaba bien no aparece...
    assert "OTRO" not in mensaje
    # ...y ningún valor recibido acaba en el mensaje: iría a los registros.
    assert "change-me" not in mensaje
    assert "valor-bueno-y-secreto" not in mensaje


# ---------------------------------------------------------------------------
# `reject_published_values`: la mitad que ADMITE la ausencia
# ---------------------------------------------------------------------------


def test_el_secreto_ausente_es_una_postura_valida():
    """El gate D-02 de `/api/v1/tenants` es fail-closed POR ausencia.

    Sin token configurado, `require_internal_token` rechaza todas las llamadas y
    el registro de instituciones queda inalcanzable desde la red. Exigir que
    exista para poder arrancar invierte el modo de fallo: obliga a inventar un
    secreto que nadie eligió, y una puerta cerrada por no tener llave pasa a
    estar abierta con una llave de relleno.
    """
    reject_published_values({"TENANT_INTERNAL_TOKEN": ""}, servicio="tenant-service")
    reject_published_values({"TENANT_INTERNAL_TOKEN": "   "}, servicio="tenant-service")


def test_el_valor_publicado_sigue_abortando_aunque_se_admita_la_ausencia():
    with pytest.raises(SecretoNoConfigurado) as exc:
        reject_published_values(
            {"TENANT_INTERNAL_TOKEN": "change-me-in-production"}, servicio="tenant-service"
        )
    assert "TENANT_INTERNAL_TOKEN" in str(exc.value)
    assert "change-me-in-production" not in str(exc.value)


def test_un_token_de_verdad_pasa():
    reject_published_values(
        {"TENANT_INTERNAL_TOKEN": "orpycamcp_tenant_internal_dev"}, servicio="tenant-service"
    )
