# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from orpycamcp_common import deadletter, events, identidad, request_log
from orpycamcp_common import secrets as common_secrets

from app.core.config import settings, variables_retiradas_presentes
from app.core.database import close_pool, create_pool
from app.core.redis_client import close_redis, init_redis
from app.routers.admin_deadletter import router as admin_dlq_router
from app.routers.cadena import router as cadena_router
from app.routers.health import router as health_router
from app.routers.signature import router as signature_router
from app.workers import document_consumer

logging.basicConfig(level=settings.log_level.upper())

logger = logging.getLogger("signature.startup")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("signature-service starting up")
    # Antes de tocar nada: sin el secreto HMAC este servicio no firma, *firma
    # mal* — con la clave publicada en el repositorio. Fallar aquí es ruidoso y
    # reparable; arrancar es un daño que solo se descubre auditando firmas.
    common_secrets.require_secrets(
        {"SIGNATURE_SECRET": settings.signature_secret}, servicio="signature-service"
    )
    for retirada, sustituta in variables_retiradas_presentes():
        # No se aborta (un despliegue con la variable heredada debe seguir
        # arrancando) pero se grita: la variable NO se lee y, en el caso de
        # TSA_ESSCERTID_OBLIGATORIO=true, el operador creía rechazar sellos sin
        # ESSCertID y hoy manda la fecha de corte.
        logger.error(
            "La variable %s ya NO tiene efecto y se ignora: use %s (ver ADR-016, "
            "addendum 2026-10-04).",
            retirada,
            sustituta,
        )
    if settings.firma_personal_require_xades == "off":
        # AREA-04-12: el gate que obliga a firmar lo clasificado con certificado
        # personal viene apagado. Es decisión de la ENTIDAD (exige enrolar antes
        # los certificados), así que no se activa solo — pero que lo clasificado
        # se firme con HMAC nativo no puede ser algo que nadie vea al arrancar.
        logger.warning(
            "FIRMA_PERSONAL_REQUIRE_XADES=off (respaldo): las entidades que no "
            "definan el parámetro firma_personal_require_xades permiten firmar "
            "actos de nivel >= 2 con HMAC nativo (sin certificado personal). La "
            "política es de cada ENTIDAD (PUT /config/params/"
            "firma_personal_require_xades): si ya enroló certificados, active "
            "'clasificados' o 'todos'."
        )
    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    await init_redis(settings.redis_url)
    # Re-sync firma_solicitud.nivel_seguridad on reclassification events (RF-SEG-08).
    consumer_task = events.spawn_supervised(
        document_consumer.run, name="signature", logger=logger
    )
    yield
    logger.info("signature-service shutting down")
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    await close_pool()
    await close_redis()


app = FastAPI(
    title="OrpycaMCP — Signature Service",
    description=(
        "Firma electrónica (E06, ADR-016): sign-xml + verify. Proveedor pluggable "
        "(nativo hash+identidad por defecto; PKI XAdES/PAdES/TSA como swap)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Registro estructurado por petición (11.2). Va aquí, con el `X-Request-Id` que
# el gateway propaga desde el borde: sin esto el identificador llega y nadie lo
# escribe, que es el mismo defecto de partida movido un salto más allá.
# La identidad que inyecta el gateway viene FIRMADA: sin esto, cualquiera
# que alcance el puerto de este servicio dentro de la red podía hablarle
# diciendo ser quien quisiera y de la entidad que quisiera. Va el primero
# —o sea el más externo— porque es la decisión que precede a todas.
app.add_middleware(identidad.IdentidadFirmadaMiddleware)
app.add_middleware(request_log.RequestLogMiddleware, servicio="signature-service")

app.include_router(signature_router)
app.include_router(cadena_router)
app.include_router(admin_dlq_router)
deadletter.instalar_manejador_radicados_ausente(app)
app.include_router(health_router)
