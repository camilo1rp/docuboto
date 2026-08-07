# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Proveedores de firma pluggable (E06, ADR-016).

Un `SignerProvider` (ver ``app.providers.base``) encapsula el ACTO de firmar un
documento ya preparado, independientemente de si el material de firma es un
secreto HMAC de servicio (``nativa``) o un par de claves X.509 del sello
institucional (``xades_local``). Añadir un proveedor nuevo (Camerfirma,
Certicámara, un HSM remoto, etc.) implica escribir una clase que implemente el
`Protocol` y registrarla en :func:`get_provider` — el resto del servicio
(router, repositorio, auditoría) no cambia.

Proveedores implementados en el Incremento 1:
    - ``xades_local`` (:class:`app.providers.local.LocalSigner`): XAdES-B
      enveloped con el sello institucional del tenant, vía ``signxml``.
    - ``nativa`` (:class:`app.providers.native.NativeSigner`): envoltorio de la
      firma HMAC-SHA256 preexistente (``app.services.signer``), sin cambios de
      comportamiento — preserva la cadena de firma secuencial
      (``/requests``, ``/pending``, ``/batch``) y las firmas ya persistidas.

Fuera de alcance del Incremento 1 (ver RT-16): proveedores externos con PKI
acreditada ante ONAC (p. ej. Camerfirma, Certicámara) o dispositivos portátiles
(token/HSM) — se conectarían aquí mismo, implementando el mismo `Protocol`,
sin tocar el router ni el esquema de `firma`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.providers.base import SignerProvider

_REGISTRY: dict[str, SignerProvider] = {}


def get_provider(name: str) -> SignerProvider:
    """Resuelve un proveedor de firma por nombre (instancias cacheadas — sin estado).

    Los proveedores concretos se importan de forma DIFERIDA (dentro de la
    función, no a nivel de módulo): ``app.providers.local`` importa
    ``app.core.keys``, que a su vez importa ``SignerRef`` desde
    ``app.providers.base`` — importar ``local``/``native`` de forma anticipada
    en este ``__init__`` (que Python ejecuta antes que CUALQUIER submódulo del
    paquete, incluido ``base``) crearía un ciclo de importación.
    """
    if name not in _REGISTRY:
        if name == "xades_local":
            from app.providers.local import LocalSigner

            _REGISTRY[name] = LocalSigner()
        elif name == "nativa":
            from app.providers.native import NativeSigner

            _REGISTRY[name] = NativeSigner()
        else:
            raise ValueError(f"Proveedor de firma desconocido: {name!r}")
    return _REGISTRY[name]


__all__ = ["get_provider"]
