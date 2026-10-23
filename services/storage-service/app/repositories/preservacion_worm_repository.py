# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Repositorio de objetos protegidos bajo WORM real (E10 Increment A).

``preservacion_worm_objeto`` es la constancia de que una copia inmutable (MinIO
Object Lock) de un artefacto (índice electrónico firmado, acta de transferencia)
fue subida con una retención derivada de la TRD. Alimenta el gate de disposición
final (``StorageService.purge_for_disposition``): mientras exista una fila activa
(``legal_hold``, o ``retain_until`` futuro para un ``tipo`` de CONTENIDO) para un
expediente, su purga física queda bloqueada. Los tipos de INSTRUMENTO DE CONTROL
(``app/core/preservacion_tipos.CONTROL_INSTRUMENT_TIPOS``) quedan excluidos del
bloqueo por retención — no del bloqueo por ``legal_hold``.

search_path al tenant en cada método (gemelo de ``PreservacionRepository``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg
from orpycamcp_common.tenant import tenant_schema

from app.core.preservacion_tipos import MULTI_ARTEFACTO_TIPOS

_COLS = (
    "id, expediente_id, tipo, bucket, object_key, version_id, sha256, "
    "retain_until, retention_mode, legal_hold, created_at, file_id, "
    # `objeto_tipo` (migración 014) dice DE QUÉ es `expediente_id`: de un
    # expediente o de un lote de eliminación. Va en todas las lecturas porque
    # el gate de purga lo exige y las vistas lo muestran.
    "objeto_tipo, "
    # E10 debt (renovación de retención WORM para series CT, migración 011):
    # incluidas en TODAS las lecturas (no solo un subconjunto dedicado, a
    # diferencia de `_WORM_COLS` en archive-service) porque tanto el camino
    # de protección (`get_by_expediente_and_tipo`, idempotencia) como el de
    # renovación (`renovar`) necesitan ver/actualizar el mismo par de
    # columnas -- no ameritan una constante separada.
    "renovado_at, renovaciones, "
    # C-5/C7 (migración 024): por qué `retain_until` es o no NULL
    # (calendario | valoracion_pendiente | valorada_sin_retencion).
    "retencion_origen"
)

# Las mismas columnas calificadas con el alias ``p`` (parte recursiva de ``cadena_descendiente``).
_COLS_P = ", ".join("p." + c.strip() for c in _COLS.split(","))


class PreservacionWormRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        # Antes NO validaba el slug (los gemelos sí): `tenant_schema` lanza ValueError.
        self._schema = tenant_schema(tenant_slug)

    async def _sp(self) -> None:
        await self._conn.execute(f'SET search_path = "{self._schema}"')

    async def insert(
        self,
        *,
        expediente_id: str,
        tipo: str,
        bucket: str,
        object_key: str,
        version_id: str | None,
        sha256: str,
        retain_until: datetime | None,
        retention_mode: str | None,
        legal_hold: bool,
        file_id: uuid.UUID | None = None,
        objeto_tipo: str = "expediente",
        renueva_a: uuid.UUID | None = None,
        retencion_origen: str = "calendario",
    ) -> asyncpg.Record:
        """`retain_until`/`retention_mode` solo son None con `retencion_origen` distinto de
        `calendario` (CHECK `ck_pwo_retencion_coherente`, migración 024)."""
        await self._sp()
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO preservacion_worm_objeto
                (expediente_id, tipo, bucket, object_key, version_id, sha256,
                 retain_until, retention_mode, legal_hold, file_id, objeto_tipo, renueva_a,
                 retencion_origen)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            RETURNING {_COLS}
            """,
            expediente_id,
            tipo,
            bucket,
            object_key,
            version_id,
            sha256,
            retain_until,
            retention_mode,
            legal_hold,
            file_id,
            objeto_tipo,
            renueva_a,
            retencion_origen,
        )
        assert row is not None
        return row

    async def get_by_file_id(self, file_id: uuid.UUID) -> asyncpg.Record | None:
        """La fila WORM más reciente de un objeto, sea cual sea su ``tipo`` (B10 §8: la
        renovación de una firma LTA parte del ``file_id`` del XML anterior, no de un expediente)."""
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM preservacion_worm_objeto "
            "WHERE file_id = $1 ORDER BY created_at DESC LIMIT 1",
            file_id,
        )

    async def cadena_descendiente(self, worm_id: uuid.UUID) -> list[asyncpg.Record]:
        """Las filas que RENUEVAN a ``worm_id`` (``renueva_a``), directa o transitivamente (B10
        §8): los XML de una firma LTA con el sello de archivo renovado. Del más cercano al más
        lejano; la propia fila ``worm_id`` no se incluye."""
        await self._sp()
        return list(
            await self._conn.fetch(
                f"""
                WITH RECURSIVE cadena AS (
                    SELECT {_COLS}, 1 AS nivel FROM preservacion_worm_objeto WHERE renueva_a = $1
                    UNION ALL
                    SELECT {_COLS_P}, c.nivel + 1
                      FROM preservacion_worm_objeto p JOIN cadena c ON p.renueva_a = c.id
                )
                SELECT {_COLS} FROM cadena ORDER BY nivel, created_at
                """,
                worm_id,
            )
        )

    async def retain_until_de_la_raiz(self, worm_id: uuid.UUID) -> datetime | None:
        """``retain_until`` de la RAÍZ de la cadena ``renueva_a`` de ``worm_id`` (la fila que no
        renueva a nadie). Una renovación hereda el MAYOR entre el anterior y la raíz: el
        anterior puede haberse quedado atrás si la raíz se extendió después (series CT)."""
        await self._sp()
        return await self._conn.fetchval(
            """
            WITH RECURSIVE subida AS (
                SELECT id, renueva_a, retain_until FROM preservacion_worm_objeto WHERE id = $1
                UNION ALL
                SELECT p.id, p.renueva_a, p.retain_until
                  FROM preservacion_worm_objeto p JOIN subida s ON p.id = s.renueva_a
            )
            SELECT max(retain_until) FROM subida
            """,
            worm_id,
        )

    async def get_by_expediente_and_tipo(
        self, expediente_id: str, tipo: str, file_id: uuid.UUID | None = None
    ) -> asyncpg.Record | None:
        """Cualquier fila (activa o ya expirada) registrada para
        ``(expediente_id, tipo)`` — respaldo en código del índice único parcial
        ``ux_preservacion_worm_objeto_expediente_indice`` (migración 009,
        `tipo='indice'` solamente): `PreservationWormService.proteger_indice`
        la consulta ANTES de tocar MinIO para que una segunda llamada al mismo
        expediente (reintento tras timeout, o carrera intento-síncrono vs.
        barrida de reconciliación) sea idempotente (200, sin re-subir) en vez
        de fallar con `UniqueViolationError` a mitad de un `put_object`
        COMPLIANCE ya irreversible.

        Tipos multi-artefacto (FIRMA-2, `MULTI_ARTEFACTO_TIPOS`): hay N objetos por
        ``(expediente_id, tipo)`` —una cadena de constancias y revocaciones—, así que la
        identidad es ``(expediente_id, tipo, file_id)`` y ``file_id`` es OBLIGATORIO. Buscar
        solo por tipo y quedarse con la más reciente haría «idempotente» a la segunda
        constancia (sin Object-Lock) y la renovación extendería únicamente la última."""
        await self._sp()
        if tipo in MULTI_ARTEFACTO_TIPOS:
            if file_id is None:
                raise ValueError(f"tipo={tipo} es multi-artefacto: `file_id` es obligatorio")
            return await self._conn.fetchrow(
                f"SELECT {_COLS} FROM preservacion_worm_objeto "
                "WHERE expediente_id = $1 AND tipo = $2 AND file_id = $3 "
                "ORDER BY created_at DESC LIMIT 1",
                expediente_id,
                tipo,
                file_id,
            )
        if tipo == "aip":
            # C-6 (H4): el AIP se VERSIONA (cada versión es una fila `aip` que `renueva_a` la
            # anterior), así que «la más reciente» ya no es la identidad de retención: la
            # renovación extendería la nueva y la cascada (`cadena_descendiente`, que DESCIENDE
            # por `renueva_a`) nunca alcanzaría a la anterior, que vencería. La identidad es la
            # RAÍZ de la cadena (`ux_pwo_aip_raiz` garantiza que hay una sola por expediente).
            return await self._conn.fetchrow(
                f"SELECT {_COLS} FROM preservacion_worm_objeto "
                "WHERE expediente_id = $1 AND tipo = $2 AND renueva_a IS NULL "
                "ORDER BY created_at ASC LIMIT 1",
                expediente_id,
                tipo,
            )
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM preservacion_worm_objeto "
            "WHERE expediente_id = $1 AND tipo = $2 "
            "ORDER BY created_at DESC LIMIT 1",
            expediente_id,
            tipo,
        )

    async def get_aip_por_sha256(
        self, expediente_id: str, sha256: str
    ) -> asyncpg.Record | None:
        """La fila WORM de UNA versión concreta del AIP (identificada por la huella de su ZIP,
        `ux_preservacion_worm_objeto_expediente_aip_sha256`, migración 012)."""
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM preservacion_worm_objeto "
            "WHERE expediente_id = $1 AND tipo = 'aip' AND sha256 = $2 LIMIT 1",
            expediente_id,
            sha256,
        )

    async def retain_until_maximo_aip(self, expediente_id: str) -> datetime | None:
        """El MAYOR `retain_until` de toda la cadena de AIP de un expediente (C-6): una versión
        nueva nunca puede vencer antes que ninguna de las anteriores."""
        await self._sp()
        return await self._conn.fetchval(
            "SELECT max(retain_until) FROM preservacion_worm_objeto "
            "WHERE expediente_id = $1 AND tipo = 'aip'",
            expediente_id,
        )

    async def cadena_ascendente(self, worm_id: uuid.UUID) -> list[asyncpg.Record]:
        """La fila ``worm_id`` y todas las que renueva (``renueva_a``), de la más cercana a la
        RAÍZ (la que no renueva a nadie, última de la lista)."""
        await self._sp()
        return list(
            await self._conn.fetch(
                f"""
                WITH RECURSIVE subida AS (
                    SELECT {_COLS}, renueva_a, 1 AS nivel FROM preservacion_worm_objeto WHERE id = $1
                    UNION ALL
                    SELECT {_COLS_P}, p.renueva_a, s.nivel + 1
                      FROM preservacion_worm_objeto p JOIN subida s ON p.id = s.renueva_a
                )
                SELECT {_COLS} FROM subida ORDER BY nivel
                """,
                worm_id,
            )
        )

    async def active_lock_for_expediente(
        self,
        expediente_id: str,
        exclude_retention_tipos: tuple[str, ...] = (),
    ) -> asyncpg.Record | None:
        """Devuelve una fila que SIGUE bloqueando la disposición, si existe.

        "Activa" = ``legal_hold = true`` (freeze global incondicional, sea cual
        sea ``tipo``: litigio/investigación, Ac. 060/2001 — nunca tiene bypass)
        O ``retain_until`` en el futuro para un ``tipo`` que NO esté en
        ``exclude_retention_tipos``. Por defecto (``()``) el comportamiento es
        all-or-nothing, igual que antes de E10 Inc.2 remedio; el gate de
        ``purge_for_disposition`` pasa ``CONTROL_INSTRUMENT_TIPOS`` para que la
        retención del acta/FUID (instrumento de control) no postergue la
        disposición del contenido (``aip``/``indice``). Fail-closed: un
        ``tipo`` no listado en ``exclude_retention_tipos`` sigue bloqueando.

        Su ausencia NO prueba que el expediente nunca tuvo protección WORM,
        solo que ya expiró (o nunca se protegió) para los tipos considerados.
        """
        await self._sp()
        # C-5/C7: `retain_until` puede ser NULL (retención diferida). `NULL > now()` es NULL, no
        # verdadero, así que esa condición sola NO bloquea; lo que bloquea una fila diferida es su
        # `legal_hold` (CHECK `ck_pwo_diferida_exige_hold`). Los holds SIN objetos los ve
        # :meth:`hold_activo_del_expediente`.
        return await self._conn.fetchrow(
            f"""
            SELECT {_COLS} FROM preservacion_worm_objeto
            WHERE expediente_id = $1
              -- `objeto_tipo` explícito (migración 014): sin él, una fila cuyo
              -- `expediente_id` guarda el id de un LOTE podría casar aquí y
              -- decidir sobre la purga de un expediente. Hoy los identificadores
              -- de ambos espacios no coinciden en la práctica, pero «en la
              -- práctica» no es un control.
              AND objeto_tipo = 'expediente'
              AND (legal_hold OR (retain_until > now() AND tipo <> ALL($2::text[])))
            LIMIT 1
            """,
            expediente_id,
            list(exclude_retention_tipos),
        )

    async def renovar(
        self, worm_id: uuid.UUID, *, retain_until: datetime
    ) -> asyncpg.Record | None:
        """Extiende (E10 debt: renovación de retención WORM para series CT,
        migración 011) el `retain_until` de una fila YA protegida.

        El `WHERE retain_until < $2` es la MISMA defensa monotónica que
        `PreservationWormService.renovar_retencion` ya aplicó en Python antes
        de llamar aquí (comparación contra la fila leída bajo el advisory
        lock de sesión, ver `_worm_lock_key`) -- se repite a nivel BD porque
        es gratis y cierra cualquier ventana si algún día un llamador nuevo
        invoca este método sin pasar por el servicio. Devuelve ``None`` si el
        `WHERE` no casó ninguna fila (no debería ocurrir bajo el lock de
        sesión; el llamador lo trata como defensa en profundidad, no como
        camino esperado).
        """
        await self._sp()
        return await self._conn.fetchrow(
            f"""
            UPDATE preservacion_worm_objeto
            SET retain_until = $2, renovado_at = now(), renovaciones = renovaciones + 1
            -- `retain_until IS NOT NULL` explícito (C-5/C7): una fila de retención diferida NO se
            -- «renueva» (no hay fecha que extender); su primera retención la fija
            -- `fijar_retencion_primera_vez`. `NULL < $2` ya es NULL, pero no se deja a la lógica ternaria.
            WHERE id = $1 AND retain_until IS NOT NULL AND retain_until < $2
            RETURNING {_COLS}
            """,
            worm_id,
            retain_until,
        )

    async def list_for_expediente(self, expediente_id: str) -> list[asyncpg.Record]:
        await self._sp()
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM preservacion_worm_objeto "
                "WHERE expediente_id = $1 AND objeto_tipo = 'expediente' "
                "ORDER BY created_at DESC",
                expediente_id,
            )
        )


    # -- C-5/C7: retención diferida y registro de holds por motivo -----------

    async def hold_activo_del_expediente(self, expediente_id: str) -> asyncpg.Record | None:
        """Un hold ACTIVO del registro (`preservacion_legal_hold`) del expediente, si hay. Cubre el
        caso de un hold fijado sobre un expediente que aún no tiene objetos WORM (H2): el gate de
        purga no puede depender de que exista una fila en `preservacion_worm_objeto`."""
        await self._sp()
        rows = await self._conn.fetch(
            "SELECT id, motivo_tipo FROM preservacion_legal_hold "
            "WHERE expediente_id = $1 AND levantado_at IS NULL LIMIT 1",
            expediente_id,
        )
        return rows[0] if rows else None

    async def holds_activos(self, expediente_id: str) -> list[str]:
        """`motivo_tipo` de cada hold activo del expediente."""
        await self._sp()
        rows = await self._conn.fetch(
            "SELECT motivo_tipo FROM preservacion_legal_hold "
            "WHERE expediente_id = $1 AND levantado_at IS NULL",
            expediente_id,
        )
        return [r["motivo_tipo"] for r in rows]

    async def pendientes_de_valoracion(self, expediente_id: str) -> list[asyncpg.Record]:
        """Filas de contenido con retención diferida (retain_until NULL, `valoracion_pendiente`)."""
        await self._sp()
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM preservacion_worm_objeto "
                "WHERE expediente_id = $1 AND objeto_tipo = 'expediente' "
                "AND retencion_origen = 'valoracion_pendiente' AND retain_until IS NULL "
                "ORDER BY created_at",
                expediente_id,
            )
        )

    async def fijar_retencion_primera_vez(
        self, worm_id: uuid.UUID, *, retain_until: datetime, retention_mode: str
    ) -> asyncpg.Record | None:
        """`valoracion_pendiente -> calendario`: fija la retención POR PRIMERA VEZ.

        El `WHERE retain_until IS NULL AND retencion_origen='valoracion_pendiente'` es lo que impide
        tocar una fila `calendario` (cuya retención solo se EXTIENDE por `renovar`, nunca se
        reasigna). `None` si no casó (otra llamada ya la fijó)."""
        await self._sp()
        return await self._conn.fetchrow(
            f"""
            UPDATE preservacion_worm_objeto
            SET retain_until = $2, retention_mode = $3, retencion_origen = 'calendario'
            WHERE id = $1 AND retain_until IS NULL AND retencion_origen = 'valoracion_pendiente'
            RETURNING {_COLS}
            """,
            worm_id,
            retain_until,
            retention_mode,
        )

    async def sincronizar_aip_retencion(
        self, *, bucket: str, object_key: str, retain_until: datetime, retention_mode: str
    ) -> None:
        """Refleja en la tabla `aip` (espejo de lectura) la retención recién fijada."""
        await self._sp()
        await self._conn.execute(
            "UPDATE aip SET retain_until = $3, retention_mode = $4 "
            "WHERE bucket = $1 AND object_key = $2 AND retain_until IS NULL",
            bucket,
            object_key,
            retain_until,
            retention_mode,
        )

    async def aplicar_estado_hold(
        self, worm_id: uuid.UUID, *, legal_hold: bool, valorada: bool
    ) -> asyncpg.Record | None:
        """Escribe `legal_hold` de la fila y, si `valorada`, la pasa de `valoracion_pendiente` a
        `valorada_sin_retencion` en el MISMO UPDATE (el CHECK `ck_pwo_diferida_exige_hold` exige
        que no exista una fila diferida sin hold). Único escritor de `legal_hold` después del INSERT."""
        await self._sp()
        return await self._conn.fetchrow(
            f"""
            UPDATE preservacion_worm_objeto
            SET legal_hold = $2,
                retencion_origen = CASE
                    WHEN $3 AND retencion_origen = 'valoracion_pendiente'
                    THEN 'valorada_sin_retencion' ELSE retencion_origen END
            WHERE id = $1
            RETURNING {_COLS}
            """,
            worm_id,
            legal_hold,
            valorada,
        )
