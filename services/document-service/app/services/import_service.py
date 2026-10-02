# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Importación interoperable de radicados (E11, RF-INT-05).

Ingiere un paquete producido por INT-01 (o un export externo mapeado al mismo
formato). Garantías (Ley 594/2000 art. 19; Ac. AGN 060/2001; spec §5 inv. 1):

1. **Validación previa, TODO-o-NADA**: cada radicado valida contra el JSON Schema
   de confianza del servicio (NO el del paquete, que podría venir laxo) y **el
   SHA-256 de cada binario se verifica** contra el declarado. Si algo falla, se
   aborta ANTES de tocar la BD → sin estado parcial (422).
2. **Identidad preservada**: se reingiere con el NÚMERO de radicado original (no se
   regenera); una colisión con un radicado existente se OMITE (no se sobreescribe).
3. **No-write-up**: un radicado con `nivel_seguridad > clearance` del importador se
   OMITE y se reporta (RF-SEG-08; espejo del no-write-up de radicación).
4. **Aislamiento**: los datos entran al schema/bucket del tenant del JWT, NUNCA al
   `tenant` declarado en el paquete (que solo se conserva dentro del `tracking`).

Guardas anti-abuso del ZIP: nº de entradas, tamaño descomprimido total y por-fichero,
y saneo de rutas (anti zip-slip). Empaquetado síncrono (async/streaming = roadmap).

Limitación declarada (Seg #4, namespace de numeración): el radicado reingiere con su
`tracking` original (incluido el `tenant_code` de ORIGEN). En el caso normal de interop
—importar de OTRO SGD— el `tenant_code` difiere del nativo y no hay colisión con la
secuencia local. Si el paquete trae el `tenant_code` propio (p.ej. re-importar el propio
export), un número importado podría chocar a futuro con la numeración nativa: el
`UNIQUE(tracking_number)` + el savepoint por-ítem evitan corrupción, pero `tracking_sequences` se RECONCILIA al importar (ver `_reconciliar_secuencia`): el
contador nativo se adelanta al número importado, de modo que la siguiente radicación
salta por encima en vez de chocar. Sin eso, la unicidad evitaba la corrupción al precio
de que la radicación siguiente FALLARA sobre un consecutivo ya consumido — y un número
de radicado es inmutable por Ley 594, no se devuelve."""

import hashlib
import io
import json
import re
import uuid
import zipfile
from datetime import UTC, datetime
from typing import Any

import asyncpg
from jsonschema import Draft202012Validator
from orpycamcp_common import audit

from app.clients import storage_client
from app.repositories.import_repository import ImportRepository
from app.services.export_service import RADICADO_SCHEMA

_SERVICE = "document-service"
_VALIDATOR = Draft202012Validator(RADICADO_SCHEMA)

# Guardas anti zip-bomb — conservadoras para acotar el trabajo SÍNCRONO en memoria
# (validación + hashing bloquean el event-loop; el offload a threadpool/streaming es
# deuda de roadmap, Seg #3).
MAX_ENTRIES = 20000
MAX_TOTAL_UNCOMPRESSED = 256 * 1024 * 1024  # 256 MiB
MAX_FILE_UNCOMPRESSED = 64 * 1024 * 1024  # 64 MiB por miembro

_TRACKING_RE = re.compile(r"^(\d{4})-(.+)-([ESI])-(\d+)$")
_MEMBER_RE = re.compile(r"^(manifiesto\.json|LEEME\.txt|checksums\.txt|esquema/[\w.-]+|"
                        r"radicados/[\w.-]+\.json|anexos/[\w.-]+/[\w.\- ]+)$")


class PackageValidationError(Exception):
    """Fallo de validación del paquete → 422, sin estado parcial en BD."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


class ImportService:
    def __init__(self, conn, tenant_slug: str) -> None:
        self._conn = conn
        self._repo = ImportRepository(conn)
        self._slug = tenant_slug

    # --- fase 1: abrir + validar (sin tocar la BD) -------------------------
    def _open_safe(self, zip_bytes: bytes) -> zipfile.ZipFile:
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile as exc:
            raise PackageValidationError(f"no es un ZIP válido: {exc}") from exc
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise PackageValidationError(f"demasiadas entradas ({len(infos)} > {MAX_ENTRIES})")
        total = 0
        for info in infos:
            name = info.filename
            if name.endswith("/"):
                continue
            # Anti zip-slip: nombre saneado, sin absolutos ni '..'.
            if name.startswith("/") or ".." in name.split("/") or not _MEMBER_RE.match(name):
                raise PackageValidationError(f"ruta de miembro no permitida: {name!r}")
            if info.file_size > MAX_FILE_UNCOMPRESSED:
                raise PackageValidationError(f"miembro demasiado grande: {name}")
            total += info.file_size
            if total > MAX_TOTAL_UNCOMPRESSED:
                raise PackageValidationError("paquete descomprimido excede el máximo")
        return zf

    def _validate(self, zf: zipfile.ZipFile) -> list[dict[str, Any]]:
        """Valida esquema + fixity de TODO. Devuelve las entidades parseadas o lanza."""
        try:
            manifest = json.loads(zf.read("manifiesto.json"))
        except KeyError as exc:
            raise PackageValidationError("falta manifiesto.json") from exc
        except json.JSONDecodeError as exc:
            raise PackageValidationError(f"manifiesto.json inválido: {exc}") from exc

        entities: list[dict[str, Any]] = []
        for item in manifest.get("radicados", []):
            data_file = item.get("archivo", "")
            declared = item.get("sha256", "")
            try:
                raw = zf.read(data_file)
            except KeyError as exc:
                raise PackageValidationError(f"falta el archivo de datos {data_file}") from exc
            # Fixity del propio JSON de datos contra el manifiesto.
            if declared and _sha256(raw) != declared:
                raise PackageValidationError(f"checksum del dato {data_file} no coincide con el manifiesto")
            try:
                entity = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise PackageValidationError(f"{data_file} no es JSON válido: {exc}") from exc
            # Esquema de CONFIANZA del servicio (no el del paquete).
            errs = sorted(_VALIDATOR.iter_errors(entity), key=lambda e: e.path)
            if errs:
                raise PackageValidationError(f"{data_file} no valida contra el esquema: {errs[0].message}")
            # **Fixity de CADA binario** antes de ingerir (RF-INT-05, crítico).
            for anexo in entity.get("anexos", []):
                if anexo.get("ausente"):
                    continue
                arc = anexo.get("archivo")
                sha = anexo.get("sha256")
                # Un anexo no-ausente DEBE traer archivo + sha256 (el esquema solo
                # exige nombre_original); si no, es paquete inválido → 422, no un 500.
                if not arc or not sha:
                    raise PackageValidationError(
                        f"anexo '{anexo.get('nombre_original')}' sin archivo/sha256 ni marca ausente"
                    )
                try:
                    blob = zf.read(arc)
                except KeyError as exc:
                    raise PackageValidationError(f"falta el binario {arc}") from exc
                if _sha256(blob) != sha:
                    raise PackageValidationError(f"checksum del binario {arc} NO coincide — import abortado")
            entities.append(entity)
        return entities

    # --- fase 2: ingerir -----------------------------------------------------
    async def import_package(
        self, *, zip_bytes: bytes, max_clearance: int, actor: uuid.UUID | None
    ) -> dict[str, Any]:
        zf = self._open_safe(zip_bytes)
        entities = self._validate(zf)  # fase 1: fixity + esquema, todo-o-nada

        omit_existentes: list[str] = []
        omit_clearance: list[str] = []

        # Fase 2a: disposición (lecturas, SIN tx) — parse + no-write-up + colisión.
        plan: list[tuple[dict[str, Any], Any]] = []
        for entity in entities:
            tracking = entity["numero_radicado"]
            m = _TRACKING_RE.match(tracking)
            if not m:
                raise PackageValidationError(f"número de radicado con formato inválido: {tracking}")
            if entity["nivel_seguridad"] > max_clearance:  # no-write-up
                omit_clearance.append(tracking)
                continue
            if await self._repo.exists(tracking):  # identidad inmutable → no sobreescribe
                omit_existentes.append(tracking)
                continue
            plan.append((entity, m))

        # Fase 2b: SUBIR binarios FUERA de la transacción (Seg #2: no sostener la tx
        # de BD abierta a través de llamadas HTTP a storage). Un fallo de subida aborta
        # antes de tocar la BD; los blobs ya subidos quedan huérfanos (deuda declarada).
        subidos_por_entity: dict[int, list[tuple[uuid.UUID, dict[str, Any]]]] = {}
        for entity, _m in plan:
            subidos: list[tuple[uuid.UUID, dict[str, Any]]] = []
            for anexo in entity.get("anexos", []):
                if anexo.get("ausente"):
                    continue
                blob = zf.read(anexo["archivo"])  # ya verificado en fase 1
                result = await storage_client.upload_bytes(
                    tenant_slug=self._slug,
                    filename=anexo.get("nombre_original", "anexo"),
                    mime_type=anexo.get("mime_type"),
                    data=blob,
                    uploaded_by=actor,
                )
                # `result.file_id` y no `result["file_id"]`: la clave inventada
                # nunca existió en el contrato de storage (`FileResponse.id`) y el
                # test la tapaba porque su mock la devolvía. El cliente traduce
                # ahora el formato de cable en un solo sitio.
                subidos.append((result.file_id, anexo))
            subidos_por_entity[id(entity)] = subidos

        # Fase 2c: transacción de BD (rápida, sin HTTP). Un SAVEPOINT por-ítem absorbe
        # la carrera de unicidad del tracking (Seg #1): un import concurrente que metió
        # el mismo número → UniqueViolation en el savepoint → se OMITE, sin abortar el
        # lote ni degradar a 500.
        importados: list[str] = []
        # Estado con el que llegó cada radicado en el paquete: la importación
        # conserva el `status` de origen (es una restauración, no un acto del
        # flujo, ADR-028), así que el asiento debe decir con cuál entró cada uno.
        status_origen: dict[str, str] = {}
        pendientes_antecedente: list[tuple[uuid.UUID, str]] = []
        relaciones = 0
        no_resueltos: list[str] = []
        async with self._conn.transaction():
            for entity, m in plan:
                tracking = entity["numero_radicado"]
                try:
                    async with self._conn.transaction():  # savepoint por-ítem
                        rid = await self._insert(entity, m, subidos_por_entity[id(entity)])
                except asyncpg.UniqueViolationError:
                    omit_existentes.append(tracking)  # carrera con otro import
                    continue
                importados.append(tracking)
                status_origen[tracking] = (entity.get("metadata_sistema") or {}).get(
                    "status", "registered"
                )
                if entity.get("antecedente"):
                    pendientes_antecedente.append((rid, entity["antecedente"]))

            # Relaciones E↔S por número; un antecedente no resuelto se DECLARA (inv. 3),
            # no se descarta en silencio.
            for rid, ant_tracking in pendientes_antecedente:
                ant_id = await self._repo.id_by_tracking(ant_tracking)
                if ant_id is not None:
                    await self._repo.set_responde_a(rid, ant_id)
                    relaciones += 1
                else:
                    no_resueltos.append(ant_tracking)

            import_uuid = str(uuid.uuid4())
            # Auditoría DURA (no best-effort) DENTRO de la tx: si el asiento del acto de
            # ingesta masiva falla, TODO el import revierte — nunca datos sin traza.
            await self._audit(
                import_uuid,
                actor,
                importados,
                omit_existentes,
                omit_clearance,
                no_resueltos,
                status_origen,
            )

        return {
            "import_uuid": import_uuid,
            "importados": len(importados),
            "omitidos_existentes": len(omit_existentes),
            "omitidos_clearance": len(omit_clearance),
            "relaciones_reconstruidas": relaciones,
            "antecedentes_no_resueltos": no_resueltos,
        }

    async def _reconciliar_secuencia(self, *, year: int, doc_type: str, sequence: int) -> None:
        """Adelanta el contador nativo si el número importado lo alcanza o supera.

        El radicado reingresa con su número de ORIGEN. Cuando el paquete viene de
        otro sistema el código de entidad difiere y no hay colisión posible; pero
        al reimportar un paquete propio —una restauración, un ensayo de
        continuidad, una migración de servidor— los números importados caen en el
        MISMO espacio que la radicación nativa va a entregar después.

        La unicidad del número evita la corrupción, sí, pero al precio de que la
        siguiente radicación FALLE al chocar, y falle sobre un consecutivo ya
        consumido: en Colombia un número de radicado es inmutable por Ley 594 y
        no se devuelve. Adelantar el contador convierte ese choque en un salto,
        que es el resultado correcto: el número importado existe, luego el
        siguiente nativo tiene que ir por detrás de él.

        Se hace por MÁXIMO y no por suma, de modo que reimportar dos veces el
        mismo paquete no adelanta el contador dos veces.
        """
        await self._conn.execute(
            """
            INSERT INTO tracking_sequences (year, doc_type, last_seq)
            VALUES ($1, $2, $3)
            ON CONFLICT (year, doc_type)
            DO UPDATE SET last_seq = GREATEST(tracking_sequences.last_seq, EXCLUDED.last_seq)
            """,
            year,
            doc_type,
            sequence,
        )

    async def _insert(self, entity, m, subidos) -> uuid.UUID:
        sist = entity.get("metadata_sistema", {})
        clasif = entity.get("clasificacion") or {}
        rid = await self._repo.insert_radicado(
            {
                "tracking_number": entity["numero_radicado"],
                "doc_type": entity["tipo"],
                "year": int(m.group(1)),
                "sequence": int(m.group(4)),
                "tenant_code": m.group(2),
                "subject": entity["asunto"],
                "nivel_seguridad": entity["nivel_seguridad"],
                "status": sist.get("status", "registered"),
                "pages": sist.get("pages", 0),
                "origin_dept": sist.get("origin_dept"),
                "dest_dept": sist.get("dest_dept"),
                "sender_name": sist.get("sender_name"),
                "sender_entity": sist.get("sender_entity"),
                "observations": sist.get("observations"),
                "registered_at": _parse_dt(entity.get("fecha_radicacion")),
                "metadata": entity.get("metadata_contextual") or {},
                "disposition": entity.get("disposicion"),
                "clasificacion_fundamento": clasif.get("fundamento"),
                "clasificacion_causal": clasif.get("causal"),
                "clasificacion_plazo_meses": clasif.get("plazo_meses"),
            }
        )
        for file_id, anexo in subidos:
            await self._repo.insert_anexo(
                radicado_id=rid,
                file_id=file_id,
                filename=anexo.get("nombre_original", "anexo"),
                file_size=anexo.get("tamano_bytes"),
                mime_type=anexo.get("mime_type"),
            )
        # El contador nativo se adelanta DESPUÉS de insertar y dentro de la misma
        # transacción del ítem: si la inserción se revierte por su savepoint, el
        # contador tampoco se mueve. Adelantarlo antes dejaría huecos por
        # radicados que al final no entraron.
        await self._reconciliar_secuencia(
            year=int(m.group(1)), doc_type=entity["tipo"], sequence=int(m.group(4))
        )
        return rid

    async def _audit(
        self, import_uuid, actor, importados, omit_ex, omit_cl, no_resueltos, status_origen
    ) -> None:
        # DURA (no swallow): un fallo aquí propaga y revierte el import (trazabilidad
        # fail-closed para una ingesta masiva). El payload incluye la LISTA de números
        # importados (procedencia, cadena de custodia — MoReq/inv. 8), no solo conteos.
        await audit.append(
            self._conn,
            tenant_slug=self._slug,
            service=_SERVICE,
            action="radicado.import",
            object_type="import",
            object_ref=import_uuid,
            actor=str(actor) if actor else None,
            payload={
                "radicados_importados": importados,
                "status_origen": status_origen,
                "omitidos_existentes": len(omit_ex),
                "omitidos_clearance": len(omit_cl),
                "antecedentes_no_resueltos": no_resueltos,
            },
        )
