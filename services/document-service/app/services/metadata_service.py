# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Validación de metadatos contra plantilla y CRUD de plantillas (E03, ADR-007).

La validación vive en la capa de servicio (no hay ORM; ADR-003): los valores de
`metadata` se contrastan con el JSON Schema de la plantilla activa del tipo
documental. Un acto que no valida se rechaza (422).
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg
import jsonschema

from app.repositories.metadata_repository import MetadataRepository
from app.schemas.metadata import MetadataTemplateCreate, MetadataTemplateResponse


class MetadataValidationError(Exception):
    """Los valores de metadato no cumplen el JSON Schema de la plantilla."""


def _as_schema(raw: Any) -> dict[str, Any]:
    # asyncpg devuelve JSONB como str; los payloads de la API ya son dict.
    return raw if isinstance(raw, dict) else json.loads(raw)


def validate_metadata(metadata: dict[str, Any], json_schema: Any) -> None:
    schema = _as_schema(json_schema)
    try:
        jsonschema.validate(instance=metadata, schema=schema)
    except jsonschema.ValidationError as exc:
        raise MetadataValidationError(exc.message) from exc
    except jsonschema.SchemaError as exc:  # plantilla mal formada
        raise MetadataValidationError(f"Plantilla inválida: {exc.message}") from exc


def _to_response(row: asyncpg.Record) -> MetadataTemplateResponse:
    return MetadataTemplateResponse(
        id=row["id"],
        tipo_documental=row["tipo_documental"],
        version=row["version"],
        json_schema=_as_schema(row["json_schema"]),
        activo=row["activo"],
        created_at=row["created_at"],
    )


class MetadataTemplateService:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._repo = MetadataRepository(conn)

    async def create(self, payload: MetadataTemplateCreate) -> MetadataTemplateResponse:
        # Una plantilla mal formada (no es un JSON Schema válido) se rechaza ya.
        try:
            jsonschema.Draft202012Validator.check_schema(payload.json_schema)
        except jsonschema.SchemaError as exc:
            raise MetadataValidationError(f"json_schema inválido: {exc.message}") from exc

        row = await self._repo.insert(
            tipo_documental=payload.tipo_documental,
            version=payload.version,
            json_schema=payload.json_schema,
            activo=payload.activo,
        )
        return _to_response(row)

    async def list(self, *, tipo_documental: str | None) -> list[MetadataTemplateResponse]:
        rows = await self._repo.list(tipo_documental=tipo_documental)
        return [_to_response(r) for r in rows]
