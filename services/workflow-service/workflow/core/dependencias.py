# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""La dependencia se correlaciona por CÓDIGO (B5, ADR-032) — errores y alias del contrato.

Campo canónico: `*_dept_code` (entero, `dependencias.codigo`). El NOMBRE (`*_dept`)
sobrevive un ciclo como ALIAS deprecado: se resuelve con el criterio único de
tenant-service (`resolver_codigo_dependencia`, migración 013) y nunca se desempata:
un nombre que casa con dos dependencias es 422 `dependencia_ambigua`, no «la de menor
código». El nombre que se GUARDA como instantánea lo pone el servidor desde el
catálogo, jamás el texto del cliente.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Response, status

logger = logging.getLogger(__name__)

DOC_ALIAS = "/docs/es/api.md#dependencias-por-codigo"


def _422(error: str, detail: str, **extra: object) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"error": error, "detail": detail, "status": 422, **extra},
    )


def dependencia_desconocida(field: str, valor: object) -> HTTPException:
    return _422(
        "dependencia_desconocida",
        f"'{valor}' no es una dependencia activa del catálogo ({field})",
        field=field,
    )


def dependencia_ambigua(field: str, nombre: str, candidatos: int) -> HTTPException:
    return _422(
        "dependencia_ambigua",
        f"El nombre '{nombre}' corresponde a {candidatos} dependencias ({field}); envíe el código",
        field=field,
        candidatos=candidatos,
    )


def dependencia_inconsistente(field: str, codigo: int, nombre: str, catalogo: str) -> HTTPException:
    return _422(
        "dependencia_inconsistente",
        f"{field}: el código {codigo} es '{catalogo}', no '{nombre}'; envíe solo el código",
        field=field,
    )


def marcar_alias_deprecado(response: Response, endpoint: str) -> None:
    """`Deprecation` + `Link` y un warning: el log mide cuándo se puede retirar el alias."""
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = f'<{DOC_ALIAS}>; rel="deprecation"'
    logger.warning("alias_dependencia_por_nombre_deprecado endpoint=%s", endpoint)
