# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Sin firma, una cabecera de identidad es una afirmación, no una identidad.

La identidad viajaba entre el gateway y los servicios como texto plano. Desde
fuera no se puede forjar —el gateway no reenvía esas cabeceras si vienen del
cliente— pero dentro de la red cualquiera que alcance el puerto de un servicio
podía hablarle diciendo ser quien quisiera y de la entidad que quisiera.
"""

import pytest

from orpycamcp_common.identidad import (
    CABECERA_FIRMA,
    IdentidadNoAutentica,
    firmar,
    verificar,
)

_SECRETO = "secreto-de-pruebas"


@pytest.fixture(autouse=True)
def con_secreto(monkeypatch):
    monkeypatch.setenv("IDENTITY_SIGNING_SECRET", _SECRETO)


def _identidad(**kw):
    base = {
        "X-User-Id": "u-1",
        "X-Username": "jperez",
        "X-Tenant-Slug": "demo",
        "X-User-Roles": "viewer",
        "X-Canal": "api",
        "X-Real-Ip": "10.0.0.1",
    }
    base.update(kw)
    return base


def test_la_identidad_del_gateway_se_verifica():
    verificar(firmar(_identidad()))


def test_una_identidad_inventada_no_pasa():
    """El ataque: hablarle directo al servicio afirmando ser quien sea."""
    with pytest.raises(IdentidadNoAutentica):
        verificar(_identidad())


def test_cambiar_la_entidad_invalida_la_firma():
    """`X-Tenant-Slug` decide sobre QUÉ entidad se actúa: está dentro de lo firmado."""
    cabeceras = firmar(_identidad(**{"X-Tenant-Slug": "demo"}))
    cabeceras["X-Tenant-Slug"] = "otra-entidad"

    with pytest.raises(IdentidadNoAutentica):
        verificar(cabeceras)


def test_cambiar_los_roles_invalida_la_firma():
    cabeceras = firmar(_identidad())
    cabeceras["X-User-Roles"] = "admin"

    with pytest.raises(IdentidadNoAutentica):
        verificar(cabeceras)


def test_las_cabeceras_en_minuscula_tambien_valen():
    """El servidor puede entregarlas en minúscula: los nombres de cabecera no
    distinguen mayúsculas y la verificación tampoco puede hacerlo."""
    firmadas = firmar(_identidad())
    verificar({k.lower(): v for k, v in firmadas.items()})


def test_sin_secreto_configurado_no_se_verifica(monkeypatch):
    """Decisión explícita: un despliegue en marcha no se queda sin atender por
    activar esto de golpe. El interruptor es la propia variable."""
    monkeypatch.delenv("IDENTITY_SIGNING_SECRET", raising=False)

    verificar(_identidad())
    assert CABECERA_FIRMA not in firmar(_identidad())
