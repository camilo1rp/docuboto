# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""No-read-up del listado de auditoría (E08, RF-SEG-08) contra Postgres REAL.

Por qué este carril y no el unit: el recorte vive **en el SQL** (dos subconsultas
correlacionadas contra tablas de OTROS servicios, en el mismo schema de tenant).
Un mock de `asyncpg` diría que la consulta "se llamó", no que filtre — y el modo
de fallo que importa aquí es exactamente ese: una entrada clasificada que se cuela.
Se verifica por MUTACIÓN del clearance: la misma consulta, con el nivel subido,
tiene que devolver las filas que antes ocultaba.

Las tablas `radicados` / `expedientes` se crean aquí en su forma MÍNIMA — solo las
columnas del contrato de la unión (`id`, identidad natural, `nivel_seguridad`). Si
document-service o archive-service renombran alguna, este test cae, que es lo que
se quiere: el acoplamiento es real y debe ser ruidoso.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from orpycamcp_common import audit as audit_lib

from app.repositories.audit_query_repository import AuditQueryRepository
from migrations import runner
from tests.integration.conftest import RUN_INTEGRATION

pytestmark = pytest.mark.skipif(
    not RUN_INTEGRATION, reason="requiere Postgres real (RUN_INTEGRATION=1)"
)

_MINIMAL_SOURCES = """
CREATE TABLE radicados (
    id              uuid PRIMARY KEY,
    tracking_number text NOT NULL UNIQUE,
    nivel_seguridad smallint NOT NULL DEFAULT 1
);
CREATE TABLE expedientes (
    id              uuid PRIMARY KEY,
    code            text NOT NULL UNIQUE,
    nivel_seguridad smallint NOT NULL DEFAULT 1
);
"""


async def _ensure_audit_log(conn: asyncpg.Connection) -> None:
    """`public.audit_log` es global y la posee auth-service: aplicar sus migraciones
    globales (idempotentes) es suficiente y no toca el schema del tenant."""
    for path in runner._sql_files(runner.GLOBAL_DIR):
        await conn.execute(path.read_text())


@pytest.mark.asyncio
async def test_listado_recorta_por_clearance_y_es_fail_closed(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await _ensure_audit_log(conn)
    await conn.execute(_MINIMAL_SOURCES)

    rad_publico = uuid.uuid4()
    rad_clasificado = uuid.uuid4()
    exp_reservado = uuid.uuid4()
    await conn.execute(
        "INSERT INTO radicados (id, tracking_number, nivel_seguridad) VALUES "
        "($1, '2026-ITEST-E-000001', 1), ($2, '2026-ITEST-E-000002', 3)",
        rad_publico,
        rad_clasificado,
    )
    await conn.execute(
        "INSERT INTO expedientes (id, code, nivel_seguridad) VALUES ($1, 'EXP-ITEST-002', 2)",
        exp_reservado,
    )

    async def _append(object_type: str, object_ref: str, action: str) -> None:
        await audit_lib.append(
            conn,
            tenant_slug=slug,
            service="itest",
            action=action,
            object_type=object_type,
            object_ref=object_ref,
            actor="itest-actor",
        )

    await _append("radicado", "2026-ITEST-E-000001", "radicado.creado")
    await _append("radicado", "2026-ITEST-E-000002", "radicado.creado")
    # workflow-service escribe el UUID, no el número: la unión también debe
    # resolverlo, o el radicado clasificado se filtraría por esta puerta.
    await _append("radicado", str(rad_clasificado), "radicado.visto_bueno")
    await _append("expediente", "EXP-ITEST-002", "expediente.creado")
    # Referencia que no resuelve contra ninguna fila: no se puede declarar
    # pública lo que no se puede clasificar → fuera.
    await _append("radicado", "2026-ITEST-E-999999", "radicado.creado")
    # Tipo de la lista de admisión (identidad/configuración, jamás un trámite):
    # pasa intacto en cualquier nivel.
    await _append("usuario", "itest-user", "usuario.creado")
    # Tipos que NO están en la lista de admisión y cuyo `payload` sí lleva la
    # identidad de un radicado (dead-letter de eventos, import masivo, anexo).
    # Con lista de DENEGACIÓN se habrían colado; con lista de ADMISIÓN, no.
    await _append("evento", "evt-itest-1", "notification.dead_letter")
    await _append("import", "imp-itest-1", "radicado.import")
    await _append("file", "file-itest-1", "storage.file_uploaded")

    repo = AuditQueryRepository(conn)

    def _refs(entries: list) -> set[str]:
        return {e.object_ref for e in entries}

    # Nivel 1 (PUBLICA): solo el radicado público y lo de la lista de admisión.
    nivel1 = await repo.list(slug, {}, max_level=1, limit=50, offset=0)
    assert _refs(nivel1) == {"2026-ITEST-E-000001", "itest-user"}
    assert await repo.count(slug, {}, max_level=1) == 2, (
        "X-Total-Count tiene que reflejar el mismo recorte: si no, el total es un "
        "oráculo de cuántas entradas clasificadas hay"
    )

    assert _refs(nivel1).isdisjoint({"evt-itest-1", "imp-itest-1", "file-itest-1"}), (
        "los tipos fuera de la lista de admisión llevan identidad de radicado en el "
        "payload (sobre del dead-letter, lista de números importados, nombre del "
        "anexo): una lista de DENEGACIÓN los dejaría pasar"
    )

    # MUTACIÓN 1 — nivel 2 (RESERVADA): aparece el expediente, siguen ocultos los
    # dos asientos del radicado clasificado (por número y por UUID).
    nivel2 = await repo.list(slug, {}, max_level=2, limit=50, offset=0)
    assert _refs(nivel2) == {"2026-ITEST-E-000001", "itest-user", "EXP-ITEST-002"}

    # MUTACIÓN 2 — nivel 3 (CLASIFICADA): auditoría íntegra. Reaparece TODO lo que
    # el recorte ocultaba, incluida la referencia irresoluble y los tres tipos
    # fuera de la lista de admisión: a este nivel no se aplica cláusula alguna.
    nivel3 = await repo.list(slug, {}, max_level=3, limit=50, offset=0)
    assert _refs(nivel3) == {
        "2026-ITEST-E-000001",
        "2026-ITEST-E-000002",
        str(rad_clasificado),
        "EXP-ITEST-002",
        "2026-ITEST-E-999999",
        "itest-user",
        "evt-itest-1",
        "imp-itest-1",
        "file-itest-1",
    }
    assert await repo.count(slug, {}, max_level=3) == 9

    # Filtrar por el radicado clasificado desde nivel 1 devuelve vacío —
    # indistinguible de que no exista (el filtro no puede ser un oráculo).
    sondeo = await repo.list(
        slug, {"object_ref": "2026-ITEST-E-000002"}, max_level=1, limit=50, offset=0
    )
    assert sondeo == []


@pytest.mark.asyncio
async def test_sin_tablas_fuente_las_entradas_clasificables_no_se_listan(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Tenant aprovisionado sin las migraciones de document/archive-service.

    Referenciar `radicados` reventaría con un 500; el guard convierte ese caso en
    fail-closed. El fallo silencioso que se evita es el contrario: dar por pública
    una entrada cuyo nivel no hay dónde consultar.
    """
    slug, conn = tenant_ctx
    await _ensure_audit_log(conn)

    await audit_lib.append(
        conn,
        tenant_slug=slug,
        service="itest",
        action="radicado.creado",
        object_type="radicado",
        object_ref="2026-ITEST-E-000001",
        actor="itest-actor",
    )
    await audit_lib.append(
        conn,
        tenant_slug=slug,
        service="itest",
        action="usuario.creado",
        object_type="usuario",
        object_ref="itest-user",
        actor="itest-actor",
    )

    repo = AuditQueryRepository(conn)
    entries = await repo.list(slug, {}, max_level=1, limit=50, offset=0)
    assert [e.object_ref for e in entries] == ["itest-user"]

    # Con clearance máximo no hay recorte: la auditoría se ve entera aunque las
    # tablas fuente no existan (no se penaliza a quien sí está habilitado).
    todas = await AuditQueryRepository(conn).list(slug, {}, max_level=3, limit=50, offset=0)
    assert len(todas) == 2
