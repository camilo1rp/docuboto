# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El consecutivo del radicado no deja huecos sin asiento (AREA-09-01).

Contra Postgres REAL: el hueco lo produce el modo de commit del UPSERT de
`tracking_sequences` (autocommit fuera de la transacción), algo que un mock no
puede ver. Un `tercero_id` inexistente aborta la radicación con 404 DENTRO de la
transacción: el radicado y su asiento se revierten, y el número tiene que
revertirse con ellos.

Mutación verificada al entregar: volver a llamar `next_sequence` fuera del bloque
`transaction()` → el radicado válido siguiente sale con +2 y el primer test cae.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.database import tenant_schema
from app.schemas.document import DocumentCreate
from app.services.document_service import DocumentService

pytestmark = pytest.mark.integration


async def _last_seq(conn: asyncpg.Connection, year: int) -> int | None:
    return await conn.fetchval(
        "SELECT last_seq FROM tracking_sequences WHERE year = $1 AND doc_type = 'E'", year
    )


@pytest.mark.asyncio
async def test_un_fallo_tras_tomar_el_numero_no_lo_consume(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    svc = DocumentService(conn, slug)
    year = await conn.fetchval("SELECT EXTRACT(YEAR FROM now() AT TIME ZONE 'America/Bogota')::int")

    ok = await svc.create(DocumentCreate(doc_type="E",doc_class="Petición",dest_dept_code=1, subject="Primer radicado"), None)
    assert ok.tracking_number.endswith("-E-000001")
    assert await _last_seq(conn, year) == 1

    with pytest.raises(HTTPException) as exc:
        await svc.create(
            DocumentCreate(
                doc_type="E",
                doc_class="Petición",
                dest_dept_code=1,
                subject="Con un tercero que no existe",
                actores=[{"tercero_id": uuid.uuid4(), "rol": "remitente", "principal": True}],
            ),
            None,
        )
    assert exc.value.status_code == 404
    assert await _last_seq(conn, year) == 1, "el 404 quemó un número sin asiento"

    siguiente = await svc.create(DocumentCreate(doc_type="E",doc_class="Petición",dest_dept_code=1, subject="Tercer intento"), None)
    assert siguiente.tracking_number.endswith("-E-000002")
    assert await _last_seq(conn, year) == 2


@pytest.mark.asyncio
async def test_radicacion_concurrente_con_fallos_no_repite_ni_deja_huecos(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Contención real del row-lock de `tracking_sequences` (riesgo declarado de T4).

    Meter `next_sequence` en la transacción alarga el lock hasta el commit: con
    conexiones SEPARADAS (el lock entre sesiones es lo que serializa, no un mock)
    10 radicaciones válidas y 5 que abortan con 404 a mitad deben dar exactamente
    1..10: sin repetidos (atomicidad) y sin huecos (los 404 devuelven su número).
    Mutación: `next_sequence` fuera de `transaction()` → los 404 dejan huecos.
    """
    import asyncio

    from tests.integration.conftest import DSN

    slug, base = tenant_ctx
    schema = tenant_schema(slug)
    await base.execute(f'SET search_path TO "{schema}"')
    year = await base.fetchval("SELECT EXTRACT(YEAR FROM now() AT TIME ZONE 'America/Bogota')::int")

    conns = [await asyncpg.connect(DSN) for _ in range(15)]
    try:
        for c in conns:
            await c.execute(f'SET search_path TO "{schema}"')

        async def _radicar(i: int, c: asyncpg.Connection):
            malo = i % 3 == 2  # 5 de 15 fallan DESPUÉS de tomar el número
            payload = DocumentCreate(
                doc_type="E",
                doc_class="Petición",
                dest_dept_code=1,
                subject=f"Concurrente {i}",
                actores=[{"tercero_id": uuid.uuid4(), "rol": "remitente", "principal": True}]
                if malo
                else [],
            )
            try:
                return (await DocumentService(c, slug).create(payload, None)).tracking_number
            except HTTPException as exc:
                assert exc.status_code == 404
                return None

        resultados = await asyncio.gather(*[_radicar(i, c) for i, c in enumerate(conns)])
    finally:
        for c in conns:
            await c.close()

    numeros = [r for r in resultados if r is not None]
    assert len(numeros) == 10
    secuencias = sorted(int(n.rsplit("-", 1)[1]) for n in numeros)
    assert secuencias == list(range(1, 11)), f"hueco o repetido: {secuencias}"
    assert await _last_seq(base, year) == 10
