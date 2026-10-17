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
    """Veredicto único sobre si una entidad puede ser atendida (ADR-035)."""

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


async def _fila_entidad(conn: asyncpg.Connection, slug: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT t.status, t.provisioning_status, "
        "       to_regnamespace(format('%I', $2::text)) IS NOT NULL AS hay_schema "
        "FROM public.tenants t WHERE t.slug = $1",
        slug,
        tenant_schema(slug),
    )


async def estado_entidad(conn: asyncpg.Connection, slug: str) -> EstadoEntidad:
    """Estado de la entidad `slug` en una sola consulta a `public.tenants`.

    No captura excepciones: quien llama decide qué hace ante un registro que no
    responde (HTTP: 503 fail-closed; job: saltar la vuelta). Una entidad inexistente
    o con slug mal formado cuenta como INACTIVA, el mismo veredicto que una
    suspendida, así que no es un oráculo de existencia. `to_regnamespace` evita que
    una fila `active` sin schema acabe en `UndefinedTableError` (500).
    """
    if not slug_valido(slug):
        return EstadoEntidad.INACTIVA
    row = await _fila_entidad(conn, slug)
    if row is None or row["status"] != "active":
        return EstadoEntidad.INACTIVA
    if row["provisioning_status"] != "active" or not row["hay_schema"]:
        return EstadoEntidad.NO_PROVISIONADA
    return EstadoEntidad.OPERATIVA


class AtencionPublica(StrEnum):
    """Qué se sirve SIN autenticar a una entidad (ADR-035, decisión de 2026-10-16)."""

    COMPLETA = "completa"
    #: Entidad suspendida y aprovisionada: solo lecturas públicas (verificación,
    #: consulta, publicidad de la eliminación, OAI, CMIS). Las escrituras, no.
    SOLO_LECTURA = "solo_lectura"
    #: Inexistente, dada de baja o sin aprovisionar: el 404 neutro de siempre.
    NINGUNA = "ninguna"


async def atencion_publica(conn: asyncpg.Connection, slug: str) -> AtencionPublica:
    """Atención pública de `slug`. Como `estado_entidad`, no captura excepciones.

    Una entidad suspendida con su schema y su aprovisionamiento intactos sigue
    siendo legible desde fuera: el plazo de oposición del inventario de eliminación
    (Ac. AGN 004/2019), la verificación de un radicado (Ley 527 art. 12) y el acceso
    a la información pública (Ley 1712 arts. 9-11) no se detienen por una
    suspensión administrativa. Que la entidad exista no es un secreto.
    """
    if not slug_valido(slug):
        return AtencionPublica.NINGUNA
    row = await _fila_entidad(conn, slug)
    if row is None or row["provisioning_status"] != "active" or not row["hay_schema"]:
        return AtencionPublica.NINGUNA
    if row["status"] == "active":
        return AtencionPublica.COMPLETA
    if row["status"] == "suspended":
        return AtencionPublica.SOLO_LECTURA
    return AtencionPublica.NINGUNA


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

    Envuelve `estado_entidad` con el fail-closed común de los `get_tenant_conn`: si
    el registro no responde se niega (503), no se sigue sirviendo ante la duda.
    """
    try:
        estado = await estado_entidad(conn, slug)
    except Exception:  # noqa: BLE001 — no poder saberlo se responde como «no»
        return ERROR_ESTADO_NO_DISPONIBLE
    if estado is EstadoEntidad.OPERATIVA:
        return None
    return ERRORES_ENTIDAD[estado]
