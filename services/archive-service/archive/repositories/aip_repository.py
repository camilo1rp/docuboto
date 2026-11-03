# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Repositorio del estado de reconciliación del AIP (E10 debt, cableado del AIP
OAIS al cierre del expediente, migración 033).

La conexión llega con search_path al tenant (mismo contrato que
`IndexRepository`). `expediente_aip` es un ciclo INDEPENDIENTE del índice
electrónico (`expediente_indice`): una fila por expediente, creada
perezosamente en el primer intento de empaquetado.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg

_COLS = (
    "expediente_id, estado, aip_worm_object_id, empaquetado_at, intentos, "
    "proximo_intento_at, ultimo_error, created_at"
)


class AipRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get_or_create(self, expediente_id: uuid.UUID) -> asyncpg.Record:
        """Get-or-create idempotente de la fila de estado: la primera llamada
        para un `expediente_id` la crea en `estado='pendiente'`
        (`ON CONFLICT DO NOTHING` + re-lectura, nunca lanza si dos intentos
        concurrentes corren esta misma línea a la vez -- el advisory lock
        por-tenant de `run_once_aip` ya serializa la barrida en la práctica,
        esto es defensa en profundidad)."""
        await self._conn.execute(
            "INSERT INTO expediente_aip (expediente_id) VALUES ($1) "
            "ON CONFLICT (expediente_id) DO NOTHING",
            expediente_id,
        )
        row = await self._conn.fetchrow(
            f"SELECT {_COLS} FROM expediente_aip WHERE expediente_id = $1", expediente_id
        )
        assert row is not None
        return row

    async def get_empaquetado(self, expediente_id: uuid.UUID) -> asyncpg.Record | None:
        """Fila de estado del AIP SIN crearla (a diferencia de `get_or_create`): lectura pura
        para el paquete de entrega, que no debe fabricar un `pendiente` al preguntar."""
        return await self._conn.fetchrow(
            f"SELECT {_COLS}, worm_retain_until FROM expediente_aip WHERE expediente_id = $1",
            expediente_id,
        )

    async def get_aip_object_id(self, expediente_id: uuid.UUID) -> uuid.UUID | None:
        """`preservacion_worm_objeto.id` del AIP empaquetado del expediente, o `None`
        (sin fila, o todavía no empaquetado). Lectura plana, sin gate: el llamador ya
        resolvió la visibilidad del expediente (AREA-07-15)."""
        row = await self._conn.fetchrow(
            "SELECT aip_worm_object_id FROM expediente_aip WHERE expediente_id = $1",
            expediente_id,
        )
        return row["aip_worm_object_id"] if row is not None else None

    async def reintentar_manual(
        self, expediente_id: uuid.UUID, *, max_clearance: int
    ) -> asyncpg.Record | None:
        """Devuelve a `pendiente` un AIP `agotado` (AREA-07-14): reintento MANUAL, el único
        camino fuera del job, que solo pasa a `agotado` y nunca vuelve.

        Un solo UPDATE y las dos condiciones EN EL WHERE: `estado = 'agotado'` (un AIP
        `empaquetado` u `omitido` no se re-arma: sería re-sellar un paquete de archivo
        ya inmutable) y el clearance del llamante contra el `nivel_seguridad` VIGENTE
        del expediente (RF-SEG-08: va en la consulta, no en una comprobación previa).
        `None` cuando no aplica por CUALQUIERA de las dos razones, indistinguibles: el
        llamador responde un 404 neutro. `intentos` vuelve a 0 y `proximo_intento_at` a
        `now()` para que la siguiente barrida lo recoja; `ultimo_error` se conserva (lo
        que explica por qué se agotó) y se devuelve como `error_previo` para el asiento."""
        return await self._conn.fetchrow(
            """
            WITH previo AS (
                SELECT ea.expediente_id, ea.intentos, ea.ultimo_error
                  FROM expediente_aip ea
                 WHERE ea.expediente_id = $1
                   AND ea.estado = 'agotado'
                   AND EXISTS (
                       SELECT 1 FROM expedientes e
                        WHERE e.id = ea.expediente_id AND e.nivel_seguridad <= $2
                   )
                   FOR UPDATE
            )
            UPDATE expediente_aip ea
               SET estado = 'pendiente', intentos = 0, proximo_intento_at = now()
              FROM previo
             WHERE ea.expediente_id = previo.expediente_id
            RETURNING ea.expediente_id, ea.estado,
                      previo.intentos AS intentos_previos,
                      previo.ultimo_error AS error_previo
            """,
            expediente_id,
            max_clearance,
        )

    async def mark_empaquetado(
        self,
        expediente_id: uuid.UUID,
        aip_worm_object_id: uuid.UUID,
        *,
        indice_file_id: uuid.UUID,
        indice_xml_sha256: str,
        firma_id: uuid.UUID | None = None,
        worm_retain_until: datetime | None = None,
    ) -> int:
        """Marca el AIP como empaquetado (TERMINAL) e inserta la VERSIÓN 1 en
        `expediente_aip_version` (C-6, ADR-042) en la misma llamada: el llamador la ejecuta
        dentro de su `conn.transaction()`, así que estado, huella del índice embebido y versión
        son un solo acto. Defensa en profundidad (mismo patrón que
        `IndexRepository.mark_worm_protegido`): el `WHERE` excluye filas ya `'empaquetado'` --
        devuelve 0 (y NO inserta versión) si otra llamada concurrente ya ganó la carrera.

        `indice_xml_sha256` es obligatoria: es la identidad de la versión y lo que compara el
        barrido de reempaquetado contra el XML vigente de signature-service.
        `aip_vigente_id` = el AIP recién creado; `aip_worm_object_id` conserva la PRIMERA versión
        (identidad de retención) y no vuelve a cambiar.

        `worm_retain_until` (E10 debt, migración 034) puebla el espejo local del `retain_until`
        que storage-service acaba de confirmar -- patrón `COALESCE`."""
        tag = await self._conn.execute(
            "UPDATE expediente_aip SET estado='empaquetado', aip_worm_object_id=$2, "
            "aip_vigente_id=$2, indice_xml_sha256=$4, "
            "empaquetado_at=now(), worm_retain_until = COALESCE($3, worm_retain_until) "
            "WHERE expediente_id=$1 AND estado <> 'empaquetado'",
            expediente_id,
            aip_worm_object_id,
            worm_retain_until,
            indice_xml_sha256,
        )
        try:
            affected = int(tag.split()[-1])
        except (AttributeError, ValueError, IndexError):
            # Fallback defensivo: solo protege mocks de test cuyo
            # `conn.execute` no configura un valor de retorno realista.
            return 0
        if affected:
            await self.insert_version(
                expediente_id,
                version=1,
                aip_id=aip_worm_object_id,
                indice_file_id=indice_file_id,
                indice_xml_sha256=indice_xml_sha256,
                firma_id=firma_id,
                firma_ordinal=None,
                motivo="ingesta",
                sustituye_a_aip_id=None,
            )
        return affected

    async def insert_version(
        self,
        expediente_id: uuid.UUID,
        *,
        version: int,
        aip_id: uuid.UUID,
        indice_file_id: uuid.UUID,
        indice_xml_sha256: str,
        firma_id: uuid.UUID | None,
        firma_ordinal: int | None,
        motivo: str,
        sustituye_a_aip_id: uuid.UUID | None,
    ) -> None:
        """Inserta una versión del AIP (append-only; trigger de la migración 074). El UNIQUE
        (expediente_id, indice_xml_sha256) hace imposible registrar dos veces el mismo índice."""
        await self._conn.execute(
            "INSERT INTO expediente_aip_version (expediente_id, version, aip_id, indice_file_id, "
            "indice_xml_sha256, firma_id, firma_ordinal, motivo, sustituye_a_aip_id) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            expediente_id,
            version,
            aip_id,
            indice_file_id,
            indice_xml_sha256,
            firma_id,
            firma_ordinal,
            motivo,
            sustituye_a_aip_id,
        )

    async def list_versiones(self, expediente_id: uuid.UUID) -> list[asyncpg.Record]:
        """Historia de versiones del AIP, de la más reciente a la primera (lectura plana: el
        llamador ya resolvió la visibilidad del expediente)."""
        return list(
            await self._conn.fetch(
                "SELECT version, aip_id, sustituye_a_aip_id, motivo, firma_ordinal, created_at "
                "FROM expediente_aip_version WHERE expediente_id = $1 ORDER BY version DESC",
                expediente_id,
            )
        )

    async def get_para_reempaque(self, expediente_id: uuid.UUID) -> asyncpg.Record | None:
        """Estado vigente de la fila (releído BAJO el lock del expediente)."""
        return await self._conn.fetchrow(
            "SELECT expediente_id, estado, aip_worm_object_id, aip_vigente_id, indice_xml_sha256, "
            "reempaque_intentos, reempaque_agotado FROM expediente_aip WHERE expediente_id = $1",
            expediente_id,
        )

    async def candidatos_reempaque(self, limit: int) -> list[asyncpg.Record]:
        """AIP empaquetados cuyo índice vigente tiene `firma_id` y toca revisar (fase 11,
        `run_once_aip_reempaquetado`). Orden por `reempaque_proximo_at ASC NULLS FIRST`: cada
        fila revisada sin cambios avanza su `proximo_at` (`marcar_revisados`), de modo que el
        barrido recorre TODAS en rotación y las sanas no acaparan el lote."""
        return list(
            await self._conn.fetch(
                """
                SELECT ea.expediente_id, e.code AS expediente_code, ea.aip_vigente_id,
                       ea.aip_worm_object_id, ea.indice_xml_sha256, ea.reempaque_intentos,
                       ei.firma_id
                FROM expediente_aip ea
                JOIN expedientes e ON e.id = ea.expediente_id
                JOIN expediente_indice ei ON ei.expediente_id = ea.expediente_id
                 AND ei.estado = 'firmado' AND ei.firma_id IS NOT NULL
                WHERE ea.estado = 'empaquetado'
                  AND ea.reempaque_agotado = FALSE
                  AND (ea.reempaque_proximo_at IS NULL OR ea.reempaque_proximo_at <= now())
                ORDER BY ea.reempaque_proximo_at ASC NULLS FIRST
                LIMIT $1
                """,
                limit,
            )
        )

    async def marcar_revisados(
        self, expediente_ids: list[uuid.UUID], *, proximo_at: datetime
    ) -> None:
        """Filas revisadas SIN cambio: la próxima revisión es `proximo_at` (rotación del barrido)."""
        if not expediente_ids:
            return
        await self._conn.execute(
            "UPDATE expediente_aip SET reempaque_proximo_at = $2 WHERE expediente_id = ANY($1::uuid[])",
            expediente_ids,
            proximo_at,
        )

    async def mark_reempaquetado(
        self,
        expediente_id: uuid.UUID,
        *,
        aip_vigente_id: uuid.UUID,
        indice_xml_sha256: str,
        proximo_at: datetime,
        worm_retain_until: datetime | None,
    ) -> None:
        """Apunta la fila a la versión nueva y pone a cero el backoff de reempaquetado. El
        espejo de retención solo SUBE (`GREATEST`): la retención nunca se acorta."""
        await self._conn.execute(
            "UPDATE expediente_aip SET aip_vigente_id = $2, indice_xml_sha256 = $3, "
            "reempaque_intentos = 0, reempaque_proximo_at = $4, reempaque_ultimo_error = NULL, "
            "reempaque_agotado = FALSE, "
            "worm_retain_until = CASE WHEN $5::timestamptz IS NULL THEN worm_retain_until "
            "ELSE GREATEST(worm_retain_until, $5::timestamptz) END "
            "WHERE expediente_id = $1",
            expediente_id,
            aip_vigente_id,
            indice_xml_sha256,
            proximo_at,
            worm_retain_until,
        )

    async def bump_reempaque_intento(
        self,
        expediente_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotado: bool,
    ) -> None:
        """Intento FALLIDO de reempaquetado. ORTOGONAL al backoff del empaquetado inicial y al de
        renovación WORM: no toca `estado`; el AIP vigente sigue `empaquetado` y conservado."""
        await self._conn.execute(
            "UPDATE expediente_aip SET reempaque_intentos = reempaque_intentos + 1, "
            "reempaque_proximo_at = $2, reempaque_ultimo_error = $3, reempaque_agotado = $4 "
            "WHERE expediente_id = $1",
            expediente_id,
            proximo_at,
            ultimo_error,
            agotado,
        )

    async def mark_omitido(self, expediente_id: uuid.UUID) -> int:
        """Marca el AIP como omitido (expediente 100% físico, TERMINAL). Solo
        transiciona desde 'pendiente' -- un expediente ya 'empaquetado'/
        'agotado' no debe degradarse a 'omitido' por una re-evaluación
        tardía."""
        tag = await self._conn.execute(
            "UPDATE expediente_aip SET estado='omitido' "
            "WHERE expediente_id=$1 AND estado='pendiente'",
            expediente_id,
        )
        try:
            return int(tag.split()[-1])
        except (AttributeError, ValueError, IndexError):
            return 0

    async def bump_intento(
        self,
        expediente_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotado: bool,
    ) -> None:
        """Registra un intento de empaquetado FALLIDO: incrementa `intentos`
        atómicamente en el propio UPDATE y fija `proximo_intento_at`/
        `ultimo_error`/`estado` (`'agotado'` si `agotado`, si no permanece/
        vuelve a `'pendiente'`) -- mismo patrón que `IndexRepository.
        bump_worm_reconcile_intento`."""
        await self._conn.execute(
            "UPDATE expediente_aip SET intentos = intentos + 1, "
            "proximo_intento_at = $2, ultimo_error = $3, "
            "estado = CASE WHEN $4 THEN 'agotado' ELSE 'pendiente' END "
            "WHERE expediente_id = $1",
            expediente_id,
            proximo_at,
            ultimo_error,
            agotado,
        )

    async def list_pending(self, limit: int) -> list[asyncpg.Record]:
        """Expedientes elegibles para empaquetar el AIP (6ª fase de
        reconciliación, `run_once_aip`): `closed`/`transferred`, con índice
        VIGENTE `estado='firmado'` Y `worm_protegido_at IS NOT NULL` (el AIP
        embebe el índice firmado+protegido como `indice_file_id` -- ver el
        diseño ratificado), y sin `expediente_aip` todavía, o con
        `estado='pendiente'` y backoff cumplido.

        `LEFT JOIN expediente_aip`: una fila AUSENTE (nunca intentado) es
        tan elegible como una `'pendiente'` con backoff vencido -- la fila se
        crea perezosamente dentro del propio intento (`get_or_create`), NO
        aquí. Sin `DISTINCT ON`: `estado='firmado'` es TERMINAL por
        expediente (ver `IndexRepository.list_worm_pending`), a lo sumo una
        fila de `expediente_indice` por `expediente_id` llega a ese estado.
        """
        return list(
            await self._conn.fetch(
                """
                SELECT e.id AS expediente_id, e.code AS expediente_code
                FROM expedientes e
                JOIN expediente_indice ei ON ei.expediente_id = e.id
                LEFT JOIN expediente_aip ea ON ea.expediente_id = e.id
                WHERE e.status IN ('closed', 'transferred')
                  AND ei.estado = 'firmado'
                  AND ei.worm_protegido_at IS NOT NULL
                  AND (
                        ea.expediente_id IS NULL
                        OR (
                            ea.estado = 'pendiente'
                            AND (ea.proximo_intento_at IS NULL OR ea.proximo_intento_at <= now())
                        )
                      )
                ORDER BY ea.proximo_intento_at ASC NULLS FIRST
                LIMIT $1
                """,
                limit,
            )
        )

    # -- E10 debt: renovación de retención WORM para series CT -------------
    # (extensión del AIP a la 5ª fase de reconciliación, mismo patrón EXACTO
    # que `IndexRepository`'s -- ver ese repositorio para el diseño completo.)

    async def list_worm_renovacion_pending(
        self, *, ventana_dias: int, ct_codes: list[str], limit: int
    ) -> list[asyncpg.Record]:
        """Filas espejo del AIP CUYA SERIE es de disposición Conservación
        Total (CT, `app.core.disposition.CT_DISPOSITION_CODES`) y cuyo
        `worm_retain_until` vence dentro de `ventana_dias` (E10 debt,
        migración 034) -- 5ª fase de reconciliación, `run_once_worm_renovacion`.

        Solo AIP YA empaquetados (`estado='empaquetado'`), no agotados de
        renovación, con backoff cumplido. El filtro CT vive EN LA CONSULTA
        (`s.disposition = ANY($2)`) -- fail-closed por construcción, mismo
        criterio que `IndexRepository.list_worm_renovacion_pending`."""
        return list(
            await self._conn.fetch(
                """
                SELECT ea.expediente_id, ea.worm_retain_until,
                       ea.worm_renovacion_intentos, e.code AS expediente_code
                FROM expediente_aip ea
                JOIN expedientes e ON e.id = ea.expediente_id
                JOIN trd_series s ON s.id = e.trd_serie_id
                WHERE ea.estado = 'empaquetado'
                  AND ea.worm_renovacion_agotado = FALSE
                  AND ea.worm_retain_until IS NOT NULL
                  AND ea.worm_retain_until < now() + ($1 * INTERVAL '1 day')
                  AND (ea.worm_renovacion_proximo_at IS NULL OR ea.worm_renovacion_proximo_at <= now())
                  AND s.disposition = ANY($2::text[])
                ORDER BY ea.worm_renovacion_proximo_at ASC NULLS FIRST
                LIMIT $3
                """,
                ventana_dias,
                ct_codes,
                limit,
            )
        )

    async def list_worm_renovacion_visibilidad(
        self, *, max_clearance: int, ct_codes: list[str], ventana_dias: int
    ) -> list[asyncpg.Record]:
        """AIP YA empaquetados, de serie CT, cuya renovación está AGOTADA
        (`worm_renovacion_agotado = TRUE`) o EN RIESGO (vence dentro de
        `ventana_dias`) -- visibilidad operacional (E10 debt,
        `GET /expedientes/indices/pendientes-renovacion`), gemela de
        `IndexRepository.list_worm_renovacion_visibilidad` para el AIP.

        No-read-up (RF-SEG-08): filtra por `e.nivel_seguridad <= $max_clearance`.
        Sin paginación SQL propia: `IndexService.list_pendientes_renovacion`
        combina esta lista con las de índice/acta y pagina en memoria."""
        return list(
            await self._conn.fetch(
                """
                SELECT ea.expediente_id, e.code AS expediente_code,
                       ea.worm_retain_until, ea.worm_renovacion_intentos,
                       ea.worm_renovacion_agotado, ea.worm_renovacion_ultimo_error
                FROM expediente_aip ea
                JOIN expedientes e ON e.id = ea.expediente_id
                JOIN trd_series s ON s.id = e.trd_serie_id
                WHERE ea.estado = 'empaquetado'
                  AND s.disposition = ANY($1::text[])
                  AND e.nivel_seguridad <= $2
                  AND (
                        ea.worm_renovacion_agotado = TRUE
                        OR (
                            ea.worm_retain_until IS NOT NULL
                            AND ea.worm_retain_until < now() + ($3 * INTERVAL '1 day')
                        )
                      )
                ORDER BY ea.worm_renovacion_agotado DESC, ea.worm_retain_until ASC NULLS LAST
                """,
                ct_codes,
                max_clearance,
                ventana_dias,
            )
        )

    async def mark_worm_renovado(
        self, expediente_id: uuid.UUID, *, worm_retain_until: datetime
    ) -> None:
        """Actualiza el espejo `worm_retain_until` del AIP tras una llamada
        CONFIRMADA (`outcome.ok=True`) a `renovar-retencion-internal` -- real
        o no-op. Resetea el backoff de renovación -- mismo criterio que
        `IndexRepository.mark_worm_renovado`."""
        await self._conn.execute(
            "UPDATE expediente_aip SET worm_retain_until = $2, "
            "worm_renovacion_intentos = 0, worm_renovacion_proximo_at = NULL, "
            "worm_renovacion_ultimo_error = NULL, worm_renovacion_agotado = FALSE "
            "WHERE expediente_id = $1",
            expediente_id,
            worm_retain_until,
        )

    async def bump_worm_renovacion_intento(
        self,
        expediente_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotado: bool,
    ) -> None:
        """Registra un intento FALLIDO de renovación de retención WORM del
        AIP (E10 debt) -- gemelo de `IndexRepository.
        bump_worm_renovacion_intento`. ORTOGONAL a `bump_intento` (backoff del
        EMPAQUETADO, arriba): no toca `estado`/`intentos`/`proximo_intento_at`
        -- el AIP permanece `estado='empaquetado'` (TERMINAL) mientras este
        eje de renovación cicla en sus propias columnas."""
        await self._conn.execute(
            "UPDATE expediente_aip SET "
            "worm_renovacion_intentos = worm_renovacion_intentos + 1, "
            "worm_renovacion_proximo_at = $2, worm_renovacion_ultimo_error = $3, "
            "worm_renovacion_agotado = $4 WHERE expediente_id = $1",
            expediente_id,
            proximo_at,
            ultimo_error,
            agotado,
        )
