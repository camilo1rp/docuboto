# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Integración (Postgres REAL) del no-read-up en disposición y PATCH (RF-SEG-08).

Por qué este tier y no solo unit: el gate es una cláusula `WHERE
nivel_seguridad <= $N` dentro del propio UPDATE. Con `AsyncMock` el SQL nunca
se ejecuta, así que un test unit solo puede afirmar que la cadena de texto
contiene el filtro — afirma mi propia redacción, no que el motor excluya la
fila. Aquí se verifica POR MUTACIÓN: tras un intento sobre-clearance se relee
la fila y se comprueba que NO cambió.

Cubre `get_disposition` / `set_disposition` / `update`, incluidas las dos ramas
de `update` (con campos → UPDATE gated; sin campos → `get_by_id` gated).
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.document_repository import DocumentRepository
from app.schemas.document import DocumentUpdate

pytestmark = pytest.mark.integration

PUBLICA = 1
RESERVADA = 2
CLASIFICADA = 3


async def _seed_radicado(
    conn: asyncpg.Connection, *, nivel: int, subject: str = "ASUNTO RESERVADO"
) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, "
        "tenant_code, nivel_seguridad, clasificacion_fundamento, clasificacion_causal) VALUES "
        "($1, 'E', 2026, 1, $2, 'DEMO', $3, "
        "CASE WHEN $3::smallint >= 2 THEN 'Ley 1712/2014 art. 19 (prueba)' END, "
        "CASE WHEN $3::smallint >= 2 THEN 'por_clasificar' END) "
        "RETURNING id",
        f"2026-DEMO-E-{uuid.uuid4().hex[:6]}",
        subject,
        nivel,
    )


async def _repo_en_tenant(tenant_ctx) -> tuple[DocumentRepository, asyncpg.Connection]:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}"')
    return DocumentRepository(conn, slug), conn


@pytest.mark.asyncio
async def test_get_disposition_excluye_el_radicado_sobre_clearance(tenant_ctx) -> None:
    """Un radicado CLASIFICADO es (False, {}) para un llamante PUBLICA.

    Idéntico a lo que devuelve un `document_id` inexistente: ahí está la
    propiedad de no-oráculo, verificada contra el motor.
    """
    repo, conn = await _repo_en_tenant(tenant_ctx)
    rid = await _seed_radicado(conn, nivel=CLASIFICADA)
    await conn.execute(
        """UPDATE radicados SET disposition = '{"accion": "conservar"}'::jsonb WHERE id = $1""",
        rid,
    )

    assert await repo.get_disposition(rid, PUBLICA) == (False, {})
    assert await repo.get_disposition(rid, RESERVADA) == (False, {})
    assert await repo.get_disposition(uuid.uuid4(), CLASIFICADA) == (False, {})

    existe, disp = await repo.get_disposition(rid, CLASIFICADA)
    assert existe is True
    assert disp == {"accion": "conservar"}


@pytest.mark.asyncio
async def test_set_disposition_sobre_clearance_no_muta_la_fila(tenant_ctx) -> None:
    """Verificación POR MUTACIÓN: el intento devuelve None y la BD no cambia.

    Es el caso que más pesa: la disposición decide conservación total frente a
    eliminación (Ley 594/2000 art. 24). Un actor por debajo del nivel del
    documento no puede marcarlo para eliminar.
    """
    repo, conn = await _repo_en_tenant(tenant_ctx)
    rid = await _seed_radicado(conn, nivel=CLASIFICADA)
    await conn.execute(
        """UPDATE radicados SET disposition = '{"accion": "conservar"}'::jsonb WHERE id = $1""",
        rid,
    )

    resultado = await repo.set_disposition(
        rid, {"accion": "eliminar", "marcado_eliminacion": True}, PUBLICA
    )
    assert resultado is None

    persistido = await conn.fetchval("SELECT disposition FROM radicados WHERE id = $1", rid)
    assert '"conservar"' in persistido, (
        "la disposición se mutó pese al no-read-up: el gate no llegó al WHERE del UPDATE"
    )
    assert "eliminar" not in persistido

    # Con el nivel adecuado sí escribe (el gate no rompe el camino legítimo).
    ok = await repo.set_disposition(rid, {"accion": "eliminar"}, CLASIFICADA)
    assert ok == {"accion": "eliminar"}


@pytest.mark.asyncio
async def test_update_sobre_clearance_no_muta_ni_revela(tenant_ctx) -> None:
    """PATCH sobre un radicado clasificado: None, y el asunto original intacto.

    Sin gate, el PATCH era también una LECTURA total: devuelve el radicado
    completo (asunto, remitente, anexos) en el `RETURNING`.
    """
    repo, conn = await _repo_en_tenant(tenant_ctx)
    rid = await _seed_radicado(conn, nivel=CLASIFICADA, subject="ASUNTO QUE NO DEBE FILTRARSE")

    devuelto = await repo.update(rid, DocumentUpdate(subject="sobrescrito"), PUBLICA)
    assert devuelto is None, "no debe devolverse el radicado: sería la fuga del asunto"

    subject = await conn.fetchval("SELECT subject FROM radicados WHERE id = $1", rid)
    assert subject == "ASUNTO QUE NO DEBE FILTRARSE"

    con_nivel = await repo.update(rid, DocumentUpdate(subject="sobrescrito"), CLASIFICADA)
    assert con_nivel is not None
    assert con_nivel.subject == "sobrescrito"


@pytest.mark.asyncio
async def test_update_sin_campos_tambien_respeta_el_clearance(tenant_ctx) -> None:
    """Rama fácil de olvidar: un payload vacío no pasa por el UPDATE.

    `update` corta antes y delega en `get_by_id`; si esa delegación no arrastra
    el clearance, un PATCH `{}` se convierte en una lectura sin gate del
    radicado completo — exactamente la fuga que se está cerrando.
    """
    repo, conn = await _repo_en_tenant(tenant_ctx)
    rid = await _seed_radicado(conn, nivel=CLASIFICADA)

    assert await repo.update(rid, DocumentUpdate(), PUBLICA) is None
    assert await repo.update(rid, DocumentUpdate(), CLASIFICADA) is not None
