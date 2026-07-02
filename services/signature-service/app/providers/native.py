# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""NativeSigner — envuelve la firma HMAC nativa preexistente en el `Protocol`
``SignerProvider`` (E06, ADR-016, RF-FIR-12).

REFACTOR, no reescritura: delega bit a bit en ``app.services.signer.firmar``
(el mismo módulo HMAC-SHA256 que ya usa la cadena de firma secuencial). El
único propósito de esta clase es que ``/sign-xml`` pueda resolver el proveedor
por nombre con :func:`app.providers.get_provider` de forma UNIFORME junto con
``xades_local``, sin bifurcar el router en dos implementaciones distintas.
Ningún dato ni algoritmo cambia: mismo secreto (``settings.signature_secret``),
mismo esquema de mensaje (``payload_sha256|firmante_id|sello_tiempo``), mismo
``hmac.compare_digest`` en verificación — la cadena de firma
(``/requests``, ``/pending``, ``/batch``, que llama a ``app.services.signer``
directamente, no a este proveedor) y las firmas HMAC ya persistidas en BD
siguen verificando exactamente igual que antes del Incremento 1.

Reutilización no ortodoxa de ``SignerRef`` (documentada también en
``app.providers.base.SignerRef``): al no haber material X.509 en la firma
nativa, los campos pensados para un certificado se reinterpretan así:

    - ``cert_pem``     → vacío (``b""``); nunca hay certificado.
    - ``key_pem``      → el SECRETO HMAC del servicio (``settings.signature_secret``
                         codificado a bytes), en vez de una clave privada PEM.
    - ``key_password`` → el ``firmante_id`` (UUID de ``X-User-Id``) codificado a
                         bytes, en vez de una contraseña de clave.

Esto permite que el router construya un ``SignerRef`` uniforme para AMBOS
proveedores antes de llamar a ``.sign(prepared, signer)`` — la alternativa
(una unión de tipos por proveedor) habría obligado al router a bifurcar según
el proveedor elegido, justo lo que esta capa de abstracción busca evitar.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import settings
from app.providers.base import PreparedDoc, SignedDoc, SignerRef
from app.services import signer as _hmac_signer


class NativeSigner:
    """Proveedor ``nativa``: HMAC-SHA256 con secreto de servicio (sin PKI)."""

    async def prepare(
        self,
        xml_bytes: bytes,
        *,
        objeto_tipo: str,
        objeto_id: str | None = None,
        indice_id: str | None = None,
        version: int | None = None,
    ) -> PreparedDoc:
        return PreparedDoc(
            xml_bytes=xml_bytes,
            objeto_tipo=objeto_tipo,
            objeto_id=objeto_id,
            indice_id=indice_id,
            version=version,
        )

    async def sign(self, prepared: PreparedDoc, signer: SignerRef) -> SignedDoc:
        firmante_id = signer.key_password.decode() if signer.key_password else ""
        secret = signer.key_pem.decode() if signer.key_pem else settings.signature_secret
        sello_tiempo = datetime.now(UTC).isoformat()

        result = _hmac_signer.firmar(prepared.xml_bytes, firmante_id, sello_tiempo, secret)

        return SignedDoc(
            # No hay "documento firmado" distinto del payload original en HMAC
            # (no se incrusta la firma en el XML) — se devuelve tal cual llegó.
            signed_xml=prepared.xml_bytes,
            payload_sha256=result["payload_sha256"],
            # `firma_blob` (hex HMAC) viaja en este campo por compatibilidad de
            # forma con SignedDoc — NO es una firma XML-DSig en base64, sigue
            # siendo el mismo hex que producía `signer.firmar` antes del
            # Incremento 1. `FirmaRepository` lo persiste tal cual en la
            # columna `firma_blob`, igual que siempre.
            signature_value_b64=result["firma_blob"],
            certificado_pem="",
            xades_level=None,
            nivel_conformidad="firma_electronica_nativa",
            provider=result["provider"],  # "nativa" — de app.services.signer, fuente única
            certificado_fingerprint=None,
            extra={"sello_tiempo": sello_tiempo},
        )

    async def check_status(self, ref: str) -> str:
        """La firma HMAC se calcula en memoria dentro de :meth:`sign` — siempre SIGNED."""
        return "SIGNED"

    async def download(self, ref: str) -> bytes:
        raise NotImplementedError(
            "NativeSigner no delega custodia externa — no aplica a la firma HMAC."
        )
