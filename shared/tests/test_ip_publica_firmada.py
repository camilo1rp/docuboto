# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""IP firmada de una ruta pública (B2, ADR-031): ligada a ruta e instante."""

import pytest
from orpycamcp_common import identidad


@pytest.fixture(autouse=True)
def _secreto(monkeypatch):
    monkeypatch.setenv("IDENTITY_SIGNING_SECRET", "s3cr3to-de-prueba")


RUTA = "/api/v1/public/demo/ventanilla/radicar"


def test_firma_y_verifica():
    h = identidad.firmar_ip_publica("203.0.113.9", RUTA, 1000)
    assert identidad.ip_publica_verificada(h, RUTA, ahora=1010) == "203.0.113.9"


def test_otra_ruta_no_sirve():
    h = identidad.firmar_ip_publica("203.0.113.9", RUTA, 1000)
    assert identidad.ip_publica_verificada(h, RUTA + "x", ahora=1010) is None


def test_ip_cambiada_no_sirve():
    h = identidad.firmar_ip_publica("203.0.113.9", RUTA, 1000)
    h["X-Real-Ip"] = "198.51.100.1"
    assert identidad.ip_publica_verificada(h, RUTA, ahora=1010) is None


def test_vencida_no_sirve():
    h = identidad.firmar_ip_publica("203.0.113.9", RUTA, 1000)
    assert identidad.ip_publica_verificada(h, RUTA, ahora=1000 + 121) is None
    assert identidad.ip_publica_verificada(h, RUTA, ahora=1000 - 121) is None


def test_ts_manipulado_no_sirve():
    h = identidad.firmar_ip_publica("203.0.113.9", RUTA, 1000)
    h["X-Real-Ip-Ts"] = "1005"
    assert identidad.ip_publica_verificada(h, RUTA, ahora=1010) is None


def test_sin_firma_o_x_forwarded_for_no_cuenta():
    assert identidad.ip_publica_verificada({"X-Real-Ip": "1.2.3.4"}, RUTA) is None
    assert (
        identidad.ip_publica_verificada({"X-Forwarded-For": "1.2.3.4"}, RUTA) is None
    )


def test_sin_secreto_no_se_emite_ni_se_verifica(monkeypatch):
    h = identidad.firmar_ip_publica("203.0.113.9", RUTA, 1000)
    monkeypatch.delenv("IDENTITY_SIGNING_SECRET")
    assert identidad.firmar_ip_publica("203.0.113.9", RUTA, 1000) == {}
    assert identidad.ip_publica_verificada(h, RUTA, ahora=1010) is None


def test_cabeceras_en_minuscula():
    h = {k.lower(): v for k, v in identidad.firmar_ip_publica("203.0.113.9", RUTA, 1000).items()}
    assert identidad.ip_publica_verificada(h, RUTA, ahora=1000) == "203.0.113.9"
