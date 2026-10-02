# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Exportación interoperable de radicados (E11, RF-INT-01).

Produce un **paquete interoperable** (ZIP) con: manifiesto (UUID de exportación,
comentario, marcas inicio/fin, entidades, excluidos), el **JSON Schema** de la
entidad radicado, un JSON por radicado **validado contra ese esquema** ("en su
totalidad": metadatos de sistema + contextuales + disposición + clasificación +
anexos + historial), los binarios de los anexos, y `checksums.txt` (SHA-256).

**No-read-up:** el `ExportRepository` ya recorta por `max_clearance`; los radicados
sobre-clearance se omiten y se reportan content-free en el manifiesto (Ac. AGN
001/2024 4.3.2.3). El **número de radicado se preserva** como identidad (Ac. AGN
060/2001) — INT-05 (import) lo conservará al reingerir.

Empaquetado FÍSICO: ZIP en memoria, síncrono. El BagIt/OAIS + WORM lo provee E10
(deuda de roadmap); aquí se define el CONTENIDO LÓGICO y el contrato de esquema.
"""

import asyncio
import hashlib
import io
import json
import logging
import re
import uuid
import zipfile
from datetime import UTC, datetime
from typing import Any

from jsonschema import Draft202012Validator
from orpycamcp_common import audit

from app.clients import storage_client
from app.repositories.export_repository import ExportRepository

logger = logging.getLogger(__name__)

_SERVICE = "document-service"
SCHEMA_VERSION = "1.0"

# Tope defensivo del export síncrono en memoria (Seg #2, anti-DoS): sin él, un export
# sin filtros sobre un tenant grande bloquearía el event-loop del worker compartido.
# El streaming/async es deuda de roadmap; hasta entonces, 422 si excede.
MAX_EXPORT_RADICADOS = 5000
# Tope de bytes de ANEXOS acumulados en el ZIP en memoria. El de radicados no
# acota nada: 5000 radicados con anexos de decenas de MiB son decenas de GiB.
MAX_EXPORT_BYTES = 512 * 1024 * 1024


class ExportBytesTooLargeError(Exception):
    """Los anexos del export exceden el tope de bytes → 422 (acotar con year/doc_type)."""


class ExportTooLargeError(Exception):
    """El export excede el tope síncrono → 422 (usar filtros year/doc_type)."""

    def __init__(self, total: int) -> None:
        self.total = total
        super().__init__(f"export de {total} radicados excede el tope {MAX_EXPORT_RADICADOS}")

# JSON Schema publicado de la entidad radicado — se incluye EN el paquete para que
# un tercero (u OrpycaMCP en INT-05) valide cada `radicados/*.json` antes de ingerir.
# Estados con los que un radicado puede cruzar una frontera de interoperabilidad
# (export/import). `anulado` NO está: ver el comentario en `RADICADO_SCHEMA`.
ESTADOS_IMPORTABLES: tuple[str, ...] = ("registered", "distributed", "in_progress", "closed")

RADICADO_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": f"https://orpyca.com/schemas/radicado-{SCHEMA_VERSION}.json",
    "title": "OrpycaMCP — Radicado (paquete interoperable E11/INT-01)",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "tipo_entidad", "numero_radicado", "tipo", "asunto",
        "nivel_seguridad", "fecha_radicacion",
    ],
    "properties": {
        "tipo_entidad": {"const": "radicado"},
        "esquema_version": {"type": "string"},
        "numero_radicado": {"type": "string", "minLength": 1},
        "tipo": {"enum": ["E", "S", "I"]},
        "asunto": {"type": "string"},
        "nivel_seguridad": {"type": "integer", "minimum": 1, "maximum": 3},
        "fecha_radicacion": {"type": "string", "format": "date-time"},
        "fecha_actualizacion": {"type": ["string", "null"], "format": "date-time"},
        "antecedente": {"type": ["string", "null"]},
        # `status`, si viene, es de un conjunto CERRADO que EXCLUYE `anulado`: el
        # export no incluye anulados (`export_repository`: `status <> 'anulado'`) y
        # un paquete que lo declare no trae el acto de anulación (causal, aprobador,
        # asiento) que lo justificaría — importarlo crearía un radicado anulado SIN
        # anulación, imposible de explicar y de deshacer. Se rechaza al validar.
        "metadata_sistema": {
            "type": "object",
            "properties": {
                "status": {"enum": list(ESTADOS_IMPORTABLES)},
                # INFORMATIVOS (ADR-029): dónde estaba el radicado al exportar. El
                # import los IGNORA —la ubicación la deriva el flujo de la entidad
                # que recibe, no un paquete— y por eso no son `required`.
                "ubicacion_dept": {"type": ["string", "null"]},
                "ubicacion_dept_code": {"type": ["integer", "null"]},
            },
        },
        "metadata_contextual": {"type": "object"},
        "disposicion": {"type": ["object", "null"]},
        "clasificacion": {
            "type": ["object", "null"],
            "properties": {
                "fundamento": {"type": ["string", "null"]},
                "causal": {"type": ["string", "null"]},
                "plazo_meses": {"type": ["integer", "null"]},
            },
        },
        "anexos": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["nombre_original"],
                "properties": {
                    # `archivo`/`sha256` presentes si el binario está en el paquete;
                    # `ausente: true` (sin ellos) si fue purgado en storage — la
                    # ausencia queda TRAZABLE en el JSON, no se descarta en silencio.
                    "archivo": {"type": "string"},
                    "nombre_original": {"type": "string"},
                    "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                    "mime_type": {"type": ["string", "null"]},
                    "tamano_bytes": {"type": ["integer", "null"]},
                    "ausente": {"type": "boolean"},
                },
            },
        },
        "historial": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "accion": {"type": "string"},
                    "fecha": {"type": "string", "format": "date-time"},
                    "actor": {"type": ["string", "null"]},
                    "canal": {"type": ["string", "null"]},
                },
            },
        },
    },
}

_VALIDATOR = Draft202012Validator(RADICADO_SCHEMA)


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _col_o_none(row: Any, nombre: str) -> Any:
    return row[nombre] if nombre in row else None


def _safe_leaf(name: str) -> str:
    """Nombre de archivo saneado a hoja (anti zip-slip): sin separadores ni `..`."""
    leaf = re.sub(r"[^A-Za-z0-9._-]", "_", (name or "anexo").rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
    return leaf.strip(".") or "anexo"


def _safe_tracking(tracking: str) -> str:
    """El número de radicado es system-generated (`AÑO-TENANT-TIPO-SEC`), pero se
    sanea igual antes de usarlo en un arcname (defensa en profundidad, Seg #3)."""
    return re.sub(r"[^0-9A-Za-z-]", "_", tracking or "radicado") or "radicado"


def _loads(value: Any) -> Any:
    """asyncpg devuelve JSONB como str; normaliza a dict/list."""
    if isinstance(value, str):
        return json.loads(value)
    return value or {}


def _escribir_binario(zf: zipfile.ZipFile, arcname: str, data: bytes) -> str:
    digest = _sha256(data)
    zf.writestr(arcname, data)
    return digest


class ExportValidationError(Exception):
    """Un radicado no valida contra el esquema publicado — aborta (nunca se emite un
    paquete que se declara conforme y no lo es, RF-FIR-15)."""


class ExportService:
    def __init__(self, conn, tenant_slug: str) -> None:
        self._conn = conn
        self._repo = ExportRepository(conn)
        self._slug = tenant_slug

    async def _entity(self, row) -> dict[str, Any]:
        tracking = row["tracking_number"]
        anexos_meta = await self._repo.list_anexos(row["id"])
        history = await self._repo.history(tenant_slug=self._slug, tracking_number=tracking)
        clasif = None
        if row["nivel_seguridad"] >= 2 or row["clasificacion_fundamento"]:
            clasif = {
                "fundamento": row["clasificacion_fundamento"],
                "causal": row["clasificacion_causal"],
                "plazo_meses": row["clasificacion_plazo_meses"],
            }
        return {
            "tipo_entidad": "radicado",
            "esquema_version": SCHEMA_VERSION,
            "numero_radicado": tracking,
            "tipo": row["doc_type"],
            "asunto": row["subject"] or "",  # defensivo: un asunto NULL no aborta el lote
            "nivel_seguridad": row["nivel_seguridad"],
            "fecha_radicacion": _iso(row["registered_at"]),
            "fecha_actualizacion": _iso(row["updated_at"]),
            # Antecedente E↔S (número portable, no UUID) para la totalidad referencial.
            "antecedente": row["antecedente"] if "antecedente" in row else None,
            "metadata_sistema": {
                "doc_class": row["doc_class"],
                "origin_dept": row["origin_dept"],
                "dest_dept": row["dest_dept"],
                "sender_name": row["sender_name"],
                "sender_entity": row["sender_entity"],
                "status": row["status"],
                "pages": row["pages"],
                "observations": row["observations"],
                "tenant_code": row["tenant_code"],
                # Informativos: el import los ignora (ADR-029).
                "ubicacion_dept": _col_o_none(row, "ubicacion_dept"),
                "ubicacion_dept_code": _col_o_none(row, "ubicacion_dept_code"),
            },
            "metadata_contextual": _loads(row["metadata"]),
            "disposicion": _loads(row["disposition"]) or None,
            "clasificacion": clasif,
            "anexos": [],  # lo llena build_export (presente vs ausente por-anexo)
            "_anexos_meta": [dict(a) for a in anexos_meta],  # interno, se quita antes de serializar
            "historial": [
                {"accion": h["action"], "fecha": _iso(h["ts"]), "actor": h["actor"], "canal": h["canal"]}
                for h in history
            ],
        }

    async def build_export(
        self,
        *,
        max_clearance: int,
        comentario: str | None,
        year: int | None,
        doc_type: str | None,
        actor: str | None = None,
    ) -> tuple[bytes, str, int, int]:
        """Devuelve (zip_bytes, export_uuid, num_radicados, num_excluidos)."""
        inicio = datetime.now(tz=UTC)
        export_uuid = str(uuid.uuid4())
        # Tope defensivo ANTES de armar nada en memoria (Seg #2, anti-DoS).
        total = await self._repo.count_incluidos(
            max_clearance=max_clearance, year=year, doc_type=doc_type
        )
        if total > MAX_EXPORT_RADICADOS:
            raise ExportTooLargeError(total)
        rows = await self._repo.list_radicados(
            max_clearance=max_clearance, year=year, doc_type=doc_type
        )
        excluidos = await self._repo.count_excluidos(
            max_clearance=max_clearance, year=year, doc_type=doc_type
        )

        buf = io.BytesIO()
        bytes_acumulados = 0
        checksums: list[tuple[str, str]] = []
        manifest_entidades: list[dict[str, Any]] = []

        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Esquema publicado (para validar al importar).
            schema_bytes = json.dumps(RADICADO_SCHEMA, ensure_ascii=False, indent=2).encode()
            zf.writestr("esquema/radicado.schema.json", schema_bytes)
            checksums.append(("esquema/radicado.schema.json", _sha256(schema_bytes)))

            for row in rows:
                entity = await self._entity(row)
                anexos_meta = entity.pop("_anexos_meta")
                tracking_safe = _safe_tracking(row["tracking_number"])
                # Binarios: descarga por storage_client, calcula SHA-256, mete al ZIP.
                # Un anexo purgado en storage queda como entrada `ausente` TRAZABLE
                # (no se descarta en silencio) — el esquema la admite sin archivo/sha256.
                for i, a in enumerate(anexos_meta):
                    base = {
                        "nombre_original": a["filename"],
                        "mime_type": a["mime_type"],
                        "tamano_bytes": a["file_size"],
                    }
                    data = await self._fetch_bytes(a["file_id"])
                    if data is None:
                        entity["anexos"].append({**base, "ausente": True})
                        continue
                    bytes_acumulados += len(data)
                    if bytes_acumulados > MAX_EXPORT_BYTES:
                        raise ExportBytesTooLargeError
                    arcname = f"anexos/{tracking_safe}/{i}-{_safe_leaf(a['filename'])}"
                    # SHA-256 + deflate del binario en un hilo: son CPU puro y en
                    # el event loop congelaban el servicio para todos los tenants.
                    digest = await asyncio.to_thread(_escribir_binario, zf, arcname, data)
                    entity["anexos"].append({**base, "archivo": arcname, "sha256": digest})
                    checksums.append((arcname, digest))

                _validate(entity)  # garantía: valida contra el esquema publicado
                data_file = f"radicados/{tracking_safe}.json"
                body = json.dumps(entity, ensure_ascii=False, indent=2).encode()
                zf.writestr(data_file, body)
                digest = _sha256(body)
                checksums.append((data_file, digest))
                manifest_entidades.append(
                    {"numero_radicado": row["tracking_number"], "archivo": data_file, "sha256": digest}
                )

            fin = datetime.now(tz=UTC)
            manifest = {
                "export_uuid": export_uuid,
                "comentario": comentario or "",
                "tenant": self._slug,
                "esquema": {"entidad": "radicado", "version": SCHEMA_VERSION,
                            "archivo": "esquema/radicado.schema.json"},
                "generado_inicio": _iso(inicio),
                "generado_fin": _iso(fin),
                "num_radicados": len(manifest_entidades),
                "radicados": manifest_entidades,
                "excluidos": {
                    "num": excluidos,
                    "motivo": "sobre-clearance (no-read-up, Ley 1712/2014 arts. 18-19)",
                },
                "empaquetado_fisico": "zip-provisional (BagIt/OAIS+WORM = E10, roadmap)",
            }
            man_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
            zf.writestr("manifiesto.json", man_bytes)
            checksums.append(("manifiesto.json", _sha256(man_bytes)))

            leeme = _LEEME.format(uuid=export_uuid, n=len(manifest_entidades), excl=excluidos)
            zf.writestr("LEEME.txt", leeme.encode())
            zf.writestr(
                "checksums.txt",
                "".join(f"{h}  {name}\n" for name, h in checksums).encode(),
            )

        # Auditar el EGRESO (Alta, compliance #9 / seg #1): quién exportó, con qué
        # alcance y el UUID — content-free (cantidades y filtros, nunca asuntos ni
        # números de los radicados incluidos). Best-effort: no degradar el export a
        # 500 por un fallo de auditoría (mismo criterio que las lecturas RF-BUS-10).
        try:
            await audit.append(
                self._conn,
                tenant_slug=self._slug,
                service=_SERVICE,
                action="radicado.export",
                object_type="export",
                object_ref=export_uuid,
                actor=actor,
                payload={
                    "num_radicados": len(manifest_entidades),
                    "num_excluidos": excluidos,
                    "max_clearance": max_clearance,
                    "filtros": {"year": year, "doc_type": doc_type},
                },
            )
        except Exception:
            logger.warning("audit.append (radicado.export) failed", exc_info=True)

        return buf.getvalue(), export_uuid, len(manifest_entidades), excluidos

    async def _fetch_bytes(self, file_id) -> bytes | None:
        try:
            stream = await storage_client.open_content_stream(
                tenant_slug=self._slug, file_id=file_id
            )
        except storage_client.StorageContentError:
            return None
        if stream is None:
            return None
        chunks = [chunk async for chunk in stream.aiter_bytes()]
        await stream.aclose()
        return b"".join(chunks)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate(entity: dict[str, Any]) -> None:
    errors = sorted(_VALIDATOR.iter_errors(entity), key=lambda e: e.path)
    if errors:
        raise ExportValidationError(
            f"radicado {entity.get('numero_radicado')} no valida: {errors[0].message}"
        )


_LEEME = (
    "Paquete interoperable OrpycaMCP (E11/RF-INT-01)\n"
    "UUID de exportacion: {uuid}\n"
    "Radicados incluidos: {n} — excluidos por clearance: {excl}\n\n"
    "Contenido:\n"
    "  manifiesto.json            — metadatos del paquete (UUID, comentario, marcas, entidades)\n"
    "  esquema/radicado.schema.json — JSON Schema de la entidad radicado (draft 2020-12)\n"
    "  radicados/<numero>.json    — datos por radicado, VALIDAN contra el esquema\n"
    "  anexos/<numero>/<i>-<archivo> — binarios de los anexos\n"
    "  checksums.txt              — SHA-256 de cada miembro\n\n"
    "El numero de radicado se PRESERVA como identidad (Ac. AGN 060/2001).\n"
    "Empaquetado fisico provisional (ZIP); BagIt/OAIS+WORM lo provee E10.\n"
)
