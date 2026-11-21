# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Un solo validador del slug de tenant y del nombre de su schema (ADR-002/012).

El slug llega del JWT o de la cabecera `X-Tenant-Slug` y acaba interpolado como
identificador DDL (`SET search_path = "tenant_{slug}"`), que no admite `$1`. Por eso
la forma se valida con un patrón anclado y de largo acotado ANTES de interpolar.

Vivía copiado en 14 sitios con cinco cotas distintas (`{0,54}`, `{0,98}`, `{1,62}`
y el `^[a-z0-9-]+$` del alta) y ningún guardián de coincidencia: una copia futura
sin ancla, o con `_`, habría permitido colisiones o salirse del entrecomillado
(AREA-02-05). Aquí está la única definición; los servicios la importan.

Cota 54: el schema es `tenant_{slug}` y PostgreSQL trunca los identificadores a 63
bytes SIN avisar; `tenant_` ocupa 7, así que 54 es lo que cabe. Dos slugs largos de
prefijo común compartirían schema (fuga entre entidades). Sin guion ni al inicio:
el alta y todos los servicios coinciden, así no se crea una entidad que ningún
servicio acepte.
"""

from __future__ import annotations

import re
from enum import StrEnum

import asyncpg

#: Largo máximo del slug: 63 bytes de identificador de PostgreSQL menos `tenant_`.
SLUG_MAX = 54
#: Patrón único del slug (anclado; solo minúsculas, dígitos y guion; sin guion inicial).
SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{1,53}$"
SLUG_RE = re.compile(SLUG_PATTERN)


def slug_valido(slug: object) -> bool:
    """¿Es `slug` un slug de tenant bien formado? (cadena, no vacía, patrón anclado)."""
    return isinstance(slug, str) and SLUG_RE.fullmatch(slug) is not None


def tenant_schema(slug: str) -> str:
    """Nombre de schema `tenant_{slug}` (guiones a `_`).

    Raises:
        ValueError: si el slug no cumple el patrón. Los servicios lo traducen a su
            respuesta (400 en el borde HTTP, fallo del job en los procesos de fondo).
    """
    if not slug_valido(slug):
        raise ValueError(f"slug de tenant inválido: {slug!r}")
    return f"tenant_{slug.replace('-', '_')}"


class EstadoEntidad(StrEnum):
    """Veredicto sobre si una entidad puede ser atendida (ADR-035, ADR-047 §3).

    Sin estados de ciclo de vida: una instalación es una entidad. Lo único que
    se comprueba es que exista su fila y su schema.
    """

    OPERATIVA = "operativa"
    INACTIVA = "inactiva"
    NO_PROVISIONADA = "no_provisionada"


#: Cuerpo de error común por estado no operativo: (HTTP, error, detalle).
ERRORES_ENTIDAD: dict[EstadoEntidad, tuple[int, str, str]] = {
    EstadoEntidad.INACTIVA: (
        403,
        "tenant_inactivo",
        "Esta entidad no está activa en la plataforma.",
    ),
    EstadoEntidad.NO_PROVISIONADA: (
        503,
        "tenant_no_provisionado",
        "Esta entidad aún no está disponible.",
    ),
}


async def _hay_schema(conn: asyncpg.Connection, slug: str) -> bool | None:
    """`None` si no hay fila; si la hay, si existe su schema."""
    return await conn.fetchval(
        "SELECT to_regnamespace(format('%I', $2::text)) IS NOT NULL "
        "FROM public.tenants WHERE slug = $1",
        slug,
        tenant_schema(slug),
    )


async def estado_entidad(conn: asyncpg.Connection, slug: str) -> EstadoEntidad:
    """Estado de la entidad `slug` en una consulta a `public.tenants`.

    No captura excepciones: quien llama decide qué hace ante un registro que no
    responde (HTTP: 503 fail-closed; job: saltar la vuelta). Inexistente o slug
    mal formado = INACTIVA, así que no es un oráculo de existencia.
    `to_regnamespace` evita que una fila sin schema acabe en `UndefinedTableError`.
    """
    if not slug_valido(slug):
        return EstadoEntidad.INACTIVA
    hay = await _hay_schema(conn, slug)
    if hay is None:
        return EstadoEntidad.INACTIVA
    return EstadoEntidad.OPERATIVA if hay else EstadoEntidad.NO_PROVISIONADA


async def entidad_publica(conn: asyncpg.Connection, slug: str) -> bool:
    """¿Se atiende a `slug` sin autenticar? Existe y tiene schema; si no, el 404 neutro."""
    return await estado_entidad(conn, slug) is EstadoEntidad.OPERATIVA


#: Error cuando el registro de entidades no responde (fail-closed).
ERROR_ESTADO_NO_DISPONIBLE: tuple[int, str, str] = (
    503,
    "tenant_estado_no_disponible",
    "No se pudo comprobar el estado de la entidad.",
)


async def veredicto_entidad(
    conn: asyncpg.Connection, slug: str
) -> tuple[int, str, str] | None:
    """`None` si la entidad es operativa; si no, `(HTTP, error, detalle)` a responder.

    Si el registro no responde se niega (503): no se sirve ante la duda.
    """
    try:
        estado = await estado_entidad(conn, slug)
    except Exception:  # noqa: BLE001 — no poder saberlo se responde como «no»
        return ERROR_ESTADO_NO_DISPONIBLE
    if estado is EstadoEntidad.OPERATIVA:
        return None
    return ERRORES_ENTIDAD[estado]


async def asistente_habilitado(conn: asyncpg.Connection, slug: str) -> bool:
    """¿La entidad `slug` tiene activada la capa de conocimiento? (ADR-037)

    Lee `public.tenants.asistente_habilitado` (nombre de columna conservado). Fila
    ausente o slug mal formado cuentan como apagado (el mismo fail-closed de
    `estado_entidad`). NO captura excepciones: quien llama decide qué hace si el
    registro no responde (HTTP: 503; `/auth/me`: `False`). Sin caché a propósito:
    apagar es el acto que debe ser inmediato.
    """
    if not slug_valido(slug):
        return False
    return bool(
        await conn.fetchval(
            "SELECT asistente_habilitado FROM public.tenants WHERE slug = $1", slug
        )
    )


async def intelligence_provider(conn: asyncpg.Connection, slug: str) -> str:
    """``ppi`` o ``local`` (ADR-045 §4). Cualquier otra cosa, o un fallo de forma, es ``local``.

    No captura excepciones: ``/auth/me`` responde ``local`` si el registro no
    responde. Sin caché: el cambio de proveedor tiene que verse enseguida.
    """
    if not slug_valido(slug):
        return "local"
    valor = await conn.fetchval(
        "SELECT intelligence_provider FROM public.tenants WHERE slug = $1", slug
    )
    return "ppi" if valor == "ppi" else "local"


#: Cuerpos de error comunes de la bandera de la capa de conocimiento (ADR-037):
#: (HTTP, error, detalle). Los códigos `asistente_*` conservan el nombre histórico.
ERROR_ASISTENTE_DESHABILITADO: tuple[int, str, str] = (
    403,
    "asistente_deshabilitado",
    "Esta entidad no tiene activada la capa de conocimiento.",
)
ERROR_ASISTENTE_NO_DISPONIBLE: tuple[int, str, str] = (
    503,
    "asistente_estado_no_disponible",
    "No se pudo comprobar si la entidad tiene activada la capa de conocimiento.",
)
