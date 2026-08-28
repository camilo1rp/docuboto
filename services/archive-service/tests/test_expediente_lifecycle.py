# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del ciclo de vida del expediente: cierre/transferencia/índice (E02)."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import (
    TENANT_HEADERS,
    USER_ID,
    make_expediente_record,
    make_radicado_link_record,
    make_trd_serie_record,
)


@pytest.mark.asyncio
async def test_close_applies_trd_disposition(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    serie_id = uuid.uuid4()
    exp_id = uuid.uuid4()
    exp_open = make_expediente_record(exp_id=exp_id, trd_serie_id=serie_id, exp_status="open")
    trd = make_trd_serie_record(
        disposition="conserve", archivo_gestion_years=3, archivo_central_years=7
    )
    exp_closed = make_expediente_record(
        exp_id=exp_id,
        trd_serie_id=serie_id,
        exp_status="closed",
        disposition={"disposition_code": "CT", "disposition_label": "Conservación Total"},
    )
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    # fetchrow: exp(open) → trd(pinned) → trd(vigente, ADR-025) →
    # trd(compute_retention sobre la vigente) → exp(closed update) →
    # exp(reload). El índice (regenerar+firmar) se aísla parcheando IndexService.
    mock_conn.fetchrow.side_effect = [exp_open, trd, trd, trd, exp_closed, exp_closed]
    mock_conn.fetch.return_value = []

    from app.services.index_service import IndexService

    with (
        patch.object(IndexService, "regenerate", new=AsyncMock()),
        patch.object(IndexService, "firmar_indice_close", new=AsyncMock(return_value=None)),
    ):
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "closed"
    assert body["disposition"]["disposition_code"] == "CT"


@pytest.mark.asyncio
async def test_close_forwards_x_user_id_as_actor_id(client_with_conn) -> None:
    """Regresión del bugfix (E06 Inc.1): el cierre reenvía X-User-Id como
    `actor_id` a `IndexService.firmar_indice_close` (antes se perdía → 401
    permanente en signature-service)."""
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    exp_open = make_expediente_record(exp_id=exp_id, exp_status="open")
    exp_closed = make_expediente_record(exp_id=exp_id, exp_status="closed")
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.side_effect = [exp_open, exp_closed, exp_closed]
    mock_conn.fetch.return_value = []

    from app.services.index_service import IndexService

    with (
        patch.object(IndexService, "regenerate", new=AsyncMock()),
        patch.object(IndexService, "firmar_indice_close", new=AsyncMock(return_value=None)) as mock_firmar,
    ):
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    mock_firmar.assert_awaited_once()
    assert mock_firmar.call_args.kwargs["actor_id"] == str(USER_ID)


@pytest.mark.asyncio
async def test_close_sin_firma_audita_indice_pendiente(client_with_conn, monkeypatch) -> None:
    """Si el sellado no se obtiene al cierre, queda traza: se emite
    'archive.indice_firma_pendiente' (ADR-010).

    Se mockea `signature_client.sign_xml_outcome` (la frontera HTTP), NO
    `firmar_indice_close`: ese asiento y el `mark_pendiente` que lo acompaña
    viven DENTRO de `firmar_indice_close`, comprometidos juntos (H5) —
    mockear el método entero borraría de la prueba justo el efecto que se
    quiere observar. `close_sign_max_sync_attempts` se acota a 1 (monkeypatch
    de settings) para que el control compensatorio agote su único intento
    síncrono sin `asyncio.sleep` real entre intentos — el test verifica el
    DESENLACE (degradación + auditoría), no el reintento acotado en sí
    (cubierto aparte por los tests dedicados de `firmar_indice_close`).

    El payload de 'archive.expediente_closed' NO puede declarar el estado del
    índice: se commitea ANTES de intentar el sellado, así que afirmar ahí
    'pendiente_firma' sería sobre-declarar un desenlace que todavía no ha
    ocurrido (RF-FIR-15). El hecho vive en su propio asiento posterior.
    """
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    exp_open = make_expediente_record(exp_id=exp_id, exp_status="open")
    exp_closed = make_expediente_record(exp_id=exp_id, exp_status="closed")
    indice_vigente = {
        "id": uuid.uuid4(),
        "estado": "vigente",
        "xml": "<indice/>",
        "version": 1,
    }
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.side_effect = [exp_open, exp_closed, indice_vigente]
    mock_conn.fetch.return_value = []

    from app.clients.signature_client import SignOutcome
    from app.core.config import settings as archive_settings
    from app.services.index_service import IndexService

    monkeypatch.setattr(archive_settings, "close_sign_max_sync_attempts", 1)

    with (
        patch.object(IndexService, "regenerate", new=AsyncMock()),
        patch(
            "app.clients.signature_client.sign_xml_outcome",
            new=AsyncMock(return_value=SignOutcome(signed=None, transient=True)),
        ),
    ):
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)
    assert resp.status_code == 200

    audit_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    # args: (sql, tenant, service, actor, canal, action, object_type, object_ref, payload, prev_hash, hash)
    actions = [a[5] for a in audit_calls]
    assert "archive.indice_firma_pendiente" in actions
    closed_payloads = [a[8] for a in audit_calls if a[5] == "archive.expediente_closed"]
    assert closed_payloads
    assert "pendiente_firma" not in closed_payloads[0]


@pytest.mark.asyncio
async def test_firmar_indice_marca_firmado(client_with_conn) -> None:
    """El índice vigente se firma vía signature-service y queda 'firmado'."""
    client, mock_conn = client_with_conn  # noqa: F841 (solo para overrides de app)
    from app.clients.signature_client import SignedIndice
    from app.services.index_service import IndexService

    exp_id = uuid.uuid4()
    firma_id = uuid.uuid4()
    actor_id = str(uuid.uuid4())
    svc = IndexService(mock_conn, "demo")
    # get_current → índice vigente; mark_signed → execute
    mock_conn.fetchrow.return_value = {"id": uuid.uuid4(), "estado": "vigente", "xml": "<indice/>", "version": 1}
    # S1: `mark_signed` debe reportar `affected=1` (ganó la carrera) para que
    # el asiento `archive.indice_firmado` se escriba — el default del mock
    # (`execute.return_value = None`) simularía una carrera perdida.
    mock_conn.execute.return_value = "UPDATE 1"

    signed = SignedIndice(
        firma_id=firma_id,
        minio_key_firmado="file-id/indice.xml",
        nivel_conformidad="xades_b_no_acreditado",
        xades_level="XAdES-B",
        tipo_firma="sello_sistema",
        acreditado=False,
        certificado_fingerprint="ab:cd",
    )

    from app.clients import signature_client

    with patch.object(signature_client, "sign_xml", new=AsyncMock(return_value=signed)):
        result = await svc.firmar_indice(exp_id, tenant_slug="demo", actor_id=actor_id)
    assert result is not None
    assert result.firma_id == firma_id
    # Se ejecutó el UPDATE de mark_signed (estado='firmado').
    assert any("estado='firmado'" in str(c.args[0]) for c in mock_conn.execute.call_args_list)


@pytest.mark.asyncio
async def test_firmar_indice_sin_actor_id_queda_pendiente(client_with_conn) -> None:
    """Sin actor_id (X-User-Id) no se intenta la llamada HTTP: 'pendiente_firma'."""
    client, mock_conn = client_with_conn  # noqa: F841
    from app.services.index_service import IndexService

    exp_id = uuid.uuid4()
    svc = IndexService(mock_conn, "demo")
    mock_conn.fetchrow.return_value = {"id": uuid.uuid4(), "estado": "vigente", "xml": "<indice/>", "version": 1}

    from app.clients import signature_client

    with patch.object(signature_client, "sign_xml", new=AsyncMock()) as mock_sign:
        result = await svc.firmar_indice(exp_id, tenant_slug="demo", actor_id=None)
    assert result is None
    mock_sign.assert_not_called()
    assert any("estado='pendiente_firma'" in str(c.args[0]) for c in mock_conn.execute.call_args_list)


@pytest.mark.asyncio
async def test_firmar_indice_signature_service_inalcanzable_no_bloquea(client_with_conn) -> None:
    """Si signature-service no responde (sign_xml → None), el índice queda
    'pendiente_firma' y el cierre no se bloquea (best-effort, RT-16)."""
    client, mock_conn = client_with_conn  # noqa: F841
    from app.services.index_service import IndexService

    exp_id = uuid.uuid4()
    actor_id = str(uuid.uuid4())
    svc = IndexService(mock_conn, "demo")
    mock_conn.fetchrow.return_value = {"id": uuid.uuid4(), "estado": "vigente", "xml": "<indice/>", "version": 1}

    from app.clients import signature_client

    with patch.object(signature_client, "sign_xml", new=AsyncMock(return_value=None)) as mock_sign:
        result = await svc.firmar_indice(exp_id, tenant_slug="demo", actor_id=actor_id)
    assert result is None
    mock_sign.assert_awaited_once()
    assert any("estado='pendiente_firma'" in str(c.args[0]) for c in mock_conn.execute.call_args_list)


@pytest.mark.asyncio
async def test_close_expediente_audit_uses_real_regenerate_result_not_redacted(
    client_with_conn,
) -> None:
    """CRÍTICO (M2, RF-SEG-08): la redacción de `RebuildResult` vive SOLO en
    el HANDLER `routers/indice.py::rebuild_indice` — `close_expediente`
    (`ArchiveService`) reutiliza `IndexService.regenerate` directamente
    (canal 'system') y su asiento `archive.expediente_closed` debe llevar el
    `xsd_version` REAL que `regenerate` devolvió, nunca `None` ni un valor
    redactado. Si alguien moviera la redacción de M2 dentro de
    `IndexService.regenerate` en vez de dejarla en el handler del rebuild,
    este test debe fallar (el payload dejaría de traer el marcador
    distintivo)."""
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    # Expediente CLASIFICADA (3): el escenario donde M2 podría, por error,
    # colarse en la ruta interna de cierre si la redacción no estuviera
    # aislada al handler del rebuild.
    exp_open = make_expediente_record(exp_id=exp_id, exp_status="open", nivel_seguridad=3)
    exp_closed = make_expediente_record(exp_id=exp_id, exp_status="closed", nivel_seguridad=3)
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE (crud>=3) granted
    mock_conn.fetchrow.side_effect = [exp_open, exp_closed, exp_closed]
    mock_conn.fetch.return_value = []

    from app.schemas.archive import RebuildResult
    from app.services.index_service import IndexService

    # Marcador distintivo e inequívocamente REAL — un `xsd_version` que la
    # redacción de M2 jamás produciría (esa solo deja pasar la constante del
    # perfil, nunca un valor inventado como este).
    fake_rebuild = RebuildResult(
        version=7,
        xml_sha256="a" * 64,
        num_documentos=3,
        unchanged=False,
        xsd_version="v4-real-marker",
    )
    with (
        patch.object(IndexService, "regenerate", new=AsyncMock(return_value=fake_rebuild)),
        patch.object(IndexService, "firmar_indice_close", new=AsyncMock(return_value=None)),
    ):
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)
    assert resp.status_code == 200

    audit_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    # args: (sql, tenant, service, actor, canal, action, object_type, object_ref, payload, prev_hash, hash)
    closed_payloads = [a[8] for a in audit_calls if a[5] == "archive.expediente_closed"]
    assert closed_payloads
    assert '"v4-real-marker"' in closed_payloads[0]


@pytest.mark.asyncio
async def test_close_expediente_with_borrador_serie_returns_409_trd_version_no_vigente(
    client_with_conn,
) -> None:
    """ADR-025 D5: cerrar un expediente cuya serie TRD pinneada NO tiene
    ninguna versión vigente (convalidada/migrada) -- p. ej. sigue en
    `borrador`, jamás convalidada -- se rechaza. El gate corta ANTES de abrir
    la transacción: no hay transición, ni evento, ni regeneración de índice."""
    client, mock_conn = client_with_conn
    serie_id = uuid.uuid4()
    exp_id = uuid.uuid4()
    exp_open = make_expediente_record(exp_id=exp_id, trd_serie_id=serie_id, exp_status="open")
    trd_borrador = make_trd_serie_record(serie_id=serie_id, code="100", estado="borrador")
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    # fetchrow: exp(open) -> trd(pinned, borrador) -> vigente_by_code (None:
    # no hay convalidada/migrada para el código).
    mock_conn.fetchrow.side_effect = [exp_open, trd_borrador, None]

    from app.services.index_service import IndexService

    with (
        patch.object(IndexService, "regenerate", new=AsyncMock()) as mock_regenerate,
        patch.object(IndexService, "firmar_indice_close", new=AsyncMock(return_value=None)),
    ):
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "trd_version_no_vigente"
    mock_regenerate.assert_not_awaited()
    # Ningún `UPDATE`/transición se emitió (el gate cortó antes de la tx).
    assert not any(
        c.args and isinstance(c.args[0], str) and "UPDATE expedientes" in c.args[0]
        for c in mock_conn.fetchrow.call_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["migrada", "convalidada"])
async def test_close_expediente_with_vigente_serie_succeeds(
    client_with_conn, estado: str
) -> None:
    """Contraparte: `migrada` (backfill, D6) y `convalidada` (circuito
    completo) SÍ gobiernan el cierre -- ambas son "vigentes" para
    `get_trd_serie_vigente_by_code`. El snapshot declara `serie_estado` tal
    cual (RF-FIR-15, nunca se afirma una convalidación que no ocurrió)."""
    client, mock_conn = client_with_conn
    serie_id = uuid.uuid4()
    exp_id = uuid.uuid4()
    exp_open = make_expediente_record(exp_id=exp_id, trd_serie_id=serie_id, exp_status="open")
    trd = make_trd_serie_record(
        serie_id=serie_id,
        code="100",
        estado=estado,
        disposition="conserve",
        archivo_gestion_years=3,
        archivo_central_years=7,
        acto_administrativo="Resolución 001" if estado == "convalidada" else None,
        fecha_convalidacion="2026-01-01" if estado == "convalidada" else None,
        instancia_convalidante="Consejo Departamental" if estado == "convalidada" else None,
    )
    exp_closed = make_expediente_record(
        exp_id=exp_id,
        trd_serie_id=serie_id,
        exp_status="closed",
        trd_serie_code="100",
        disposition={"disposition_code": "CT", "serie_estado": estado},
        trd_revision_requerida=(estado == "migrada"),
        trd_revision_motivo=("cerrado_bajo_serie_migrada" if estado == "migrada" else None),
    )
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    # fetchrow: exp(open) -> trd(pinned) -> trd(vigente_by_code) ->
    # trd(compute_retention sobre la vigente) -> exp(closed update) -> exp(reload).
    mock_conn.fetchrow.side_effect = [exp_open, trd, trd, trd, exp_closed, exp_closed]
    mock_conn.fetch.return_value = []

    from app.services.index_service import IndexService

    with (
        patch.object(IndexService, "regenerate", new=AsyncMock()),
        patch.object(IndexService, "firmar_indice_close", new=AsyncMock(return_value=None)),
    ):
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # `POST /close` responde `ExpedienteDisposicionResult` (M2, RF-SEG-08):
    # deliberadamente NO trae `trd_serie_code`/`trd_revision_requerida` (esos
    # campos SÍ están en `GET /expedientes/{id}` -- `ExpedienteResponse`,
    # cubierto en la fixture de `exp_closed` más arriba). Aquí solo se
    # confirma que el cierre tuvo éxito y materializó la disposición.
    assert body["status"] == "closed"
    assert body["disposition"]["disposition_code"] == "CT"


@pytest.mark.asyncio
async def test_close_invalid_state(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = make_expediente_record(exp_status="closed")
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{uuid.uuid4()}/close", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "invalid_status_transition"


@pytest.mark.asyncio
async def test_transfer_from_closed(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    closed = make_expediente_record(exp_id=exp_id, exp_status="closed")
    transferred = make_expediente_record(exp_id=exp_id, exp_status="transferred")
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    # _expediente_or_404 → _assert_indice_firmado(get_current) → transition_expediente → reload.
    mock_conn.fetchrow.side_effect = [
        closed,
        {"id": uuid.uuid4(), "estado": "firmado"},
        transferred,
        transferred,
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/transfer", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "transferred"


@pytest.mark.asyncio
async def test_transfer_sin_indice_firmado_409(client_with_conn) -> None:
    """E06 Inc.1 / Acuerdo AGN 001/2024 art. 4.3.2.4: un índice 'pendiente_firma'
    (sellado no obtenido al cierre) bloquea la transferencia, aunque no bloqueó
    el cierre (RT-16, best-effort)."""
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    closed = make_expediente_record(exp_id=exp_id, exp_status="closed")
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.side_effect = [
        closed,
        {"id": uuid.uuid4(), "estado": "pendiente_firma"},
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/transfer", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "indice_no_firmado"


@pytest.mark.asyncio
async def test_indice_foliation(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = make_expediente_record(exp_id=exp_id)
    mock_conn.fetch.return_value = [
        make_radicado_link_record(radicado_id=uuid.uuid4(), tracking_number="2024-A-001"),
        make_radicado_link_record(radicado_id=uuid.uuid4(), tracking_number="2024-A-002"),
    ]

    resp = await client.get(f"/api/v1/expedientes/{exp_id}/foliado", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_folios"] == 2
    assert body["items"][0]["folio"] == 1
    assert body["items"][1]["folio"] == 2


# ---------------------------------------------------------------------------
# RF-TRD-09 (2026-08-28): un cierre SIN serie TRD deja marca de revisión.
#
# Hasta esta fecha el expediente quedaba `closed` con `disposition = NULL` y
# `trd_revision_requerida = false`: sin calendario de retención y **sin
# aparecer en ninguna cola**. El motivo `cierre_sin_snapshot_de_retencion`
# existía desde la migración 037 —que lo usó para marcar los cierres
# históricos— y ningún camino de código lo escribió nunca después: el backfill
# reparó el pasado y nada protegía el futuro. Comprobado sobre `tenant_demo`:
# 1 expediente cerrado en ese estado, invisible para `/trd/revision-pendiente`.
# ---------------------------------------------------------------------------


def _sql_del_update_de_cierre(mock_conn) -> tuple[str, tuple]:
    """El `UPDATE ... SET status = $1` de `transition_expediente`, con sus
    parámetros. Se inspecciona el SQL EMITIDO, no el fuente: la lección de la
    auditoría cruzada es que un `getsource` encuentra la frase en el propio
    docstring que la niega."""
    for llamada in mock_conn.fetchrow.await_args_list:
        sql = llamada.args[0] if llamada.args else ""
        if isinstance(sql, str) and sql.startswith("UPDATE expedientes SET status ="):
            return sql, llamada.args[1:]
    raise AssertionError("no se emitió ningún UPDATE de transición")


@pytest.mark.asyncio
async def test_cerrar_sin_serie_trd_deja_marca_de_revision(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    exp_open = make_expediente_record(exp_id=exp_id, trd_serie_id=None, exp_status="open")
    exp_closed = make_expediente_record(exp_id=exp_id, trd_serie_id=None, exp_status="closed")
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [exp_open, exp_closed, exp_closed]
    mock_conn.fetch.return_value = []

    from app.services.index_service import IndexService

    with (
        patch.object(IndexService, "regenerate", new=AsyncMock()),
        patch.object(IndexService, "firmar_indice_close", new=AsyncMock(return_value=None)),
    ):
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    sql, params = _sql_del_update_de_cierre(mock_conn)
    assert "trd_revision_requerida" in sql, sql
    assert "trd_revision_motivo" in sql, sql
    assert True in params, params
    assert "cierre_sin_snapshot_de_retencion" in params, params
    # Y NO se toca el pin: no hay serie que fijar.
    assert "trd_serie_id" not in sql, sql


@pytest.mark.asyncio
async def test_cerrar_con_serie_convalidada_no_marca_nada(client_with_conn) -> None:
    """Control positivo: la marca es del caso sin calendario, no de todo
    cierre. Sin esto, el test de arriba pasaría con un `True` constante."""
    client, mock_conn = client_with_conn
    serie_id = uuid.uuid4()
    exp_id = uuid.uuid4()
    exp_open = make_expediente_record(exp_id=exp_id, trd_serie_id=serie_id, exp_status="open")
    trd = make_trd_serie_record(estado="convalidada", acto_administrativo="Res. 1 de 2026")
    exp_closed = make_expediente_record(
        exp_id=exp_id, trd_serie_id=serie_id, exp_status="closed", disposition={}
    )
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [exp_open, trd, trd, trd, exp_closed, exp_closed]
    mock_conn.fetch.return_value = []

    from app.services.index_service import IndexService

    with (
        patch.object(IndexService, "regenerate", new=AsyncMock()),
        patch.object(IndexService, "firmar_indice_close", new=AsyncMock(return_value=None)),
    ):
        resp = await client.post(f"/api/v1/expedientes/{exp_id}/close", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    sql, params = _sql_del_update_de_cierre(mock_conn)
    assert "cierre_sin_snapshot_de_retencion" not in params, params
    assert False in params, params


@pytest.mark.asyncio
async def test_transferir_no_borra_una_marca_pendiente(client_with_conn) -> None:
    """La transferencia NO escribe las marcas: con los defaults del método
    (`False`/`None`) borraría una marca que nadie ha resuelto — trasladar un
    expediente no responde la pregunta que la marca hace."""
    client, mock_conn = client_with_conn
    exp_id = uuid.uuid4()
    exp_closed = make_expediente_record(exp_id=exp_id, exp_status="closed")
    exp_transferred = make_expediente_record(exp_id=exp_id, exp_status="transferred")
    mock_conn.fetchval.return_value = True
    # _expediente_or_404 → _assert_indice_firmado(get_current) → transition → reload.
    mock_conn.fetchrow.side_effect = [
        exp_closed,
        {"id": uuid.uuid4(), "estado": "firmado"},
        exp_transferred,
        exp_transferred,
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/expedientes/{exp_id}/transfer", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    sql, _ = _sql_del_update_de_cierre(mock_conn)
    assert "trd_revision_requerida" not in sql, sql
    assert "trd_revision_motivo" not in sql, sql
