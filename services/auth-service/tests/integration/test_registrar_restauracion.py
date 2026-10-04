# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`registrar_restauracion` contra Postgres REAL (T14).

Fija: el mapa `version_id viejo -> nuevo` actualiza `preservacion_worm_objeto` y deja
el asiento `storage.restauracion` en la MISMA transacción. Todo dentro de una
transacción que SIEMPRE se revierte (`audit_log` es append-only).

Mutación que lo detecta: omitir el `UPDATE` (cae la fila actualizada); omitir el
`audit.append` (cae el asiento); poner el `append` fuera de la transacción (cae la
atomicidad: con el asiento forzado a fallar, la fila debe quedar como estaba).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncGenerator

import asyncpg
import pytest
import pytest_asyncio
from orpycamcp_common import audit

from app.ops import registrar_restauracion as job

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def tx(db_conn: asyncpg.Connection) -> AsyncGenerator[asyncpg.Connection, None]:
    t = db_conn.transaction()
    await t.start()
    try:
        yield db_conn
    finally:
        await t.rollback()


async def _tenant(conn: asyncpg.Connection) -> tuple[str, str]:
    slug = f"itest-rst-{uuid.uuid4().hex[:6]}"
    schema = f"tenant_{slug.replace('-', '_')}"
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await conn.execute(
        f'CREATE TABLE "{schema}".preservacion_worm_objeto ('
        "id UUID PRIMARY KEY DEFAULT gen_random_uuid(), bucket TEXT NOT NULL, "
        "object_key TEXT NOT NULL, version_id TEXT)"
    )
    return slug, schema


async def test_el_mapa_actualiza_la_fila_y_deja_el_asiento(tx: asyncpg.Connection) -> None:
    slug, schema = await _tenant(tx)
    bucket = f"orpycamcp-{slug}-documents"
    await tx.execute(
        f'INSERT INTO "{schema}".preservacion_worm_objeto (bucket, object_key, version_id) VALUES '
        "($1, 'indice/a.json', 'v-vieja'), ($1, 'indice/b.json', 'otra')",
        bucket,
    )
    mapa = f"OBJETO\t{bucket}/indice/a.json\tv-vieja\tv-nueva\nOBJETO\t{bucket}/indice/b.json\tno-coincide\tv-x\n"
    resumen = await job.registrar(tx, mapa_texto=mapa, respaldo="20261003_020000", previas_no_restauradas=2)

    filas = {r["object_key"]: r["version_id"] for r in await tx.fetch(f'SELECT * FROM "{schema}".preservacion_worm_objeto')}
    assert filas == {"indice/a.json": "v-nueva", "indice/b.json": "otra"}, "solo cambia donde el version_id viejo coincide"
    assert resumen["filas_worm_actualizadas"] == 1 and resumen["sin_fila_worm"] == 1
    asiento = await tx.fetchrow(
        "SELECT actor, payload FROM public.audit_log WHERE tenant_slug = $1 AND action = 'storage.restauracion' "
        "AND object_ref = '20261003_020000' ORDER BY id DESC LIMIT 1",
        audit.SYSTEM_TENANT,
    )
    assert asiento is not None, "la restauración deja asiento inmutable"
    import json

    payload = asiento["payload"] if isinstance(asiento["payload"], dict) else json.loads(asiento["payload"])
    assert payload["mapa_sha256"] == hashlib.sha256(mapa.encode()).hexdigest()
    assert payload["previas_compliance_no_restauradas"] == 2 and payload["por_tenant"] == {slug: 1}


async def test_si_el_asiento_falla_la_actualizacion_se_revierte(tx: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    slug, schema = await _tenant(tx)
    bucket = f"orpycamcp-{slug}-documents"
    await tx.execute(
        f'INSERT INTO "{schema}".preservacion_worm_objeto (bucket, object_key, version_id) VALUES ($1, \'k\', \'v1\')',
        bucket,
    )

    async def _roto(*_a: object, **_k: object) -> str:
        raise RuntimeError("auditoría caída")

    monkeypatch.setattr(job._audit, "append", _roto)
    with pytest.raises(RuntimeError):
        await job.registrar(tx, mapa_texto=f"OBJETO\t{bucket}/k\tv1\tv2\n", respaldo="r")
    v = await tx.fetchval(f'SELECT version_id FROM "{schema}".preservacion_worm_objeto')
    assert v == "v1", "sin asiento no hay actualización (misma transacción)"


async def test_un_bucket_ajeno_o_sin_tabla_no_rompe_ni_se_toca(tx: asyncpg.Connection) -> None:
    mapa = "OBJETO\tbucket-raro/k\tv1\tv2\nOBJETO\torpycamcp-no-existe-documents/k\tv1\tv2\n"
    resumen = await job.registrar(tx, mapa_texto=mapa, respaldo="r2")
    assert resumen["filas_worm_actualizadas"] == 0 and resumen["sin_fila_worm"] == 2


async def test_un_mapa_malformado_se_rechaza() -> None:
    with pytest.raises(ValueError):
        job.parsear_mapa("OBJETO\tsolo-dos-campos\tv1\n")
