# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Repositorio de preservación digital (E10).

Plan versionado append-only (vigente = MAX(version)) y eventos de preservación
append-only (PREMIS-style). search_path al tenant en cada método.
"""

from __future__ import annotations

import json
import uuid

import asyncpg
from orpycamcp_common.tenant import slug_valido, tenant_schema

from app.repositories.file_repository import InvalidTenantSlugError

_PLAN_COLS = (
    "id, version, formatos_destino, formatos_origen, num_copias, "
    "periodicidad_fixity_dias, politica_migracion, contingencia, creado_por, created_at"
)
_EVT_COLS = "id, object_ref, tipo, resultado, hash_before, hash_after, detalle, agente, ts"
# E10 Increment B: `bagit` (columna legacy del stub) queda fuera — ya no se
# lee ni se escribe; el AIP real vive como ZIP en el bucket WORM (bucket +
# object_key), no como texto en esta tabla.
_AIP_COLS = (
    "id, expediente_id, premis_xml, aip_sha256, indice_sha256, num_documentos, "
    "estado, created_at, bucket, object_key, aip_bytes, formato, retain_until, "
    "retention_mode, legal_hold"
)


class PreservacionRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        # Defensa en profundidad (remediación de auditoría Baja, E07): el
        # slug llega ya validado por el gateway/claim JWT, pero se valida de
        # nuevo AQUÍ, antes de interpolarlo en `SET search_path`, con la
        # MISMA regex canónica que `app.core.database.tenant_schema()` y
        # `FileRepository` — nunca se confía en el slug crudo para construir
        # SQL, sin importar de dónde venga.
        if not slug_valido(tenant_slug):
            raise InvalidTenantSlugError(f"Invalid tenant slug: {tenant_slug!r}")
        self._conn = conn
        self._schema = tenant_schema(tenant_slug)

    async def _sp(self) -> None:
        await self._conn.execute(f'SET search_path = "{self._schema}", public')

    # -- plan ----------------------------------------------------------

    async def current_plan(self) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_PLAN_COLS} FROM preservacion_plan ORDER BY version DESC LIMIT 1"
        )

    async def current_num_copias(self) -> int | None:
        """``num_copias`` del plan vigente, o ``None`` si no hay plan.

        Usa ``fetchval`` (no ``fetchrow``/``current_plan``) DELIBERADAMENTE:
        ``empaquetar_aip`` ya consume una secuencia fija de ``fetchrow`` (doc(s)
        + índice opcional + INSERT RETURNING de `aip`) que los tests fijan con
        ``side_effect`` — leer aquí con ``fetchrow`` desplazaría esa lista y
        rompería todos los tests existentes de AIP. El llamador trata ``None``
        (sin plan) como ``num_copias=1`` (copia única, sin réplica).
        """
        await self._sp()
        return await self._conn.fetchval(
            "SELECT num_copias FROM preservacion_plan ORDER BY version DESC LIMIT 1"
        )

    async def current_formatos_origen(self) -> list[str]:
        """Los tipos MIME que la entidad declara en riesgo, del plan VIGENTE.

        Lee con ``fetchval`` y no reutiliza ``current_plan`` por la misma razón
        que ``current_num_copias``: el barrido consume una secuencia fija de
        ``fetchrow`` que los tests fijan con ``side_effect``, y leer aquí con
        ``fetchrow`` la desplazaría.

        Sin plan, o con el campo vacío, devuelve ``[]`` — política no declarada
        por la entidad. Quién decide entonces es el llamador (el barrido cae al
        valor por defecto del despliegue, si lo hay); este método no inventa
        una lista propia.
        """
        await self._sp()
        valor = await self._conn.fetchval(
            "SELECT formatos_origen FROM preservacion_plan ORDER BY version DESC LIMIT 1"
        )
        return list(valor or [])

    async def list_plan_versions(self) -> list[asyncpg.Record]:
        await self._sp()
        return list(
            await self._conn.fetch(
                f"SELECT {_PLAN_COLS} FROM preservacion_plan ORDER BY version DESC"
            )
        )

    async def insert_plan(self, data: dict, *, creado_por: str | None) -> asyncpg.Record:
        await self._sp()
        current = await self._conn.fetchval(
            "SELECT COALESCE(MAX(version), 0) FROM preservacion_plan"
        )
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO preservacion_plan
                (version, formatos_destino, formatos_origen, num_copias,
                 periodicidad_fixity_dias, politica_migracion, contingencia, creado_por)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING {_PLAN_COLS}
            """,
            current + 1,
            data["formatos_destino"],
            data.get("formatos_origen") or [],
            data["num_copias"],
            data["periodicidad_fixity_dias"],
            data.get("politica_migracion"),
            data.get("contingencia"),
            creado_por,
        )
        assert row is not None
        return row

    # -- eventos -------------------------------------------------------

    async def insert_migracion(self, data: dict) -> asyncpg.Record:
        """Registra una migración de formato (E10, RF-PRE-04, migración 015).

        Se inserta TAMBIÉN cuando falló: «se intentó y no se pudo» es
        información de preservación, no ruido — es lo que dice qué documentos
        siguen esperando una migración que no sale."""
        await self._sp()
        row = await self._conn.fetchrow(
            """
            INSERT INTO preservacion_migracion
                (file_id_origen, file_id_destino, formato_origen, formato_destino,
                 sha256_origen, sha256_destino, herramienta, estado, motivo, iniciada_por)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING *
            """,
            data["file_id_origen"],
            data["file_id_destino"],
            data["formato_origen"],
            data["formato_destino"],
            data["sha256_origen"],
            data["sha256_destino"],
            data["herramienta"],
            data["estado"],
            data["motivo"],
            data["iniciada_por"],
        )
        assert row is not None
        return row

    async def list_migraciones(self, file_id: uuid.UUID) -> list[asyncpg.Record]:
        """Cadena de procedencia de un archivo, la más reciente primero."""
        await self._sp()
        return list(
            await self._conn.fetch(
                "SELECT * FROM preservacion_migracion WHERE file_id_origen = $1 "
                "ORDER BY iniciada_at DESC",
                file_id,
            )
        )

    async def list_pendientes_migracion(
        self, mime_types: list[str], *, formato_destino: str, limit: int
    ) -> list[asyncpg.Record]:
        """Ficheros de un formato en riesgo que todavía no tienen copia de
        preservación — la cola del barrido periódico (E10, RF-PRE-04).

        Tres exclusiones, y la tercera es la que evita que el job se convierta
        en un bucle caro:

        * los **eliminados** (`deleted_at`): migrar lo que ya no está sería
          resucitarlo por la puerta de atrás;
        * los que ya tienen una migración `migrado` a ese formato — el índice
          único parcial de la migración 015 ya lo impide, pero llegar hasta la
          conversión para chocar contra él costaría un subproceso por archivo
          y barrida;
        * los que ya fallaron. **`fallido` y `no_verificado` NO son lo mismo**:
          `fallido` dice algo del ARCHIVO —no se pudo convertir, o el resultado
          no era conforme— y reintentarlo cada día no lo va a arreglar; hace
          falta que alguien mire. `no_verificado` dice algo del DESPLIEGUE —se
          convirtió y no había validador con qué comprobarlo—, así que sí se
          reintenta: el día que el validador esté instalado, la barrida
          siguiente lo resuelve sola.
        """
        mime_types = [m.strip().lower() for m in mime_types if m and m.strip()]
        if not mime_types:
            # Política sin declarar. Se responde vacío en vez de «todos»: la
            # dirección segura es no convertir el archivo de nadie por omisión.
            return []
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT f.id, f.filename, f.mime_type, f.sha256
                  FROM files f
                 WHERE f.deleted_at IS NULL
                   -- `lower(...)`: los tipos MIME son case-insensitive
                   -- (RFC 2045 §5.1) y el valor guardado es el que mandó quien
                   -- subió el archivo. Comparar en crudo dejaría un
                   -- "Application/Msword" fuera de una política que sí lo
                   -- nombra, y el barrido diría cero candidatos sin que nada
                   -- pareciera roto. No cuesta índice: no hay ninguno sobre
                   -- `mime_type`, la exploración ya era secuencial.
                   AND lower(f.mime_type) = ANY($1::text[])
                   AND NOT EXISTS (
                       SELECT 1 FROM preservacion_migracion m
                        WHERE m.file_id_origen = f.id
                          AND m.formato_destino = $2
                          AND m.estado IN ('migrado', 'fallido')
                   )
                 ORDER BY f.uploaded_at ASC
                 LIMIT $3
                """,
                mime_types,
                formato_destino,
                limit,
            )
        )

    async def insert_evento(self, data: dict, *, agente: str | None) -> asyncpg.Record:
        await self._sp()
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO preservacion_evento
                (object_ref, tipo, resultado, hash_before, hash_after, detalle, agente)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
            RETURNING {_EVT_COLS}
            """,
            data["object_ref"],
            data["tipo"],
            data["resultado"],
            data.get("hash_before"),
            data.get("hash_after"),
            json.dumps(data.get("detalle") or {}),
            agente,
        )
        assert row is not None
        return row

    async def find_evento_por_dedup(
        self, *, object_ref: str, tipo: str, dedup_key: str
    ) -> asyncpg.Record | None:
        """Evento ya registrado con esa clave de idempotencia (`detalle.dedup_key`), para que
        un reintento at-least-once de otro servicio no duplique un evento append-only."""
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_EVT_COLS} FROM preservacion_evento "
            "WHERE object_ref = $1 AND tipo = $2 AND detalle->>'dedup_key' = $3 "
            "ORDER BY ts ASC LIMIT 1",
            object_ref,
            tipo,
            dedup_key,
        )

    async def insert_aip(self, data: dict) -> asyncpg.Record:
        """Inserta el AIP real (E10 Increment B).

        `id` se genera en la capa de servicio (no se deja el `DEFAULT
        gen_random_uuid()` de la tabla) porque `object_key` del ZIP subido a
        MinIO se deriva de ese mismo id — necesita conocerse ANTES del INSERT.

        `bagit` (columna legacy del stub del Increment A, ahora NULLABLE tras
        la migración 007) deliberadamente NO se escribe: el Bag real es un ZIP
        binario que vive en el bucket WORM, no un texto en esta tabla.
        """
        await self._sp()
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO aip (id, expediente_id, premis_xml, aip_sha256,
                             indice_sha256, num_documentos, bucket, object_key,
                             aip_bytes, formato, retain_until, retention_mode,
                             legal_hold)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            RETURNING {_AIP_COLS}
            """,
            data["id"],
            data["expediente_id"],
            data["premis_xml"],
            data["aip_sha256"],
            data.get("indice_sha256"),
            data["num_documentos"],
            data["bucket"],
            data["object_key"],
            data["aip_bytes"],
            data["formato"],
            data["retain_until"],
            data["retention_mode"],
            data["legal_hold"],
        )
        assert row is not None
        return row

    async def latest_aip(self, expediente_id: str) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_AIP_COLS} FROM aip "
            "WHERE expediente_id = $1 ORDER BY created_at DESC LIMIT 1",
            expediente_id,
        )

    async def get_aip_by_expediente_and_sha256(
        self, expediente_id: str, aip_sha256: str
    ) -> asyncpg.Record | None:
        """Fila `aip` para `(expediente_id, aip_sha256)` EXACTO — respaldo en
        código, bajo el advisory lock de sesión, del índice único parcial
        `ux_preservacion_worm_objeto_expediente_aip_sha256` (migración 012,
        sobre `preservacion_worm_objeto`, no sobre esta tabla).

        `AipService.empaquetar_aip` la consulta DESPUÉS de construir el Bag
        (solo entonces se conoce el `aip_sha256` real del ZIP completo) y
        ANTES de `put_object` — remediación de auditoría (Media, cierre de la
        carrera de doble-empaquetado): si ya existe una fila con el MISMO
        contenido para este expediente, la llamada es idempotente (corte-
        circuito, sin re-subir nada al bucket WORM), mismo criterio que
        `PreservacionWormRepository.get_by_expediente_and_tipo` para
        `proteger_indice` — pero contra `aip` (que sí tiene todas las
        columnas necesarias para reconstruir `AipResponse` directamente,
        a diferencia de `preservacion_worm_objeto`, que no persiste
        `premis_xml`/`num_documentos`/etc.). Contenido DISTINTO (`aip_sha256`
        distinto) no encuentra nada aquí y procede a un nuevo empaquetado
        legítimo (ver migración 012: el AIP admite N versiones por
        expediente)."""
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_AIP_COLS} FROM aip WHERE expediente_id = $1 AND aip_sha256 = $2 "
            "ORDER BY created_at DESC LIMIT 1",
            expediente_id,
            aip_sha256,
        )

    async def list_eventos(self, object_ref: str | list[str] | None) -> list[asyncpg.Record]:
        """Eventos de UNO o VARIOS `object_ref` (AREA-07-15), orden cronológico. Sin
        `object_ref`: los 200 más recientes del tenant (variante interna)."""
        await self._sp()
        if object_ref is not None:
            refs = [object_ref] if isinstance(object_ref, str) else list(object_ref)
            return list(
                await self._conn.fetch(
                    f"SELECT {_EVT_COLS} FROM preservacion_evento "
                    "WHERE object_ref = ANY($1::text[]) ORDER BY ts ASC",
                    refs,
                )
            )
        return list(
            await self._conn.fetch(
                f"SELECT {_EVT_COLS} FROM preservacion_evento ORDER BY ts DESC LIMIT 200"
            )
        )
