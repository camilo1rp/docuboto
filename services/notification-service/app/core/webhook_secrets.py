# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Custodia del secreto HMAC de los webhooks salientes (cifrado en reposo).

El servicio NECESITA el secreto original para firmar cada entrega, así que no
sirve un hash: se cifra con AES-256-GCM bajo una KEK versionada de este servicio
(`settings.webhook_secret_kek*`, solo en el entorno) y un AAD que ata el
ciphertext al tenant y al webhook. Una fila copiada a otro tenant u otro webhook
no descifra. Esquema común con auth-service: `orpycamcp_common.kek`.

El texto claro solo existe en memoria, durante el tiempo de firmar UNA entrega
(`webhook_service._deliver_one`); jamás se loguea, audita ni devuelve.
"""

from __future__ import annotations

import uuid

from orpycamcp_common import kek

from app.core.config import settings

_V1_LABEL = "webhook_secret_kek"
_EXTRA_LABEL = "webhook_secret_keks"


def build_aad(*, tenant_slug: str, webhook_id: uuid.UUID | str) -> bytes:
    """AAD del secreto de un webhook.

    `tenant_slug` es el slug CRUDO (con guiones), el mismo que llega en la
    cabecera `X-Tenant-Slug` y en el sobre del evento — no el nombre del schema
    (`tenant_mi_slug`): las dos rutas de cifrado y descifrado (HTTP, entrega,
    re-wrap) tienen que derivar el MISMO AAD, y una derivación distinta por ruta
    deja las filas ilegibles para la otra (InvalidTag).
    """
    return f"tenant:{tenant_slug}:webhook:{webhook_id}:secret".encode()


def slug_from_schema(schema: str) -> str:
    """Inverso exacto de `tenant_schema` (`-` -> `_`).

    El validador de slug (`^[a-z0-9][a-z0-9-]*$`) PROHÍBE `_`, así que la
    sustitución es biyectiva y este inverso es seguro. Lo usan los canales de ops
    que recorren schemas, que no tienen un slug a mano.
    """
    base = schema[len("tenant_") :] if schema.startswith("tenant_") else schema
    return base.replace("_", "-")


def keyring() -> dict[int, bytes]:
    """Llavero completo {versión: 32B}. Fail-closed si la v1 falta o algo no mide 32 B."""
    return kek.build_keyring(
        settings.webhook_secret_kek,
        settings.webhook_secret_keks,
        v1_label=_V1_LABEL,
        extra_label=_EXTRA_LABEL,
    )


def active_key() -> tuple[int, bytes]:
    """(versión_activa, KEK_activa). Lanza si la activa no está en el llavero."""
    ring = keyring()
    version = settings.webhook_secret_kek_active_version
    return version, kek.key_for_version(ring, version)


def validate_kek_at_startup() -> None:
    """Aborta el arranque si el llavero no es utilizable (fail-closed).

    Sin KEK, crear un webhook con secreto fallaría y las entregas firmadas no
    podrían descifrar: mejor no arrancar que fallar en silencio por cada evento.
    """
    active_key()


def encrypt_secret(
    secret: str, *, tenant_slug: str, webhook_id: uuid.UUID | str
) -> tuple[bytes, bytes, int]:
    """Cifra bajo la KEK activa. Devuelve (ciphertext, nonce, versión de la KEK)."""
    version, key = active_key()
    aad = build_aad(tenant_slug=tenant_slug, webhook_id=webhook_id)
    ciphertext, nonce = kek.seal(secret.encode(), key=key, aad=aad)
    return ciphertext, nonce, version


def decrypt_secret(
    ciphertext: bytes,
    *,
    nonce: bytes,
    version: int,
    tenant_slug: str,
    webhook_id: uuid.UUID | str,
) -> str:
    """Descifra con la KEK de la versión de la fila. Solo en memoria.

    Lanza `InvalidTag` si el AAD (otro tenant/webhook), la KEK o los bytes no
    coinciden, y `RuntimeError` si esa versión de KEK no está cargada.
    """
    key = kek.key_for_version(keyring(), version)
    aad = build_aad(tenant_slug=tenant_slug, webhook_id=webhook_id)
    return kek.unseal(ciphertext, nonce=nonce, key=key, aad=aad).decode()
