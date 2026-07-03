# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Contrato pluggable de proveedores de firma (E06, ADR-016, RT-16).

``SignerProvider`` es un ``Protocol`` estructural (no una clase base — cualquier
objeto con estos cuatro métodos async sirve, sin herencia obligatoria). Los dos
proveedores del Incremento 1 (:class:`app.providers.local.LocalSigner` para
XAdES-B y :class:`app.providers.native.NativeSigner` para el HMAC nativo)
implementan ``prepare`` y ``sign``; ``check_status``/``download`` existen para
que un proveedor futuro asíncrono (p. ej. un HSM remoto o una pasarela
Camerfirder/Certicámara que firme fuera de banda) encaje sin romper el
contrato — ambos proveedores locales resuelven de forma síncrona y devuelven
``"SIGNED"``/``NotImplementedError`` respectivamente (ver sus docstrings).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class SignatureFormat(str, Enum):
    """Nivel XAdES del formato de firma producido.

    :attr:`XADES_B` (Incremento 1, firma "baseline") y :attr:`XADES_T`
    (Incremento 4 — sello de tiempo RFC 3161/TSA sobre ``ds:SignatureValue``,
    ver ``app.core.tsa``) están implementados. ``xades_local`` produce
    XAdES-T por defecto (``settings.signer_timestamp_enabled=True``); con el
    kill-switch en ``False``, o sin TSA local/remota disponible tratada como
    fallo de infraestructura, degrada a XAdES-B (ver
    ``app.providers.local.LocalSigner.sign``). :attr:`XADES_LTA` (validación
    a largo plazo, requiere CRL/OCSP) queda RESERVADO — no tiene productor ni
    consumidor en este código.
    """

    XADES_B = "XAdES-B"
    XADES_T = "XAdES-T"  # Inc.4 — sello de tiempo RFC 3161/TSA (app.core.tsa)
    XADES_LTA = "XAdES-LTA"  # reservado — CRL/OCSP, no implementado


@dataclass(slots=True)
class PreparedDoc:
    """Documento listo para firmar, tal como lo produce ``SignerProvider.prepare``.

    ``xml_bytes`` viaja exactamente como llegó al servicio (para el índice
    electrónico, ya canonicalizado C14N 2.0 por
    ``archive-service/app/services/index_builder.build_xml`` — ningún
    proveedor re-canonicaliza el CUERPO del documento antes de firmar; ver la
    nota de canonicalización en ``app.providers.local``).
    """

    xml_bytes: bytes
    objeto_tipo: str
    objeto_id: str | None = None
    indice_id: str | None = None
    version: int | None = None


@dataclass(slots=True)
class SignerRef:
    """Material de firma resuelto para UN acto de firma.

    Para ``xades_local`` transporta el **sello institucional** del tenant
    (par de claves de la PERSONA JURÍDICA, cargado por
    ``app.core.keys.load_seal`` — NUNCA la clave del usuario que firma; el
    firmante se atribuye por identidad vía ``X-User-Id`` + auditoría, no por
    posesión de clave, RF-FIR-11).

    Para ``nativa`` (:class:`app.providers.native.NativeSigner`) estos mismos
    campos se REUTILIZAN con otro significado, documentado en ese módulo:
    no hay certificado X.509, así que ``cert_pem`` queda vacío y
    ``key_pem``/``key_password`` transportan el secreto HMAC del servicio y la
    identidad del firmante respectivamente. Se prefirió reusar el mismo
    dataclass (en vez de una unión de tipos) para que el router pueda tratar
    ambos proveedores de forma uniforme a través del mismo `Protocol`.
    """

    cert_pem: bytes
    key_pem: bytes
    key_password: bytes | None = None
    tipo_firma: str = "sello_sistema"
    nivel_conformidad: str = "xades_b_no_acreditado"
    # RF-FIR-15: nunca sobre-declarar conformidad. Un certificado de pruebas
    # (el único material disponible en el Incremento 1 — ver RT-16, sin CA
    # acreditada ONAC) SIEMPRE resuelve `acreditado=False`, sin excepción.
    acreditado: bool = False


@dataclass(slots=True)
class SignedDoc:
    """Resultado de un acto de firma, uniforme entre proveedores.

    ``payload_sha256`` es la huella SHA-256 del ``xml_bytes`` de entrada
    (huella de EXHIBICIÓN pre-firma) — para ``xades_local`` NO coincidirá con
    el ``DigestValue`` embebido en la firma XAdES (que se calcula sobre una
    canonicalización exclusiva distinta); ver la nota extensa en
    ``app.providers.local``. La integridad AUTORITATIVA de un documento
    ``xades_local`` es la propia firma XAdES, verificable con
    ``app.providers.local.verify_xades`` — este campo nunca se reconcilia con
    el `DigestValue` interno.
    """

    signed_xml: bytes
    payload_sha256: str
    signature_value_b64: str
    certificado_pem: str
    xades_level: str | None
    nivel_conformidad: str
    provider: str
    certificado_fingerprint: str | None = None
    extra: dict = field(default_factory=dict)


class SignerProvider(Protocol):
    """Interfaz estructural que debe cumplir cualquier proveedor de firma."""

    async def prepare(
        self,
        xml_bytes: bytes,
        *,
        objeto_tipo: str,
        objeto_id: str | None = None,
        indice_id: str | None = None,
        version: int | None = None,
    ) -> PreparedDoc:
        """Empaqueta el documento y sus metadatos antes de firmar.

        Para ``xades_local`` este es el punto donde, en un proveedor futuro,
        se resolvería la "identidad" necesaria para el enveloped signing
        (p. ej. normalizar el `Id` del elemento raíz). El Incremento 1 no
        necesita mutar el XML aquí — `signxml` localiza el punto de inserción
        de la firma automáticamente — así que es un empaquetado directo.
        """
        ...

    async def sign(self, prepared: PreparedDoc, signer: SignerRef) -> SignedDoc:
        """Produce la firma. Único método con lógica real en el Incremento 1."""
        ...

    async def check_status(self, ref: str) -> str:
        """Consulta el estado de un acto de firma potencialmente asíncrono.

        Los proveedores locales (Incremento 1) firman de forma síncrona
        dentro de :meth:`sign`, así que siempre devuelven ``"SIGNED"``
        inmediatamente. Existe para que un proveedor remoto futuro (que
        delegue la firma a un HSM o pasarela externa) pueda reportar
        ``"PENDING"``/``"FAILED"`` sin cambiar el contrato.
        """
        ...

    async def download(self, ref: str) -> bytes:
        """Descarga el documento firmado desde una custodia externa.

        No aplica a los proveedores locales del Incremento 1: el XML firmado
        ya se devuelve inline en ``SignedDoc.signed_xml`` desde :meth:`sign`,
        así que ambos implementan esto como ``NotImplementedError``. Reservado
        para un proveedor que delegue la custodia del documento firmado a un
        tercero (p. ej. una pasarela que solo entrega el resultado bajo
        demanda).
        """
        ...
