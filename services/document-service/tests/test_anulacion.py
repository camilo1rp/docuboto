# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de anulación de radicado en dos pasos (paridad legado).

Incluye la emisión del evento `document.radicado.annulled` al aprobar (residual
"purga-en-anulación", E21) — gemelo de los tests de emisión de
`test_documents.py`/`test_reclassify.py`: solo `aprobar` publica; `solicitar` y
`rechazar` no cambian el estado del radicado y no emiten nada.
"""

import json
import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)
_USER = str(uuid.uuid4())
# Quien RESUELVE la anulación es siempre otra persona: solicitar y aprobar no
# pueden ser el mismo acto (ver `TestControlDual` al final de este archivo).
# Antes estos tests aprobaban con `_USER`, el mismo que figuraba como
# solicitante en el fixture, así que la suite entera daba por buena una
# aprobación que el sistema no debe aceptar.
_SUPERVISOR = str(uuid.uuid4())


def _clearance_row(max_level: int = 3, is_root: bool = False) -> dict:
    """Fila que consume `resolve_caller_clearance` (SELECT is_root, max_level).

    Aprobar/rechazar resuelven el clearance del llamante antes de tocar la
    solicitud (RF-SEG-08), así que consumen UN `fetchrow` previo.
    """
    return {"is_root": is_root, "max_level": max_level}


def _anulacion_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "radicado_id": uuid.uuid4(),
        "estado": "solicitada",
        "causal": "ANU-ERR",
        "motivo": "Error de radicación",
        "solicitado_por": uuid.UUID(_USER),
        "solicitado_at": NOW,
        "decidido_por": None,
        "decidido_at": None,
        "observacion": None,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_solicitar_anulacion(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    # `radicado_estado_y_tipo` (fetchrow) + la fila de la solicitud. El tipo es
    # "S": una Entrada NO se anula, y ese caso tiene su propio test.
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"status": "registered", "doc_type": "S"},
        _anulacion_row(radicado_id=rid),
    ]

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion",
        json={"causal": "ANU-ERR", "motivo": "Error de radicación"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 201
    assert resp.json()["estado"] == "solicitada"


@pytest.mark.asyncio
async def test_solicitar_sobre_anulado_409(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"status": "anulado", "doc_type": "S"},
    ]
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/anulacion",
        json={"causal": "ANU-DUP"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "ya_anulado"


@pytest.mark.asyncio
async def test_aprobar_anula_el_radicado(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    # get_active (solicitada) → decide (UPDATE RETURNING aprobada)
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),  # resolve_caller_clearance
        _anulacion_row(radicado_id=rid, estado="solicitada"),
        _anulacion_row(
            radicado_id=rid, estado="aprobada", decidido_por=uuid.UUID(_USER), decidido_at=NOW
        ),
    ]
    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion/aprobar",
        json={"observacion": "Procede"},
        headers={**TENANT_HEADERS, "X-User-Id": _SUPERVISOR},
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] == "aprobada"


@pytest.mark.asyncio
async def test_aprobar_sin_solicitud_404(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None  # get_active → None
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/anulacion/aprobar",
        json={},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "sin_solicitud_activa"


# ---------------------------------------------------------------------------
# D-06: comprobaciones de autorización (PERM_SOL_ANULAR / PERM_PANU_CODI)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solicitar_sin_permiso_403(client_with_conn) -> None:
    """check_permission usa fetchval; None → usuario no encontrado → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # sin fila en auth_users → no tiene permiso
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/anulacion",
        json={"causal": "ANU-ERR", "motivo": "Sin permiso"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_solicitar_con_permiso_201(client_with_conn) -> None:
    """fetchval truthy → permission ok; fetchrow → estado/tipo, luego INSERT."""
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchval.return_value = True  # permission granted
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"status": "registered", "doc_type": "S"},
        _anulacion_row(radicado_id=rid),
    ]
    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion",
        json={"causal": "ANU-ERR", "motivo": "Con permiso"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 201
    assert resp.json()["estado"] == "solicitada"


@pytest.mark.asyncio
async def test_aprobar_sin_permiso_403(client_with_conn) -> None:
    """PERM_PANU_CODI requerido para aprobar; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/anulacion/aprobar",
        json={},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_rechazar_sin_permiso_403(client_with_conn) -> None:
    """PERM_PANU_CODI requerido para rechazar; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/anulacion/rechazar",
        json={},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


# ---------------------------------------------------------------------------
# Residual "purga-en-anulación" (E21): document.radicado.annulled al aprobar.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aprobar_emite_evento_annulled(client_with_redis) -> None:
    """Aprobar la anulación publica `document.radicado.annulled` en
    orpycamcp.document.events (ADR-010) — gemelo de `_emit_created`.
    knowledge-service lo consume para purgar PERMANENTEMENTE el chunk indexado."""
    client, mock_conn, fake_redis = client_with_redis
    rid = uuid.uuid4()
    tracking = "2026-DEMO-E-000042"
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),  # resolve_caller_clearance
        _anulacion_row(radicado_id=rid, estado="solicitada"),
        _anulacion_row(
            radicado_id=rid, estado="aprobada", decidido_por=uuid.UUID(_USER), decidido_at=NOW
        ),
    ]
    mock_conn.fetchval.return_value = tracking  # get_tracking_number

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion/aprobar",
        json={"observacion": "Procede"},
        headers={**TENANT_HEADERS, "X-User-Id": _SUPERVISOR},
    )

    assert resp.status_code == 200
    assert fake_redis.xadd.await_count == 1
    stream, fields = fake_redis.xadd.await_args.args
    assert stream == "orpycamcp.document.events"
    assert fields["event_type"] == "document.radicado.annulled"
    assert fields["tenant_slug"] == TENANT_HEADERS["X-Tenant-Slug"]
    assert fields["dedup_key"] == f"{tracking}:annulled"
    payload = json.loads(fields["payload"])
    assert payload == {"id": str(rid), "tracking_number": tracking}


@pytest.mark.asyncio
async def test_rechazar_no_emite_evento(client_with_redis) -> None:
    """Rechazar NO cambia el estado del radicado: no publica ningún evento."""
    client, mock_conn, fake_redis = client_with_redis
    rid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),  # resolve_caller_clearance
        _anulacion_row(radicado_id=rid, estado="solicitada"),
        _anulacion_row(
            radicado_id=rid, estado="rechazada", decidido_por=uuid.UUID(_USER), decidido_at=NOW
        ),
    ]

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion/rechazar",
        json={"observacion": "No procede"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 200
    fake_redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_solicitar_no_emite_evento(client_with_redis) -> None:
    """Solicitar (paso 1) tampoco cambia el estado del radicado: sin evento."""
    client, mock_conn, fake_redis = client_with_redis
    rid = uuid.uuid4()
    # `radicado_estado_y_tipo` (fetchrow) + la fila de la solicitud. El tipo es
    # "S": una Entrada NO se anula, y ese caso tiene su propio test.
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"status": "registered", "doc_type": "S"},
        _anulacion_row(radicado_id=rid),
    ]

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion",
        json={"causal": "ANU-ERR", "motivo": "Error de radicación"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 201
    fake_redis.xadd.assert_not_awaited()


@pytest.mark.asyncio
async def test_aprobar_evento_best_effort_no_revierte_anulacion(client_with_redis) -> None:
    """Si Redis falla al publicar, la anulación NO se revierte (evento best-effort,
    gemelo de `test_create_document_survives_event_failure`)."""
    client, mock_conn, fake_redis = client_with_redis
    fake_redis.xadd.side_effect = RuntimeError("redis down")
    rid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),  # resolve_caller_clearance
        _anulacion_row(radicado_id=rid, estado="solicitada"),
        _anulacion_row(
            radicado_id=rid, estado="aprobada", decidido_por=uuid.UUID(_USER), decidido_at=NOW
        ),
    ]
    mock_conn.fetchval.return_value = "2026-DEMO-E-000099"

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion/aprobar",
        json={"observacion": "Procede"},
        headers={**TENANT_HEADERS, "X-User-Id": _SUPERVISOR},
    )

    assert resp.status_code == 200  # la anulación se aplicó pese al fallo de Redis
    assert resp.json()["estado"] == "aprobada"


# ---------------------------------------------------------------------------
# Reglas del TIPO documental — otro eje distinto del permiso
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_un_radicado_de_entrada_no_se_anula(client_with_conn) -> None:
    """La regla es del DOCUMENTO, no del usuario.

    Si un documento entró a la entidad, entró: ese es un hecho ocurrido, no una
    decisión revisable. La anulación existe para lo que la entidad **produce**
    —una Salida o un Interno emitidos por error—, donde sí cabe dejar sin efecto
    lo emitido. Sobre una Entrada no hay nada que dejar sin efecto.

    Hasta ahora el servicio solo miraba que el radicado existiera y no estuviera
    ya anulado, así que con `PERM_SOL_ANULAR` se anulaba una Entrada igual. El
    permiso responde a «¿está autorizada esta persona?»; esto responde a una
    pregunta anterior: «¿es este acto posible sobre esta clase de documento?».

    Se comprueba que **no se inserta la solicitud**: el daño no es el código de
    respuesta, es que quede en la base una solicitud de anulación sobre un
    documento que no admite ninguna.
    """
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchval.return_value = True  # permiso CONCEDIDO: no es el eje
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"status": "registered", "doc_type": "E"},
    ]

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion",
        json={"causal": "ANU-ERR", "motivo": "Me equivoqué"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 409
    cuerpo = resp.json()["detail"]
    assert cuerpo["error"] == "acto_no_aplica_al_tipo"
    # El mensaje explica POR QUÉ y ofrece la vía que sí existe: un 403 seco
    # mandaría a la persona a pedir un permiso que no le iba a servir de nada.
    assert "Entrada" in cuerpo["detail"]
    # Solo se consumieron las DOS lecturas previas: ninguna inserción.
    assert mock_conn.fetchrow.await_count == 2


@pytest.mark.asyncio
async def test_ni_siquiera_ROOT_anula_una_entrada(client_with_conn) -> None:
    """El bypass de ROOT es sobre AUTORIZACIÓN, no sobre lo que es posible.

    ROOT sirve para permitir a quien no estaba permitido. No convierte en
    posible un acto que sobre ese tipo de documento no existe — si lo hiciera,
    la regla archivística dependería de con qué cuenta se entre.
    """
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=3, is_root=True),  # ROOT
        {"status": "registered", "doc_type": "E"},
    ]

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion",
        json={"causal": "ANU-ERR", "motivo": "Soy root"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "acto_no_aplica_al_tipo"


@pytest.mark.asyncio
async def test_una_salida_si_se_anula(client_with_conn) -> None:
    """Control positivo, sin el cual una regla que negara SIEMPRE pasaría los dos
    tests de arriba y dejaría la anulación inservible para todo el sistema."""
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        {"status": "registered", "doc_type": "S"},
        _anulacion_row(radicado_id=rid),
    ]

    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion",
        json={"causal": "ANU-ERR", "motivo": "Salida emitida por error"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Segregación de funciones: quien pide la anulación no la aprueba
# ---------------------------------------------------------------------------
#
# Anular retira de la circulación un documento oficial y no se deshace (Ley
# 594/2000). La pantalla que recoge la solicitud ya se lo dice a quien la hace
# —«la anulación requiere aprobación posterior por un supervisor»— pero eso era
# una promesa que nadie hacía cumplir: en la revisión por navegador, el mismo
# usuario solicitó y aprobó seguido, y el radicado quedó `anulado` sin que
# ningún tercero lo hubiera mirado. La comprobación no vivía en ningún sitio, y
# los tres tests de aprobación de este archivo la daban por buena porque
# resolvían con el mismo usuario que figuraba como solicitante.


@pytest.mark.asyncio
async def test_el_solicitante_no_puede_aprobar_su_propia_anulacion(client_with_conn) -> None:
    """409 explícito, no 403: no es falta de permiso, es que el acto exige otro."""
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        _anulacion_row(radicado_id=rid, estado="solicitada", solicitado_por=uuid.UUID(_USER)),
    ]
    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion/aprobar",
        json={"observacion": "La apruebo yo mismo"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "aprobacion_del_propio_solicitante"


@pytest.mark.asyncio
async def test_el_mensaje_ofrece_la_via_alternativa(client_with_conn) -> None:
    """Decir «no puede» sin decir «qué sí puede» deja el trámite parado.

    Quien topa con este límite tiene dos salidas legítimas —pasarla a otro
    funcionario, o desistir rechazándola— y el mensaje las nombra.
    """
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        _anulacion_row(radicado_id=rid, estado="solicitada", solicitado_por=uuid.UUID(_USER)),
    ]
    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion/aprobar",
        json={},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    detalle = resp.json()["detail"]["detail"]
    assert "rechazarla" in detalle
    assert "otro funcionario" in detalle


@pytest.mark.asyncio
async def test_otra_persona_SI_puede_aprobarla(client_with_conn) -> None:
    """Control positivo: la corrección no puede consistir en impedir el acto."""
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        _anulacion_row(radicado_id=rid, estado="solicitada", solicitado_por=uuid.UUID(_USER)),
        _anulacion_row(
            radicado_id=rid,
            estado="aprobada",
            decidido_por=uuid.UUID(_SUPERVISOR),
            decidido_at=NOW,
        ),
    ]
    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion/aprobar",
        json={"observacion": "Verificado el error de radicación"},
        headers={**TENANT_HEADERS, "X-User-Id": _SUPERVISOR},
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] == "aprobada"


@pytest.mark.asyncio
async def test_el_solicitante_SI_puede_rechazar_su_propia_solicitud(client_with_conn) -> None:
    """Rechazar lo propio es desistir, no ejecutar el acto irreversible.

    Bloquearlo también dejaría solicitudes vivas que su autor no puede retirar,
    esperando a un tercero que quizá no llegue nunca.
    """
    client, mock_conn = client_with_conn
    rid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        _anulacion_row(radicado_id=rid, estado="solicitada", solicitado_por=uuid.UUID(_USER)),
        _anulacion_row(
            radicado_id=rid,
            estado="rechazada",
            decidido_por=uuid.UUID(_USER),
            decidido_at=NOW,
        ),
    ]
    resp = await client.post(
        f"/api/v1/documents/{rid}/anulacion/rechazar",
        json={"observacion": "Desisto: el destinatario sí era el correcto"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] == "rechazada"
