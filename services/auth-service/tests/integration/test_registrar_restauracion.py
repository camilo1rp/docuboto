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


async def _tenant(conn: asyncpg.Connection, *, con_eventos: bool = True) -> tuple[str, str]:
    slug = f"itest-rst-{uuid.uuid4().hex[:6]}"
    schema = f"tenant_{slug.replace('-', '_')}"
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await conn.execute(
        f'CREATE TABLE "{schema}".preservacion_worm_objeto ('
        "id UUID PRIMARY KEY DEFAULT gen_random_uuid(), bucket TEXT NOT NULL, "
        "object_key TEXT NOT NULL, version_id TEXT, expediente_id TEXT NOT NULL DEFAULT 'exp-1', "
        "tipo TEXT NOT NULL DEFAULT 'indice', sha256 CHAR(64) NOT NULL DEFAULT repeat('a', 64))"
    )
    if con_eventos:
        await conn.execute(
            f'CREATE TABLE "{schema}".preservacion_evento ('
            "id UUID PRIMARY KEY DEFAULT gen_random_uuid(), object_ref TEXT NOT NULL, "
            "tipo VARCHAR(24) NOT NULL, resultado VARCHAR(16) NOT NULL DEFAULT 'ok', "
            "hash_before CHAR(64), hash_after CHAR(64), detalle JSONB, agente TEXT, "
            "ts TIMESTAMPTZ NOT NULL DEFAULT now())"
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


async def test_cada_objeto_remapeado_deja_un_evento_premis_de_replica(tx: asyncpg.Connection) -> None:
    import json

    slug, schema = await _tenant(tx)
    bucket = f"orpycamcp-{slug}-documents"
    await tx.execute(
        f'INSERT INTO "{schema}".preservacion_worm_objeto (bucket, object_key, version_id, expediente_id, tipo) VALUES '
        "($1, 'indice/a.json', 'v-vieja', 'exp-A', 'indice'), ($1, 'indice/b.json', 'otra', 'exp-B', 'aip')",
        bucket,
    )
    mapa = f"OBJETO\t{bucket}/indice/a.json\tv-vieja\tv-nueva\nOBJETO\t{bucket}/indice/b.json\tno-coincide\tv-x\n"
    resumen = await job.registrar(tx, mapa_texto=mapa, respaldo="R1")
    assert resumen["eventos_premis"] == 1, "un evento por fila remapeada, ninguno por la que no coincide"
    eventos = await tx.fetch(f'SELECT * FROM "{schema}".preservacion_evento')
    assert len(eventos) == 1
    e = eventos[0]
    assert (e["object_ref"], e["tipo"], e["resultado"], e["agente"]) == ("exp-A", "REPLICA", "ok", "system:ops-restauracion")
    # No se mide el objeto restaurado: nunca se sobre-declara `hash_after`.
    assert e["hash_before"] == "a" * 64 and e["hash_after"] is None
    d = e["detalle"] if isinstance(e["detalle"], dict) else json.loads(e["detalle"])
    assert d["fixity"] == "no_verificada"
    assert d["motivo"] == "restauracion" and d["evento"] == "replication" and d["respaldo"] == "R1"
    assert (d["version_vieja"], d["version_nueva"], d["artefacto"]) == ("v-vieja", "v-nueva", "indice")
    assert (d["bucket"], d["object_key"]) == (bucket, "indice/a.json")


async def test_sin_la_tabla_de_eventos_todo_se_revierte(tx: asyncpg.Connection) -> None:
    slug, schema = await _tenant(tx, con_eventos=False)
    bucket = f"orpycamcp-{slug}-documents"
    await tx.execute(
        f'INSERT INTO "{schema}".preservacion_worm_objeto (bucket, object_key, version_id) VALUES ($1, \'k\', \'v1\')',
        bucket,
    )
    sp = tx.transaction()  # savepoint: el fallo no aborta la transacción de la prueba
    await sp.start()
    with pytest.raises(asyncpg.PostgresError):
        await job.registrar(tx, mapa_texto=f"OBJETO\t{bucket}/k\tv1\tv2\n", respaldo="r")
    await sp.rollback()
    assert await tx.fetchval(f'SELECT version_id FROM "{schema}".preservacion_worm_objeto') == "v1"
