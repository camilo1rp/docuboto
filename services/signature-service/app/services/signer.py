# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Firma electrónica nativa pluggable (E06, ADR-016, RF-FIR-12).

La `firma_blob` es un HMAC-SHA256 que incorpora:
  - El SHA-256 de los BYTES QUE RECIBE (`payload_sha256`). Decía «del
    contenido», y no siempre lo es: los llamantes pasan aquí lo que quieren
    cubrir con la firma, y para un `borrador` pasan el TEXTO HEXADECIMAL de la
    huella del documento, no el documento. El valor resultante es entonces el
    hash del hash. Esta función es genérica y está bien —su `Args` ya admite
    «XML, hash, etc.»—; lo que estaba mal era esta línea, que prometía una
    correspondencia directa con el documento que no existe. La huella del
    documento vive en `firma_solicitud.payload_sha256`.
  - La identidad verificada del firmante (firmante_id, derivado del JWT).
  - El sello de tiempo del servidor (sello_tiempo, en ISO 8601 UTC).
  - Desde la migración 020 (``key_version=1``, AREA-04-11): la ENTIDAD
    (``tenant_slug``) y el OBJETO (``objeto_tipo``, ``objeto_id``), y la clave
    es una derivada por HKDF de ``settings.signature_secret`` para el propósito
    ``firma_nativa`` y esa entidad (``app.core.secret_derivation``), no el
    secreto raíz directo. Una fila copiada a otro schema, o reasignada a otro
    objeto, deja de verificar.
  - El esquema anterior (``key_version=0``, clave raíz directa) se retiró en
    B9: una firma nativa con 0 no verifica.

Consecuencia: la firma NO es reproducible desde fuera aunque el payload y el
firmante_id sean públicos — se necesita el secreto del servicio.

Notas:
- Esto es firma ELECTRÓNICA, no firma digital cualificada (sin PKI/X.509).
- Un proveedor XAdES/PAdES/TSA real se conecta implementando `firmar`/`verificar`
  sin cambiar el contrato de la API ni el esquema de BD.
- `hmac.compare_digest` evita timing-attack en la verificación.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from app.core.secret_derivation import (
    KEY_VERSION_ACTUAL,
    KEY_VERSION_HKDF,
    PROPOSITO_FIRMA_NATIVA,
    derive_key,
)


def _mensaje_v1(
    payload_sha256: str,
    firmante_id: str,
    sello_tiempo: str,
    *,
    tenant_slug: str,
    objeto_tipo: str,
    objeto_id: str | None,
) -> bytes:
    """Esquema ``key_version=1``: ata entidad, tipo y objeto además de lo anterior.

    JSON y no una unión con ``|``: ``objeto_id`` es texto libre (número de
    radicado, código de expediente) y con un separador un id que lo contenga
    movería la frontera entre campos sin cambiar los bytes del mensaje.
    """
    return json.dumps(
        [
            "orpycamcp/firma_nativa/v1",
            tenant_slug,
            objeto_tipo,
            objeto_id or "",
            payload_sha256,
            firmante_id,
            sello_tiempo,
        ],
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()


def _mac(
    key_version: int,
    secret: str,
    payload_sha256: str,
    firmante_id: str,
    sello_tiempo: str,
    *,
    tenant_slug: str,
    objeto_tipo: str,
    objeto_id: str | None,
) -> str:
    if key_version == KEY_VERSION_HKDF:
        key = derive_key(secret, purpose=PROPOSITO_FIRMA_NATIVA, tenant_slug=tenant_slug)
        return hmac.new(
            key,
            _mensaje_v1(
                payload_sha256,
                firmante_id,
                sello_tiempo,
                tenant_slug=tenant_slug,
                objeto_tipo=objeto_tipo,
                objeto_id=objeto_id,
            ),
            digestmod=hashlib.sha256,
        ).hexdigest()
    # Una versión que este código no conoce no se «adivina»: no verifica.
    raise ValueError(f"key_version desconocida: {key_version!r}")


def firmar(
    payload: bytes,
    firmante_id: str,
    sello_tiempo: str,
    secret: str,
    *,
    tenant_slug: str,
    objeto_tipo: str,
    objeto_id: str | None,
) -> dict:
    """
    Produce la firma electrónica nativa (siempre con el esquema ACTUAL).

    Args:
        payload:      Bytes del contenido a firmar (XML, hash, etc.).
        firmante_id:  UUID del firmante, derivado del JWT validado.
        sello_tiempo: Timestamp ISO 8601 UTC generado por el servidor.
        secret:       Secreto RAÍZ del servicio (settings.signature_secret); la
                      clave de la firma es una derivada por entidad.
        tenant_slug:  Entidad en cuyo schema vivirá la firma.
        objeto_tipo / objeto_id: el objeto que se firma.

    Returns:
        Dict con payload_sha256, firma_blob, provider y key_version.
    """
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    firma_blob = _mac(
        KEY_VERSION_ACTUAL,
        secret,
        payload_sha256,
        firmante_id,
        sello_tiempo,
        tenant_slug=tenant_slug,
        objeto_tipo=objeto_tipo,
        objeto_id=objeto_id,
    )
    return {
        "payload_sha256": payload_sha256,
        "firma_blob": firma_blob,
        "provider": "nativa",
        "key_version": KEY_VERSION_ACTUAL,
    }


def verificar(
    payload: bytes,
    firmante_id: str,
    firma_blob: str,
    sello_tiempo: str,
    secret: str,
    *,
    key_version: int,
    tenant_slug: str,
    objeto_tipo: str,
    objeto_id: str | None,
) -> bool:
    """
    Verifica una firma electrónica nativa.

    Recomputa el HMAC con el esquema que indica ``key_version`` (el de la fila
    ``firma``) y compara con ``hmac.compare_digest``. ``key_version`` es
    OBLIGATORIO y sin default: quien verifica tiene que haber leído la columna,
    no suponer una versión. Una versión desconocida no verifica (``False``).
    """
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    try:
        expected = _mac(
            key_version,
            secret,
            payload_sha256,
            firmante_id,
            sello_tiempo,
            tenant_slug=tenant_slug,
            objeto_tipo=objeto_tipo,
            objeto_id=objeto_id,
        )
    except ValueError:
        return False
    return hmac.compare_digest(expected, firma_blob)
