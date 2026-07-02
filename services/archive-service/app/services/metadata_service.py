# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Validación de metadatos del expediente y CRUD de plantillas (E03, ADR-007).

Mismo criterio que en document-service: los valores de `metadata` se validan
contra el JSON Schema de la plantilla activa de la serie TRD del expediente.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg
import jsonschema
from orpycamcp_common import audit

from app.repositories.metadata_repository import MetadataRepository
from app.schemas.metadata import ExpedienteTemplateCreate, ExpedienteTemplateResponse

_SERVICE = "archive-service"


class MetadataValidationError(Exception):
    """Los valores de metadato no cumplen el JSON Schema de la plantilla."""


def _as_schema(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else json.loads(raw)


def validate_metadata(metadata: dict[str, Any], json_schema: Any) -> None:
    schema = _as_schema(json_schema)
    try:
        jsonschema.validate(instance=metadata, schema=schema)
    except jsonschema.ValidationError as exc:
        raise MetadataValidationError(exc.message) from exc
    except jsonschema.SchemaError as exc:
        raise MetadataValidationError(f"Plantilla inválida: {exc.message}") from exc


def _to_response(row: asyncpg.Record) -> ExpedienteTemplateResponse:
    return ExpedienteTemplateResponse(
        id=row["id"],
        trd_serie_id=row["trd_serie_id"],
        version=row["version"],
        json_schema=_as_schema(row["json_schema"]),
        activo=row["activo"],
        created_at=row["created_at"],
    )


class ExpedienteTemplateService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._repo = MetadataRepository(conn, tenant_slug)

    async def create(
        self, payload: ExpedienteTemplateCreate, actor: uuid.UUID | None = None
    ) -> ExpedienteTemplateResponse:
        try:
            jsonschema.Draft202012Validator.check_schema(payload.json_schema)
        except jsonschema.SchemaError as exc:
            raise MetadataValidationError(f"json_schema inválido: {exc.message}") from exc

        row = await self._repo.insert(
            trd_serie_id=payload.trd_serie_id,
            version=payload.version,
            json_schema=payload.json_schema,
            activo=payload.activo,
        )
        # Auditoría forense transversal (E08 §9, Canal A): la plantilla rige la
        # validación de metadatos del expediente (E03, ADR-007) — su alta debe
        # quedar trazada igual que la de una serie TRD.
        await audit.append(
            self._conn,
            tenant_slug=self._tenant_slug,
            service=_SERVICE,
            action="archive.metadata_template_created",
            object_type="metadata_template",
            object_ref=f"{row['trd_serie_id']}:v{row['version']}",
            actor=str(actor) if actor else None,
            payload={"id": str(row["id"])},
        )
        return _to_response(row)

    async def list(self, *, trd_serie_id: uuid.UUID | None) -> list[ExpedienteTemplateResponse]:
        rows = await self._repo.list(trd_serie_id=trd_serie_id)
        return [_to_response(r) for r in rows]
