# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Radicar un borrador con tipo de plantilla de metadatos (B6, ítem 23).

Postgres REAL. `DocumentService.create` valida los metadatos antes de `next_sequence`:
un incumplimiento es 422 `metadata_validation_failed` con `field` y NO consume número.
`from_borrador` lo pone el sistema DESPUÉS de validar (una plantilla con
`additionalProperties: false` no puede rechazarlo).

Mutaciones verificadas al entregar: (1) validar `from_borrador` con el resto (volver a
meterlo en el payload) → el test de `additionalProperties:false` cae; (2) mover la
validación tras `next_sequence` → el test de número no consumido cae.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.database import tenant_schema
from app.schemas.borrador import BorradorCreate, BorradorRadicar
from app.services.borrador_service import BorradorService
from app.services.document_service import DocumentService
from app.services.metadata_service import MetadataValidationError

pytestmark = pytest.mark.integration

AUTOR = uuid.uuid4()
_SCHEMA = (
    '{"type":"object","required":["expediente_ref"],"additionalProperties":false,'
    '"properties":{"expediente_ref":{"type":"string"}}}'
)


class _SinCircuito:
    @staticmethod
    async def get_estado_circuito(borrador_id: str, tenant_slug: str) -> dict:
        return {"pendiente": False, "completo": False}


async def _preparar(tenant_flujo):
    slug, conn = tenant_flujo
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    await conn.execute(
        "INSERT INTO metadata_templates (tipo_documental, json_schema) VALUES ('TPLM', $1::jsonb)",
        _SCHEMA,
    )
    servicio = BorradorService(conn, slug, signature_client=_SinCircuito)
    b = await servicio.crear(
        BorradorCreate(doc_type="S", subject="Respuesta"), AUTOR, max_clearance=3
    )
    await conn.execute("UPDATE borrador SET estado = 'aprobado' WHERE id = $1", b.id)
    # B6: el origen de una Salida debe ser una dependencia del actor (URD activa).
    await conn.execute(
        "INSERT INTO urd_usuaroldep (user_id, depe_id) SELECT $1, 1 "
        "WHERE NOT EXISTS (SELECT 1 FROM urd_usuaroldep WHERE user_id = $1 AND depe_id = 1)",
        AUTOR,
    )
    return slug, conn, servicio, b


async def _ultimo_consecutivo(conn) -> int | None:
    return await conn.fetchval(
        "SELECT COALESCE(sum(last_seq), 0) FROM tracking_sequences WHERE doc_type = 'S'"
    )


def _datos(metadata):
    return BorradorRadicar(tipo_documental="TPLM", origin_dept_code=1, metadata=metadata)


@pytest.mark.asyncio
async def test_metadata_incumplida_es_422_con_field_y_no_consume_numero(tenant_flujo) -> None:
    slug, conn, servicio, b = await _preparar(tenant_flujo)
    antes = await _ultimo_consecutivo(conn)
    with pytest.raises(MetadataValidationError) as exc:
        await servicio.radicar(
            b.id, DocumentService(conn, slug), AUTOR, max_clearance=3, datos=_datos(None)
        )
    assert exc.value.field == "expediente_ref"
    assert await _ultimo_consecutivo(conn) == antes, "el 422 gastó un número de radicado"
    assert await conn.fetchval("SELECT count(*) FROM radicados") == 0
    assert await conn.fetchval("SELECT estado FROM borrador WHERE id = $1", b.id) == "aprobado"


@pytest.mark.asyncio
async def test_metadata_valida_radica_y_conserva_la_procedencia(tenant_flujo) -> None:
    slug, conn, servicio, b = await _preparar(tenant_flujo)
    r = await servicio.radicar(
        b.id,
        DocumentService(conn, slug),
        AUTOR,
        max_clearance=3,
        datos=_datos({"expediente_ref": "EXP-1"}),
    )
    meta = await conn.fetchval("SELECT metadata FROM radicados WHERE id = $1", r.id)
    import json

    meta = json.loads(meta) if isinstance(meta, str) else meta
    assert meta == {"expediente_ref": "EXP-1", "from_borrador": str(b.id)}
    assert await conn.fetchval("SELECT estado FROM borrador WHERE id = $1", b.id) == "radicado"
