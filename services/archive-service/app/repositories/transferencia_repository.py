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
