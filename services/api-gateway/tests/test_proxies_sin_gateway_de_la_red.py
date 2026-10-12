# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ARC-B4: en producción el gateway no arranca si confía en la puerta de enlace de la red.

La IP de la puerta de enlace de la red Docker es la que ve el gateway para todo cliente que
llegue por docker-proxy o desde el anfitrión. Si `TRUSTED_PROXIES` la cubre, esa IP se salta
y se toma la entrada de `X-Forwarded-For` que escribió el cliente: IP forjada sellada en
`audit_log`. En desarrollo el valor (172.16.0.0/12) la contiene a propósito y NO se comprueba.

Mutaciones: quitar la comprobación de `app_env`, hacer `exigir_proxies_sin_gateway` un no-op,
o comprobar también en desarrollo (cae `test_en_desarrollo_no_se_comprueba`).
"""

from pathlib import Path

import pytest

from app import main
from app.core.client_ip import exigir_proxies_sin_gateway, gateway_de_la_ruta_por_defecto

GATEWAY = "172.20.0.1"


@pytest.mark.parametrize("proxies", ["172.20.0.0/16", "172.16.0.0/12", "172.20.0.1", "10.0.0.5,172.20.0.0/24"])
def test_confiar_en_el_gateway_de_la_red_impide_arrancar(proxies: str) -> None:
    with pytest.raises(RuntimeError, match="puerta de enlace"):
        exigir_proxies_sin_gateway(proxies, GATEWAY)


@pytest.mark.parametrize("proxies", ["172.20.128.0/17", "10.0.0.5", "", "172.20.128.0/17,10.0.0.5"])
def test_el_rango_de_los_contenedores_si_arranca(proxies: str) -> None:
    exigir_proxies_sin_gateway(proxies, GATEWAY)


def test_un_gateway_ilegible_no_se_ignora() -> None:
    with pytest.raises(RuntimeError, match="GATEWAY_RED_DOCKER_GATEWAY"):
        exigir_proxies_sin_gateway("172.20.0.0/16", "no-es-una-ip")


def test_en_produccion_el_arranque_lo_exige(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.settings, "app_env", "production")
    monkeypatch.setattr(main.settings, "gateway_red_docker_gateway", GATEWAY)
    monkeypatch.setattr(main.settings, "trusted_proxies", "172.20.0.0/16")
    with pytest.raises(RuntimeError, match="puerta de enlace"):
        main._exigir_proxies_de_produccion()
    monkeypatch.setattr(main.settings, "trusted_proxies", "172.20.128.0/17")
    main._exigir_proxies_de_produccion()


def test_en_desarrollo_no_se_comprueba(monkeypatch: pytest.MonkeyPatch) -> None:
    """El valor de desarrollo (172.16.0.0/12) contiene al gateway a propósito."""
    monkeypatch.setattr(main.settings, "app_env", "")
    monkeypatch.setattr(main.settings, "gateway_red_docker_gateway", GATEWAY)
    monkeypatch.setattr(main.settings, "trusted_proxies", "172.16.0.0/12")
    main._exigir_proxies_de_produccion()


def test_si_no_se_declara_se_deriva_de_la_ruta_por_defecto(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tabla = tmp_path / "route"
    tabla.write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
        "eth0\t00000000\t0100FEAC\t0003\t0\t0\t0\t00000000\n"
        "eth0\t0000FEAC\t00000000\t0001\t0\t0\t0\t0000FFFF\n"
    )
    assert gateway_de_la_ruta_por_defecto(str(tabla)) == "172.254.0.1"
    assert gateway_de_la_ruta_por_defecto(str(tmp_path / "no-existe")) is None

    tabla.write_text("Iface\tDestination\tGateway\neth0\t00000000\t0100140A\n")  # 10.20.0.1
    monkeypatch.setattr(main, "gateway_de_la_ruta_por_defecto", lambda: gateway_de_la_ruta_por_defecto(str(tabla)))
    monkeypatch.setattr(main.settings, "app_env", "production")
    monkeypatch.setattr(main.settings, "gateway_red_docker_gateway", "")
    monkeypatch.setattr(main.settings, "trusted_proxies", "10.20.0.0/16")
    with pytest.raises(RuntimeError, match="10.20.0.1"):
        main._exigir_proxies_de_produccion()
