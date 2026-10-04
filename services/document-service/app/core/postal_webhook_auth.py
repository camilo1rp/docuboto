# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Verificación HMAC del webhook ENTRANTE del operador postal (E20 F5).

Simétrico al webhook SALIENTE de ADR-018 (notification-service firma con
HMAC-SHA256 y el receptor verifica); aquí OrpycaMCP es el receptor: el operador
firma el cuerpo crudo con el secreto compartido per-tenant/per-operador
(`postal_operator_credential`) y este servicio verifica la cabecera
`X-OrpycaMCP-Signature: sha256=<hexdigest>`.

El secreto vive en el schema del tenant: una firma válida PRUEBA que el emisor
conoce el secreto de ESE tenant, así que el `tenant_slug` del path no puede
usarse para falsificar callbacks de otro tenant (necesitaría su secreto).
"""

from __future__ import annotations

import hashlib
import hmac

_PREFIX = "sha256="


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """True sólo si `signature_header` es el HMAC-SHA256 de `body` con `secret`.

    Comparación en tiempo constante (`hmac.compare_digest`). Fail-closed: cabecera
    ausente, sin el prefijo esperado o secreto vacío → False.
    """
    if not secret or not signature_header or not signature_header.startswith(_PREFIX):
        return False
    provided = signature_header[len(_PREFIX) :]
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def verify_any(secrets: list[str], body: bytes, signature_header: str | None) -> bool:
    """True si la firma corresponde a CUALQUIERA de los secretos vigentes (rotación).

    Se evalúan SIEMPRE todos (sin cortocircuito) para que el tiempo de respuesta no
    delate cuál de las ranuras —la vigente o la anterior— coincidió. La lista vacía no
    verifica nada: fail-closed.
    """
    resultado = False
    for secreto in secrets:
        if verify_signature(secreto, body, signature_header):
            resultado = True
    return resultado


def aad_credencial(tenant_slug: str, operador: str) -> bytes:
    """Datos autenticados de la custodia: entidad + operador.

    Un secreto copiado a otro operador o al esquema de otra entidad NO descifra.
    El separador no puede aparecer en un slug ni en un operador, así que
    `("a", "b-c")` y `("a-b", "c")` no colisionan.
    """
    return f"postal_operador|{tenant_slug}|{operador}".encode()


# -- Custodia cifrada del secreto (AESGCM, misma clave maestra que las credenciales
#    de correo: `EMAIL_ENCRYPTION_KEY`). El AAD ata el ciphertext a entidad+operador.


def cifrar_secreto(secreto: str, *, clave_b64: str, tenant_slug: str, operador: str) -> tuple[bytes, bytes]:
    """`(ciphertext, nonce)`. Falla (`RuntimeError`) si la clave maestra no está: nunca
    se guarda un secreto en claro ni bajo una clave adivinada."""
    import secrets as _secrets

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from app.core.email_credenciales import NONCE_LENGTH, _clave

    nonce = _secrets.token_bytes(NONCE_LENGTH)
    return (
        AESGCM(_clave(clave_b64)).encrypt(
            nonce, secreto.encode("utf-8"), aad_credencial(tenant_slug, operador)
        ),
        nonce,
    )


def descifrar_secreto(
    ciphertext: bytes, *, nonce: bytes, clave_b64: str, tenant_slug: str, operador: str
) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from app.core.email_credenciales import _clave

    return (
        AESGCM(_clave(clave_b64))
        .decrypt(bytes(nonce), bytes(ciphertext), aad_credencial(tenant_slug, operador))
        .decode("utf-8")
    )
