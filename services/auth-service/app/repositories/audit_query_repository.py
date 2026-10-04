# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Consulta de la auditoría canónica `public.audit_log` (E08, ADR-008).

Solo lectura: el ÚNICO punto de escritura es `orpycamcp_common.audit`. La cadena
es por `tenant_slug` (columna en la tabla, no por schema). Toda consulta filtra
por el tenant del request **y** por el clearance del llamante (no-read-up,
RF-SEG-08): ver `_clearance_clause`.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from app.core.database import tenant_schema
from app.repositories.clearance_repository import MAX_LEVEL
from app.schemas.audit import AuditEntry

__all__ = ["MAX_LEVEL", "AuditQueryRepository"]

_COLS = "id, ts, service, actor, canal, action, object_type, object_ref, payload, hash"
_FILTERABLE = ("action", "object_type", "object_ref", "actor")

# La entrada de auditoría no guarda el nivel de seguridad de su objeto — y no debe:
# el nivel cambia con la reclasificación, así que un valor denormalizado quedaría
# obsoleto justo en el caso peligroso (documento que SUBE de nivel; ver la ventana
# de fuga ya documentada en las copias derivadas). El nivel se resuelve EN VIVO
# contra la tabla dueña del objeto, dentro del mismo schema de tenant.
#
# `object_ref` no es homogéneo entre servicios: para radicados casi siempre es el
# número de radicado, pero workflow-service escribe el UUID en el visto bueno; para
# expedientes es el `code`, salvo el AIP y algunas entradas de índice que escriben
# el UUID. Por eso cada resolución compara contra la identidad natural **y** contra
# el id, y por eso lo que no resuelve queda FUERA (fail-closed): una referencia que
# no podemos clasificar no se puede declarar pública.
_RADICADO_TYPES = ("radicado",)
_EXPEDIENTE_TYPES = ("expediente", "expediente_indice", "expediente_aip", "aip")

# LISTA DE ADMISIÓN, no de denegación — y la diferencia es todo el control.
#
# Enumerar los tipos clasificables y dejar pasar el resto parece equivalente y no lo
# es: `audit_log` la escriben once servicios con ~40 `object_type` distintos, y varios
# de los que NO denotan un radicado llevan su identidad —y su contenido— en el
# `payload`. Ejemplos reales, no hipotéticos: `evento` (dead-letter) serializa el
# sobre íntegro más un bloque `business.tracking_number`; `import` guarda la LISTA
# literal de números importados; `file` guarda el `filename` del anexo, que en un
# SGDEA suele ser el asunto. Con lista de denegación, cada tipo nuevo —o cada
# servicio nuevo— nace VISIBLE y el olvido es silencioso; con lista de admisión nace
# OCULTO y el olvido se ve en pantalla. Se elige el fallo ruidoso.
#
# Criterio para añadir aquí: el tipo no puede referirse a un documento ni a un
# expediente, ni por `object_ref` ni por `payload`. Configuración, identidad y
# estructura orgánica lo cumplen; cualquier cosa que cite un trámite, no. Ante la
# duda, NO se añade: el coste de omitir es una fila que un admin sin habilitación no
# ve; el de admitir de más es la fuga que este recorte existe para cerrar.
_TIPOS_SIN_OBJETO_CLASIFICABLE = (
    # Identidad y autorización
    "usuario",
    "grupo",
    "membresia",
    "permiso_grupo",
    "clearance_grupo",
    "rbac",
    "auth_session",
    "totp",
    "signing_key",
    # Estructura orgánica y ubicación física
    "dependencia",
    "unidad",
    "urd",
    "ubicacion",
    # Instrumentos y catálogos (describen CÓMO se clasifica, no QUÉ se clasificó)
    "trd_serie",
    "tvd_agrupacion",
    "tipo_documental",
    "metadata_template",
    "metadata_element",
    "catalogo_item",
    "parametro",
    "festivo",
    "pinar_plan",
)


class AuditQueryRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn
        self._sources: set[str] | None = None

    async def _available_sources(self, schema: str) -> set[str]:
        """Tablas dueñas del nivel de seguridad presentes en el schema del tenant.

        `radicados` (document-service) y `expedientes` (archive-service) viven en el
        mismo schema de tenant que las tablas de auth, pero las crean las migraciones
        de OTROS servicios. Si un tenant se aprovisionó sin ellas, referenciarlas
        reventaría la consulta con un 500; resolverlo aquí convierte ese caso en
        fail-closed (las entradas de ese tipo simplemente no se listan).

        El schema llega del `tenant_slug` del request, NO de `current_schema()`: un
        `SET search_path` a un schema inexistente no falla, deja `current_schema()`
        en `public`, y el guard pasaría a describir un schema que no es el del
        llamante. Las subconsultas cualifican con este mismo nombre, así que guard y
        consulta no pueden divergir.
        """
        if self._sources is None:
            rows = await self._conn.fetch(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = $1
                  AND c.relkind IN ('r', 'p', 'v', 'm')
                  AND c.relname = ANY($2::text[])
                """,
                schema,
                ["radicados", "expedientes", "expediente_indice"],
            )
            self._sources = {r["relname"] for r in rows}
        return self._sources

    async def _clearance_clause(
        self, max_level: int, next_idx: int, *, schema: str
    ) -> tuple[str, list[Any]]:
        """Recorte no-read-up del listado (RF-SEG-08).

        Devuelve `("", [])` cuando el llamante alcanza el nivel máximo: ver la
        auditoría completa es precisamente lo que su clearance autoriza, y recortar
        ahí rompería la utilidad probatoria del registro sin ganar nada.

        Para el resto, una entrada se lista solo si (a) su `object_type` está en la
        lista de admisión —tipos que no pueden referirse a un documento ni a un
        expediente— o (b) su objeto resuelve a un `nivel_seguridad <= max_level`.
        Nótese el coste asumido: la resolución por `id::text` no es indexable, así
        que cada página de un llamante no habilitado paga un recorrido. Se acepta
        porque el caso común —clearance máximo— no ejecuta esta cláusula.
        """
        if max_level >= MAX_LEVEL:
            return "", []

        params: list[Any] = []

        def _p(value: Any) -> str:
            params.append(value)
            return f"${next_idx + len(params) - 1}"

        # Punto de partida: NADA se lista. Las ramas de abajo son las únicas que
        # readmiten filas — los tipos de la lista de admisión, y los clasificables
        # que resuelven a un nivel alcanzable. Todo lo demás queda fuera.
        branches = [f"al.object_type = ANY({_p(list(_TIPOS_SIN_OBJETO_CLASIFICABLE))}::text[])"]

        # Si la tabla fuente no existe en el schema, su rama NO se emite: las
        # entradas de esos tipos quedan fuera. Un nivel que no hay dónde consultar
        # no se presume público.
        sources = await self._available_sources(schema)

        # `schema` viene de `tenant_schema()`, que solo devuelve nombres validados
        # contra el validador único (`orpycamcp_common.tenant`) — interpolarlo es seguro y elimina la
        # dependencia del `search_path` de la conexión.
        if "radicados" in sources:
            branches.append(
                f"""EXISTS (
                    SELECT 1 FROM "{schema}".radicados r
                    WHERE al.object_type = ANY({_p(list(_RADICADO_TYPES))}::text[])
                      AND (r.tracking_number = al.object_ref OR r.id::text = al.object_ref)
                      AND r.nivel_seguridad <= {_p(max_level)}
                )"""
            )

        if "expedientes" in sources:
            branches.append(
                f"""EXISTS (
                    SELECT 1 FROM "{schema}".expedientes e
                    WHERE al.object_type = ANY({_p(list(_EXPEDIENTE_TYPES))}::text[])
                      AND (e.code = al.object_ref OR e.id::text = al.object_ref)
                      AND e.nivel_seguridad <= {_p(max_level)}
                )"""
            )

        return "(" + " OR ".join(branches) + ")", params

    async def _object_ref_cond(self, schema: str, n: int) -> str:
        """Condición del filtro `object_ref` = `$n`, tolerante a las tres formas con que
        un asiento nombra el MISMO objeto (AREA-03-07).

        Los asientos viejos de un expediente llevan su UUID, los de un índice el UUID
        del índice y los nuevos el código; un `=` exacto perdía el resto, y el
        historial de un expediente salía incompleto. El histórico no se reescribe: el
        filtro lo resuelve en lectura. Se amplía por subconsulta (sin viaje extra) y
        solo contra las tablas presentes; el recorte no-read-up se aplica DESPUÉS
        sobre la misma fila, así que ensanchar la referencia no ensancha el acceso.
        """
        sources = await self._available_sources(schema)
        ramas = [f"al.object_ref = ${n}"]
        if "expedientes" in sources:
            ramas.append(
                f'al.object_ref IN (SELECT e.id::text FROM "{schema}".expedientes e '
                f"WHERE e.code = ${n})"
            )
            if "expediente_indice" in sources:
                ramas.append(
                    f'al.object_ref IN (SELECT i.id::text FROM "{schema}".expediente_indice i '
                    f'JOIN "{schema}".expedientes e ON e.id = i.expediente_id '
                    f"WHERE e.code = ${n})"
                )
        if "radicados" in sources:
            ramas.append(
                f'al.object_ref IN (SELECT r.id::text FROM "{schema}".radicados r '
                f"WHERE r.tracking_number = ${n})"
            )
        return "(" + " OR ".join(ramas) + ")"

    async def _where(
        self, tenant_slug: str, filters: dict[str, str | None], *, max_level: int
    ) -> tuple[str, list[Any]]:
        conds = ["al.tenant_slug = $1"]
        params: list[Any] = [tenant_slug]
        idx = 2
        schema = tenant_schema(tenant_slug)
        for col in _FILTERABLE:
            val = filters.get(col)
            if val is None:
                continue
            if col == "object_ref":
                conds.append(await self._object_ref_cond(schema, idx))
            else:
                conds.append(f"al.{col} = ${idx}")
            params.append(val)
            idx += 1
        clause, clause_params = await self._clearance_clause(max_level, idx, schema=schema)
        if clause:
            conds.append(clause)
            params.extend(clause_params)
        return " AND ".join(conds), params

    async def count(
        self, tenant_slug: str, filters: dict[str, str | None], *, max_level: int
    ) -> int:
        where, params = await self._where(tenant_slug, filters, max_level=max_level)
        total: int = await self._conn.fetchval(
            f"SELECT COUNT(*) FROM public.audit_log al WHERE {where}", *params
        )
        return total or 0

    async def list(
        self,
        tenant_slug: str,
        filters: dict[str, str | None],
        *,
        max_level: int,
        limit: int,
        offset: int,
    ) -> list[AuditEntry]:
        where, params = await self._where(tenant_slug, filters, max_level=max_level)
        idx = len(params) + 1
        rows = await self._conn.fetch(
            f"SELECT {_COLS} FROM public.audit_log al WHERE {where} "
            f"ORDER BY al.ts DESC, al.id DESC LIMIT ${idx} OFFSET ${idx + 1}",
            *params,
            limit,
            offset,
        )
        return [_to_entry(r) for r in rows]


def _to_entry(row: asyncpg.Record) -> AuditEntry:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return AuditEntry(
        id=row["id"],
        ts=row["ts"],
        service=row["service"],
        actor=row["actor"],
        canal=row["canal"],
        action=row["action"],
        object_type=row["object_type"],
        object_ref=row["object_ref"],
        payload=payload,
        hash=row["hash"],
    )
