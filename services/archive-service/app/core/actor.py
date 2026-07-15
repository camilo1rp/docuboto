# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Identidad del llamante autenticado (E12/H-D, RF-SEG-08 / Ac. AGN 001/2024 art. 4.3.2.2).

Punto ÚNICO donde se resuelve el actor de un acto de custodia — antes cada router
tenía su propia variante de "X-User-Id o X-Username", y las dos variantes
DIVERGÍAN en cuál priorizar (`routers/expedientes.py` ya usaba X-User-Id primero;
`routers/transferencias.py::_actor` usaba X-Username primero). El mismo
funcionario quedaba escrito con dos identidades distintas en `audit_log`
INDELEBLE según la ruta por la que entrara, y la responsabilidad dejaba de ser
reconstruible.

Fuente de verdad: `X-User-Id` (UUID de `auth_users.id`), SIEMPRE — un username es
mutable/reasignable (cambio de nombre, baja y reutilización del alias), y
`expediente_eventos.actor` es una columna UUID tipada; poblarla desde un string
exigiría un lookup inverso username→id que no es inyectivo en el tiempo.
`X-Username` se conserva solo como atributo DESCRIPTIVO (payload), nunca como
identidad. No hay fallback de uno a otro: quien no traiga `X-User-Id` válido no
tiene actor resoluble para un acto de custodia (fail-closed).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Header, HTTPException, status


@dataclass(frozen=True)
class Actor:
    """Identidad del llamante. `id` es la fuente de verdad; `username` es
    puramente descriptivo (nunca se usa como clave de identidad)."""

    id: uuid.UUID
    username: str | None = None

    @property
    def audit_actor(self) -> str:
        """Lo que se escribe en `audit_log.actor` y en columnas TEXT de
        identidad (`creada_por`/`enviada_por`/`decidida_por`): SIEMPRE el UUID
        como string, nunca el username."""
        return str(self.id)


def _parse_user_id(x_user_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_user_id",
                "detail": "X-User-Id must be a valid UUID",
                "status": 400,
            },
        )


async def require_actor(
    x_user_id: str | None = Header(default=None),
    x_username: str | None = Header(default=None),
) -> Actor:
    """Dependencia FastAPI para actos de custodia (create/enviar/recibir/
    rechazar transferencias; close/transfer de expedientes): exige un
    `X-User-Id` válido, sin fallback a `X-Username`. En la práctica ya es
    inalcanzable sin él — `require_permission` exige la misma cabecera para
    autorizar la operación (min_crud=3) — pero el fail-closed queda explícito
    aquí en vez de depender de ese acoplamiento implícito."""
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_user_id",
                "detail": "X-User-Id is required to identify the actor of this action",
                "status": 400,
            },
        )
    return Actor(id=_parse_user_id(x_user_id), username=x_username)


async def optional_actor(
    x_user_id: str | None = Header(default=None),
    x_username: str | None = Header(default=None),
) -> Actor | None:
    """Gemelo de `require_actor` para rutas de solo-LECTURA (FUID, list, get):
    `None` si no hay `X-User-Id` (llamante anónimo/sin identificar), en vez de
    fail-closed 400 — leer no es un acto de custodia."""
    if not x_user_id:
        return None
    return Actor(id=_parse_user_id(x_user_id), username=x_username)
