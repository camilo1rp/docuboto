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


class PreservacionWormRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._schema = f"tenant_{tenant_slug.replace('-', '_')}"

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
    ) -> asyncpg.Record:
        await self._sp()
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO preservacion_worm_objeto
                (expediente_id, tipo, bucket, object_key, version_id, sha256,
                 retain_until, retention_mode, legal_hold, file_id, objeto_tipo)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
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
        )
        assert row is not None
        return row

    async def get_by_expediente_and_tipo(
        self, expediente_id: str, tipo: str
    ) -> asyncpg.Record | None:
        """Cualquier fila (activa o ya expirada) registrada para
        ``(expediente_id, tipo)`` — respaldo en código del índice único parcial
        ``ux_preservacion_worm_objeto_expediente_indice`` (migración 009,
        `tipo='indice'` solamente): `PreservationWormService.proteger_indice`
        la consulta ANTES de tocar MinIO para que una segunda llamada al mismo
        expediente (reintento tras timeout, o carrera intento-síncrono vs.
        barrida de reconciliación) sea idempotente (200, sin re-subir) en vez
        de fallar con `UniqueViolationError` a mitad de un `put_object`
        COMPLIANCE ya irreversible."""
        await self._sp()
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
