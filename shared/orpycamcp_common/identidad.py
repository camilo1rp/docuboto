# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma de las cabeceras de identidad que el gateway inyecta.

La identidad viajaba entre el gateway y los servicios como texto plano: quién
eres, de qué entidad y con qué roles, en tres cabeceras sin firma. El gateway no
las reenvía si vienen del cliente, así que desde fuera no se pueden forjar; pero
dentro de la red de contenedores cualquiera que alcance el puerto de un servicio
—un contenedor comprometido, un operador, un proceso auxiliar— podía hablarle
directamente afirmando ser quien quisiera, de la entidad que quisiera.

Se cierra con el mismo mecanismo que el sobre de eventos y por la misma razón:
sin prueba de origen, una cabecera es una afirmación, no una identidad.

**Sin secreto configurado no se verifica**, igual que allí, y es deliberado: un
despliegue en marcha no puede quedarse sin atender por activar esto de golpe. El
interruptor es la propia variable, y en producción es obligatoria.
"""

from __future__ import annotations

import hashlib
import hmac
import os

#: Cabeceras que se firman, en orden fijo. Son las que deciden QUIÉN hace algo y
#: SOBRE QUÉ entidad: si alguna quedara fuera, se podría cambiar sin romper la
#: firma, que es exactamente el defecto que esto cierra.
CAMPOS_FIRMADOS = (
    "X-User-Id",
    "X-Username",
    "X-Tenant-Slug",
    "X-User-Roles",
    "X-Canal",
    "X-Real-Ip",
)

#: Cabecera donde viaja la firma.
CABECERA_FIRMA = "X-Identity-Signature"


class IdentidadNoAutentica(ValueError):
    """Las cabeceras de identidad no vienen firmadas por el gateway."""


def _secreto() -> str | None:
    return os.environ.get("IDENTITY_SIGNING_SECRET") or None


def _firma(cabeceras: dict[str, str], secreto: str) -> str:
    # Concatenación canónica en orden fijo, con un separador que no puede
    # aparecer en el valor de una cabecera HTTP.
    base = "\x1e".join((cabeceras.get(c) or "") for c in CAMPOS_FIRMADOS)
    return hmac.new(secreto.encode(), base.encode(), hashlib.sha256).hexdigest()


def firmar(cabeceras: dict[str, str]) -> dict[str, str]:
    """Devuelve las cabeceras con su firma, si hay secreto configurado."""
    secreto = _secreto()
    if secreto is None:
        return cabeceras
    return {**cabeceras, CABECERA_FIRMA: _firma(cabeceras, secreto)}


def verificar(cabeceras: dict[str, str]) -> None:
    """Lanza `IdentidadNoAutentica` si la identidad no viene del gateway.

    Las claves se comparan sin distinguir mayúsculas, porque los nombres de
    cabecera no las distinguen y el servidor puede entregarlas en minúscula.
    """
    secreto = _secreto()
    if secreto is None:
        return
    normalizadas = {k.lower(): v for k, v in cabeceras.items()}
    recibidas = {c: normalizadas.get(c.lower(), "") for c in CAMPOS_FIRMADOS}
    esperada = _firma(recibidas, secreto)
    if not hmac.compare_digest(normalizadas.get(CABECERA_FIRMA.lower(), ""), esperada):
        raise IdentidadNoAutentica(
            "Las cabeceras de identidad no vienen firmadas por el gateway."
        )


class IdentidadFirmadaMiddleware:
    """Verifica la firma de la identidad, si viene identidad.

    Se comprueba **solo cuando hay cabecera de usuario**. Las llamadas
    servicio-a-servicio selladas con token interno y SIN identidad de persona
    tienen su propio gate: exigirles firma las rompería sin ganar nada.

    Lo que esta clase decía antes era que las llamadas servicio-a-servicio «no
    traen identidad de persona», y es falso: siete clientes internos propagan el
    `X-User-Id` de quien originó el acto, porque el destino lo EXIGE para
    atribuirlo a una persona — el sellado del índice electrónico se atribuye a
    quien cierra el expediente (RF-FIR-11/12/14), no al sistema. Con esa premisa
    escrita, esas llamadas pasaron a morir en 401 y ningún expediente cerrado
    volvía a tener su índice sellado. **Quien propaga una identidad, la firma**
    (`firmar`), igual que hace el gateway; el guardián que lo censa vive en
    `shared/tests/test_la_identidad_propagada_va_firmada.py`.

    Es ASGI puro y no `BaseHTTPMiddleware` para poder ir el primero sin
    interferir con el cuerpo en streaming de las subidas.
    """

    def __init__(self, app):  # noqa: ANN001 — firma ASGI
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cabeceras = {
            k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        if cabeceras.get("x-user-id"):
            try:
                verificar(cabeceras)
            except IdentidadNoAutentica:
                cuerpo = (
                    b'{"error":"identidad_no_autentica","detail":"La identidad de la '
                    b'peticion no viene firmada por el gateway.","status":401}'
                )
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(cuerpo)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": cuerpo})
                return

        await self.app(scope, receive, send)
