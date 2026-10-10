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

import logging
import uuid

import asyncpg
from fastapi import Depends, Header, HTTPException, Request, status
from orpycamcp_common.rbac import (  # noqa: F401 — re-exportados: los demás módulos del servicio importan de aquí
    CLASIFICADA,
    PUBLICA,
    check_permission,
    mensaje_denegacion,
    nivel_efectivo,
    resolve_caller_clearance,
)

from app.core.config import settings
from app.core.database import get_tenant_conn

try:
    from orpycamcp_common import audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "knowledge-service"
logger = logging.getLogger(__name__)

RESERVADA = 2

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


def url_es_local(url: str) -> bool:
    """¿El host de ``url`` es loopback, red privada, hostname de contenedor o ``.local``?

    Es la prueba de RESIDENCIA del destino de ``ollama_local``: el nombre del
    proveedor solo dice lo que el operador *quiso*, el host dice a dónde sale de
    verdad el contexto. Fail-closed: una URL sin host, o con un FQDN público
    (``https://ollama.example.com``), NO es local. Un FQDN que resuelve a la red
    interna de la entidad tampoco se reconoce por nombre: se declara por IP
    privada o con sufijo ``.local``.
    """
    import ipaddress
    from urllib.parse import urlparse

    try:
        host = (urlparse(url or "").hostname or "").strip().lower()
    except ValueError:
        return False
    if not host:
        return False
    # Hostname de contenedor (sin punto, p. ej. "ollama"), loopback por nombre, *.local.
    if host == "localhost" or "." not in host or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # FQDN público: no es un host local reconocible.
    return ip.is_loopback or ip.is_private or ip.is_link_local


def is_local_llm_provider(provider: str, ollama_url: str | None = None) -> bool:
    """¿Este proveedor de GENERACIÓN es soberano (corre local, no exfiltra)?

    `ollama_local` es soberano **si y solo si su destino real lo es**: antes se
    decidía por NOMBRE, y con ``AI_PROVIDER=ollama_local`` más una ``OLLAMA_URL``
    pública, `/knowledge/rag` enviaba chunks Reservados/Clasificados a ese host
    dejando solo un warning en el log (AREA-11-12). Ahora además se exige que el
    host de ``ollama_url`` (por defecto ``settings.ollama_url``) sea local
    (:func:`url_es_local`). `ollama_cloud`, `openai_compatible` y `anthropic` son
    SIEMPRE externos. `disabled` no aplica — el router nunca invoca un proveedor
    cuando `AI_PROVIDER=disabled` (cae al Patrón A antes de llegar aquí).
    """
    if provider not in LOCAL_LLM_PROVIDERS:
        return False
    return url_es_local(settings.ollama_url if ollama_url is None else ollama_url)


def is_generatable(nivel_seguridad: int, provider: str, ollama_url: str | None = None) -> bool:
    """Predicado PURO — barrera dura de residencia para el contexto que se envía a
    un LLM de GENERACIÓN (E21 Inc.3, Patrón B).

    Gemelo estructural de `is_embeddable`, pero SIN el matiz de override
    configurable que aquella documenta: material Reservado/Clasificado
    (``nivel_seguridad >= RESERVADA``) jamás puede incluirse en el contexto
    enviado a un proveedor de generación EXTERNO (``not
    is_local_llm_provider(provider, ollama_url)``, que incluye un ``ollama_local``
    apuntado a un host público) — decisión de soberanía TOMADA, sin excepción
    auditada posible. El router usa este predicado para EXCLUIR chunks del
    contexto (no para bloquear la petición completa): el resto del contexto
    (nivel < RESERVADA, o cualquier nivel con proveedor local) sigue enviándose.
    """
    return not (
        nivel_seguridad >= RESERVADA and not is_local_llm_provider(provider, ollama_url)
    )


# ---------------------------------------------------------------------------
# Gate de autorización del admin de la DLQ (AREA-10-10, ADR-021 Fase 5)
# ---------------------------------------------------------------------------
#
# Gemelo de signature-service/app/core/clearance.py: RBAC fail-closed resuelto en
# la BD del tenant (ADR-013) y no-read-up sobre el radicado referenciado por el
# dead-letter, con 404 neutro.


def require_permission(perm_name: str, min_crud: int = 1):
    """FastAPI dependency factory: raises 401/403 when caller lacks ``perm_name``.

    Reads actor UUID from X-User-Id header (injected by api-gateway after auth-service
    /validate resolved the internal id).  Raises 401 when the header is absent or not
    a valid UUID; 403 when permission is denied or user not found (fail-closed).

    ``min_crud`` (default ``1``, backward-compatible) requires a minimum level on the
    0..5 crud scale (RF-SEG-03); see :func:`check_permission`.

    Usage at endpoint level::

        @router.post("/...", dependencies=[Depends(require_permission("PERM_DLQ_ADMIN"))])

    Or at router level to protect all routes::

        router = APIRouter(dependencies=[Depends(require_permission("PERM_DLQ_ADMIN"))])
    """

    async def _dep(
        actor: uuid.UUID = Depends(get_actor),
        x_tenant_slug: str | None = Header(default=None),
        conn: asyncpg.Connection = Depends(get_tenant_conn),
    ) -> None:
        if not await check_permission(conn, actor, perm_name, min_crud):
            # Traza best-effort del acceso denegado (RF-SEG / MoReq). ``actor``
            # aquí SIEMPRE está identificado — ``get_actor`` ya devolvió 401 si
            # faltaba/era inválido X-User-Id — así que no hay caso anónimo que
            # excluir (a diferencia de document/archive/workflow).
            if _AUDIT_AVAILABLE:
                try:
                    await audit.append_denial(
                        conn,
                        tenant_slug=x_tenant_slug,
                        service=_SERVICE,
                        actor=str(actor),
                        perm_name=perm_name,
                        min_crud=min_crud,
                    )
                except Exception:  # noqa: BLE001 — best-effort: nunca 500 por auditoría.
                    logger.warning("audit.append_denial failed", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "forbidden",
                    "detail": mensaje_denegacion(
                        perm_name, min_crud, await nivel_efectivo(conn, actor, perm_name)
                    ),
                    "status": 403,
                },
            )

    return _dep


async def get_actor(request: Request) -> uuid.UUID:
    """Extracts the caller's internal UUID from X-User-Id header.

    Raises 401 when the header is missing or not a valid UUID.  Never touches the
    database — identity was already validated by auth-service /validate.
    """
    raw = request.headers.get("X-User-Id")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "unauthorized",
                "detail": "Falta la cabecera X-User-Id",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "unauthorized",
                "detail": "La cabecera X-User-Id debe ser un UUID válido (auth_users.id interno)",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
