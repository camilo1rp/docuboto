# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Repositorio del índice electrónico (E15, versionado append-only).

La conexión llega con search_path al tenant. `insert_version` escribe la versión
del índice y su snapshot de items atómicamente. La versión vigente es MAX(version).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg

from app.services.index_builder import XSD_VERSION, IndiceItemData

_INDICE_COLS = (
    "id, expediente_id, version, xml, xml_sha256, content_hash, algoritmo, "
    "xsd_version, num_documentos, estado, firma_id, minio_key_firmado, "
    "generado_por, generado_ts"
)

# E10 Inc.1 (migración 029): estado del cableado WORM del índice — ORTOGONAL
# a `estado`/`reconcile_*` de arriba (esas son del SELLADO XAdES-B). Columnas
# aparte en vez de sumarlas a `_INDICE_COLS`: solo `get_by_id`/
# `list_worm_pending` (consumidores del cableado WORM) las necesitan —
# `get_current`/`get_version` (consumidos por rutas de lectura/verify/fixity
# ya probadas) no ganan columnas que no usan.
_WORM_COLS = (
    "worm_protegido_at, worm_object_id, worm_reconcile_intentos, "
    "worm_reconcile_proximo_at, worm_reconcile_ultimo_error, worm_reconcile_agotado"
)


class IndexRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get_current(self, expediente_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_INDICE_COLS} FROM expediente_indice "
            "WHERE expediente_id = $1 ORDER BY version DESC LIMIT 1",
            expediente_id,
        )

    async def get_version(self, expediente_id: uuid.UUID, version: int) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_INDICE_COLS} FROM expediente_indice "
            "WHERE expediente_id = $1 AND version = $2",
            expediente_id,
            version,
        )

    async def list_versions(
        self, expediente_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[list[asyncpg.Record], int]:
        total = await self._conn.fetchval(
            "SELECT COUNT(*) FROM expediente_indice WHERE expediente_id = $1",
            expediente_id,
        )
        rows = await self._conn.fetch(
            f"SELECT {_INDICE_COLS} FROM expediente_indice WHERE expediente_id = $1 "
            "ORDER BY version DESC LIMIT $2 OFFSET $3",
            expediente_id,
            limit,
            offset,
        )
        return list(rows), total or 0

    async def mark_signed(
        self,
        indice_id: uuid.UUID,
        firma_id: uuid.UUID,
        minio_key_firmado: str | None = None,
    ) -> int:
        """Marca la versión del índice como firmada e inmutable (E06 Inc.1).

        Defensa en profundidad: el `WHERE` excluye explícitamente filas ya
        `'firmado'` — un índice sellado es inmutable y no debe re-escribirse
        (aunque `IndexService.firmar_indice`/`firmar_indice_close` ya evitan
        llamar aquí dos veces sobre el mismo índice, esta cláusula lo
        garantiza también a nivel BD).

        Resetea el backoff de reconciliación (residual Ac. AGN 001/2024 art.
        4.3.2.4, migración 025): un índice recién firmado ya no tiene NADA
        pendiente que reconciliar — sin este reset, un índice que hubiera
        acumulado intentos antes de firmarse (p. ej. sellado manual mientras
        el job ya lo había reintentado un par de veces) dejaría basura
        (`reconcile_intentos`/`reconcile_ultimo_error` obsoletos) visible en
        `GET /expedientes/indices/pendientes-firma` si alguna vez volviera a
        degradarse en una versión FUTURA del índice (nueva fila, pero un
        operador leyendo el histórico podría confundirse).

        Devuelve el número de filas afectadas (0 o 1, parseado del status
        tag `"UPDATE n"` de asyncpg) — el job de reconciliación
        (`app/jobs/indice_reconciliation.py::_reconciliar_indice`) lo usa
        para distinguir "yo firmé esta fila" (1) de "perdí la carrera, otra
        conexión ya la había firmado" (0, el `WHERE estado <> 'firmado'` no
        casó ninguna fila) — en el segundo caso NO se escribe un asiento de
        auditoría fantasma sobre un efecto que esta llamada no produjo. Los
        demás llamadores (`IndexService.firmar_indice`/`firmar_indice_close`)
        ignoran el valor de retorno sin efecto — no rompe su comportamiento.
        """
        tag = await self._conn.execute(
            "UPDATE expediente_indice SET estado='firmado', firma_id=$2, "
            "minio_key_firmado=$3, reconcile_intentos=0, reconcile_agotado=FALSE, "
            "reconcile_proximo_at=NULL, reconcile_ultimo_error=NULL "
            "WHERE id=$1 AND estado <> 'firmado'",
            indice_id,
            firma_id,
            minio_key_firmado,
        )
        try:
            return int(tag.split()[-1])
        except (AttributeError, ValueError, IndexError):
            # asyncpg SIEMPRE devuelve una etiqueta de estado real
            # ("UPDATE 0"/"UPDATE 1") — este fallback SOLO protege contra
            # mocks de test cuyo `conn.execute` no configura un valor de
            # retorno realista (default `None` en `tests/conftest.py::
            # _make_mock_conn`, usado por decenas de tests que nunca antes
            # necesitaron que `mark_signed` devolviera nada). Fail-safe: 0
            # filas afectadas — el job de reconciliación simplemente no
            # auditará un efecto que no puede confirmar, nunca una excepción
            # sin capturar por un detalle de mock ajeno a lo que ese test
            # quería ejercitar.
            return 0

    async def mark_pendiente(self, indice_id: uuid.UUID, *, motivo: str) -> None:
        """Marca la versión como 'pendiente_firma' (E06 Inc.1, RT-16).

        Decisión del usuario: si el sellado XAdES-B falla, el índice queda en
        este estado (en vez de 'vigente' silencioso, que ocultaba el fallo) y
        el cierre del expediente NO se bloquea. Reintentable e idempotente vía
        `POST /expedientes/{id}/indice/firmar`.

        Defensa en profundidad: el `WHERE` excluye explícitamente filas ya
        `'firmado'` — un índice sellado es inmutable y NUNCA debe degradarse
        a `'pendiente_firma'` (protege la misma invariante que la migración
        015 / `regenerate`, aunque los llamadores actuales solo invocan esto
        sobre índices no firmados).

        `motivo` (residual Ac. AGN 001/2024 art. 4.3.2.4, migración 025) se
        persiste en `reconcile_ultimo_error` y `reconcile_proximo_at` se fija
        a `now()` — TODA degradación queda de inmediato elegible para
        `IndexRepository.list_pending_signature` (el job de reconciliación no
        tiene que esperar un primer `bump_intento` para recogerla) y visible
        con su motivo en `GET /expedientes/indices/pendientes-firma` incluso
        antes de que el job la toque.

        Re-arma el backoff (`reconcile_agotado=FALSE, reconcile_intentos=0`,
        E06/Ac. AGN 001/2024 art. 4.3.2.4, hallazgo C1): esta función SOLO la
        invoca `IndexService` (nunca el job — el job degrada vía
        `bump_intento`, que sí incrementa/agota), es decir, cada llamada aquí
        es una degradación humana-adyacente (cierre nuevo del expediente, o
        reintento manual `POST /indice/firmar`). Si un índice había quedado
        `reconcile_agotado=TRUE` (excluido del job) y un archivista corrige la
        causa raíz y reintenta manualmente, ese reintento debe volver a hacer
        al índice elegible para la convergencia automática aunque el propio
        intento manual también falle — sin este reset, un retry manual fallido
        dejaba el índice agotado E inalcanzable tanto para el job como
        (aparentemente) para el operador, hasta un segundo retry manual con
        ÉXITO.
        """
        await self._conn.execute(
            "UPDATE expediente_indice SET estado='pendiente_firma', "
            "reconcile_ultimo_error=$2, reconcile_proximo_at=now(), "
            "reconcile_agotado=FALSE, reconcile_intentos=0 "
            "WHERE id=$1 AND estado <> 'firmado'",
            indice_id,
            motivo,
        )

    async def list_pending_signature(self, limit: int) -> list[asyncpg.Record]:
        """Índices 'pendiente_firma' elegibles para el job de reconciliación
        (residual Ac. AGN 001/2024 art. 4.3.2.4, `app/jobs/indice_reconciliation.py`).

        Solo la versión VIGENTE (`DISTINCT ON (expediente_id) ... ORDER BY
        version DESC`, el índice es append-only) de expedientes `closed`/
        `transferred` — un índice `pendiente_firma` de un expediente todavía
        `open` no puede ocurrir en la práctica (el sellado solo se intenta al
        cierre), pero el filtro por `status` es defensa en profundidad, no
        una suposición. Excluye `reconcile_agotado=TRUE` (requiere
        intervención manual, el job ya no debe malgastar cupo HTTP en ellos)
        y respeta el backoff (`reconcile_proximo_at IS NULL OR <= now()`).

        Orden `reconcile_proximo_at NULLS FIRST`: los recién degradados
        (elegibles de inmediato, ver `mark_pendiente`) se procesan antes que
        los que ya vienen arrastrando backoff — sin este orden, un índice con
        `reconcile_proximo_at` muy en el pasado (tras varios ciclos de
        `bump_intento`) podría acaparar el batch indefinidamente frente a uno
        recién llegado si ambos entraran por PK/insert order.
        """
        return list(
            await self._conn.fetch(
                """
                SELECT vigente.id, vigente.expediente_id, vigente.version, vigente.xml,
                       vigente.reconcile_intentos, vigente.reconcile_agotado,
                       vigente.reconcile_ultimo_error, e.code AS expediente_code
                FROM (
                    SELECT DISTINCT ON (expediente_id) *
                    FROM expediente_indice
                    ORDER BY expediente_id, version DESC
                ) vigente
                JOIN expedientes e ON e.id = vigente.expediente_id
                WHERE vigente.estado = 'pendiente_firma'
                  AND e.status IN ('closed', 'transferred')
                  AND vigente.reconcile_agotado = FALSE
                  AND (vigente.reconcile_proximo_at IS NULL OR vigente.reconcile_proximo_at <= now())
                ORDER BY vigente.reconcile_proximo_at ASC NULLS FIRST
                LIMIT $1
                """,
                limit,
            )
        )

    async def bump_intento(
        self,
        indice_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotado: bool,
    ) -> None:
        """Registra un intento de sellado FALLIDO del job de reconciliación
        (residual Ac. AGN 001/2024 art. 4.3.2.4): incrementa
        `reconcile_intentos` atómicamente en el propio UPDATE (sin lectura
        previa — evita una carrera lectura-modificación-escritura si dos
        instancias del job procesaran el mismo índice, aunque el advisory
        lock por-tenant del job ya debería impedirlo en la práctica) y fija
        `reconcile_proximo_at`/`reconcile_ultimo_error`/`reconcile_agotado`
        con los valores que decidió el llamador.

        `proximo_at=None` es válido para la fila `agotado=True` (ya no hay
        "próximo intento" que programar — permanece `pendiente_firma`
        indefinidamente hasta intervención manual).
        """
        await self._conn.execute(
            "UPDATE expediente_indice SET reconcile_intentos = reconcile_intentos + 1, "
            "reconcile_proximo_at = $2, reconcile_ultimo_error = $3, reconcile_agotado = $4 "
            "WHERE id = $1",
            indice_id,
            proximo_at,
            ultimo_error,
            agotado,
        )

    async def list_pending_signature_page(
        self, *, max_clearance: int, offset: int, limit: int
    ) -> tuple[list[asyncpg.Record], int]:
        """Listado operacional PAGINADO de índices 'pendiente_firma' para el
        archivista (residual Ac. AGN 001/2024 art. 4.3.2.4,
        `GET /expedientes/indices/pendientes-firma`) — visibilidad
        complementaria a `audit_log` (ningún índice sin firmar queda
        silenciado).

        No-read-up (RF-SEG-08): filtra por `e.nivel_seguridad <= $max_clearance`,
        mismo criterio que `ArchiveRepository.list_expedientes`. A diferencia
        de `list_pending_signature` (consumida por el job, sin gate de
        clearance — el sistema puede/debe sellar cualquier índice pendiente
        sin importar su nivel), este método SÍ redacta por clearance del
        llamante porque expone metadatos de expedientes clasificados
        (`expediente_code`) a un usuario humano.
        """
        where = (
            "vigente.estado = 'pendiente_firma' "
            "AND e.status IN ('closed', 'transferred') "
            "AND e.nivel_seguridad <= $1"
        )
        total = await self._conn.fetchval(
            f"""
            SELECT COUNT(*)
            FROM (
                SELECT DISTINCT ON (expediente_id) expediente_id, estado
                FROM expediente_indice
                ORDER BY expediente_id, version DESC
            ) vigente
            JOIN expedientes e ON e.id = vigente.expediente_id
            WHERE {where}
            """,
            max_clearance,
        )
        rows = await self._conn.fetch(
            f"""
            SELECT vigente.id AS indice_id, vigente.expediente_id, vigente.version,
                   vigente.reconcile_intentos, vigente.reconcile_agotado,
                   vigente.reconcile_ultimo_error, e.code AS expediente_code
            FROM (
                SELECT DISTINCT ON (expediente_id) *
                FROM expediente_indice
                ORDER BY expediente_id, version DESC
            ) vigente
            JOIN expedientes e ON e.id = vigente.expediente_id
            WHERE {where}
            ORDER BY vigente.expediente_id
            LIMIT $2 OFFSET $3
            """,
            max_clearance,
            limit,
            offset,
        )
        return list(rows), total or 0

    # -- E10 Inc.1: cableado WORM del índice --------------------------------

    async def get_by_id(self, indice_id: uuid.UUID) -> asyncpg.Record | None:
        """Fila del índice por `id`, con las columnas del cableado WORM
        (`_WORM_COLS`) incluidas — usada por
        `IndexService.proteger_worm_indice` (disparo síncrono del cierre Y
        3ra fase de reconciliación) para leer `minio_key_firmado`/`estado`/
        `worm_protegido_at` de la MISMA versión que se acaba de firmar, sin
        asumir que sigue siendo la vigente de `expediente_id` (aunque en la
        práctica lo es: `estado='firmado'` es terminal, ver `regenerate`)."""
        return await self._conn.fetchrow(
            f"SELECT {_INDICE_COLS}, {_WORM_COLS} FROM expediente_indice WHERE id = $1",
            indice_id,
        )

    async def mark_worm_protegido(
        self,
        indice_id: uuid.UUID,
        worm_object_id: uuid.UUID,
        *,
        worm_retain_until: datetime | None = None,
    ) -> int:
        """Marca la copia WORM del índice como confirmada (E10 Inc.1).

        Defensa en profundidad (mismo patrón que `mark_signed`): el `WHERE`
        excluye filas ya protegidas — `worm_protegido_at` se fija UNA sola
        vez, nunca se re-escribe. Devuelve el número de filas afectadas (0 o
        1): `IndexService.proteger_worm_indice` lo usa para no escribir un
        asiento `archive.indice_worm_protegido` fantasma si OTRA llamada
        concurrente (el disparo síncrono del cierre vs. la barrida de
        reconciliación corriendo en paralelo) ya ganó la carrera.

        `worm_retain_until` (E10 debt, migración 031) puebla el espejo local
        del `retain_until` que storage-service acaba de confirmar — opcional
        (default `None`) para no romper llamadores/tests anteriores a esta
        columna; `IndexService.proteger_worm_indice` SIEMPRE lo pasa desde
        `WormProtectionOutcome.retain_until`.
        """
        tag = await self._conn.execute(
            "UPDATE expediente_indice SET worm_protegido_at = now(), worm_object_id = $2, "
            "worm_retain_until = COALESCE($3, worm_retain_until) "
            "WHERE id = $1 AND worm_protegido_at IS NULL",
            indice_id,
            worm_object_id,
            worm_retain_until,
        )
        try:
            return int(tag.split()[-1])
        except (AttributeError, ValueError, IndexError):
            # Mismo fallback defensivo que `mark_signed` — solo protege mocks
            # de test cuyo `conn.execute` no configura un valor de retorno
            # realista.
            return 0

    async def bump_worm_reconcile_intento(
        self,
        indice_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotado: bool,
    ) -> None:
        """Registra un intento FALLIDO de protección WORM (E10 Inc.1) —
        gemelo de `bump_intento` (sellado XAdES-B) para el eje del cableado
        WORM: incrementa `worm_reconcile_intentos` atómicamente en el propio
        UPDATE y fija `worm_reconcile_proximo_at`/`worm_reconcile_ultimo_error`/
        `worm_reconcile_agotado` con lo que decidió el llamador."""
        await self._conn.execute(
            "UPDATE expediente_indice SET "
            "worm_reconcile_intentos = worm_reconcile_intentos + 1, "
            "worm_reconcile_proximo_at = $2, worm_reconcile_ultimo_error = $3, "
            "worm_reconcile_agotado = $4 WHERE id = $1",
            indice_id,
            proximo_at,
            ultimo_error,
            agotado,
        )

    async def list_worm_pending(self, limit: int) -> list[asyncpg.Record]:
        """Índices 'firmado' elegibles para `run_once_worm` (3ra fase de
        reconciliación, E10 Inc.1): sin proteger todavía
        (`worm_protegido_at IS NULL`), no agotados, y con el backoff
        cumplido.

        Sin `DISTINCT ON (expediente_id)` (a diferencia de
        `list_pending_signature`): `estado='firmado'` es TERMINAL por
        expediente — `regenerate` rechaza con 409 crear una versión nueva
        sobre un índice ya firmado (ver su docstring) — así que a lo sumo
        una fila por `expediente_id` puede llegar a `estado='firmado'`, nunca
        hay ambigüedad de "cuál versión" que resolver aquí.
        """
        return list(
            await self._conn.fetch(
                """
                SELECT ei.id, ei.expediente_id, ei.minio_key_firmado,
                       ei.worm_reconcile_intentos, ei.worm_reconcile_agotado,
                       ei.worm_reconcile_ultimo_error, e.code AS expediente_code
                FROM expediente_indice ei
                JOIN expedientes e ON e.id = ei.expediente_id
                WHERE ei.estado = 'firmado'
                  AND ei.worm_protegido_at IS NULL
                  AND ei.worm_reconcile_agotado = FALSE
                  AND (ei.worm_reconcile_proximo_at IS NULL OR ei.worm_reconcile_proximo_at <= now())
                ORDER BY ei.worm_reconcile_proximo_at ASC NULLS FIRST
                LIMIT $1
                """,
                limit,
            )
        )

    # -- E10 debt: renovación de retención WORM para series CT -------------

    async def list_worm_renovacion_pending(
        self, *, ventana_dias: int, ct_codes: list[str], limit: int
    ) -> list[asyncpg.Record]:
        """Filas espejo del índice CUYA SERIE es de disposición Conservación
        Total (CT, `app.core.disposition.CT_DISPOSITION_CODES`) y cuyo
        `worm_retain_until` vence dentro de `ventana_dias` (E10 debt,
        migración 031) — 5ª fase de reconciliación, `run_once_worm_renovacion`.

        Solo YA protegidas (`worm_protegido_at IS NOT NULL`), no agotadas de
        renovación, con backoff cumplido. El filtro CT vive EN LA CONSULTA
        (`s.disposition = ANY($2)`) — fail-closed por construcción: una serie
        SIN disposición CT jamás entra en el resultado, sin importar cuán
        próxima esté a vencer (E/S/M expiran y se disponen legítimamente, no
        deben renovarse). Sin `DISTINCT ON`: `estado='firmado'` es terminal
        por expediente (ver `list_worm_pending`), a lo sumo una fila
        `worm_protegido_at IS NOT NULL` por `expediente_id`.
        """
        return list(
            await self._conn.fetch(
                """
                SELECT ei.id, ei.expediente_id, ei.worm_retain_until,
                       ei.worm_renovacion_intentos, e.code AS expediente_code
                FROM expediente_indice ei
                JOIN expedientes e ON e.id = ei.expediente_id
                JOIN trd_series s ON s.id = e.trd_serie_id
                WHERE ei.worm_protegido_at IS NOT NULL
                  AND ei.worm_renovacion_agotado = FALSE
                  AND ei.worm_retain_until IS NOT NULL
                  AND ei.worm_retain_until < now() + ($1 * INTERVAL '1 day')
                  AND (ei.worm_renovacion_proximo_at IS NULL OR ei.worm_renovacion_proximo_at <= now())
                  AND s.disposition = ANY($2::text[])
                ORDER BY ei.worm_renovacion_proximo_at ASC NULLS FIRST
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
        """Índices YA protegidos bajo WORM, de serie CT, cuya renovación está
        AGOTADA (`worm_renovacion_agotado = TRUE`) o EN RIESGO (vence dentro
        de `ventana_dias`, MISMA ventana que vigila el job de reconciliación
        -- `settings.worm_renovacion_ventana_dias`) -- visibilidad
        operacional (E10 debt, `GET /expedientes/indices/pendientes-renovacion`),
        gemela de `list_pending_signature_page`.

        A diferencia de `list_worm_renovacion_pending` (consumida por el job:
        excluye agotados y respeta el backoff `worm_renovacion_proximo_at`,
        porque el job NO debe reintentar algo agotado ni antes de su próxima
        ventana), este método es puramente de LECTURA humana: SÍ incluye los
        agotados (son justamente los que requieren intervención manual
        urgente) y NO filtra por backoff (un archivista debe poder ver un
        artefacto en riesgo aunque el job todavía no esté listo para
        reintentarlo).

        No-read-up (RF-SEG-08): filtra por `e.nivel_seguridad <= $max_clearance`,
        mismo criterio que `list_pending_signature_page`. Fail-closed por
        construcción: `s.disposition = ANY($ct_codes)` (`CT_DISPOSITION_CODES`,
        `app/core/disposition.py`) -- una serie E/S/M jamás entra al
        resultado, sin importar cuán agotada esté su renovación (no la
        necesitan: expiran y se disponen legítimamente).

        Sin paginación SQL propia (a diferencia de `list_pending_signature_page`):
        el servicio combina esta lista con la gemela de actas
        (`TransferenciaRepository.list_worm_renovacion_visibilidad`, JOIN
        distinto -- `transferencia_acta_firma`/`transferencias`) y pagina el
        resultado combinado en memoria; el volumen esperado (artefactos CT en
        riesgo de un tenant) es pequeño."""
        return list(
            await self._conn.fetch(
                """
                SELECT ei.expediente_id, e.code AS expediente_code,
                       ei.worm_retain_until, ei.worm_renovacion_intentos,
                       ei.worm_renovacion_agotado, ei.worm_renovacion_ultimo_error
                FROM expediente_indice ei
                JOIN expedientes e ON e.id = ei.expediente_id
                JOIN trd_series s ON s.id = e.trd_serie_id
                WHERE ei.worm_protegido_at IS NOT NULL
                  AND s.disposition = ANY($1::text[])
                  AND e.nivel_seguridad <= $2
                  AND (
                        ei.worm_renovacion_agotado = TRUE
                        OR (
                            ei.worm_retain_until IS NOT NULL
                            AND ei.worm_retain_until < now() + ($3 * INTERVAL '1 day')
                        )
                      )
                ORDER BY ei.worm_renovacion_agotado DESC, ei.worm_retain_until ASC NULLS LAST
                """,
                ct_codes,
                max_clearance,
                ventana_dias,
            )
        )

    async def mark_worm_renovado(
        self, indice_id: uuid.UUID, *, worm_retain_until: datetime
    ) -> None:
        """Actualiza el espejo `worm_retain_until` tras una llamada CONFIRMADA
        (`outcome.ok=True`) a `renovar-retencion-internal` — real o no-op
        (ver `PreservationWormService.renovar_retencion` del lado de
        storage-service). Resetea el backoff de renovación (mismo criterio
        que `mark_signed` resetea el backoff de sellado): una llamada
        confirmada, por definición, deja la fila sana, sin importar si
        `outcome.renovado` fue True o False — el AUDITADO de la extensión
        real lo decide el llamador (`IndexService.renovar_worm_indice`)
        aparte, no esta actualización de estado."""
        await self._conn.execute(
            "UPDATE expediente_indice SET worm_retain_until = $2, "
            "worm_renovacion_intentos = 0, worm_renovacion_proximo_at = NULL, "
            "worm_renovacion_ultimo_error = NULL, worm_renovacion_agotado = FALSE "
            "WHERE id = $1",
            indice_id,
            worm_retain_until,
        )

    async def bump_worm_renovacion_intento(
        self,
        indice_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotado: bool,
    ) -> None:
        """Registra un intento FALLIDO de renovación de retención WORM (E10
        debt) — gemelo de `bump_worm_reconcile_intento` para el eje de
        RENOVACIÓN: incrementa `worm_renovacion_intentos` atómicamente en el
        propio UPDATE y fija `worm_renovacion_proximo_at`/
        `worm_renovacion_ultimo_error`/`worm_renovacion_agotado` con lo que
        decidió el llamador."""
        await self._conn.execute(
            "UPDATE expediente_indice SET "
            "worm_renovacion_intentos = worm_renovacion_intentos + 1, "
            "worm_renovacion_proximo_at = $2, worm_renovacion_ultimo_error = $3, "
            "worm_renovacion_agotado = $4 WHERE id = $1",
            indice_id,
            proximo_at,
            ultimo_error,
            agotado,
        )

    async def get_items(self, indice_id: uuid.UUID) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                "SELECT document_id, orden, titulo, tipologia, fecha_declaracion, "
                "fecha_archivado, valor_huella, funcion_resumen, formato, tamano_bytes, "
                "folios, orden_documental, nivel_seguridad_snapshot, estado, "
                "dependencia_productora_codigo, dependencia_productora_nombre "
                "FROM expediente_indice_item WHERE indice_id = $1 ORDER BY orden ASC",
                indice_id,
            )
        )

    async def insert_version(
        self,
        *,
        expediente_id: uuid.UUID,
        version: int,
        xml: str,
        xml_sha256: str,
        content_hash: str,
        num_documentos: int,
        generado_por: str,
        items: list[IndiceItemData],
    ) -> asyncpg.Record:
        async with self._conn.transaction():
            indice = await self._conn.fetchrow(
                f"""
                INSERT INTO expediente_indice
                    (expediente_id, version, xml, xml_sha256, content_hash,
                     num_documentos, generado_por, xsd_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING {_INDICE_COLS}
                """,
                expediente_id,
                version,
                xml,
                xml_sha256,
                content_hash,
                num_documentos,
                generado_por,
                XSD_VERSION,  # explícito: NO confiar en el DEFAULT 'v1' de la tabla
            )
            assert indice is not None
            for it in items:
                await self._conn.execute(
                    """
                    INSERT INTO expediente_indice_item
                        (indice_id, document_id, orden, titulo, tipologia,
                         fecha_archivado, valor_huella, funcion_resumen,
                         formato, tamano_bytes, folios,
                         orden_documental, nivel_seguridad_snapshot, estado,
                         dependencia_productora_codigo, dependencia_productora_nombre)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                    """,
                    indice["id"],
                    uuid.UUID(it.document_id),
                    it.orden,
                    it.titulo,
                    it.tipologia,
                    it.fecha_archivado,
                    it.valor_huella,
                    it.funcion_resumen,
                    it.formato,
                    it.tamano_bytes,
                    it.folios,
                    # v3 (E15 finalización RT-15): snapshot inmutable del
                    # orden_documental ESTABLE, el nivel de seguridad heredado
                    # del expediente y el estado del vínculo EN el momento de
                    # esta versión — permiten reconstruir el hueco trazable de
                    # versiones históricas sin depender del estado actual de
                    # `expediente_radicados`.
                    it.orden,
                    it.nivel_seguridad,
                    it.estado,
                    # v4 (E15 perfil v4, R1): snapshot de la dependencia
                    # productora — NULL si no se declaró al vincular.
                    it.dependencia_productora_codigo,
                    it.dependencia_productora_nombre,
                )
        return indice
