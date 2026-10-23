# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Acceso a datos para OAI-PMH sobre EXPEDIENTES (E11, RF-INT-06). SQL crudo;
search_path fijado al schema del tenant por el router (endpoint público
por-path, gemelo de `document-service/app/repositories/oai_repository.py`).

INVARIANTE DE SEGURIDAD (Ley 1712/2014 arts. 18-19, RF-SEG-08): la cosecha
SOLO expone expedientes **públicos** (`nivel_seguridad = 1`). El filtro se
aplica AQUÍ, en la fuente — el resto de la pila (servicio OAI, gateway) nunca
ve un expediente reservado, así que la omisión es **indistinguible** por
construcción: los conteos (`completeListSize`), los *sets* y `GetRecord` se
derivan todos de este mismo filtro.

FRONTERA DE PROFUNDIDAD (crítica, ver `oai_service.py`): un expediente
PÚBLICO puede contener radicados RESERVADOS. `expediente_radicados` no lleva
el `nivel_seguridad` del radicado (esa columna vive en `radicados`, en
document-service), así que este repositorio NO PUEDE filtrar radicados
reservados uno a uno. Por eso NUNCA se seleccionan `tracking_number` de
`expediente_radicados` aquí — solo un CONTEO agregado (`radicado_count`),
que no delata identidades individuales.
"""

import json
import uuid
from datetime import datetime
from typing import Any

import asyncpg

from app.core.params_entidad import leer_param

# Un expediente es cosechable SOLO si la entidad lo publicó Y no está reservado
# (migración 042, Ley 1712/2014 art. 12). Las dos condiciones se exigen a la vez:
# la publicación es una decisión de transparencia y la clasificación una de
# reserva, y ninguna debe poder anular a la otra por descuido.
#
# `publicable` llegó tarde a este servicio: la migración 019 de document-service
# corrigió la premisa para radicados y aquí quedó `nivel_seguridad = 1`, así que
# el OAI EAD multinivel siguió difundiendo de oficio todo expediente no
# clasificado — el default de la columna. La asimetría era implícita, que es lo
# peor: quien leyera el recorte de document-service supondría que este hace lo
# mismo.
#
# No hay condición de estado, a diferencia de radicados: no existe 'anulado' en
# expedientes, y open/closed/transferred son estados normales del ciclo de vida
# en los que la difusión sigue siendo legítima. Fuente única de verdad del recorte.
_PUBLIC = "publicable AND nivel_seguridad = 1"

# `radicado_count` es un CONTEO agregado (subconsulta correlacionada) — NUNCA
# se listan los tracking_number hijos (fuga de item-level, ver docstring del
# módulo). Solo se cuentan los vínculos activos ('vinculado'); un radicado
# excluido (soft-delete, migración 018) ya no forma parte del expediente.
_EXPEDIENTE_COLS = """
    id, code, name, description, trd_serie_id, status, opened_at, closed_at,
    (SELECT count(*) FROM expediente_radicados er
      WHERE er.expediente_id = expedientes.id AND er.estado = 'vinculado'
    ) AS radicado_count
"""


class OaiRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    def _filters(
        self, from_dt: datetime | None, until_dt: datetime | None, set_spec: str | None
    ) -> tuple[str, list]:
        """Cláusula WHERE (siempre ancla en `_PUBLIC`) + parámetros posicionales.
        `set` = `status` del expediente (open/closed/transferred)."""
        clauses = [_PUBLIC]
        params: list = []
        if from_dt is not None:
            params.append(from_dt)
            clauses.append(f"opened_at >= ${len(params)}")
        if until_dt is not None:
            # Límite superior EXCLUSIVO: el servicio ya expandió `until` a
            # fin-de-rango según su granularidad (día/segundo).
            params.append(until_dt)
            clauses.append(f"opened_at < ${len(params)}")
        if set_spec is not None:
            params.append(set_spec)
            clauses.append(f"status = ${len(params)}")
        return " AND ".join(clauses), params

    async def count_public(
        self, from_dt: datetime | None, until_dt: datetime | None, set_spec: str | None
    ) -> int:
        where, params = self._filters(from_dt, until_dt, set_spec)
        return await self._conn.fetchval(
            f"SELECT count(*) FROM expedientes WHERE {where}", *params
        )

    async def list_public(
        self,
        *,
        offset: int,
        limit: int,
        from_dt: datetime | None,
        until_dt: datetime | None,
        set_spec: str | None,
    ) -> list[asyncpg.Record]:
        where, params = self._filters(from_dt, until_dt, set_spec)
        params.extend([limit, offset])
        return await self._conn.fetch(
            f"SELECT {_EXPEDIENTE_COLS} FROM expedientes WHERE {where} "
            f"ORDER BY opened_at, code LIMIT ${len(params) - 1} OFFSET ${len(params)}",
            *params,
        )

    async def get_public(self, code: str) -> asyncpg.Record | None:
        """Un expediente por su `code` — SOLO si es público. Un reservado o
        inexistente devuelve None indistinguiblemente (el router lo mapea a
        `idDoesNotExist`)."""
        return await self._conn.fetchrow(
            f"SELECT {_EXPEDIENTE_COLS} FROM expedientes WHERE code = $1 AND {_PUBLIC}",
            code,
        )

    async def sets_public(self) -> list[str]:
        """Los `setSpec` (status) que tienen AL MENOS un expediente público —
        un set que solo contenga reservados NO se enumera (canal lateral
        cerrado)."""
        rows = await self._conn.fetch(
            f"SELECT DISTINCT status FROM expedientes WHERE {_PUBLIC} ORDER BY status"
        )
        return [r["status"] for r in rows]

    async def earliest_public(self) -> datetime | None:
        return await self._conn.fetchval(
            f"SELECT min(opened_at) FROM expedientes WHERE {_PUBLIC}"
        )

    async def get_serie_chain(self, serie_id: uuid.UUID) -> list[asyncpg.Record]:
        """Cadena `trd_series.parent_id` desde la RAÍZ (parent_id NULL) hasta
        `serie_id` INCLUSIVE, ordenada de lo general a lo específico — para el
        anidamiento EAD multinivel (ISAD(G) 2.2/2.4). El CCD (TRD/series) es
        catálogo institucional, no contenido reservado: no lleva `_PUBLIC`.

        GUARDA ANTI-DoS (auditoría de seguridad #1): el FK auto-referente de
        `trd_series` NO impide ciclos (A→B→A); Postgres no los detecta solo en un
        CTE recursivo. Como este es un endpoint PÚBLICO/anónimo con amplificación
        (hasta 100 expedientes/página × una cadena cada uno), se acota la recursión
        a `depth < 50` — la profundidad real del CCD es de pocos niveles, así que el
        tope no recorta jerarquías legítimas y corta un `parent_id` en bucle."""
        return await self._conn.fetch(
            """
            WITH RECURSIVE chain AS (
                SELECT id, code, name, parent_id, 0 AS depth
                  FROM trd_series WHERE id = $1
                UNION ALL
                SELECT s.id, s.code, s.name, s.parent_id, c.depth + 1
                  FROM trd_series s
                  JOIN chain c ON s.id = c.parent_id
                 WHERE c.depth < 50
            )
            SELECT id, code, name, parent_id FROM chain ORDER BY depth DESC
            """,
            serie_id,
        )

    async def get_entidad_nombre(self) -> str | None:
        """Nombre de la entidad (`sgd_param_admin.entidad_nombre`) para titular el fondo
        del EAD (C-4/C8). `None` si no está configurado o no se puede leer: el fondo
        conserva entonces su título por omisión, no se inventa uno."""
        valor = await leer_param(self._conn, "entidad_nombre")
        return valor.strip() if isinstance(valor, str) and valor.strip() else None

    async def get_ruta_subfonds(self, serie_id: uuid.UUID) -> list[dict[str, Any]]:
        """Ruta raíz > productora del organigrama de la SERIE `serie_id`, o `[]`.

        Solo cuando la versión declara EXACTAMENTE una dependencia productora: con cero o
        con varias no hay columna de dependencia en el expediente que desempate, y elegir
        una sería inventar (fallback honesto, como el de «sin serie»). Se usa la ruta que
        fijó la convalidación (lo aprobado) y, si no la hay, la viva. Un ciclo o un
        catálogo ilegible degrada a `[]`: el endpoint es público y no debe fallar por eso."""
        try:
            prods = await self._conn.fetch(
                "SELECT dependencia_codigo, ruta_convalidada "
                "FROM trd_serie_dependencia_productora WHERE trd_serie_id = $1",
                serie_id,
            )
        except asyncpg.UndefinedTableError:
            return []
        if len(prods) != 1:
            return []
        ruta = prods[0]["ruta_convalidada"]
        if isinstance(ruta, (str, bytes)):
            ruta = json.loads(ruta)
        if ruta:
            return list(ruta)
        try:
            filas = await self._conn.fetch(
                """
                WITH RECURSIVE sube AS (
                    SELECT d.id, d.parent_id, d.codigo, d.sigla, d.nombre, 1 AS prof
                      FROM dependencias d WHERE d.codigo = $1
                    UNION ALL
                    SELECT p.id, p.parent_id, p.codigo, p.sigla, p.nombre, s.prof + 1
                      FROM sube s JOIN dependencias p ON p.id = s.parent_id
                     WHERE s.prof < 32
                ) CYCLE id SET es_ciclo USING camino
                SELECT codigo, sigla, nombre, es_ciclo, prof, (parent_id IS NOT NULL) AS tiene_padre
                  FROM sube ORDER BY prof DESC
                """,
                prods[0]["dependencia_codigo"],
            )
        except asyncpg.UndefinedTableError:
            return []
        if any(f["es_ciclo"] for f in filas):
            return []
        # Cortada por el tope de profundidad sin llegar a la raíz: una ruta truncada mostraría
        # una sección que no es la raíz. Se degrada, no se emite a medias.
        if filas and max(filas, key=lambda f: f["prof"]).get("tiene_padre"):
            return []
        return [{"codigo": f["codigo"], "sigla": f["sigla"], "nombre": f["nombre"]} for f in filas]
