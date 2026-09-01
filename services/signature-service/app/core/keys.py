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

import json
import logging
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

logger = logging.getLogger(__name__)

# Nombre del descriptor que declara que el sello de un tenant proviene de un
# operador de firma ACREDITADO (ONAC). Vive en el MISMO directorio de solo
# lectura que el material, y no en la base ni en un endpoint, por dos razones:
#
# 1. **Quien lo sabe es quien monta el material**, no quien usa el sistema. La
#    acreditación es un hecho sobre el certificado, y el certificado lo monta el
#    operador del despliegue con lo que la entidad contrató.
# 2. **Un administrador de tenant no puede declararse acreditado.** Si esto
#    fuera un campo de configuración editable por API, cualquiera podría marcar
#    su certificado de pruebas como acreditado y el sistema empezaría a
#    sobre-declarar conformidad — justo lo que RF-FIR-15 prohíbe. Un fichero en
#    un volumen de solo lectura no lo escribe nadie desde dentro.
_ACREDITACION_FILE = "acreditacion.json"


def _leer_acreditacion(resolved_dir: Path, *, es_dev: bool) -> tuple[bool, str]:
    """¿El sello servido desde ``resolved_dir`` está acreditado? Y con qué perfil.

    Devuelve ``(acreditado, nivel_conformidad_base)``. Sin descriptor —el caso
    normal— devuelve ``(False, "xades_b_no_acreditado")``, que es exactamente el
    comportamiento anterior: quien no declara nada, no cambia.

    **El sello de DESARROLLO nunca se acredita**, mire lo que mire el fichero.
    Es material de pruebas compartido entre tenants sin sello propio, y un
    descriptor colado en ``_dev/`` acreditaría de golpe a todos ellos.

    Un descriptor ilegible o mal formado degrada a NO acreditado y lo dice en el
    log con la ruta y la causa. Se prefiere sub-declarar a reventar la firma: un
    error de tecleo en un fichero de metadatos no puede impedir que se cierre un
    expediente. Pero tampoco puede ser mudo — quien pagó una acreditación y ve
    ``acreditado=false`` necesita encontrar el motivo sin leer código.
    """
    ruta = resolved_dir / _ACREDITACION_FILE
    if not ruta.is_file():
        return False, "xades_b_no_acreditado"
    if es_dev:
        logger.warning(
            "Se ignora %s: el sello de DESARROLLO nunca se declara acreditado", ruta
        )
        return False, "xades_b_no_acreditado"
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("No se pudo leer %s (%s): el sello queda NO acreditado", ruta, exc)
        return False, "xades_b_no_acreditado"
    if not isinstance(datos, dict) or datos.get("acreditado") is not True:
        # `is not True` y no un `bool()` de cortesía: `"true"`, `1` o `"sí"` son
        # exactamente los valores que alguien teclea creyendo que activan esto,
        # y aceptarlos convertiría una errata en una declaración de conformidad.
        logger.warning(
            "%s no declara `acreditado: true` (JSON booleano): el sello queda NO acreditado",
            ruta,
        )
        return False, "xades_b_no_acreditado"
    operador = datos.get("operador") or "(sin declarar)"
    logger.info("Sello acreditado declarado en %s — operador: %s", ruta, operador)
    return True, "xades_b_acreditado"


class SealNotFoundError(RuntimeError):
    """No hay sello (ni de tenant ni de desarrollo) disponible para firmar."""


class TsaSealNotFoundError(RuntimeError):
    """No hay par de la TSA local (ni principal ni de desarrollo) para sellar (Inc.4)."""


def _validate_slug(tenant_slug: str) -> str:
    if not tenant_slug or not _SLUG_RE.match(tenant_slug):
        raise ValueError(f"Invalid tenant slug: {tenant_slug!r}")
    return tenant_slug


def _read_pair(dir_path: Path, *, stem: str = "seal") -> tuple[bytes, bytes] | None:
    """Lee ``<dir_path>/<stem>.crt`` + ``<dir_path>/<stem>.key`` si ambos existen.

    ``stem`` generaliza este helper para :func:`load_seal` (``"seal"``, el
    caso original) y :func:`load_tsa_seal` (``"tsa"``, Inc.4) sin duplicar la
    lectura de disco.
    """
    cert_path = dir_path / f"{stem}.crt"
    key_path = dir_path / f"{stem}.key"
    if not cert_path.is_file() or not key_path.is_file():
        return None
    cert_pem = cert_path.read_bytes()
    key_pem = key_path.read_bytes()
    return cert_pem, key_pem


def _read_optional_file(path: Path) -> bytes | None:
    """Lee ``path`` si existe; ``None`` si no (material LT/LTA OPCIONAL).

    Usado para ``ca.crt``/``crl.der`` (XAdES-LT/LTA) — a diferencia de
    ``seal.{crt,key}``, su ausencia nunca es un error: :func:`load_seal`
    simplemente devuelve ``ca_cert_pem``/``crl_der`` en ``None`` y
    ``LocalSigner.sign`` se queda en XAdES-T (RT-16, degradación observable).
    """
    return path.read_bytes() if path.is_file() else None


def load_seal(tenant_slug: str) -> SignerRef:
    """Resuelve el sello institucional (cert+key) del tenant.

    Fail-closed: valida el slug ANTES de tocar el filesystem (evita path
    traversal). Cachea solo el certificado público; la clave privada se lee
    del disco en cada llamada, se entrega al llamador (que la pasa a
    ``signxml`` y la descarta) y nunca se retiene ni se loguea — REGLA
    no-fuga (RT-16): ningún endpoint, response, evento o log de este servicio
    emite jamás una clave privada, solo el certificado público y su huella.

    Cae al par de DESARROLLO (``_dev/``) si el tenant no tiene sello propio
    montado Y ``settings.signer_allow_dev_seal`` es ``True`` — **siempre con
    ``acreditado=False``**, mire lo que mire el descriptor (RF-FIR-15: nunca se
    sobre-declara conformidad para un certificado de pruebas, y ese par es
    COMPARTIDO, así que acreditarlo acreditaría de golpe a todos los tenants sin
    sello propio). Un sello PROPIO del tenant sí puede declararse acreditado con
    ``acreditacion.json`` en su directorio (ver :func:`_leer_acreditacion`): eso
    es lo que permite que una entidad que contrató un operador acreditado ONAC
    no vea el sistema sub-declarando la conformidad que pagó. Sin descriptor
    —el caso normal— nada cambia: ``acreditado=False``. Con el flag en ``False``
    (default, recomendado en producción) la ausencia de sello propio del
    tenant NUNCA cae a un par compartido con otros tenants: falla cerrado.

    Además del par cert+key, resuelve el material OPCIONAL de la CA local de
    desarrollo (XAdES-LT/LTA, ``scripts/gen-dev-ca.sh``) — ``ca.crt``/
    ``crl.der`` — del MISMO directorio que efectivamente sirvió el sello (el
    del tenant, o ``_dev`` si se cayó al fallback): nunca se mezcla la CA de
    un directorio con el sello de otro. Ausencia de ese material no es un
    error — ``ca_cert_pem``/``crl_der`` quedan en ``None`` y
    ``LocalSigner.sign`` se mantiene en XAdES-T (ver ``settings.signer_lt_enabled``).

    También resuelve el par OPCIONAL del responder OCSP delegado (OCSP
    stapled en XAdES-LT) — ``ocsp.crt``/``ocsp.key``
    (``scripts/gen-dev-ocsp-responder.sh``) — del MISMO directorio, con el
    mismo criterio de no-mezcla. Ausencia no es un error —
    ``ocsp_cert_pem``/``ocsp_key_pem`` quedan en ``None`` y
    ``LocalSigner.sign`` omite ``xades:OCSPValues`` (ver
    ``settings.signer_ocsp_enabled``).

    Raises:
        ValueError: slug inválido (el router lo traduce a 400/500 según el
            contexto de la petición).
        SealNotFoundError: el tenant no tiene un par cert+key montado y, o
            bien el fallback ``_dev`` está deshabilitado
            (``signer_allow_dev_seal=False``), o bien tampoco existe.
    """
    slug = _validate_slug(tenant_slug)
    base = Path(settings.signer_cert_dir)

    resolved_dir = base / slug
    es_dev = False
    pair = _read_pair(resolved_dir)
    if pair is None and settings.signer_allow_dev_seal:
        resolved_dir = base / "_dev"
        es_dev = True
        pair = _read_pair(resolved_dir)
    if pair is None:
        raise SealNotFoundError(
            f"No se encontró sello de firma propio para el tenant {slug!r} bajo {base} "
            f"(fallback _dev {'deshabilitado' if not settings.signer_allow_dev_seal else 'sin par montado'})"
        )
    cert_pem, key_pem = pair
    _public_cert_cache[slug] = cert_pem

    ocsp_pair = _read_pair(resolved_dir, stem="ocsp")
    ocsp_cert_pem, ocsp_key_pem = ocsp_pair if ocsp_pair is not None else (None, None)

    # Del MISMO directorio que sirvió el sello, con el mismo criterio de
    # no-mezcla que `ca.crt`/`crl.der`: la acreditación es un hecho sobre ESE
    # certificado, y leerla de otro sitio la ataría al certificado equivocado.
    acreditado, nivel_conformidad = _leer_acreditacion(resolved_dir, es_dev=es_dev)

    return SignerRef(
        cert_pem=cert_pem,
        key_pem=key_pem,
        key_password=settings.signer_key_password.encode() if settings.signer_key_password else None,
        tipo_firma="sello_sistema",
        nivel_conformidad=nivel_conformidad,
        acreditado=acreditado,
        ca_cert_pem=_read_optional_file(resolved_dir / "ca.crt"),
        crl_der=_read_optional_file(resolved_dir / "crl.der"),
        ocsp_cert_pem=ocsp_cert_pem,
        ocsp_key_pem=ocsp_key_pem,
    )


def load_tsa_seal() -> SignerRef:
    """Resuelve el par de la TSA local (E06 Inc.4, RT-16).

    A diferencia de :func:`load_seal`, la TSA es ÚNICA del despliegue — NO hay
    scoping por tenant: el par principal vive directamente en
    ``<tsa_cert_dir>/tsa.{crt,key}`` (sin subcarpeta), con un único fallback
    de DESARROLLO en ``<tsa_cert_dir>/_dev/tsa.{crt,key}`` (generado por
    ``scripts/gen-dev-tsa-cert.sh``), gobernado por el mismo tipo de
    kill-switch (``settings.tsa_allow_dev_seal``, ``False`` por defecto).

    Reutiliza el dataclass ``SignerRef`` (mismo campo ``cert_pem``/``key_pem``
    que :func:`load_seal`) para que :mod:`app.core.tsa` no necesite un tipo
    de retorno distinto — aquí ``tipo_firma``/``nivel_conformidad``/
    ``acreditado`` no se usan (la TSA no firma un acto de negocio, sella un
    ``ds:SignatureValue``), se dejan en sus valores por defecto.

    La clave privada NUNCA se retiene ni se loguea — misma REGLA no-fuga que
    :func:`load_seal`: se entrega al llamador (que la pasa a ``cryptography``
    para firmar el token CMS y la descarta) y no se cachea aquí.

    Raises:
        TsaSealNotFoundError: ni el par principal ni (si
            ``tsa_allow_dev_seal=True``) el de desarrollo están montados.
    """
    base = Path(settings.tsa_cert_dir)

    pair = _read_pair(base, stem="tsa")
    if pair is None and settings.tsa_allow_dev_seal:
        pair = _read_pair(base / "_dev", stem="tsa")
    if pair is None:
        raise TsaSealNotFoundError(
            f"No se encontró sello de TSA bajo {base} "
            f"(fallback _dev {'deshabilitado' if not settings.tsa_allow_dev_seal else 'sin par montado'})"
        )
    cert_pem, key_pem = pair
    return SignerRef(
        cert_pem=cert_pem,
        key_pem=key_pem,
        key_password=settings.tsa_key_password.encode() if settings.tsa_key_password else None,
    )


def cert_fingerprint(cert_pem: bytes) -> str:
    """Huella SHA-256 (hex) de un certificado X.509 en formato PEM.

    Usada tanto al firmar (para persistir ``firma.certificado_fingerprint``)
    como al verificar (para cotejar el certificado embebido en la firma contra
    el que quedó registrado en la fila — ver ``app.providers.local.verify_xades``).
    """
    cert = x509.load_pem_x509_certificate(cert_pem)
    return cert.fingerprint(hashes.SHA256()).hex()
