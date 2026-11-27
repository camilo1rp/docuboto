# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Rota la clave de firma de los JWT (ADR-048).

La vigente se marca `retired_at` y sigue en la JWKS `JWT_KEY_OVERLAP_S` segundos
(una hora por defecto), así que los tokens ya emitidos siguen verificando y
el proveedor PPI no pierde el de servicio. La nueva firma desde la próxima
lectura del llavero (como mucho un minuto en el proceso que corre).

Uso::

    python -m auth.ops.rotate_jwt_key
"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg
from orpycamcp_common import audit

from auth.core import security


async def run() -> int:
    crudo = os.environ.get("DATABASE_URL", "")
    if not crudo:
        print("ERROR: DATABASE_URL no está definida", file=sys.stderr)
        return 1
    conn = await asyncpg.connect(dsn=crudo.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        kid = await security.rotar(conn)
        await audit.append(
            conn,
            tenant_slug=None,
            service="auth-service",
            action="auth.jwt_key_rotated",
            object_type="jwt_key",
            object_ref=kid,
            actor="system:ops-rotate-jwt-key",
            payload={"kid": kid},
            canal="system",
        )
    finally:
        await conn.close()
    print(f"OK: clave de firma nueva kid={kid}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
