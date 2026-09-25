# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import re
from datetime import UTC, datetime

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.client_ip import resolve_client_ip
from app.core.config import settings

GLOBAL_RATE_KEY = "ratelimit:global"

# Puertas ciudadanas de radicación: crean radicados SIN autenticar y consumen
# números que no se reutilizan. Llevan cubo propio, mucho más estrecho que el
# general, y por eso se identifican por RUTA y no por permiso — no hay usuario a
# quien atribuirlas.
#
# La lista se escribe por ADMISIÓN y cubre las DOS puertas, no solo la histórica:
# `pqrs` es la ruta legada y `ventanilla` la vigente (la que además acepta
# anexos, o sea la cara). Cuando la ventanilla sustituyó a PQRS, el cubo estrecho
# se quedó vigilando la puerta que ya casi nadie usa: el tope caía sobre el
# endpoint barato mientras el caro heredaba el general de 100/min. Toda puerta
# anónima que consuma numeración oficial entra aquí el día que nace.
_RADICACION_ANONIMA_RE = re.compile(r"^/api/v1/public/[^/]+/(pqrs|ventanilla)/?$")

# Cosecha anónima de interoperabilidad (OAI-PMH y CMIS). Cada petición ASIENTA en
# la cadena de auditoría, y esa escritura toma el cerrojo por entidad que
# serializa la cadena entera: sin cubo propio, un anónimo repitiendo consultas
# ponía a competir por ese cerrojo a todas las escrituras de auditoría de la
# entidad — las radicaciones incluidas. No hace falta que consiga leer nada.
#
# Son lecturas, así que el tope es más holgado que el de la radicación, pero
# existe: lo caro aquí no es lo que devuelven, es lo que escriben.
_COSECHA_ANONIMA_RE = re.compile(r"^/api/v1/public/(oai|cmis)/")

# Login: las dos únicas rutas anónimas CARAS que no tenían cubo propio. Cada
# petición es un password-grant contra Keycloak con hash de contraseña (PBKDF2),
# sobre el componente que no tiene réplicas. Heredaban el cubo general, así que
# subirlo de 100 a 1200 por el problema del NAT convirtió eso en amplificación de
# CPU x12. La lista se escribe por ADMISIÓN, como las dos de arriba.
_LOGIN_RE = re.compile(r"^/api/v1/auth/(token|refresh)/?$")

# El chequeo de salud es el comando del healthcheck del contenedor y del balanceador.
# Queda fuera del cubo GLOBAL —no del suyo por IP— para que la saturación del
# global no vuelva «no apto» a un gateway que está perfectamente sano.
_SALUD_RE = re.compile(r"^/health(/ready)?$")

# Lo que no puede entrar en una clave de cubo. `:` es el separador de las claves
# de Redis que usa este módulo, así que un slug que lo contenga haría alias entre
# dos cubos distintos y fusionaría cuotas de entidades diferentes.
_SLUG_SEGURO_RE = re.compile(r"[^a-zA-Z0-9_-]")


def _current_minute_window() -> str:
    now = datetime.now(tz=UTC)
    return f"{now.year}{now.month:02d}{now.day:02d}{now.hour:02d}{now.minute:02d}"


def _client_ip(request: Request) -> str:
    """IP del cliente por saltos de CONFIANZA (ver `app/core/client_ip.py`).

    La versión anterior tomaba el primer valor de `X-Forwarded-For` sin más, así
    que el limitador se evadía rotando la cabecera: cada petición parecía venir de
    una IP distinta y solo quedaba en pie el tope global — que el mismo atacante
    podía agotar para tirar el servicio para todos.
    """
    return resolve_client_ip(request, getattr(request.app.state, "trusted_proxies", ""))


def _demasiadas_peticiones() -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "detail": "Demasiadas peticiones; espere un momento antes de reintentar",
            "status": 429,
        },
        headers={"Retry-After": "60"},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        redis = getattr(request.app.state, "redis", None)

        if redis is None:
            # Fail open — no Redis, skip rate limiting
            return await call_next(request)

        minute = _current_minute_window()
        ip = _client_ip(request)
        ip_key = f"ratelimit:{ip}:{minute}"

        # El cubo GLOBAL ya no se cuenta aquí: vive en
        # `RateLimitPorUsuarioMiddleware`, que es el más interno. Contarlo en este
        # —más externo— hacía que una petición rechazada por la cuota de una
        # PERSONA hubiera sumado ya al contador compartido de todas las entidades:
        # la misma regresión que este módulo declara cerrada para el eje por IP,
        # reaparecida al añadir el eje por usuario. Un limitador no puede gastar en
        # el cubo de terceros el tráfico que ya declaró abusivo.
        per_ip_limit: int = getattr(
            request.app.state, "rate_limit_per_ip", settings.rate_limit_per_ip
        )

        # Cubo dedicado y estrecho para las altas anónimas de radicación (PQRS y
        # ventanilla). Va en la MISMA clave por-IP (no en una aparte) para que el
        # tope estricto sustituya al general en esas rutas en vez de sumarse a él.
        es_radicacion_anonima = request.method == "POST" and bool(
            _RADICACION_ANONIMA_RE.match(request.url.path)
        )
        if es_radicacion_anonima:
            per_ip_limit = request.app.state.rate_limit_pqrs_per_ip
            ip_key = f"ratelimit:pqrs:{ip}:{minute}"
        elif _COSECHA_ANONIMA_RE.match(request.url.path):
            per_ip_limit = request.app.state.rate_limit_cosecha_per_ip
            ip_key = f"ratelimit:cosecha:{ip}:{minute}"
        elif _LOGIN_RE.match(request.url.path):
            per_ip_limit = getattr(
                request.app.state, "rate_limit_login_per_ip", settings.rate_limit_login_per_ip
            )
            ip_key = f"ratelimit:login:{ip}:{minute}"

        try:
            # El cubo por IP se resuelve PRIMERO y, si rechaza, se sale sin tocar
            # el global. Antes ambos INCR iban en el mismo pipeline y la decisión
            # venía después: cada petición que el propio limitador rechazaba
            # seguía sumando al contador compartido, de modo que un anónimo desde
            # UNA sola IP empujaba el global hasta el tope y dejaba en 429 a todos
            # los usuarios de todas las entidades. Un limitador no puede gastar en
            # el cubo de terceros el tráfico que ya declaró abusivo.
            pipe = redis.pipeline()
            pipe.incr(ip_key)
            pipe.expire(ip_key, 60)
            ip_count: int = (await pipe.execute())[0]

            if ip_count > per_ip_limit:
                return _demasiadas_peticiones()
        except Exception:
            # Fail open — Redis error, skip rate limiting
            pass

        return await call_next(request)


class RateLimitPorUsuarioMiddleware(BaseHTTPMiddleware):
    """Cuota por USUARIO AUTENTICADO, no por IP.

    Por qué existe, con el número que lo motivó: el limitador por IP rechazaba
    **antes** de autenticar —el log del gateway lo delata con `"usuario": null` y
    1,6 ms de duración—, así que la cuota se repartía por dirección de red. En una
    entidad pública lo normal es una oficina entera detrás de un NAT: cincuenta
    funcionarios con una sola IP saliente compartiendo cubo. Medido, una pantalla
    del sistema cuesta **7 peticiones al gateway**, de modo que el tope de 100/min
    daba **14 pantallas por minuto para toda la oficina**. El primero que
    trabajara deprisa dejaba a los demás con 429, y ninguno podía saber por qué.

    Los dos ejes son complementarios y miden cosas distintas, así que ninguno
    sustituye al otro:

    * **Por IP, antes de autenticar** (`RateLimitMiddleware`): frena avalanchas de
      quien todavía no ha probado ser nadie. Tiene que ir antes, porque validar un
      token cuesta, y pagar esa validación es exactamente lo que busca una
      avalancha.
    * **Por usuario, después de autenticar** (este): regula el uso normal. No se
      puede evadir rotando la identidad, porque la identidad ya viene **validada y
      firmada** por `JWTValidationMiddleware`; leer un `sub` del token sin
      verificarlo sería regalar cuota infinita a quien lo invente.

    Orden de registro: este middleware se registra **antes** de
    `JWTValidationMiddleware` en `main.py`. Starlette aplica los middlewares en
    orden inverso al de registro, así que el registrado antes es el más interno —
    es decir, el que se ejecuta **después**, con la identidad ya resuelta.

    Fail open igual que el cubo por IP: sin Redis o con Redis caído no se limita.
    Es la misma decisión de siempre en este proyecto para un control de
    disponibilidad —no de autorización—: quedarse cerrado dejaría el sistema
    inservible por una dependencia auxiliar.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        redis = getattr(request.app.state, "redis", None)
        if redis is None:
            return await call_next(request)

        minuto = _current_minute_window()
        cabeceras = getattr(request.state, "auth_headers", None)
        user_id = (cabeceras or {}).get("X-User-Id") or ""

        # El tenant entra en la clave porque el identificador de usuario solo es
        # único dentro de su entidad: sin él, dos personas de entidades distintas
        # con el mismo id compartirían cuota. Se SANEA antes de componerla: el
        # claim llega tal cual y un slug con `:` haría alias entre cubos
        # (`tenant "a:b" + usuario "c"` ≡ `tenant "a" + usuario "b:c"`).
        tenant = _SLUG_SEGURO_RE.sub("_", cabeceras.get("X-Tenant-Slug") or "") if cabeceras else ""

        try:
            # Cubo por PERSONA. Sin identidad —ruta pública, o token que no pasó—
            # no se cuenta aquí: ese tráfico lo cubre el cubo por IP, y meterlo en
            # una clave vacía juntaría a todos los anónimos en un mismo cubo, que
            # es el defecto que este eje viene a cerrar reaparecido en la puerta
            # pública.
            if user_id:
                clave = f"ratelimit:usuario:{tenant}:{user_id}:{minuto}"
                limite: int = getattr(
                    request.app.state,
                    "rate_limit_per_usuario",
                    settings.rate_limit_per_usuario,
                )
                pipe = redis.pipeline()
                pipe.incr(clave)
                pipe.expire(clave, 60)
                cuenta: int = (await pipe.execute())[0]
                if cuenta > limite:
                    return _demasiadas_peticiones()

            # Cubo por ENTIDAD, entre el de la persona y el de la plataforma.
            # Sin él, el techo de una entidad es el cubo global de TODAS: bastaban
            # ~33 personas de una sola trabajando a la vez para dejar a las demás
            # en 429, sin abuso ninguno. Va en este middleware porque es el único
            # que conoce el tenant.
            if tenant:
                clave_tenant = f"ratelimit:tenant:{tenant}:{minuto}"
                limite_tenant: int = getattr(
                    request.app.state,
                    "rate_limit_per_tenant",
                    settings.rate_limit_per_tenant,
                )
                pipe = redis.pipeline()
                pipe.incr(clave_tenant)
                pipe.expire(clave_tenant, 60)
                cuenta_tenant: int = (await pipe.execute())[0]
                if cuenta_tenant > limite_tenant:
                    return _demasiadas_peticiones()

            # Cubo GLOBAL, y se cuenta AQUÍ —en el middleware más interno— porque
            # solo aquí han decidido ya los otros dos ejes. Contarlo antes hacía
            # que lo rechazado por cuota de persona siguiera empujando el contador
            # compartido de todas las entidades.
            #
            # El chequeo de salud sí cuenta en su cubo por IP, pero no entra en el
            # global: si lo hiciera, un gateway sano se declararía enfermo justo
            # cuando más importa distinguir una cosa de la otra.
            if not _SALUD_RE.match(request.url.path):
                clave_global = f"{GLOBAL_RATE_KEY}:{minuto}"
                limite_global: int = getattr(
                    request.app.state, "rate_limit_global", settings.rate_limit_global
                )
                pipe = redis.pipeline()
                pipe.incr(clave_global)
                pipe.expire(clave_global, 60)
                cuenta_global: int = (await pipe.execute())[0]
                if cuenta_global > limite_global:
                    return _demasiadas_peticiones()
        except Exception:
            # Fail open — Redis error, skip rate limiting
            pass

        return await call_next(request)
