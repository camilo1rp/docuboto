# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Firma electrónica PERSONAL PKI por-usuario (E17/F4, Incremento 1) — custodia
SERVIDOR (postura B, ver ADR y CLAUDE.md de este incremento). Marco honesto:
NO acreditado (mismo nivel legal, art. 7 Ley 527/1999 — firma electrónica
simple), forward-compatible a un HSM (:func:`sign_digest` es el ÚNICO punto
que un proveedor HSM futuro necesitaría reemplazar).

INVARIANTE RECTOR (no negociable): la clave privada del usuario JAMÁS sale de
este proceso en claro. Se cifra en reposo con AESGCM
(``cryptography.hazmat``, mismo patrón que ``app/core/totp.py`` — NO Fernet,
AESGCM permite Additional Authenticated Data) y solo se descifra en memoria
dentro de :meth:`SigningKeyService.load_and_sign_digest`, para el tiempo
mínimo indispensable de firmar UN digest. Ningún endpoint, log, evento o
entrada de auditoría de este servicio la emite jamás — a diferencia del
secreto TOTP (revelado una sola vez en el enroll), la clave de firma NUNCA se
revela, ni siquiera una vez: solo su CSR (público) y, tras la emisión
offline, su certificado (público).

CAVEAT explícito (C7, honestidad RF-FIR-15): custodia SERVIDOR ⇒ NO hay
sole-control del titular sobre su clave. Distinguir la propiedad de
SEGURIDAD (arriba: la clave no se filtra — cifrada en reposo, descifrada
solo en memoria, tiempo mínimo) de la propiedad LEGAL (distinta: el
custodio — este servicio, y por extensión la institución que lo opera —
SIGUE teniendo acceso técnico a la clave, así que no puede excluirse como
autor material de una firma). Este esquema es firma electrónica del art. 7
Ley 527/1999 (atribución + integridad verificable), NO da no-repudio
oponible CONTRA el operador/institución, y NO equivale a firma digital
cualificada (art. 28 Ley 527/1999). Ese tier exigiría clave bajo control
EXCLUSIVO del firmante (HSM/token personal) + CA acreditada ONAC —
roadmap HSM/PKI, ver el comentario forward-compatible sobre
:func:`sign_digest` arriba.

RSA-2048 (mismo tamaño que el sello institucional — ``app/core/keys.py`` en
signature-service, y que la CA de desarrollo — ``scripts/gen-dev-ca.sh``).
``sign_digest`` firma un digest YA CALCULADO (``Prehashed``) con
PKCS1v15+SHA256 — el mismo esquema (``SignatureMethod.RSA_SHA256`` de
signxml) que el sello institucional usa para XAdES-B/T, así que el
``ds:SignatureValue`` producido por signature-service (ver
``app.providers.local``, técnica de "firma remota" validada en el Paso 0 del
Incremento) es indistinguible en forma de uno producido con la clave en
proceso.
"""

from __future__ import annotations

import secrets

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID

KEY_SIZE = 2048  # RSA-2048 — mismo tamaño que el sello institucional (signature-service)
NONCE_LENGTH = 12  # AESGCM standard nonce size


# ---------------------------------------------------------------------------
# Keygen + CSR
# ---------------------------------------------------------------------------


def generate_keypair() -> rsa.RSAPrivateKey:
    """Par RSA-2048 fresco. Nunca se retiene fuera del scope del caller —
    ver la REGLA no-fuga en el docstring del módulo."""
    return rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)


def private_key_to_pem(key: rsa.RSAPrivateKey) -> bytes:
    """PEM sin cifrar (PKCS8) — el cifrado en reposo lo aporta AESGCM
    (:func:`encrypt_private_key`), no una passphrase de PEM."""
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def load_private_key_pem(pem: bytes) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):  # pragma: no cover — defensivo
        raise ValueError("La clave descifrada no es RSA")
    return key


def build_subject_name(*, username: str, email: str | None, cargo: str | None) -> x509.Name:
    """Subject DN del CSR: identidad de ``auth_users`` + ``cargo`` de RBAC
    (mejor esfuerzo — ``auth_groups`` no modela un "cargo" formal en este
    incremento, se usa el nombre de grupo(s) del usuario si los tiene, ver
    :meth:`SigningKeyService._resolve_cargo`). ``cargo`` viaja en
    ``NameOID.ORGANIZATIONAL_UNIT_NAME`` cuando está presente.
    """
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, username)]
    if email:
        attrs.append(x509.NameAttribute(NameOID.EMAIL_ADDRESS, email))
    if cargo:
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, cargo))
    return x509.Name(attrs)


def build_csr(key: rsa.RSAPrivateKey, subject: x509.Name) -> x509.CertificateSigningRequest:
    """CSR PEM-exportable (público) — es lo único que ``enroll()`` devuelve
    al llamante junto al estado, NUNCA la clave privada."""
    return (
        x509.CertificateSigningRequestBuilder()
        .subject_name(subject)
        .sign(key, hashes.SHA256())
    )


def csr_to_pem(csr: x509.CertificateSigningRequest) -> bytes:
    return csr.public_bytes(serialization.Encoding.PEM)


# ---------------------------------------------------------------------------
# AESGCM encryption at rest — mismo patrón que app/core/totp.py
# ---------------------------------------------------------------------------


def build_aad(*, tenant_slug: str, user_id: str) -> bytes:
    """AAD que ata el ciphertext a la fila/tenant — SEPARADA del namespace de
    TOTP (``:totp``) aunque comparta forma, para que una clave de cifrado
    nunca pueda reutilizarse entre propósitos ni el ciphertext de un secreto
    se confunda con el del otro."""
    return f"tenant:{tenant_slug}:user:{user_id}:signing".encode()


def encrypt_private_key(private_key_pem: bytes, *, key: bytes, aad: bytes) -> tuple[bytes, bytes]:
    """Devuelve ``(ciphertext, nonce)``. ``nonce`` aleatorio, 12 bytes, por fila."""
    nonce = secrets.token_bytes(NONCE_LENGTH)
    ciphertext = AESGCM(key).encrypt(nonce, private_key_pem, aad)
    return ciphertext, nonce


def decrypt_private_key(ciphertext: bytes, *, nonce: bytes, key: bytes, aad: bytes) -> bytes:
    """Lanza ``cryptography.exceptions.InvalidTag`` si ``aad`` no coincide
    (fila/tenant equivocados) o el ciphertext/nonce fue alterado."""
    return AESGCM(key).decrypt(nonce, ciphertext, aad)


# ---------------------------------------------------------------------------
# Firma de un digest ya calculado (el seam a un HSM futuro)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Sub-CA de usuarios (E17/F4, Incremento 2 — anclaje, cierra la Baja #4)
# ---------------------------------------------------------------------------


class UserCaNotConfiguredError(RuntimeError):
    """``signing_key_ca_users_cert_path`` no está configurada, o el archivo
    montado no es un certificado X.509 legible. FAIL-CLOSED — el llamante
    (``SigningKeyService.activar_certificado``) lo traduce en un 503: sin la
    sub-CA montada, NINGÚN certificado puede activarse (no hay contra qué
    anclar la cadena), nunca se degrada en silencio a "confiar en cualquier
    emisor"."""


def load_user_ca_certificate() -> x509.Certificate:
    """Carga el certificado PÚBLICO de la sub-CA de usuarios desde
    ``settings.signing_key_ca_users_cert_path`` (``ca-users.crt``, montada
    read-only) — NUNCA su clave privada, que este servicio no necesita ni
    debe tener acceso a: solo verifica cadenas, no emite certificados.

    Releído en CADA llamada (sin caché) — es un archivo público de bajo
    volumen de lectura (solo en ``activar_certificado``, una operación
    administrativa/offline, no el camino caliente de firma), así que la
    simplicidad de "siempre la versión en disco" pesa más que el ahorro de
    una relectura ocasional.
    """
    from app.core.config import settings  # local import: evita el ciclo config<->signing_keys

    path = settings.signing_key_ca_users_cert_path
    if not path:
        raise UserCaNotConfiguredError(
            "signing_key_ca_users_cert_path no configurada — requerida para "
            "activar certificados de firma personal"
        )
    try:
        with open(path, "rb") as fh:
            pem_bytes = fh.read()
        return x509.load_pem_x509_certificate(pem_bytes)
    except OSError as exc:
        raise UserCaNotConfiguredError(
            f"no se pudo leer la sub-CA de usuarios en '{path}': {exc}"
        ) from exc
    except ValueError as exc:
        raise UserCaNotConfiguredError(
            f"el archivo en '{path}' no es un certificado X.509 PEM válido: {exc}"
        ) from exc


def sign_digest(private_key: rsa.RSAPrivateKey, digest: bytes) -> bytes:
    """Firma un digest SHA-256 YA CALCULADO por el llamante (``Prehashed``) —
    NUNCA recibe el documento completo, ni siquiera dentro de este proceso:
    signature-service calcula ``C14N-exclusiva(ds:SignedInfo)`` y envía solo
    su SHA-256 (ver ``app.core.http_clients.sign_digest_remote`` y
    ``app.providers.local``, técnica validada en el Paso 0 del incremento).

    ÚNICO punto que un proveedor HSM futuro (PKCS#11, KMS remoto, ...)
    necesitaría reemplazar — misma firma de función, otra implementación
    (ver ``app.providers.base.SignerRef`` en signature-service, que ya
    documenta este seam del lado del proveedor de firma).
    """
    return private_key.sign(digest, padding.PKCS1v15(), Prehashed(hashes.SHA256()))
