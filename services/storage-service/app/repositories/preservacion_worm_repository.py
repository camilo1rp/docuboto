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
    "renovado_at, renovaciones"
)

# Las mismas columnas calificadas con el alias ``p`` (parte recursiva de ``cadena_descendiente``).
_COLS_P = ", ".join("p." + c.strip() for c in _COLS.split(","))


class PreservacionWormRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        # Antes NO validaba el slug (los gemelos sí): `tenant_schema` lanza ValueError.
        self._schema = tenant_schema(tenant_slug)

    async def _sp(self) -> None:
        await self._conn.execute(f'SET search_path = "{self._schema}", public')

    async def insert(
        self,
        *,
        expediente_id: str,
        tipo: str,
        bucket: str,
        object_key: str,
        version_id: str | None,
        sha256: str,
        retain_until: datetime,
        retention_mode: str,
        legal_hold: bool,
        file_id: uuid.UUID | None = None,
        objeto_tipo: str = "expediente",
        renueva_a: uuid.UUID | None = None,
    ) -> asyncpg.Record:
        await self._sp()
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO preservacion_worm_objeto
                (expediente_id, tipo, bucket, object_key, version_id, sha256,
                 retain_until, retention_mode, legal_hold, file_id, objeto_tipo, renueva_a)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
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
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM preservacion_worm_objeto "
            "WHERE expediente_id = $1 AND tipo = $2 "
            "ORDER BY created_at DESC LIMIT 1",
            expediente_id,
            tipo,
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
            WHERE id = $1 AND retain_until < $2
            RETURNING {_COLS}
            """,
            worm_id,
            retain_until,
        )

    async def set_legal_hold(self, worm_id: uuid.UUID, *, activo: bool) -> asyncpg.Record | None:
        """Fija el `legal_hold` de una fila (AREA-08-07). Único escritor de la
        columna después del INSERT: el acto interno `fijar_legal_hold`."""
        await self._sp()
        return await self._conn.fetchrow(
            f"""
            UPDATE preservacion_worm_objeto
            SET legal_hold = $2
            WHERE id = $1
            RETURNING {_COLS}
            """,
            worm_id,
            activo,
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
