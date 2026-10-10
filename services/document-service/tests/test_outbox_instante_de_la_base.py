# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El instante del evento del outbox lo da el reloj de la BASE, no el del proceso.

Con varias réplicas, `datetime.now()` de Python desfasa los instantes y la segunda red de
knowledge (`nivel_evento_at`) puede descartar la reclasificación buena. Mutación: volver a
`datetime.now(UTC)` en `outbox.encolar` hace caer el primer test.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.services import outbox

_RELOJ_REAL = outbox._reloj_bd  # el conftest sustituye el reloj en las suites con mocks


@pytest.mark.asyncio
async def test_el_instante_del_evento_sale_del_reloj_de_la_base(monkeypatch) -> None:
    monkeypatch.setattr(outbox, "_reloj_bd", _RELOJ_REAL)
    de_la_bd = datetime(2031, 5, 17, 8, 30, tzinfo=UTC)  # lejos de cualquier reloj local
    conn = AsyncMock()
    conn.fetchval.return_value = de_la_bd

    pendiente = await outbox.encolar(
        conn,
        stream="orpycamcp.document.events",
        event_type="document.radicado.reclassified",
        tenant_slug="demo",
        actor="u",
        dedup_key="k",
        payload={"tracking_number": "2026-DEMO-E-000001"},
    )

    assert pendiente["event"]["occurred_at"] == de_la_bd.isoformat()
    assert "clock_timestamp()" in conn.fetchval.call_args.args[0]
    insertado = conn.execute.call_args.args[-1]
    assert insertado == de_la_bd
