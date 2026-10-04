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
Desde la migración 020 (AREA-04-11) la firma se produce con ``key_version=1``
(clave derivada por entidad, mensaje que ata entidad+tipo+objeto); las firmas
HMAC ya persistidas (``key_version=0``) siguen verificando con el esquema
original — ver ``app.services.signer``.

Reutilización no ortodoxa de ``SignerRef`` (documentada también en
``app.providers.base.SignerRef``): al no haber material X.509 en la firma
nativa, los campos pensados para un certificado se reinterpretan así:

    - ``cert_pem``     → vacío (``b""``); nunca hay certificado.
    - ``key_pem``      → el SECRETO RAÍZ del servicio (``settings.signature_secret``
                         codificado a bytes), en vez de una clave privada PEM; la
                         clave real del HMAC se DERIVA de él por entidad
                         (``SignerRef.tenant_slug``, AREA-04-11).
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

        if not signer.tenant_slug:
            # Sin entidad no hay clave derivada que valga: fallar aquí, no firmar
            # con una clave «de nadie».
            raise ValueError("SignerRef.tenant_slug es obligatorio para la firma nativa")

        result = _hmac_signer.firmar(
            prepared.xml_bytes,
            firmante_id,
            sello_tiempo,
            secret,
            tenant_slug=signer.tenant_slug,
            objeto_tipo=prepared.objeto_tipo,
            objeto_id=prepared.objeto_id,
        )

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
            # `sello_tiempo` es el instante que QUEDÓ dentro del HMAC: el llamante
            # tiene que persistir ESE valor en `firma.sello_tiempo`, o `/verify`
            # recomputaría con otro instante y la firma no verificaría nunca.
            extra={"sello_tiempo": sello_tiempo, "key_version": result["key_version"]},
        )

    async def check_status(self, ref: str) -> str:
        """La firma HMAC se calcula en memoria dentro de :meth:`sign` — siempre SIGNED."""
        return "SIGNED"

    async def download(self, ref: str) -> bytes:
        raise NotImplementedError(
            "NativeSigner no delega custodia externa — no aplica a la firma HMAC."
        )
