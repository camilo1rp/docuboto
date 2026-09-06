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
    "municipio_codigo",
    "departamento_codigo",
    "pais",
    "activo",
    "creado_por",
    "creado_at",
    # La marca de fusión viaja con la ficha: el servicio la necesita para
    # negarse a encadenar fusiones, y la pantalla para no ofrecer como
    # superviviente algo que ya se consolidó en otra cosa.
    "fusionado_en",
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
        # `fusionado_en IS NULL`: una ficha ya consolidada en otra no puede
        # volver a elegirse al radicar, o el duplicado que alguien acaba de
        # resolver reaparecería en el siguiente radicado.
        condiciones = ["activo", "fusionado_en IS NULL"]
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

    async def buscar_por_email(self, email: str) -> list[asyncpg.Record]:
        """Fichas vivas con ese correo, **hasta dos**.

        En la ingesta de correo la dirección **es** la identidad del
        corresponsal, igual que el documento lo es en la ventanilla: es la única
        clave estable que ese canal aporta.

        Devuelve la LISTA y no «la primera», ni `None` cuando hay varias: al
        llamante le hace falta distinguir **ninguna** (hay que crearla) de
        **varias** (es un duplicado sin resolver y no se puede elegir sola).
        Colapsar las dos respuestas en `None` fue un defecto real de la primera
        versión de esto: caía a crear una TERCERA ficha, empeorando justo el
        problema que venía a resolver. Es el mismo patrón que ya mordió en otros
        sitios de este proyecto — si de la lectura depende una escritura, la
        lectura no puede devolver «vacío» para dos situaciones distintas.

        El `LIMIT 2` basta: nadie necesita saber si son dos o nueve, solo si hay
        más de una.
        """
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM tercero "
                "WHERE activo AND fusionado_en IS NULL AND lower(email) = lower($1) LIMIT 2",
                email,
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
                email, telefono, direccion, municipio, departamento,
                municipio_codigo, departamento_codigo, pais, creado_por
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
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
            datos.get("municipio_codigo"),
            datos.get("departamento_codigo"),
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
                nombre, entidad, email, telefono, direccion, municipio, departamento,
                municipio_codigo, departamento_codigo, pais
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            ON CONFLICT (radicado_id, tercero_id, rol,
                         coalesce(direccion, ''), coalesce(email, ''),
                         coalesce(municipio_codigo, '')) DO NOTHING
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
            contacto.get("municipio_codigo"),
            contacto.get("departamento_codigo"),
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
                       email, telefono, direccion, municipio, departamento,
                       municipio_codigo, departamento_codigo, pais, creado_at
                  FROM radicado_actor
                 WHERE radicado_id = $1
                 ORDER BY rol, orden ASC, nombre ASC
                """,
                radicado_id,
            )
        )

    # ── Catálogo DIVIPOLA (global, schema `public`) ─────────────────────────

    async def list_departamentos(self) -> list[asyncpg.Record]:
        """Los 33 departamentos vigentes, para el primer paso de la cascada.

        Se califica `public.` a propósito: el `search_path` de la petición
        apunta al schema del tenant, y el catálogo es nacional — no lo edita la
        entidad ni varía entre entidades."""
        return list(
            await self._conn.fetch(
                "SELECT codigo, nombre FROM public.divipola_departamento "
                "WHERE vigente ORDER BY nombre ASC"
            )
        )

    async def list_municipios(
        self, departamento_codigo: str, *, texto: str | None = None
    ) -> list[asyncpg.Record]:
        """Los municipios de UN departamento. El departamento es obligatorio:
        devolver los 1.135 de golpe sería una lista que nadie puede recorrer, y
        es justo lo que la cascada existe para evitar.

        El filtro por texto usa `public.unaccent` en la comparación —no en un
        índice— porque `unaccent` no está marcada IMMUTABLE y PostgreSQL
        rechazaría el índice de expresión. Sobre 1.135 filas no hace falta:
        quien escribe «monteria» tiene que encontrar «Montería»."""
        params: list[object] = [departamento_codigo]
        condiciones = ["departamento_codigo = $1", "vigente"]
        if texto and texto.strip():
            params.append(f"%{texto.strip()}%")
            condiciones.append(
                f"public.unaccent(nombre) ILIKE public.unaccent(${len(params)})"
            )
        return list(
            await self._conn.fetch(
                "SELECT codigo, departamento_codigo, nombre FROM public.divipola_municipio "
                f"WHERE {' AND '.join(condiciones)} ORDER BY nombre ASC",
                *params,
            )
        )

    async def get_municipio(self, codigo: str) -> asyncpg.Record | None:
        """Un municipio por su código, VIGENTE O NO.

        No se filtra por `vigente` aquí: esta consulta la usa la capa de
        servicio para resolver el nombre oficial de un código ya guardado, y un
        envío histórico a un municipio suprimido tiene que seguir pudiendo
        decir cómo se llamaba."""
        return await self._conn.fetchrow(
            "SELECT codigo, departamento_codigo, nombre, vigente "
            "FROM public.divipola_municipio WHERE codigo = $1",
            codigo,
        )

    async def get_departamento(self, codigo: str) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            "SELECT codigo, nombre, vigente FROM public.divipola_departamento WHERE codigo = $1",
            codigo,
        )

    async def desvincular_actor(self, actor_id: uuid.UUID) -> bool:
        """Quita un actor por SU identificador, no por `(radicado, tercero)`:
        el mismo tercero puede figurar dos veces con direcciones distintas, y
        borrar por la pareja se llevaría las dos."""
        resultado: str = await self._conn.execute(
            "DELETE FROM radicado_actor WHERE id = $1", actor_id
        )
        return resultado.rsplit(" ", 1)[-1] != "0"

    # ── Duplicados y fusión ─────────────────────────────────────────────────

    async def grupos_duplicados(self, *, limit: int) -> list[asyncpg.Record]:
        """Fichas vivas que probablemente son la misma persona.

        Dos ejes, y el orden importa porque no valen lo mismo:

        * **mismo documento de identidad** — no hay discusión posible, son la
          misma persona. Que existan es un residuo de antes de que el alta
          fuera idempotente, o de fichas creadas sin documento y completadas
          después.
        * **mismo nombre normalizado** (sin tildes ni mayúsculas) — es una
          *sospecha*, no un hecho: puede haber dos Juan Pérez distintos. Por eso
          la pantalla los muestra para que alguien decida y nunca los fusiona
          sola.

        Se excluyen las ya fusionadas: reaparecerían como duplicado de aquello
        en lo que se consolidaron, que es exactamente lo contrario de resolver.
        """
        return list(
            await self._conn.fetch(
                """
                WITH vivos AS (
                    SELECT id, nombre, entidad, tipo_documento, numero_documento,
                           email, municipio, creado_at
                    FROM tercero
                    WHERE activo AND fusionado_en IS NULL
                ),
                por_documento AS (
                    SELECT 'documento' AS eje,
                           tipo_documento || ':' || numero_documento AS clave,
                           count(*) AS cuantos
                    FROM vivos
                    WHERE numero_documento IS NOT NULL AND numero_documento <> ''
                    GROUP BY 1, 2 HAVING count(*) > 1
                ),
                por_nombre AS (
                    SELECT 'nombre' AS eje,
                           lower(unaccent(nombre)) AS clave,
                           count(*) AS cuantos
                    FROM vivos
                    GROUP BY 1, 2 HAVING count(*) > 1
                ),
                grupos AS (
                    SELECT * FROM por_documento UNION ALL SELECT * FROM por_nombre
                )
                SELECT g.eje, g.clave, g.cuantos,
                       jsonb_agg(
                           jsonb_build_object(
                               'id', v.id, 'nombre', v.nombre, 'entidad', v.entidad,
                               'tipo_documento', v.tipo_documento,
                               'numero_documento', v.numero_documento,
                               'email', v.email, 'municipio', v.municipio,
                               'creado_at', v.creado_at
                           ) ORDER BY v.creado_at
                       ) AS fichas
                FROM grupos g
                JOIN vivos v ON (
                    (g.eje = 'documento'
                     AND v.tipo_documento || ':' || v.numero_documento = g.clave)
                 OR (g.eje = 'nombre' AND lower(unaccent(v.nombre)) = g.clave)
                )
                GROUP BY g.eje, g.clave, g.cuantos
                -- El documento primero: es certeza, no sospecha.
                ORDER BY (g.eje = 'documento') DESC, g.cuantos DESC, g.clave
                LIMIT $1
                """,
                limit,
            )
        )

    async def radicados_de(self, tercero_id: uuid.UUID, *, limit: int) -> list[asyncpg.Record]:
        """En qué radicados aparece esta ficha, con el rol que tuvo.

        Es lo que hace falta para decidir una fusión: quién ha escrito y qué se
        le ha enviado. Sin esto, fusionar sería elegir a ciegas cuál de las dos
        fichas sobrevive.
        """
        return list(
            await self._conn.fetch(
                """
                SELECT ra.rol, ra.creado_at, r.tracking_number, r.subject, r.doc_type
                FROM radicado_actor ra
                JOIN radicados r ON r.id = ra.radicado_id
                -- Incluye los vínculos de las fichas ya FUSIONADAS en esta: el
                -- pasado sigue apuntando a la ficha que se usó ese día —el
                -- trigger de inmutabilidad lo impone—, y la fusión es
                -- conocimiento posterior que se resuelve al leer, no
                -- reescribiendo. Sin este OR, consolidar dos fichas haría
                -- DESAPARECER de la vista los radicados de la absorbida.
                WHERE ra.tercero_id = $1
                   OR ra.tercero_id IN (SELECT id FROM tercero WHERE fusionado_en = $1)
                ORDER BY ra.creado_at DESC
                LIMIT $2
                """,
                tercero_id,
                limit,
            )
        )

    async def fusionar(
        self, superviviente_id: uuid.UUID, absorbido_id: uuid.UUID, *, actor: uuid.UUID | None
    ) -> int:
        """Marca al absorbido como consolidado en el superviviente.

        **No toca `radicado_actor`. Ni una fila.** El primer diseño repuntaba el
        `tercero_id` de los vínculos, y la base lo impidió: el trigger
        `radicado_actor` inmutable de la migración 026 incluye `tercero_id`
        entre las columnas que no se editan, con este mensaje —

            «los datos de contacto de un actor son los USADOS en el envío y no
             se editan: cambiarlos daría información errónea sobre un acto ya
             ocurrido»

        — y tiene razón. A QUÉ FICHA del catálogo se atendió a alguien también
        es parte de lo que ocurrió: el día del envío existían dos fichas y se
        usó una. Reescribirlo diría que siempre hubo una sola.

        Así que la fusión no reescribe el pasado, solo añade lo que se supo
        después: estas dos fichas son la misma persona. Quien lee resuelve a
        través de `fusionado_en` (ver `radicados_de`), y los vínculos siguen
        diciendo la verdad de su momento.

        Devuelve cuántos vínculos pasan a resolverse hacia el superviviente
        —informativo, para que quien fusiona sepa el alcance de lo que hace—.
        """
        heredados: int = await self._conn.fetchval(
            "SELECT count(*) FROM radicado_actor WHERE tercero_id = $1", absorbido_id
        )
        await self._conn.execute(
            "UPDATE tercero SET fusionado_en = $1, fusionado_at = now(), "
            "fusionado_por = $2, actualizado_at = now() WHERE id = $3",
            superviviente_id,
            actor,
            absorbido_id,
        )
        return int(heredados)
