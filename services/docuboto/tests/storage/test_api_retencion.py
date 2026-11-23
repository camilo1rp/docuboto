# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Las dos fuentes de la retención que acepta `storage.api` (ADR-043)."""

from datetime import date

import pytest

from storage import api as storage_api


def test_las_dos_fuentes_de_retencion_son_excluyentes() -> None:
    """`retencion_diferida` no se mezcla con años+fecha ni se omite todo: es un error de
    programación del llamante y falla antes de tocar el almacén. Mutación: quitar el
    ValueError -> rojo."""
    assert storage_api._retention_fields(None, None, "valoracion_pendiente") == {
        "retencion_diferida": "valoracion_pendiente"
    }
    assert storage_api._retention_fields(5, date(2026, 1, 1), None) == {
        "retencion_anios": 5, "fecha_inicio": date(2026, 1, 1)
    }
    with pytest.raises(ValueError):
        storage_api._retention_fields(5, date(2026, 1, 1), "valoracion_pendiente")
    with pytest.raises(ValueError):
        storage_api._retention_fields(None, None, None)


@pytest.mark.asyncio
async def test_mezclar_las_fuentes_lanza_y_no_se_degrada_a_transitorio() -> None:
    """El `ValueError` sube a quien llama: no se confunde con un fallo del almacén."""
    with pytest.raises(ValueError):
        await storage_api.protect_index_as_system(
            tenant_slug="demo",
            file_id="00000000-0000-0000-0000-000000000001",
            expediente_id="EXP-1",
            retencion_anios=5,
            fecha_inicio=date(2026, 1, 1),
            retencion_diferida="valoracion_pendiente",
        )


def test_el_hold_de_sistema_no_lleva_persona() -> None:
    """`valoracion_pendiente` lo fija el SISTEMA: sin persona no hay `fijado_por`, y con
    ella el acto es suyo. Mutación: atribuir a un humano el hold de sistema -> rojo."""
    from storage.core.constants import SYSTEM_RECONCILER_ID

    assert storage_api._hold_actor(None) == (str(SYSTEM_RECONCILER_ID), None)
    persona = "11111111-1111-1111-1111-111111111111"
    actor, actor_id = storage_api._hold_actor(persona)
    assert actor == persona and str(actor_id) == persona


@pytest.mark.asyncio
async def test_renovar_lleva_el_file_id_solo_cuando_se_da(monkeypatch) -> None:
    """Tipos multi-artefacto (FIRMA-2): sin `file_id` se renovaría solo el más reciente."""
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from storage.services.preservation_worm_service import PreservationWormService

    @asynccontextmanager
    async def _conn():
        yield AsyncMock()

    servicio = AsyncMock(side_effect=RuntimeError("sin almacén"))
    monkeypatch.setattr(storage_api, "_tenant_conn", _conn)
    monkeypatch.setattr(PreservationWormService, "renovar_retencion", servicio)
    for tipo, extra in (("acta_firma_excepcion_recibe", {"file_id": "11111111-1111-1111-1111-111111111111"}), ("indice", {})):
        outcome = await storage_api.renew_retention(
            tenant_slug="demo", expediente_id="e", tipo=tipo,
            retencion_anios=5, fecha_inicio=date(2026, 1, 1), **extra,
        )
        assert outcome.transient is True
    con, sin = (c.args[0] for c in servicio.await_args_list)
    assert str(con.file_id) == "11111111-1111-1111-1111-111111111111" and sin.file_id is None
