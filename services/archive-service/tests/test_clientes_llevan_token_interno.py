# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Contrato saliente de archive hacia el sello y el WORM (AREA-04-07, AREA-08-16).

signature-service exige `X-Internal-Token` en `/sign-xml` para el sello
institucional y storage-service en `/proteger-indice`, `/proteger-artefacto` y
`/aip`. Si archive dejara de enviarlo, el sellado del índice al cerrar caería a
`pendiente_firma` (best-effort) y la reconciliación lo repetiría sin fin: un
fallo permanente disfrazado de transitorio. Este test mira la PETICIÓN
SALIENTE, no el método aislado.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from app.clients import signature_client, storage_client
from app.core.config import settings

_SIG = "tok-firma"
_STO = "tok-storage"


@pytest.fixture
def capturadas(monkeypatch):
    peticiones: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        peticiones.append(request)
        return httpx.Response(500, json={})

    real = httpx.AsyncClient

    class _Cliente(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(_handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _Cliente)
    monkeypatch.setattr(settings, "signature_internal_token", _SIG)
    monkeypatch.setattr(settings, "storage_internal_token", _STO)
    return peticiones


@pytest.mark.asyncio
async def test_sign_xml_lleva_token_interno_e_identidad(capturadas) -> None:
    await signature_client.sign_xml(
        tenant_slug="demo", actor_id="u", payload_xml="<x/>",
        objeto_id="e", indice_id="i", version=1,
    )
    req = capturadas[-1]
    assert req.url.path.endswith("/sign-xml")
    assert req.headers["X-Internal-Token"] == _SIG
    assert "X-User-Id" in req.headers


@pytest.mark.asyncio
async def test_sign_xml_outcome_lleva_token_interno(capturadas) -> None:
    await signature_client.sign_xml_outcome(
        tenant_slug="demo", actor_id="u", payload_xml="<x/>",
        objeto_id="e", indice_id=None, version=1, objeto_tipo="acta_transferencia",
    )
    req = capturadas[-1]
    assert req.headers["X-Internal-Token"] == _SIG


@pytest.mark.asyncio
async def test_proteger_indice_worm_lleva_token_y_no_declara_legal_hold(capturadas) -> None:
    await storage_client.proteger_indice_worm(
        tenant_slug="demo", actor_id="u", file_id="f", expediente_id="e",
        retencion_anios=5, fecha_inicio=date(2026, 1, 1),
    )
    req = capturadas[-1]
    assert req.url.path.endswith("/proteger-indice")
    assert req.headers["X-Internal-Token"] == _STO
    assert "legal_hold" not in json.loads(req.content)


@pytest.mark.asyncio
async def test_proteger_artefacto_worm_lleva_token(capturadas) -> None:
    await storage_client.proteger_artefacto_worm(
        tenant_slug="demo", actor_id="u", file_id="f", expediente_id="e",
        tipo="acta_transferencia", retencion_anios=5, fecha_inicio=date(2026, 1, 1),
    )
    req = capturadas[-1]
    assert req.url.path.endswith("/proteger-artefacto")
    assert req.headers["X-Internal-Token"] == _STO
    assert "legal_hold" not in json.loads(req.content)


@pytest.mark.asyncio
async def test_gemelas_internas_llevan_token_y_ninguna_declara_legal_hold(capturadas) -> None:
    """Los 3 caminos de SISTEMA (reconciliacion): token en la peticion y sin
    `legal_hold` (storage lo rechaza con 422: un cliente que lo enviara
    quedaria en 422 permanente disfrazado de transitorio)."""
    await storage_client.proteger_indice_worm_internal(
        tenant_slug="demo", file_id="f", expediente_id="e",
        retencion_anios=5, fecha_inicio=date(2026, 1, 1),
    )
    await storage_client.proteger_artefacto_worm_internal(
        tenant_slug="demo", file_id="f", expediente_id="e", tipo="acta_transferencia",
        retencion_anios=5, fecha_inicio=date(2026, 1, 1),
    )
    await storage_client.empaquetar_aip_internal(
        tenant_slug="demo", expediente_id="e",
        documentos=[{"document_id": "R", "nombre": "a.pdf", "file_id": "f"}],
        indice_file_id=None, retencion_anios=5, fecha_inicio=date(2026, 1, 1),
    )
    assert len(capturadas) == 3
    for req in capturadas:
        assert req.url.path.endswith("-internal"), req.url.path
        assert req.headers["X-Internal-Token"] == _STO
        assert "legal_hold" not in json.loads(req.content)


@pytest.mark.asyncio
async def test_sign_indice_internal_lleva_token(capturadas) -> None:
    await signature_client.sign_indice_internal(
        tenant_slug="demo", payload_xml="<x/>", objeto_id="e", indice_id=None, version=1,
    )
    assert capturadas[-1].headers["X-Internal-Token"] == _SIG


@pytest.mark.asyncio
async def test_fijar_legal_hold_lleva_token_identidad_y_va_a_la_ruta_interna(capturadas) -> None:
    with pytest.raises(storage_client.StorageLegalHoldError):
        await storage_client.fijar_legal_hold(
            tenant_slug="demo",
            actor_id="u",
            expediente_id="e",
            motivo_tipo="judicial",
            fundamento="Orden del juzgado 12",
            acta_ref="A-1",
        )
    (req,) = capturadas
    assert req.url.path == "/api/v1/preservacion/legal-hold-internal/fijar"
    assert req.headers["x-internal-token"] == _STO
    assert "x-user-id" in req.headers and "x-tenant-slug" in req.headers
    cuerpo = json.loads(req.content)
    assert cuerpo["acta_ref"] == "A-1" and cuerpo["motivo_tipo"] == "judicial"
    assert "activo" not in cuerpo  # el booleano por objeto ya no existe (ADR-043)


@pytest.mark.asyncio
async def test_hold_de_sistema_va_sin_identidad_humana_pero_con_token(capturadas) -> None:
    """`valoracion_pendiente` es un acto de SISTEMA: sin `X-User-Id` (storage lo atribuye a
    SISTEMA) pero con el token interno. Mutación: mandar un actor inventado -> rojo."""
    with pytest.raises(storage_client.StorageLegalHoldError):
        await storage_client.fijar_legal_hold(
            tenant_slug="demo",
            actor_id=None,
            expediente_id="e",
            motivo_tipo="valoracion_pendiente",
            fundamento="Fondo vencido hasta la valoracion",
            acta_ref="TVD X v1",
        )
    (req,) = capturadas
    assert req.headers["x-internal-token"] == _STO
    assert "x-user-id" not in req.headers


@pytest.mark.asyncio
async def test_levantar_legal_hold_va_por_hold_id_y_confirma_el_motivo(capturadas) -> None:
    hold = "5d0f6a52-0d0b-4f3e-9e2c-2b1f9b0a1111"
    with pytest.raises(storage_client.StorageLegalHoldError):
        await storage_client.levantar_legal_hold(
            tenant_slug="demo",
            actor_id="u",
            hold_id=hold,
            motivo_tipo_esperado="judicial",
            fundamento="Fallo ejecutoriado",
            acta_ref="A-2",
        )
    (req,) = capturadas
    assert req.url.path == f"/api/v1/preservacion/legal-hold-internal/{hold}/levantar"
    assert json.loads(req.content)["motivo_tipo_esperado"] == "judicial"
    assert req.headers["x-internal-token"] == _STO


@pytest.mark.asyncio
async def test_fijar_retencion_internal_lleva_token_y_fecha(capturadas) -> None:
    from datetime import date

    with pytest.raises(storage_client.StorageLegalHoldError):
        await storage_client.fijar_retencion_internal(
            tenant_slug="demo", expediente_id="e", retencion_anios=10, fecha_inicio=date(2026, 10, 21)
        )
    (req,) = capturadas
    assert req.url.path == "/api/v1/preservacion/fijar-retencion-internal"
    assert req.headers["x-internal-token"] == _STO
    assert json.loads(req.content) == {
        "expediente_id": "e", "retencion_anios": 10, "fecha_inicio": "2026-10-21"
    }


def test_las_dos_fuentes_de_retencion_son_excluyentes() -> None:
    """`retencion_diferida` no se mezcla con años+fecha ni se omite todo (error de
    programación, falla antes de la red). Mutación: quitar el ValueError -> rojo."""
    from datetime import date

    assert storage_client._campos_retencion(None, None, "valoracion_pendiente") == {
        "retencion_diferida": "valoracion_pendiente"
    }
    assert storage_client._campos_retencion(5, date(2026, 1, 1), None) == {
        "retencion_anios": 5, "fecha_inicio": "2026-01-01"
    }
    with pytest.raises(ValueError):
        storage_client._campos_retencion(5, date(2026, 1, 1), "valoracion_pendiente")
    with pytest.raises(ValueError):
        storage_client._campos_retencion(None, None, None)


# ── Cableado de la tanda T12 (AREA-07-05 / 07-15 / 07-03): peticiones salientes ──


@pytest.mark.asyncio
async def test_registrar_evento_internal_lleva_token_ruta_y_cuerpo(capturadas) -> None:
    ok = await storage_client.registrar_evento_internal(
        tenant_slug="demo", object_ref="R-1", tipo="fixity check",
        resultado="ok", detalle={"k": 1}, agente="sistema", dedup_key="dk-1",
    )
    assert ok is False  # el doble responde 500: best-effort, nunca lanza
    req = capturadas[-1]
    assert req.method == "POST"
    assert req.url.path.endswith("/preservacion/eventos-internal")
    assert req.headers["X-Internal-Token"] == _STO
    assert req.headers["X-Tenant-Slug"] == "demo"
    cuerpo = json.loads(req.content)
    assert cuerpo["dedup_key"] == "dk-1"
    assert cuerpo["tipo"] == "fixity check"


@pytest.mark.asyncio
async def test_registrar_evento_internal_200_y_201_son_exito_y_la_red_no_lanza(monkeypatch) -> None:
    real = httpx.AsyncClient
    estado = {"codigo": 201}

    def _handler(request: httpx.Request) -> httpx.Response:
        if estado["codigo"] == 0:
            raise httpx.ConnectError("caido", request=request)
        return httpx.Response(estado["codigo"], json={})

    class _Cliente(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(_handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _Cliente)
    kw = dict(tenant_slug="demo", object_ref="R", tipo="t", resultado="ok",
              detalle={}, agente=None, dedup_key="d")
    for codigo, esperado in ((201, True), (200, True), (422, False), (0, False)):
        estado["codigo"] = codigo
        assert await storage_client.registrar_evento_internal(**kw) is esperado, codigo


@pytest.mark.asyncio
async def test_list_eventos_lleva_token_y_todos_los_object_ref(capturadas) -> None:
    with pytest.raises(storage_client.StorageEventosError):
        await storage_client.list_eventos(
            tenant_slug="demo", object_refs=["exp", "aip", "f1"]
        )
    req = capturadas[-1]
    assert req.headers["X-Internal-Token"] == _STO
    assert req.url.params.get_list("object_ref") == ["exp", "aip", "f1"]


@pytest.mark.asyncio
async def test_list_eventos_pide_por_tandas_funde_sin_duplicados_y_ordena(monkeypatch) -> None:
    peticiones: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        peticiones.append(request)
        n = len(peticiones)
        # el evento "dup" aparece en las dos tandas; "ts" desordenado a propósito
        return httpx.Response(200, json=[
            {"id": "dup", "ts": "2026-01-02"},
            {"id": f"e{n}", "ts": f"2026-01-0{5 - n}"},
        ])

    real = httpx.AsyncClient

    class _Cliente(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(_handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _Cliente)
    monkeypatch.setattr(settings, "storage_internal_token", _STO)
    refs = [f"r{i}" for i in range(250)]
    eventos = await storage_client.list_eventos(tenant_slug="demo", object_refs=refs)
    assert [len(p.url.params.get_list("object_ref")) for p in peticiones] == [100, 100, 50]
    assert all(p.headers["X-Internal-Token"] == _STO for p in peticiones)
    assert len(eventos) == len({e["id"] for e in eventos}) == 4
    assert [e["ts"] for e in eventos] == sorted(e["ts"] for e in eventos)


@pytest.mark.asyncio
async def test_enviar_aviso_lleva_token_y_no_declara_el_cuerpo_como_sensible(monkeypatch) -> None:
    from app.clients import notification_client

    peticiones: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        peticiones.append(request)
        return httpx.Response(202, json={})

    real = httpx.AsyncClient

    class _Cliente(real):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(_handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _Cliente)
    monkeypatch.setattr(settings, "notification_internal_token", "tok-notif")
    await notification_client.enviar_aviso(
        recipient_email="a@b.co", subject="s", body="b", tenant_slug="demo"
    )
    req = peticiones[-1]
    assert req.url.path.endswith("/notifications/send")
    assert req.headers["X-Internal-Token"] == "tok-notif"
    assert json.loads(req.content)["tenant_slug"] == "demo"

    # sin token configurado no sale ninguna petición
    peticiones.clear()
    monkeypatch.setattr(settings, "notification_internal_token", "")
    with pytest.raises(notification_client.NotificationDeliveryError):
        await notification_client.enviar_aviso(
            recipient_email="a@b.co", subject="s", body="b", tenant_slug="demo"
        )
    assert peticiones == []
