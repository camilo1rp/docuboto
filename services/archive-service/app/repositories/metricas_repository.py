# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Conteo de artefactos WORM con el reintento AGOTADO, para `/metrics` (C18, ADR-041).

Cada artefacto protegido con Object-Lock tiene un ciclo de reintento que, al agotarse,
deja de reintentarse y queda a la espera de un humano. Ese estado vive solo aquí
(storage-service no persiste estado de reintento), por eso la señal `worm` la
expone archive-service.

`COMBINACIONES` es la lista de admisión: (artefacto, tabla, fase, predicado). Los
`reconcile_agotado` del SELLADO XAdES y `fixity_premis_agotado` no son WORM y quedan
fuera a propósito (ampliación natural: `phase=sealing`). Un guardián
(`shared/tests/test_metricas_worm_censo.py`) recalcula esta lista desde las
migraciones de tenant: añadir una tabla WORM sin su métrica hace que caiga.

Todo va CALIFICADO por schema (sin `search_path`) y agregado en SQL, en una sola
consulta `UNION ALL`. Es un conteo sin contenido: no aplica clearance.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import asyncpg
from orpycamcp_common.metricas import Muestra

_RECONCILE = "worm_reconcile_agotado"
_RENOVACION = "worm_renovacion_agotado"

#: (artifact, tabla, phase, predicado SQL). Orden estable.
COMBINACIONES: tuple[tuple[str, str, str, str], ...] = (
    ("expediente_indice", "expediente_indice", "reconcile", f"{_RECONCILE} IS TRUE"),
    ("expediente_indice", "expediente_indice", "renewal", f"{_RENOVACION} IS TRUE"),
    ("expediente_aip", "expediente_aip", "renewal", f"{_RENOVACION} IS TRUE"),
    ("expediente_aip", "expediente_aip", "packaging", "estado = 'agotado'"),
    ("acta_transferencia", "transferencia_acta_firma", "reconcile", f"{_RECONCILE} IS TRUE"),
    ("acta_transferencia", "transferencia_acta_firma", "renewal", f"{_RENOVACION} IS TRUE"),
    ("acta_firma_personal", "transferencia_acta_firma_personal_worm", "reconcile", f"{_RECONCILE} IS TRUE"),
    ("acta_firma_personal", "transferencia_acta_firma_personal_worm", "renewal", f"{_RENOVACION} IS TRUE"),
    ("acta_eliminacion", "eliminacion_acta_firma", "reconcile", f"{_RECONCILE} IS TRUE"),
    ("excepcion_firma", "transferencia_acta_firma_excepcion_worm", "reconcile", f"{_RECONCILE} IS TRUE"),
    ("excepcion_firma", "transferencia_acta_firma_excepcion_worm", "renewal", f"{_RENOVACION} IS TRUE"),
    (
        "revocacion_excepcion",
        "transferencia_acta_firma_excepcion_revocacion_worm",
        "reconcile",
        f"{_RECONCILE} IS TRUE",
    ),
    (
        "revocacion_excepcion",
        "transferencia_acta_firma_excepcion_revocacion_worm",
        "renewal",
        f"{_RENOVACION} IS TRUE",
    ),
)

_SCHEMA_RE = re.compile(r"tenant_[a-z0-9_]+")


def sql_agotados(schemas: Sequence[str]) -> str:
    """UNION ALL (schema × combinación) agregado por (artifact, phase). Sin schemas ⇒ vacío."""
    ramas: list[str] = []
    for schema in schemas:
        if not _SCHEMA_RE.fullmatch(schema):
            raise ValueError(f"schema de tenant inválido: {schema!r}")
        for artifact, tabla, fase, predicado in COMBINACIONES:
            ramas.append(
                f"SELECT '{artifact}' AS artifact, '{fase}' AS phase, count(*) AS n "
                f'FROM "{schema}".{tabla} WHERE {predicado}'
            )
    union = " UNION ALL ".join(ramas)
    return (
        "SELECT artifact, phase, sum(n)::bigint AS n "
        f"FROM ({union}) u GROUP BY artifact, phase"
    )


async def contar_agotados(conn: asyncpg.Connection, schemas: Sequence[str]) -> list[Muestra]:
    """Una muestra por combinación admitida, también con 0 (aquí el 0 es verdad).

    Si una tabla falta en algún schema la excepción sube: el llamador declara la señal
    «no verificada» y no publica ninguna suma parcial.
    """
    conteos: dict[tuple[str, str], int] = {(a, f): 0 for a, _t, f, _p in COMBINACIONES}
    if schemas:
        for fila in await conn.fetch(sql_agotados(schemas)):
            conteos[(fila["artifact"], fila["phase"])] = int(fila["n"])
    return [
        Muestra("orpycamcp_worm_exhausted_artifacts", conteos[(a, f)], {"artifact": a, "phase": f})
        for a, _t, f, _p in COMBINACIONES
    ]
