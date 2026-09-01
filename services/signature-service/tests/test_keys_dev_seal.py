# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de ``app.core.keys.load_seal`` (RT-16): kill-switch del fallback `_dev/`.

Media: sin este flag, un tenant sin sello propio montado firmaba silenciosamente
con el par de desarrollo COMPARTIDO entre todos los tenants (aislamiento
criptográfico roto). Ver la memoria del proyecto / hallazgo de la auditoría E06 Inc.1.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.keys import SealNotFoundError, load_seal


def _write_pair(dir_path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "seal.crt").write_bytes(b"fake-cert")
    (dir_path / "seal.key").write_bytes(b"fake-key")


def test_load_seal_uses_tenant_pair_when_present(tmp_path, monkeypatch) -> None:
    """Con sello propio montado, nunca se toca `_dev/` (independiente del flag)."""
    monkeypatch.setattr(settings, "signer_cert_dir", str(tmp_path))
    monkeypatch.setattr(settings, "signer_allow_dev_seal", False)
    _write_pair(tmp_path / "acme")

    ref = load_seal("acme")
    assert ref.cert_pem == b"fake-cert"
    assert ref.key_pem == b"fake-key"


def test_load_seal_dev_fallback_disabled_by_default_fails_closed(tmp_path, monkeypatch) -> None:
    """Sin sello propio y `signer_allow_dev_seal=False` (default): SealNotFoundError,
    NUNCA cae al par `_dev/` compartido — aunque exista montado."""
    monkeypatch.setattr(settings, "signer_cert_dir", str(tmp_path))
    monkeypatch.setattr(settings, "signer_allow_dev_seal", False)
    _write_pair(tmp_path / "_dev")

    with pytest.raises(SealNotFoundError):
        load_seal("acme")


def test_load_seal_dev_fallback_enabled_explicitly(tmp_path, monkeypatch) -> None:
    """Con el kill-switch activado explícitamente, sí cae al par `_dev/` compartido
    (comportamiento de desarrollo, `docker-compose.yml` lo activa por defecto)."""
    monkeypatch.setattr(settings, "signer_cert_dir", str(tmp_path))
    monkeypatch.setattr(settings, "signer_allow_dev_seal", True)
    _write_pair(tmp_path / "_dev")

    ref = load_seal("acme")
    assert ref.cert_pem == b"fake-cert"
    assert ref.acreditado is False


def test_load_seal_no_pair_anywhere_fails_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "signer_cert_dir", str(tmp_path))
    monkeypatch.setattr(settings, "signer_allow_dev_seal", True)

    with pytest.raises(SealNotFoundError):
        load_seal("acme")


# ---------------------------------------------------------------------------
# Declaración de acreditación (ONAC) — `acreditacion.json`
#
# La acreditación dejó de ser deuda del producto: es de cada instalación, porque
# depende del operador de firma que contrate la entidad. Lo que sí es del
# producto es poder DECLARARLA, y que esa declaración no se pueda falsificar
# desde dentro del sistema.
# ---------------------------------------------------------------------------

import json  # noqa: E402


def _declarar(dir_path, contenido) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    texto = contenido if isinstance(contenido, str) else json.dumps(contenido)
    (dir_path / "acreditacion.json").write_text(texto, encoding="utf-8")


def test_sin_descriptor_el_sello_no_esta_acreditado(tmp_path, monkeypatch) -> None:
    """El caso normal no cambia: quien no declara nada, sigue igual que siempre."""
    monkeypatch.setattr(settings, "signer_cert_dir", str(tmp_path))
    monkeypatch.setattr(settings, "signer_allow_dev_seal", False)
    _write_pair(tmp_path / "acme")

    ref = load_seal("acme")
    assert ref.acreditado is False
    assert ref.nivel_conformidad == "xades_b_no_acreditado"


def test_un_sello_propio_puede_declararse_acreditado(tmp_path, monkeypatch) -> None:
    """Lo que hace ejecutable «la acreditación se resuelve en la instalación».

    Sin esto, una entidad montaba el certificado de una CA acreditada ONAC y el
    sistema seguía reportando `xades_b_no_acreditado`: **sub-declaraba la
    conformidad que la entidad pagó**, y el acta y el índice electrónico —que
    llevan ese dato— no servían para invocar el art. 28 de la Ley 527/1999.
    """
    monkeypatch.setattr(settings, "signer_cert_dir", str(tmp_path))
    monkeypatch.setattr(settings, "signer_allow_dev_seal", False)
    _write_pair(tmp_path / "acme")
    _declarar(tmp_path / "acme", {"acreditado": True, "operador": "Operador X"})

    ref = load_seal("acme")
    assert ref.acreditado is True
    assert ref.nivel_conformidad == "xades_b_acreditado"


def test_el_sello_de_desarrollo_no_se_acredita_nunca(tmp_path, monkeypatch) -> None:
    """Aunque alguien cuele el descriptor en `_dev/`.

    El par `_dev/` es COMPARTIDO por todos los tenants sin sello propio:
    acreditarlo no acreditaría a uno, acreditaría a todos a la vez, y con
    material de pruebas. Es el mismo razonamiento que el kill-switch de arriba,
    aplicado a la declaración en vez de a la clave.
    """
    monkeypatch.setattr(settings, "signer_cert_dir", str(tmp_path))
    monkeypatch.setattr(settings, "signer_allow_dev_seal", True)
    _write_pair(tmp_path / "_dev")
    _declarar(tmp_path / "_dev", {"acreditado": True, "operador": "No, gracias"})

    ref = load_seal("sin-sello-propio")
    assert ref.acreditado is False
    assert ref.nivel_conformidad == "xades_b_no_acreditado"


@pytest.mark.parametrize(
    "contenido",
    [
        {"acreditado": "true"},  # cadena, no booleano
        {"acreditado": 1},  # entero
        {"acreditado": "sí"},
        {"operador": "Operador X"},  # sin el campo
        "{no es json",
        [],  # JSON válido pero no un objeto
    ],
)
def test_una_declaracion_dudosa_degrada_a_no_acreditado(tmp_path, monkeypatch, contenido) -> None:
    """Solo el booleano `true` acredita; cualquier otra cosa, no.

    `"true"`, `1` y `"sí"` son exactamente los valores que alguien teclea
    creyendo que activan esto. Aceptarlos convertiría una errata en una
    declaración de conformidad, que es la dirección del error que RF-FIR-15
    prohíbe. Degradar a NO acreditado es la dirección segura: se firma igual, y
    la firma vale lo que de verdad vale.
    """
    monkeypatch.setattr(settings, "signer_cert_dir", str(tmp_path))
    monkeypatch.setattr(settings, "signer_allow_dev_seal", False)
    _write_pair(tmp_path / "acme")
    _declarar(tmp_path / "acme", contenido)

    ref = load_seal("acme")
    assert ref.acreditado is False
    assert ref.nivel_conformidad == "xades_b_no_acreditado"


def test_una_declaracion_dudosa_no_es_muda(tmp_path, monkeypatch, caplog) -> None:
    """Degradar en silencio es la otra mitad del error.

    Quien pagó una acreditación y ve `acreditado=false` tiene que poder
    encontrar el motivo sin leer código: el log nombra el fichero y la causa.
    """
    monkeypatch.setattr(settings, "signer_cert_dir", str(tmp_path))
    monkeypatch.setattr(settings, "signer_allow_dev_seal", False)
    _write_pair(tmp_path / "acme")
    _declarar(tmp_path / "acme", {"acreditado": "true"})

    with caplog.at_level("WARNING"):
        load_seal("acme")
    assert any("acreditacion.json" in r.getMessage() for r in caplog.records), (
        "el aviso debe nombrar el fichero que se ignoró"
    )


def test_la_acreditacion_no_se_mezcla_entre_tenants(tmp_path, monkeypatch) -> None:
    """Se lee del directorio que SIRVIÓ el sello, igual que `ca.crt`/`crl.der`.

    La acreditación es un hecho sobre ESE certificado; leerla de otro sitio la
    ataría al certificado equivocado — que es justo el error que el resto del
    módulo evita para el material CA y el responder OCSP.
    """
    monkeypatch.setattr(settings, "signer_cert_dir", str(tmp_path))
    monkeypatch.setattr(settings, "signer_allow_dev_seal", False)
    _write_pair(tmp_path / "acreditada")
    _declarar(tmp_path / "acreditada", {"acreditado": True})
    _write_pair(tmp_path / "vecina")

    assert load_seal("acreditada").acreditado is True
    assert load_seal("vecina").acreditado is False
