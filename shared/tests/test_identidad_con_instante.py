# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La identidad firmada lleva su instante y CADUCA (B9-N3, ADR-035, AUD-SEG #2).

Sin instante firmado, un juego de cabeceras capturado dentro de la red valía para
siempre: reproducido contra archive/workflow/storage evadía una suspensión. El
instante va DENTRO de lo firmado (cambiarlo rompe la firma) y el verificador rechaza
lo viejo y lo futuro más allá del desfase tolerado.
"""

import pytest

from orpycamcp_common import identidad
from orpycamcp_common.identidad import (
    CABECERA_TS,
    CAMPOS_FIRMADOS,
    IdentidadNoAutentica,
    firmar,
    verificar,
)

AHORA = 1_800_000_000
OP = {"metodo": "POST", "ruta": "/api/v1/preservacion/proteger-indice"}


@pytest.fixture(autouse=True)
def con_secreto(monkeypatch):
    monkeypatch.setenv("IDENTITY_SIGNING_SECRET", "secreto-de-pruebas")
    monkeypatch.delenv("IDENTITY_MAX_AGE_SECONDS", raising=False)
    monkeypatch.delenv("IDENTITY_MAX_SKEW_SECONDS", raising=False)


def _identidad():
    return {
        "X-User-Id": "u-1",
        "X-Username": "jperez",
        "X-Tenant-Slug": "demo",
        "X-User-Roles": "viewer",
        "X-Canal": "api",
        "X-Real-Ip": "10.0.0.1",
    }


def test_el_instante_es_un_campo_firmado():
    assert CABECERA_TS in CAMPOS_FIRMADOS


def test_firmar_estampa_el_instante():
    assert firmar(_identidad(), **OP, ahora=AHORA)[CABECERA_TS] == str(AHORA)


def test_una_identidad_reciente_pasa():
    verificar(firmar(_identidad(), **OP, ahora=AHORA), **OP, ahora=AHORA + 5)


def test_una_identidad_capturada_caduca():
    capturada = firmar(_identidad(), **OP, ahora=AHORA)
    verificar(capturada, **OP, ahora=AHORA + 120)  # justo en el límite
    with pytest.raises(IdentidadNoAutentica):
        verificar(capturada, **OP, ahora=AHORA + 121)


def test_una_identidad_del_futuro_lejano_se_rechaza_pero_el_desfase_normal_pasa():
    del_futuro = firmar(_identidad(), **OP, ahora=AHORA + 31)
    with pytest.raises(IdentidadNoAutentica):
        verificar(del_futuro, **OP, ahora=AHORA)
    verificar(firmar(_identidad(), **OP, ahora=AHORA + 30), **OP, ahora=AHORA)


def test_no_se_puede_rejuvenecer_una_identidad_cambiando_el_instante():
    """El ataque: tomar la captura y poner un instante fresco. La firma ya no casa."""
    capturada = firmar(_identidad(), **OP, ahora=AHORA)
    capturada[CABECERA_TS] = str(AHORA + 500)
    with pytest.raises(IdentidadNoAutentica):
        verificar(capturada, **OP, ahora=AHORA + 500)


def test_sin_instante_o_con_basura_no_pasa():
    sin_ts = firmar(_identidad(), **OP, ahora=AHORA)
    del sin_ts[CABECERA_TS]
    with pytest.raises(IdentidadNoAutentica):
        verificar(sin_ts, **OP, ahora=AHORA)
    # Firma legítima sobre un instante que no es un entero: tampoco.
    basura = {**_identidad(), CABECERA_TS: "ayer"}
    basura[identidad.CABECERA_FIRMA] = identidad._firma(basura, "secreto-de-pruebas", **OP)
    with pytest.raises(IdentidadNoAutentica):
        verificar(basura, **OP, ahora=AHORA)


def test_la_ventana_es_configurable(monkeypatch):
    capturada = firmar(_identidad(), **OP, ahora=AHORA)
    monkeypatch.setenv("IDENTITY_MAX_AGE_SECONDS", "10")
    with pytest.raises(IdentidadNoAutentica):
        verificar(capturada, **OP, ahora=AHORA + 11)
    verificar(capturada, **OP, ahora=AHORA + 10)


def test_firmar_dos_veces_no_acumula_instantes():
    una = firmar(_identidad(), **OP, ahora=AHORA)
    dos = firmar(una, **OP, ahora=AHORA + 3)
    assert dos[CABECERA_TS] == str(AHORA + 3)
    verificar(dos, **OP, ahora=AHORA + 3)


def test_los_nombres_de_cabecera_no_distinguen_mayusculas():
    minusculas = {k.lower(): v for k, v in firmar(_identidad(), **OP, ahora=AHORA).items()}
    verificar(minusculas, **OP, ahora=AHORA)


def test_sin_secreto_no_se_verifica_nada(monkeypatch):
    monkeypatch.delenv("IDENTITY_SIGNING_SECRET")
    verificar(_identidad(), **OP, ahora=AHORA)
    assert CABECERA_TS not in firmar(_identidad(), **OP)


# ---------------------------------------------------------------------------
# La identidad va atada al MÉTODO y a la RUTA (ADR-036)
# ---------------------------------------------------------------------------


def test_otra_ruta_no_verifica():
    """El ataque: una identidad legítima de `proteger-indice` reutilizada en otra
    operación del mismo servicio."""
    capturada = firmar(_identidad(), **OP, ahora=AHORA)
    with pytest.raises(IdentidadNoAutentica):
        verificar(
            capturada,
            metodo="POST",
            ruta="/api/v1/preservacion/legal-hold-internal",
            ahora=AHORA,
        )


def test_otro_metodo_no_verifica():
    capturada = firmar(_identidad(), **OP, ahora=AHORA)
    with pytest.raises(IdentidadNoAutentica):
        verificar(capturada, metodo="DELETE", ruta=OP["ruta"], ahora=AHORA)


def test_el_metodo_no_distingue_mayusculas():
    verificar(firmar(_identidad(), metodo="post", ruta=OP["ruta"], ahora=AHORA),
              metodo="POST", ruta=OP["ruta"], ahora=AHORA)


def test_query_no_cuenta():
    """La query no decide qué operación es: firmar sin ella y verificar con ella pasa."""
    capturada = firmar(_identidad(), **OP, ahora=AHORA)
    verificar(capturada, metodo="POST", ruta=OP["ruta"] + "?a=1&b=2#x", ahora=AHORA)


def test_ruta_doblemente_codificada_coincide():
    """El gateway firma lo que Starlette decodificó una vez y el destino decodifica
    otra: `/a/%41` y `/a/A` son la misma ruta canónica."""
    capturada = firmar(_identidad(), metodo="GET", ruta="/a/%41", ahora=AHORA)
    verificar(capturada, metodo="GET", ruta="/a/A", ahora=AHORA)
    capturada = firmar(_identidad(), metodo="GET", ruta="/x/%2541", ahora=AHORA)
    verificar(capturada, metodo="GET", ruta="/x/A", ahora=AHORA)


def test_ruta_que_no_estabiliza_no_verifica():
    anidada = "/a/" + "%25" * 12 + "41"
    with pytest.raises(IdentidadNoAutentica):
        verificar(firmar(_identidad(), **OP, ahora=AHORA), metodo="POST", ruta=anidada, ahora=AHORA)


def test_ruta_sin_barra_inicial_no_verifica():
    with pytest.raises(IdentidadNoAutentica):
        verificar(firmar(_identidad(), **OP, ahora=AHORA), metodo="POST", ruta="api/v1/x", ahora=AHORA)


def test_metodo_y_ruta_son_obligatorios():
    """Sin valor por defecto: quien olvide atarlos falla al escribirlo."""
    with pytest.raises(TypeError):
        firmar(_identidad())  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        verificar(_identidad())  # type: ignore[call-arg]
