# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
Catálogo de tipos documentales: UNA sola comprobación para todas las puertas.

`tipos_documentales` lo administra archive-service pero vive en el MISMO schema
del tenant (`tenant_{slug}`), como `urd_usuaroldep` o `auth_users`: se lee con la
conexión del request, sin cliente HTTP (misma práctica que el resto del servicio).

Un `doc_class` que no es un código activo del catálogo deja un radicado sin
serie TRD, y sin serie no hay retención ni disposición final (Ley 594/2000
art. 24). Por eso se comprueba ANTES de consumir el consecutivo, que no se
devuelve (Ac. AGN 060/2001): el rechazo no puede costar un número.
"""

from __future__ import annotations

import asyncpg
from fastapi import HTTPException, status


async def nombre_tipo_documental(
    conn: asyncpg.Connection, code: str, *, solo_ventanilla: bool = False
) -> str | None:
    """Nombre del tipo ACTIVO con ese código, o `None` si no existe / está inactivo."""
    extra = " AND admite_ventanilla_publica" if solo_ventanilla else ""
    nombre = await conn.fetchval(
        f"SELECT nombre FROM tipos_documentales WHERE code = $1 AND activo{extra}", code
    )
    return str(nombre) if nombre is not None else None


async def exigir_tipo_documental(conn: asyncpg.Connection, code: str | None) -> None:
    """422 `tipo_documental_desconocido` si `code` no es un tipo activo del catálogo.

    `None`/vacío no se juzga aquí: que el tipo sea obligatorio lo decide
    `datos_minimos` según el tipo de radicado (la Entrada puede radicarse sin
    tipificar y completarse después).
    """
    if not code:
        return
    if await nombre_tipo_documental(conn, code) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "tipo_documental_desconocido",
                "detail": (
                    f"El tipo documental '{code}' no existe o está inactivo en el "
                    "catálogo de la entidad. Consulte los tipos disponibles."
                ),
                "status": 422,
            },
        )
