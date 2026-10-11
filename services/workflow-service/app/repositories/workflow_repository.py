# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import asyncpg

from app.core.clearance import resolve_caller_clearance
from app.core.database import tenant_schema
from app.models.flow import FlowStep
from app.services.estado_radicado import (
    ESTADOS_TERMINALES,
    PasoUbicacion,
    Ubicacion,
    derivar_estado_radicado,
    derivar_ubicacion,
)


def _clearance_condition(idx: int) -> str:
    """Predicado no-read-up (RF-SEG-08) correlacionado con `flow_steps.radicado_id`.

    COALESCE a CLASIFICADA(3) si no hay fila de radicado (mismo criterio que
    `list_flow_events`): `radicados.nivel_seguridad` es NOT NULL, así que una fila
    AUSENTE nunca significa «sin clasificar» sino que el radicado se purgó (TRD)
    y dejó huérfanos append-only —que, tratados como públicos, convertían en
    legible la hoja de ruta de un reservado—. No es «dato aún no replicado»:
    `radicados` vive en el MISMO schema. Factorizado porque lo reutilizan
    `list_pending`, `list_inbox`, `get_history` y `get_current` — así el
    predicado no puede divergir entre call sites.
    """
    return (
        f"COALESCE((SELECT nivel_seguridad FROM radicados "
        f"WHERE id = flow_steps.radicado_id), 3) <= ${idx}"
    )


def _user_depts_subquery(idx: int) -> str:
    """CÓDIGOS de las dependencias ACTIVAS a las que pertenece el usuario `$idx`
    (URD activa). Compartido por el alcance de bandeja (`_own_scope_condition`)
    y por la tenencia (`_holder_condition`): una sola definición de «mi
    dependencia», para que ambos ejes no puedan divergir.

    Por CÓDIGO (ADR-032): `urd_usuaroldep.depe_id` ya es el código, así que no hay
    nada que traducir. Por nombre, renombrar una dependencia con trámite vivo
    dejaba los pasos fuera de toda bandeja."""
    return (
        f"SELECT u.depe_id FROM urd_usuaroldep u "
        f"JOIN dependencias d ON d.codigo = u.depe_id "
        f"WHERE u.user_id = ${idx} AND u.is_active = TRUE AND d.activo = TRUE"
    )


def _holder_condition(idx: int, alias: str = "") -> str:
    """Poseedor del paso activo (ADR-028): asignado a `$idx`, o paso de
    dependencia SIN dueño (`assigned_to IS NULL`) y `$idx` pertenece a ella.

    Más estrecho que `_own_scope_condition` a propósito: aquello es visibilidad
    de bandeja (un miembro de la dependencia VE lo asignado a un colega); esto
    es tenencia (solo el dueño, o cualquiera de la dependencia si nadie lo
    tiene). NULL-safe: sin `$idx` ninguna rama casa (fail-closed)."""
    return (
        f"(assigned_to = ${idx} OR "
        f"(assigned_to IS NULL AND to_dept_code IN ({_user_depts_subquery(idx)})))"
    )


def _nivel_poseedor_condition(idx: int, tabla: str = "flow_steps") -> str:
    """No-read-up (RF-SEG-08) para los actos por TENENCIA: el nivel del radicado
    del paso va EN el `WHERE`, no en una comprobación previa. Tener el paso (o
    pertenecer a su dependencia) no basta si el radicado está por encima del
    clearance del actor: un miembro de nivel 1 de la dependencia no cierra un
    reservado repartido a ella sin dueño. Ausencia de fila en `radicados` = no
    casa (fail-closed; difiere de `_clearance_condition`, que es de lectura).
    El resultado es el mismo que «no hay paso»: el llamante responde su 404
    neutro, sin oráculo."""
    return (
        "EXISTS (SELECT 1 FROM radicados rn "
        f"WHERE rn.id = {tabla}.radicado_id AND rn.nivel_seguridad <= ${idx})"
    )


def _own_scope_condition(idx: int) -> str:
    """Predicado de alcance POR DEFECTO de bandeja/pending (cierre de la
    primitiva de enumeración: `GET /workflows/inbox`/`/pending` sin acotar
    dejaban ver la carga de TODA la entidad a cualquier usuario autenticado).

    Sin `dept`/`assigned_to` explícitos, el llamante ve lo suyo: pasos
    asignados a él (`assigned_to = $idx`) O de una dependencia a la que
    pertenece por URD activa (`urd_usuaroldep`, auth-service) — la
    correlación es por CÓDIGO (ADR-032): `flow_steps.to_dept_code` contra
    `urd_usuaroldep.depe_id` (que ya es el código de `dependencias`). Antes era
    por NOMBRE y renombrar una dependencia dejaba sus pasos fuera de toda
    bandeja; `to_dept` es hoy solo la instantánea legible. Ambas tablas
    viven en el MISMO schema de tenant que `flow_steps`
    (ADR-002/012), igual que `auth_memberships`/`role_clearance` en
    `resolve_caller_clearance` — sin llamada HTTP a otro servicio.

    NULL-safe por construcción: si `$idx` es NULL (llamante sin `X-User-Id`),
    ambas ramas quedan sin match (`assigned_to = NULL` y el `IN` sobre un
    subquery sin filas) y el predicado entero es NULL → ninguna fila pasa el
    `WHERE` (fail-closed), sin necesidad de una rama explícita.

    Inline, sin try/except UndefinedTableError: mismo criterio que
    `_clearance_condition`, que ya referencia sin protección la tabla
    `radicados` de otro servicio — ambas asumen el schema del tenant
    totalmente provisionado.
    """
    return f"(assigned_to = ${idx} OR to_dept_code IN ({_user_depts_subquery(idx)}))"


def _no_terminal_condition(tabla: str = "flow_steps") -> str:
    """Excluye de los listados los pasos activos de radicados terminales
    (`closed`/`anulado`, ADR-028). Un paso activo bajo un terminal es un zombi
    (p. ej. una anulación o un cierre manual anteriores al ADR): no se puede
    cerrar ni tramitar, así que mostrarlo en bandeja/vencidos solo estorba.
    Defensa en profundidad: la anulación ya cancela el paso y la migración 015
    saneó los existentes. Un radicado sin fila en `radicados` NO se excluye
    (mismo criterio que `_clearance_condition`)."""
    return (
        "NOT EXISTS (SELECT 1 FROM radicados rt "
        f"WHERE rt.id = {tabla}.radicado_id AND rt.status IN ('closed', 'anulado'))"
    )


# Clase del cerrojo de ALCANCE (par de enteros de `pg_advisory_xact_lock`, un
# espacio de claves distinto del cerrojo por radicado, que usa la forma de un
# solo `bigint`). Ver `lock_radicado` y `reassign_cascade`.
_LOCK_CLASE_ALCANCE = 28_000


@dataclass(slots=True)
class Tenencia:
    """Quién posee el paso activo de un radicado, visto desde un actor."""

    hay_activo: bool
    poseedor: bool


@dataclass(slots=True)
class EstadoCambio:
    """Un cambio de `radicados.status` producido por un acto del flujo."""

    radicado_id: uuid.UUID
    tracking_number: str
    antes: str
    despues: str
    flow_event_id: uuid.UUID | None


@dataclass(slots=True)
class CascadeResult:
    reassigned: int
    skipped_terminal: int = 0
    cambios: list[EstadoCambio] = field(default_factory=list)


class WorkflowRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        # Id del último `flow_events` escrito por esta instancia (lo lee el
        # servicio para la clave de dedup de `estado_cambiado`).
        self.last_flow_event_id: uuid.UUID | None = None

    async def _set_search_path(self) -> None:
        schema = tenant_schema(self._tenant_slug)  # valida + normaliza (fail-closed)
        await self._conn.execute(f'SET search_path TO "{schema}", public')

    async def caller_clearance(self, user_id: uuid.UUID | None) -> int:
        """Clearance del llamante, resuelto tras fijar el search_path del tenant."""
        await self._set_search_path()
        return await resolve_caller_clearance(self._conn, user_id)

    async def nivel_radicado(self, radicado_id: uuid.UUID) -> int | None:
        """`nivel_seguridad` del radicado para el sobre de los eventos que salen.

        `None` si no se puede resolver: el consumidor (notification) trata un
        evento de radicado sin nivel como clasificado (fail-closed), así que la
        ausencia aquí jamás vuelve público algo.
        """
        try:
            nivel = await self._conn.fetchval(
                "SELECT nivel_seguridad FROM radicados WHERE id = $1", radicado_id
            )
        except Exception:  # noqa: BLE001 — best-effort, el evento es posterior al commit.
            return None
        return nivel if isinstance(nivel, int) and not isinstance(nivel, bool) else None

    async def lock_radicado(self, radicado_id: uuid.UUID) -> None:
        """Serializa los actos sobre un radicado dentro de la transacción.

        Tres cerrojos, siempre en este orden:
          1. advisory de ALCANCE del tenant, COMPARTIDO: los actos de distintos
             radicados no se estorban. Existe para que la cascada
             (`reassign_cascade`) lo tome EXCLUSIVO una sola vez en lugar de un
             cerrojo por radicado: cada `pg_advisory_xact_lock` ocupa una ranura de
             la tabla de cerrojos (`max_locks_per_transaction`) y un barrido de
             cientos de radicados la agotaba.
          2. advisory del RADICADO, exclusivo: protege la SECUENCIA de pasos (no hay
             una fila natural que bloquear para eso).
          3. fila del radicado (`FOR UPDATE`): serializa con quien escribe el
             radicado sin pasar por el advisory (la anulación de document-service).
             Todos los escritores toman «fila del radicado, luego sus pasos»: sin
             ciclos de interbloqueo.
        Se liberan solos al terminar la transacción.
        """
        await self._set_search_path()
        # La fila va como subconsulta (no un `execute` aparte) para no añadir un
        # viaje por acto; sin fila (radicado inexistente) no bloquea nada.
        await self._conn.execute(
            "SELECT pg_advisory_xact_lock_shared($1, hashtext(current_schema())), "
            "pg_advisory_xact_lock(hashtext($2)), "
            "(SELECT 1 FROM radicados WHERE id = $3 FOR UPDATE)",
            _LOCK_CLASE_ALCANCE,
            str(radicado_id),
            radicado_id,
        )

    async def sincronizar_estado_radicado(
        self, radicado_id: uuid.UUID, *, finalizar: bool = False
    ) -> tuple[str, str] | None:
        """Escribe `radicados.status` según el paso activo (ADR-028) y la ubicación
        actual `flujo_*` (ADR-029, en el MISMO `UPDATE`); devuelve
        `(antes, despues)`; `None` si el radicado no existe.

        Debe llamarse DENTRO de la transacción del acto, DESPUÉS de mutar los
        pasos: lee el estado ya mutado y bloquea la fila del radicado
        (`FOR UPDATE`) hasta el commit. NO decide la política de terminales: si
        `antes` es terminal no escribe nada y lo devuelve tal cual, para que el
        servicio responda 409 y revierta el acto entero.

        El `UPDATE ... WHERE status IS DISTINCT FROM` es idempotente y no toca
        `updated_at` si nada cambia.
        """
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "SELECT status, flujo_dept, flujo_dept_code, flujo_usuario "
            "FROM radicados WHERE id = $1 FOR UPDATE",
            radicado_id,
        )
        if row is None:
            return None
        actual: str = row["status"]
        pasos = await self._conn.fetchrow(
            """
            SELECT (SELECT status FROM flow_steps
                     WHERE radicado_id = $1 AND status IN ('pending', 'in_progress')
                     ORDER BY step_number DESC LIMIT 1) AS activo,
                   EXISTS (SELECT 1 FROM flow_steps WHERE radicado_id = $1) AS tiene_pasos
            """,
            radicado_id,
        )
        despues = derivar_estado_radicado(
            actual,
            pasos["activo"] if pasos else None,
            tiene_pasos=bool(pasos and pasos["tiene_pasos"]),
            finalizar=finalizar,
        )
        if actual in ESTADOS_TERMINALES:
            # Terminal: ni estado ni ubicación se mueven (el servicio responde 409).
            return actual, despues

        # Ubicación actual (ADR-029): MISMO UPDATE que `status`, misma transacción.
        ubicacion = derivar_ubicacion(
            actual,
            await self._pasos_para_ubicacion(radicado_id),
            congelada=_ubicacion_de_fila(row),
        )
        await self._conn.execute(
            "UPDATE radicados SET status = $2, flujo_dept = $3, flujo_dept_code = $4, "
            "flujo_usuario = $5, updated_at = now() "
            "WHERE id = $1 AND (status IS DISTINCT FROM $2 "
            "OR flujo_dept IS DISTINCT FROM $3 "
            "OR flujo_dept_code IS DISTINCT FROM $4 "
            "OR flujo_usuario IS DISTINCT FROM $5)",
            radicado_id,
            despues,
            ubicacion.dept if ubicacion else None,
            ubicacion.dept_code if ubicacion else None,
            ubicacion.usuario if ubicacion else None,
        )
        return actual, despues

    async def _pasos_para_ubicacion(self, radicado_id: uuid.UUID) -> list[PasoUbicacion]:
        """Pasos del radicado con el CÓDIGO de dependencia que cada uno guarda
        (`to_dept_code`, ADR-032); `to_dept` es solo la instantánea del nombre."""
        rows = await self._conn.fetch(
            """
            SELECT s.step_number, s.status, s.to_dept, s.to_dept_code, s.assigned_to,
                   s.completed_at
              FROM flow_steps s
             WHERE s.radicado_id = $1
            """,
            radicado_id,
        )
        return [
            PasoUbicacion(
                step_number=r["step_number"],
                status=r["status"],
                to_dept=r["to_dept"],
                assigned_to=r["assigned_to"],
                completed_at=r["completed_at"],
                to_dept_code=r["to_dept_code"],
            )
            for r in rows
        ]

    async def es_poseedor(
        self, radicado_id: uuid.UUID, actor: uuid.UUID | None, *, max_clearance: int
    ) -> bool:
        """¿`actor` posee el paso activo del radicado? (`_holder_condition`) y el
        radicado está a su alcance (`_nivel_poseedor_condition`)."""
        await self._set_search_path()
        return bool(
            await self._conn.fetchval(
                "SELECT 1 FROM flow_steps WHERE radicado_id = $1 "
                "AND status IN ('pending', 'in_progress') "
                f"AND {_holder_condition(2)} AND {_nivel_poseedor_condition(3)} LIMIT 1",
                radicado_id,
                actor,
                max_clearance,
            )
        )

    async def tenencia(self, radicado_id: uuid.UUID, actor: uuid.UUID | None) -> Tenencia:
        """Si hay paso activo y si `actor` lo posee, en UNA lectura (misma
        `_holder_condition` que `es_poseedor`: una sola definición de poseedor)."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "SELECT EXISTS (SELECT 1 FROM flow_steps WHERE radicado_id = $1 "
            "                AND status IN ('pending', 'in_progress')) AS hay_activo, "
            "       EXISTS (SELECT 1 FROM flow_steps WHERE radicado_id = $1 "
            "                AND status IN ('pending', 'in_progress') "
            f"               AND {_holder_condition(2)}) AS poseedor",
            radicado_id,
            actor,
        )
        return Tenencia(
            hay_activo=bool(row and row["hay_activo"]),
            poseedor=bool(row and row["poseedor"]),
        )

    async def clearance_para_acto(self, actor: uuid.UUID | None) -> int:
        """Clearance REAL del actor de un acto de trámite (RF-SEG-08).

        Distinto de `CLEARANCE_SIN_RESTRICCION` (la precondición interna de
        `rollback`/`devolver`, que se anclan a una tenencia YA existente): abrir o
        mover un paso sobre un radicado NO es un acto de quien ya lo tiene, así que
        el no-read-up/no-write-up aplica. Fail-closed a PUBLICA sin identidad."""
        await self._set_search_path()
        return await resolve_caller_clearance(self._conn, actor)

    async def get_active_step_for_update(
        self, radicado_id: uuid.UUID, *, max_clearance: int
    ) -> FlowStep | None:
        """Paso activo del radicado (acto de tenencia), con el no-read-up del
        actor en el `WHERE`: un radicado por encima de su clearance es «sin paso»."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "SELECT * FROM flow_steps WHERE radicado_id = $1 "
            "AND status IN ('pending', 'in_progress') "
            f"AND {_nivel_poseedor_condition(2)} "
            "ORDER BY step_number DESC LIMIT 1 FOR UPDATE",
            radicado_id,
            max_clearance,
        )
        return _row_to_model(row) if row is not None else None

    async def ultimo_paso_si_lo_poseia(
        self, radicado_id: uuid.UUID, actor: uuid.UUID | None, *, max_clearance: int
    ) -> FlowStep | None:
        """Último paso del radicado SI ya está `completed` y `actor` era su poseedor
        (`_holder_condition`), el radicado no es terminal y está a su alcance
        (`max_clearance`); si no, `None`. Es lo que permite «Finalizar» a quien
        completó su paso y no repartió el radicado a nadie más: exigir un paso activo
        obligaba a inventar un reparto ficticio para poder cerrar."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "SELECT * FROM (SELECT * FROM flow_steps WHERE radicado_id = $1 "
            "ORDER BY step_number DESC LIMIT 1) ultimo "
            "WHERE ultimo.status = 'completed' "
            f"AND {_holder_condition(2, 'ultimo.')} "
            f"AND {_no_terminal_condition('ultimo')} "
            f"AND {_nivel_poseedor_condition(3, 'ultimo')}",
            radicado_id,
            actor,
            max_clearance,
        )
        return _row_to_model(row) if row is not None else None

    async def puede_repartir_sin_paso_activo(
        self, radicado_id: uuid.UUID, actor: uuid.UUID | None, *, max_clearance: int
    ) -> bool:
        """Quién puede repartir un radicado que NO tiene paso activo (`assign`/
        `transfer`; el reparto de sistema no pasa por aquí).

        - Con historial (todo completado, sin cerrar): solo quien poseía el último
          paso (`ultimo_paso_si_lo_poseia`: misma definición de poseedor, no
          terminal, clearance en el `WHERE`).
        - Sin ningún paso (nadie lo ha repartido): solo quien lo radicó,
          `radicados.registered_by = actor` (id interno de `auth_users`, el mismo
          `X-User-Id` que document-service guarda al radicar), con clearance.
        Sin `actor` ninguna rama casa (NULL-safe, fail-closed). Se llama tras
        `lock_radicado`."""
        await self._set_search_path()
        if await self._conn.fetchval(
            "SELECT 1 FROM flow_steps WHERE radicado_id = $1 LIMIT 1", radicado_id
        ):
            return (
                await self.ultimo_paso_si_lo_poseia(radicado_id, actor, max_clearance=max_clearance)
                is not None
            )
        return bool(
            await self._conn.fetchval(
                "SELECT 1 FROM radicados WHERE id = $1 AND registered_by = $2 "
                "AND nivel_seguridad <= $3",
                radicado_id,
                actor,
                max_clearance,
            )
        )

    async def contexto_cierre(self, radicado_id: uuid.UUID) -> dict[str, bool | None]:
        """Hechos del radicado que el asiento de `finalizar` deja como evidencia.

        `tiene_respuesta`: existe un radicado de Salida que lo responde
        (`radicados.responde_a`, document-service). `en_expediente`: está vinculado
        a un expediente (`expediente_radicados`, archive-service). Ambas tablas
        viven en el mismo schema de tenant. Cada lectura va en su SAVEPOINT: si la
        tabla/columna aún no existe en este tenant (el otro servicio migra aparte)
        el resultado es `None` («no consta») y NO se aborta la transacción del acto.
        """
        await self._set_search_path()
        return {
            "tiene_respuesta": await self._leer_opcional(
                "SELECT EXISTS (SELECT 1 FROM radicados WHERE responde_a = $1 "
                "AND doc_type = 'S' AND status <> 'anulado')",
                radicado_id,
            ),
            "en_expediente": await self._leer_opcional(
                "SELECT EXISTS (SELECT 1 FROM expediente_radicados "
                "WHERE radicado_id = $1 AND estado = 'vinculado')",
                radicado_id,
            ),
            # Vencimiento LEGAL del radicado (días hábiles, document-service). El
            # plazo de un paso es interno y casi nunca existe: medido en la pila,
            # el corpus entero tenía `flow_steps.due_at` NULL y el asiento decía
            # «no consta» sobre radicados vencidos hacía dos semanas.
            "radicado_vencido": await self._leer_opcional(
                "SELECT due_date < CURRENT_DATE FROM radicados WHERE id = $1",
                radicado_id,
            ),
        }

    async def _leer_opcional(self, sql: str, *args: Any) -> bool | None:
        try:
            async with self._conn.transaction():
                valor = await self._conn.fetchval(sql, *args)
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
            return None
        return None if valor is None else bool(valor)

    async def apply_finalizar(
        self,
        *,
        current: FlowStep,
        actor: uuid.UUID | None,
        comentario: str,
        motivo_cierre: str,
    ) -> FlowStep:
        """Cierra el paso activo como `completed` y deja su evento `finalizar`,
        atómicamente (ADR-028). Si `current` ya está cerrado (el actor completó su
        paso y no repartió) no hay nada que cerrar: solo se deja el evento. El
        estado del radicado lo escribe después
        `sincronizar_estado_radicado(finalizar=True)`."""
        await self._set_search_path()
        now = datetime.now(tz=UTC)
        async with self._conn.transaction():
            if current.status in ("pending", "in_progress"):
                row = await self._conn.fetchrow(
                    "UPDATE flow_steps SET status = 'completed', completed_at = $1 "
                    "WHERE id = $2 RETURNING *",
                    now,
                    current.id,
                )
            else:
                row = None
            await self._insert_flow_event(
                radicado_id=current.radicado_id,
                tracking_number=current.tracking_number,
                tipo_tx="finalizar",
                from_dept=current.to_dept,
                to_dept=current.to_dept,
                from_dept_code=current.to_dept_code,
                to_dept_code=current.to_dept_code,
                actor=actor,
                comentario=comentario,
                detalles={"step_number": current.step_number, "motivo_cierre": motivo_cierre},
            )
        return _row_to_model(row) if row is not None else current

    async def paso_activo(self, radicado_id: uuid.UUID) -> FlowStep | None:
        """Paso activo (pending/in_progress) del radicado, sin filtro de clearance.

        Solo para DERIVAR el origen de un paso nuevo, dentro del cerrojo del radicado y
        DESPUÉS de que el acto superó su propio gate de tenencia y clearance: no se
        devuelve al cliente."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "SELECT * FROM flow_steps WHERE radicado_id = $1 "
            "AND status IN ('pending', 'in_progress') "
            "ORDER BY step_number DESC LIMIT 1",
            radicado_id,
        )
        return _row_to_model(row) if row is not None else None

    async def get_next_step_number(self, radicado_id: uuid.UUID) -> int:
        await self._set_search_path()
        val = await self._conn.fetchval(
            "SELECT COALESCE(MAX(step_number), 0) + 1 FROM flow_steps WHERE radicado_id = $1",
            radicado_id,
        )
        return val

    async def get_radicado_tracking_number(
        self, radicado_id: uuid.UUID, *, max_clearance: int
    ) -> str | None:
        """Resuelve el `tracking_number` REAL desde `radicados` (Bloqueante 3) y, en
        la MISMA lectura, aplica el no-read-up (RF-SEG-08): el `nivel_seguridad`
        va en el `WHERE`, no en una comprobación previa. Se llama DESPUÉS de
        `lock_radicado`, que ya retiene la fila (`FOR UPDATE`): una reclasificación
        concurrente (document-service) espera, y no se abre un paso sobre un radicado
        que acaba de dejar de estar al alcance.

        `_create_step` (assign/transfer) nunca debe confiar en el `tracking_number`
        del cuerpo: es un campo libre del cliente que termina en `flow_events`
        (append-only) y en `public.audit_log.object_ref` sin más verificación.
        `None` = el radicado no existe O queda por encima del clearance del
        llamante (mismo resultado a propósito: sin oráculo; el llamante responde 404).
        """
        await self._set_search_path()
        return await self._conn.fetchval(
            "SELECT tracking_number FROM radicados "
            "WHERE id = $1 AND nivel_seguridad <= $2",
            radicado_id,
            max_clearance,
        )

    async def dependencia_activa(self, codigo: int) -> str | None:
        """Nombre del catálogo de la dependencia ACTIVA con este CÓDIGO, o `None`.

        `_create_step` (assign/transfer) no debe confiar en el destino como dato
        libre del cliente: un código inexistente crea un paso que no coincide con
        el alcance de NADIE (`pending` en BD pero invisible en toda bandeja —
        pérdida silenciosa de un trámite oficial). Devuelve el NOMBRE y no un
        booleano porque la instantánea que se guarda (`to_dept`) la pone el
        servidor desde el catálogo, nunca el texto del cliente (ADR-032).

        `dependencias` vive en el MISMO schema de tenant que `flow_steps`
        (ADR-002/012) — sin llamada HTTP a otro servicio. Una dependencia INACTIVA
        cuenta como inexistente a propósito: un paso hacia una dependencia dada de
        baja es igual de invisible en la práctica (nadie tiene URD activa en ella).
        """
        await self._set_search_path()
        nombre = await self._conn.fetchval(
            "SELECT nombre FROM dependencias WHERE codigo = $1 AND activo = TRUE", codigo
        )
        return str(nombre) if nombre is not None else None

    async def dependencia_existente(self, codigo: int) -> str | None:
        """Nombre del catálogo de la dependencia con este CÓDIGO, activa o no, o `None`.

        Para lo que CITA una dependencia sin asignarle trabajo (la condición de destino
        de una regla): una inactiva sigue siendo la misma dependencia."""
        await self._set_search_path()
        nombre = await self._conn.fetchval(
            "SELECT nombre FROM dependencias WHERE codigo = $1", codigo
        )
        return str(nombre) if nombre is not None else None

    async def resolver_alias(self, nombre: str, *, historico: bool = False) -> tuple[int | None, int]:
        """(código, candidatos) de un NOMBRE de dependencia, por el criterio ÚNICO de
        tenant-service 013 (`resolver_codigo_dependencia`).

        `historico=False` (alias de API): solo dependencias ACTIVAS con ese nombre
        ACTUAL. `historico=True` (eventos viejos, replay de la cola de fallidos):
        también inactivas, nombres anteriores y mapeos manuales. El código solo
        sale con EXACTAMENTE un candidato; 0 es huérfano y >=2 ambiguo: elegir uno
        sería inventar el destino.
        """
        await self._set_search_path()
        fila = await self._conn.fetchrow(
            "SELECT * FROM resolver_codigo_dependencia($1, $2)", nombre, historico
        )
        if fila is None:
            return None, 0
        return fila["codigo"], int(fila["candidatos"])

    async def user_exists(self, user_id: uuid.UUID) -> bool:
        """Existencia del destino de una reasignación (Hallazgo C).

        `reassign_cascade` aparca los pasos en `to_assigned_to` sin verificar
        nada: un UUID equivocado los deja `pending` pero invisibles en cualquier
        bandeja — denegación de servicio silenciosa del trámite.
        """
        await self._set_search_path()
        return bool(await self._conn.fetchval("SELECT 1 FROM auth_users WHERE id = $1", user_id))

    async def complete_active_step(self, radicado_id: uuid.UUID) -> None:
        """Mark the current pending/in_progress step as completed."""
        await self._set_search_path()
        await self._conn.execute(
            """
            UPDATE flow_steps
               SET status = 'completed', completed_at = $1
             WHERE radicado_id = $2
               AND status IN ('pending', 'in_progress')
            """,
            datetime.now(tz=UTC),
            radicado_id,
        )

    async def create(
        self,
        *,
        radicado_id: uuid.UUID,
        tracking_number: str,
        step_number: int,
        from_dept: str | None,
        to_dept: str,
        to_dept_code: int,
        from_dept_code: int | None = None,
        assigned_to: uuid.UUID | None,
        assigned_by: uuid.UUID,
        action: str,
        notes: str | None,
        doc_type: str | None = None,
        due_at: datetime | None = None,
    ) -> FlowStep:
        await self._set_search_path()
        # El paso (proyección) y su evento append-only se escriben atómicamente:
        # no hay paso de trámite sin su registro inalterable (RF-FLU-02).
        async with self._conn.transaction():
            row = await self._conn.fetchrow(
                """
                INSERT INTO flow_steps (
                    radicado_id, tracking_number, step_number,
                    from_dept, to_dept, assigned_to, assigned_by,
                    action, notes, status, doc_type, due_at,
                    to_dept_code, from_dept_code
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'pending', $10, $11, $12, $13)
                RETURNING *
                """,
                radicado_id,
                tracking_number,
                step_number,
                from_dept,
                to_dept,
                assigned_to,
                assigned_by,
                action,
                notes,
                doc_type,
                due_at,
                to_dept_code,
                from_dept_code,
            )
            await self._insert_flow_event(
                radicado_id=radicado_id,
                tracking_number=tracking_number,
                tipo_tx=action,
                from_dept=from_dept,
                to_dept=to_dept,
                from_dept_code=from_dept_code,
                to_dept_code=to_dept_code,
                actor=assigned_by,
                comentario=notes,
                detalles={"step_number": step_number},
            )
        return _row_to_model(row)

    async def _insert_flow_event(
        self,
        *,
        radicado_id: uuid.UUID,
        tracking_number: str,
        tipo_tx: str,
        from_dept: str | None,
        to_dept: str | None,
        actor: uuid.UUID | None,
        comentario: str | None,
        detalles: dict[str, Any],
        from_dept_code: int | None = None,
        to_dept_code: int | None = None,
    ) -> uuid.UUID:
        # Asume search_path ya fijado por el método llamante. El id se genera aquí
        # (no con RETURNING) para poder devolverlo sin cambiar la forma de la
        # sentencia: el evento `estado_cambiado` lo usa en su clave de dedup.
        event_id = uuid.uuid4()
        await self._conn.execute(
            """
            INSERT INTO flow_events (
                radicado_id, tracking_number, tipo_tx,
                from_dept, to_dept, actor, comentario, detalles, id,
                from_dept_code, to_dept_code
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11)
            """,
            radicado_id,
            tracking_number,
            tipo_tx,
            from_dept,
            to_dept,
            actor,
            comentario,
            json.dumps(detalles or {}),
            event_id,
            from_dept_code,
            to_dept_code,
        )
        self.last_flow_event_id = event_id
        return event_id

    async def list_flow_events(
        self, radicado_id: uuid.UUID, max_clearance: int = 3
    ) -> list[asyncpg.Record]:
        # Canal B (E08 §9): la hoja de ruta solo es visible si el radicado lo es para el
        # clearance del llamante (no read-up, RF-SEG-08). El nivel del radicado vive en la
        # tabla `radicados` (document-service, mismo schema tenant, NOT NULL); COALESCE a
        # CLASIFICADA(3) si no hay fila: ausente = purgado por TRD con huérfanos, nunca
        # «sin clasificar». max_clearance=3 = sin restricción para llamantes internos/sistema.
        await self._set_search_path()
        return list(
            await self._conn.fetch(
                """
                SELECT id, radicado_id, tracking_number, tipo_tx,
                       from_dept, to_dept, from_dept_code, to_dept_code,
                       actor, comentario, detalles, ts
                FROM flow_events
                WHERE radicado_id = $1
                  AND COALESCE(
                        (SELECT nivel_seguridad FROM radicados WHERE id = $1), 3
                      ) <= $2
                ORDER BY ts ASC, id ASC
                """,
                radicado_id,
                max_clearance,
            )
        )

    async def get_history(self, radicado_id: uuid.UUID, *, max_clearance: int) -> list[FlowStep]:
        """Hoja de ruta completa de un radicado (RF-SEG-08, no-read-up).

        ``max_clearance`` es **keyword-only y obligatorio** (sin default peligroso,
        gemelo del patrón de archive-service): un radicado por encima del clearance
        del llamante devuelve una hoja de ruta VACÍA, no un error — no hay oráculo.
        Expuesto vía HTTP (`GET /{radicado_id}/history`), sin más llamador interno.
        """
        await self._set_search_path()
        rows = await self._conn.fetch(
            f"""
            SELECT * FROM flow_steps
             WHERE radicado_id = $1
               AND {_clearance_condition(2)}
             ORDER BY step_number ASC
            """,
            radicado_id,
            max_clearance,
        )
        return [_row_to_model(r) for r in rows]

    async def get_current(self, radicado_id: uuid.UUID, *, max_clearance: int) -> FlowStep | None:
        """Paso activo de un radicado (RF-SEG-08, no-read-up).

        ``max_clearance`` es **keyword-only y obligatorio**. Tiene DOS naturalezas de
        llamador (ver `app.core.clearance.CLEARANCE_SIN_RESTRICCION`):
          - `WorkflowService.get_current` (HTTP-facing, `GET /{radicado_id}/current`):
            SIEMPRE resuelve el clearance REAL del llamante.
          - `WorkflowService.rollback`/`devolver` (acción de trámite/tenencia, no
            lectura): pasan `CLEARANCE_SIN_RESTRICCION` a propósito — mover el
            radicado es ortogonal al clearance de lectura, la autorización real vive
            en la verificación de tenencia/estado de esos métodos.
        """
        await self._set_search_path()
        row = await self._conn.fetchrow(
            f"""
            SELECT * FROM flow_steps
             WHERE radicado_id = $1
               AND status IN ('pending', 'in_progress')
               AND {_clearance_condition(2)}
             ORDER BY step_number DESC
             LIMIT 1
            """,
            radicado_id,
            max_clearance,
        )
        return _row_to_model(row) if row is not None else None

    async def list_pending(
        self,
        *,
        dept_code: int | None,
        assigned_to: uuid.UUID | None,
        own_scope_user_id: uuid.UUID | None,
        max_clearance: int,
        offset: int,
        limit: int,
    ) -> tuple[list[FlowStep], int]:
        """``max_clearance`` es keyword-only y obligatorio (RF-SEG-08, no-read-up):
        un radicado clasificado por encima del clearance del llamante no aparece en
        la bandeja NI cuenta en el total — el predicado entra en el ``WHERE``
        compartido por el `COUNT(*)` y la página (`X-Total-Count` no debe delatar
        filas ocultas).

        ``own_scope_user_id``: cierre de la primitiva de enumeración (`GET
        /workflows/pending` sin acotar). El SERVICIO decide su valor: no
        ``None`` cuando la petición NO trae `dept_code`/`assigned_to` (alcance por
        defecto, `_own_scope_condition`); ``None`` cuando sí los trae (el
        servicio ya exigió `PERM_TRAMITAR`/`USUA_PERM_ADMIN` para ampliar el
        alcance) — ambos ejes son mutuamente excluyentes por request.
        """
        await self._set_search_path()

        conditions = ["status IN ('pending', 'in_progress')", _no_terminal_condition()]
        params: list[object] = []
        idx = 1

        if dept_code is not None:
            conditions.append(f"to_dept_code = ${idx}")
            params.append(dept_code)
            idx += 1

        if assigned_to is not None:
            conditions.append(f"assigned_to = ${idx}")
            params.append(assigned_to)
            idx += 1

        if dept_code is None and assigned_to is None:
            conditions.append(_own_scope_condition(idx))
            params.append(own_scope_user_id)
            idx += 1

        conditions.append(_clearance_condition(idx))
        params.append(max_clearance)
        idx += 1

        where = " AND ".join(conditions)

        total: int = await self._conn.fetchval(
            f"SELECT COUNT(*) FROM flow_steps WHERE {where}", *params
        )

        rows = await self._conn.fetch(
            f"""
            SELECT * FROM flow_steps
             WHERE {where}
             ORDER BY created_at ASC
             LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params,
            limit,
            offset,
        )
        return [_row_to_model(r) for r in rows], total

    async def get_by_id(self, step_id: uuid.UUID) -> FlowStep | None:
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "SELECT * FROM flow_steps WHERE id = $1",
            step_id,
        )
        return _row_to_model(row) if row is not None else None

    async def complete_step(
        self,
        step_id: uuid.UUID,
        notes: str | None,
        actor: uuid.UUID | None,
        *,
        max_clearance: int,
    ) -> FlowStep | None:
        """Cierra un paso, anclando autorización y mutación en el MISMO `UPDATE`
        (Bloqueantes 1+2): sin ventana TOCTOU entre "¿es tuyo?" y "ciérralo".

        Solo el POSEEDOR (`_holder_condition`: asignado a él, o paso de
        dependencia sin dueño —los que genera `apply_devolucion`— y él pertenece a
        ella) y solo un paso ACTIVO. Antes era `assigned_to = $4 OR assigned_to IS
        NULL` sin filtrar estado: cualquiera con PERM_TRAMITAR cerraba un paso sin
        dueño de OTRA dependencia, y un paso ya cerrado se «re-cerraba». Es la
        misma definición de poseedor que `finalizar` y `marcar_leido`. Si el
        `WHERE` no casa (paso ajeno, inexistente o ya cerrado), `row` es `None` y
        el llamante responde el MISMO 404 `step_not_found` en ambos casos (no-oráculo,
        gemelo de `rollback`/`devolver`). Idem si el radicado está por encima del
        clearance del actor (`_nivel_poseedor_condition`, en el mismo `WHERE`):
        no se distingue "no es tuyo" de "no existe".

        `actor` (el ejecutor real, no `assigned_to`) queda en `flow_events.actor`
        — antes se asentaba `row["assigned_to"]`, que en una tabla append-only
        con trigger anti-UPDATE/DELETE dejaba la traza acusando para siempre a
        la persona equivocada (o sin actor si `assigned_to` era NULL).
        """
        await self._set_search_path()
        now = datetime.now(tz=UTC)
        async with self._conn.transaction():
            row = await self._conn.fetchrow(
                f"""
                UPDATE flow_steps
                   SET status = 'completed',
                       completed_at = $1,
                       notes = COALESCE($2, notes)
                 WHERE id = $3
                   AND status IN ('pending', 'in_progress')
                   AND {_holder_condition(4)}
                   AND {_nivel_poseedor_condition(5)}
                RETURNING *
                """,
                now,
                notes,
                step_id,
                actor,
                max_clearance,
            )
            if row is None:
                return None
            await self._insert_flow_event(
                radicado_id=row["radicado_id"],
                tracking_number=row["tracking_number"],
                tipo_tx="complete",
                from_dept=row["to_dept"],
                to_dept=row["to_dept"],
                from_dept_code=row["to_dept_code"],
                to_dept_code=row["to_dept_code"],
                actor=actor,
                comentario=notes,
                detalles={"step_id": str(step_id)},
            )
        return _row_to_model(row)

    async def list_inbox(
        self,
        *,
        doc_type: str | None,
        dept_code: int | None,
        assigned_to: uuid.UUID | None,
        own_scope_user_id: uuid.UUID | None,
        max_clearance: int,
        offset: int,
        limit: int,
    ) -> tuple[list[FlowStep], int]:
        """Bandeja tipada: pasos activos filtrados por doc_type (E/S/I), dependencia
        y/o responsable, priorizados por antigüedad (RF-FLU-03).

        ``max_clearance`` es keyword-only y obligatorio (RF-SEG-08, no-read-up):
        mismo predicado y misma garantía COUNT==SELECT que `list_pending`.

        ``own_scope_user_id``: ver docstring de `list_pending` (gemelo exacto,
        `box`/`doc_type` es ortogonal al eje de alcance)."""
        await self._set_search_path()
        conditions = ["status IN ('pending', 'in_progress')", _no_terminal_condition()]
        params: list[object] = []
        idx = 1
        if doc_type is not None:
            conditions.append(f"doc_type = ${idx}")
            params.append(doc_type)
            idx += 1
        if dept_code is not None:
            conditions.append(f"to_dept_code = ${idx}")
            params.append(dept_code)
            idx += 1
        if assigned_to is not None:
            conditions.append(f"assigned_to = ${idx}")
            params.append(assigned_to)
            idx += 1
        if dept_code is None and assigned_to is None:
            conditions.append(_own_scope_condition(idx))
            params.append(own_scope_user_id)
            idx += 1
        conditions.append(_clearance_condition(idx))
        params.append(max_clearance)
        idx += 1
        where = " AND ".join(conditions)

        total: int = await self._conn.fetchval(
            f"SELECT COUNT(*) FROM flow_steps WHERE {where}", *params
        )
        rows = await self._conn.fetch(
            f"SELECT * FROM flow_steps WHERE {where} "
            f"ORDER BY created_at ASC LIMIT ${idx} OFFSET ${idx + 1}",
            *params,
            limit,
            offset,
        )
        return [_row_to_model(r) for r in rows], total

    async def list_overdue_unnotified(self) -> list[FlowStep]:
        """Pasos activos vencidos (due_at < now) aún no alertados (RF-FLU-07)."""
        await self._set_search_path()
        rows = await self._conn.fetch(
            """
            SELECT * FROM flow_steps
             WHERE status IN ('pending', 'in_progress')
               AND due_at IS NOT NULL AND due_at < now()
               AND overdue_notified = false
               AND """ + _no_terminal_condition() + """
             ORDER BY due_at ASC
            """
        )
        return [_row_to_model(r) for r in rows]

    async def mark_overdue_notified(self, step_ids: list[uuid.UUID]) -> None:
        if not step_ids:
            return
        await self._set_search_path()
        await self._conn.execute(
            "UPDATE flow_steps SET overdue_notified = true WHERE id = ANY($1::uuid[])",
            step_ids,
        )

    # ------------------------------------------------------------------
    # Transacciones de trámite (RF-FLU-01)
    # ------------------------------------------------------------------

    async def list_transaction_types(self) -> list[asyncpg.Record]:
        await self._set_search_path()
        return list(
            await self._conn.fetch(
                "SELECT code, nombre, descripcion, requires_permission, "
                "target_step_status, requires_holder, activo FROM transaction_types "
                "WHERE activo ORDER BY code"
            )
        )

    async def get_transaction_type(self, code: str) -> asyncpg.Record | None:
        await self._set_search_path()
        return await self._conn.fetchrow(
            "SELECT code, nombre, descripcion, requires_permission, "
            "target_step_status, requires_holder, activo FROM transaction_types "
            "WHERE code = $1",
            code,
        )

    async def get_latest_step(
        self, radicado_id: uuid.UUID, *, max_clearance: int
    ) -> FlowStep | None:
        """Último paso del radicado, con el no-read-up del actor en el `WHERE`:
        un radicado por encima de su clearance es «sin flujo», igual que uno
        inexistente (las transacciones sin `requires_holder` no leían nivel)."""
        await self._set_search_path()
        row = await self._conn.fetchrow(
            "SELECT * FROM flow_steps WHERE radicado_id = $1 "
            f"AND {_nivel_poseedor_condition(2)} "
            "ORDER BY step_number DESC LIMIT 1",
            radicado_id,
            max_clearance,
        )
        return _row_to_model(row) if row is not None else None

    async def apply_transaction(
        self,
        *,
        latest: FlowStep,
        tipo_tx: str,
        target_step_status: str | None,
        actor: uuid.UUID | None,
        comentario: str | None,
        detalles: dict[str, Any],
    ) -> asyncpg.Record:
        """Aplica una transacción de trámite: registra el evento append-only y, si
        corresponde, cambia el estado del paso activo, atómicamente (RF-FLU-01)."""
        await self._set_search_path()
        now = datetime.now(tz=UTC)
        async with self._conn.transaction():
            if target_step_status is not None and latest.status in (
                "pending",
                "in_progress",
            ):
                completed = now if target_step_status in ("cancelled", "completed") else None
                await self._conn.execute(
                    "UPDATE flow_steps SET status = $1, completed_at = $2 WHERE id = $3",
                    target_step_status,
                    completed,
                    latest.id,
                )
            event = await self._conn.fetchrow(
                """
                INSERT INTO flow_events (
                    radicado_id, tracking_number, tipo_tx,
                    from_dept, to_dept, actor, comentario, detalles,
                    from_dept_code, to_dept_code
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $9)
                RETURNING id, radicado_id, tracking_number, tipo_tx,
                          from_dept, to_dept, from_dept_code, to_dept_code,
                          actor, comentario, detalles, ts
                """,
                latest.radicado_id,
                latest.tracking_number,
                tipo_tx,
                latest.to_dept,
                latest.to_dept,
                actor,
                comentario,
                json.dumps(detalles or {}),
                latest.to_dept_code,
            )
        assert event is not None
        self.last_flow_event_id = event["id"]
        return event

    # ------------------------------------------------------------------
    # Rollback y reasignación en cascada (RF-FLU-08)
    # ------------------------------------------------------------------

    async def get_previous_step(
        self, radicado_id: uuid.UUID, before_step_number: int
    ) -> FlowStep | None:
        await self._set_search_path()
        row = await self._conn.fetchrow(
            """
            SELECT * FROM flow_steps
             WHERE radicado_id = $1 AND step_number < $2
             ORDER BY step_number DESC
             LIMIT 1
            """,
            radicado_id,
            before_step_number,
        )
        return _row_to_model(row) if row is not None else None

    async def apply_rollback(
        self,
        *,
        current: FlowStep,
        previous: FlowStep,
        actor: uuid.UUID | None,
        comentario: str | None = None,
        max_clearance: int,
    ) -> FlowStep | None:
        """Cancela el paso actual y reactiva el anterior, atómicamente (RF-FLU-08).

        Repite la condición de tenencia en el propio `UPDATE` (`pending` + poseedor
        + nivel): `None` si ya no la cumple (carrera con otro acto), y entonces no
        se toca nada.

        `comentario` es el motivo de la reversión (`RollbackRequest.motivo`,
        obligatorio desde el router) — queda en `flow_events.comentario`, gemelo
        de `apply_devolucion` (que ya recibe `comentario` para el mismo campo)."""
        await self._set_search_path()
        now = datetime.now(tz=UTC)
        async with self._conn.transaction():
            cancelado = await self._conn.fetchval(
                "UPDATE flow_steps SET status='cancelled', completed_at=$1 "
                "WHERE id=$2 AND status = 'pending' "
                f"AND {_holder_condition(3)} AND {_nivel_poseedor_condition(4)} "
                "RETURNING id",
                now,
                current.id,
                actor,
                max_clearance,
            )
            if cancelado is None:
                return None
            row = await self._conn.fetchrow(
                """
                UPDATE flow_steps SET status='pending', completed_at=NULL
                 WHERE id=$1 RETURNING *
                """,
                previous.id,
            )
            await self._insert_flow_event(
                radicado_id=current.radicado_id,
                tracking_number=current.tracking_number,
                tipo_tx="rollback",
                from_dept=current.to_dept,
                to_dept=previous.to_dept,
                from_dept_code=current.to_dept_code,
                to_dept_code=previous.to_dept_code,
                actor=actor,
                comentario=comentario,
                detalles={"cancelled_step": current.step_number},
            )
        return _row_to_model(row)

    async def apply_devolucion(
        self,
        *,
        current: FlowStep,
        to_dept: str,
        to_dept_code: int,
        causal: str,
        comentario: str | None,
        actor: uuid.UUID | None,
        max_clearance: int,
    ) -> FlowStep | None:
        """Devuelve el radicado al remitente/origen con causal (RF-FLU): cierra el
        paso actual como 'returned' y crea un paso nuevo hacia `to_dept`, atómicamente.
        Repite la condición de tenencia en el `UPDATE` (activo + poseedor + nivel):
        `None` si ya no la cumple, sin crear el paso nuevo."""
        await self._set_search_path()
        now = datetime.now(tz=UTC)
        async with self._conn.transaction():
            cerrado = await self._conn.fetchval(
                "UPDATE flow_steps SET status='returned', completed_at=$1 "
                "WHERE id=$2 AND status IN ('pending', 'in_progress') "
                f"AND {_holder_condition(3)} AND {_nivel_poseedor_condition(4)} "
                "RETURNING id",
                now,
                current.id,
                actor,
                max_clearance,
            )
            if cerrado is None:
                return None
            row = await self._conn.fetchrow(
                """
                INSERT INTO flow_steps (
                    radicado_id, tracking_number, step_number,
                    from_dept, to_dept, assigned_to, assigned_by, action, notes, status,
                    from_dept_code, to_dept_code
                ) VALUES ($1, $2, $3, $4, $5, NULL, $6, 'devolucion', $7, 'pending', $8, $9)
                RETURNING *
                """,
                current.radicado_id,
                current.tracking_number,
                current.step_number + 1,
                current.to_dept,
                to_dept,
                actor,
                comentario,
                current.to_dept_code,
                to_dept_code,
            )
            await self._insert_flow_event(
                radicado_id=current.radicado_id,
                tracking_number=current.tracking_number,
                tipo_tx="devolucion",
                from_dept=current.to_dept,
                to_dept=to_dept,
                from_dept_code=current.to_dept_code,
                to_dept_code=to_dept_code,
                actor=actor,
                comentario=comentario,
                detalles={"causal": causal, "step_number": current.step_number + 1},
            )
        return _row_to_model(row)

    async def reassign_cascade(
        self,
        *,
        from_assigned_to: uuid.UUID | None,
        from_dept_code: int | None,
        to_assigned_to: uuid.UUID,
        actor: uuid.UUID | None,
    ) -> CascadeResult:
        """Reasigna en una transacción todos los pasos activos del origen al nuevo
        responsable, sin dejar radicados huérfanos (RF-FLU-08).

        ADR-028: reasignar equivale a que el nuevo responsable NO ha leído el
        radicado (Orfeo `LEIDO=0`), así que el paso vuelve a `pending` y el
        radicado a `distributed`. Los radicados terminales (`closed`/`anulado`)
        se EXCLUYEN —ni se reasigna su paso ni se les toca el estado— y se
        cuentan en `skipped_terminal` (pasos omitidos). Concurrencia: un único
        cerrojo de alcance + cerrojos de fila ordenados (ver comentarios abajo)."""
        await self._set_search_path()
        conds = ["status IN ('pending', 'in_progress')"]
        params: list[object] = []
        idx = 1
        if from_assigned_to is not None:
            conds.append(f"assigned_to = ${idx}")
            params.append(from_assigned_to)
            idx += 1
        if from_dept_code is not None:
            conds.append(f"to_dept_code = ${idx}")
            params.append(from_dept_code)
            idx += 1
        where = " AND ".join(conds)

        result = CascadeResult(reassigned=0)
        async with self._conn.transaction():
            # UN solo cerrojo EXCLUSIVO de alcance, en vez de uno por radicado: cada
            # `pg_advisory_xact_lock` ocupa una ranura de la tabla de cerrojos
            # (`max_locks_per_transaction`, 64 por defecto y compartida) y un barrido
            # de cientos de radicados la agotaba a mitad de la reasignación. Los
            # actos de trámite toman el MISMO cerrojo en modo compartido
            # (`lock_radicado`), así que mientras dura la cascada ninguno corre, y los
            # que ya corrían terminaron antes de que ésta lo obtuviera: la lectura de
            # pasos de abajo es consistente. Orden fijo (alcance primero) => sin ciclos
            # con `lock_radicado`. Coste: la cascada, un acto administrativo raro,
            # detiene brevemente el trámite de ESE tenant; un barrido por lotes
            # habría dejado cada lote parcialmente aplicado y sin asiento atómico.
            await self._conn.execute(
                "SELECT pg_advisory_xact_lock($1, hashtext(current_schema()))",
                _LOCK_CLASE_ALCANCE,
            )
            # Cerrojo de FILA de los radicados (no ocupa ranuras: va en la tupla),
            # SIEMPRE en orden de id y también para los que aparezcan tras releer —
            # `document-service` (anulación) no usa el cerrojo advisory y esto es lo
            # que lo serializa con la cascada.
            estados: dict[uuid.UUID, str] = {}
            while True:
                rows = await self._conn.fetch(
                    f"SELECT * FROM flow_steps WHERE {where} "
                    "ORDER BY radicado_id, step_number",
                    *params,
                )
                nuevos = sorted({r["radicado_id"] for r in rows} - estados.keys(), key=str)
                if not nuevos:
                    break
                for fila in await self._conn.fetch(
                    "SELECT id, status FROM radicados WHERE id = ANY($1::uuid[]) "
                    "ORDER BY id FOR UPDATE",
                    nuevos,
                ):
                    estados[fila["id"]] = fila["status"]
                for rid in nuevos:  # sin fila en `radicados`: no es terminal
                    estados.setdefault(rid, "")
            por_radicado: dict[uuid.UUID, list[asyncpg.Record]] = {}
            for r in rows:
                por_radicado.setdefault(r["radicado_id"], []).append(r)
            for rid in sorted(por_radicado, key=str):
                pasos = por_radicado[rid]
                if estados.get(rid) in ESTADOS_TERMINALES:
                    result.skipped_terminal += len(pasos)
                    continue
                last_event: uuid.UUID | None = None
                for r in pasos:
                    await self._conn.execute(
                        "UPDATE flow_steps SET assigned_to=$1, status='pending' WHERE id=$2",
                        to_assigned_to,
                        r["id"],
                    )
                    last_event = await self._insert_flow_event(
                        radicado_id=r["radicado_id"],
                        tracking_number=r["tracking_number"],
                        tipo_tx="reassign",
                        from_dept=r["to_dept"],
                        to_dept=r["to_dept"],
                        from_dept_code=r["to_dept_code"],
                        to_dept_code=r["to_dept_code"],
                        actor=actor,
                        comentario=None,
                        detalles={
                            "from_assigned_to": (
                                str(r["assigned_to"]) if r["assigned_to"] else None
                            ),
                            "to_assigned_to": str(to_assigned_to),
                        },
                    )
                    result.reassigned += 1
                estado = await self.sincronizar_estado_radicado(rid)
                if estado is not None and estado[0] != estado[1]:
                    result.cambios.append(
                        EstadoCambio(
                            radicado_id=rid,
                            tracking_number=pasos[0]["tracking_number"],
                            antes=estado[0],
                            despues=estado[1],
                            flow_event_id=last_event,
                        )
                    )
        return result


def _ubicacion_de_fila(row: asyncpg.Record) -> Ubicacion | None:
    """`flujo_*` ya escritas en el radicado, o `None` si nunca se escribieron."""
    if row["flujo_dept"] is None:
        return None
    return Ubicacion(
        dept=row["flujo_dept"],
        dept_code=row["flujo_dept_code"],
        usuario=row["flujo_usuario"],
    )


def _row_to_model(row: asyncpg.Record) -> FlowStep:
    return FlowStep(
        id=row["id"],
        radicado_id=row["radicado_id"],
        tracking_number=row["tracking_number"],
        step_number=row["step_number"],
        from_dept=row["from_dept"],
        to_dept=row["to_dept"],
        assigned_to=row["assigned_to"],
        assigned_by=row["assigned_by"],
        action=row["action"],
        notes=row["notes"],
        status=row["status"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
        doc_type=row["doc_type"],
        due_at=row["due_at"],
        overdue_notified=row["overdue_notified"],
        to_dept_code=row["to_dept_code"],
        from_dept_code=row["from_dept_code"],
    )
