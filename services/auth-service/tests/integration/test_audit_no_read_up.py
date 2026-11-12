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

Las tablas `radicados` / `expedientes` / `expediente_indice` son las REALES de la
cadena única (`services/docuboto/migrations/tenant`). Si alguna renombra una columna
del contrato de la unión (`id`, identidad natural, `nivel_seguridad`), este test cae,
que es lo que se quiere: el acoplamiento es real y debe ser ruidoso.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from orpycamcp_common import audit as audit_lib

from auth.repositories.audit_query_repository import AuditQueryRepository
from tests.integration.conftest import RUN_INTEGRATION

pytestmark = pytest.mark.skipif(
    not RUN_INTEGRATION, reason="requiere Postgres real (RUN_INTEGRATION=1)"
)


async def _radicado(conn: asyncpg.Connection, rid: uuid.UUID, numero: str, nivel: int) -> None:
    """Un radicado real con lo mínimo que exigen sus restricciones."""
    clasificado = nivel == 3
    await conn.execute(
        "INSERT INTO radicados (id, tracking_number, doc_type, year, sequence, subject, "
        "tenant_code, nivel_seguridad, clasificacion_causal, clasificacion_fundamento) "
        "VALUES ($1, $2, 'E', 2026, $3, 'itest', 'ITEST', $4, $5, $6)",
        rid,
        numero,
        int(numero[-6:]),
        nivel,
        "art18_a_intimidad" if clasificado else None,
        "itest" if clasificado else None,
    )


@pytest.mark.asyncio
async def test_listado_recorta_por_clearance_y_es_fail_closed(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    rad_publico = uuid.uuid4()
    rad_clasificado = uuid.uuid4()
    exp_reservado = uuid.uuid4()
    await _radicado(conn, rad_publico, "2026-ITEST-E-000001", 1)
    await _radicado(conn, rad_clasificado, "2026-ITEST-E-000002", 3)
    await conn.execute(
        "INSERT INTO expedientes (id, code, name, nivel_seguridad) "
        "VALUES ($1, 'EXP-ITEST-002', 'itest', 2)",
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
            canal="api",
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
    """Schema de tenant al que le faltan `radicados` y `expedientes`.

    La cadena única siempre las crea; aquí se retiran a propósito para ejercer el
    guard (un schema a medio aprovisionar o dañado).

    Referenciar `radicados` reventaría con un 500; el guard convierte ese caso en
    fail-closed. El fallo silencioso que se evita es el contrario: dar por pública
    una entrada cuyo nivel no hay dónde consultar.
    """
    slug, conn = tenant_ctx
    await conn.execute("DROP TABLE radicados, expedientes CASCADE")
    assert await conn.fetchval("SELECT to_regclass('radicados')") is None

    await audit_lib.append(
        conn,
        tenant_slug=slug,
        service="itest",
        action="radicado.creado",
        object_type="radicado",
        object_ref="2026-ITEST-E-000001",
        actor="itest-actor",
        canal="api",
    )
    await audit_lib.append(
        conn,
        tenant_slug=slug,
        service="itest",
        action="usuario.creado",
        object_type="usuario",
        object_ref="itest-user",
        actor="itest-actor",
        canal="api",
    )

    repo = AuditQueryRepository(conn)
    entries = await repo.list(slug, {}, max_level=1, limit=50, offset=0)
    assert [e.object_ref for e in entries] == ["itest-user"]

    # Con clearance máximo no hay recorte: la auditoría se ve entera aunque las
    # tablas fuente no existan (no se penaliza a quien sí está habilitado).
    todas = await AuditQueryRepository(conn).list(slug, {}, max_level=3, limit=50, offset=0)
    assert len(todas) == 2


@pytest.mark.asyncio
async def test_filtro_por_object_ref_de_un_expediente_une_codigo_uuid_e_indice(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """AREA-03-07: un expediente se nombra en `audit_log` de tres formas (código los
    asientos nuevos, UUID del expediente y UUID del índice los viejos). Filtrar por el
    código con `=` exacto perdía los otros dos y el historial salía incompleto.

    Mutación: volver el filtro a `al.object_ref = $n` deja solo 1 de los 3.
    """
    slug, conn = tenant_ctx
    exp, otro, indice = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await conn.execute(
        "INSERT INTO expedientes (id, code, name, nivel_seguridad) VALUES "
        "($1, 'EXP-ITEST-010', 'itest', 1), ($2, 'EXP-ITEST-011', 'itest', 1)",
        exp,
        otro,
    )
    await conn.execute(
        "INSERT INTO expediente_indice (id, expediente_id, version, xml, xml_sha256, "
        "content_hash, num_documentos) VALUES ($1, $2, 1, '<indice/>', $3, $3, 0)",
        indice,
        exp,
        "0" * 64,
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
            canal="api",
        )

    await _append("expediente", "EXP-ITEST-010", "expediente.creado")
    await _append("expediente", str(exp), "expediente.cerrado")
    await _append("expediente_indice", str(indice), "archive.indice_firmado")
    # Ruido que NO debe entrar: otro expediente, por código y por UUID.
    await _append("expediente", "EXP-ITEST-011", "expediente.creado")
    await _append("expediente", str(otro), "expediente.cerrado")

    repo = AuditQueryRepository(conn)
    filtro = {"object_ref": "EXP-ITEST-010"}
    entradas = await repo.list(slug, filtro, max_level=3, limit=50, offset=0)
    assert {e.object_ref for e in entradas} == {"EXP-ITEST-010", str(exp), str(indice)}
    assert await repo.count(slug, filtro, max_level=3) == 3

    # El recorte no-read-up sigue mandando: ensanchar la referencia no ensancha el acceso.
    await conn.execute("UPDATE expedientes SET nivel_seguridad = 3 WHERE id = $1", exp)
    oculto = await repo.list(slug, filtro, max_level=1, limit=50, offset=0)
    assert oculto == []
