# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Custodia de las credenciales de las cuentas de correo (E19 Increment 1).

Antes de esto la única credencial de correo del sistema vivía **en claro en una
variable de entorno** (`IMAP_PASSWORD`), visible en `docker compose config`, en
el inspect del contenedor y en cualquier volcado del entorno. El Orfeo legacy no
lo hacía mejor: encontramos contraseñas de correo escritas dentro de los
`config.php`.

Mismo esquema que la clave de firma personal y el secreto TOTP de auth-service
—**AESGCM** de `cryptography.hazmat`, no Fernet, porque AESGCM admite *Additional
Authenticated Data*— y por la misma razón: el AAD ata el ciphertext a la fila y a
la entidad que lo produjo. Un secreto copiado a otra fila, o al esquema de otra
institución, **no descifra**: lanza `InvalidTag` en vez de entregar una
credencial en un contexto que no le corresponde.

**Lo que NO se guarda aquí.** La contraseña de una cuenta *personal* no se cifra:
no se almacena en absoluto. Se pide al funcionario cuando entra a trabajar su
buzón y vive solo en su sesión — el patrón del `lock.php` del Orfeo legacy, que
en esto acertaba. Custodiar la credencial personal de alguien convertiría al
sistema en titular de su correo entero, y para radicar no hace falta tanto.
"""

from __future__ import annotations

import base64
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_LENGTH = 12  # tamaño estándar de nonce para AESGCM


def _clave(clave_b64: str) -> bytes:
    """Decodifica la clave maestra a 32 bytes crudos (AES-256).

    Fail-closed y explícito: si falta o no mide 32 bytes se levanta
    `RuntimeError`, **nunca** se cae a una clave derivada o implícita. Una
    credencial cifrada bajo una clave adivinada da la apariencia de custodia sin
    la custodia — es peor que guardarla en claro, porque nadie va a revisarla.
    """
    if not clave_b64:
        raise RuntimeError(
            "email_encryption_key no configurada — requerida para custodiar "
            "credenciales de cuentas de correo"
        )
    try:
        crudo = base64.b64decode(clave_b64, validate=True)
    except Exception as exc:  # noqa: BLE001 — el motivo exacto no aporta y podría filtrar
        raise RuntimeError("email_encryption_key no es base64 válido") from exc
    if len(crudo) != 32:
        raise RuntimeError(
            f"email_encryption_key debe decodificar a 32 bytes (AES-256), no {len(crudo)}"
        )
    return crudo


def aad(tenant_slug: str, cuenta_id: str) -> bytes:
    """Datos autenticados: entidad + fila.

    Es lo que impide que un ciphertext sirva fuera de su sitio. Se construye con
    un separador que no puede aparecer en un UUID ni en un slug, para que
    `("a", "b-c")` y `("a-b", "c")` no produzcan el mismo AAD.
    """
    return f"email_cuenta|{tenant_slug}|{cuenta_id}".encode()


def cifrar(secreto: str, *, clave_b64: str, tenant_slug: str, cuenta_id: str) -> tuple[bytes, bytes]:
    """Devuelve `(ciphertext, nonce)`. Nonce aleatorio de 12 bytes por fila."""
    nonce = secrets.token_bytes(NONCE_LENGTH)
    ciphertext = AESGCM(_clave(clave_b64)).encrypt(
        nonce, secreto.encode("utf-8"), aad(tenant_slug, cuenta_id)
    )
    return ciphertext, nonce


def descifrar(
    ciphertext: bytes, *, nonce: bytes, clave_b64: str, tenant_slug: str, cuenta_id: str
) -> str:
    """Descifra en memoria, para el tiempo mínimo de abrir una conexión IMAP.

    Lanza `cryptography.exceptions.InvalidTag` si el AAD no coincide (fila o
    entidad equivocadas) o si el ciphertext fue alterado.
    """
    crudo = AESGCM(_clave(clave_b64)).decrypt(nonce, ciphertext, aad(tenant_slug, cuenta_id))
    return crudo.decode("utf-8")
