# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Validación de metadatos contra plantilla y CRUD de plantillas/elementos (E03,
ADR-007/RF-MET-03).

La validación vive en la capa de servicio (no hay ORM; ADR-003): los valores de
`metadata` se contrastan con el JSON Schema de la plantilla activa del tipo
documental. Un acto que no valida se rechaza (422).

Las escrituras del catálogo (crear/actualizar plantilla, crear/actualizar/borrar
elemento) dejan un asiento en `public.audit_log` DENTRO de la misma transacción
que la mutación (patrón de `AnulacionService`/`RBACService`): una plantilla que
relaja o anula la validación obligatoria de un tipo documental para toda la
entidad es un acto tan consecuente como una anulación de radicado, así que la
auditoría es fail-closed (si `audit.append` falla, la mutación revierte) — a
diferencia de las lecturas del servicio, que auditan best-effort.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg
import jsonschema
from fastapi import HTTPException, status

from app.repositories.metadata_repository import MetadataElementRepository, MetadataRepository
from app.schemas.metadata import MetadataTemplateCreate, MetadataTemplateResponse

logger = logging.getLogger(__name__)

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "document-service"


class MetadataValidationError(Exception):
    """Los valores de metadato no cumplen el JSON Schema de la plantilla.

    `field` es la ruta con puntos de la propiedad que falla (`a.b`), cuando el
    fallo es atribuible a una; el sobre del 422 la expone para que la pantalla
    marque el control en vez de adivinarlo del texto del mensaje.
    """

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


def _require_audit_available() -> None:
    """Fail-closed: ver docstring del módulo — misma política que
    `RBACService._require_audit_available` (auth-service) y
    `AnulacionService` en este servicio.
    """
    if not _AUDIT_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "auditoria_no_disponible",
                "detail": "Auditoría inmutable no disponible — no se puede "
                "escribir el catálogo de metadatos sin traza",
                "status": 500,
            },
        )


def _audit_tenant(tenant_slug: str | None) -> str:
    """Tenant al que se imputa el asiento — fail-closed.

    `audit.append` hace ``tenant_slug or SYSTEM_TENANT``: un llamante que lo
    omitiera archivaría la escritura bajo el tenant del sistema — la fila
    existe, pero queda fuera de la vista de auditoría de la entidad y de su
    cadena de hash. Preferimos que la escritura falle a que su traza se
    archive en el sitio equivocado (mismo patrón que
    ``AnulacionService._audit_tenant``).
    """
    if not tenant_slug:
        raise RuntimeError(
            "El catálogo de metadatos requiere tenant_slug para auditar una "
            "escritura (imputar el asiento al tenant del sistema lo "
            "ocultaría de la auditoría de la entidad)"
        )
    return tenant_slug


def _as_schema(raw: Any) -> dict[str, Any]:
    # asyncpg devuelve JSONB como str; los payloads de la API ya son dict.
    return raw if isinstance(raw, dict) else json.loads(raw)


def _campo_del_error(exc: jsonschema.ValidationError) -> str | None:
    """Ruta con puntos de la propiedad que incumple el schema (`a.b`, `lista.0`).

    `absolute_path` apunta al PADRE cuando falta una propiedad `required`, así
    que en ese caso se le añade el nombre de la propiedad ausente: es lo que la
    pantalla necesita para marcar el control, y el único dato que el mensaje en
    prosa trae mezclado con el texto. `None` si el fallo es de la raíz.
    """
    ruta = [str(p) for p in exc.absolute_path]
    if exc.validator == "required" and isinstance(exc.instance, dict):
        faltan = [p for p in exc.validator_value if p not in exc.instance]
        if faltan:
            ruta.append(str(faltan[0]))
    return ".".join(ruta) or None


def validate_metadata(metadata: dict[str, Any], json_schema: Any) -> None:
    schema = _as_schema(json_schema)
    try:
        jsonschema.validate(instance=metadata, schema=schema)
    except jsonschema.ValidationError as exc:
        raise MetadataValidationError(exc.message, field=_campo_del_error(exc)) from exc
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
        self._conn = conn
        self._repo = MetadataRepository(conn)

    async def create(
        self,
        payload: MetadataTemplateCreate,
        *,
        tenant_slug: str | None,
        actor: str | None,
    ) -> MetadataTemplateResponse:
        # Una plantilla mal formada (no es un JSON Schema válido) se rechaza ya.
        try:
            jsonschema.Draft202012Validator.check_schema(payload.json_schema)
        except jsonschema.SchemaError as exc:
            raise MetadataValidationError(f"json_schema inválido: {exc.message}") from exc

        _require_audit_available()
        tenant = _audit_tenant(tenant_slug)
        async with self._conn.transaction():
            row = await self._repo.insert(
                tipo_documental=payload.tipo_documental,
                version=payload.version,
                json_schema=payload.json_schema,
                activo=payload.activo,
            )
            await _audit.append(
                self._conn,
                tenant_slug=tenant,
                service=_SERVICE,
                action="document.metadata_template_creada",
                object_type="metadata_template",
                object_ref=str(row["id"]),
                actor=actor,
                payload={
                    "tipo_documental": row["tipo_documental"],
                    "version": row["version"],
                    "activo": row["activo"],
                },
                canal=_audit.CANAL_PETICION,
            )
        return _to_response(row)

    async def list(self, *, tipo_documental: str | None) -> list[MetadataTemplateResponse]:
        rows = await self._repo.list(tipo_documental=tipo_documental)
        return [_to_response(r) for r in rows]


class MetadataElementService:
    """CRUD auditado del catálogo de elementos de metadato (RF-MET-03).

    La conversión a schema de respuesta (decodificar JSONB `valor_default`/
    `opciones`) queda en el router, igual que antes — aquí solo vive la
    mutación + su asiento de auditoría, atómicos.
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn
        self._repo = MetadataElementRepository(conn)

    async def create(
        self,
        data: dict[str, Any],
        *,
        tenant_slug: str | None,
        actor: str | None,
    ) -> asyncpg.Record:
        _require_audit_available()
        tenant = _audit_tenant(tenant_slug)
        async with self._conn.transaction():
            row = await self._repo.insert(data)
            await _audit.append(
                self._conn,
                tenant_slug=tenant,
                service=_SERVICE,
                action="document.metadata_element_creado",
                object_type="metadata_element",
                object_ref=str(row["id"]),
                actor=actor,
                payload={"clave": row["clave"], "tipo_dato": row["tipo_dato"]},
                canal=_audit.CANAL_PETICION,
            )
        return row

    async def update(
        self,
        element_id: Any,
        data: dict[str, Any],
        *,
        tenant_slug: str | None,
        actor: str | None,
    ) -> asyncpg.Record | None:
        _require_audit_available()
        tenant = _audit_tenant(tenant_slug)
        async with self._conn.transaction():
            row = await self._repo.update(element_id, data)
            if row is None:
                return None
            await _audit.append(
                self._conn,
                tenant_slug=tenant,
                service=_SERVICE,
                action="document.metadata_element_actualizado",
                object_type="metadata_element",
                object_ref=str(element_id),
                actor=actor,
                payload={"cambios": sorted(data.keys())},
                canal=_audit.CANAL_PETICION,
            )
        return row

    async def delete(
        self,
        element_id: Any,
        *,
        tenant_slug: str | None,
        actor: str | None,
    ) -> bool:
        _require_audit_available()
        tenant = _audit_tenant(tenant_slug)
        async with self._conn.transaction():
            deleted = await self._repo.delete(element_id)
            if not deleted:
                return False
            await _audit.append(
                self._conn,
                tenant_slug=tenant,
                service=_SERVICE,
                action="document.metadata_element_eliminado",
                object_type="metadata_element",
                object_ref=str(element_id),
                actor=actor,
                payload={},
                canal=_audit.CANAL_PETICION,
            )
        return True
