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

from app.jobs import indice_reconciliation as job

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
@pytest.mark.parametrize(("crudo", "esperado"), [(False, False), (True, True), (None, None), ("no", None)])
async def test_verify_result_propaga_lta_vigente(
    monkeypatch: pytest.MonkeyPatch, crudo: object, esperado: bool | None
) -> None:
    """Mutación: no leer `lta_vigente` en `signature_client.verify` -> cae (False llega como None)."""
    import httpx

    from app.clients import signature_client

    cuerpo = {"valida": True, "acreditado": False, "lta_vigente": crudo}

    class _Cliente:
        def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *a) -> None:  # noqa: ANN002
            return None

        async def post(self, url, json=None, headers=None):  # noqa: ANN001, ANN202
            return httpx.Response(200, json=cuerpo, request=httpx.Request("POST", url))

    monkeypatch.setattr(signature_client.httpx, "AsyncClient", _Cliente)
    r = await signature_client.verify(tenant_slug="demo", firma_id="f", payload_xml="<x/>")
    assert r is not None and r.lta_vigente is esperado


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
