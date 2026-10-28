# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Pasarela de conocimiento en modo `local`: sin modelos (ADR-046, fase D0).

`/search` y `/antecedentes` responden con la búsqueda de texto completo de
document-service, consultada como el usuario (`app/clients/document_search.py`).
`/rag` responde 503 ``rag_unavailable``: sin proveedor no hay generación, y
`/availability` se lo dice al frontend antes de que muestre «Sugerir texto».

La bandera de la entidad y ``PERM_CONOCIMIENTO`` (ADR-037) gobiernan todas las rutas.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator

from app.clients import document_search
from app.core.clearance import get_actor, require_asistente_habilitado, require_permission

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])

#: Etiqueta de origen de todo lo que responde el respaldo sin proveedor.
LOCAL_INDEX = "local_index"


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=2)
    top_k: int = Field(default=5, ge=1, le=50)


class SearchHit(BaseModel):
    id: uuid.UUID
    source_type: str
    source_ref: str
    texto: str
    metadata: dict[str, Any]
    score: float
    origin: Literal["local_index"] = LOCAL_INDEX


def _a_search_hit(hit: dict[str, Any]) -> SearchHit:
    return SearchHit(
        id=hit["id"],
        source_type="radicado",
        source_ref=hit["tracking_number"],
        texto=hit["subject"],
        metadata={
            k: hit.get(k)
            for k in ("doc_type", "doc_class", "sender_name", "sender_entity", "status", "registered_at")
        },
        score=float(hit.get("rank") or 0.0),
    )


@router.post(
    "/search",
    response_model=list[SearchHit],
    dependencies=[
        Depends(require_asistente_habilitado),
        Depends(require_permission("PERM_CONOCIMIENTO")),
        Depends(get_actor),
    ],
)
async def search(payload: SearchRequest, request: Request) -> list[SearchHit]:
    hits = await document_search.search(request, q=payload.query, size=payload.top_k)
    return [_a_search_hit(h) for h in hits]


ANTECEDENTES_ADVISORY = (
    "Resultados del índice local (búsqueda de texto completo de los radicados): son "
    "pistas a confirmar, no la fuente autoritativa. Verifique cada hecho contra el "
    "radicado fuente (`source_ref`) antes de afirmarlo."
)


class AntecedentesRequest(BaseModel):
    """Exactamente uno de ``query``/``radicado_ref`` (XOR, validado abajo)."""

    query: str | None = Field(default=None, min_length=2)
    radicado_ref: str | None = Field(default=None, min_length=1)
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
    origin: Literal["local_index"] = LOCAL_INDEX


def _antecedentes_source_not_found() -> HTTPException:
    """404 neutro: no distingue «no existe» de «existe pero excede su clearance»
    (anti-oráculo, RF-SEG-08); document-service ya los colapsa al buscar."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error": "antecedente_source_not_found",
            "detail": "No se encontró un antecedente para esa referencia",
            "status": 404,
        },
    )


#: Palabras del asunto que cuentan como término de búsqueda del pivote.
_PALABRA = re.compile(r"[^\W\d_]{4,}")
_MAX_TERMINOS = 8


def _consulta_desde_asunto(asunto: str) -> str:
    """Consulta OR con las palabras significativas del asunto del pivote.

    `websearch_to_tsquery` exige TODAS las palabras por defecto, y otro radicado casi
    nunca repite el asunto entero: con «or» entre términos, `ts_rank` ordena por
    cuántas comparte.
    """
    terminos = list(dict.fromkeys(p.lower() for p in _PALABRA.findall(asunto)))[:_MAX_TERMINOS]
    return " or ".join(terminos)


@router.post(
    "/antecedentes",
    response_model=AntecedentesResponse,
    dependencies=[
        Depends(require_asistente_habilitado),
        Depends(require_permission("PERM_CONOCIMIENTO")),
        Depends(get_actor),
    ],
)
async def antecedentes(payload: AntecedentesRequest, request: Request) -> AntecedentesResponse:
    """Antecedentes por texto o por radicado, citados con su ``source_ref``."""
    if payload.radicado_ref is not None:
        if payload.source_type != "radicado":
            raise _antecedentes_source_not_found()
        candidatos = await document_search.search(request, q=payload.radicado_ref, size=5)
        pivote = next(
            (h for h in candidatos if h["tracking_number"] == payload.radicado_ref), None
        )
        if pivote is None:
            raise _antecedentes_source_not_found()
        consulta = _consulta_desde_asunto(pivote["subject"])
        if not consulta:
            return AntecedentesResponse(antecedentes=[])
        # top_k + 1: el propio pivote suele ser el mejor resultado y se excluye.
        hits = await document_search.search(request, q=consulta, size=payload.top_k + 1)
        hits = [h for h in hits if str(h["id"]) != str(pivote["id"])][: payload.top_k]
    else:
        assert payload.query is not None  # garantizado por el validador XOR
        hits = await document_search.search(request, q=payload.query, size=payload.top_k)

    return AntecedentesResponse(
        antecedentes=[
            Antecedente(
                source_type="radicado",
                source_ref=h["tracking_number"],
                snippet=h["subject"],
                score=float(h.get("rank") or 0.0),
            )
            for h in hits
        ]
    )


class RagRequest(BaseModel):
    query: str = Field(..., min_length=2)
    top_k: int = Field(default=5, ge=1, le=20)


@router.post(
    "/rag",
    dependencies=[
        Depends(require_asistente_habilitado),
        Depends(require_permission("PERM_CONOCIMIENTO")),
        Depends(get_actor),
    ],
)
async def rag(payload: RagRequest) -> None:
    """Sin proveedor no hay generación: 503 honesto, nunca una respuesta simulada
    (RF-FIR-15). Con `ppi` (D1) la pasarela delegará esta consulta al proveedor."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "rag_unavailable",
            "detail": "La entidad no tiene un proveedor de inteligencia: no hay generación",
            "status": 503,
        },
    )


class AvailabilityResponse(BaseModel):
    generation: bool
    mode: Literal["local", "ppi"]


@router.get(
    "/availability",
    response_model=AvailabilityResponse,
    dependencies=[
        Depends(require_asistente_habilitado),
        Depends(require_permission("PERM_CONOCIMIENTO")),
        Depends(get_actor),
    ],
)
async def availability() -> AvailabilityResponse:
    """¿Hay generación para la entidad? Responde sin llamar a ningún proveedor.

    En D0 toda entidad está en `local`, sin generación; el frontend oculta
    «Sugerir texto» en vez de mostrar el botón y fallar.
    """
    return AvailabilityResponse(generation=False, mode="local")
