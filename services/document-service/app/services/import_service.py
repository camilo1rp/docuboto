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

import asyncio
import hashlib
import io
import json
import logging
import re
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from jsonschema import Draft202012Validator
from orpycamcp_common import audit

from app.clients import storage_client
from app.core.causales_reserva import POR_CLASIFICAR, normalizar_para_importar
from app.repositories.import_repository import ImportRepository
from app.schemas.document import CLAVES_RESERVADAS
from app.services.export_service import RADICADO_SCHEMA
from app.services.import_huerfanos_service import ImportHuerfanosService

logger = logging.getLogger(__name__)
_SERVICE = "document-service"
_VALIDATOR = Draft202012Validator(RADICADO_SCHEMA)

# Guardas anti zip-bomb — conservadoras para acotar el trabajo SÍNCRONO en memoria
# (validación + hashing bloquean el event-loop; el offload a threadpool/streaming es
# deuda de roadmap, Seg #3).
MAX_ENTRIES = 20000
MAX_TOTAL_UNCOMPRESSED = 256 * 1024 * 1024  # 256 MiB
MAX_FILE_UNCOMPRESSED = 64 * 1024 * 1024  # 64 MiB por miembro

_TRACKING_RE = re.compile(r"^(\d{4})-(.+)-([ESI])-(\d+)$")

# Techo del consecutivo: `{SEQUENCE:06d}` y `tracking_sequences.last_seq` es INTEGER.
# Un paquete fabricado con `...-E-2147483647` dejaba el contador en INT_MAX y la
# siguiente radicación NATIVA fallaba con «integer out of range» (todo el año del
# tipo caído); con 999999 rompía el formato y dejaba un hueco irreversible.
MAX_SECUENCIA = 999_999
# Con el código de ESTA entidad el contador sí se mueve: el siguiente nativo es
# last_seq + 1, así que un propio en 999_999 lo dejaría en `-E-1000000` (7 dígitos,
# fuera del formato oficial, Ac. AGN 060/2001). El techo propio deja cabida a uno más.
MAX_SECUENCIA_PROPIA = MAX_SECUENCIA - 1
MIN_ANIO = 1900
# Holgura al fijar `subido_desde` (relojes de document-service y storage).
MARGEN_RELOJ = timedelta(seconds=30)
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


def _sin_claves_reservadas(contextual: dict | None) -> dict:
    """Las claves de procedencia DEL SISTEMA (`canal`, `consentimiento`, `from_borrador`)
    no pueden venir de un ZIP: los consumidores las leen como hechos de ESTE sistema
    (gate de publicación Ley 1581, origen del borrador). Si el sistema de origen las
    traía, se conservan como dato de procedencia bajo `procedencia_origen`."""
    meta = dict(contextual or {})
    movidas = {k: meta.pop(k) for k in sorted(CLAVES_RESERVADAS & set(meta))}
    if movidas:
        previa = meta.get("procedencia_origen")
        meta["procedencia_origen"] = {**(previa if isinstance(previa, dict) else {}), **movidas}
    return meta


class ImportService:
    def __init__(self, conn, tenant_slug: str) -> None:
        self._conn = conn
        self._repo = ImportRepository(conn)
        self._slug = tenant_slug
        self._tenant_code: str | None = None
        self._huerfanos = ImportHuerfanosService(conn, tenant_slug)
        # Desde cuándo cuenta un objeto como CREADO por esta importación (storage no
        # borra nada anterior). Se fija antes de la primera subida, con margen por
        # desfase de relojes entre servicios.
        self._subido_desde = datetime.now(tz=UTC)
        # C12: lo clasificado cuya causal no cumple el catálogo entra `por_clasificar`; el
        # texto original viaja al asiento de la importación (no a la columna).
        self._causales_normalizadas: list[dict[str, Any]] = []

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
            # Ley 1712/2014 art. 19/28 (AREA-09-04): un radicado clasificado o
            # reservado entra MOTIVADO. El esquema admite `fundamento: null` porque
            # el nivel 1 no lo lleva, pero con nivel >= 2 un fundamento vacío metería
            # una fila sin motivo en el índice del art. 20 — la promesa de «nunca
            # NULL» que la base también defiende (CHECK 041). Todo-o-nada: 422.
            nivel = entity.get("nivel_seguridad")
            if isinstance(nivel, int) and nivel >= 2:
                fundamento = (entity.get("clasificacion") or {}).get("fundamento")
                if not (isinstance(fundamento, str) and fundamento.strip()):
                    raise PackageValidationError(
                        f"el radicado {entity.get('numero_radicado')} tiene nivel_seguridad "
                        f"{nivel} sin clasificacion.fundamento (Ley 1712/2014 art. 19/28)"
                    )
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
        # Fase 1 en un hilo: abrir el ZIP, hashear cada binario con SHA-256 y validar
        # contra el esquema es CPU puro sobre hasta 256 MiB; en el event loop
        # congelaba el servicio entero —todos los tenants— mientras duraba.
        zf = await asyncio.to_thread(self._open_safe, zip_bytes)
        entities = await asyncio.to_thread(self._validate, zf)  # fixity + esquema, todo-o-nada

        omit_existentes: list[str] = []
        omit_clearance: list[str] = []

        # El contador solo se reconcilia con números de ESTA entidad: el de otra
        # vive en otro espacio de numeración y no puede mover el nuestro.
        self._tenant_code = await self._repo.get_tenant_code(self._slug)

        # Fase 2a: disposición (lecturas, SIN tx) — parse + no-write-up + colisión.
        plan: list[tuple[dict[str, Any], Any]] = []
        for entity in entities:
            tracking = entity["numero_radicado"]
            m = _TRACKING_RE.match(tracking)
            if not m:
                raise PackageValidationError(f"número de radicado con formato inválido: {tracking}")
            anio, secuencia = int(m.group(1)), int(m.group(4))
            if not MIN_ANIO <= anio <= datetime.now(tz=UTC).year:
                raise PackageValidationError(f"año fuera de rango en el número de radicado: {tracking}")
            if secuencia > MAX_SECUENCIA:
                raise PackageValidationError(
                    f"secuencia fuera de rango (máximo {MAX_SECUENCIA}) en el número de radicado: {tracking}"
                )
            if m.group(2) == self._tenant_code and secuencia > MAX_SECUENCIA_PROPIA:
                raise PackageValidationError(
                    "secuencia propia agota el año del tipo "
                    f"(máximo {MAX_SECUENCIA_PROPIA}) en el número de radicado: {tracking}"
                )
            if entity["nivel_seguridad"] > max_clearance:  # no-write-up
                omit_clearance.append(tracking)
                continue
            if await self._repo.exists(tracking):  # identidad inmutable → no sobreescribe
                omit_existentes.append(tracking)
                continue
            plan.append((entity, m))

        # Fase 2b: SUBIR binarios FUERA de la transacción (Seg #2: no sostener la tx
        # de BD abierta a través de llamadas HTTP a storage). Un fallo de subida aborta
        # antes de tocar la BD; los blobs ya subidos se ASIENTAN como huérfanos (document.import_binarios_sin_radicado).
        subidos_por_entity: dict[int, list[tuple[uuid.UUID, dict[str, Any]]]] = {}
        import_uuid = str(uuid.uuid4())
        self._subido_desde = datetime.now(tz=UTC) - MARGEN_RELOJ
        try:
            return await self._ingerir(
                zf, plan, subidos_por_entity, import_uuid, actor,
                omit_existentes, omit_clearance,
            )
        except Exception:
            # Reversión total: lo ya subido no tiene radicado. Se ASIENTA (ningún byte
            # del tenant queda sin traza, MoReq/ISO 15489) y se PURGA: storage
            # deduplica por hash, así que el borrado lo decide él comprobando que
            # ningún anexo del tenant lo referencie (AREA-09-14).
            todos = [
                (fid, anexo) for subs in subidos_por_entity.values() for fid, anexo in subs
            ]
            await self._asentar_huerfanos_tras_reversion(import_uuid, actor, todos)
            await self._purgar_sin_enmascarar(import_uuid)
            raise

    async def _ingerir(
        self, zf, plan, subidos_por_entity, import_uuid, actor, omit_existentes, omit_clearance
    ) -> dict[str, Any]:
        for entity, _m in plan:
            subidos: list[tuple[uuid.UUID, dict[str, Any]]] = []
            subidos_por_entity[id(entity)] = subidos  # visible aunque falle a mitad
            for anexo in entity.get("anexos", []):
                if anexo.get("ausente"):
                    continue
                blob = await asyncio.to_thread(zf.read, anexo["archivo"])  # ya verificado en fase 1
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
        huerfanos: list[tuple[uuid.UUID, dict[str, Any]]] = []
        async with self._conn.transaction():
            for entity, m in plan:
                tracking = entity["numero_radicado"]
                try:
                    async with self._conn.transaction():  # savepoint por-ítem
                        rid = await self._insert(entity, m, subidos_por_entity[id(entity)])
                except asyncpg.UniqueViolationError:
                    omit_existentes.append(tracking)  # carrera con otro import
                    huerfanos.extend(subidos_por_entity[id(entity)])
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

            if huerfanos:
                await self._asentar_huerfanos(import_uuid, actor, huerfanos)
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

        if huerfanos:
            # Ya con la transacción confirmada: los binarios de los ítems omitidos por
            # carrera no tienen radicado. La purga es HTTP y no puede sostener la tx.
            await self._purgar_sin_enmascarar(import_uuid)

        return {
            "import_uuid": import_uuid,
            "importados": len(importados),
            "omitidos_existentes": len(omit_existentes),
            "omitidos_clearance": len(omit_clearance),
            "relaciones_reconstruidas": relaciones,
            "antecedentes_no_resueltos": no_resueltos,
        }

    async def _asentar_huerfanos(
        self, import_uuid: str, actor: uuid.UUID | None, huerfanos: list[tuple[uuid.UUID, dict[str, Any]]]
    ) -> None:
        """Asiento content-free de binarios subidos que NO quedaron ligados a un radicado
        + su fila en la cola de purga (`import_binario_huerfano`), en la MISMA
        transacción que llama: si el asiento falla, tampoco queda pendiente de purga."""
        await self._huerfanos.registrar(
            uuid.UUID(import_uuid),
            actor,
            [(fid, anexo.get("sha256")) for fid, anexo in huerfanos],
            self._subido_desde,
        )
        await audit.append(
            self._conn,
            tenant_slug=self._slug,
            service=_SERVICE,
            action="document.import_binarios_sin_radicado",
            object_type="import",
            object_ref=import_uuid,
            actor=str(actor) if actor else None,
            payload={
                "import_uuid": import_uuid,
                "binarios": [
                    {"file_id": str(fid), "sha256": anexo.get("sha256")} for fid, anexo in huerfanos
                ],
            },
            canal=audit.CANAL_PETICION,
        )

    async def _asentar_huerfanos_tras_reversion(
        self, import_uuid: str, actor: uuid.UUID | None, huerfanos: list[tuple[uuid.UUID, dict[str, Any]]]
    ) -> None:
        if not huerfanos:
            return
        try:
            async with self._conn.transaction():
                await self._asentar_huerfanos(import_uuid, actor, huerfanos)
        except Exception:  # no enmascara el fallo original del import
            logger.exception("no se pudo asentar los binarios huérfanos del import %s", import_uuid)

    async def _purgar_sin_enmascarar(self, import_uuid: str) -> None:
        """Intenta purgar ya los binarios de ESTA importación. Best-effort: si storage
        no responde (o lo que sea), las filas quedan 'pendiente' y las recoge el
        barrido; el fallo original del import (o el éxito) no se enmascara."""
        try:
            await self._huerfanos.purgar(import_uuid=uuid.UUID(import_uuid))
        except Exception:
            logger.exception("no se pudo purgar de inmediato los huérfanos del import %s", import_uuid)

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
        nivel = int(entity["nivel_seguridad"])
        causal, plazo = normalizar_para_importar(
            nivel, clasif.get("causal"), clasif.get("plazo_meses")
        )
        if causal == POR_CLASIFICAR:
            self._causales_normalizadas.append(
                {
                    "numero_radicado": entity["numero_radicado"],
                    "causal_original": clasif.get("causal"),
                    "plazo_original": clasif.get("plazo_meses"),
                }
            )
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
                "metadata": _sin_claves_reservadas(entity.get("metadata_contextual")),
                "disposition": entity.get("disposicion"),
                "clasificacion_fundamento": clasif.get("fundamento"),
                "clasificacion_causal": causal,
                "clasificacion_plazo_meses": plazo,
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
        if m.group(2) == self._tenant_code:
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
                "causales_por_clasificar": self._causales_normalizadas,
            },
            canal=audit.CANAL_PETICION,
        )
