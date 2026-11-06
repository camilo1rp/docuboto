# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El fragmento `intelligence` no arranca al proveedor (ADR-045 §8).

No va en `include`: Compose lo mezclaría aunque no lo pidas, y `up` exigiría
la red `ppi`. Se suma con `-f`. Sin ese archivo, `app` y knowledge-service no
entran en `ppi` y no reciben URL. Con él, solo se completan esos dos: la red
es externa, la URL no tiene valor por defecto y ningún otro servicio se conecta.
"""

from __future__ import annotations

import re

from tests._compose import COMPOSE_DEV, COMPOSE_PROD, RAIZ, cargar, servicios

PARES = (
    (COMPOSE_DEV, RAIZ / "docker-compose.intelligence.yml"),
    (COMPOSE_PROD, RAIZ / "infra" / "docker-compose.intelligence.yml"),
)

_URL = re.compile(r"\$\{PPI_PROVIDER_URL(?::[?+-])?")


def _redes(servicio: dict) -> set[str]:
    redes = servicio.get("networks") or []
    if isinstance(redes, dict):
        return set(redes)
    return {str(red) for red in redes}


def _texto_entorno(servicio: dict) -> str:
    entorno = servicio.get("environment") or {}
    if isinstance(entorno, dict):
        return "\n".join(f"{k}={v}" for k, v in entorno.items())
    return "\n".join(str(item) for item in entorno)


def url_sin_defecto(texto: str) -> bool:
    """La URL se exige con `:?` y no trae un valor de fábrica."""
    if "${PPI_PROVIDER_URL:?" not in texto:
        return False
    return "PPI_PROVIDER_URL:-" not in texto and "PPI_PROVIDER_URL-" not in texto


def test_hay_un_fragmento_por_manifiesto() -> None:
    for _base, extra in PARES:
        assert extra.is_file(), extra


def test_el_perfil_no_define_servicios_nuevos() -> None:
    for base, extra in PARES:
        propios = set(servicios(base))
        añadidos = servicios(extra)
        assert set(añadidos) == {"app", "knowledge-service"}
        assert set(añadidos) <= propios
        for nombre in ("app", "knowledge-service"):
            servicio = añadidos[nombre]
            assert "image" not in servicio
            assert "build" not in servicio
            assert "profiles" not in servicio
            assert "container_name" not in servicio


def test_el_manifiesto_base_no_incluye_el_fragmento() -> None:
    """Un `include` se mezcla siempre: Compose ignora `profiles` ahí."""
    for base, extra in PARES:
        data = cargar(base)
        assert "include" not in data, base
        texto = base.read_text(encoding="utf-8")
        ruta = extra.relative_to(RAIZ).as_posix()
        assert f"-f {ruta}" in texto
        assert "docker network create --internal ppi" in texto


def test_sin_el_perfil_la_pasarela_no_sale_a_ppi() -> None:
    for base, _extra in PARES:
        for nombre in ("app", "knowledge-service"):
            servicio = servicios(base)[nombre]
            assert "ppi" not in _redes(servicio)
            assert "PPI_PROVIDER_URL" not in _texto_entorno(servicio)


def test_solo_app_y_knowledge_se_conectan_a_ppi() -> None:
    for base, extra in PARES:
        unidos = {**servicios(base), **servicios(extra)}
        en_ppi = sorted(nombre for nombre, svc in unidos.items() if "ppi" in _redes(svc))
        assert en_ppi == ["app", "knowledge-service"]


def test_la_red_ppi_es_externa_y_se_crea_internal() -> None:
    for _base, extra in PARES:
        texto = extra.read_text(encoding="utf-8")
        assert "docker network create --internal ppi" in texto
        red = cargar(extra)["networks"]["ppi"]
        assert red.get("external") is True
        assert red.get("name") == "ppi"
        assert "driver" not in red


def test_la_url_no_tiene_valor_por_defecto() -> None:
    for _base, extra in PARES:
        texto = extra.read_text(encoding="utf-8")
        assert url_sin_defecto(texto)
        assert _URL.findall(texto) == ["${PPI_PROVIDER_URL:?", "${PPI_PROVIDER_URL:?"]


def test_un_defecto_en_la_url_no_pasa() -> None:
    assert url_sin_defecto("PPI_PROVIDER_URL=${PPI_PROVIDER_URL:?falta}")
    assert not url_sin_defecto("PPI_PROVIDER_URL=${PPI_PROVIDER_URL:-http://ejemplo}")
    assert not url_sin_defecto("PPI_PROVIDER_URL=${PPI_PROVIDER_URL}")
