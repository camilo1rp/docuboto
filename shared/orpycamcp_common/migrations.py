# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Libro único de migraciones aplicadas (11.3).

**El defecto.** Había *dos* ejecutores de migraciones y *tres* registros que no
se veían entre sí:

===========================  =============================  ==================
Ejecutor                     Registro                       Clave
===========================  =============================  ==================
``services/*/migrations/     ``public._migrations``         nombre desnudo
runner.py`` (global)
``services/*/migrations/     ``public._tenant_migrations``  esquema + nombre
runner.py`` (tenant)                                        desnudo
``scripts/init_tenant.py``   ``public._migrations`` y       ``servicio/archivo``
                             ``tenant_X._migrations``
===========================  =============================  ==================

De ahí salen tres fallos, todos silenciosos:

1. **Migraciones saltadas.** El nombre desnudo no identifica una migración: cada
   servicio numera su serie desde 001. Hay cuatro colisiones reales entre
   ``archive``/``document`` y ``notification``/``signature``. Con la clave
   desnuda, el runner del segundo servicio ve el nombre en el registro y hace
   *skip* de una migración **suya que nunca se aplicó**. Hoy no ha hecho daño
   por casualidad —las cuatro son idempotentes y casi idénticas—, pero eso es
   suerte, no diseño.

2. **Migraciones aplicadas dos veces.** ``init_tenant.py`` reescribe los nombres
   desnudos de ``public._migrations`` a ``servicio/archivo``; después de eso, el
   runner del servicio ya no reconoce sus propias entradas y las vuelve a
   aplicar.

3. **Registros de tenant ciegos entre sí.** ``public._tenant_migrations`` y
   ``tenant_X._migrations`` cuentan la misma historia en dos sitios distintos:
   provisionar con un ejecutor y actualizar con el otro reaplica todo.

**La corrección.** Un solo libro, ``public.schema_migrations``, con la identidad
completa: **esquema + servicio + archivo**. Los dos ejecutores lo usan a través
de este módulo, así que dejan de poder divergir. Las entradas de los tres libros
antiguos se importan una vez (``importar_libros_antiguos``) y quedan ahí; las
tablas viejas no se borran, para que un despliegue pueda mirarlas si algo no
cuadra.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

# Nombre nuevo, no reutilizado: `_migrations` sigue existiendo con el contenido
# antiguo. Reutilizar el nombre habría mezclado dos convenciones de clave en la
# misma tabla, que es exactamente el problema de partida.
LEDGER = "public.schema_migrations"

# Nombres de archivo que declaran DOS servicios distintos. Existen porque cada
# servicio numera su serie desde 001, y son el motivo de este módulo: con la
# clave desnuda del registro antiguo, el runner del segundo servicio veía el
# nombre y hacía *skip* de una migración suya que nunca se aplicó.
#
# Se listan explícitamente porque un runner **no puede detectarlos**: dentro de
# su imagen solo existen sus propios directorios, así que ve un único candidato
# —él mismo—, no percibe ambigüedad y se auto-atribuye el nombre. Eso fosilizaría
# el *skip* en el libro nuevo, con identidad completa y para siempre: exactamente
# el defecto que este módulo vino a cerrar, un salto más allá.
#
# `shared/tests/test_migrations_ambiguos.py` recalcula este conjunto desde
# `services/*/migrations/**` y falla si diverge: una lista escrita a mano que
# nadie revalida caduca en silencio, que es el modo de fallo de la familia.
NOMBRES_AMBIGUOS = frozenset(
    {
        "002_batch_jobs.sql",  # archive-service, document-service
        "005_evento_dead_letter.sql",  # notification-service, signature-service
        "006_deadletter_status.sql",  # notification-service, signature-service
        "007_deadletter_origin_group_notnull_index.sql",  # notification-service, signature-service
    }
)



# ---------------------------------------------------------------------------
# Cerrojo global de migraciones (C19, ADR-041)
# ---------------------------------------------------------------------------
#
# Antes de este cerrojo el manifiesto de producción y la documentación decían
# que los `migrate-*` se «serializaban por pg_advisory_lock»; el código NO tenía
# ninguno. Nueve migradores en paralelo se pisaban en `CREATE TABLE IF NOT EXISTS
# public.schema_migrations`, en la `evento_dead_letter` que crean cuatro
# servicios y en los asientos-ancla (comprobar-y-luego-escribir).
#
# UN solo cerrojo para todos los servicios, no uno por servicio: comparten el
# libro, `public` y tablas del esquema tenant. Constante fija y legible (los
# ocho bytes ASCII de «ORPYCAM1») en vez de `hashtext(...)`, para reconocerla
# en `pg_locks` sin tener que recalcular nada. Nivel de SESIÓN: sobrevive a las
# transacciones por archivo y se libera sola si la conexión muere.
CERROJO_MIGRACIONES = 0x4F52505943414D31

PLAZO_CERROJO_POR_DEFECTO_S = 600.0
_ESPERA_ENTRE_INTENTOS_S = 0.5


def plazo_cerrojo_s() -> float:
    """`MIGRATIONS_LOCK_TIMEOUT_S` (segundos); 600 si no está. Valor inválido ⇒ error."""
    crudo = os.environ.get("MIGRATIONS_LOCK_TIMEOUT_S", "").strip()
    if not crudo:
        return PLAZO_CERROJO_POR_DEFECTO_S
    try:
        valor = float(crudo)
    except ValueError:
        raise RuntimeError(
            f"MIGRATIONS_LOCK_TIMEOUT_S={crudo!r} no es un número de segundos"
        ) from None
    if valor <= 0:
        raise RuntimeError("MIGRATIONS_LOCK_TIMEOUT_S debe ser mayor que 0")
    return valor


async def _quien_tiene_el_cerrojo(conn: asyncpg.Connection) -> str:
    """Pid que lo retiene, para el mensaje de error; best-effort."""
    try:
        pid = await conn.fetchval(
            "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted "
            "AND objsubid = 1 AND ((classid::bigint << 32) | objid::bigint) = $1 "
            "LIMIT 1",
            CERROJO_MIGRACIONES,
        )
    except Exception:  # noqa: BLE001 - diagnóstico; nunca debe tapar el error real
        return "desconocido"
    return str(pid) if pid is not None else "desconocido"


@asynccontextmanager
async def cerrojo(
    conn: asyncpg.Connection, *, timeout_s: float | None = None
) -> AsyncIterator[None]:
    """Serializa todo ejecutor de migraciones del sistema (runners e init_tenant).

    Intenta ``pg_try_advisory_lock`` cada medio segundo hasta ``timeout_s``
    (por defecto ``MIGRATIONS_LOCK_TIMEOUT_S``). Si vence, ``RuntimeError``
    legible: el proceso sale con código 1 y ``service_completed_successfully``
    no se cumple, así que el servicio NO arranca (fail-closed). Se libera en
    ``finally``.
    """
    plazo = plazo_cerrojo_s() if timeout_s is None else timeout_s
    inicio = time.monotonic()
    while not await conn.fetchval("SELECT pg_try_advisory_lock($1)", CERROJO_MIGRACIONES):
        espera = time.monotonic() - inicio
        if espera >= plazo:
            raise RuntimeError(
                f"otra migración tiene el cerrojo desde hace {espera:.0f} s "
                f"(pid {await _quien_tiene_el_cerrojo(conn)}); plazo "
                f"MIGRATIONS_LOCK_TIMEOUT_S={plazo:g} s agotado"
            )
        await asyncio.sleep(min(_ESPERA_ENTRE_INTENTOS_S, max(plazo - espera, 0.01)))
    try:
        yield
    finally:
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)", CERROJO_MIGRACIONES)
        except Exception:  # noqa: BLE001 - conexión ya caída: el servidor lo libera al cerrar la sesión
            pass


# Valor de `schema_name` para las migraciones del esquema `public`. Se guarda
# explícitamente en vez de dejarlo a NULL: un NULL en una clave única no compara
# igual consigo mismo en PostgreSQL, así que la unicidad de las globales se
# perdería sin que nada avisara.
ESQUEMA_GLOBAL = "public"


async def ensure_ledger(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            id           SERIAL PRIMARY KEY,
            schema_name  TEXT NOT NULL,
            servicio     TEXT NOT NULL,
            filename     TEXT NOT NULL,
            applied_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (schema_name, servicio, filename)
        )
        """
    )


async def applied(conn: asyncpg.Connection, schema_name: str) -> set[tuple[str, str]]:
    """Pares ``(servicio, archivo)`` ya aplicados en *schema_name*."""
    filas = await conn.fetch(
        "SELECT servicio, filename FROM public.schema_migrations WHERE schema_name = $1",
        schema_name,
    )
    return {(f["servicio"], f["filename"]) for f in filas}


async def record(
    conn: asyncpg.Connection, schema_name: str, servicio: str, filename: str
) -> None:
    await conn.execute(
        "INSERT INTO public.schema_migrations (schema_name, servicio, filename) "
        "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
        schema_name,
        servicio,
        filename,
    )


async def importar_libros_antiguos(
    conn: asyncpg.Connection,
    *,
    servicios_por_archivo_global: dict[str, list[str]],
    servicios_por_archivo_tenant: dict[str, list[str]],
    ignorar_desconocidos: bool = False,
) -> list[str]:
    """Trae al libro único lo que ya estaba aplicado, sin reaplicar nada.

    Los dos mapas dicen qué servicios declaran cada nombre de archivo; el
    llamador los construye recorriendo los directorios de migraciones que
    conoce. Se necesitan porque las entradas antiguas del runner guardan el
    nombre desnudo y hay que devolverles el servicio.

    Hay dos llamadores con visión distinta, de ahí ``ignorar_desconocidos``:

    - ``scripts/init_tenant.py`` ve los directorios de los once servicios, así
      que un nombre que no reconoce es una migración **retirada del código** y
      merece aviso.
    - ``services/<svc>/migrations/runner.py`` solo ve los suyos: dentro de su
      imagen no existen los demás. Un nombre ajeno no es una anomalía, es lo
      normal, y lo importará el runner de su servicio. Con
      ``ignorar_desconocidos=True`` se salta en silencio en vez de inundar la
      consola con avisos que no significan nada.

    Devuelve la lista de avisos para que el llamador los imprima: un nombre que
    declaran DOS servicios no se puede atribuir con certeza —el registro no
    guarda quién lo escribió—, así que se atribuye al primero por orden
    alfabético y el del otro servicio queda como pendiente. Es la decisión
    correcta: la que nunca se aplicó es justamente la del servicio que hizo
    *skip*, y volver a aplicar una migración idempotente no cuesta nada,
    mientras que dar por aplicada una que no lo está deja el esquema incompleto
    para siempre.

    Silenciar esa ambigüedad sería repetir el defecto que este módulo corrige,
    de ahí que se devuelva en vez de tragarse.
    """
    avisos: list[str] = []

    # Un llamador con visión PARCIAL (el runner de un servicio) no es autoridad
    # sobre los nombres ambiguos: vería un solo candidato y se lo quedaría.
    vision_parcial = ignorar_desconocidos

    def _atribuir(archivo: str, mapa: dict[str, list[str]], ambito: str) -> str | None:
        candidatos = sorted(mapa.get(archivo, []))
        if archivo in NOMBRES_AMBIGUOS and vision_parcial and len(candidatos) <= 1:
            # No se importa: se deja sin marcar para que la migración se APLIQUE
            # (son idempotentes, y esa es la razón por la que hoy el defecto
            # original no ha hecho daño). Dar por aplicada una que no lo está
            # deja el esquema incompleto sin ningún error; reaplicar una
            # idempotente no cuesta nada. La asimetría manda la decisión.
            avisos.append(
                f"  WARN  [{ambito}] '{archivo}' lo declaran dos servicios y este "
                "ejecutor solo ve el suyo: no se importa del registro antiguo. Si "
                "estaba pendiente, se aplica ahora; si ya estaba, es idempotente."
            )
            return None
        if not candidatos:
            if ignorar_desconocidos:
                return None
            avisos.append(
                f"  WARN  [{ambito}] '{archivo}' está registrado como aplicado pero "
                "ningún servicio lo declara: migración retirada del código. Se "
                "importa como servicio '(desconocido)' para no perder el rastro."
            )
            return "(desconocido)"
        if len(candidatos) > 1:
            avisos.append(
                f"  WARN  [{ambito}] '{archivo}' lo declaran {', '.join(candidatos)}; "
                f"el registro antiguo no guarda cuál se aplicó. Se atribuye a "
                f"'{candidatos[0]}'; la de los demás se aplicará ahora (son "
                "idempotentes)."
            )
        return candidatos[0]

    # (A) public._migrations — global. Mezcla nombres desnudos (runner) y
    #     'servicio/archivo' (init_tenant).
    if await _existe_tabla(conn, "public", "_migrations"):
        for fila in await conn.fetch("SELECT filename FROM public._migrations"):
            crudo = fila["filename"]
            if "/" in crudo:
                servicio, archivo = crudo.split("/", 1)
            else:
                archivo = crudo
                atribuido = _atribuir(archivo, servicios_por_archivo_global, "global")
                if atribuido is None:
                    continue
                servicio = atribuido
            await record(conn, ESQUEMA_GLOBAL, servicio, archivo)

    # (B) public._tenant_migrations — tenant, nombre desnudo. Aquí están las
    #     cuatro colisiones reales.
    if await _existe_tabla(conn, "public", "_tenant_migrations"):
        for fila in await conn.fetch(
            "SELECT schema_name, filename FROM public._tenant_migrations"
        ):
            archivo = fila["filename"]
            if "/" in archivo:
                servicio, archivo = archivo.split("/", 1)
            else:
                atribuido = _atribuir(archivo, servicios_por_archivo_tenant, "tenant")
                if atribuido is None:
                    continue
                servicio = atribuido
            await record(conn, fila["schema_name"], servicio, archivo)

    # (C) tenant_X._migrations — tenant, clave 'servicio/archivo'.
    esquemas = await conn.fetch(
        r"""
        SELECT schema_name FROM information_schema.schemata
        WHERE schema_name LIKE 'tenant\_%' ESCAPE '\'
        ORDER BY schema_name
        """
    )
    for fila_esq in esquemas:
        esquema = fila_esq["schema_name"]
        if not await _existe_tabla(conn, esquema, "_migrations"):
            continue
        # `esquema` viene de information_schema, no de una entrada de usuario.
        for fila in await conn.fetch(
            f'SELECT filename FROM "{esquema}"._migrations'  # noqa: S608
        ):
            crudo = fila["filename"]
            if "/" in crudo:
                servicio, archivo = crudo.split("/", 1)
            else:
                archivo = crudo
                atribuido = _atribuir(archivo, servicios_por_archivo_tenant, esquema)
                if atribuido is None:
                    continue
                servicio = atribuido
            await record(conn, esquema, servicio, archivo)

    return avisos


async def _existe_tabla(conn: asyncpg.Connection, esquema: str, tabla: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = $1 AND table_name = $2",
            esquema,
            tabla,
        )
    )
