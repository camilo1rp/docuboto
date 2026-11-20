# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 (ADR-035, enmienda): las fases de CONSERVACIÓN recorren también las entidades suspendidas.

Mutaciones: volver una fase a `list_active_tenants` (cae su caso parametrizado); abrir
`list_tenants_conservacion` a `inactive` (cae `test_la_lista_...`)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from archive.jobs import indice_reconciliation as job

_FASES_DE_CONSERVACION = [
    "run_once_worm",
    "run_once_actas_worm",
    "run_once_firmas_personales_worm",
    "run_once_actas_eliminacion_worm",
    "run_once_worm_renovacion",
    "run_once_fixity_premis",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("fase", _FASES_DE_CONSERVACION)
async def test_la_fase_de_conservacion_usa_la_lista_con_suspendidas(
    fase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(job, "get_pool", lambda: MagicMock())
    conserva = AsyncMock(return_value=[])
    monkeypatch.setattr(job, "list_tenants_conservacion", conserva)
    monkeypatch.setattr(
        job, "list_active_tenants", AsyncMock(side_effect=AssertionError("solo las activas"))
    )
    await getattr(job, fase)()
    conserva.assert_awaited_once()


@pytest.mark.asyncio
async def test_la_lista_incluye_suspendidas_y_excluye_baja_y_sin_aprovisionar() -> None:
    sql: list[str] = []

    class _Conn:
        async def fetch(self, q: str):
            sql.append(q)
            return [{"slug": "a"}]

    class _Ctx:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *a):
            return None

    class _Pool:
        def acquire(self):
            return _Ctx()

    assert await job.list_tenants_conservacion(_Pool()) == ["a"]
    assert "'suspended'" in sql[0] and "inactive" not in sql[0]
    assert "provisioning_status = 'active'" in sql[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("lta_vigente", [False, True, None])
async def test_verify_result_propaga_lta_vigente(
    monkeypatch: pytest.MonkeyPatch, lta_vigente: bool | None
) -> None:
    """Mutación: no copiar `lta_vigente` en `signature_api.verify` -> cae (False llega como None)."""
    import uuid
    from contextlib import asynccontextmanager

    from signature import api as signature_api
    from signature.schemas.firma import VerifyResponse

    fid = uuid.uuid4()

    @asynccontextmanager
    async def _conn(*_a, **_k):
        yield MagicMock()

    repo = MagicMock()
    repo.return_value.get = AsyncMock(return_value={"id": fid})
    veredicto = VerifyResponse(
        firma_id=fid, valida=True, provider="xades_local", lta_vigente=lta_vigente,
        advertencias=["tsa_sin_esscertid_anterior_al_corte"],
    )
    monkeypatch.setattr(signature_api, "_tenant_conn", _conn)
    monkeypatch.setattr(signature_api, "FirmaRepository", repo)
    monkeypatch.setattr(
        signature_api.verification, "verificar_firma", AsyncMock(return_value=veredicto)
    )
    r = await signature_api.verify(tenant_slug="demo", firma_id=str(fid), payload_xml="<x/>")
    assert r is not None and r.lta_vigente is lta_vigente
    assert r.advertencias == ["tsa_sin_esscertid_anterior_al_corte"]
    # Sin usuario detrás no hay clearance que aplicar: quien llama ya resolvió el acceso.
    repo.return_value.get.assert_awaited_once_with(fid, max_clearance=None)


@pytest.mark.asyncio
async def test_el_empaquetado_del_aip_solo_recorre_las_activas(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2-2: document-service veta `fetch_anexos_internal` a la suspendida; visitarla agotaría
    reintentos. Mutación: `run_once_aip` con `list_tenants_conservacion` -> cae."""
    monkeypatch.setattr(job, "get_pool", lambda: MagicMock())
    activas = AsyncMock(return_value=[])
    monkeypatch.setattr(job, "list_active_tenants", activas)
    monkeypatch.setattr(
        job, "list_tenants_conservacion", AsyncMock(side_effect=AssertionError("no incluye suspendidas"))
    )
    await job.run_once_aip()
    activas.assert_awaited_once()
