# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de transferencias documentales (E12). search_path al tenant."""

from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg

from app.core.database import tenant_schema

_COLS = (
    "id, tipo, estado, expediente_id, ubicacion_origen_id, ubicacion_destino_id, "
    "creada_por, observacion, created_at, enviada_at, enviada_por, recibida_at, decidida_por, "
    # H-K/H-J (migración 021): motivo propio del rechazo (nunca pisa
    # `observacion`) + sello temporal propio del acto.
    "motivo_rechazo, rechazada_at"
)

# Migración 022 (E12 H-I): acta de entrega congelada, 1:1 con la transferencia
# RECIBIDA. Columnas propias -- deliberadamente NO se añaden a `_COLS` (el
# XML no debe arrastrarse por el hot path de `insert`/`get`/`list`/
# `update_estado`, ver docstring de la migración).
_ACTA_COLS = (
    "transferencia_id, acta_fuid_xml, acta_fuid_sha256, elaborado_por, entregado_por, "
    "recibido_por, elaborado_at, entregado_at, recibido_at, indice_version, congelada_at"
)

# Migración 026 (H-H, E06/F4): tabla HERMANA mutable de `transferencia_acta`
# -- rastrea el sellado XAdES del acta, 1:1 por transferencia. Deliberadamente
# NO fusionada en `_ACTA_COLS`: `transferencia_acta` sigue siendo append-only,
# esta tabla es la ÚNICA que admite UPDATE.
_ACTA_FIRMA_COLS = (
    "transferencia_id, estado, firma_id, minio_key_firmado, xades_level, acreditado, "
    "last_error, reconcile_intentos, reconcile_proximo_at, reconcile_agotado, "
    "created_at, firmado_at"
)

# E10 Inc.2 wiring (migración 030): estado del cableado WORM del acta —
# ORTOGONAL a `estado`/`reconcile_*` de arriba (esas son del SELLADO XAdES).
# Columnas aparte en vez de sumarlas a `_ACTA_FIRMA_COLS`: solo
# `get_acta_firma_worm`/`list_worm_pending_actas` (consumidores del cableado
# WORM) las necesitan — mismo criterio que `IndexRepository._WORM_COLS`.
_ACTA_FIRMA_WORM_COLS = (
    "worm_protegido_at, worm_object_id, worm_reconcile_intentos, "
    "worm_reconcile_proximo_at, worm_reconcile_ultimo_error, worm_reconcile_agotado"
)

# Migración 027 (E17/F4 Incremento 1 -- firma PERSONAL PKI por-usuario del
# acta): tabla ADITIVA sobre `transferencia_acta_firma` (sello institucional,
# migración 026) -- 1:N por transferencia (una fila por `rol` firmante,
# hoy solo 'recibe'), estrictamente append-only (sin columnas reconcile_*:
# la firma personal exige un TOTP interactivo, nunca se reconcilia en
# background).
_ACTA_FIRMA_PERSONAL_COLS = (
    "id, transferencia_id, rol, firmante_id, firma_id, minio_key_firmado, "
    "xades_level, ca_fingerprint, acreditado, creada_en"
)

# Columnas que `update_estado` NUNCA acepta en `assignments`: `estado` tiene su
# propio parámetro (para que la guarda `expected_estado` quede visible en la
# firma, no enterrada en un dict libre) y `id` identifica la fila, no un valor
# a escribir. Un solo escritor de la columna de estado — mismo principio que
# retiró `status` de `ArchiveRepository.update_expediente` (ver su docstring).
_FORBIDDEN_ASSIGNMENT_COLS = frozenset({"estado", "id"})


class TransferenciaRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug

    async def _sp(self) -> None:
        schema = tenant_schema(self._tenant_slug)  # valida + normaliza (fail-closed)
        await self._conn.execute(f'SET search_path TO "{schema}", public')

    async def insert(self, data: dict) -> asyncpg.Record:
        await self._sp()
        cols = list(data.keys())
        ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        row = await self._conn.fetchrow(
            f"INSERT INTO transferencias ({', '.join(cols)}) VALUES ({ph}) " f"RETURNING {_COLS}",
            *data.values(),
        )
        assert row is not None
        return row

    async def get(self, transferencia_id: uuid.UUID) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM transferencias WHERE id = $1", transferencia_id
        )

    async def list(self, *, estado: str | None, tipo: str | None) -> list[asyncpg.Record]:
        await self._sp()
        conds: list[str] = []
        params: list[object] = []
        if estado is not None:
            params.append(estado)
            conds.append(f"estado = ${len(params)}")
        if tipo is not None:
            params.append(tipo)
            conds.append(f"tipo = ${len(params)}")
        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM transferencias {where} " "ORDER BY created_at DESC",
                *params,
            )
        )

    async def update_estado(
        self,
        transferencia_id: uuid.UUID,
        *,
        expected_estado: str,
        estado: str,
        assignments: dict | None = None,
    ) -> asyncpg.Record | None:
        """Transiciona `estado` con guarda TOCTOU (H-A) — gemela de
        `ArchiveRepository.transition_expediente` (ver su docstring, H3/cf83324).

        Antes este método emitía `UPDATE ... WHERE id = $N` SIN comprobar el
        estado de origen: `TransferenciaService._require` validaba la
        transición ANTES de llamar aquí, pero esa validación quedaba FUERA de
        la sentencia — dos llamadas concurrentes (p. ej. `rechazar()` y
        `recibir()` sobre la MISMA transferencia 'enviada') podían leer ambas
        el mismo estado de origen, pasar `_require`, y comprometer AMBOS
        `UPDATE`. El desenlace letal: `rechazada` + expediente ya congelado
        `transferred` por el `recibir()` concurrente -- un estado jurídicamente
        imposible e irreversible en un `audit_log` INDELEBLE (Ley 594/2000 art.
        16; Acuerdo AGN 001/2024 Tít. 4.4).

        `expected_estado` es OBLIGATORIO y solo por *keyword*, sin default: con
        un default "permisivo" un call-site futuro podría omitirlo y el TOCTOU
        volvería en silencio; sin default, olvidarlo es un `TypeError` en la
        primera llamada, no un bug que sobrevive a los tests. `estado` sale del
        dict libre para que la guarda sea visible en la firma, no un valor más
        entre `assignments` (que además la RECHAZA explícitamente, junto con
        `id`, ver `_FORBIDDEN_ASSIGNMENT_COLS`).

        `None` (perdedor de la carrera) es responsabilidad del LLAMANTE:
        `_require` sigue siendo la validación temprana/UX (404/409 con mensaje
        de negocio); este `WHERE` es quien serializa de verdad bajo
        concurrencia real y quien decide el caso alcanzable.
        """
        assignments = assignments or {}
        forbidden = _FORBIDDEN_ASSIGNMENT_COLS & assignments.keys()
        if forbidden:
            raise ValueError(
                f"update_estado: assignments no puede incluir {sorted(forbidden)} "
                "-- usa los parámetros `estado`/`transferencia_id` dedicados."
            )
        await self._sp()
        cols = list(assignments.keys())
        set_parts = ["estado = $1"]
        params: list[object] = [estado]
        idx = 2
        for c in cols:
            set_parts.append(f"{c} = ${idx}")
            params.append(assignments[c])
            idx += 1
        id_idx = idx
        params.append(transferencia_id)
        idx += 1
        expected_idx = idx
        params.append(expected_estado)
        return await self._conn.fetchrow(
            f"UPDATE transferencias SET {', '.join(set_parts)} "
            f"WHERE id = ${id_idx} AND estado = ${expected_idx} "
            f"RETURNING {_COLS}",
            *params,
        )

    async def insert_acta(self, data: dict) -> None:
        """Congela el acta de entrega (E12 H-I): escritura ÚNICA dentro de la
        MISMA `conn.transaction()` de `TransferenciaService.recibir` -- no hay
        ruta de `UPDATE` sobre esta tabla en el repositorio (la `PRIMARY KEY`
        de la migración 022 impide además un segundo `INSERT`), así que el
        acta es append-only de facto. Sin `RETURNING`: el llamante ya tiene
        todos los valores que insertó, no necesita releerlos."""
        await self._sp()
        cols = list(data.keys())
        ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        await self._conn.execute(
            f"INSERT INTO transferencia_acta ({', '.join(cols)}) VALUES ({ph})",
            *data.values(),
        )

    async def get_acta(self, transferencia_id: uuid.UUID) -> asyncpg.Record | None:
        """Devuelve el acta YA CONGELADA -- `None` si la transferencia aún no
        se recibió (o fue rechazada, que nunca produce acta, ver
        `TransferenciaService.recibir`)."""
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_ACTA_COLS} FROM transferencia_acta WHERE transferencia_id = $1",
            transferencia_id,
        )

    # -----------------------------------------------------------------
    # transferencia_acta_firma (H-H, E06/F4) -- estado MUTABLE del sellado
    # -----------------------------------------------------------------

    async def insert_acta_firma_pendiente(self, transferencia_id: uuid.UUID) -> None:
        """Crea la fila de bookkeeping `'pendiente_firma'` -- escritura LOCAL
        de BD (sin HTTP), llamada DENTRO de la MISMA `conn.transaction()` que
        `insert_acta` en `TransferenciaService.recibir` (preserva la
        atomicidad H-A/H-I: nunca HTTP dentro de esa tx). Garantiza que TODA
        acta congelada tenga su fila de firma-tracking aunque el proceso
        muera entre el commit y el intento de sellado post-commit -- el job
        de reconciliación la recoge igual."""
        await self._sp()
        await self._conn.execute(
            "INSERT INTO transferencia_acta_firma (transferencia_id) VALUES ($1)",
            transferencia_id,
        )

    async def get_acta_firma(self, transferencia_id: uuid.UUID) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_ACTA_FIRMA_COLS} FROM transferencia_acta_firma WHERE transferencia_id = $1",
            transferencia_id,
        )

    async def mark_acta_firmada(
        self,
        transferencia_id: uuid.UUID,
        *,
        firma_id: uuid.UUID,
        minio_key: str | None,
        xades_level: str | None,
        acreditado: bool,
    ) -> int:
        """Marca el acta como sellada e inmutable (gemela de
        `IndexRepository.mark_signed`). `WHERE estado <> 'firmado'` es
        defensa en profundidad Y el mecanismo real de idempotencia bajo
        carrera manual-vs-job (ver `SignOutcome`/`_sellar_acta_post_commit`):
        devuelve el número de filas afectadas (0 o 1) para que el llamante
        NUNCA escriba un asiento `archive.acta_firmada` fantasma sobre un
        efecto que esta llamada no produjo. Resetea el backoff -- un acta
        recién firmada no tiene nada más que reconciliar."""
        await self._sp()
        tag = await self._conn.execute(
            "UPDATE transferencia_acta_firma SET estado='firmado', firma_id=$2, "
            "minio_key_firmado=$3, xades_level=$4, acreditado=$5, "
            "reconcile_intentos=0, reconcile_agotado=false, reconcile_proximo_at=NULL, "
            "last_error=NULL, firmado_at=now() "
            "WHERE transferencia_id=$1 AND estado <> 'firmado'",
            transferencia_id,
            firma_id,
            minio_key,
            xades_level,
            acreditado,
        )
        try:
            return int(tag.split()[-1])
        except (AttributeError, ValueError, IndexError):
            # Mismo fallback defensivo que `IndexRepository.mark_signed`
            # (docstring ahí): solo protege mocks de test sin status tag
            # realista, asyncpg SIEMPRE devuelve "UPDATE n".
            return 0

    async def mark_acta_firma_pendiente(
        self, transferencia_id: uuid.UUID, *, last_error: str | None
    ) -> None:
        """Degrada/re-arma a `'pendiente_firma'` (gemela de
        `IndexRepository.mark_pendiente`): re-arma SIEMPRE el backoff
        (`reconcile_agotado=false, reconcile_intentos=0`) y fija
        `reconcile_proximo_at=now()` -- toda degradación queda de inmediato
        elegible para el job de reconciliación, sin esperar un primer
        `bump_acta_firma_intento`. Usada tanto por el intento síncrono
        post-commit de `recibir()` (degradación) como por el reintento manual
        `POST /transferencias/{id}/acta/firmar` (re-armado explícito antes de
        reintentar) -- mismo criterio C1 que su gemelo del índice: un acta
        `reconcile_agotado=TRUE` cuyo reintento manual vuelve a fallar no debe
        quedar invisible ni para el job ni para el operador.

        `WHERE estado <> 'firmado'`: un acta ya sellada (inmutable) nunca se
        degrada de vuelta a pendiente."""
        await self._sp()
        await self._conn.execute(
            "UPDATE transferencia_acta_firma SET estado='pendiente_firma', "
            "last_error=$2, reconcile_proximo_at=now(), "
            "reconcile_agotado=false, reconcile_intentos=0 "
            "WHERE transferencia_id=$1 AND estado <> 'firmado'",
            transferencia_id,
            last_error,
        )

    async def bump_acta_firma_intento(
        self,
        transferencia_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        last_error: str,
        agotado: bool,
    ) -> None:
        """Registra un intento de sellado FALLIDO del job de reconciliación
        (gemela de `IndexRepository.bump_intento`) -- incrementa
        `reconcile_intentos` atómicamente en el propio UPDATE, sin lectura
        previa. `proximo_at=None` es válido cuando `agotado=True` (ya no hay
        "próximo intento" que programar)."""
        await self._sp()
        await self._conn.execute(
            "UPDATE transferencia_acta_firma SET "
            "reconcile_intentos = reconcile_intentos + 1, "
            "reconcile_proximo_at = $2, last_error = $3, reconcile_agotado = $4 "
            "WHERE transferencia_id = $1",
            transferencia_id,
            proximo_at,
            last_error,
            agotado,
        )

    # -----------------------------------------------------------------
    # transferencia_acta_firma_personal (E17/F4 Inc.1) -- firma PERSONAL
    # PKI por-usuario, ADITIVA sobre el sello institucional, append-only
    # -----------------------------------------------------------------

    async def get_acta_firma_personal(
        self, transferencia_id: uuid.UUID, *, rol: str
    ) -> asyncpg.Record | None:
        """Fila de firma personal para UN rol -- `None` si ese rol aún no
        firmó (idempotencia de `firmar_personal`: una fila existente
        significa "ya firmado", nunca se re-firma ni se re-quema el TOTP)."""
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_ACTA_FIRMA_PERSONAL_COLS} FROM transferencia_acta_firma_personal "
            "WHERE transferencia_id = $1 AND rol = $2",
            transferencia_id,
            rol,
        )

    async def list_acta_firma_personal(self, transferencia_id: uuid.UUID) -> list[asyncpg.Record]:
        """Todas las firmas personales de una transferencia (hoy, a lo sumo
        una: 'recibe'; 'entrega' queda representable para el Inc.2 -- deuda)."""
        await self._sp()
        return list(
            await self._conn.fetch(
                f"SELECT {_ACTA_FIRMA_PERSONAL_COLS} FROM transferencia_acta_firma_personal "
                "WHERE transferencia_id = $1 ORDER BY rol",
                transferencia_id,
            )
        )

    async def insert_acta_firma_personal(self, data: dict) -> asyncpg.Record | None:
        """Inserta la fila de firma personal -- `ON CONFLICT (transferencia_id,
        rol) DO NOTHING`: bajo una carrera manual-vs-manual (dos peticiones
        concurrentes de `firmar_personal` para el MISMO rol, ambas ya
        pasaron el chequeo de idempotencia de lectura y ambas firmaron con
        signature-service, ver `TransferenciaService.firmar_personal`), solo
        UNA gana la escritura local -- `None` para la perdedora, que NUNCA
        debe escribir un asiento `archive.acta_firmada_personal` fantasma
        (misma clase de riesgo S1 que `mark_acta_firmada`)."""
        await self._sp()
        cols = list(data.keys())
        ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        return await self._conn.fetchrow(
            f"INSERT INTO transferencia_acta_firma_personal ({', '.join(cols)}) "
            f"VALUES ({ph}) ON CONFLICT (transferencia_id, rol) DO NOTHING "
            f"RETURNING {_ACTA_FIRMA_PERSONAL_COLS}",
            *data.values(),
        )

    async def list_acta_firmas_pendientes(self, limit: int) -> list[asyncpg.Record]:
        """Actas `'pendiente_firma'` elegibles para el job de reconciliación
        (gemela de `IndexRepository.list_pending_signature`). A diferencia
        del índice (append-only, `DISTINCT ON` sobre versiones), el acta es
        1:1 por transferencia -- no hay versión que resolver. Excluye
        `reconcile_agotado=TRUE` y respeta el backoff
        (`reconcile_proximo_at IS NULL OR <= now()`); mismo orden
        `NULLS FIRST` que su gemela (recién degradadas primero)."""
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT taf.transferencia_id, taf.reconcile_intentos,
                       ta.acta_fuid_xml, ta.indice_version
                FROM transferencia_acta_firma taf
                JOIN transferencia_acta ta ON ta.transferencia_id = taf.transferencia_id
                WHERE taf.estado = 'pendiente_firma'
                  AND taf.reconcile_agotado = false
                  AND (taf.reconcile_proximo_at IS NULL OR taf.reconcile_proximo_at <= now())
                ORDER BY taf.reconcile_proximo_at ASC NULLS FIRST
                LIMIT $1
                """,
                limit,
            )
        )

    # -----------------------------------------------------------------
    # E10 Inc.2 wiring: cableado WORM del acta -- espejo EXACTO de
    # `IndexRepository.get_by_id`/`mark_worm_protegido`/
    # `bump_worm_reconcile_intento`/`list_worm_pending`.
    # -----------------------------------------------------------------

    async def get_acta_firma_worm(self, transferencia_id: uuid.UUID) -> asyncpg.Record | None:
        """Fila de `transferencia_acta_firma` con las columnas del cableado
        WORM (`_ACTA_FIRMA_WORM_COLS`) incluidas -- usada por
        `TransferenciaService.proteger_worm_acta` (disparo síncrono
        post-commit Y 4ª fase de reconciliación) para leer
        `minio_key_firmado`/`estado`/`firmado_at`/`worm_protegido_at` de la
        MISMA fila que se acaba de firmar (gemela de `IndexRepository.
        get_by_id`)."""
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_ACTA_FIRMA_COLS}, {_ACTA_FIRMA_WORM_COLS} "
            "FROM transferencia_acta_firma WHERE transferencia_id = $1",
            transferencia_id,
        )

    async def mark_acta_worm_protegido(
        self,
        transferencia_id: uuid.UUID,
        worm_object_id: uuid.UUID,
        *,
        worm_retain_until: datetime | None = None,
    ) -> int:
        """Marca la copia WORM del acta como confirmada (E10 Inc.2 wiring,
        gemela de `IndexRepository.mark_worm_protegido`).

        Defensa en profundidad (mismo patrón que `mark_acta_firmada`): el
        `WHERE` excluye filas ya protegidas -- `worm_protegido_at` se fija UNA
        sola vez, nunca se re-escribe. Devuelve el número de filas afectadas
        (0 o 1): `TransferenciaService.proteger_worm_acta` lo usa para no
        escribir un asiento `archive.acta_worm_protegido` fantasma si OTRA
        llamada concurrente (el disparo síncrono post-commit vs. la barrida de
        reconciliación corriendo en paralelo) ya ganó la carrera.

        `worm_retain_until` (E10 debt, migración 032) puebla el espejo local
        del `retain_until` confirmado por storage-service -- opcional
        (default `None`, mismo criterio que `IndexRepository.mark_worm_protegido`).
        """
        await self._sp()
        tag = await self._conn.execute(
            "UPDATE transferencia_acta_firma SET worm_protegido_at = now(), "
            "worm_object_id = $2, worm_retain_until = COALESCE($3, worm_retain_until) "
            "WHERE transferencia_id = $1 AND worm_protegido_at IS NULL",
            transferencia_id,
            worm_object_id,
            worm_retain_until,
        )
        try:
            return int(tag.split()[-1])
        except (AttributeError, ValueError, IndexError):
            # Mismo fallback defensivo que `mark_acta_firmada` -- solo protege
            # mocks de test cuyo `conn.execute` no configura un valor de
            # retorno realista.
            return 0

    async def bump_acta_worm_reconcile_intento(
        self,
        transferencia_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotado: bool,
    ) -> None:
        """Registra un intento FALLIDO de protección WORM del acta (E10 Inc.2
        wiring) -- gemelo de `IndexRepository.bump_worm_reconcile_intento`:
        incrementa `worm_reconcile_intentos` atómicamente en el propio UPDATE
        y fija `worm_reconcile_proximo_at`/`worm_reconcile_ultimo_error`/
        `worm_reconcile_agotado` con lo que decidió el llamador."""
        await self._sp()
        await self._conn.execute(
            "UPDATE transferencia_acta_firma SET "
            "worm_reconcile_intentos = worm_reconcile_intentos + 1, "
            "worm_reconcile_proximo_at = $2, worm_reconcile_ultimo_error = $3, "
            "worm_reconcile_agotado = $4 WHERE transferencia_id = $1",
            transferencia_id,
            proximo_at,
            ultimo_error,
            agotado,
        )

    async def list_worm_pending_actas(self, limit: int) -> list[asyncpg.Record]:
        """Actas 'firmado' elegibles para `run_once_actas_worm` (4ª fase de
        reconciliación, E10 Inc.2 wiring): sin proteger todavía
        (`worm_protegido_at IS NULL`), no agotadas, y con el backoff cumplido
        -- gemela de `IndexRepository.list_worm_pending`.

        Sin `DISTINCT ON`: el acta es 1:1 por transferencia (no versiona como
        el índice), a lo sumo una fila por `transferencia_id` puede llegar a
        `estado='firmado'`.
        """
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT taf.transferencia_id, taf.minio_key_firmado, t.expediente_id,
                       taf.worm_reconcile_intentos, taf.worm_reconcile_agotado,
                       taf.worm_reconcile_ultimo_error
                FROM transferencia_acta_firma taf
                JOIN transferencias t ON t.id = taf.transferencia_id
                WHERE taf.estado = 'firmado'
                  AND taf.worm_protegido_at IS NULL
                  AND taf.worm_reconcile_agotado = FALSE
                  AND (taf.worm_reconcile_proximo_at IS NULL OR taf.worm_reconcile_proximo_at <= now())
                ORDER BY taf.worm_reconcile_proximo_at ASC NULLS FIRST
                LIMIT $1
                """,
                limit,
            )
        )

    # -----------------------------------------------------------------
    # E10 debt: renovación de retención WORM del acta para series CT --
    # espejo EXACTO de `IndexRepository.list_worm_renovacion_pending`/
    # `mark_worm_renovado`/`bump_worm_renovacion_intento`.
    # -----------------------------------------------------------------

    async def list_worm_renovacion_pending(
        self, *, ventana_dias: int, ct_codes: list[str], limit: int
    ) -> list[asyncpg.Record]:
        """Actas CUYO EXPEDIENTE es de disposición Conservación Total (CT) y
        cuyo `worm_retain_until` vence dentro de `ventana_dias` (E10 debt,
        migración 032) -- 5ª fase de reconciliación, `run_once_worm_renovacion`.
        Gemela EXACTA de `IndexRepository.list_worm_renovacion_pending`."""
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT taf.transferencia_id, t.expediente_id, taf.worm_retain_until,
                       taf.worm_renovacion_intentos
                FROM transferencia_acta_firma taf
                JOIN transferencias t ON t.id = taf.transferencia_id
                JOIN expedientes e ON e.id = t.expediente_id
                JOIN trd_series s ON s.id = e.trd_serie_id
                WHERE taf.worm_protegido_at IS NOT NULL
                  AND taf.worm_renovacion_agotado = FALSE
                  AND taf.worm_retain_until IS NOT NULL
                  AND taf.worm_retain_until < now() + ($1 * INTERVAL '1 day')
                  AND (taf.worm_renovacion_proximo_at IS NULL OR taf.worm_renovacion_proximo_at <= now())
                  AND s.disposition = ANY($2::text[])
                ORDER BY taf.worm_renovacion_proximo_at ASC NULLS FIRST
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
        """Actas YA protegidas bajo WORM, de expediente CT, cuya renovación
        está AGOTADA o EN RIESGO de vencer -- gemela EXACTA de
        `IndexRepository.list_worm_renovacion_visibilidad` (mismo criterio de
        no-read-up, misma ventana, mismos códigos CT); ver su docstring para
        el contraste con `list_worm_renovacion_pending` (consumida por el
        job)."""
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT t.expediente_id, e.code AS expediente_code,
                       taf.worm_retain_until, taf.worm_renovacion_intentos,
                       taf.worm_renovacion_agotado, taf.worm_renovacion_ultimo_error
                FROM transferencia_acta_firma taf
                JOIN transferencias t ON t.id = taf.transferencia_id
                JOIN expedientes e ON e.id = t.expediente_id
                JOIN trd_series s ON s.id = e.trd_serie_id
                WHERE taf.worm_protegido_at IS NOT NULL
                  AND s.disposition = ANY($1::text[])
                  AND e.nivel_seguridad <= $2
                  AND (
                        taf.worm_renovacion_agotado = TRUE
                        OR (
                            taf.worm_retain_until IS NOT NULL
                            AND taf.worm_retain_until < now() + ($3 * INTERVAL '1 day')
                        )
                      )
                ORDER BY taf.worm_renovacion_agotado DESC, taf.worm_retain_until ASC NULLS LAST
                """,
                ct_codes,
                max_clearance,
                ventana_dias,
            )
        )

    async def mark_acta_worm_renovado(
        self, transferencia_id: uuid.UUID, *, worm_retain_until: datetime
    ) -> None:
        """Actualiza el espejo `worm_retain_until` del acta tras una llamada
        CONFIRMADA a `renovar-retencion-internal` -- gemela EXACTA de
        `IndexRepository.mark_worm_renovado`."""
        await self._sp()
        await self._conn.execute(
            "UPDATE transferencia_acta_firma SET worm_retain_until = $2, "
            "worm_renovacion_intentos = 0, worm_renovacion_proximo_at = NULL, "
            "worm_renovacion_ultimo_error = NULL, worm_renovacion_agotado = FALSE "
            "WHERE transferencia_id = $1",
            transferencia_id,
            worm_retain_until,
        )

    async def bump_acta_worm_renovacion_intento(
        self,
        transferencia_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotado: bool,
    ) -> None:
        """Registra un intento FALLIDO de renovación de retención WORM del
        acta (E10 debt) -- gemela EXACTA de
        `IndexRepository.bump_worm_renovacion_intento`."""
        await self._sp()
        await self._conn.execute(
            "UPDATE transferencia_acta_firma SET "
            "worm_renovacion_intentos = worm_renovacion_intentos + 1, "
            "worm_renovacion_proximo_at = $2, worm_renovacion_ultimo_error = $3, "
            "worm_renovacion_agotado = $4 WHERE transferencia_id = $1",
            transferencia_id,
            proximo_at,
            ultimo_error,
            agotado,
        )
