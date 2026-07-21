# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Resolución del *clearance* (nivel de seguridad máximo accesible) del llamante.

Gemelo del helper de document-service / archive-service (RF-SEG-08 / RF-BUS-04). El
nivel máximo de un usuario es el MAX de la matriz rol→nivel (``role_clearance``) sobre
sus grupos (``auth_memberships``); ``is_root`` accede a todo. Estas tablas las provee
auth-service pero VIVEN en el schema del tenant (ADR-002/ADR-012), por lo que
knowledge-service las resuelve desde su propia conexión (ADR-013: la BD autoriza).

La conexión debe tener el ``search_path`` ya fijado al schema del tenant (lo hace
``get_tenant_conn``). Política **fail-closed**: ante duda, PUBLICA (1).
"""

import uuid

import asyncpg

PUBLICA = 1
RESERVADA = 2
CLASIFICADA = 3  # nivel máximo (security_levels.code)

# Proveedores de embeddings que NUNCA envían el texto a un tercero (E21 Inc.1):
# corren en el propio proceso/contenedor del servicio. `local_st` (sentence-
# transformers) se suma aquí a `stub` — es la razón de ser del incremento: con
# un modelo LOCAL, el material Reservado/Clasificado SÍ puede embeberse (no
# sale). `ollama` queda deliberadamente FUERA (fail-closed): la instancia puede
# ser remota (`ollama_url` es configurable), así que hasta que exista una
# reconciliación explícita de residencia para ese proveedor (CC-03, diferido a
# Inc.3) se trata como externo.
LOCAL_EMBEDDING_PROVIDERS = frozenset({"stub", "local_st"})


def is_local_embedding_provider(provider: str) -> bool:
    """¿Este proveedor de embeddings es soberano (corre local, no exfiltra)?

    Único punto de verdad reusado por `is_embeddable` (gate de ingesta/nivel) y
    por `ingestion.apply_reclassification` (misma pregunta de residencia, sin
    el nivel de por medio) — evita que las dos rutas diverjan sobre qué cuenta
    como "local" (ver docstring de `LOCAL_EMBEDDING_PROVIDERS`).
    """
    return provider in LOCAL_EMBEDDING_PROVIDERS


def is_embeddable(nivel_seguridad: int, provider: str) -> bool:
    """Predicado PURO compartido por el router HTTP (`/ingest`) y el worker de
    ingesta event-driven (E21) — D-02 / RF-SEG-08.

    Material Reservado/Clasificado (``nivel_seguridad >= RESERVADA``) solo puede
    embeberse con un proveedor LOCAL/soberano (``LOCAL_EMBEDDING_PROVIDERS`` —
    ``stub`` o ``local_st``); un proveedor externo lo bloquea. El router traduce
    ``False`` en 403; el consumer lo traduce en un SKIP auditado (no es un
    fallo del pipeline, es política — no va a DLQ).
    """
    return not (nivel_seguridad >= RESERVADA and not is_local_embedding_provider(provider))


# --- E21 Inc.3: barrera dura material clasificado → LLM externo -------------------
#
# Gemelo de `LOCAL_EMBEDDING_PROVIDERS`/`is_local_embedding_provider` (arriba), pero
# para GENERACIÓN, no embeddings. Decisión de soberanía TOMADA (2026-07-20,
# `documentos/specDrive/00-capa-conocimiento.md`): material `nivel>=RESERVADA` NUNCA
# se envía a un LLM externo, aunque el usuario esté autorizado a verlo (el pre-filtro
# ACL de `KnowledgeRepository.search` es una capa SEPARADA y PREVIA — decide qué puede
# VER el usuario; esta barrera decide qué puede SALIR a un tercero). Fail-closed, SIN
# override configurable (a propósito: no existe un `ALLOW_CLASSIFIED_EXTERNAL` — a
# diferencia de `is_embeddable`, que documenta la posibilidad de un override futuro
# para embeddings, aquí la barrera es dura sin excepción).
LOCAL_LLM_PROVIDERS = frozenset({"ollama_local"})


def is_local_llm_provider(provider: str) -> bool:
    """¿Este proveedor de GENERACIÓN es soberano (corre local, no exfiltra)?

    `ollama_local` apunta a una instancia Ollama servida por este despliegue
    (`OLLAMA_URL`) — se trata como local por la misma razón que
    `EMBEDDING_PROVIDER=ollama` NO lo es en `is_local_embedding_provider`: aquí el
    operador la corre en su propia infraestructura por diseño (`ollama_local` es el
    DEFAULT soberano del RAG, ver `app/core/config.py`). `ollama_cloud`,
    `openai_compatible` y `anthropic` son SIEMPRE externos. `disabled` no aplica —
    el router nunca invoca un proveedor cuando `AI_PROVIDER=disabled` (cae al
    Patrón A antes de llegar aquí).
    """
    return provider in LOCAL_LLM_PROVIDERS


def is_generatable(nivel_seguridad: int, provider: str) -> bool:
    """Predicado PURO — barrera dura de residencia para el contexto que se envía a
    un LLM de GENERACIÓN (E21 Inc.3, Patrón B).

    Gemelo estructural de `is_embeddable`, pero SIN el matiz de override
    configurable que aquella documenta: material Reservado/Clasificado
    (``nivel_seguridad >= RESERVADA``) jamás puede incluirse en el contexto
    enviado a un proveedor de generación EXTERNO (``not
    is_local_llm_provider(provider)``) — decisión de soberanía TOMADA, sin
    excepción auditada posible. El router usa este predicado para EXCLUIR chunks
    del contexto (no para bloquear la petición completa): el resto del contexto
    (nivel < RESERVADA, o cualquier nivel con proveedor local) sigue enviándose.
    """
    return not (nivel_seguridad >= RESERVADA and not is_local_llm_provider(provider))


async def resolve_caller_clearance(
    conn: asyncpg.Connection, user_id: uuid.UUID | None
) -> int:
    """Devuelve el nivel de seguridad máximo (1..3) accesible por ``user_id``."""
    if user_id is None:
        return PUBLICA
    try:
        row = await conn.fetchrow(
            """
            SELECT u.is_root,
                   (SELECT MAX(rc.max_level)
                      FROM auth_memberships m
                      JOIN role_clearance rc ON rc.group_id = m.group_id
                     WHERE m.user_id = u.id) AS max_level
            FROM auth_users u
            WHERE u.id = $1
            """,
            user_id,
        )
    except asyncpg.UndefinedTableError:
        return PUBLICA
    if row is None:
        return PUBLICA
    if row.get("is_root"):
        return CLASIFICADA
    max_level = row.get("max_level")
    return int(max_level) if max_level is not None else PUBLICA
