# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Cliente HTTP hacia signature-service — estado del circuito de aprobación.

Existe para una sola pregunta, y la pregunta es la que convierte el circuito de
visto bueno en algo que ata: **¿hay un circuito abierto sobre este borrador?**
Hasta que existió, `BorradorService.aprobar` no consultaba nada: se abría una
cadena de tres turnos, nadie resolvía ninguno, y quien tuviera `PERM_RADI`
pulsaba «Aprobar» y consumía el número. El circuito era decorativo.

La dirección es deliberada. La alternativa —guardar en la fila del borrador una
marca que signature-service actualizara— es una copia, y una copia se queda
rancia por caminos que no pasan por signature (borrar la cadena, restaurar la
base, un consumidor de eventos retrasado). Rancia en el lado permisivo es una
puerta abierta; preguntar devuelve el estado de ahora.

Sellado con `X-Internal-Token` y SIN reenviar el `Authorization` del usuario: la
respuesta es content-free (dos booleanos y un identificador), así que no hay
nada que recortar por clearance.
"""

from __future__ import annotations

import httpx

from app.core.config import settings


class CircuitoNoVerificableError(RuntimeError):
    """No se pudo saber si hay un circuito abierto.

    Se traduce a **503, y NO se aprueba**. Degradar a «supongo que no hay
    circuito» sería exactamente el fallo que este cliente existe para impedir:
    la única consecuencia de no saber es no poder aprobar, nunca aprobar de
    más.
    """


async def get_estado_circuito(borrador_id: str, tenant_slug: str) -> dict:
    """Estado del circuito de aprobación de un borrador.

    Devuelve `{"pendiente": bool, "completo": bool, "solicitud_id": str|None}`.
    `pendiente` y `completo` no son opuestos: sin ningún circuito los dos son
    `False`, que es el camino directo de siempre.
    """
    headers = {
        # El de SIGNATURE-service: es a él a quien se le pregunta. Mandar aquí
        # el token de otro servicio da 401 en TODA aprobación, y el 401 se lee
        # como un problema de red en vez de como lo que es.
        "X-Internal-Token": settings.signature_internal_token,
        "X-Tenant-Slug": tenant_slug,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.signature_service_url, timeout=10.0
        ) as client:
            resp = await client.get(
                f"/api/v1/signature/internal/circuitos/borrador/{borrador_id}",
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise CircuitoNoVerificableError(
            f"signature-service inalcanzable: {exc}"
        ) from exc

    if resp.status_code != 200:
        raise CircuitoNoVerificableError(
            f"signature-service respondió {resp.status_code}"
        )
    try:
        return dict(resp.json())
    except ValueError as exc:  # respuesta no-JSON
        raise CircuitoNoVerificableError(
            "signature-service devolvió una respuesta ilegible"
        ) from exc
