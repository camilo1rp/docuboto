# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Capa de conocimiento: ingesta y recuperación semántica con pre-filtrado ACL (E21).

D-02 (remediación): ingesta requiere X-Internal-Token (solo servicios internos); la
clasificación del chunk es obligatoria en la carga (derivada del documento origen);
material Reservado/Clasificado no puede ir a proveedores externos de embeddings.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import datetime
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from orpycamcp_common import audit
from pydantic import BaseModel, Field, model_validator

from app.core.clearance import (
    get_actor,
    is_embeddable,
    is_generatable,
    require_asistente_habilitado,
    require_permission,
    resolve_caller_clearance,
)
from app.core.config import settings
from app.core.database import get_tenant_conn
from app.repositories.kb_suggestion_repository import KbSuggestionRepository
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.embeddings import embed, to_pgvector
from app.services.ingestion import egreso_con_bandera_apagada
from app.services.llm import (
    GENERATIVE_PROVIDERS,
    RAGUnavailableError,
    RetrievedChunk,
    get_llm_provider,
)

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


def _optional_canal(x_canal: str | None = Header(default=None)) -> str:
    """Canal de origen (ui/mcp/api/system) — mismo patrón que
    `document-service`/`archive-service` (`_optional_canal`): el gateway/cliente
    puede declararlo; sin cabecera se asume `system` (llamada directa/servicio)."""
    return x_canal or "system"


def _tenant_slug(request: Request) -> str:
    """`X-Tenant-Slug` ya validado por `get_tenant_conn` (misma request) — se
    relee aquí solo para el asiento de `audit_log`, que vive en `public` y por
    tanto necesita el slug explícito (no lo infiere del `search_path`)."""
    return request.headers.get("X-Tenant-Slug", "")


def _require_internal_token(
    x_internal_token: str | None = Header(default=None),
) -> None:
    """D-02 (punto 4): solo servicios internos pueden ingestar chunks.

    Fail-closed: si INTERNAL_SERVICE_TOKEN no está configurado, todas las llamadas
    son rechazadas (401). Token presente pero incorrecto → 403.
    """
    configured = settings.internal_service_token
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "ingest_not_configured",
                "detail": "El servicio de ingesta no está habilitado (token sin configurar)",
                "status": 401,
            },
        )
    if x_internal_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "missing_internal_token",
                "detail": "Se requiere el header X-Internal-Token para ingestar chunks",
                "status": 401,
            },
        )
    # Comparación en tiempo constante, como las otras cinco copias de este gate.
    # Ésta era la única que usaba `!=`, que corta en el primer byte distinto y
    # filtra por tiempo cuánto prefijo del token se acertó. No es la diferencia
    # entre seguro e inseguro por sí sola, pero sí una divergencia dentro de un
    # control replicado: la copia que se desvía no avisa, y la próxima que se
    # escriba mirando a ésta hereda el desvío.
    if not hmac.compare_digest(x_internal_token, configured):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "invalid_internal_token",
                "detail": "Token de servicio interno inválido",
                "status": 403,
            },
        )


def _guard_external_llm(nivel_seguridad: int) -> None:
    """D-02 (punto 3): barrera dura — Reservado/Clasificado no sale a LLM externos.

    Material con nivel >= RESERVADA (2) sólo puede embeberse con el proveedor local
    (stub o modelo on-premise). Si está configurado un proveedor externo, la ingesta
    falla con 403 antes de llamar al proveedor. Envuelve el predicado puro
    ``is_embeddable`` (E21), compartido con el worker de ingesta event-driven.
    """
    if not is_embeddable(nivel_seguridad, settings.embedding_provider):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "classified_content_external_provider",
                "detail": (
                    "Material Reservado/Clasificado no puede procesarse con un "
                    "proveedor externo de embeddings (D-02 / RF-SEG-08)"
                ),
                "status": 403,
            },
        )


class IngestRequest(BaseModel):
    source_type: str = Field(..., min_length=1, max_length=40)
    source_ref: str = Field(..., min_length=1)
    texto: str = Field(..., min_length=1)
    # D-02 (punto 2): obligatorio, sin default. El llamante DEBE derivarlo del
    # documento origen; no hay valor optimista (publica) por omisión.
    nivel_seguridad: int = Field(..., ge=1, le=3)
    metadata: dict[str, Any] = Field(default_factory=dict)
    acl: dict[str, Any] = Field(default_factory=dict)


class IngestResponse(BaseModel):
    id: uuid.UUID
    source_type: str
    source_ref: str
    created_at: datetime


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    acl: dict[str, Any] = Field(default_factory=dict)


class SearchHit(BaseModel):
    id: uuid.UUID
    source_type: str
    source_ref: str
    texto: str
    metadata: dict[str, Any]
    score: float
    #: Clasificación del fragmento. Se EXPONE, aunque `/search` no la use para
    #: filtrar —el pre-filtro por clearance ya limitó lo que el usuario puede
    #: ver—, porque quien decide si algo puede además SALIR a un proveedor de
    #: generación externo es el consumidor que genera, y sin este campo no podía
    #: decidirlo. El asistente obtenía por aquí los mismos fragmentos que `/rag`
    #: retira deliberadamente del contexto de un LLM externo, y los reenviaba.
    nivel_seguridad: int = 1


@router.post(
    "/ingest",
    response_model=IngestResponse,
    status_code=201,
    dependencies=[Depends(_require_internal_token)],
)
async def ingest(
    payload: IngestRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> IngestResponse:
    # D-02 (punto 3): bloquea antes de llamar al proveedor de embeddings.
    _guard_external_llm(payload.nivel_seguridad)
    # ADR-037: con la bandera apagada solo se indexa si el proveedor es LOCAL (sin egreso).
    if await egreso_con_bandera_apagada(conn, request.headers.get("X-Tenant-Slug", "")):
        await audit.append(
            conn,
            tenant_slug=_tenant_slug(request),
            service="knowledge-service",
            action="knowledge.ingest_skipped_asistente_apagado",
            object_type=payload.source_type,
            object_ref=payload.source_ref,
            actor="system",
            canal="system",
            payload={
                "nivel_seguridad": payload.nivel_seguridad,
                "embedding_provider": settings.embedding_provider,
                "motivo": "asistente_apagado_proveedor_de_embeddings_no_local",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "asistente_deshabilitado",
                "detail": "La entidad tiene el asistente apagado y el proveedor de embeddings no es local: no se indexa.",
                "status": 403,
            },
        )
    # asyncio.to_thread: no bloquear el loop con inferencia CPU (local_st) o
    # HTTP síncrono (ollama) — ver mismo patrón en app/services/ingestion.py.
    literal = to_pgvector(await asyncio.to_thread(embed, payload.texto))
    row = await KnowledgeRepository(conn).insert_chunk(
        source_type=payload.source_type,
        source_ref=payload.source_ref,
        texto=payload.texto,
        embedding_literal=literal,
        metadata=payload.metadata,
        acl=payload.acl,
        nivel_seguridad=payload.nivel_seguridad,
    )
    return IngestResponse(**dict(row))


@router.post(
    "/search",
    response_model=list[SearchHit],
    dependencies=[
        Depends(require_asistente_habilitado),
        Depends(require_permission("PERM_CONOCIMIENTO")),
    ],
)
async def search(
    payload: SearchRequest,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    caller_id: uuid.UUID = Depends(get_actor),
) -> list[SearchHit]:
    literal = to_pgvector(await asyncio.to_thread(embed, payload.query))
    clearance = await resolve_caller_clearance(conn, caller_id)
    rows = await KnowledgeRepository(conn).search(
        embedding_literal=literal,
        top_k=payload.top_k,
        max_clearance=clearance,
        acl_filter=payload.acl or None,
    )
    return [SearchHit(**dict(r)) for r in rows]


# --- Inc.2 (E21) — Patrón A: recuperación anclada y citada, SIN LLM -----------------
#
# `search` ya hace el trabajo pesado (pre-filtro ACL + ranking vectorial); esta sección
# NO duplica esa lógica, solo la EXPONE como respuesta "segura por defecto" (advisoria,
# CC-01/CC-02) que E18/UI consumen cuando no hay generación server-side (AI_PROVIDER
# disabled). Ver diseño en la memoria de `orfeo-architect`
# (`project_e21-capa-conocimiento-real-rag.md`, Inc.2).

ANTECEDENTES_ADVISORY = (
    "Resultados de un índice DERIVADO y ADVISORIO (E21, CC-01) — no sustituyen la "
    "fuente autoritativa. Preséntelos como pistas a confirmar; para cualquier hecho "
    "concreto verifique contra el radicado/expediente fuente (`source_ref`) antes de "
    "afirmarlo (CC-02: la capa asiste, no decide)."
)


class AntecedentesRequest(BaseModel):
    """Exactamente uno de ``query``/``radicado_ref`` (XOR, validado abajo)."""

    query: str | None = Field(default=None, min_length=2)
    radicado_ref: str | None = Field(default=None, min_length=1)
    # Solo relevante junto con `radicado_ref` — la clave real es (source_type,
    # source_ref); default "radicado" porque es el caso de uso principal (C1/C6:
    # antecedentes de un radicado), pero un futuro Inc. podría pivotear sobre otro
    # `source_type` (p. ej. un expediente) sin cambiar el contrato.
    source_type: str = Field(default="radicado", min_length=1, max_length=40)
    top_k: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def _check_query_xor_radicado_ref(self) -> AntecedentesRequest:
        if bool(self.query) == bool(self.radicado_ref):
            raise ValueError(
                "Indique exactamente uno de 'query' o 'radicado_ref' (no ambos, no ninguno)"
            )
        return self


class Antecedente(BaseModel):
    source_type: str
    source_ref: str
    snippet: str
    score: float


class AntecedentesResponse(BaseModel):
    antecedentes: list[Antecedente]
    advisory: str = ANTECEDENTES_ADVISORY


def _antecedentes_source_not_found() -> HTTPException:
    """404 genérico para el pivote de ``radicado_ref``.

    Colapsa DELIBERADAMENTE dos causas indistinguibles para el llamante: (a) no
    existe ningún chunk indexado para esa referencia, y (b) existe pero su
    ``nivel_seguridad`` excede el clearance del solicitante. Diferenciarlas (p. ej.
    404 vs 403) reconstruiría el oráculo de existencia sobre radicados
    Reservados/Clasificados — mismo patrón anti-oráculo ya aplicado en el asistente
    E18 (colapso 403/404) y en la descarga de anexos (RF-SEG-08).
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error": "antecedente_source_not_found",
            "detail": "No se encontró un antecedente indexado para esa referencia",
            "status": 404,
        },
    )


@router.post(
    "/antecedentes",
    response_model=AntecedentesResponse,
    dependencies=[
        Depends(require_asistente_habilitado),
        Depends(require_permission("PERM_CONOCIMIENTO")),
    ],
)
async def antecedentes(
    payload: AntecedentesRequest,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    caller_id: uuid.UUID = Depends(get_actor),
) -> AntecedentesResponse:
    """Patrón A (E21 Inc.2): recuperación pura, ANCLADA y CITADA, sin LLM ni egreso
    externo — cada resultado trae su ``source_ref`` (tracking del radicado fuente)
    para verificación contra la fuente autoritativa (CC-01).

    Reusa `KnowledgeRepository.search` tal cual: el pre-filtro ACL NO se duplica
    aquí. El clearance del solicitante se resuelve SIEMPRE server-side desde
    `X-User-Id` (nunca del body, mismo patrón que `/search`) y conduce el
    `max_clearance` que `search` ya impone en el WHERE.
    """
    repo = KnowledgeRepository(conn)
    clearance = await resolve_caller_clearance(conn, caller_id)

    if payload.radicado_ref is not None:
        # El gate va en el WHERE de la consulta (regla 5): sobre-clearance == inexistente.
        pivot = await repo.get_pivot_chunk(
            payload.source_type, payload.radicado_ref, max_clearance=clearance
        )
        if pivot is None:
            raise _antecedentes_source_not_found()
        # top_k + 1: el propio radicado pivote es su vecino más cercano
        # (score=1.0, embedding <=> sí mismo = 0); se pide uno de más para poder
        # excluirlo y aun así devolver hasta top_k antecedentes distintos.
        rows = await repo.search(
            embedding_literal=pivot["embedding_literal"],
            top_k=payload.top_k + 1,
            max_clearance=clearance,
            acl_filter=None,
        )
        rows = [
            r
            for r in rows
            if not (
                r["source_type"] == payload.source_type
                and r["source_ref"] == payload.radicado_ref
            )
        ][: payload.top_k]
    else:
        assert payload.query is not None  # garantizado por el validador XOR
        literal = to_pgvector(await asyncio.to_thread(embed, payload.query))
        rows = await repo.search(
            embedding_literal=literal,
            top_k=payload.top_k,
            max_clearance=clearance,
            acl_filter=None,
        )

    antecedentes_hits = [
        Antecedente(
            source_type=r["source_type"],
            source_ref=r["source_ref"],
            snippet=r["texto"],
            score=r["score"],
        )
        for r in rows
    ]
    return AntecedentesResponse(antecedentes=antecedentes_hits)


# --- Inc.3 (E21) — Patrón B: RAG generativo con citas -------------------------------
#
# Reusa `KnowledgeRepository.search` para la recuperación (mismo pre-filtro ACL que
# `/search`/`/antecedentes` — no se duplica); lo nuevo es la SÍNTESIS con un
# `LLMProvider` (app/services/llm.py) y la barrera dura de soberanía (`is_generatable`,
# app/core/clearance.py) que decide qué de lo recuperado puede además SALIR a un
# proveedor externo. Ver diseño en la memoria de `orfeo-architect`
# (`project_e21-capa-conocimiento-real-rag.md`, Inc.3) y la decisión de soberanía en
# `documentos/specDrive/00-capa-conocimiento.md` (2026-07-20): material
# `nivel_seguridad >= RESERVADA` NUNCA sale a un LLM externo — fail-closed, SIN
# override configurable.

RAG_ADVISORY = (
    "Respuesta generada por un índice DERIVADO y ADVISORIO (E21 Patrón B, CC-02) — "
    "es una SUGERENCIA a confirmar, no una decisión ni la fuente autoritativa. "
    "Verifique cada cita contra el radicado/expediente fuente (`source_ref`) antes "
    "de actuar sobre ella."
)


class RagRequest(BaseModel):
    query: str = Field(..., min_length=2)
    top_k: int = Field(default=5, ge=1, le=20)


class RagCitation(BaseModel):
    cited_text: str
    source_type: str
    source_ref: str
    score: float


class RagResponse(BaseModel):
    answer: str | None
    citations: list[RagCitation]
    proveedor: str
    modelo: str | None
    confianza: float
    suggestion_id: uuid.UUID | None
    advisory: str = RAG_ADVISORY


def _rag_patron_a_fallback(
    rows: list[asyncpg.Record], *, proveedor: str
) -> RagResponse:
    """Recuperación pura, SIN generación — usada cuando `AI_PROVIDER=disabled` (o
    un nombre no reconocido) y cuando, tras la barrera dura, no queda contexto
    generable. `answer=None`/`modelo=None`/`confianza=0.0`/`suggestion_id=None`:
    sin generación no hay nada que registrar en `kb_suggestions` (esa tabla es
    auditoría del ACTO de generar, CC-03 — no se escribe una fila cuando no se
    generó nada)."""
    return RagResponse(
        answer=None,
        citations=[
            RagCitation(
                cited_text=r["texto"],
                source_type=r["source_type"],
                source_ref=r["source_ref"],
                score=r["score"],
            )
            for r in rows
        ],
        proveedor=proveedor,
        modelo=None,
        confianza=0.0,
        suggestion_id=None,
    )


@router.post(
    "/rag",
    response_model=RagResponse,
    dependencies=[
        Depends(require_asistente_habilitado),
        Depends(require_permission("PERM_CONOCIMIENTO")),
    ],
)
async def rag(
    payload: RagRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    caller_id: uuid.UUID = Depends(get_actor),
    canal: str = Depends(_optional_canal),
) -> RagResponse:
    """Patrón B (E21 Inc.3): RAG generativo ANCLADO y CITADO.

    Orden de las dos capas de seguridad (SEPARADAS, ver docstring del módulo):
      1. Pre-filtro ACL (`resolve_caller_clearance` + `search`) — qué puede VER
         el solicitante. Ya lo hace `search`, sin cambios.
      2. Barrera dura de residencia (`is_generatable`) — de lo que el
         solicitante puede ver, qué puede además SALIR a un proveedor de
         generación EXTERNO. Aplica SOLO cuando hay generación real (proveedor
         distinto de `disabled`); nunca tiene override.
    """
    repo = KnowledgeRepository(conn)
    clearance = await resolve_caller_clearance(conn, caller_id)

    literal = to_pgvector(await asyncio.to_thread(embed, payload.query))
    rows = await repo.search(
        embedding_literal=literal,
        top_k=payload.top_k,
        max_clearance=clearance,
        acl_filter=None,
    )

    provider_name = settings.ai_provider
    if provider_name not in GENERATIVE_PROVIDERS:
        # "disabled" (o cualquier nombre no registrado) -> Patrón A: recuperación
        # pura, seguro por defecto, sin egreso externo.
        return _rag_patron_a_fallback(rows, proveedor="disabled")

    # Barrera dura (capa 2): excluye del CONTEXTO que se envía al LLM los chunks
    # nivel>=RESERVADA cuando el proveedor es externo. El chunk sigue siendo
    # visible por ACL (ya pasó el filtro de `search`) — solo no sale a un tercero.
    generatable_rows = [r for r in rows if is_generatable(r["nivel_seguridad"], provider_name)]

    if not generatable_rows:
        # Tras excluir lo clasificado no queda contexto suficiente: degradación
        # HONESTA — ni se inventa una respuesta ni se cuela el material excluido
        # en el contexto. Se cae a Patrón A con TODO lo recuperado (rows, no
        # generatable_rows): el usuario SÍ puede *ver* esos resultados por ACL,
        # el límite es únicamente sobre qué puede salir a este proveedor.
        return _rag_patron_a_fallback(rows, proveedor=provider_name)

    chunks = [
        RetrievedChunk(
            source_type=r["source_type"],
            source_ref=r["source_ref"],
            texto=r["texto"],
            score=r["score"],
        )
        for r in generatable_rows
    ]

    try:
        provider = get_llm_provider(provider_name)
        generated = await asyncio.to_thread(provider.generate_grounded, payload.query, chunks)
    except RAGUnavailableError as exc:
        # RF-FIR-15 (mismo patrón que E18): degradación HONESTA, nunca una
        # respuesta simulada.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "rag_unavailable", "detail": str(exc), "status": 503},
        ) from exc

    # Confianza: heurística simple (fracción de chunks del contexto que
    # terminaron respaldando al menos una afirmación citada) — no una
    # probabilidad calibrada, solo una señal ordinal para la UI/MCP.
    confianza = round(len({c.source_ref for c in generated.citations}) / len(chunks), 4)

    # Registro CC-03 (traza de a-dónde-fue-el-contenido) + asiento en la MISMA
    # transacción (remediación de auditoría Media): un fallo del `audit.append`
    # no puede dejar una fila `kb_suggestions` huérfana sin su asiento en
    # `audit_log`. El egreso al proveedor (`generate_grounded`, arriba) ocurre
    # ANTES y queda FUERA de la tx — no se puede revertir un POST a un proveedor
    # con ROLLBACK; ese egreso-antes-del-asiento es un límite inherente conocido.
    async with conn.transaction():
        suggestion_row = await KbSuggestionRepository(conn).insert(
            tipo="rag_respuesta",
            objeto_ref=None,
            sugerencia={
                "query": payload.query,
                "answer": generated.answer,
                "citations": [
                    {
                        "cited_text": c.cited_text,
                        "source_type": c.source_type,
                        "source_ref": c.source_ref,
                        "score": c.score,
                    }
                    for c in generated.citations
                ],
            },
            proveedor=provider_name,
            modelo=generated.model_id,
            confianza=confianza,
            canal=canal,
            creada_por=caller_id,
        )

        # Asiento en audit_log (CC-03): proveedor + modelo del acto de generación,
        # SIN el contenido (el contenido vive en kb_suggestions.sugerencia).
        await audit.append(
            conn,
            tenant_slug=_tenant_slug(request),
            service="knowledge-service",
            action="rag.generado",
            object_type="kb_suggestion",
            object_ref=str(suggestion_row["id"]),
            actor=str(caller_id) if caller_id else None,
            canal=canal,
            payload={"proveedor": provider_name, "modelo": generated.model_id, "confianza": confianza},
        )

    return RagResponse(
        answer=generated.answer,
        citations=[
            RagCitation(
                cited_text=c.cited_text,
                source_type=c.source_type,
                source_ref=c.source_ref,
                score=c.score,
            )
            for c in generated.citations
        ],
        proveedor=provider_name,
        modelo=generated.model_id,
        confianza=confianza,
        suggestion_id=suggestion_row["id"],
    )


class SuggestionAcceptResponse(BaseModel):
    id: uuid.UUID
    aceptada_por: uuid.UUID
    aceptada_en: datetime


def _que_se_acepto(fila: asyncpg.Record | dict) -> dict:
    """Qué contenido se aceptó, para el asiento inmutable (sin el contenido mismo).

    `kb_suggestions` es una tabla mutable y fuera de la cadena hash: sin la huella,
    el asiento no podría probar después que el texto aceptado es el que hoy está en
    la fila. Se calcula de la fila BLOQUEADA, dentro de la misma transacción, así
    que no puede cambiar entre leerla y asentarla. Solo huella, proveedor/modelo y
    las referencias citadas (nunca su texto: Ley 1712).
    """
    sugerencia = fila["sugerencia"]
    if isinstance(sugerencia, str | bytes):
        sugerencia = json.loads(sugerencia)
    texto = sugerencia.get("answer") if isinstance(sugerencia, dict) else None
    if not isinstance(texto, str):
        texto = json.dumps(sugerencia, sort_keys=True, ensure_ascii=False, default=str)
    citas = sugerencia.get("citations") if isinstance(sugerencia, dict) else None
    fuentes = sorted(
        {c["source_ref"] for c in (citas or []) if isinstance(c, dict) and c.get("source_ref")}
    )
    return {
        "sha256_texto": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
        "proveedor": fila["proveedor"],
        "modelo": fila["modelo"],
        "fuentes": fuentes,
    }


@router.post(
    "/suggestions/{suggestion_id}/aceptar",
    response_model=SuggestionAcceptResponse,
    dependencies=[
        Depends(require_asistente_habilitado),
        Depends(require_permission("PERM_CONOCIMIENTO", min_crud=2)),
    ],
)
async def aceptar_sugerencia(
    suggestion_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    actor: uuid.UUID = Depends(get_actor),
    canal: str = Depends(_optional_canal),
) -> SuggestionAcceptResponse:
    """Aceptación HUMANA de una sugerencia del RAG (CC-02: la capa sugiere, la
    persona decide).

    Solo la acepta quien la generó (`kb_suggestions.creada_por`): sin eso, cualquiera
    con el UUID aceptaría la sugerencia de otro y `aceptada_por` no diría quién
    decidió. **404 neutro** si no existe, es de otra persona o es anterior a la
    migración 012 (sin creador): no distingue los tres casos. **409** si ya estaba
    aceptada (solo la propia persona llega a verlo). La respuesta NO incluye el
    contenido de la sugerencia. Aceptación y asiento van en UNA transacción.
    """
    repo = KbSuggestionRepository(conn)
    async with conn.transaction():
        existente = await repo.get_for_update(suggestion_id, creada_por=actor)
        if existente is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "suggestion_not_found",
                    "detail": "La sugerencia no existe o no es accesible",
                    "status": 404,
                },
            )
        if existente["aceptada_en"] is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "suggestion_already_accepted",
                    "detail": "La sugerencia ya fue aceptada",
                    "status": 409,
                },
            )
        fila = await repo.mark_accepted(suggestion_id, aceptada_por=actor)
        if fila is None:  # pragma: no cover — la fila está bloqueada arriba
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "suggestion_already_accepted",
                    "detail": "La sugerencia ya fue aceptada",
                    "status": 409,
                },
            )
        await audit.append(
            conn,
            tenant_slug=_tenant_slug(request),
            service="knowledge-service",
            action="rag.aceptada",
            object_type="kb_suggestion",
            object_ref=str(suggestion_id),
            actor=str(actor),
            canal=canal,
            payload={
                "aceptada_en": fila["aceptada_en"].isoformat(),
                **_que_se_acepto(existente),
            },
        )
    return SuggestionAcceptResponse(
        id=fila["id"], aceptada_por=fila["aceptada_por"], aceptada_en=fila["aceptada_en"]
    )
