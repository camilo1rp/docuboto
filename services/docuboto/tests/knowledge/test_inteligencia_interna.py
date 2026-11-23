# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El acto de configuración que tenant pide a la pasarela (`knowledge.api`).

Era un router interno con token; ahora es una llamada a función (ADR-047 §1).
Lo que queda es validar el pedido antes de tocar la BD o el proveedor.
"""

from __future__ import annotations

import uuid

import pytest

from knowledge import api as knowledge_api


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "levels"),
    [
        ("host_request", [2, 2]),  # un nivel no se repite
        ("host_request", None),  # deshabilitar un nivel indica cuál
        ("withdrawn", [1]),  # el retiro de la entidad no lleva niveles
        ("host_request", [4]),  # nivel fuera del vocabulario
        ("otra", None),
    ],
)
async def test_la_purga_valida_el_pedido_antes_de_tocar_nada(reason, levels) -> None:
    with pytest.raises(knowledge_api.IntelligenceError) as exc:
        await knowledge_api.purge(
            tenant_slug="demo", actor=uuid.uuid4(), reason=reason, levels=levels
        )
    assert exc.value.status == 422


@pytest.mark.asyncio
async def test_la_aceptacion_exige_una_huella_bien_formada() -> None:
    with pytest.raises(knowledge_api.IntelligenceError) as exc:
        await knowledge_api.accept(tenant_slug="demo", level=1, sha256="no-es-una-huella")
    assert exc.value.status == 422
