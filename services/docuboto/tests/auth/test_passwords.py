# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La política de contraseñas que antes aplicaba el realm (ADR-048): pura, sin base."""

from __future__ import annotations

import pytest

from auth.core import passwords
from docuboto.config import auth as settings


def test_la_contrasena_corta_no_pasa() -> None:
    with pytest.raises(passwords.PoliticaIncumplida) as e:
        passwords.exigir_politica("x" * (settings.password_min_length - 1))
    assert e.value.codigo == "password_too_short"
    passwords.exigir_politica("x" * settings.password_min_length)


def test_la_contrasena_enorme_no_pasa() -> None:
    with pytest.raises(passwords.PoliticaIncumplida) as e:
        passwords.exigir_politica("x" * (passwords.LONGITUD_MAXIMA + 1))
    assert e.value.codigo == "password_too_long"


def test_no_se_repite_una_del_historial() -> None:
    anteriores = [passwords.cifrar("una-clave-larga-1"), passwords.cifrar("una-clave-larga-2")]
    with pytest.raises(passwords.PoliticaIncumplida) as e:
        passwords.exigir_politica("una-clave-larga-2", anteriores=anteriores)
    assert e.value.codigo == "password_reused"
    passwords.exigir_politica("una-clave-larga-3", anteriores=anteriores)


def test_argon2id_y_sin_hash_nunca_coincide() -> None:
    h = passwords.cifrar("una-clave-larga")
    assert h.startswith("$argon2id$")
    assert passwords.coincide(h, "una-clave-larga")
    assert not passwords.coincide(h, "otra-clave-larga")
    assert not passwords.coincide(None, "una-clave-larga")
    assert not passwords.coincide("no-es-un-hash", "una-clave-larga")
