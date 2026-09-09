# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Auditoría inmutable contra ``public.audit_log`` (ADR-008).

Cada fila guarda ``hash = sha256(prev_hash || contenido_canónico)``, donde
``prev_hash`` es el hash de la última fila del **mismo tenant**. Alterar o borrar
una fila rompe la cadena de todas las posteriores → manipulación evidente.

Reglas:
- Es el **único** punto de escritura de ``audit_log``; nadie inserta "a mano".
- La cadena se serializa **por tenant** con un advisory lock transaccional, de
  modo que dos ``append`` concurrentes del mismo tenant no compitan por ``prev_hash``
  (tenants distintos no se bloquean entre sí).
- Append-only: esta librería nunca hace ``UPDATE``/``DELETE`` (además el trigger
  de BD lo rechaza).

Uso::

    from orpycamcp_common import audit

    await audit.append(
        conn,
        tenant_slug="icetex",
        service="document-service",
        action="radicado.creado",
        object_type="radicado",
        object_ref="2024-ICETEX-E-001234",
        actor=user_id,
        payload={"asunto": "..."},
    )
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
import json
import logging
from dataclasses import dataclass
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Cuando un evento no pertenece a ningún tenant (p. ej. refresh/logout de auth),
# se ancla en una cadena de sistema dedicada en vez de violar el NOT NULL.
SYSTEM_TENANT = "__system__"

# Enum cerrado de canales válidos (espejo del CHECK de BD en migration 003).
# El gateway deriva el valor server-side; nunca lo acepta de cabeceras de cliente.
#   api    — acceso REST directo
#   ui     — petición originada en el frontend SvelteKit
#   mcp    — petición originada en el MCP-server
#   system — operación interna de un job/worker
VALID_CANALES: frozenset[str] = frozenset({"api", "ui", "mcp", "system"})


def _validate_canal(canal: str) -> None:
    """Valida que el canal sea uno de los valores registrados.

    Raises:
        ValueError: si ``canal`` no pertenece a ``VALID_CANALES``.
    """
    if canal not in VALID_CANALES:
        raise ValueError(
            f"Invalid canal {canal!r}. Allowed values: {sorted(VALID_CANALES)}"
        )

_INSERT_SQL = """
    INSERT INTO public.audit_log
        (tenant_slug, service, actor, canal, action, object_type, object_ref,
         payload, prev_hash, hash, ts)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11)
"""

_LAST_HASH_SQL = """
    SELECT hash
    FROM public.audit_log
    WHERE tenant_slug = $1
    ORDER BY id DESC
    LIMIT 1
"""


def _canonical(
    prev_hash: str | None,
    *,
    ts: str | None = None,
    tenant_slug: str,
    service: str,
    actor: str | None,
    canal: str,
    action: str,
    object_type: str,
    object_ref: str | None,
    payload: dict[str, Any],
) -> str:
    """Representación determinista de la fila para el cálculo del hash.

    `ts=None` reproduce la forma ANTERIOR, la que no cubría la marca de tiempo.
    Se conserva porque los asientos ya escritos se sellaron así y el registro es
    de solo-anexar: no se pueden recalcular. `verify_chain` prueba la forma nueva
    y, si no casa, la vieja — y deja constancia de cuáles siguen sin cubrir su
    `ts`, en vez de dar por buena una garantía que esos asientos no tienen.

    Por qué importa: en un registro archivístico el CUÁNDO es la mitad de la
    prueba. Sin el `ts` dentro del sello, cambiar la fecha de un asiento —mover
    una consulta a un documento reservado fuera del periodo que audita un
    inspector— no rompía la cadena y la reverificación lo daba por íntegro.
    """
    campos: dict[str, Any] = {}
    if ts is not None:
        campos["ts"] = ts
    body = json.dumps(
        {
            **campos,
            "tenant_slug": tenant_slug,
            "service": service,
            "actor": actor,
            "canal": canal,
            "action": action,
            "object_type": object_type,
            "object_ref": object_ref,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"{prev_hash or ''}|{body}"


def compute_hash(prev_hash: str | None, **fields: Any) -> str:
    """Hash SHA-256 de una fila dado el hash previo (expuesto para verificación).

    **Sin clave, y conviene saber qué significa eso.** La cadena prueba que la
    historia no se reescribe: modificar y borrar están cerrados por privilegios
    y la inserción retroactiva exigiría recalcular las filas siguientes, que es
    un UPDATE. Lo que NO prueba es la AUTENTICIDAD de un asiento nuevo — quien
    pueda insertar puede calcular su hash correcto con esta misma función y
    dejar un asiento indistinguible de uno legítimo. Ninguna cadena de hash sin
    secreto puede evitarlo: añadir es la operación que una cadena permite.

    Comprobado contra el registro real (B12, transacción revertida): 932
    asientos íntegros, se añade uno forjado y `verify_chain` sigue devolviendo
    cero roturas. Cerrarlo sería sellar con HMAC bajo una clave que el rol de
    base de datos no tenga, o anclar el extremo de la cadena fuera del sistema
    —lo que ya hace el respaldo—; está declarado en
    `auth-service/migrations/global/006_audit_log_hash_forma.sql`.
    """
    canonical = _canonical(prev_hash, **fields)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def append(
    conn: asyncpg.Connection,
    *,
    tenant_slug: str | None,
    service: str,
    action: str,
    object_type: str,
    object_ref: str | None = None,
    actor: str | None = None,
    canal: str = "api",
    payload: dict[str, Any] | None = None,
) -> str:
    """Añade una entrada de auditoría y devuelve su hash.

    Se ejecuta dentro de una (sub)transacción para que el advisory lock por
    tenant garantice el orden de la cadena. Si ``conn`` ya está en transacción,
    ``conn.transaction()`` crea un savepoint.
    """
    _validate_canal(canal)

    tenant = tenant_slug or SYSTEM_TENANT
    payload = payload or {}

    # El instante lo fija la APLICACIÓN, no el DEFAULT de la tabla.
    #
    # No es un capricho: el sello tiene que cubrir el `ts`, y para eso lo que se
    # sella y lo que se guarda han de ser el MISMO valor. Con `DEFAULT now()` el
    # instante nacía después del cálculo, así que el sello no podía incluirlo —
    # y sin él, cambiar la fecha de un asiento no rompía la cadena.
    ts = datetime.now(UTC)

    fields = {
        "ts": ts.isoformat(),
        "tenant_slug": tenant,
        "service": service,
        "actor": actor,
        "canal": canal,
        "action": action,
        "object_type": object_type,
        "object_ref": object_ref,
        "payload": payload,
    }

    async with conn.transaction():
        # Serializa la cadena de ESTE tenant (no bloquea otros tenants).
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", tenant)
        prev_hash = await conn.fetchval(_LAST_HASH_SQL, tenant)
        row_hash = compute_hash(prev_hash, **fields)
        await conn.execute(
            _INSERT_SQL,
            tenant,
            service,
            actor,
            canal,
            action,
            object_type,
            object_ref,
            json.dumps(payload, default=str),
            prev_hash,
            row_hash,
            ts,
        )
    return row_hash


async def append_denial(
    conn: asyncpg.Connection,
    *,
    tenant_slug: str | None,
    service: str,
    actor: str | None,
    perm_name: str,
    min_crud: int = 1,
    canal: str = "api",
    motivo: str = "permiso_insuficiente",
) -> None:
    """Deja traza de un intento de acceso DENEGADO por el gate RBAC (RF-SEG / MoReq).

    ``require_permission`` levanta el 403 **antes** de llegar al handler del
    servicio, así que ninguna de las llamadas a :func:`append` que hace la lógica
    de negocio se ejecuta para una denegación — sin este helper, la denegación
    no deja traza alguna en ``audit_log``. Pensado para invocarse justo antes del
    ``raise HTTPException(403)`` en la rama de permiso insuficiente de cada gate.

    Escribe ``action="acceso.denegado"``, ``object_type="rbac"`` (constante:
    ``object_type`` es ``NOT NULL`` y el objeto denegado siempre es "un permiso",
    no un recurso de negocio concreto), ``object_ref=perm_name``.

    ``motivo`` distingue la causa probatoria del asiento en ``payload.motivo``.
    Por defecto ``"permiso_insuficiente"`` (el caso normal: el llamante SÍ está
    aprovisionado pero no tiene el permiso/crud exigido). Pásese un valor
    distinto (p. ej. ``"usuario_no_aprovisionado"``) cuando la denegación no
    proviene de una verificación de permiso concreta, para que un analista que
    filtre ``audit_log`` por ``payload.motivo`` no clasifique mal el evento.

    **Best-effort**: envuelta en su propio ``try/except`` que nunca relanza — una
    denegación de acceso jamás debe degradar a un 500 por un fallo de auditoría
    (BD caída, tabla no provisionada, etc.). Ante fallo, deja un ``warning`` en el
    log del servicio en vez de propagar.

    Nota de llamador: como esta función nunca lanza, un test que quiera simular
    un fallo de auditoría típicamente sustituye la función entera (mock/monkeypatch)
    en vez de forzar una excepción interna — en ese caso el ``try/except`` de aquí
    no aplica y el llamador (el gate) es responsable de no dejar que ese mock
    reviente el flujo 403 (ver ``require_permission`` en cada ``clearance.py``).
    """
    try:
        await append(
            conn,
            tenant_slug=tenant_slug,
            service=service,
            action="acceso.denegado",
            object_type="rbac",
            object_ref=perm_name,
            actor=actor,
            canal=canal,
            payload={
                "perm": perm_name,
                "min_crud": min_crud,
                "motivo": motivo,
            },
        )
    except Exception:  # noqa: BLE001 — best-effort: nunca convertir un 403 en 500.
        logger.warning(
            "audit.append_denial failed (service=%s tenant=%s perm=%s)",
            service,
            tenant_slug,
            perm_name,
            exc_info=True,
        )


@dataclass(frozen=True, slots=True)
class RoturaCadena:
    """Una fila cuyo hash almacenado no cuadra con su contenido."""

    id: int
    #: "hash" (el contenido no produce el hash guardado), "prev_hash" (no
    #: encadena con la fila anterior) o ambos.
    motivo: str


@dataclass(frozen=True, slots=True)
class ResultadoCadena:
    """Lo que se puede AFIRMAR sobre la cadena de un tenant, no solo si pasa.

    `audit_log` es append-only por Ley 594: una rotura no se puede reparar
    nunca. Con la verificación anterior —que retornaba en la PRIMERA— eso
    significaba que una sola fila mala inutilizaba la comprobación para el resto
    de la vida del sistema: la herramienta dejaba de servir exactamente en la
    situación para la que existe, después de un incidente. Y decía menos de lo
    que la entidad puede demostrar: ante un juez importa poder afirmar «2052
    asientos íntegros y estas 4 filas rotas, aquí están sus id», no «desde la
    41004 no sé nada».
    """

    total: int
    roturas: tuple[RoturaCadena, ...]
    #: Roturas que existen y no se enumeran (tope `MAX_ROTURAS_REPORTADAS`).
    #: Se cuentan igual: un registro masivamente corrupto no puede parecer que
    #: tiene exactamente 100 problemas.
    roturas_omitidas: int = 0
    #: Asientos sellados con la forma ANTERIOR, la que no cubría su marca de
    #: tiempo. No son roturas —su sello es correcto para la forma con que se
    #: escribieron— pero sobre ellos no se puede afirmar que la fecha esté
    #: protegida. Decir «cadena íntegra» sin distinguirlos daría por probado un
    #: CUÁNDO que nadie selló, y en un registro archivístico el cuándo es la
    #: mitad de la prueba.
    sin_ts_sellado: int = 0

    @property
    def ok(self) -> bool:
        return not self.roturas and not self.roturas_omitidas

    @property
    def primera_rota(self) -> int | None:
        return self.roturas[0].id if self.roturas else None

    @property
    def total_roturas(self) -> int:
        return len(self.roturas) + self.roturas_omitidas

    @property
    def verificados(self) -> int:
        return self.total - self.total_roturas


#: Tope de roturas ENUMERADAS. Recorrer entero no cuesta más memoria (la
#: consulta ya traía todas las filas), pero devolver una lista sin tope sí:
#: sobre un registro entero corrupto serían millones de ids en la respuesta.
MAX_ROTURAS_REPORTADAS = 100


async def verify_chain(conn: asyncpg.Connection, tenant_slug: str) -> ResultadoCadena:
    """Recalcula la cadena de un tenant ENTERA y reporta todas las roturas.

    Devuelve un `ResultadoCadena`. Antes devolvía `(ok, id_de_la_primera_rota)`
    y **retornaba en la primera** — ver el docstring de `ResultadoCadena` para
    por qué eso era un problema y no una economía.

    **Tras una rotura se sigue encadenando desde el hash ALMACENADO**, no desde
    el recomputado. Es la decisión que hace que el recuento signifique algo: con
    el recomputado, una sola fila mala pintaría de rotas todas las siguientes y
    el resultado volvería a ser «desde aquí no sé nada» con más pasos. Así cada
    fila se juzga contra su propio predecesor tal y como está escrito, y las
    roturas quedan localizadas donde de verdad están.

    **`timeout` explícito.** Los pools de los servicios fijan un
    `command_timeout` (120 s) para que una consulta atascada no retenga su
    conexión indefinidamente. Esta consulta es la excepción conocida: recorre el
    registro **entero** del tenant, que en una entidad con años de operación son
    millones de filas, así que el tope general la mataría justo cuando más falta
    hace — comprobar la integridad de la cadena de custodia. Se le concede una
    hora.

    (Traer el registro completo a memoria es un problema de escala distinto y
    anterior a este tope; queda declarado, no resuelto aquí. Recorrer entero en
    vez de parar en la primera rotura **no lo empeora**: las filas ya se traían
    todas.)
    """
    # Se recorre POR LOTES, con cursor, en vez de traer el registro entero.
    #
    # Antes se hacía un `fetch` de todas las filas del tenant —nueve columnas,
    # incluido el `payload`— materializando la lista completa, y con un plazo
    # explícito de una hora que anulaba a propósito el del pool. Sobre un archivo
    # con años de historia eso es cientos de megabytes en el proceso de
    # autenticación, y el endpoint que lo dispara está al alcance de cualquier
    # administrador: una consulta barata pidiendo trabajo sin techo.
    #
    # El cursor mantiene la propiedad que importa —recorrer en orden y enlazar
    # cada fila con la anterior— sin que el coste dependa del tamaño del
    # registro. El plazo de una hora ya no hace falta y se retira: era la señal
    # de que algo se estaba pidiendo mal.
    prev_hash: str | None = None
    roturas: list[RoturaCadena] = []
    omitidas = 0
    sin_ts_sellado = 0
    total = 0

    async with conn.transaction():
        cursor = conn.cursor(
            """
            SELECT id, ts, service, actor, canal, action, object_type, object_ref,
                   payload, prev_hash, hash
            FROM public.audit_log
            WHERE tenant_slug = $1
            ORDER BY id ASC
            """,
            tenant_slug,
        )
        async for row in cursor:
            total += 1
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            comunes = {
                "tenant_slug": tenant_slug,
                "service": row["service"],
                "actor": row["actor"],
                "canal": row["canal"],
                "action": row["action"],
                "object_type": row["object_type"],
                "object_ref": row["object_ref"],
                "payload": payload,
            }
            # Forma vigente: el sello cubre el instante.
            expected = compute_hash(prev_hash, ts=row["ts"].isoformat(), **comunes)

            if expected != row["hash"]:
                # Forma ANTERIOR, la que no cubría el `ts`. Se acepta porque esos
                # asientos se sellaron así y el registro es de solo-anexar: no se
                # pueden recalcular. Pero se CUENTAN, para no afirmar sobre ellos
                # una garantía que no tienen — decir «cadena íntegra» sin más
                # daría por protegida una fecha que nadie selló.
                legado = compute_hash(prev_hash, **comunes)
                if legado == row["hash"]:
                    expected = legado
                    sin_ts_sellado += 1

            motivos = []
            if expected != row["hash"]:
                motivos.append("hash")
            if row["prev_hash"] != prev_hash:
                motivos.append("prev_hash")
            if motivos:
                if len(roturas) < MAX_ROTURAS_REPORTADAS:
                    roturas.append(RoturaCadena(id=row["id"], motivo="+".join(motivos)))
                else:
                    omitidas += 1
            prev_hash = row["hash"]

    if sin_ts_sellado:
        logger.info(
            "verify_chain %s: %d de %d asientos usan la forma anterior de sello, "
            "que NO cubre su marca de tiempo.",
            tenant_slug,
            sin_ts_sellado,
            total,
        )
    return ResultadoCadena(
        total=total,
        roturas=tuple(roturas),
        roturas_omitidas=omitidas,
        sin_ts_sellado=sin_ts_sellado,
    )
