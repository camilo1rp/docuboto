# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Workflow Rules Engine — gestión y evaluación de reglas de enrutamiento (E05)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.core.database import tenant_schema
from app.core.dependencias import (
    dependencia_ambigua,
    dependencia_desconocida,
    dependencia_inconsistente,
)
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.rules import (
    RuleBatchEvaluationRequest,
    RuleBatchEvaluationResponse,
    RuleEvaluationRequest,
    RuleEvaluationResponse,
    WorkflowRuleCreate,
    WorkflowRuleResponse,
    WorkflowRuleUpdate,
)
from app.services.rules_engine import rule_matches

_SERVICE = "workflow-service"

# Lo que de una regla hay que poder leer en el asiento para saber QUÉ decía.
# Se enumera a propósito en vez de volcar la fila entera: `created_at`,
# `updated_at` y `created_by` son mecánica de la tabla, no contenido de la
# decisión, y una lista por admisión no se ensancha sola cuando alguien añade
# una columna.
_CAMPOS_AUDITABLES = (
    "name",
    "description",
    "is_active",
    "priority",
    "match_mode",
    "doc_type",
    "doc_class",
    "dest_dept",
    "dest_dept_code",
    "subject_contains",
    "assign_to_dept",
    "assign_to_dept_code",
    "assign_notes",
)

#: Campos de NOMBRE de dependencia sobre los que ya no se acepta una condición de
#: igualdad/pertenencia (ADR-032): cambiaban de sentido al renombrar la dependencia.
_CAMPOS_POR_NOMBRE = ("dest_dept", "origin_dept")
_OPS_DE_IDENTIDAD = ("eq", "ne", "in")


def _condiciones_por_nombre_obsoletas(conditions: list[dict[str, Any]]) -> HTTPException | None:
    """422 `condicion_por_nombre_obsoleta` si una condición eq/ne/in compara un NOMBRE de
    dependencia. `contains`/`not_contains`/`regex` siguen permitidas (texto legítimo)."""
    malas = [
        c
        for c in conditions
        if c.get("field") in _CAMPOS_POR_NOMBRE and c.get("op") in _OPS_DE_IDENTIDAD
    ]
    if not malas:
        return None
    campos = sorted({f"{c['field']}_code" for c in malas})
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "error": "condicion_por_nombre_obsoleta",
            "detail": (
                "Comparar una dependencia por nombre (eq/ne/in) ya no se admite: el "
                f"nombre cambia al renombrarla. Use {', '.join(campos)} con el código."
            ),
            "campos": campos,
            "status": 422,
        },
    )


def _codigos_validos(conditions: list[dict[str, Any]]) -> HTTPException | None:
    """Las condiciones sobre `*_dept_code` comparan enteros: otra cosa no casaría nunca."""
    for c in conditions:
        if c.get("field") not in ("dest_dept_code", "origin_dept_code"):
            continue
        valor = c.get("value")
        items = valor if isinstance(valor, list) else str(valor).split(",")
        try:
            [int(str(v).strip()) for v in items if str(v).strip() != ""]
        except ValueError:
            return HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "error": "condicion_invalida",
                    "detail": f"{c.get('field')} compara códigos de dependencia (enteros)",
                    "status": 422,
                },
            )
    return None


def _conditions(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    return json.loads(raw)


def _retrato(row: asyncpg.Record) -> dict[str, Any]:
    """Lo que la regla dice, en forma serializable para el asiento."""
    data = dict(row)
    retrato: dict[str, Any] = {c: data.get(c) for c in _CAMPOS_AUDITABLES}
    retrato["conditions"] = _conditions(data.get("conditions"))
    return retrato


def _to_response(row: asyncpg.Record) -> WorkflowRuleResponse:
    data = dict(row)
    data["conditions"] = _conditions(data.get("conditions"))
    return WorkflowRuleResponse(**data)


def _context(req: RuleEvaluationRequest) -> dict[str, Any]:
    return {
        "doc_type": req.doc_type,
        "doc_class": req.doc_class,
        "dest_dept": req.dest_dept,
        "origin_dept": req.origin_dept,
        "dest_dept_code": req.dest_dept_code,
        "origin_dept_code": req.origin_dept_code,
        "subject": req.subject,
        "sender_name": req.sender_name,
        "sender_entity": req.sender_entity,
        "pages": req.pages,
    }


class WorkflowRulesService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self.conn = conn
        self.tenant_slug = tenant_slug

    async def _set_search_path(self) -> None:
        # Las tablas de reglas viven en tenant_{slug} (ADR-012).
        schema = tenant_schema(self.tenant_slug)  # valida + normaliza (fail-closed)
        await self.conn.execute(f'SET search_path TO "{schema}"')

    async def _resolver_dependencia(
        self, codigo: int | None, nombre: str | None, *, field: str, activa: bool
    ) -> tuple[int, str]:
        """(código, nombre del catálogo) de la dependencia que una regla cita.

        - código: debe existir (y estar ACTIVA si la regla ASIGNA a ella,
          `activa=True`); si además llega el alias por nombre debe ser el del catálogo
          (422 `dependencia_inconsistente`).
        - solo nombre (alias deprecado): se resuelve con el criterio único de
          tenant-service (`resolver_codigo_dependencia`, solo ACTIVAS con ese nombre
          ACTUAL): 1 -> su código; >=2 -> 422 `dependencia_ambigua`; 0 -> 422
          `dependencia_desconocida`.
        El nombre guardado es SIEMPRE el del catálogo, nunca el texto del cliente.

        Cierra lo que antes era el residual del servicio: una regla mal configurada se
        aceptaba en silencio y solo se descubría radicado a radicado en ejecución.
        """
        repo = WorkflowRepository(self.conn, self.tenant_slug)
        if codigo is None:
            assert nombre is not None
            cod, candidatos = await repo.resolver_alias(nombre, historico=False)
            if candidatos >= 2:
                raise dependencia_ambigua(field, nombre, candidatos)
            if cod is None:
                raise dependencia_desconocida(field, nombre)
            codigo = cod
        catalogo = await (
            repo.dependencia_activa(codigo) if activa else repo.dependencia_existente(codigo)
        )
        if catalogo is None:
            raise dependencia_desconocida(field, codigo)
        if nombre is not None and nombre != catalogo:
            raise dependencia_inconsistente(field, codigo, nombre, catalogo)
        return codigo, catalogo

    async def create_rule(
        self, payload: WorkflowRuleCreate, created_by: UUID
    ) -> WorkflowRuleResponse:
        """Crea una regla de enrutamiento, con su asiento en la misma transacción.

        Una regla decide **a qué dependencia se asigna cada radicado entrante**:
        quien la escribe está redirigiendo correspondencia oficial que todavía no
        ha llegado. Es de las mutaciones que ADR-010 obliga a asentar, y el
        servicio entero no tenía ni una referencia a `audit` mientras su hermano
        `workflow_service` tenía cuarenta y nueve.

        El asiento va DENTRO de la transacción, no después: si la cadena inmutable
        no puede recibirlo, la regla no se crea. Es el criterio del resto del
        servicio — una regla activa sin asiento es una decisión sin autor.
        """
        await self._set_search_path()
        condiciones = [c.model_dump() for c in payload.conditions]
        for error in (
            _condiciones_por_nombre_obsoletas(condiciones),
            _codigos_validos(condiciones),
        ):
            if error is not None:
                raise error
        assign_code, assign_name = await self._resolver_dependencia(
            payload.assign_to_dept_code, payload.assign_to_dept,
            field="assign_to_dept_code", activa=True,
        )
        dest_code: int | None = None
        dest_name: str | None = None
        if payload.dest_dept_code is not None or payload.dest_dept is not None:
            dest_code, dest_name = await self._resolver_dependencia(
                payload.dest_dept_code, payload.dest_dept,
                field="dest_dept_code", activa=False,
            )
        async with self.conn.transaction():
            row = await self.conn.fetchrow(
                """
                INSERT INTO workflow_rules
                    (name, description, is_active, priority, conditions, match_mode,
                     doc_type, doc_class, dest_dept, subject_contains,
                     assign_to_dept, assign_notes, created_by,
                     assign_to_dept_code, dest_dept_code)
                VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                RETURNING *
                """,
                payload.name,
                payload.description,
                payload.is_active,
                payload.priority,
                json.dumps(condiciones),
                payload.match_mode,
                payload.doc_type,
                payload.doc_class,
                dest_name,
                payload.subject_contains,
                assign_name,
                payload.assign_notes,
                created_by,
                assign_code,
                dest_code,
            )
            await audit.append(
                self.conn,
                tenant_slug=self.tenant_slug,
                service=_SERVICE,
                action="workflow.regla_creada",
                object_type="workflow_rule",
                # El UUID y no el nombre: renombrar una regla no puede partir su
                # historial en dos asientos que parezcan de objetos distintos.
                # El nombre viaja en el retrato, que es donde se lee.
                object_ref=str(row["id"]),
                actor=str(created_by),
                payload={"regla": _retrato(row)},
                canal=audit.CANAL_PETICION,
            )
        return _to_response(row)

    async def list_rules(
        self, *, only_active: bool = False, page: int = 1, size: int = 50
    ) -> tuple[list[WorkflowRuleResponse], int]:
        await self._set_search_path()
        where = "WHERE is_active = true" if only_active else ""
        offset = (page - 1) * size

        rows = await self.conn.fetch(
            f"SELECT * FROM workflow_rules {where} "
            "ORDER BY priority, created_at LIMIT $1 OFFSET $2",
            size,
            offset,
        )
        total: int = await self.conn.fetchval(f"SELECT COUNT(*) FROM workflow_rules {where}") or 0
        return [_to_response(r) for r in rows], total

    async def get_rule(self, rule_id: UUID) -> WorkflowRuleResponse | None:
        await self._set_search_path()
        row = await self.conn.fetchrow("SELECT * FROM workflow_rules WHERE id=$1", rule_id)
        return _to_response(row) if row else None

    async def update_rule(
        self, rule_id: UUID, payload: WorkflowRuleUpdate, actor: UUID
    ) -> WorkflowRuleResponse | None:
        """Edita una regla y asienta QUÉ cambió, con el valor anterior de cada campo.

        El asiento lleva solo los campos que esta petición tocó, con su `antes` y
        su `despues`. Volcar la regla entera enterraría el cambio —que es lo único
        que se viene a mirar— entre once campos que siguen igual; y sin el `antes`
        el asiento diría que alguien la editó sin decir de qué a qué, que es lo
        mismo que no decir nada. Es el criterio que ya usa la reclasificación TRD.
        """
        await self._set_search_path()
        data = payload.model_dump(exclude_unset=True)
        if not data:
            # Un PATCH vacío no muta nada: no hay acto que asentar. Asentarlo
            # llenaría la cadena de asientos que no describen ningún cambio.
            return await self.get_rule(rule_id)

        if data.get("conditions") is not None:
            for error in (
                _condiciones_por_nombre_obsoletas(data["conditions"]),
                _codigos_validos(data["conditions"]),
            ):
                if error is not None:
                    raise error
        # PATCH parcial: solo se valida si el cliente TOCÓ el destino en esta petición
        # (`exclude_unset`) — un PATCH que edita `name`/`priority`/etc. no debe
        # re-validar (ni romper) el valor ya vigente. El par (código, nombre) se
        # reescribe ENTERO desde el catálogo (ADR-032).
        if data.get("assign_to_dept_code") is not None or data.get("assign_to_dept") is not None:
            data["assign_to_dept_code"], data["assign_to_dept"] = await self._resolver_dependencia(
                data.get("assign_to_dept_code"), data.get("assign_to_dept"),
                field="assign_to_dept_code", activa=True,
            )
        else:
            data.pop("assign_to_dept_code", None)
            data.pop("assign_to_dept", None)
        if "dest_dept_code" in data or "dest_dept" in data:
            if data.get("dest_dept_code") is not None or data.get("dest_dept") is not None:
                data["dest_dept_code"], data["dest_dept"] = await self._resolver_dependencia(
                    data.get("dest_dept_code"), data.get("dest_dept"),
                    field="dest_dept_code", activa=False,
                )
            else:  # limpiar la condición: ambos a NULL
                data["dest_dept_code"] = None
                data["dest_dept"] = None
        if not data:
            return await self.get_rule(rule_id)

        set_parts: list[str] = []
        values: list[Any] = []
        idx = 2
        for col, val in data.items():
            if col == "conditions":
                set_parts.append(f"conditions = ${idx}::jsonb")
                values.append(json.dumps(val))
            else:
                set_parts.append(f"{col} = ${idx}")
                values.append(val)
            idx += 1

        async with self.conn.transaction():
            # El estado anterior se lee DENTRO de la transacción y con `FOR
            # UPDATE`: entre leerlo y escribir no puede colarse otra edición que
            # dejaría el asiento describiendo un `antes` que ya no era el vigente.
            previo = await self.conn.fetchrow(
                "SELECT * FROM workflow_rules WHERE id=$1 FOR UPDATE", rule_id
            )
            if previo is None:
                return None

            row = await self.conn.fetchrow(
                f"""
                UPDATE workflow_rules
                SET {', '.join(set_parts)}, updated_at=now()
                WHERE id=$1
                RETURNING *
                """,
                rule_id,
                *values,
            )
            antes = _retrato(previo)
            despues = _retrato(row)
            cambios = {
                campo: {"antes": antes.get(campo), "despues": despues.get(campo)}
                for campo in data
                if antes.get(campo) != despues.get(campo)
            }
            await audit.append(
                self.conn,
                tenant_slug=self.tenant_slug,
                service=_SERVICE,
                action="workflow.regla_modificada",
                object_type="workflow_rule",
                object_ref=str(rule_id),
                actor=str(actor),
                # `nombre` viaja fuera de `cambios` para que el asiento se pueda
                # leer sin ir a buscar de qué regla habla — aunque el propio
                # nombre sea lo que cambió.
                payload={"nombre": despues.get("name"), "cambios": cambios},
                canal=audit.CANAL_PETICION,
            )
        return _to_response(row)

    async def delete_rule(self, rule_id: UUID, actor: UUID) -> bool:
        """Borra una regla dejando en el asiento QUÉ decía.

        El `DELETE` es físico: después no queda dónde ir a mirar. Un asiento que
        solo dijera «se borró la regla tal» convertiría la pregunta «¿por qué
        dejaron de asignarse a Jurídica?» en irrespondible. Por eso el retrato
        completo de la regla viaja en el asiento — es el único sitio donde
        sobrevive.
        """
        await self._set_search_path()
        async with self.conn.transaction():
            previo = await self.conn.fetchrow(
                "DELETE FROM workflow_rules WHERE id=$1 RETURNING *", rule_id
            )
            if previo is None:
                return False
            await audit.append(
                self.conn,
                tenant_slug=self.tenant_slug,
                service=_SERVICE,
                action="workflow.regla_eliminada",
                object_type="workflow_rule",
                object_ref=str(rule_id),
                actor=str(actor),
                payload={"regla": _retrato(previo)},
                canal=audit.CANAL_PETICION,
            )
        return True

    # ------------------------------------------------------------------
    # Evaluación
    # ------------------------------------------------------------------

    async def _active_rules(self) -> list[asyncpg.Record]:
        await self._set_search_path()
        return list(
            await self.conn.fetch(
                "SELECT * FROM workflow_rules WHERE is_active = true "
                "ORDER BY priority, created_at"
            )
        )

    async def _log_activation(self, rule_id: UUID, radicado_id: UUID, tracking_number: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO workflow_rule_activations
                (rule_id, radicado_id, tracking_number)
            VALUES ($1, $2, $3)
            """,
            rule_id,
            radicado_id,
            tracking_number,
        )

    def _first_match(
        self, rules: list[asyncpg.Record], req: RuleEvaluationRequest
    ) -> asyncpg.Record | None:
        context = _context(req)
        for row in rules:
            if rule_matches(dict(row), _conditions(row["conditions"]), context):
                return row
        return None

    async def evaluate(self, request: RuleEvaluationRequest) -> RuleEvaluationResponse:
        """Evalúa las reglas activas (por prioridad) y devuelve la primera que casa."""
        rules = await self._active_rules()
        match = self._first_match(rules, request)
        if match is None:
            return RuleEvaluationResponse(matched=False, radicado_id=request.radicado_id)

        await self._log_activation(match["id"], request.radicado_id, request.tracking_number)
        return RuleEvaluationResponse(
            matched=True,
            radicado_id=request.radicado_id,
            rule_id=match["id"],
            rule_name=match["name"],
            assign_to_dept=match["assign_to_dept"],
            assign_to_dept_code=match["assign_to_dept_code"],
            assign_notes=match["assign_notes"],
        )

    async def evaluate_batch(
        self, request: RuleBatchEvaluationRequest
    ) -> RuleBatchEvaluationResponse:
        """Evalúa varios radicados contra el mismo conjunto de reglas (una sola
        lectura de reglas)."""
        rules = await self._active_rules()
        results: list[RuleEvaluationResponse] = []
        for item in request.items:
            match = self._first_match(rules, item)
            if match is None:
                results.append(RuleEvaluationResponse(matched=False, radicado_id=item.radicado_id))
                continue
            await self._log_activation(match["id"], item.radicado_id, item.tracking_number)
            results.append(
                RuleEvaluationResponse(
                    matched=True,
                    radicado_id=item.radicado_id,
                    rule_id=match["id"],
                    rule_name=match["name"],
                    assign_to_dept=match["assign_to_dept"],
                    assign_to_dept_code=match["assign_to_dept_code"],
                    assign_notes=match["assign_notes"],
                )
            )
        return RuleBatchEvaluationResponse(results=results)
