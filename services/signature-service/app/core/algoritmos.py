# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Catálogo de algoritmos criptográficos vigentes (C-6, ADR-034 §5).

Un sello de archivo prueba que algo existía en un instante, y esa prueba solo vale mientras
los algoritmos con que se produjo siguen siendo seguros. La regla de ETSI EN 319 102-1
§5.6.2.5 (prueba de existencia) es TEMPORAL: los algoritmos de un sello tienen que ser
vigentes en el instante en que el sello SIGUIENTE los cubrió, y los del último sello, hoy.

El catálogo está en CÓDIGO y no en el request ni en la base: lo que se acepta no lo decide
quien presenta el documento. Cada entrada lleva la fecha hasta la que se considera vigente
(``None`` = sin caducidad conocida); caducar un algoritmo es editar una fecha, no una lógica.

Admitidos: digests ``sha256/sha384/sha512``; firma RSA (PKCS#1 v1.5 o PSS) con módulo de al
menos 2048 bits; ECDSA P-256 y P-384. SHA-1 NO está: solo se tolera donde es un IDENTIFICADOR
(``ESSCertID`` v1, ``CertID`` de OCSP), nunca como digest de una firma.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cryptography.hazmat.primitives.asymmetric import ec, rsa

#: nombre -> último instante en que es vigente (``None`` = vigente). Mutable a propósito:
#: las pruebas fijan una caducidad intermedia para ejercitar la regla temporal.
ALGORITMOS: dict[str, datetime | None] = {
    "sha256": None,
    "sha384": None,
    "sha512": None,
    "rsa": None,  # módulo >= 2048 (ver ``identificador_de_clave``)
    "ecdsa-p256": None,
    "ecdsa-p384": None,
}

_RSA_MODULO_MINIMO = 2048


def vigente_en(algoritmo: str, fecha: datetime) -> bool:
    """¿``algoritmo`` es vigente en ``fecha``? Fuera del catálogo = no vigente (admisión, no exclusión)."""
    if algoritmo not in ALGORITMOS:
        return False
    hasta = ALGORITMOS[algoritmo]
    return hasta is None or fecha <= hasta


def identificador_de_clave(clave: object) -> str:
    """Nombre de catálogo de una clave pública. Una clave débil o desconocida da un nombre que
    NO está en el catálogo (``rsa-1024``, ``ec-secp192r1``, ``dsa``): así falla por admisión."""
    if isinstance(clave, rsa.RSAPublicKey):
        return "rsa" if clave.key_size >= _RSA_MODULO_MINIMO else f"rsa-{clave.key_size}"
    if isinstance(clave, ec.EllipticCurvePublicKey):
        return {"secp256r1": "ecdsa-p256", "secp384r1": "ecdsa-p384"}.get(
            clave.curve.name, f"ec-{clave.curve.name}"
        )
    return type(clave).__name__.lower()


def algoritmos_de_firma(hash_nombre: str | None, clave_firmante: object) -> tuple[str, ...]:
    """Identificadores de catálogo que usa una firma: el digest y la clave que la produjo."""
    return (hash_nombre or "sin-digest", identificador_de_clave(clave_firmante))


def no_vigentes(algoritmos: tuple[str, ...] | list[str], fecha: datetime | None = None) -> list[str]:
    """Los algoritmos de la lista que NO son vigentes en ``fecha`` (por defecto, hoy)."""
    cuando = fecha or datetime.now(UTC)
    return [a for a in algoritmos if not vigente_en(a, cuando)]
