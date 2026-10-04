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
