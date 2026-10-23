# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C7 (ADR-043): el cableado WORM del índice y del AIP ante `RetencionDiferida`.

Lo que se comprueba es la PETICIÓN SALIENTE a storage (no el resolutor aislado):
  * fondo vencido -> `retencion_diferida="valoracion_pendiente"` y NINGÚN `retencion_anios` /
    `fecha_inicio` (mutación: pasar también los años -> storage rechazaría por exclusión);
  * calendario normal -> años + fecha y `retencion_diferida=None` (control positivo);
  * hold no fijado -> backoff con motivo propio y NO se llama a storage para proteger
    (mutación: tragarse `ValoracionHoldError` y proteger igual -> contenido sin hold).
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.clients.storage_client import AipPackagingOutcome, WormProtectionOutcome
from app.services.index_service import IndexService
from app.services.retention_math import RetencionDiferida
from app.services.valoracion_service import ValoracionHoldError
from tests.conftest import USER_ID
from tests.test_aip_wiring_service import _mock_conn as _aip_conn
from tests.test_aip_wiring_service_pdfa_profile import (
    _expediente,
    _radicado,
    _service,
    _trd_serie,
    _wire_ready_to_call_storage,
)
from tests.test_index_service_worm import _indice_row, _mock_conn

TENANT = "demo"


# --- índice --------------------------------------------------------------------------------


async def _proteger_indice(resolver_value=None, *, side_effect=None):
    conn = _mock_conn()
    index = IndexService(conn, TENANT)
    row = _indice_row()
    conn.fetchrow.return_value = row
    conn.execute.return_value = "UPDATE 1"
    index._resolve_worm_retention = AsyncMock(return_value=resolver_value, side_effect=side_effect)
    index._index.bump_worm_reconcile_intento = AsyncMock()
    with (
        patch(
            "app.clients.storage_client.proteger_indice_worm",
            new=AsyncMock(
                return_value=WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4())
            ),
        ) as publico,
        patch(
            "app.clients.storage_client.proteger_indice_worm_internal",
            new=AsyncMock(
                return_value=WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4())
            ),
        ) as interno,
    ):
        res = await index.proteger_worm_indice(
            row["expediente_id"], row["id"], actor_id=str(USER_ID), origen="close"
        )
    return res, publico, interno, index, conn


@pytest.mark.asyncio
async def test_indice_de_fondo_vencido_protege_solo_con_el_hold() -> None:
    res, publico, _interno, _index, conn = await _proteger_indice(RetencionDiferida())
    assert res == "protegido"
    kw = publico.await_args.kwargs
    assert kw["retencion_diferida"] == "valoracion_pendiente"
    assert kw["retencion_anios"] is None and kw["fecha_inicio"] is None
    # y el asiento dice que esta protección no lleva calendario
    audit = [c for c in conn.execute.call_args_list if c.args and "INSERT INTO public.audit_log" in c.args[0]]
    assert any("valoracion_pendiente" in str(c.args) for c in audit)


@pytest.mark.asyncio
async def test_indice_con_calendario_normal_no_difiere_nada() -> None:
    res, publico, _i, _x, _c = await _proteger_indice((5, date(2026, 1, 1)))
    assert res == "protegido"
    kw = publico.await_args.kwargs
    assert kw["retencion_anios"] == 5 and kw["fecha_inicio"] == date(2026, 1, 1)
    assert kw["retencion_diferida"] is None


@pytest.mark.asyncio
async def test_indice_si_no_se_pudo_fijar_el_hold_pospone_y_no_protege() -> None:
    res, publico, interno, index, _c = await _proteger_indice(
        side_effect=ValoracionHoldError("storage caído")
    )
    assert res == "pendiente"
    publico.assert_not_awaited()
    interno.assert_not_awaited()
    index._index.bump_worm_reconcile_intento.assert_awaited_once()
    assert (
        index._index.bump_worm_reconcile_intento.await_args.kwargs["ultimo_error"]
        == "hold_valoracion_no_fijado"
    )


# --- AIP -------------------------------------------------------------------------------------


async def _empaquetar(retention):
    conn = _aip_conn()
    service = _service(conn)
    radicado = _radicado()
    serie_id = uuid.uuid4()
    exp = _expediente(trd_serie_id=serie_id, radicados=[radicado])
    _wire_ready_to_call_storage(service, exp)
    if isinstance(retention, Exception):
        service._resolve_aip_retention = AsyncMock(side_effect=retention)
    else:
        service._resolve_aip_retention = AsyncMock(return_value=retention)
    service._archive.get_trd_serie_by_id = AsyncMock(return_value=_trd_serie(serie_id=serie_id))
    service._aip.mark_empaquetado = AsyncMock(return_value=1)
    service._aip.bump_intento = AsyncMock()
    with (
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(
                return_value={
                    str(radicado.radicado_id): [{"file_id": str(uuid.uuid4()), "filename": "o.pdf"}]
                }
            ),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=True, aip_id=uuid.uuid4())),
        ) as storage,
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()),
    ):
        res = await service.intentar_empaquetar(exp.id)
    return res, storage, service


@pytest.mark.asyncio
async def test_aip_de_fondo_vencido_se_empaqueta_solo_con_el_hold() -> None:
    res, storage, service = await _empaquetar(RetencionDiferida())
    assert res == "empaquetado"
    kw = storage.await_args.kwargs
    assert kw["retencion_diferida"] == "valoracion_pendiente"
    assert kw["retencion_anios"] is None and kw["fecha_inicio"] is None
    # el espejo local queda SIN fecha (NULL): la renovación no lo selecciona hasta la valoración CT
    assert service._aip.mark_empaquetado.await_args.kwargs["worm_retain_until"] is None


@pytest.mark.asyncio
async def test_aip_con_calendario_normal_conserva_anios_y_fecha() -> None:
    res, storage, _s = await _empaquetar((5, date(2026, 1, 1)))
    assert res == "empaquetado"
    kw = storage.await_args.kwargs
    assert kw["retencion_anios"] == 5 and kw["retencion_diferida"] is None


@pytest.mark.asyncio
async def test_aip_si_no_se_pudo_fijar_el_hold_pospone_con_motivo_propio() -> None:
    res, storage, service = await _empaquetar(ValoracionHoldError("storage caído"))
    assert res == "pendiente"
    storage.assert_not_awaited()
    assert service._aip.bump_intento.await_args.kwargs["ultimo_error"] == "hold_valoracion_no_fijado"

