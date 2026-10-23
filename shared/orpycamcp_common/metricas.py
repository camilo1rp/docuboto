# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Métricas operativas mínimas en formato de texto de Prometheus 0.0.4 (C18, ADR-041).

Exportador PROPIO, sin ``prometheus_client``: todo lo que se publica son
*gauges* de estado leídos de la base con caché, no contadores en proceso; el
registro por defecto de la librería empuja a emitir 0 donde queremos ausencia, y
añadirla serían seis dependencias nuevas para pintar nueve líneas.

Tres reglas que este módulo hace cumplir (no son convención):

1. **Frontera por admisión.** :data:`FAMILIAS` es el registro ÚNICO: lo que no
   está aquí no se puede emitir, y una etiqueta solo admite los valores de su
   conjunto cerrado. Ninguna familia lleva etiqueta de tenant, slug, usuario,
   radicado ni id: el exportador publica agregados, nunca identidad.
2. **«No verificado» no es 0.** Si la medición de una señal falla en CUALQUIER
   tenant (tabla ausente, timeout, error SQL, valor fuera del registro) se
   emite ``orpycamcp_signal_measured{signal=…} 0`` y NINGUNA serie de valor de
   esa señal. Nunca una suma parcial (subestimaría). Cuando sí se midió, el 0 es
   verdad y se emite.
3. **Medir es perezoso y acotado.** Sin tarea de fondo: se mide al ser scrapeado,
   con caché TTL (``METRICS_TTL_SECONDS``, 30 s), una sola medición en vuelo, un
   plazo total (10 s) y ``statement_timeout`` local en una transacción de solo
   lectura. Nadie scrapea ⇒ cero carga.

Defensa en profundidad: :func:`responder` contesta 404 a toda petición con
``X-Forwarded-For``/``Forwarded``/``X-Real-IP``. Prometheus no las manda;
nginx y el gateway sí. Si un día una ruta mal enrutada llevara tráfico externo
hasta aquí, no publicaría nada. NO sustituye a la red.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from orpycamcp_common import deadletter
from orpycamcp_common.tenant import tenant_schema

logger = logging.getLogger(__name__)

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

TTL_POR_DEFECTO_S = 30.0
PLAZO_POR_DEFECTO_S = 10.0

# Cabeceras que añaden proxies (nginx, gateway). Prometheus no las envía.
CABECERAS_DE_PROXY = ("x-forwarded-for", "forwarded", "x-real-ip")

SENALES = frozenset({"dlq", "worm", "audit_chain"})
ARTEFACTOS_WORM = frozenset(
    {
        "expediente_indice",
        "expediente_aip",
        "acta_transferencia",
        "acta_firma_personal",
        "acta_eliminacion",
        "excepcion_firma",
        "revocacion_excepcion",
    }
)
FASES_WORM = frozenset({"reconcile", "renewal", "packaging"})


@dataclass(frozen=True, slots=True)
class Familia:
    """Una familia de métricas: nombre, ayuda y etiquetas con valores CERRADOS."""

    nombre: str
    ayuda: str
    etiquetas: Mapping[str, frozenset[str]] = field(default_factory=dict)
    tipo: str = "gauge"


def _f(nombre: str, ayuda: str, **etiquetas: frozenset[str]) -> tuple[str, Familia]:
    return nombre, Familia(nombre, ayuda, dict(etiquetas))


#: Registro único de las nueve familias (ADR-041). Orden = orden de emisión.
FAMILIAS: dict[str, Familia] = dict(
    [
        _f(
            "orpycamcp_signal_measured",
            "1 si la ultima medicion de la senal fue completa en todos los tenants; "
            "0 si no esta verificada (no es lo mismo que 'cero problemas').",
            signal=SENALES,
        ),
        _f(
            "orpycamcp_signal_last_success_timestamp_seconds",
            "Epoch de la ultima medicion completa de la senal (ausente si nunca).",
            signal=SENALES,
        ),
        _f(
            "orpycamcp_dlq_pending_events",
            "Eventos pendientes en la dead-letter (status=pending) del grupo propio del servicio, "
            "sumados sobre tenants.",
        ),
        _f(
            "orpycamcp_dlq_oldest_pending_timestamp_seconds",
            "Epoch del evento pendiente mas antiguo de la dead-letter (ausente si no hay pendientes).",
        ),
        _f(
            "orpycamcp_worm_exhausted_artifacts",
            "Artefactos WORM cuyo reintento se agoto, por artefacto y fase, sumados sobre tenants.",
            artifact=ARTEFACTOS_WORM,
            phase=FASES_WORM,
        ),
        _f(
            "orpycamcp_audit_chain_breaks",
            "Roturas NO declaradas de la cadena de auditoria en la ultima corrida completa.",
        ),
        _f(
            "orpycamcp_audit_chain_declared_breaks",
            "Roturas historicas declaradas (linea base aprobada) en la ultima corrida completa.",
        ),
        _f(
            "orpycamcp_audit_chain_verification_ok",
            "1 si la ultima corrida completa de verificacion salio con codigo 0.",
        ),
        _f(
            "orpycamcp_audit_chain_last_verification_timestamp_seconds",
            "Epoch en que termino la ultima corrida completa de verificacion de la cadena.",
        ),
    ]
)

# Etiquetas que NUNCA pueden existir (defensa del registro, vigilada por test).
ETIQUETAS_PROHIBIDAS = ("tenant", "slug", "user", "id", "radicado", "expediente")


@dataclass(frozen=True, slots=True)
class Muestra:
    familia: str
    valor: float
    etiquetas: Mapping[str, str] = field(default_factory=dict)


class MetricaInvalida(ValueError):
    """Una muestra fuera del registro: se trata como fallo de medición, no se emite."""


class SinMedicion(RuntimeError):
    """La señal no tiene de dónde medirse todavía (p. ej. ninguna corrida completa)."""


def _validar(m: Muestra) -> Familia:
    fam = FAMILIAS.get(m.familia)
    if fam is None:
        raise MetricaInvalida(f"familia fuera del registro: {m.familia!r}")
    if set(m.etiquetas) != set(fam.etiquetas):
        raise MetricaInvalida(
            f"{m.familia}: etiquetas {sorted(m.etiquetas)} != declaradas {sorted(fam.etiquetas)}"
        )
    for k, v in m.etiquetas.items():
        if v not in fam.etiquetas[k]:
            raise MetricaInvalida(f"{m.familia}: valor {v!r} fuera del conjunto cerrado de {k!r}")
    if isinstance(m.valor, bool) or not isinstance(m.valor, (int, float)) or not math.isfinite(m.valor):
        raise MetricaInvalida(f"{m.familia}: valor no numerico o no finito: {m.valor!r}")
    return fam


def _escapar(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _numero(v: float) -> str:
    return str(int(v)) if float(v).is_integer() and abs(v) < 1e15 else repr(float(v))


def render(muestras: Sequence[Muestra]) -> str:
    """Texto 0.0.4: HELP/TYPE una vez por familia, familias en orden del registro.

    Lanza :class:`MetricaInvalida` ante cualquier muestra fuera del registro.
    """
    for m in muestras:
        _validar(m)
    lineas: list[str] = []
    for nombre, fam in FAMILIAS.items():
        propias = [m for m in muestras if m.familia == nombre]
        if not propias:
            continue
        lineas.append(f"# HELP {nombre} {fam.ayuda.replace(chr(10), ' ')}")
        lineas.append(f"# TYPE {nombre} {fam.tipo}")
        for m in propias:
            if m.etiquetas:
                par = ",".join(
                    f'{k}="{_escapar(m.etiquetas[k])}"' for k in fam.etiquetas  # orden del registro
                )
                lineas.append(f"{nombre}{{{par}}} {_numero(m.valor)}")
            else:
                lineas.append(f"{nombre} {_numero(m.valor)}")
    return "\n".join(lineas) + "\n"


@dataclass(frozen=True, slots=True)
class Resultado:
    ok: bool
    muestras: tuple[Muestra, ...]
    ultimo_exito: float | None


Medidor = Callable[[asyncpg.Connection], Awaitable[list[Muestra]]]


class Senal:
    """Una señal medible: caché TTL, una medición en vuelo, plazo y solo lectura.

    ``medir`` recibe una conexión ya dentro de una transacción de solo lectura con
    ``statement_timeout`` local; devuelve las muestras de VALOR de la señal (las de
    ``signal_measured``/``last_success`` las añade el exportador). Cualquier
    excepción —incluida una muestra fuera del registro o de otra señal— hace que
    la señal salga «no verificada» entera.
    """

    def __init__(
        self,
        nombre: str,
        medir: Medidor,
        *,
        familias: frozenset[str],
        ttl_s: float | None = None,
        timeout_s: float = PLAZO_POR_DEFECTO_S,
        reloj: Callable[[], float] = time.monotonic,
        ahora: Callable[[], float] = time.time,
    ) -> None:
        if nombre not in SENALES:
            raise ValueError(f"senal fuera del registro: {nombre!r}")
        desconocidas = familias - FAMILIAS.keys()
        if desconocidas:
            raise ValueError(f"familias fuera del registro: {sorted(desconocidas)}")
        self.nombre = nombre
        self._medir = medir
        self._familias = familias
        self._ttl_s = ttl_ambiente() if ttl_s is None else ttl_s
        self._timeout_s = timeout_s
        self._reloj = reloj
        self._ahora = ahora
        self._lock = asyncio.Lock()
        self._cache: Resultado | None = None
        self._medido_en: float = 0.0
        self._ultimo_exito: float | None = None

    async def obtener(self, pool: asyncpg.Pool) -> Resultado:
        async with self._lock:
            if self._cache is not None and (self._reloj() - self._medido_en) < self._ttl_s:
                return self._cache
            try:
                muestras = await asyncio.wait_for(self._medir_en_tx(pool), self._timeout_s)
                fuera = {m.familia for m in muestras} - self._familias
                if fuera:
                    raise MetricaInvalida(f"{self.nombre}: familias ajenas a la senal: {sorted(fuera)}")
                render(muestras)  # valida contra el registro ANTES de aceptar nada
                self._ultimo_exito = self._ahora()
                resultado = Resultado(True, tuple(muestras), self._ultimo_exito)
            except Exception as exc:  # noqa: BLE001 - cualquier fallo = no verificado, nunca 0
                logger.warning("metrica '%s' no verificada: %s: %s", self.nombre, type(exc).__name__, exc)
                resultado = Resultado(False, (), self._ultimo_exito)
            self._cache = resultado
            self._medido_en = self._reloj()
            return resultado

    async def _medir_en_tx(self, pool: asyncpg.Pool) -> list[Muestra]:
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                # `SET LOCAL` no admite parámetros; el valor es un entero propio.
                await conn.execute(f"SET LOCAL statement_timeout = {int(self._timeout_s * 1000)}")
                return await self._medir(conn)


def ttl_ambiente() -> float:
    """``METRICS_TTL_SECONDS`` (30 por defecto). Inválido o negativo ⇒ el valor por defecto."""
    crudo = os.environ.get("METRICS_TTL_SECONDS", "").strip()
    try:
        valor = float(crudo) if crudo else TTL_POR_DEFECTO_S
    except ValueError:
        return TTL_POR_DEFECTO_S
    return valor if valor >= 0 else TTL_POR_DEFECTO_S


def _no_encontrada() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "Not Found", "status": 404},
    )


def detras_de_proxy(request: Request) -> bool:
    return any(c in request.headers for c in CABECERAS_DE_PROXY)


def _muestras_de(senal: Senal, res: Resultado) -> list[Muestra]:
    out = [Muestra("orpycamcp_signal_measured", 1 if res.ok else 0, {"signal": senal.nombre})]
    if res.ultimo_exito is not None:
        out.append(
            Muestra(
                "orpycamcp_signal_last_success_timestamp_seconds",
                res.ultimo_exito,
                {"signal": senal.nombre},
            )
        )
    if res.ok:
        out.extend(res.muestras)
    return out


async def responder(request: Request, pool: asyncpg.Pool, senales: Sequence[Senal]) -> Response:
    """Cuerpo de ``GET /metrics``. 404 si la petición pasó por un proxy."""
    if detras_de_proxy(request):
        return _no_encontrada()
    muestras: list[Muestra] = []
    for senal in senales:
        muestras.extend(_muestras_de(senal, await senal.obtener(pool)))
    return Response(content=render(muestras), media_type=CONTENT_TYPE)


# ---------------------------------------------------------------------------
# Tenants y señal de dead-letter (compartida por workflow/signature/notification/knowledge)
# ---------------------------------------------------------------------------


async def esquemas_a_medir(conn: asyncpg.Connection) -> list[str]:
    """Esquemas de TODOS los tenants (también suspendidos: su DLQ y su WORM existen).

    Solo se omite una entidad con aprovisionamiento pendiente (aún sin esquema).
    Una entidad activa sin esquema NO se omite: la consulta fallará y la señal
    saldrá «no verificada», que es lo honesto.
    """
    filas = await conn.fetch(
        "SELECT slug FROM public.tenants WHERE provisioning_status = 'active' ORDER BY slug"
    )
    return [tenant_schema(f["slug"]) for f in filas]


def senal_dlq(grupo: str, **kw: float) -> Senal:
    """Señal ``dlq`` del servicio cuyo ``origin_group`` es *grupo*."""

    async def medir(conn: asyncpg.Connection) -> list[Muestra]:
        n, mas_antiguo = await deadletter.contar_pendientes(
            conn, await esquemas_a_medir(conn), [grupo]
        )
        out = [Muestra("orpycamcp_dlq_pending_events", n)]
        if n > 0 and mas_antiguo is not None:
            out.append(
                Muestra("orpycamcp_dlq_oldest_pending_timestamp_seconds", mas_antiguo.timestamp())
            )
        return out

    return Senal(
        "dlq",
        medir,
        familias=frozenset(
            {"orpycamcp_dlq_pending_events", "orpycamcp_dlq_oldest_pending_timestamp_seconds"}
        ),
        **kw,
    )


__all__ = [
    "FAMILIAS",
    "Muestra",
    "Senal",
    "SinMedicion",
    "esquemas_a_medir",
    "render",
    "responder",
    "senal_dlq",
]
