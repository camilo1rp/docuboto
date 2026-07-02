# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Carga del sello institucional para firma XAdES-B (E06, ADR-016, RT-16, Inc.1).

Custodia el par de claves del SISTEMA (sello de persona jurídica — la
institución tenant), NUNCA la clave de un usuario individual: el firmante se
atribuye por identidad (``X-User-Id`` + ``audit_log``, RF-FIR-11), no por
posesión de una clave propia. El PEM se monta como secreto/volumen Docker
de solo lectura, fuera de BD, del repositorio, de cualquier response HTTP y de
los logs — ver la "REGLA no-fuga" más abajo.

Resolución estricta por tenant: ``<signer_cert_dir>/<tenant_slug>/seal.{crt,key}``.
Si el tenant no tiene sello propio montado y ``settings.signer_allow_dev_seal``
es ``True`` (kill-switch, ``False`` por defecto), cae al par de DESARROLLO
``<signer_cert_dir>/_dev/seal.{crt,key}`` (generado por
``scripts/gen-dev-signing-cert.sh``), marcado explícitamente no-acreditado —
COMPARTIDO entre todos los tenants sin sello propio, así que con el flag en
``False`` (recomendado en producción) la ausencia de sello del tenant falla
cerrado (``SealNotFoundError``) en vez de firmar con esa clave compartida.
Un tenant A nunca puede resolver el sello de un tenant B: el slug se valida
(fail-closed) antes de tocar el filesystem y solo se usa para construir la
subcarpeta exacta bajo ``signer_cert_dir`` — no hay forma de que el nombre de
un tenant referencie una carpeta ajena (regex ancla + sin ``..``/``/``).
"""

from __future__ import annotations

import re
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from app.core.config import settings
from app.providers.base import SignerRef

# Mismo patrón que app/core/database.py — un slug válido nunca contiene
# separadores de path, así que no hace falta sanear "../" por separado.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,54}$")

# Cache SOLO del certificado PÚBLICO (evita I/O repetido en cargas sucesivas del
# mismo tenant). La clave privada NUNCA entra aquí — ver `load_seal`.
_public_cert_cache: dict[str, bytes] = {}


class SealNotFoundError(RuntimeError):
    """No hay sello (ni de tenant ni de desarrollo) disponible para firmar."""


def _validate_slug(tenant_slug: str) -> str:
    if not tenant_slug or not _SLUG_RE.match(tenant_slug):
        raise ValueError(f"Invalid tenant slug: {tenant_slug!r}")
    return tenant_slug


def _read_pair(dir_path: Path) -> tuple[bytes, bytes] | None:
    cert_path = dir_path / "seal.crt"
    key_path = dir_path / "seal.key"
    if not cert_path.is_file() or not key_path.is_file():
        return None
    cert_pem = cert_path.read_bytes()
    key_pem = key_path.read_bytes()
    return cert_pem, key_pem


def load_seal(tenant_slug: str) -> SignerRef:
    """Resuelve el sello institucional (cert+key) del tenant.

    Fail-closed: valida el slug ANTES de tocar el filesystem (evita path
    traversal). Cachea solo el certificado público; la clave privada se lee
    del disco en cada llamada, se entrega al llamador (que la pasa a
    ``signxml`` y la descarta) y nunca se retiene ni se loguea — REGLA
    no-fuga (RT-16): ningún endpoint, response, evento o log de este servicio
    emite jamás una clave privada, solo el certificado público y su huella.

    Cae al par de DESARROLLO (``_dev/``) si el tenant no tiene sello propio
    montado Y ``settings.signer_allow_dev_seal`` es ``True`` — siempre con
    ``acreditado=False`` (RF-FIR-15: nunca se sobre-declara conformidad para
    un certificado de pruebas; el Incremento 1 no integra ninguna CA
    acreditada ONAC, así que esto es así también para un sello "real" de
    tenant, no solo para el de desarrollo). Con el flag en ``False``
    (default, recomendado en producción) la ausencia de sello propio del
    tenant NUNCA cae a un par compartido con otros tenants: falla cerrado.

    Raises:
        ValueError: slug inválido (el router lo traduce a 400/500 según el
            contexto de la petición).
        SealNotFoundError: el tenant no tiene un par cert+key montado y, o
            bien el fallback ``_dev`` está deshabilitado
            (``signer_allow_dev_seal=False``), o bien tampoco existe.
    """
    slug = _validate_slug(tenant_slug)
    base = Path(settings.signer_cert_dir)

    pair = _read_pair(base / slug)
    if pair is None and settings.signer_allow_dev_seal:
        pair = _read_pair(base / "_dev")
    if pair is None:
        raise SealNotFoundError(
            f"No se encontró sello de firma propio para el tenant {slug!r} bajo {base} "
            f"(fallback _dev {'deshabilitado' if not settings.signer_allow_dev_seal else 'sin par montado'})"
        )
    cert_pem, key_pem = pair
    _public_cert_cache[slug] = cert_pem

    return SignerRef(
        cert_pem=cert_pem,
        key_pem=key_pem,
        key_password=settings.signer_key_password.encode() if settings.signer_key_password else None,
        tipo_firma="sello_sistema",
        nivel_conformidad="xades_b_no_acreditado",
        acreditado=False,
    )


def cert_fingerprint(cert_pem: bytes) -> str:
    """Huella SHA-256 (hex) de un certificado X.509 en formato PEM.

    Usada tanto al firmar (para persistir ``firma.certificado_fingerprint``)
    como al verificar (para cotejar el certificado embebido en la firma contra
    el que quedó registrado en la fila — ver ``app.providers.local.verify_xades``).
    """
    cert = x509.load_pem_x509_certificate(cert_pem)
    return cert.fingerprint(hashes.SHA256()).hex()
