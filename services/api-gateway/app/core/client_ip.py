# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Derivación de la IP del cliente por saltos de confianza.

**El problema.** `X-Forwarded-For` lo escribe quien quiera: es una cabecera de
petición como otra cualquiera. Tomar su primer valor —lo que hacían el limitador
de tasa del gateway y el asiento de login de auth-service— significa dejar que el
cliente elija su propia identidad de red. Dos consecuencias, y la segunda es peor
que la primera:

1. El limitador de tasa se evade rotando la cabecera: cada petición parece venir
   de una IP distinta y solo queda el tope global, que además el mismo atacante
   puede agotar para tirar el servicio para todos.
2. La IP que queda **sellada en el `audit_log`** —un registro inmutable, con
   valor probatorio— la escoge el propio cliente. Un campo de la cadena de
   custodia falsificable con una cabecera es peor que un campo vacío, porque el
   vacío no engaña a nadie.

**La regla.** `X-Forwarded-For` es una lista `cliente, proxy1, proxy2, …` en la
que cada salto AÑADE al final la IP de quien le habló. Por tanto solo son
fiables los valores escritos por saltos que controlamos. Se recorre la lista
**desde la derecha**, descartando las direcciones que estén en
`trusted_proxies`, y se devuelve la primera que no lo esté: esa es la primera que
un intermediario nuestro observó de verdad.

**Fail-closed sin configuración.** Si `trusted_proxies` está vacío —el default—
la cabecera se IGNORA por completo y se usa la dirección del socket. Un
despliegue que no ha declarado sus proxies no puede distinguir un
`X-Forwarded-For` legítimo de uno inventado, y en la duda vale más una IP
inútil (la del proxy) que una falsa.

**Por qué no `--proxy-headers` de uvicorn.** Ese flag hace que uvicorn reescriba
`request.client` a partir de la cabecera, y entonces el valor ya llega adulterado
a este módulo, que no podría aplicar su propia política. Se resuelve aquí, donde
la política es visible y comprobable.
"""

import ipaddress
from functools import lru_cache

from fastapi import Request


@lru_cache(maxsize=8)
def _parse_networks(spec: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Convierte el CSV de configuración en redes. Lo inválido se descarta.

    Cachea por cadena: se llama en cada petición y el resultado solo cambia con
    la configuración, que no cambia en caliente.
    """
    redes: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for parte in spec.split(","):
        parte = parte.strip()
        if not parte:
            continue
        try:
            redes.append(ipaddress.ip_network(parte, strict=False))
        except ValueError:
            # Una entrada mal escrita no debe ampliar la confianza en silencio:
            # se descarta, lo que deja el conjunto MÁS pequeño, nunca mayor.
            continue
    return tuple(redes)


def _es_de_confianza(direccion: str, redes: tuple) -> bool:
    try:
        ip = ipaddress.ip_address(direccion)
    except ValueError:
        return False
    return any(ip in red for red in redes)


def resolve_client_ip(request: Request, trusted_proxies: str) -> str:
    """IP del cliente según la política de saltos de confianza descrita arriba."""
    socket_ip = request.client.host if request.client else "unknown"

    redes = _parse_networks(trusted_proxies)
    if not redes:
        return socket_ip

    # El salto inmediato (el socket) tiene que ser un proxy conocido; si no, quien
    # nos habla es el cliente directamente y su cabecera no vale nada.
    if not _es_de_confianza(socket_ip, redes):
        return socket_ip

    cabecera = request.headers.get("x-forwarded-for", "")
    saltos = [p.strip() for p in cabecera.split(",") if p.strip()]
    for direccion in reversed(saltos):
        # Lo que no es una dirección válida se DESCARTA, no se devuelve. Un salto
        # nuestro nunca escribe basura, así que solo puede venir del prefijo que
        # controla el cliente; devolverla sería sellar una cadena arbitraria en el
        # `audit_log` y usarla como clave de limitación de tasa. (Este caso lo
        # destapó el propio test parametrizado: la primera versión la aceptaba,
        # porque «no parseable» implicaba «no es de confianza» y por tanto «es el
        # cliente».)
        try:
            ipaddress.ip_address(direccion)
        except ValueError:
            continue
        if not _es_de_confianza(direccion, redes):
            return direccion

    # Todas las direcciones de la cadena son proxies nuestros: la petición nace
    # dentro de la infraestructura (p. ej. el SSR llamando al gateway sin haber
    # propagado la IP del navegador). Devolver el socket es lo honesto.
    return socket_ip
