# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma PERSONAL PKI por-usuario del acta de transferencia (E17/F4,
Incremento 1): repositorio (migración 027), `TransferenciaService.
firmar_personal` (mediador de `POST /sign-personal`) y `acta_verificacion`
(`GET /acta/verificacion`, agregación sello + firmas personales).

Todo pure-unit (conexiones/pool mockeados) — la atomicidad/idempotencia REAL
se prueba aparte en `tests/integration/` (Postgres real).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.signature_client import SignedPersonal, SignPersonalOutcome, VerifyResult
from app.core.actor import Actor
from app.core.config import settings
from app.repositories.transferencia_repository import TransferenciaRepository
from app.services.transferencia_service import TransferenciaService
from tests.conftest import USER_ID, make_expediente_record
from tests.test_transferencias import _acta_firma_row, _acta_row, _transfer_row

TENANT = "demo"


def _mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _service(conn: AsyncMock) -> TransferenciaService:
    return TransferenciaService(conn, TENANT)


def _personal_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "transferencia_id": uuid.uuid4(),
        "rol": "recibe",
        "firmante_id": USER_ID,
        "firma_id": uuid.uuid4(),
        "minio_key_firmado": "file-id/acta-personal-firmado.xml",
        "xades_level": "XAdES-T",
        "ca_fingerprint": None,
        "acreditado": False,
        "creada_en": datetime.now(tz=UTC),
    }
    base.update(kw)
    return base


def _signed_personal(**kw) -> SignedPersonal:
    base = dict(
        firma_id=uuid.uuid4(),
        minio_key_firmado="file-id/acta-personal-firmado.xml",
        xades_level="XAdES-T",
        acreditado=False,
        certificado_fingerprint="ab:cd",
        nivel_conformidad="xades_personal_no_acreditado",
    )
    base.update(kw)
    return SignedPersonal(**base)


# ---------------------------------------------------------------------------
# TransferenciaRepository — nuevos métodos (migración 027)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_acta_firma_personal_queries_by_transferencia_and_rol() -> None:
    conn = _mock_conn()
    conn.fetchrow.return_value = _personal_row()
    repo = TransferenciaRepository(conn, TENANT)
    tid = uuid.uuid4()

    row = await repo.get_acta_firma_personal(tid, rol="recibe")

    assert row is not None
    sql, *params = conn.fetchrow.call_args[0]
    assert "transferencia_acta_firma_personal" in sql
    assert "rol = $2" in sql
    assert params == [tid, "recibe"]


@pytest.mark.asyncio
async def test_list_acta_firma_personal_orders_by_rol() -> None:
    conn = _mock_conn()
    conn.fetch.return_value = [_personal_row()]
    repo = TransferenciaRepository(conn, TENANT)
    tid = uuid.uuid4()

    rows = await repo.list_acta_firma_personal(tid)

    assert len(rows) == 1
    sql, *params = conn.fetch.call_args[0]
    assert "ORDER BY rol" in sql
    assert params == [tid]


@pytest.mark.asyncio
async def test_insert_acta_firma_personal_uses_on_conflict_do_nothing() -> None:
    conn = _mock_conn()
    conn.fetchrow.return_value = _personal_row()
    repo = TransferenciaRepository(conn, TENANT)

    row = await repo.insert_acta_firma_personal(
        {
            "transferencia_id": uuid.uuid4(),
            "rol": "recibe",
            "firmante_id": USER_ID,
            "firma_id": uuid.uuid4(),
            "minio_key_firmado": "k/x.xml",
            "xades_level": "XAdES-T",
            "ca_fingerprint": None,
            "acreditado": False,
        }
    )

    assert row is not None
    sql = conn.fetchrow.call_args[0][0]
    assert "ON CONFLICT (transferencia_id, rol) DO NOTHING" in sql


@pytest.mark.asyncio
async def test_insert_acta_firma_personal_lost_race_returns_none() -> None:
    conn = _mock_conn()
    conn.fetchrow.return_value = None  # ON CONFLICT DO NOTHING -> no row returned
    repo = TransferenciaRepository(conn, TENANT)

    row = await repo.insert_acta_firma_personal(
        {
            "transferencia_id": uuid.uuid4(),
            "rol": "recibe",
            "firmante_id": USER_ID,
            "firma_id": uuid.uuid4(),
            "minio_key_firmado": None,
            "xades_level": None,
            "ca_fingerprint": None,
            "acreditado": False,
        }
    )

    assert row is None


# ---------------------------------------------------------------------------
# TransferenciaService.firmar_personal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_firmar_personal_ok_inserts_row_and_audits() -> None:
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    inserted = _personal_row(transferencia_id=tid)
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", decidida_por=str(USER_ID)),
        None,  # caller_clearance -> PUBLICA(1)
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,  # get_acta_firma_personal -> aún no firmado
        None,  # get_acta_firma_excepcion -> sin excepción (AREA-07-10)
        inserted,  # insert_acta_firma_personal RETURNING
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)

    with patch(
        "app.clients.signature_client.sign_personal",
        new=AsyncMock(return_value=SignPersonalOutcome(signed=_signed_personal())),
    ) as mock_sign:
        result = await svc.firmar_personal(tid, actor, "123456", "Bearer faketoken")

    mock_sign.assert_awaited_once()
    call_kwargs = mock_sign.await_args.kwargs
    assert call_kwargs["objeto_tipo"] == "acta_transferencia_personal"
    assert call_kwargs["objeto_id"] == str(tid)
    assert call_kwargs["totp_code"] == "123456"
    assert call_kwargs["authorization"] == "Bearer faketoken"

    assert result.rol == "recibe"
    assert result.transferencia_id == tid

    audit_calls = [
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0][5] == "archive.acta_firmada_personal"


@pytest.mark.asyncio
async def test_firmar_personal_idempotent_does_not_call_signature_service() -> None:
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    existing = _personal_row(transferencia_id=tid)
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", decidida_por=str(USER_ID)),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        existing,  # get_acta_firma_personal -> YA firmado
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)

    with patch(
        "app.clients.signature_client.sign_personal", new=AsyncMock()
    ) as mock_sign:
        result = await svc.firmar_personal(tid, actor, "123456", "Bearer faketoken")

    mock_sign.assert_not_called()
    assert result.firma_id == existing["firma_id"]
    audit_calls = [
        c
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert audit_calls == []


@pytest.mark.asyncio
async def test_firmar_personal_transferencia_no_recibida_returns_409() -> None:
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)

    with pytest.raises(Exception) as exc_info:
        await svc.firmar_personal(tid, actor, "123456", "Bearer faketoken")
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error"] == "transferencia_no_recibida"


@pytest.mark.asyncio
async def test_firmar_personal_no_read_up_404_before_reaching_acta() -> None:
    """RF-SEG-08: un llamante sin clearance suficiente sobre un expediente
    CLASIFICADA(3) recibe 404 `expediente_not_found` — sin llegar siquiera a
    `repo.get_acta`/signature-service."""
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,  # caller_clearance -> PUBLICA(1)
        None,  # get_expediente_by_id: nivel_seguridad real no casa -> None
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)

    with patch("app.clients.signature_client.sign_personal", new=AsyncMock()) as mock_sign:
        with pytest.raises(Exception) as exc_info:
            await svc.firmar_personal(tid, actor, "123456", "Bearer faketoken")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "expediente_not_found"
    mock_sign.assert_not_called()


@pytest.mark.asyncio
async def test_firmar_personal_rejected_totp_incorrecto_propagates_status() -> None:
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", decidida_por=str(USER_ID)),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,
        None,  # get_acta_firma_excepcion -> sin excepción (AREA-07-10)
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)
    rejected = (401, {"error": "totp_incorrecto", "detail": "wrong code", "status": 401})

    with patch(
        "app.clients.signature_client.sign_personal",
        new=AsyncMock(return_value=SignPersonalOutcome(signed=None, rejected=rejected)),
    ):
        with pytest.raises(Exception) as exc_info:
            await svc.firmar_personal(tid, actor, "000000", "Bearer faketoken")
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"] == "totp_incorrecto"


@pytest.mark.asyncio
async def test_firmar_personal_rejected_sin_certificado_activo_returns_422() -> None:
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", decidida_por=str(USER_ID)),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,
        None,  # get_acta_firma_excepcion -> sin excepción (AREA-07-10)
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)
    rejected = (422, {"error": "sin_certificado_activo", "detail": "no cert", "status": 422})

    with patch(
        "app.clients.signature_client.sign_personal",
        new=AsyncMock(return_value=SignPersonalOutcome(signed=None, rejected=rejected)),
    ):
        with pytest.raises(Exception) as exc_info:
            await svc.firmar_personal(tid, actor, "123456", "Bearer faketoken")
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["error"] == "sin_certificado_activo"


@pytest.mark.asyncio
async def test_firmar_personal_channel_unavailable_returns_503() -> None:
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", decidida_por=str(USER_ID)),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,
        None,  # get_acta_firma_excepcion -> sin excepción (AREA-07-10)
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)

    with patch(
        "app.clients.signature_client.sign_personal",
        new=AsyncMock(return_value=SignPersonalOutcome(signed=None, unavailable=True)),
    ):
        with pytest.raises(Exception) as exc_info:
            await svc.firmar_personal(tid, actor, "123456", "Bearer faketoken")
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error"] == "canal_firma_personal_no_disponible"


@pytest.mark.asyncio
async def test_firmar_personal_fallo_remoto_nombra_la_causa_y_no_acusa_al_canal() -> None:
    """Gemelo del anterior, y el que faltaba: signature-service RESPONDE y falla.

    El defecto que corrige: el cliente colapsaba `RequestError` y cualquier 5xx
    en un mismo `unavailable=True`, así que la pantalla decía «signature-service
    no está disponible» las dos veces que ocurrió de verdad — y las dos veces
    era falso: la causa estaba dos servicios más abajo (MinIO sin disco; y el
    CHECK de `firma.nivel_conformidad` rechazando el valor que el propio
    signature-service escribe). Quien leía el mensaje iba a mirar el servicio
    sano.

    Se exige lo que distingue una corrección de un cambio de texto: que el
    `detail` CONTENGA la causa remota, y que NO diga que el canal está caído.
    """
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", decidida_por=str(USER_ID)),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,
        None,  # get_acta_firma_excepcion -> sin excepción (AREA-07-10)
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)

    causa = "artefacto_no_almacenado: MinIO rechazó el objeto (XMinioStorageFull)"
    with patch(
        "app.clients.signature_client.sign_personal",
        new=AsyncMock(
            return_value=SignPersonalOutcome(signed=None, remote_failure=(500, causa))
        ),
    ):
        with pytest.raises(Exception) as exc_info:
            await svc.firmar_personal(tid, actor, "123456", "Bearer faketoken")

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["error"] == "firma_personal_fallo_remoto"
    detalle = exc_info.value.detail["detail"]
    assert causa in detalle, "el mensaje debe llevar la causa remota, no resumirla"
    assert "500" in detalle
    assert "no está disponible" not in detalle
    # Fail-closed: el acto no se registra. Se mira el SQL enviado y no el número
    # de llamadas — la conexión simulada recibe además `SET search_path` y demás
    # tráfico de infraestructura, así que `assert_not_awaited()` sería falso y,
    # peor, `assert_awaited()` pasaría sin comprobar nada.
    sentencias = [
        str(c.args[0]).lower()
        for c in conn.execute.await_args_list + conn.fetchrow.await_args_list
        if c.args
    ]
    assert not [
        q for q in sentencias if "insert" in q and "acta_firma_personal" in q
    ], "no debe insertarse la firma cuando el servicio remoto falló"


@pytest.mark.asyncio
async def test_firmar_personal_non_receiver_returns_403_no_totp_burned() -> None:
    """Hallazgo Alta (auditoría de seguridad, generalizado a ambos roles en
    Inc.2): un actor con permiso/clearance suficientes pero que NO es ni
    `enviada_por` (remitente) ni `decidida_por` (receptor) intenta firmar el
    acta -- debe recibir 403 `firmante_no_es_parte` SIN llegar a
    `signature_client.sign_personal` (TOTP no quemado) y SIN insertar fila
    (idempotencia nunca filtra la firma ajena: el cotejo de identidad va
    ANTES del chequeo de idempotencia)."""
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    receptor_real = uuid.uuid4()
    otro_archivista = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(
            id=tid, expediente_id=exp_id, estado="recibida", decidida_por=str(receptor_real)
        ),
        None,  # caller_clearance -> PUBLICA(1)
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
    ]
    svc = _service(conn)
    actor = Actor(id=otro_archivista)

    with patch(
        "app.clients.signature_client.sign_personal", new=AsyncMock()
    ) as mock_sign:
        with pytest.raises(Exception) as exc_info:
            await svc.firmar_personal(tid, actor, "123456", "Bearer faketoken")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == "firmante_no_es_parte"
    mock_sign.assert_not_called()

    insert_calls = [
        c
        for c in conn.fetchrow.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO transferencia_acta_firma_personal" in c.args[0]
    ]
    assert insert_calls == []
    audit_calls = [
        c
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert audit_calls == []


@pytest.mark.asyncio
async def test_firmar_personal_legitimate_receiver_still_signs_ok() -> None:
    """Confirma que el fix del hallazgo Alta NO rompe el camino feliz: el
    receptor legítimo (`actor.audit_actor == decidida_por`) sigue pudiendo
    firmar."""
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    inserted = _personal_row(transferencia_id=tid)
    conn.fetchrow.side_effect = [
        _transfer_row(
            id=tid, expediente_id=exp_id, estado="recibida", decidida_por=str(USER_ID)
        ),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,  # get_acta_firma_personal -> aún no firmado
        None,  # get_acta_firma_excepcion -> sin excepción (AREA-07-10)
        inserted,  # insert_acta_firma_personal RETURNING
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)

    with patch(
        "app.clients.signature_client.sign_personal",
        new=AsyncMock(return_value=SignPersonalOutcome(signed=_signed_personal())),
    ) as mock_sign:
        result = await svc.firmar_personal(tid, actor, "123456", "Bearer faketoken")

    mock_sign.assert_awaited_once()
    assert result.rol == "recibe"


@pytest.mark.asyncio
async def test_firmar_personal_lost_race_no_phantom_audit_returns_winning_row() -> None:
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    winning_row = _personal_row(transferencia_id=tid)
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", decidida_por=str(USER_ID)),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,  # get_acta_firma_personal -> aún no firmado (a nuestros ojos)
        None,  # get_acta_firma_excepcion -> sin excepción (AREA-07-10)
        None,  # insert_acta_firma_personal -> ON CONFLICT DO NOTHING (perdió la carrera)
        winning_row,  # re-lectura tras perder la carrera
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)

    with patch(
        "app.clients.signature_client.sign_personal",
        new=AsyncMock(return_value=SignPersonalOutcome(signed=_signed_personal())),
    ):
        result = await svc.firmar_personal(tid, actor, "123456", "Bearer faketoken")

    assert result.firma_id == winning_row["firma_id"]
    audit_calls = [
        c
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert audit_calls == []


# ---------------------------------------------------------------------------
# TransferenciaService.acta_verificacion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acta_verificacion_firmada_receptor_when_personal_present() -> None:
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,  # caller_clearance
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        _acta_firma_row(
            transferencia_id=tid,
            estado="firmado",
            xades_level="XAdES-T",
            minio_key_firmado="file-id/acta-firmado.xml",
        ),
    ]
    # `get_expediente_by_id` consume `conn.fetch` PRIMERO (radicados del
    # expediente, `_get_radicados`) -- ANTES de `list_acta_firma_personal`.
    conn.fetch.side_effect = [[], [_personal_row(transferencia_id=tid)], []]  # + list_acta_firma_excepciones
    svc = _service(conn)

    with (
        patch(
            "app.clients.storage_client.download_signed_xml",
            new=AsyncMock(return_value="<xml/>"),
        ),
        patch(
            "app.clients.signature_client.verify",
            new=AsyncMock(
                return_value=VerifyResult(
                    valida=True,
                    nivel_conformidad="xades_personal_no_acreditado",
                    acreditado=False,
                    motivo=None,
                    revocacion_status="vigente",
                )
            ),
        ),
    ):
        result = await svc.acta_verificacion(tid, USER_ID)

    assert result.completitud == "firmada_receptor"
    assert result.sello.presente is True
    assert len(result.firmas_personales) == 1
    assert result.firmas_personales[0].rol == "recibe"
    assert result.firmas_personales[0].valida is True
    assert result.firmas_personales[0].revocacion_status == "vigente"
    assert result.valida is True
    assert result.acreditado is False


@pytest.mark.asyncio
async def test_acta_verificacion_gate_activo_sin_firma_personal_reports_pendiente(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "acta_firma_personal_require", "todos")
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        _acta_firma_row(transferencia_id=tid, estado="firmado", xades_level="XAdES-T"),
    ]
    conn.fetch.return_value = []  # sin firmas personales todavía
    svc = _service(conn)

    result = await svc.acta_verificacion(tid, USER_ID)

    assert result.completitud == "firma_personal_pendiente"
    assert result.firmas_personales == []


@pytest.mark.asyncio
async def test_acta_verificacion_gate_off_sin_firma_personal_reports_sellada() -> None:
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        _acta_firma_row(transferencia_id=tid, estado="firmado", xades_level="XAdES-T"),
    ]
    conn.fetch.return_value = []
    svc = _service(conn)

    result = await svc.acta_verificacion(tid, USER_ID)

    assert result.completitud == "sellada"


@pytest.mark.asyncio
async def test_acta_verificacion_no_read_up_404() -> None:
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,  # caller_clearance -> PUBLICA(1)
        None,  # get_expediente_by_id: no casa -> None
    ]
    svc = _service(conn)

    with pytest.raises(Exception) as exc_info:
        await svc.acta_verificacion(tid, USER_ID)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "expediente_not_found"


# ---------------------------------------------------------------------------
# Inc.2 — firma del REMITENTE (rol 'entrega'), derivación de rol, completitud
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_firmar_personal_remitente_signs_as_entrega() -> None:
    """Inc.2: quien ENVIÓ (`enviada_por`) firma el acta como rol 'entrega' --
    el rol se DERIVA de la identidad congelada, no del cliente. Airtight: se
    captura el payload del insert para comprobar que el rol derivado ('entrega')
    fluye hasta la escritura (no un hardcode 'recibe')."""
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    inserted = _personal_row(transferencia_id=tid, rol="entrega")
    conn.fetchrow.side_effect = [
        _transfer_row(
            id=tid,
            expediente_id=exp_id,
            estado="recibida",
            enviada_por=str(USER_ID),
            decidida_por=str(uuid.uuid4()),
        ),
        None,  # caller_clearance
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,  # get_acta_firma_personal(rol='entrega') -> aún no firmado
        None,  # get_acta_firma_excepcion -> sin excepción (AREA-07-10)
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)

    with (
        patch.object(
            TransferenciaRepository,
            "insert_acta_firma_personal",
            new=AsyncMock(return_value=inserted),
        ) as mock_insert,
        patch(
            "app.clients.signature_client.sign_personal",
            new=AsyncMock(return_value=SignPersonalOutcome(signed=_signed_personal())),
        ) as mock_sign,
    ):
        result = await svc.firmar_personal(tid, actor, "123456", "Bearer faketoken")

    mock_sign.assert_awaited_once()
    assert result.rol == "entrega"
    # El rol DERIVADO llega al insert (no un hardcode):
    assert mock_insert.await_args.args[0]["rol"] == "entrega"


@pytest.mark.asyncio
async def test_firmar_personal_tercero_ni_remitente_ni_receptor_403() -> None:
    """Un tercero cuya identidad NO casa ni con `enviada_por` ni con
    `decidida_por` (ambos poblados con otras personas) recibe 403
    `firmante_no_es_parte` sin firmar."""
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(
            id=tid,
            expediente_id=exp_id,
            estado="recibida",
            enviada_por=str(uuid.uuid4()),
            decidida_por=str(uuid.uuid4()),
        ),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)  # ni remitente ni receptor

    with patch("app.clients.signature_client.sign_personal", new=AsyncMock()) as mock_sign:
        with pytest.raises(Exception) as exc_info:
            await svc.firmar_personal(tid, actor, "123456", "Bearer faketoken")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == "firmante_no_es_parte"
    mock_sign.assert_not_called()


@pytest.mark.asyncio
async def test_firmar_personal_rol_param_no_concede_rol_ajeno_403() -> None:
    """El `rol` param NUNCA concede un rol: un receptor (roles={'recibe'}) que
    pide explícitamente 'entrega' recibe 403 sin firmar (no reintroduce el
    hallazgo Alta por la vía del param)."""
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(
            id=tid,
            expediente_id=exp_id,
            estado="recibida",
            enviada_por=str(uuid.uuid4()),
            decidida_por=str(USER_ID),
        ),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)  # es receptor, NO remitente

    with patch("app.clients.signature_client.sign_personal", new=AsyncMock()) as mock_sign:
        with pytest.raises(Exception) as exc_info:
            await svc.firmar_personal(
                tid, actor, "123456", "Bearer faketoken", rol_solicitado="entrega"
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == "firmante_no_es_parte"
    mock_sign.assert_not_called()


@pytest.mark.asyncio
async def test_firmar_personal_autotraslado_sin_rol_requiere_desempate_400() -> None:
    """Auto-traslado (`enviada_por == decidida_por`): la identidad da derecho a
    AMBOS roles -> el `rol` param es OBLIGATORIO para desambiguar; sin él, 400
    `rol_requerido` sin firmar."""
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(
            id=tid,
            expediente_id=exp_id,
            estado="recibida",
            enviada_por=str(USER_ID),
            decidida_por=str(USER_ID),
        ),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)

    with patch("app.clients.signature_client.sign_personal", new=AsyncMock()) as mock_sign:
        with pytest.raises(Exception) as exc_info:
            await svc.firmar_personal(tid, actor, "123456", "Bearer faketoken")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "rol_requerido"
    mock_sign.assert_not_called()


@pytest.mark.asyncio
async def test_firmar_personal_autotraslado_con_rol_firma_el_indicado() -> None:
    """Auto-traslado con `rol='entrega'`: firma ese rol (el UNIQUE por rol
    admite luego firmar 'recibe' como acto independiente)."""
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    inserted = _personal_row(transferencia_id=tid, rol="entrega")
    conn.fetchrow.side_effect = [
        _transfer_row(
            id=tid,
            expediente_id=exp_id,
            estado="recibida",
            enviada_por=str(USER_ID),
            decidida_por=str(USER_ID),
        ),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,  # idempotencia rol='entrega' -> aún no
        None,  # get_acta_firma_excepcion -> sin excepción (AREA-07-10)
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)

    with (
        patch.object(
            TransferenciaRepository,
            "insert_acta_firma_personal",
            new=AsyncMock(return_value=inserted),
        ) as mock_insert,
        patch(
            "app.clients.signature_client.sign_personal",
            new=AsyncMock(return_value=SignPersonalOutcome(signed=_signed_personal())),
        ),
    ):
        result = await svc.firmar_personal(
            tid, actor, "123456", "Bearer faketoken", rol_solicitado="entrega"
        )

    assert result.rol == "entrega"
    assert mock_insert.await_args.args[0]["rol"] == "entrega"


@pytest.mark.asyncio
async def test_acta_verificacion_firmada_remitente_only_entrega() -> None:
    """Solo el remitente firmó ('entrega') -> completitud 'firmada_remitente'
    (espejo por order-independence)."""
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        _acta_firma_row(
            transferencia_id=tid,
            estado="firmado",
            xades_level="XAdES-T",
            minio_key_firmado="file-id/acta-firmado.xml",
        ),
    ]
    conn.fetch.side_effect = [[], [_personal_row(transferencia_id=tid, rol="entrega")], []]  # + list_acta_firma_excepciones
    svc = _service(conn)

    with (
        patch(
            "app.clients.storage_client.download_signed_xml",
            new=AsyncMock(return_value="<xml/>"),
        ),
        patch(
            "app.clients.signature_client.verify",
            new=AsyncMock(
                return_value=VerifyResult(
                    valida=True,
                    nivel_conformidad="xades_personal_no_acreditado",
                    acreditado=False,
                    motivo=None,
                    revocacion_status="vigente",
                )
            ),
        ),
    ):
        result = await svc.acta_verificacion(tid, USER_ID)

    assert result.completitud == "firmada_remitente"
    assert len(result.firmas_personales) == 1
    assert result.firmas_personales[0].rol == "entrega"


@pytest.mark.asyncio
async def test_acta_verificacion_firmada_completa_ambos_roles() -> None:
    """Ambas partes firmaron ('entrega' + 'recibe') -> completitud
    'firmada_completa', `valida` compone las dos verificaciones."""
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        _acta_firma_row(
            transferencia_id=tid,
            estado="firmado",
            xades_level="XAdES-T",
            minio_key_firmado="file-id/acta-firmado.xml",
        ),
    ]
    conn.fetch.side_effect = [
        [],
        [
            _personal_row(transferencia_id=tid, rol="entrega"),
            _personal_row(transferencia_id=tid, rol="recibe"),
        ],    [],  # list_acta_firma_excepciones (AREA-07-10)
    ]
    svc = _service(conn)

    with (
        patch(
            "app.clients.storage_client.download_signed_xml",
            new=AsyncMock(return_value="<xml/>"),
        ),
        patch(
            "app.clients.signature_client.verify",
            new=AsyncMock(
                return_value=VerifyResult(
                    valida=True,
                    nivel_conformidad="xades_personal_no_acreditado",
                    acreditado=False,
                    motivo=None,
                    revocacion_status="vigente",
                )
            ),
        ),
    ):
        result = await svc.acta_verificacion(tid, USER_ID)

    assert result.completitud == "firmada_completa"
    assert len(result.firmas_personales) == 2
    assert {f.rol for f in result.firmas_personales} == {"entrega", "recibe"}
    assert result.valida is True


@pytest.mark.asyncio
async def test_acta_verificacion_auto_traslado_flag_cuando_mismo_firmante() -> None:
    """Observación de conformidad: `enviada_por == decidida_por` (auto-traslado)
    se expone como `auto_traslado=True` para que el auditor vea la ausencia de
    control dual sin cotejar UUIDs."""
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(
            id=tid,
            expediente_id=exp_id,
            estado="recibida",
            enviada_por=str(USER_ID),
            decidida_por=str(USER_ID),
        ),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        _acta_firma_row(
            transferencia_id=tid,
            estado="firmado",
            xades_level="XAdES-T",
            minio_key_firmado="file-id/acta-firmado.xml",
        ),
    ]
    conn.fetch.side_effect = [
        [],
        [
            _personal_row(transferencia_id=tid, rol="entrega", firmante_id=USER_ID),
            _personal_row(transferencia_id=tid, rol="recibe", firmante_id=USER_ID),
        ],    [],  # list_acta_firma_excepciones (AREA-07-10)
    ]
    svc = _service(conn)

    with (
        patch(
            "app.clients.storage_client.download_signed_xml",
            new=AsyncMock(return_value="<xml/>"),
        ),
        patch(
            "app.clients.signature_client.verify",
            new=AsyncMock(
                return_value=VerifyResult(
                    valida=True,
                    nivel_conformidad="xades_personal_no_acreditado",
                    acreditado=False,
                    motivo=None,
                    revocacion_status="vigente",
                )
            ),
        ),
    ):
        result = await svc.acta_verificacion(tid, USER_ID)

    assert result.completitud == "firmada_completa"
    assert result.auto_traslado is True


@pytest.mark.asyncio
async def test_acta_verificacion_auto_traslado_false_partes_distintas() -> None:
    """Partes distintas -> `auto_traslado=False` (control dual presente)."""
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(
            id=tid,
            expediente_id=exp_id,
            estado="recibida",
            enviada_por=str(uuid.uuid4()),
            decidida_por=str(USER_ID),
        ),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        _acta_firma_row(transferencia_id=tid, estado="firmado", xades_level="XAdES-T"),
    ]
    conn.fetch.side_effect = [[], [], []]  # + list_acta_firma_excepciones
    svc = _service(conn)

    result = await svc.acta_verificacion(tid, USER_ID)

    assert result.auto_traslado is False


# ---------------------------------------------------------------------------
# Inc.3 — firma del ELABORADOR (rol 'elabora'), eje ortogonal elaborador_firmado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_firmar_personal_elaborador_signs_as_elabora() -> None:
    """Inc.3: quien CREÓ la transferencia (`creada_por`) firma como rol
    'elabora' ("Elaborado por" del FUID) -- rol derivado de la identidad."""
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    inserted = _personal_row(transferencia_id=tid, rol="elabora")
    conn.fetchrow.side_effect = [
        _transfer_row(
            id=tid,
            expediente_id=exp_id,
            estado="recibida",
            creada_por=str(USER_ID),
            enviada_por=str(uuid.uuid4()),
            decidida_por=str(uuid.uuid4()),
        ),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,  # idempotencia rol='elabora' -> aún no
        None,  # get_acta_firma_excepcion -> sin excepción (AREA-07-10)
    ]
    svc = _service(conn)
    actor = Actor(id=USER_ID)

    with (
        patch.object(
            TransferenciaRepository,
            "insert_acta_firma_personal",
            new=AsyncMock(return_value=inserted),
        ) as mock_insert,
        patch(
            "app.clients.signature_client.sign_personal",
            new=AsyncMock(return_value=SignPersonalOutcome(signed=_signed_personal())),
        ),
    ):
        result = await svc.firmar_personal(tid, actor, "123456", "Bearer faketoken")

    assert result.rol == "elabora"
    assert mock_insert.await_args.args[0]["rol"] == "elabora"


@pytest.mark.asyncio
async def test_acta_verificacion_elaborador_firmado_ortogonal_a_completitud() -> None:
    """El elaborador es ADITIVO: firmar 'elabora' pone `elaborador_firmado=True`
    pero NO cambia `completitud` (que sigue siendo el eje de las dos PARTES);
    con solo 'elabora' y gate off, completitud='sellada'."""
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        _acta_firma_row(
            transferencia_id=tid,
            estado="firmado",
            xades_level="XAdES-T",
            minio_key_firmado="file-id/acta-firmado.xml",
        ),
    ]
    conn.fetch.side_effect = [[], [_personal_row(transferencia_id=tid, rol="elabora")], []]  # + list_acta_firma_excepciones
    svc = _service(conn)

    with (
        patch(
            "app.clients.storage_client.download_signed_xml",
            new=AsyncMock(return_value="<xml/>"),
        ),
        patch(
            "app.clients.signature_client.verify",
            new=AsyncMock(
                return_value=VerifyResult(
                    valida=True,
                    nivel_conformidad="xades_personal_no_acreditado",
                    acreditado=False,
                    motivo=None,
                    revocacion_status="vigente",
                )
            ),
        ),
    ):
        result = await svc.acta_verificacion(tid, USER_ID)

    assert result.elaborador_firmado is True
    assert result.completitud == "sellada"  # elaborador NO cuenta para completitud
    assert result.firmas_personales[0].rol == "elabora"


@pytest.mark.asyncio
async def test_firmar_personal_tres_roles_coincidentes_requiere_rol() -> None:
    """Los TRES responsables son la misma persona (`creada_por == enviada_por ==
    decidida_por`): la identidad da derecho a los 3 roles -> `rol` obligatorio
    (400 sin él); con `rol='elabora'` firma ese."""
    conn = _mock_conn()
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()

    def _rows():
        return [
            _transfer_row(
                id=tid,
                expediente_id=exp_id,
                estado="recibida",
                creada_por=str(USER_ID),
                enviada_por=str(USER_ID),
                decidida_por=str(USER_ID),
            ),
            None,
            make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
            _acta_row(transferencia_id=tid),
        ]

    # Sin rol -> 400 rol_requerido, sin firmar.
    conn.fetchrow.side_effect = _rows()
    svc = _service(conn)
    actor = Actor(id=USER_ID)
    with patch("app.clients.signature_client.sign_personal", new=AsyncMock()) as mock_sign:
        with pytest.raises(Exception) as exc_info:
            await svc.firmar_personal(tid, actor, "123456", "Bearer faketoken")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "rol_requerido"
    mock_sign.assert_not_called()

    # Con rol='elabora' -> firma ese.
    conn2 = _mock_conn()
    inserted = _personal_row(transferencia_id=tid, rol="elabora")
    conn2.fetchrow.side_effect = [
        _transfer_row(
            id=tid,
            expediente_id=exp_id,
            estado="recibida",
            creada_por=str(USER_ID),
            enviada_por=str(USER_ID),
            decidida_por=str(USER_ID),
        ),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,
        None,  # get_acta_firma_excepcion -> sin excepción (AREA-07-10)
    ]
    svc2 = _service(conn2)
    with (
        patch.object(
            TransferenciaRepository,
            "insert_acta_firma_personal",
            new=AsyncMock(return_value=inserted),
        ),
        patch(
            "app.clients.signature_client.sign_personal",
            new=AsyncMock(return_value=SignPersonalOutcome(signed=_signed_personal())),
        ),
    ):
        result = await svc2.firmar_personal(
            tid, actor, "123456", "Bearer faketoken", rol_solicitado="elabora"
        )
    assert result.rol == "elabora"
