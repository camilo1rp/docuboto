# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Carga un certificado emitido OFFLINE sobre la credencial pendiente (ADR-047).

Antes era `POST /internal/signing-key/activate` con `X-Internal-Token`. Sin
tokens internos, la vía de operador es la misma que el resto de `auth.ops`:
acceso al contenedor, no HTTP. El operador que firmó el CSR ya validó la
identidad fuera de banda (`scripts/gen-user-cert.sh`).

Uso::

    docker compose exec -T app python -m auth.ops.activar_certificado \\
        --tenant mi-entidad --user-id <uuid> --cert /ruta/usuario.crt

Sale con código distinto de 0 si la credencial no existe, no está pendiente o
el certificado no lo emitió la CA de usuarios.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

import asyncpg
from orpycamcp_common.tenant import slug_valido, tenant_schema

from auth.services.signing_key_service import SigningKeyService


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        raise RuntimeError("DATABASE_URL no está definida")
    return raw.replace("postgresql+asyncpg://", "postgresql://")


async def activar(tenant: str, user_id: uuid.UUID, cert_pem: str) -> dict:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(f'SET search_path = "{tenant_schema(tenant)}"')
        estado = await SigningKeyService(conn).activar_certificado(user_id, cert_pem, tenant)
        return estado.model_dump(mode="json")
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--tenant", required=True)
    p.add_argument("--user-id", required=True, type=uuid.UUID)
    p.add_argument("--cert", required=True, help="ruta al PEM, o - para leer de stdin")
    args = p.parse_args(argv)
    if not slug_valido(args.tenant):
        print(f"slug de tenant inválido: {args.tenant!r}", file=sys.stderr)
        return 2
    pem = sys.stdin.read() if args.cert == "-" else Path(args.cert).read_text()
    try:
        estado = asyncio.run(activar(args.tenant, args.user_id, pem))
    except Exception as exc:  # noqa: BLE001 — el operador necesita el motivo
        print(f"no se pudo activar: {exc}", file=sys.stderr)
        return 1
    print(estado)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
