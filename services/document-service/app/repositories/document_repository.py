# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Document repository — all direct asyncpg calls live here.

Tenant isolation: the connection arrives with its search_path already set to
tenant_{slug} by the get_tenant_conn dependency (ADR-002/012), so every query
hits the tenant's own tables. The slug is kept only to resolve the tenant code
from public.tenants for tracking-number formatting.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

import asyncpg
from orpycamcp_common.tenant import slug_valido

from app.models.document import Anexo, Radicado
from app.schemas.document import AnexoInput, DocumentCreate, DocumentUpdate


class InvalidTenantSlugError(Exception):
    pass


class DocumentRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        if not slug_valido(tenant_slug):
            raise InvalidTenantSlugError(f"Invalid tenant slug: {tenant_slug!r}")
        # El search_path al schema del tenant lo fija get_tenant_conn (ADR-012);
        # el repositorio sólo usa el slug para resolver el código en public.tenants.
        self._conn = conn
        self._slug = tenant_slug

    async def get_tenant_code(self) -> str:
        """Look up the tenant's tracking-number code from public.tenants."""
        row = await self._conn.fetchrow(
            "SELECT code FROM public.tenants WHERE slug = $1", self._slug
        )
        if row is None:
            return self._slug.upper()
        return row["code"]

    # ------------------------------------------------------------------
    # Tracking number sequence — atomic via INSERT … ON CONFLICT DO UPDATE
    # ------------------------------------------------------------------

    async def next_sequence(self, year: int, doc_type: str) -> int:
        seq: int = await self._conn.fetchval(
            """
            INSERT INTO tracking_sequences (year, doc_type, last_seq)
            VALUES ($1, $2, 1)
            ON CONFLICT (year, doc_type)
            DO UPDATE SET last_seq = tracking_sequences.last_seq + 1
            RETURNING last_seq
            """,
            year,
            doc_type,
        )
        return seq

    # ------------------------------------------------------------------
    # Radicados
    # ------------------------------------------------------------------

    async def create(
        self,
        payload: DocumentCreate,
        tracking_number: str,
        year: int,
        sequence: int,
        tenant_code: str,
        registered_by: uuid.UUID | None,
        due_date: date | None,
        metadata_template_id: uuid.UUID | None,
    ) -> Radicado:
        row = await self._conn.fetchrow(
            """
            INSERT INTO radicados (
                tracking_number, doc_type, year, sequence,
                subject, doc_class,
                origin_dept, dest_dept,
                origin_dept_code, dest_dept_code,
                sender_name, sender_entity,
                pages, observations,
                registered_by, tenant_code, due_date,
                metadata, metadata_template_id,
                nivel_seguridad, clasificacion_fundamento
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6,
                $7, $8,
                $9, $10,
                $11, $12,
                $13, $14,
                $15, $16, $17,
                $18::jsonb, $19,
                $20, $21
            )
            RETURNING
                id, tracking_number, doc_type, year, sequence,
                subject, doc_class, origin_dept, dest_dept,
                origin_dept_code, dest_dept_code,
                sender_name, sender_entity, status, pages, nivel_seguridad, observations,
                registered_by, registered_at, updated_at, tenant_code, due_date,
                metadata, metadata_template_id,
                publicable, publicado_at,
                flujo_dept_code, flujo_dept, ubicacion_dept_code, ubicacion_dept
            """,
            tracking_number,
            payload.doc_type,
            year,
            sequence,
            payload.subject,
            payload.doc_class,
            payload.origin_dept,
            payload.dest_dept,
            payload.origin_dept_code,
            payload.dest_dept_code,
            payload.sender_name,
            payload.sender_entity,
            payload.pages,
            payload.observations,
            registered_by,
            tenant_code,
            due_date,
            json.dumps(payload.metadata),
            metadata_template_id,
            payload.nivel_seguridad,
            # El CHECK `radicados_clasificado_con_fundamento` (041) no es diferible:
            # el radicado nace YA motivado; `set_classification` completa el resto.
            payload.clasificacion_fundamento if payload.nivel_seguridad >= 2 else None,
        )
        radicado = _row_to_radicado(row)

        if payload.anexos:
            radicado.anexos = await self._create_anexos(radicado.id, payload.anexos)

        return radicado

    async def _create_anexos(self, radicado_id: uuid.UUID, anexos: list[AnexoInput]) -> list[Anexo]:
        # search_path is already set by the caller (create method)
        created: list[Anexo] = []
        for a in anexos:
            row = await self._conn.fetchrow(
                """
                INSERT INTO anexos (
                    radicado_id, file_id, filename, file_size, mime_type,
                    tipo_anexo, es_principal, folios
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id, radicado_id, file_id, filename, file_size, mime_type,
                          created_at, tipo_anexo, es_principal, folios
                """,
                radicado_id,
                a.file_id,
                a.filename,
                a.file_size,
                a.mime_type,
                a.tipo_anexo,
                a.es_principal,
                a.folios,
            )
            created.append(_row_to_anexo(row))
        return created

    async def get_by_id(
        self, document_id: uuid.UUID, max_clearance: int = 3
    ) -> Radicado | None:
        # max_clearance=3 (CLASIFICADA) = sin restricción para llamantes internos/sistema;
        # los GET de usuario pasan el clearance real → un doc por encima devuelve None
        # (el router lo traduce a 404, sin revelar su existencia). RF-SEG-08 / RF-BUS-04.
        row = await self._conn.fetchrow(
            """
            SELECT
                id, tracking_number, doc_type, year, sequence,
                subject, doc_class, origin_dept, dest_dept,
                origin_dept_code, dest_dept_code,
                sender_name, sender_entity, status, pages, nivel_seguridad, observations,
                registered_by, registered_at, updated_at, tenant_code, due_date,
                metadata, metadata_template_id,
                publicable, publicado_at,
                flujo_dept_code, flujo_dept, flujo_usuario, ubicacion_dept_code, ubicacion_dept
            FROM radicados
            WHERE id = $1 AND nivel_seguridad <= $2
            """,
            document_id,
            max_clearance,
        )
        if row is None:
            return None
        radicado = _row_to_radicado(row)
        radicado.anexos = await self._get_anexos(radicado.id)
        return radicado

    async def get_anexo_for_download(
        self, document_id: uuid.UUID, file_id: uuid.UUID, max_clearance: int
    ) -> tuple[Anexo, str, int] | None:
        """Resuelve, en UNA sola consulta, los tres requisitos de la descarga
        mediada de anexos (D-02, RF-SEG-08): el radicado existe, es visible
        al `max_clearance` del llamante (no-read-up) y `file_id` es
        efectivamente un anexo de ESE radicado (join, no dos lookups
        separados) — así los tres caminos de denegación convergen en el
        MISMO `None` → el router responde el MISMO 404 neutro para los tres,
        sin oráculo de "existe pero no es tuyo" vs "no existe".
        """
        row = await self._conn.fetchrow(
            """
            SELECT a.id, a.radicado_id, a.file_id, a.filename, a.file_size,
                   a.mime_type, a.created_at, a.tipo_anexo, a.es_principal, a.folios,
                   r.tracking_number, r.nivel_seguridad
            FROM anexos a
            JOIN radicados r ON r.id = a.radicado_id
            WHERE r.id = $1 AND a.file_id = $2 AND r.nivel_seguridad <= $3
            """,
            document_id,
            file_id,
            max_clearance,
        )
        if row is None:
            return None
        # tracking_number + nivel_seguridad se devuelven para el asiento de
        # descarga (RF-BUS-10): auditar el acceso al CONTENIDO, no solo al metadato.
        return _row_to_anexo(row), row["tracking_number"], row["nivel_seguridad"]

    async def set_responde_a(self, respuesta_id: uuid.UUID, antecedente_id: uuid.UUID) -> None:
        """Enlaza una Salida con su antecedente de Entrada (respuesta rápida)."""
        await self._conn.execute(
            "UPDATE radicados SET responde_a = $2 WHERE id = $1",
            respuesta_id,
            antecedente_id,
        )

    async def list_respuestas(
        self, antecedente_id: uuid.UUID, max_clearance: int = 3
    ) -> list[asyncpg.Record]:
        """Salidas que responden al radicado dado (consulta ligera, sin mapper).

        No-read-up (RF-SEG-08): filtra `nivel_seguridad <= max_clearance` igual
        que todo el resto de lecturas de radicados (invariante del CLAUDE.md del
        servicio). Sin el filtro, cualquier llamante enumeraría las Salidas
        vinculadas (tracking/asunto/estado/fecha) sin importar su clasificación.
        """
        return list(
            await self._conn.fetch(
                "SELECT id, tracking_number, doc_type, subject, nivel_seguridad, status, registered_at "
                "FROM radicados WHERE responde_a = $1 AND nivel_seguridad <= $2 "
                "ORDER BY registered_at",
                antecedente_id,
                max_clearance,
            )
        )

    async def get_by_tracking_number(
        self, tracking_number: str, max_clearance: int = 3
    ) -> Radicado | None:
        row = await self._conn.fetchrow(
            """
            SELECT
                id, tracking_number, doc_type, year, sequence,
                subject, doc_class, origin_dept, dest_dept,
                origin_dept_code, dest_dept_code,
                sender_name, sender_entity, status, pages, nivel_seguridad, observations,
                registered_by, registered_at, updated_at, tenant_code, due_date,
                metadata, metadata_template_id,
                publicable, publicado_at,
                flujo_dept_code, flujo_dept, flujo_usuario, ubicacion_dept_code, ubicacion_dept
            FROM radicados
            WHERE tracking_number = $1 AND nivel_seguridad <= $2
            """,
            tracking_number,
            max_clearance,
        )
        if row is None:
            return None
        radicado = _row_to_radicado(row)
        radicado.anexos = await self._get_anexos(radicado.id)
        return radicado

    # Los únicos órdenes que el listado acepta. Es un catálogo CERRADO y no un
    # nombre de columna que llegue por la URL: lo segundo es inyección SQL con
    # otro nombre, porque un `ORDER BY` no admite parámetro enlazado.
    #
    # `due_date` va con `NULLS LAST` en los dos sentidos: un radicado sin plazo
    # no es ni el más urgente ni el más holgado — la pregunta no le aplica, y
    # abrir con él un informe de vencimientos es ruido.
    #
    # En ascendente es REDUNDANTE y se escribe igual: el default de Postgres ya
    # es `NULLS LAST` para `ASC`. Donde cambia algo es en DESCENDENTE, cuyo
    # default es `NULLS FIRST` — sin esto, «los de plazo más lejano» abriría con
    # los que no tienen plazo ninguno. Escribirlo en los dos evita que el
    # comportamiento dependa de recordar cuál de los dos defaults aplica.
    ORDENES_ADMITIDOS: dict[tuple[str, str], str] = {
        ("registered_at", "desc"): "registered_at DESC",
        ("registered_at", "asc"): "registered_at ASC",
        ("due_date", "asc"): "due_date ASC NULLS LAST",
        ("due_date", "desc"): "due_date DESC NULLS LAST",
    }

    async def find_all(
        self,
        *,
        doc_type: str | None,
        status: list[str] | None,
        metadata_filter: dict | None,
        page: int,
        size: int,
        max_clearance: int,
        due_from: date | None = None,
        due_to: date | None = None,
        order_by: str = "registered_at",
        order_dir: str = "desc",
        ubicacion_dept_code: int | None = None,
    ) -> tuple[list[Radicado], int]:
        conditions: list[str] = []
        params: list[object] = []
        idx = 1

        # Control de acceso por clasificación (RF-SEG-08 / RF-BUS-04): solo radicados
        # cuyo nivel no supere el clearance del llamante (no read-up). Siempre aplica.
        conditions.append(f"nivel_seguridad <= ${idx}")
        params.append(max_clearance)
        idx += 1

        if doc_type is not None:
            conditions.append(f"doc_type = ${idx}")
            params.append(doc_type)
            idx += 1

        if status:
            # `= ANY($n::text[])` y no un `IN` compuesto a mano: el número de
            # estados lo decide quien consulta, y concatenar marcadores según
            # ese número produce un plan distinto por cada tamaño de lista.
            # Con UN estado se comporta igual que el `=` que había antes.
            conditions.append(f"status = ANY(${idx}::text[])")
            params.append(status)
            idx += 1

        # Rango de fecha límite, ambos extremos INCLUSIVOS. El endpoint no
        # decide qué significa «vencido» —el proyecto usa hoy las dos
        # convenciones opuestas: la TRD cuenta vencido el día en que se cumple
        # el plazo y los préstamos no—, así que lo declara quien pregunta:
        # `due_to=<ayer>` excluye el que vence hoy y `due_to=<hoy>` lo incluye.
        # Hornear una de las dos aquí obligaría a la otra mitad del sistema a
        # pedir lo contrario de lo que quiere decir.
        if due_from is not None:
            conditions.append(f"due_date >= ${idx}")
            params.append(due_from)
            idx += 1

        if due_to is not None:
            conditions.append(f"due_date <= ${idx}")
            params.append(due_to)
            idx += 1

        if ubicacion_dept_code is not None:
            # Dónde está AHORA (ADR-029), no a quién iba dirigido. Va en
            # `conditions`: lo comparten el COUNT y el SELECT, así que el total
            # nunca cuenta filas que la página no puede mostrar.
            conditions.append(f"ubicacion_dept_code = ${idx}")
            params.append(ubicacion_dept_code)
            idx += 1

        if metadata_filter:
            # Filtro por campo de metadato vía índice GIN (E03, RF-MET-02).
            conditions.append(f"metadata @> ${idx}::jsonb")
            params.append(json.dumps(metadata_filter))
            idx += 1

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        count_params = list(params)
        total: int = await self._conn.fetchval(
            f"SELECT COUNT(*) FROM radicados {where_clause}", *count_params
        )

        # `id` como última clave SIEMPRE, incluso con el orden por defecto: sin
        # un orden TOTAL, dos filas empatadas pueden salir en distinto orden en
        # dos consultas, y al paginar eso significa una fila repetida en una
        # página y otra que no aparece en ninguna. `due_date` es una fecha sin
        # hora: los empates son la norma, no la excepción.
        orden = self.ORDENES_ADMITIDOS[(order_by, order_dir)]

        offset = (page - 1) * size
        params.append(size)
        params.append(offset)

        rows = await self._conn.fetch(
            f"""
            SELECT
                id, tracking_number, doc_type, year, sequence,
                subject, doc_class, origin_dept, dest_dept,
                origin_dept_code, dest_dept_code,
                sender_name, sender_entity, status, pages, nivel_seguridad, observations,
                registered_by, registered_at, updated_at, tenant_code, due_date,
                metadata, metadata_template_id,
                publicable, publicado_at,
                flujo_dept_code, flujo_dept, ubicacion_dept_code, ubicacion_dept
            FROM radicados
            {where_clause}
            ORDER BY {orden}, id DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *params,
        )

        # Anexos are not loaded for list responses — they are included only in
        # single-document endpoints to keep list queries fast.
        return [_row_to_radicado(r) for r in rows], total

    async def update(
        self,
        document_id: uuid.UUID,
        payload: DocumentUpdate,
        max_clearance: int,
        *,
        metadata_template_id: uuid.UUID | None = None,
        escribir_template: bool = False,
    ) -> Radicado | None:
        """Actualiza los campos mutables de un radicado (RF-SEG-08: no-read-up).

        El filtro `nivel_seguridad <= max_clearance` viaja en el `WHERE` del propio
        UPDATE (no en una comprobación previa) para que la autorización y la
        escritura sean el MISMO acto: sin ventana TOCTOU entre comprobar y mutar.
        Un radicado por encima del clearance devuelve `None`, indistinguible de uno
        inexistente — el router traduce ambos al mismo 404 neutro.

        `AND status <> 'anulado'` va en el MISMO `WHERE`, por el mismo motivo.
        Hasta ADR-028 `DocumentUpdate.status` admitía `registered`, así que sin esta
        condición un PATCH gateado solo por `PERM_RADI` **deshacía una anulación** aprobada por el
        circuito de dos pasos con visto bueno de supervisor — sin causal, sin
        aprobador y (hasta este cambio) sin traza. Es la misma ruta lateral que ya se
        cerró en el PATCH de expedientes. Hoy el router rechaza `status` (422) y el
        guard queda como defensa en profundidad: el estado lo mueve el flujo.
        """
        fields: list[str] = []
        params: list[object] = []
        idx = 1

        if payload.subject is not None:
            fields.append(f"subject = ${idx}")
            params.append(payload.subject)
            idx += 1

        if payload.dest_dept is not None or payload.dest_dept_code is not None:
            # El par (nombre, código) viaja en el MISMO UPDATE y llega YA normalizado por
            # el servicio desde el catálogo (ADR-032): alimenta la ubicación generada
            # (ADR-029) y cambiar solo uno dejaba el código de OTRA dependencia.
            fields.append(f"dest_dept = ${idx}::text")
            params.append(payload.dest_dept)
            idx += 1
            fields.append(f"dest_dept_code = ${idx}::integer")
            params.append(payload.dest_dept_code)
            idx += 1

        if payload.observations is not None:
            fields.append(f"observations = ${idx}")
            params.append(payload.observations)
            idx += 1

        if payload.pages is not None:
            fields.append(f"pages = ${idx}")
            params.append(payload.pages)
            idx += 1

        if payload.doc_class is not None:
            fields.append(f"doc_class = ${idx}")
            params.append(payload.doc_class)
            idx += 1

        if payload.metadata is not None:
            fields.append(f"metadata = ${idx}::jsonb")
            params.append(json.dumps(payload.metadata))
            idx += 1

        # La versión de plantilla usada viaja SIEMPRE que el servicio haya
        # revalidado, incluida la escritura de `NULL` cuando el tipo nuevo no
        # tiene plantilla activa. Sin ese `NULL` explícito, el radicado quedaría
        # apuntando a la plantilla del tipo ANTERIOR: una traza que dice que sus
        # metadatos se validaron contra un esquema que ya no le corresponde.
        if escribir_template:
            fields.append(f"metadata_template_id = ${idx}")
            params.append(metadata_template_id)
            idx += 1

        if not fields:
            # Nothing to update — return current state (mismo gate de clearance).
            return await self.get_by_id(document_id, max_clearance)

        fields.append(f"updated_at = ${idx}")
        params.append(datetime.now(tz=UTC))
        idx += 1

        params.append(document_id)
        params.append(max_clearance)
        set_clause = ", ".join(fields)

        row = await self._conn.fetchrow(
            f"""
            UPDATE radicados
            SET {set_clause}
            WHERE id = ${idx} AND nivel_seguridad <= ${idx + 1}
              AND status <> 'anulado'
            RETURNING
                id, tracking_number, doc_type, year, sequence,
                subject, doc_class, origin_dept, dest_dept,
                origin_dept_code, dest_dept_code,
                sender_name, sender_entity, status, pages, nivel_seguridad, observations,
                registered_by, registered_at, updated_at, tenant_code, due_date,
                metadata, metadata_template_id,
                publicable, publicado_at,
                flujo_dept_code, flujo_dept, flujo_usuario, ubicacion_dept_code, ubicacion_dept
            """,
            *params,
        )
        if row is None:
            return None

        radicado = _row_to_radicado(row)
        radicado.anexos = await self._get_anexos(radicado.id)
        return radicado

    async def get_disposition(
        self, document_id: uuid.UUID, max_clearance: int
    ) -> tuple[bool, dict]:
        """Devuelve (existe, disposition). disposition es {} si no hay nada fijado.

        No-read-up (RF-SEG-08): un radicado por encima de `max_clearance` devuelve
        `(False, {})`, EXACTAMENTE lo mismo que uno inexistente — de otro modo el
        200-frente-a-404 sería un oráculo de existencia para un llamante sin nivel.
        """
        row = await self._conn.fetchrow(
            "SELECT disposition FROM radicados WHERE id = $1 AND nivel_seguridad <= $2",
            document_id,
            max_clearance,
        )
        if row is None:
            return False, {}
        return True, _json_obj(row["disposition"])

    async def set_disposition(
        self, document_id: uuid.UUID, disposition: dict, max_clearance: int
    ) -> dict | None:
        """Fija la disposición final del radicado, gated por clearance.

        El gate va en el `WHERE` del UPDATE, no en una lectura previa: la
        disposición decide el DESTINO del documento (conservación total /
        eliminación, Ley 594/2000 art. 24), así que un actor sin nivel no debe
        poder mutarla ni siquiera en la ventana entre comprobar y escribir.
        """
        row = await self._conn.fetchrow(
            "UPDATE radicados SET disposition = $2::jsonb "
            "WHERE id = $1 AND nivel_seguridad <= $3 "
            "RETURNING disposition",
            document_id,
            json.dumps(disposition, default=str),
            max_clearance,
        )
        return _json_obj(row["disposition"]) if row is not None else None

    async def set_publicable(
        self,
        document_id: uuid.UUID,
        publicable: bool,
        max_clearance: int,
        actor: uuid.UUID | None,
    ) -> asyncpg.Record | None:
        """Marca o retira la publicación de oficio (11.1, Ley 1712 art. 12).

        El gate va en el `WHERE` del UPDATE, como en `set_disposition`: publicar
        es la decisión que hace visible un documento a cualquiera en internet, y
        un actor sin nivel para leerlo no debe poder tomarla ni en la ventana
        entre comprobar y escribir.

        `nivel_seguridad = 1` está en el `WHERE` además del clearance, pero **solo
        cuando se publica**: marcar como publicable un documento reservado no es
        un caso a filtrar más tarde, es una operación que no debe existir, porque
        guardaría en la base una afirmación falsa —«esto es público»— sobre un
        documento reservado.

        Exigirlo también al RETIRAR era un error de simetría: dejaba sin salida a
        un radicado publicado y clasificado después, justo el estado en el que más
        urge poder retirar la publicación. Un retiro no lee de más ni guarda
        ninguna afirmación falsa, así que la única condición que conserva es el
        clearance del actor. Fail-safe: en la duda, la dirección segura es poder
        dejar de difundir.
        """
        gate_nivel = " AND nivel_seguridad = 1" if publicable else ""
        if publicable:
            # AREA-07-12: defensa en profundidad frente a la Ley 1581. Lo que
            # llegó por la ventanilla pública es información de TERCEROS (datos
            # personales de quien radica) y nace en nivel 1 por el DEFAULT de la
            # columna, así que «nivel 1» no dice «difundible». Se excluye por
            # dos vías que no dependen una de otra: el canal con el que entró
            # (queda en `metadata.canal`, sin importar cómo se tipificó después)
            # y el tipo documental que la entidad declara admitido en ventanilla
            # (lo leído EN VIVO, así que sigue la configuración de la entidad).
            # Va en el WHERE: 404 neutro, sin asiento, como el resto de motivos.
            gate_nivel += (
                " AND COALESCE(metadata->>'canal', '') <> 'ventanilla_publica'"
                " AND NOT EXISTS (SELECT 1 FROM tipos_documentales t"
                " WHERE t.code = radicados.doc_class AND t.admite_ventanilla_publica)"
            )
        return await self._conn.fetchrow(
            "UPDATE radicados SET publicable = $2, "
            "    publicado_at  = CASE WHEN $2 THEN now() ELSE NULL END, "
            "    publicado_por = CASE WHEN $2 THEN $4::uuid ELSE NULL END "
            f"WHERE id = $1 AND nivel_seguridad <= $3{gate_nivel} "
            "  AND status <> 'anulado' "
            "RETURNING tracking_number, publicable, publicado_at",
            document_id,
            publicable,
            max_clearance,
            actor,
        )

    async def set_anexos_publicables(
        self, document_id: uuid.UUID, anexo_ids: list[uuid.UUID]
    ) -> int:
        """Fija QUÉ anexos de un radicado se difunden (AREA-07-12).

        Marca TRUE exactamente los indicados y FALSE el resto, así que el acto de
        publicar deja el conjunto en un estado conocido y no acumula marcas de
        actos anteriores. Con lista vacía —y al retirar— ninguno se difunde.
        Devuelve cuántos de los indicados pertenecen al radicado: si es menos que
        los pedidos, alguno era ajeno y el llamante revierte.
        """
        await self._conn.execute(
            "UPDATE anexos SET publicable = (id = ANY($2::uuid[])) "
            "WHERE radicado_id = $1 AND publicable IS DISTINCT FROM (id = ANY($2::uuid[]))",
            document_id,
            anexo_ids,
        )
        if not anexo_ids:
            return 0
        return int(
            await self._conn.fetchval(
                "SELECT COUNT(*) FROM anexos WHERE radicado_id = $1 AND id = ANY($2::uuid[])",
                document_id,
                anexo_ids,
            )
        )

    async def get_security_context(self, document_id: uuid.UUID) -> asyncpg.Record | None:
        """Returns id, tracking_number and nivel_seguridad for a radicado by UUID.

        Does NOT apply a clearance filter — clearance enforcement is the caller's
        responsibility (the service layer checks no-read-up before acting).
        """
        return await self._conn.fetchrow(
            "SELECT id, tracking_number, nivel_seguridad FROM radicados WHERE id = $1",
            document_id,
        )

    async def set_classification(
        self,
        document_id: uuid.UUID,
        nivel: int,
        *,
        fundamento: str | None = None,
        causal: str | None = None,
        plazo_meses: int | None = None,
    ) -> None:
        """Fija el nivel y la metadata de clasificación VIGENTE (E08, índice Ley 1712).

        Al desclasificar (`nivel <= 1`) se LIMPIA la metadata — el radicado deja de
        estar en el índice y no debe conservar un fundamento de reserva obsoleto.
        Al clasificar (`nivel >= 2`) se sella `clasificado_at = now()` y se guardan
        fundamento/causal/plazo. El `audit_log` conserva el histórico inmutable; esto
        es solo la proyección del estado vigente.

        **Clasificar RETIRA la publicación** (`publicable = FALSE`), y no como
        limpieza cosmética: sin esto la marca quedaba latente —inofensiva mientras
        el recorte público exige las dos condiciones— y la siguiente
        desclasificación, hecha meses después y con otro propósito, devolvía el
        radicado a los canales de cosecha sin que nadie hubiera decidido
        publicarlo. Es exactamente el modo de fallo que la migración 019 dice
        querer evitar, entrando por la otra puerta: no «marcar publicable algo
        reservado», sino «publicar primero y clasificar después». La reserva es un
        acto motivado que prevalece (Ley 1712 arts. 18-19), así que el retiro es su
        consecuencia legal y va en la misma transacción que su asiento.
        """
        if nivel <= 1:
            await self._conn.execute(
                "UPDATE radicados SET nivel_seguridad = $2, updated_at = now(), "
                "clasificacion_fundamento = NULL, clasificacion_causal = NULL, "
                "clasificacion_plazo_meses = NULL, clasificado_at = NULL WHERE id = $1",
                document_id,
                nivel,
            )
        else:
            await self._conn.execute(
                "UPDATE radicados SET nivel_seguridad = $2, updated_at = now(), "
                "clasificacion_fundamento = $3, clasificacion_causal = $4, "
                "clasificacion_plazo_meses = $5, clasificado_at = now(), "
                "publicable = FALSE, publicado_at = NULL, publicado_por = NULL "
                "WHERE id = $1",
                document_id,
                nivel,
                fundamento,
                causal,
                plazo_meses,
            )
            # La reserva prevalece también sobre los anexos marcados.
            await self._conn.execute(
                "UPDATE anexos SET publicable = FALSE WHERE radicado_id = $1 AND publicable",
                document_id,
            )

    async def indice_clasificados(self) -> list[asyncpg.Record]:
        """Registro de información clasificada y reservada (Ley 1712/2014 art. 20).

        SIN filtro de clearance a propósito: el índice es un registro PÚBLICO y
        CONTENT-FREE (no expone `subject`/contenido, solo metadata del acto de
        clasificación) que debe ser COMPLETO para cumplir su función legal — es
        ortogonal al clearance de lectura (como la disposición/custodia). La
        consulta NO selecciona `subject` por construcción (defensa en profundidad).
        """
        return list(
            await self._conn.fetch(
                "SELECT tracking_number, doc_type, nivel_seguridad, "
                "clasificacion_fundamento, clasificacion_causal, "
                "clasificacion_plazo_meses, clasificado_at, registered_at, "
                "origin_dept, dest_dept, registered_by "
                "FROM radicados WHERE nivel_seguridad >= 2 "
                "ORDER BY nivel_seguridad DESC, registered_at DESC"
            )
        )

    async def crear_anexo_producido(
        self,
        radicado_id: uuid.UUID,
        *,
        file_id: uuid.UUID,
        filename: str,
        file_size: int | None,
    ) -> None:
        """Anexo del documento que el SISTEMA produjo (E23, RF-PRO-03).

        `es_principal = true` y `tipo_anexo = 'documento_principal'`: es la
        comunicación misma, no un adjunto que la acompaña. El índice electrónico
        y el envío postal necesitan poder distinguirlo para saber qué pieza
        despachar.
        """
        await self._conn.execute(
            "INSERT INTO anexos (radicado_id, file_id, filename, file_size, "
            "mime_type, tipo_anexo, es_principal) "
            "VALUES ($1, $2, $3, $4, 'application/pdf', 'documento_principal', true)",
            radicado_id,
            file_id,
            filename,
            file_size,
        )

    async def list_anexos_for_document(
        self, document_id: uuid.UUID, max_clearance: int
    ) -> list[Anexo] | None:
        """Lista los anexos de un radicado respetando no-read-up (RF-SEG-08).

        `None` si el radicado no existe o está por encima del clearance del
        llamante (el router lo traduce a 404 neutro, sin revelar existencia).
        Un radicado VISIBLE sin anexos devuelve `[]` (200) — el LEFT JOIN
        distingue "visible-sin-anexos" (una fila con `a.id` NULL) de
        "no-visible" (cero filas), sin un segundo lookup ni oráculo.
        """
        rows = await self._conn.fetch(
            """
            SELECT a.id, a.radicado_id, a.file_id, a.filename, a.file_size,
                   a.mime_type, a.created_at, a.tipo_anexo, a.es_principal, a.folios
            FROM radicados r
            LEFT JOIN anexos a ON a.radicado_id = r.id
            WHERE r.id = $1 AND r.nivel_seguridad <= $2
            ORDER BY a.created_at ASC
            """,
            document_id,
            max_clearance,
        )
        if not rows:
            return None  # radicado inexistente o sobre-clearance → 404 neutro
        # `a.id` NULL = radicado visible sin anexos → lista vacía.
        return [_row_to_anexo(r) for r in rows if r["id"] is not None]

    async def list_radicado_metadata_internal(
        self, radicado_ids: list[uuid.UUID], max_clearance: int
    ) -> dict[uuid.UUID, dict]:
        """Metadata de radicados VISIBLES (`subject`/`doc_type`) para enriquecer el
        manifiesto de exportación (E02/export). Filtra por `nivel_seguridad <=
        max_clearance` — un radicado sobre-clearance NO aparece (no-read-up,
        mismo criterio que `list_anexos_for_radicados_internal` en modo export).
        Acto de sistema (D-02), scoped al tenant por `search_path`."""
        if not radicado_ids:
            return {}
        # `origin_dept`/`origin_dept_code` (dependencia PRODUCTORA) y el
        # formato/tamaño del documento principal se resuelven AQUÍ, en el
        # servicio dueño de la tabla `radicados`, y no los aporta el cliente:
        # alimentan el manifiesto de exportación y el índice electrónico que se
        # FIRMA, y un dato probatorio que el llamante puede escribir a su gusto
        # no prueba nada. El documento principal es el PRIMER anexo por
        # `created_at` (desempate por `id` para que el resultado sea
        # determinista cuando dos anexos comparten instante).
        rows = await self._conn.fetch(
            """
            SELECT r.id, r.tracking_number, r.subject, r.doc_type, r.doc_class,
                   r.origin_dept, r.origin_dept_code,
                   a.mime_type AS formato, a.file_size AS tamano_bytes
              FROM radicados r
              LEFT JOIN LATERAL (
                    SELECT mime_type, file_size
                      FROM anexos
                     WHERE radicado_id = r.id
                     ORDER BY created_at, id
                     LIMIT 1
              ) a ON TRUE
             WHERE r.id = ANY($1::uuid[]) AND r.nivel_seguridad <= $2
            """,
            radicado_ids,
            max_clearance,
        )
        return {
            r["id"]: {
                "subject": r["subject"],
                "doc_type": r["doc_type"],
                "tracking_number": r["tracking_number"],
                "doc_class": r["doc_class"],
                "origin_dept": r["origin_dept"],
                "origin_dept_code": r["origin_dept_code"],
                "formato": r["formato"],
                "tamano_bytes": r["tamano_bytes"],
            }
            for r in rows
        }

    async def list_tracking_numbers_internal(
        self, radicado_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """Números de radicado de un lote, **sin recortar por clearance**.

        Es la lectura previa a destruirlos (E04, RF-TRD-07 fase 5): recortarla
        dejaría los clasificados sin número en su asiento de auditoría, que es
        lo único que va a quedar de ellos. El no-read-up protege de LEER lo que
        no te toca; aquí no se está leyendo un documento, se está anotando cuál
        se destruyó por orden de la TRD."""
        if not radicado_ids:
            return {}
        rows = await self._conn.fetch(
            "SELECT id, tracking_number FROM radicados WHERE id = ANY($1::uuid[])",
            radicado_ids,
        )
        return {r["id"]: r["tracking_number"] for r in rows}

    async def purge_radicado(self, radicado_id: uuid.UUID) -> bool:
        """Borrado FÍSICO del radicado (E04, RF-TRD-07 fase 5).

        El único `DELETE FROM radicados` del servicio. Las tablas dependientes
        —`anexos`, `postal_shipment`, `radicado_anulacion`— caen por
        `ON DELETE CASCADE`, que es lo correcto: son partes del documento, no
        constancias del acto. Lo que da fe de la destrucción es `audit_log`, que
        no cuelga de esta fila.

        Devuelve `False` si ya no estaba: un reintento tras un fallo a medias
        tiene que converger, no fallar."""
        tag: str = await self._conn.execute(
            "DELETE FROM radicados WHERE id = $1", radicado_id
        )
        return tag.rsplit(" ", 1)[-1] != "0"

    async def file_ids_referenciados_fuera(
        self, file_ids: list[uuid.UUID], radicado_ids: list[uuid.UUID]
    ) -> set[uuid.UUID]:
        """De `file_ids`, cuáles siguen colgando de un radicado FUERA del lote.

        El almacén deduplica por huella: dos radicados con el mismo documento
        comparten fila de fichero, y no hay contador de referencias en ninguna
        parte. La purga por disposición borraba los bytes mirando solo el
        expediente que se dispone, así que destruía el anexo de OTRO expediente
        vigente que lo referenciaba. El segundo quedaba con una fila que ya no
        resuelve y una descarga que devuelve el 404 neutro — indistinguible de
        «no existe», de modo que ni siquiera se puede diagnosticar.

        La pregunta se responde AQUÍ y no en el almacén porque `anexos` es de
        este servicio: el almacén no sabe quién referencia sus objetos, y
        preguntárselo le exigiría mirar una tabla que no es suya.
        """
        if not file_ids:
            return set()
        filas = await self._conn.fetch(
            """
            SELECT DISTINCT a.file_id
              FROM anexos a
             WHERE a.file_id = ANY($1::uuid[])
               AND a.radicado_id <> ALL($2::uuid[])
            """,
            file_ids,
            radicado_ids,
        )
        return {f["file_id"] for f in filas}

    async def get_documento_principal_para_firma(
        self, ref: str
    ) -> tuple[asyncpg.Record, asyncpg.Record | None] | None:
        """Radicado por número o UUID y su PDF vigente: lo que una cadena de firma sella.

        `ref` es lo que la cadena guarda como `objeto_id`: el número de radicado
        o su UUID (igual que `resolver_nivel_por_objeto` en signature-service).
        Devuelve `None` si el radicado no existe en este tenant (schema por
        tenant, ADR-002); `(radicado, None)` si existe y no tiene documento
        principal.

        «Vigente» = SOLO el anexo `es_principal` (el más reciente si hubiera
        varios). Antes, sin principal, se tomaba «el PDF más reciente»: un
        soporte cualquiera (un anexo PDF que acompaña a la comunicación) pasaba
        por el documento firmado, y bastaba subir otro PDF para cambiar la huella
        y bloquear una cadena abierta (`documento_modificado`). Sin principal no
        hay contenido que sellar: `(radicado, None)` y el router responde
        `409 radicado_sin_documento`. Un acto de sistema sin gate de clearance:
        la respuesta es una huella, y el no-read-up lo aplicó signature-service
        al abrir la cadena.
        """
        radicado = await self._conn.fetchrow(
            "SELECT id, tracking_number FROM radicados "
            "WHERE tracking_number = $1 OR id::text = $1 LIMIT 1",
            ref,
        )
        if radicado is None:
            return None
        anexo = await self._conn.fetchrow(
            """
            SELECT id, file_id, filename
            FROM anexos
            WHERE radicado_id = $1
              AND es_principal
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            radicado["id"],
        )
        return radicado, anexo

    async def list_anexos_for_radicados_internal(
        self, radicado_ids: list[uuid.UUID], max_clearance: int | None = None
    ) -> dict[uuid.UUID, list[Anexo]]:
        """Inventario de anexos de un LOTE de radicados.

        `max_clearance=None` (AIP, acto de sistema): inventario TOTAL sin gate de
        clearance — ver el resto del docstring. `max_clearance` con valor
        (exportación de expediente, RF-SEG-08): filtra por `nivel_seguridad <=
        max_clearance` (JOIN a `radicados`) y devuelve una entrada por cada
        radicado VISIBLE (aunque no tenga anexos), OMITIENDO por completo los
        radicados sobre-clearance — así el llamador no puede ni enumerar su
        existencia (no-read-up: cierra la fuga read-up por-radicado del export,
        donde un radicado clasificado vinculado a un expediente de menor nivel
        entregaría sus bytes a un llamante sin clearance).

        Modo TOTAL (`max_clearance=None`):

        Deliberadamente distinto de `list_anexos_for_document` (no-read-up,
        RF-SEG-08): el empaquetado del AIP es un ACTO DE SISTEMA que necesita
        el inventario COMPLETO de archivos digitales del expediente — filtrar
        por el clearance de quien disparó el cierre dejaría anexos fuera en
        silencio (AIP incompleto), justo el fallo que este endpoint existe
        para evitar. Inalcanzable desde el exterior (prefijo interno +
        `require_internal_token`, ver `app/core/internal_auth.py`).

        UN solo round-trip (`WHERE radicado_id = ANY($1)`) en vez de N
        llamadas — ya scoped al tenant por el `search_path` que fija
        `get_tenant_conn`: un `radicado_id` de OTRO tenant simplemente no
        matchea ninguna fila de la tabla `anexos` de ESTE schema (aislamiento
        físico por schema, ADR-002), sin necesidad de un JOIN a `radicados`
        para filtrar. Radicados sin anexos digitales, o `radicado_id` que no
        existen en este tenant, simplemente no aparecen como clave del dict
        devuelto (el llamador lo trata como lista vacía).
        """
        if not radicado_ids:
            return {}
        if max_clearance is None:
            rows = await self._conn.fetch(
                """
                SELECT id, radicado_id, file_id, filename, file_size, mime_type,
                       created_at, tipo_anexo, es_principal, folios
                FROM anexos
                WHERE radicado_id = ANY($1::uuid[])
                ORDER BY radicado_id, created_at ASC
                """,
                radicado_ids,
            )
            result: dict[uuid.UUID, list[Anexo]] = {}
            for r in rows:
                result.setdefault(r["radicado_id"], []).append(_row_to_anexo(r))
            return result

        # Modo clearance-aware (export): primero los radicados VISIBLES
        # (nivel_seguridad <= max_clearance) → sirven de esqueleto para que el
        # llamador conozca la visibilidad incluso de los radicados sin anexos;
        # los sobre-clearance no aparecen (no-read-up).
        visibles = await self._conn.fetch(
            "SELECT id FROM radicados WHERE id = ANY($1::uuid[]) AND nivel_seguridad <= $2",
            radicado_ids,
            max_clearance,
        )
        result = {r["id"]: [] for r in visibles}
        if not result:
            return result
        rows = await self._conn.fetch(
            """
            SELECT a.id, a.radicado_id, a.file_id, a.filename, a.file_size,
                   a.mime_type, a.created_at, a.tipo_anexo, a.es_principal, a.folios
            FROM anexos a
            JOIN radicados r ON r.id = a.radicado_id
            WHERE a.radicado_id = ANY($1::uuid[]) AND r.nivel_seguridad <= $2
            ORDER BY a.radicado_id, a.created_at ASC
            """,
            radicado_ids,
            max_clearance,
        )
        for r in rows:
            result[r["radicado_id"]].append(_row_to_anexo(r))
        return result

    async def _get_anexos(self, radicado_id: uuid.UUID) -> list[Anexo]:
        rows = await self._conn.fetch(
            """
            SELECT id, radicado_id, file_id, filename, file_size, mime_type,
                   created_at, tipo_anexo, es_principal, folios
            FROM anexos
            WHERE radicado_id = $1
            ORDER BY created_at ASC
            """,
            radicado_id,
        )
        return [_row_to_anexo(r) for r in rows]


# ---------------------------------------------------------------------------
# Row → dataclass helpers
# ---------------------------------------------------------------------------


def _row_to_radicado(row: asyncpg.Record) -> Radicado:
    return Radicado(
        id=row["id"],
        tracking_number=row["tracking_number"],
        doc_type=row["doc_type"],
        year=row["year"],
        sequence=row["sequence"],
        subject=row["subject"],
        doc_class=row["doc_class"],
        origin_dept=row["origin_dept"],
        dest_dept=row["dest_dept"],
        origin_dept_code=row["origin_dept_code"],
        dest_dept_code=row["dest_dept_code"],
        sender_name=row["sender_name"],
        sender_entity=row["sender_entity"],
        status=row["status"],
        pages=row["pages"],
        nivel_seguridad=row["nivel_seguridad"],
        observations=row["observations"],
        registered_by=row["registered_by"],
        registered_at=row["registered_at"],
        updated_at=row["updated_at"],
        tenant_code=row["tenant_code"],
        due_date=row["due_date"],
        metadata=_json_obj(row["metadata"]),
        metadata_template_id=row["metadata_template_id"],
        # Acceso tolerante a propósito, y el default es el seguro. La marca de
        # publicación se podía ESCRIBIR (`set_publicable`) pero no leer: nadie
        # —ni la interfaz ni un auditor por API— podía responder «¿qué hemos
        # publicado?», que es justo lo que un esquema de publicación (Ley 1712
        # art. 12) tiene que poder acreditar. Si una consulta futura olvidara
        # las columnas, se ve «no publicado», nunca lo contrario; y la cosecha
        # real no depende de este mapeo: el chokepoint `_PUBLIC` de
        # `oai_repository` lee la columna en su propio SQL.
        publicable=bool(_col(row, "publicable", False)),
        publicado_at=_col(row, "publicado_at", None),
        flujo_dept_code=_col(row, "flujo_dept_code", None),
        flujo_dept=_col(row, "flujo_dept", None),
        flujo_usuario=_col(row, "flujo_usuario", None),
        ubicacion_dept_code=_col(row, "ubicacion_dept_code", None),
        ubicacion_dept=_col(row, "ubicacion_dept", None),
    )


def _col(row: asyncpg.Record, name: str, default: Any) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError):
        return default


def _json_obj(raw: object) -> dict:
    # asyncpg devuelve JSONB como str; toleramos dict por si hay codec configurado.
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, (str, bytes, bytearray)):
        # Ni dict, ni None, ni texto: `json.loads` lanzaría un TypeError opaco.
        # Se devuelve vacío, que es lo que esta función ya promete tolerar.
        return {}
    return json.loads(raw)


def _row_to_anexo(row: asyncpg.Record) -> Anexo:
    return Anexo(
        id=row["id"],
        radicado_id=row["radicado_id"],
        file_id=row["file_id"],
        filename=row["filename"],
        file_size=row["file_size"],
        mime_type=row["mime_type"],
        created_at=row["created_at"],
        tipo_anexo=row["tipo_anexo"],
        es_principal=row["es_principal"],
        folios=row["folios"],
    )
