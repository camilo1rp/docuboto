# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lógica de negocio del PINAR (RF-ADM-08, E14) — Acuerdo AGN 003/2015.

Reglas / invariantes (spec §10.3):
1. A lo sumo un plan en 'en_ejecucion' por tenant (índice único parcial en BD).
   Reformular = crear una versión nueva (version = max+1), preservando el histórico.
2. Priorización determinista (Σ impactos) — se calcula en el repositorio.
3. Seguimiento append-only (trigger en BD); avance del proyecto proyectado del último.
4. Aprobación exige acto administrativo y queda en admin_audit (E08).
5. Articulación por referencia, no por copia.

Estados y transiciones:
    borrador --aprobar--> aprobado --ejecutar--> en_ejecucion --cerrar--> cerrado
    (cerrar también desde aprobado). 'cerrado' es terminal.
La transición aprobado→en_ejecucion ('ejecutar') NO está enumerada en §10.2 pero es
la única forma de alcanzar el estado 'en_ejecucion' que la spec define; se añade para
hacer el invariante 1 verificable. Edición estructural permitida en {borrador, aprobado};
seguimiento permitido en {aprobado, en_ejecucion}.
"""

import uuid
from typing import Any

import asyncpg
from orpycamcp_common import audit

from app.repositories.pinar_repository import PinarRepository

# El contenido se CONGELA al aprobar (acto administrativo): tras 'aprobado' no se
# edita la estructura, solo su seguimiento. Preserva la correspondencia acto↔contenido
# (Ac. AGN 003/2015; hallazgo de conformidad #10 + agravante de seguridad #1).
_EDITABLE = {"borrador"}
_TRACKABLE = {"aprobado", "en_ejecucion"}

_SERVICE = "tenant-service"


class PinarNotFound(Exception):
    def __init__(self, detail: str = "PINAR resource not found") -> None:
        self.detail = detail
        super().__init__(detail)


class PinarConflict(Exception):
    def __init__(self, error: str, detail: str) -> None:
        self.error = error
        self.detail = detail
        super().__init__(detail)


class PinarService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str | None) -> None:
        self._conn = conn
        self._slug = tenant_slug
        self._repo = PinarRepository(conn)

    async def _audit_log(
        self,
        *,
        action: str,
        object_ref: str,
        actor: uuid.UUID | None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Asiento en la audit_log INMUTABLE (E08, hash-chain) para los actos
        oficiales del ciclo de vida del plan (spec §10.3.4). Corre dentro de la
        transacción del acto → un fallo revierte también el asiento."""
        await audit.append(
            self._conn,
            tenant_slug=self._slug,
            service=_SERVICE,
            action=action,
            object_type="pinar_plan",
            object_ref=object_ref,
            actor=str(actor) if actor else None,
            payload=payload or {},
        )

    # --- helpers -----------------------------------------------------------
    async def _plan_or_404(self, plan_id: uuid.UUID) -> asyncpg.Record:
        plan = await self._repo.get_plan(plan_id)
        if plan is None:
            raise PinarNotFound(f"PINAR plan {plan_id} not found")
        return plan

    def _assert_editable(self, plan: asyncpg.Record) -> None:
        if plan["estado"] not in _EDITABLE:
            raise PinarConflict(
                "plan_no_editable",
                f"El plan en estado '{plan['estado']}' no admite cambios estructurales "
                "(reformule creando una versión nueva).",
            )

    def _assert_trackable(self, plan: asyncpg.Record) -> None:
        if plan["estado"] not in _TRACKABLE:
            raise PinarConflict(
                "plan_no_seguible",
                f"El plan en estado '{plan['estado']}' no admite seguimiento.",
            )

    # --- plan --------------------------------------------------------------
    async def create_plan(self, data: dict[str, Any], actor: uuid.UUID | None) -> asyncpg.Record:
        async with self._conn.transaction():
            version = await self._repo.max_version() + 1
            plan = await self._repo.create_plan(
                version=version,
                vision=data.get("vision"),
                vigencia_desde=data.get("vigencia_desde"),
                vigencia_hasta=data.get("vigencia_hasta"),
                acto_administrativo=data.get("acto_administrativo"),
            )
            await self._audit_log(
                action="pinar.plan_creado",
                object_ref=str(plan["id"]),
                actor=actor,
                payload={"version": version, "estado": "borrador"},
            )
            return plan

    async def get_plan(self, plan_id: uuid.UUID) -> asyncpg.Record:
        return await self._plan_or_404(plan_id)

    async def list_plans(self, estado: str | None) -> list[asyncpg.Record]:
        return await self._repo.list_plans(estado)

    async def update_plan(
        self, plan_id: uuid.UUID, fields: dict[str, Any], actor: uuid.UUID | None
    ) -> asyncpg.Record:
        plan = await self._plan_or_404(plan_id)
        if plan["estado"] != "borrador":
            raise PinarConflict(
                "plan_no_borrador",
                "La visión/vigencia solo se edita en estado 'borrador'.",
            )
        async with self._conn.transaction():
            updated = await self._repo.update_plan_fields(plan_id, fields)
            await self._audit_log(
                action="pinar.plan_actualizado",
                object_ref=str(plan_id),
                actor=actor,
                payload={
                    "old": {k: str(plan[k]) for k in fields if k in plan},
                    "new": {k: str(v) for k, v in fields.items()},
                },
            )
            return updated  # type: ignore[return-value]

    async def aprobar(
        self, plan_id: uuid.UUID, acto_administrativo: str, actor: uuid.UUID | None
    ) -> asyncpg.Record:
        await self._plan_or_404(plan_id)
        async with self._conn.transaction():
            updated = await self._repo.set_estado(
                plan_id,
                nuevo="aprobado",
                expected="borrador",
                acto_administrativo=acto_administrativo,
                aprobado_por=actor,
            )
            if updated is None:
                raise PinarConflict(
                    "transicion_invalida",
                    "Solo un plan en 'borrador' puede aprobarse.",
                )
            await self._audit_log(
                action="pinar.plan_aprobado",
                object_ref=str(plan_id),
                actor=actor,
                payload={"estado": "aprobado", "acto_administrativo": acto_administrativo},
            )
            return updated

    async def ejecutar(self, plan_id: uuid.UUID, actor: uuid.UUID | None) -> asyncpg.Record:
        await self._plan_or_404(plan_id)
        try:
            async with self._conn.transaction():
                updated = await self._repo.set_estado(
                    plan_id, nuevo="en_ejecucion", expected="aprobado"
                )
                if updated is None:
                    raise PinarConflict(
                        "transicion_invalida",
                        "Solo un plan 'aprobado' puede pasar a ejecución.",
                    )
                await self._audit_log(
                    action="pinar.plan_en_ejecucion",
                    object_ref=str(plan_id),
                    actor=actor,
                    payload={"estado": "en_ejecucion"},
                )
                return updated
        except asyncpg.UniqueViolationError:
            # Invariante 1: ya hay un plan en 'en_ejecucion'.
            raise PinarConflict(
                "plan_en_ejecucion_existe",
                "Ya existe un PINAR en ejecución; ciérrelo antes de ejecutar otra versión.",
            )

    async def cerrar(self, plan_id: uuid.UUID, actor: uuid.UUID | None) -> asyncpg.Record:
        plan = await self._plan_or_404(plan_id)
        if plan["estado"] in ("cerrado", "borrador"):
            raise PinarConflict(
                "transicion_invalida",
                "Solo un plan 'aprobado' o 'en_ejecucion' puede cerrarse.",
            )
        async with self._conn.transaction():
            updated = await self._repo.set_estado(
                plan_id, nuevo="cerrado", expected=plan["estado"]
            )
            if updated is None:
                raise PinarConflict("transicion_invalida", "El estado del plan cambió; reintente.")
            await self._audit_log(
                action="pinar.plan_cerrado",
                object_ref=str(plan_id),
                actor=actor,
                payload={"estado_previo": plan["estado"], "estado": "cerrado"},
            )
            return updated

    # Auditoría de dominio (admin_audit) de las mutaciones ESTRUCTURALES — que solo
    # ocurren en 'borrador' (contenido en borrador; el snapshot oficial se congela y
    # audita en la aprobación, ya en audit_log inmutable). Cubre el hallazgo #1.
    async def _domain(
        self,
        *,
        entity: str,
        entity_id: str,
        action: str,
        actor: uuid.UUID | None,
        old_value: Any = None,
        new_value: Any = None,
    ) -> None:
        await self._repo.audit(
            entity=entity,
            entity_id=entity_id,
            action=action,
            old_value=old_value,
            new_value=new_value,
            actor_id=actor,
        )

    # --- aspectos ----------------------------------------------------------
    async def create_aspecto(
        self, plan_id: uuid.UUID, data: dict[str, Any], actor: uuid.UUID | None
    ) -> asyncpg.Record:
        plan = await self._plan_or_404(plan_id)
        self._assert_editable(plan)
        async with self._conn.transaction():
            row = await self._repo.create_aspecto(
                plan_id,
                descripcion=data["descripcion"],
                riesgo=data.get("riesgo"),
                orden=data.get("orden", 0),
            )
            await self._domain(
                entity="pinar_aspecto",
                entity_id=str(row["id"]),
                action="create",
                actor=actor,
                new_value={"descripcion": data["descripcion"]},
            )
            return row

    async def list_aspectos(self, plan_id: uuid.UUID) -> list[asyncpg.Record]:
        await self._plan_or_404(plan_id)
        return await self._repo.list_aspectos(plan_id)

    async def _aspecto_plan(self, aspecto_id: uuid.UUID) -> tuple[asyncpg.Record, asyncpg.Record]:
        aspecto = await self._repo.get_aspecto(aspecto_id)
        if aspecto is None:
            raise PinarNotFound(f"Aspecto {aspecto_id} not found")
        plan = await self._plan_or_404(aspecto["plan_id"])
        return aspecto, plan

    async def update_aspecto(
        self, aspecto_id: uuid.UUID, fields: dict[str, Any], actor: uuid.UUID | None
    ) -> asyncpg.Record:
        aspecto, plan = await self._aspecto_plan(aspecto_id)
        self._assert_editable(plan)
        async with self._conn.transaction():
            row = await self._repo.update_aspecto(aspecto_id, fields)
            await self._domain(
                entity="pinar_aspecto",
                entity_id=str(aspecto_id),
                action="update",
                actor=actor,
                old_value={k: str(aspecto[k]) for k in fields if k in aspecto},
                new_value={k: str(v) for k, v in fields.items()},
            )
            return row  # type: ignore[return-value]

    async def delete_aspecto(self, aspecto_id: uuid.UUID, actor: uuid.UUID | None) -> None:
        aspecto, plan = await self._aspecto_plan(aspecto_id)
        self._assert_editable(plan)
        async with self._conn.transaction():
            # old_value ANTES de borrar (el DELETE es destructivo, sin append-only).
            await self._domain(
                entity="pinar_aspecto",
                entity_id=str(aspecto_id),
                action="delete",
                actor=actor,
                old_value={"descripcion": aspecto["descripcion"], "riesgo": aspecto["riesgo"]},
            )
            await self._repo.delete_aspecto(aspecto_id)

    # --- priorización ------------------------------------------------------
    async def set_priorizacion_plan(
        self,
        plan_id: uuid.UUID,
        filas: list[tuple[uuid.UUID, list[tuple[int, int]]]],
        actor: uuid.UUID | None,
    ) -> None:
        """Carga la matriz de priorización del plan de forma ATÓMICA (una sola
        transacción para todas las filas — cubre el hallazgo #3)."""
        plan = await self._plan_or_404(plan_id)
        self._assert_editable(plan)
        async with self._conn.transaction():
            for aspecto_id, celdas in filas:
                aspecto = await self._repo.get_aspecto(aspecto_id)
                if aspecto is None or aspecto["plan_id"] != plan_id:
                    raise PinarNotFound(
                        f"Aspecto {aspecto_id} no pertenece al plan {plan_id}"
                    )
                await self._repo.replace_priorizacion(aspecto_id, celdas)
                await self._domain(
                    entity="pinar_priorizacion",
                    entity_id=str(aspecto_id),
                    action="update",
                    actor=actor,
                    new_value={str(e): i for e, i in celdas},
                )

    async def priorizacion_del_plan(self, plan_id: uuid.UUID) -> list[asyncpg.Record]:
        await self._plan_or_404(plan_id)
        return await self._repo.priorizacion_del_plan(plan_id)

    async def list_ejes(self) -> list[asyncpg.Record]:
        return await self._repo.list_ejes()

    # --- objetivos ---------------------------------------------------------
    async def create_objetivo(
        self, plan_id: uuid.UUID, data: dict[str, Any], actor: uuid.UUID | None
    ) -> asyncpg.Record:
        plan = await self._plan_or_404(plan_id)
        self._assert_editable(plan)
        async with self._conn.transaction():
            row = await self._repo.create_objetivo(
                plan_id, descripcion=data["descripcion"], orden=data.get("orden", 0)
            )
            await self._domain(
                entity="pinar_objetivo",
                entity_id=str(row["id"]),
                action="create",
                actor=actor,
                new_value={"descripcion": data["descripcion"]},
            )
            return row

    async def list_objetivos(self, plan_id: uuid.UUID) -> list[asyncpg.Record]:
        await self._plan_or_404(plan_id)
        return await self._repo.list_objetivos(plan_id)

    async def update_objetivo(
        self, objetivo_id: uuid.UUID, fields: dict[str, Any], actor: uuid.UUID | None
    ) -> asyncpg.Record:
        objetivo = await self._repo.get_objetivo(objetivo_id)
        if objetivo is None:
            raise PinarNotFound(f"Objetivo {objetivo_id} not found")
        plan = await self._plan_or_404(objetivo["plan_id"])
        self._assert_editable(plan)
        async with self._conn.transaction():
            row = await self._repo.update_objetivo(objetivo_id, fields)
            await self._domain(
                entity="pinar_objetivo",
                entity_id=str(objetivo_id),
                action="update",
                actor=actor,
                old_value={k: str(objetivo[k]) for k in fields if k in objetivo},
                new_value={k: str(v) for k, v in fields.items()},
            )
            return row  # type: ignore[return-value]

    async def delete_objetivo(self, objetivo_id: uuid.UUID, actor: uuid.UUID | None) -> None:
        objetivo = await self._repo.get_objetivo(objetivo_id)
        if objetivo is None:
            raise PinarNotFound(f"Objetivo {objetivo_id} not found")
        plan = await self._plan_or_404(objetivo["plan_id"])
        self._assert_editable(plan)
        async with self._conn.transaction():
            await self._domain(
                entity="pinar_objetivo",
                entity_id=str(objetivo_id),
                action="delete",
                actor=actor,
                old_value={"descripcion": objetivo["descripcion"]},
            )
            await self._repo.delete_objetivo(objetivo_id)

    # --- proyectos ---------------------------------------------------------
    async def create_proyecto(
        self, plan_id: uuid.UUID, data: dict[str, Any], actor: uuid.UUID | None
    ) -> asyncpg.Record:
        plan = await self._plan_or_404(plan_id)
        self._assert_editable(plan)
        async with self._conn.transaction():
            row = await self._repo.create_proyecto(plan_id, data)
            await self._domain(
                entity="pinar_proyecto",
                entity_id=str(row["id"]),
                action="create",
                actor=actor,
                new_value={"nombre": data["nombre"]},
            )
            return row

    async def list_proyectos(self, plan_id: uuid.UUID) -> list[asyncpg.Record]:
        await self._plan_or_404(plan_id)
        return await self._repo.list_proyectos(plan_id)

    async def _proyecto_plan(self, proyecto_id: uuid.UUID) -> tuple[asyncpg.Record, asyncpg.Record]:
        proyecto = await self._repo.get_proyecto(proyecto_id)
        if proyecto is None:
            raise PinarNotFound(f"Proyecto {proyecto_id} not found")
        plan = await self._plan_or_404(proyecto["plan_id"])
        return proyecto, plan

    async def update_proyecto(
        self, proyecto_id: uuid.UUID, fields: dict[str, Any], actor: uuid.UUID | None
    ) -> asyncpg.Record:
        proyecto, plan = await self._proyecto_plan(proyecto_id)
        self._assert_editable(plan)
        async with self._conn.transaction():
            row = await self._repo.update_proyecto(proyecto_id, fields)
            await self._domain(
                entity="pinar_proyecto",
                entity_id=str(proyecto_id),
                action="update",
                actor=actor,
                old_value={k: str(proyecto[k]) for k in fields if k in proyecto},
                new_value={k: str(v) for k, v in fields.items()},
            )
            return row  # type: ignore[return-value]

    async def delete_proyecto(self, proyecto_id: uuid.UUID, actor: uuid.UUID | None) -> None:
        proyecto, plan = await self._proyecto_plan(proyecto_id)
        self._assert_editable(plan)
        async with self._conn.transaction():
            await self._domain(
                entity="pinar_proyecto",
                entity_id=str(proyecto_id),
                action="delete",
                actor=actor,
                old_value={"nombre": proyecto["nombre"]},
            )
            await self._repo.delete_proyecto(proyecto_id)

    async def set_proyecto_aspectos(
        self, proyecto_id: uuid.UUID, aspecto_ids: list[uuid.UUID], actor: uuid.UUID | None
    ) -> list[uuid.UUID]:
        _, plan = await self._proyecto_plan(proyecto_id)
        self._assert_editable(plan)
        async with self._conn.transaction():
            await self._repo.replace_proyecto_aspectos(proyecto_id, aspecto_ids)
            await self._domain(
                entity="pinar_proyecto_aspecto",
                entity_id=str(proyecto_id),
                action="update",
                actor=actor,
                new_value={"aspectos": [str(a) for a in aspecto_ids]},
            )
            return await self._repo.list_proyecto_aspectos(proyecto_id)

    # --- seguimiento -------------------------------------------------------
    async def add_seguimiento(
        self, proyecto_id: uuid.UUID, data: dict[str, Any], actor: uuid.UUID | None
    ) -> asyncpg.Record:
        _, plan = await self._proyecto_plan(proyecto_id)
        self._assert_trackable(plan)
        return await self._repo.add_seguimiento(
            proyecto_id,
            fecha=data["fecha"],
            avance_pct=data["avance_pct"],
            observacion=data.get("observacion"),
            registrado_por=actor,
        )

    async def list_seguimiento(self, proyecto_id: uuid.UUID) -> list[asyncpg.Record]:
        await self._proyecto_plan(proyecto_id)
        return await self._repo.list_seguimiento(proyecto_id)

    # --- instrumentos ------------------------------------------------------
    async def create_instrumento(
        self, plan_id: uuid.UUID, data: dict[str, Any], actor: uuid.UUID | None
    ) -> asyncpg.Record:
        plan = await self._plan_or_404(plan_id)
        self._assert_editable(plan)
        async with self._conn.transaction():
            row = await self._repo.create_instrumento(
                plan_id, tipo=data["tipo"], ref=data["ref"], descripcion=data.get("descripcion")
            )
            await self._domain(
                entity="pinar_instrumento",
                entity_id=str(row["id"]),
                action="create",
                actor=actor,
                new_value={"tipo": data["tipo"], "ref": data["ref"]},
            )
            return row

    async def list_instrumentos(self, plan_id: uuid.UUID) -> list[asyncpg.Record]:
        await self._plan_or_404(plan_id)
        return await self._repo.list_instrumentos(plan_id)

    async def delete_instrumento(
        self, plan_id: uuid.UUID, instrumento_id: uuid.UUID, actor: uuid.UUID | None
    ) -> None:
        plan = await self._plan_or_404(plan_id)
        self._assert_editable(plan)
        async with self._conn.transaction():
            await self._domain(
                entity="pinar_instrumento",
                entity_id=str(instrumento_id),
                action="delete",
                actor=actor,
            )
            if not await self._repo.delete_instrumento(instrumento_id):
                raise PinarNotFound(f"Instrumento {instrumento_id} not found")

    # --- tableros ----------------------------------------------------------
    async def tablero(self, plan_id: uuid.UUID) -> dict[str, Any]:
        await self._plan_or_404(plan_id)
        return {
            "plan_id": plan_id,
            "por_objetivo": await self._repo.tablero_por_objetivo(plan_id),
            "por_eje": await self._repo.tablero_por_eje(plan_id),
        }

    async def mapa_ruta(self, plan_id: uuid.UUID) -> list[asyncpg.Record]:
        await self._plan_or_404(plan_id)
        return await self._repo.mapa_ruta(plan_id)
