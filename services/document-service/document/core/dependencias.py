# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""La dependencia se correlaciona por CÓDIGO (B5, ADR-032) — resolución del alias.

El campo canónico es `*_dept_code`. `*_dept` (nombre) sobrevive como ALIAS
deprecado un ciclo: se resuelve con `resolver_codigo_dependencia(nombre, false)`
(migración 013 de tenant-service: UNA definición del criterio, solo dependencias
ACTIVAS con ese nombre actual). Un nombre que casa con dos NO se desempata
—`ORDER BY codigo LIMIT 1` radicaba hacia la menor en silencio—: 422.

El nombre que se guarda como instantánea lo pone el SERVIDOR desde el catálogo,
nunca el texto del cliente.
"""

from __future__ import annotations

import logging

import asyncpg
from fastapi import HTTPException, Response, status

logger = logging.getLogger(__name__)

DOC_ALIAS = "/docs/es/api.md#dependencias-por-codigo"


def _error(error: str, detail: str, **extra: object) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error": error, "detail": detail, "status": 422, **extra},
    )


def dependencia_desconocida(campo: str, valor: object) -> HTTPException:
    return _error(
        "dependencia_desconocida",
        f"{campo}: no existe una dependencia ACTIVA con «{valor}»",
        campo=campo,
    )


def dependencia_ambigua(campo: str, nombre: str, candidatos: int) -> HTTPException:
    return _error(
        "dependencia_ambigua",
        f"{campo}: el nombre «{nombre}» corresponde a {candidatos} dependencias; envíe el código",
        campo=campo,
        candidatos=candidatos,
    )


def dependencia_inconsistente(campo: str, codigo: int, nombre: str, catalogo: str) -> HTTPException:
    return _error(
        "dependencia_inconsistente",
        f"{campo}: el código {codigo} es «{catalogo}», no «{nombre}»; envíe solo el código",
        campo=campo,
    )


async def normalizar_dependencia(
    conn: asyncpg.Connection,
    *,
    campo: str,
    codigo: int | None,
    nombre: str | None,
    estricto: bool = True,
) -> tuple[str | None, int | None, bool]:
    """(nombre instantánea, código, ¿se usó el alias?) para un par nombre/código.

    - código: debe ser una dependencia ACTIVA (422 `dependencia_desconocida`); si
      además llega un nombre, debe ser el del catálogo (422
      `dependencia_inconsistente`). El nombre devuelto es el del catálogo.
    - solo nombre (alias): 1 candidato -> su código; >=2 -> 422
      `dependencia_ambigua`; 0 -> 422 `dependencia_desconocida` si `estricto`;
      si no, el texto queda como instantánea sin código (destino que puede no ser
      una dependencia, p. ej. la Salida a un tercero).
    """
    if codigo is not None:
        catalogo = await conn.fetchval(
            "SELECT nombre FROM dependencias WHERE codigo = $1 AND activo", codigo
        )
        if catalogo is None:
            raise dependencia_desconocida(campo, codigo)
        if nombre is not None and nombre != catalogo:
            raise dependencia_inconsistente(campo, codigo, nombre, catalogo)
        return str(catalogo), codigo, False
    if nombre is None:
        return None, None, False
    fila = await conn.fetchrow("SELECT * FROM resolver_codigo_dependencia($1, false)", nombre)
    candidatos = int(fila["candidatos"]) if fila else 0
    if candidatos == 1:
        return nombre, int(fila["codigo"]), True
    if candidatos >= 2:
        raise dependencia_ambigua(campo, nombre, candidatos)
    if estricto:
        raise dependencia_desconocida(campo, nombre)
    return nombre, None, True


async def exigir_concordancia(
    conn: asyncpg.Connection, *, campo: str, codigo: int | None, nombre: str | None
) -> None:
    """422 `dependencia_inconsistente` si llegan código Y nombre y no son la misma.

    Solo compara; que el código exista o esté activo lo sigue decidiendo quien
    radica (`invalid_dependencia`). El nombre del cliente nunca se guarda.
    """
    if codigo is None or nombre is None:
        return
    catalogo = await conn.fetchval("SELECT nombre FROM dependencias WHERE codigo = $1", codigo)
    if catalogo is not None and catalogo != nombre:
        raise dependencia_inconsistente(campo, codigo, nombre, catalogo)


def usa_alias(codigo: int | None, nombre: str | None) -> bool:
    """¿El cliente envió el NOMBRE sin el código? (alias deprecado)"""
    return nombre is not None and codigo is None


def marcar_alias_deprecado(response: Response, endpoint: str) -> None:
    """`Deprecation` + `Link` y un warning para medir cuándo se puede retirar el alias."""
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = f'<{DOC_ALIAS}>; rel="deprecation"'
    logger.warning("alias_dependencia_por_nombre_deprecado endpoint=%s", endpoint)
