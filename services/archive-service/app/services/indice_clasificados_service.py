# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Índice público de información clasificada y reservada POR SERIE (C12, ADR-039).

archive-service es el dueño del cuadro de clasificación (CCD/TRD); document-service,
el de los radicados y de la ley (catálogo de causales). El índice se COMPONE aquí:

1. `GET /internal/documentos/clasificados` (document): radicados nivel >= 2 con causal,
   plazo y fecha, SIN número, asunto ni fundamento; más el catálogo de causales.
2. La serie/subserie de cada uno sale de `expediente_radicados -> expedientes -> trd_series`.
3. `componer_indice` agrupa por (serie, subserie, causal, categoría, cita normativa) y descarta
   los identificadores: lo que sale no permite saber CUÁNTOS ni CUÁLES radicados hay.

Un radicado en N expedientes aparece en las N series (semántica de conjunto). Sin
expediente con serie => grupo «Sin serie documental asignada».
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

import asyncpg
from orpycamcp_common.tenant import tenant_schema

from app.clients import document_client
from app.repositories.clasificados_serie_repository import series_de_radicados
from app.schemas.indice_clasificados import IndiceSeriePublico

SIN_SERIE = "Sin serie documental asignada"
POR_CLASIFICAR = "por_clasificar"
_PENDIENTE = "Pendiente de motivar"


def _dia(valor: Any) -> date | None:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).date()


def componer_indice(
    radicados: Iterable[Mapping[str, Any]],
    series: Iterable[Mapping[str, Any]],
    causales: Iterable[Mapping[str, Any]],
) -> list[IndiceSeriePublico]:
    """Agrupa y anonimiza. Función pura: no toca red ni base."""
    catalogo = {c["codigo"]: c for c in causales}
    por_radicado: dict[str, list[Mapping[str, Any]]] = {}
    for s in series:
        por_radicado.setdefault(str(s["radicado_id"]), []).append(s)

    grupos: dict[tuple, dict[str, Any]] = {}
    for r in radicados:
        nivel = int(r["nivel_seguridad"])
        causal = r.get("clasificacion_causal") or POR_CLASIFICAR
        propias = por_radicado.get(str(r["radicado_id"]))
        ubicaciones: list[Mapping[str, Any] | None] = [*propias] if propias else [None]
        for u in ubicaciones:
            clave = (
                u["serie_codigo"] if u else None,
                u["subserie_codigo"] if u else None,
                causal,
                nivel,
                r.get("clasificacion_norma") or None,
            )
            g = grupos.get(clave)
            if g is None:
                g = grupos[clave] = {
                    "serie_nombre": u["serie_nombre"] if u else SIN_SERIE,
                    "subserie_nombre": u["subserie_nombre"] if u else None,
                    "dependencia": u["dependencia_responsable"] if u else None,
                    "desde": None,
                    "hasta": None,
                    "gen_desde": None,
                    "gen_hasta": None,
                    "plazo": None,
                }
            gen = _dia(r.get("fecha_generacion"))
            if gen is not None:
                g["gen_desde"] = gen if g["gen_desde"] is None else min(g["gen_desde"], gen)
                g["gen_hasta"] = gen if g["gen_hasta"] is None else max(g["gen_hasta"], gen)
            d = _dia(r.get("clasificado_at"))
            if d is not None:
                g["desde"] = d if g["desde"] is None else min(g["desde"], d)
                g["hasta"] = d if g["hasta"] is None else max(g["hasta"], d)
            p = r.get("clasificacion_plazo_meses")
            if p is not None:
                g["plazo"] = p if g["plazo"] is None else max(g["plazo"], p)

    filas: list[IndiceSeriePublico] = []
    for (serie_cod, sub_cod, causal, nivel, norma), g in grupos.items():
        c = catalogo.get(causal)
        filas.append(
            IndiceSeriePublico(
                serie_codigo=serie_cod,
                serie_nombre=g["serie_nombre"],
                subserie_codigo=sub_cod,
                subserie_nombre=g["subserie_nombre"],
                categoria="clasificada" if nivel >= 3 else "reservada",
                causal_codigo=causal,
                articulo=c["articulo"] if c else None,
                literal=c["literal"] if c else None,
                causal_descripcion=c["descripcion"] if c else _PENDIENTE,
                excepcion="total",
                fecha_generacion_desde=g["gen_desde"],
                fecha_generacion_hasta=g["gen_hasta"],
                fecha_calificacion_desde=g["desde"],
                fecha_calificacion_hasta=g["hasta"],
                fundamento_norma=norma,
                # Art. 18: sin plazo (mientras subsista el derecho). El `plazo` del grupo
                # solo se publica si la causal admite uno (o está por clasificar).
                plazo_maximo_meses=(
                    None if (c and c.get("plazo_max_meses") is None) else g["plazo"]
                ),
                responsable_informacion=g["dependencia"],
            )
        )
    filas.sort(
        key=lambda f: (
            f.serie_codigo is None,
            f.serie_codigo or "",
            f.subserie_codigo or "",
            f.categoria,
            f.causal_codigo,
            f.fundamento_norma or "",
        )
    )
    return filas


async def indice_publico(conn: asyncpg.Connection, tenant_slug: str) -> list[IndiceSeriePublico]:
    """Compone el índice de una entidad (ya verificada como operativa por el router).

    document-service caído => `DocumentClientError` (el router responde 503): una lista
    parcial de reservas publicada como completa sería peor que no publicarla."""
    datos = await document_client.fetch_clasificados_internal(tenant_slug=tenant_slug)
    radicados = datos.get("radicados", [])
    schema = tenant_schema(tenant_slug)  # valida + normaliza (fail-closed)
    await conn.execute(f'SET search_path TO "{schema}"')
    series = await series_de_radicados(
        conn, [_uuid(r["radicado_id"]) for r in radicados]
    )
    return componer_indice(radicados, [dict(s) for s in series], datos.get("causales", []))


def _uuid(v: Any) -> uuid.UUID:
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
