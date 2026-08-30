# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Catálogo de terceros y actores de un radicado — E01, RF-RAD-05.

El catálogo existe para una cosa concreta: que un destinatario **tenga
identidad**. Antes vivía como texto libre en dos columnas de `radicados`, así
que «Juan Pérez», «JUAN PEREZ» y «Juan Perez Gómez» eran tres destinatarios
distintos y no había forma de responder «¿qué le hemos enviado a esta persona?».

Dos defensas contra el duplicado, y hacen falta las dos:

* **La dura**: índice único por documento de identidad (migración 026). Nadie
  crea dos terceros con la misma cédula, ni desde la pantalla ni por la API.
* **La blanda, que es la que evita la mayoría**: la búsqueda por nombre
  normalizado. Los duplicados no se crean por saltarse una restricción, se
  crean por **no encontrar lo que ya está** — quien busca «Jose Perez» tiene que
  dar con el «José Pérez» que existe.

**Y la decisión que evita la plaga del Orfeo original**, donde el mismo sujeto
llegaba a aparecer treinta veces: los **datos de contacto no viven aquí, viven
en el envío**. Atarlos a la identidad es lo que obligaba a crear un tercero
nuevo por cada dirección. `tercero.direccion` guarda el valor *vigente* como
comodidad para prellenar un formulario; lo que se usó de verdad en un radicado
está congelado en `radicado_actor` y **no se edita nunca** — cambiarlo daría
información errónea sobre un envío ya hecho con un dato que era verdad
entonces.
"""

from __future__ import annotations

import uuid

import asyncpg

_CAMPOS = (
    "id",
    "tipo",
    "nombre",
    "entidad",
    "tipo_documento",
    "numero_documento",
    "email",
    "telefono",
    "direccion",
    "municipio",
    "departamento",
    "pais",
    "activo",
    "creado_por",
    "creado_at",
)
_COLS = ", ".join(_CAMPOS)


class TerceroRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug

    async def buscar(
        self, texto: str | None, *, tipo: str | None, limit: int
    ) -> list[asyncpg.Record]:
        """Búsqueda por nombre o entidad, **sin acentos ni mayúsculas**.

        Reutiliza `public.spanish_unaccent`, la misma configuración que la
        búsqueda de expedientes: un diccionario propio aquí se separaría del
        otro en cuanto alguien afinara uno de los dos."""
        condiciones = ["activo"]
        params: list[object] = []
        if texto and texto.strip():
            params.append(texto.strip())
            condiciones.append(
                f"to_tsvector('public.spanish_unaccent'::regconfig, "
                f"coalesce(nombre,'') || ' ' || coalesce(entidad,'')) "
                f"@@ plainto_tsquery('public.spanish_unaccent'::regconfig, ${len(params)})"
            )
        if tipo:
            params.append(tipo)
            condiciones.append(f"tipo = ${len(params)}")
        params.append(limit)
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM tercero WHERE {' AND '.join(condiciones)} "
                f"ORDER BY nombre ASC LIMIT ${len(params)}",
                *params,
            )
        )

    async def get_por_documento(
        self, tipo_documento: str, numero_documento: str
    ) -> asyncpg.Record | None:
        """El tercero con ese documento, si existe.

        Se consulta ANTES de crear: devolver el que ya está es mejor que
        chocar contra el índice único y traducir el error — y, sobre todo,
        deja que crear sea idempotente."""
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM tercero WHERE tipo_documento = $1 AND numero_documento = $2",
            tipo_documento,
            numero_documento,
        )

    async def get(self, tercero_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM tercero WHERE id = $1", tercero_id
        )

    async def crear(self, datos: dict) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO tercero (
                tipo, nombre, entidad, tipo_documento, numero_documento,
                email, telefono, direccion, municipio, departamento, pais, creado_por
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            RETURNING {_COLS}
            """,
            datos["tipo"],
            datos["nombre"],
            datos.get("entidad"),
            datos.get("tipo_documento"),
            datos.get("numero_documento"),
            datos.get("email"),
            datos.get("telefono"),
            datos.get("direccion"),
            datos.get("municipio"),
            datos.get("departamento"),
            datos.get("pais") or "CO",
            datos.get("creado_por"),
        )
        assert row is not None
        return row

    # ── Actores de un radicado ──────────────────────────────────────────────

    async def vincular_actor(
        self,
        radicado_id: uuid.UUID,
        tercero_id: uuid.UUID,
        *,
        rol: str,
        orden: int,
        principal: bool,
        contacto: dict,
        canal: str | None = None,
    ) -> bool:
        """Ata un tercero al radicado **congelando los datos de contacto usados**.

        `contacto` es lo que de verdad se empleó en este envío, que puede no
        coincidir con la ficha del catálogo: al radicar se busca a alguien y se
        le corrige el correo o la dirección, porque pasa. Ese ajuste puntual es
        justamente lo que queda congelado aquí.

        `ON CONFLICT DO NOTHING` sobre `(radicado, tercero, rol, dirección,
        correo)`: **añadir dos veces al mismo destinatario en la misma dirección
        no es un error, es un no-op** —el resultado que se quería ya se cumple—,
        pero el mismo destinatario en OTRA dirección sí entra, porque enviar a
        dos direcciones de la misma persona es un envío legítimo.

        Devuelve `False` si ya estaba."""
        resultado: str = await self._conn.execute(
            """
            INSERT INTO radicado_actor (
                radicado_id, tercero_id, rol, orden, principal, canal,
                nombre, entidad, email, telefono, direccion, municipio, departamento, pais
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            ON CONFLICT (radicado_id, tercero_id, rol,
                         coalesce(direccion, ''), coalesce(email, '')) DO NOTHING
            """,
            radicado_id,
            tercero_id,
            rol,
            orden,
            principal,
            canal,
            contacto["nombre"],
            contacto.get("entidad"),
            contacto.get("email"),
            contacto.get("telefono"),
            contacto.get("direccion"),
            contacto.get("municipio"),
            contacto.get("departamento"),
            contacto.get("pais"),
        )
        return resultado.rsplit(" ", 1)[-1] != "0"

    async def list_actores(self, radicado_id: uuid.UUID) -> list[asyncpg.Record]:
        """Actores del radicado **con los datos CONGELADOS**, no con los
        vigentes del catálogo.

        Es la diferencia entre decir a dónde se envió y decir dónde vive hoy esa
        persona. `tercero_id` viaja para poder ir a la ficha actual si alguien
        la quiere; lo que se muestra como destino del radicado es la copia."""
        return list(
            await self._conn.fetch(
                """
                SELECT id, tercero_id, rol, orden, principal, canal, nombre, entidad,
                       email, telefono, direccion, municipio, departamento, pais, creado_at
                  FROM radicado_actor
                 WHERE radicado_id = $1
                 ORDER BY rol, orden ASC, nombre ASC
                """,
                radicado_id,
            )
        )

    async def desvincular_actor(self, actor_id: uuid.UUID) -> bool:
        """Quita un actor por SU identificador, no por `(radicado, tercero)`:
        el mismo tercero puede figurar dos veces con direcciones distintas, y
        borrar por la pareja se llevaría las dos."""
        resultado: str = await self._conn.execute(
            "DELETE FROM radicado_actor WHERE id = $1", actor_id
        )
        return resultado.rsplit(" ", 1)[-1] != "0"
