# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Pasarela de conocimiento (ADR-045, ADR-046).

Sin proveedor, `/search` y `/antecedentes` responden con la búsqueda de texto
completo de document-service, consultada como el usuario. Con `ppi`, buscar
delega: cada cita se rechequea y, si el proveedor no responde, vuelve el índice
local. `/rag`, `/availability` y `/transmission` se retiraron (ADR-051 §2).

La bandera de la entidad y ``PERM_CONOCIMIENTO`` (ADR-037) gobiernan todas las rutas.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from orpycamcp_common.clearance import get_actor
from orpycamcp_common.clearance import require_actor_permission as require_permission
from orpycamcp_common.database import get_tenant_conn
from orpycamcp_common.usuario import canal_de, id_opcional, slug_de
from pydantic import BaseModel, Field, model_validator

from docuboto.config import knowledge as settings
from document import api as document_api
from knowledge.core import consulta, paso
from knowledge.core.clearance import require_asistente_habilitado
from knowledge.core.salida_http import transporte_de_consulta

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])

#: Etiqueta de origen de todo lo que responde el respaldo sin proveedor.
LOCAL_INDEX: Literal["local_index"] = "local_index"


async def _buscar_texto(request: Request, *, q: str, size: int) -> list[dict[str, Any]]:
    """Texto completo de document COMO el usuario (ADR-046 §2).

    document aplica su clearance sobre el nivel vivo del radicado, así que aquí
    no se duplica ninguna barrera. Fail-closed: 503, nunca una lista vacía que
    parezca «no hay».
    """
    try:
        return await document_api.search(
            tenant_slug=slug_de(request),
            actor=id_opcional(request),
            canal=canal_de(request),
            q=q,
            size=size,
        )
    except document_api.DocumentUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "search_unavailable",
                "detail": "La búsqueda de texto completo no está disponible",
                "status": 503,
            },
        ) from None


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=2)
    top_k: int = Field(default=5, ge=1, le=50)
    record_ref: uuid.UUID | None = None


class SearchHit(BaseModel):
    id: uuid.UUID
    source_type: str
    source_ref: str
    texto: str
    metadata: dict[str, Any]
    score: float
    origin: Literal["local_index", "ppi"] = LOCAL_INDEX


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
async def search(
    payload: SearchRequest,
    request: Request,
    conn: Any = Depends(get_tenant_conn),
) -> list[SearchHit]:
    delegada = await consulta.buscar(
        conn,
        _slug(request),
        _cliente(),
        texto=payload.query,
        limite=payload.top_k,
        actor=await get_actor(request),
        alcance=payload.record_ref,
    )
    if delegada.delegada:
        return [_hit_delegado(h) for h in delegada.hallazgos]
    hits = await _buscar_texto(request, q=payload.query, size=payload.top_k)
    return [_a_search_hit(h) for h in hits]


def _hit_delegado(hallazgo: consulta.Hallazgo) -> SearchHit:
    return SearchHit(
        id=hallazgo.id,
        source_type="radicado",
        source_ref=hallazgo.tracking,
        texto=hallazgo.texto,
        metadata={"page": hallazgo.page, "attachment_ref": hallazgo.attachment_ref},
        score=hallazgo.score,
        origin="ppi",
    )


def _slug(request: Request) -> str:
    return request.headers.get("X-Tenant-Slug") or ""


def _cliente() -> Any:
    return paso.destino_de(settings.ppi_provider_url, transporte_de_consulta())


ANTECEDENTES_ADVISORY = (
    "Resultados del índice local (búsqueda de texto completo de los radicados): son "
    "pistas a confirmar, no la fuente autoritativa. Verifique cada hecho contra el "
    "radicado fuente (`source_ref`) antes de afirmarlo."
)
ANTECEDENTES_ADVISORY_PPI = (
    "Resultados del proveedor de inteligencia, ya rechequeados contra el registro. "
    "Verifique cada hecho contra el radicado fuente (`source_ref`) antes de afirmarlo."
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
    origin: Literal["local_index", "ppi"] = LOCAL_INDEX


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
async def antecedentes(
    payload: AntecedentesRequest,
    request: Request,
    conn: Any = Depends(get_tenant_conn),
) -> AntecedentesResponse:
    """Antecedentes por texto o por radicado, citados con su ``source_ref``."""
    excluir: str | None = None
    texto: str | None = payload.query
    if payload.radicado_ref is not None:
        if payload.source_type != "radicado":
            raise _antecedentes_source_not_found()
        candidatos = await _buscar_texto(request, q=payload.radicado_ref, size=5)
        pivote = next(
            (h for h in candidatos if h["tracking_number"] == payload.radicado_ref), None
        )
        if pivote is None:
            raise _antecedentes_source_not_found()
        texto = _consulta_desde_asunto(pivote["subject"])
        if not texto:
            return AntecedentesResponse(antecedentes=[])
        excluir = str(pivote["id"])

    assert texto is not None  # query, o el asunto del pivote
    actor = await get_actor(request)
    limite = payload.top_k + 1 if excluir is not None else payload.top_k
    delegada = await consulta.buscar(
        conn,
        _slug(request),
        _cliente(),
        texto=texto,
        limite=limite,
        actor=actor,
    )
    if delegada.delegada:
        hallazgos = [h for h in delegada.hallazgos if str(h.id) != excluir][: payload.top_k]
        return AntecedentesResponse(
            antecedentes=[
                Antecedente(
                    source_type="radicado",
                    source_ref=h.tracking,
                    snippet=h.texto,
                    score=h.score,
                )
                for h in hallazgos
            ],
            advisory=ANTECEDENTES_ADVISORY_PPI,
            origin="ppi",
        )

    if excluir is not None:
        # top_k + 1: el propio pivote suele ser el mejor resultado y se excluye.
        hits = await _buscar_texto(request, q=texto, size=payload.top_k + 1)
        hits = [h for h in hits if str(h["id"]) != excluir][: payload.top_k]
    else:
        hits = await _buscar_texto(request, q=texto, size=payload.top_k)

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
