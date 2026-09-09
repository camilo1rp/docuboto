# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de transferencias documentales (E12)."""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.services.transferencia_service import TransferenciaService
from tests.conftest import (
    TENANT_HEADERS,
    USER_ID,
    make_expediente_record,
    make_radicado_link_record,
)


# H-H (E06/F4): `recibir()` intenta el sellado SÍNCRONO ACOTADO del acta
# POST-COMMIT (`TransferenciaService._sellar_acta_post_commit`, vía HTTP a
# signature-service) — mismo criterio que los tests de `close_expediente`
# mockean `IndexService.firmar_indice_close` (ver `test_expediente_lifecycle.py`):
# los tests de ESTE módulo que no versan sobre el sellado del acta mockean
# esta frontera para no depender de un signature-service real ni pagar el
# `acta_sign_sync_delay_seconds` de un reintento transitorio real. Función
# (no un objeto único a nivel de módulo): cada `with` necesita su PROPIO
# `AsyncMock` -- un único mock reutilizado entre tests acumularía
# `await_count` de llamadas de tests anteriores.
def _mock_sellar_acta():
    return patch.object(
        TransferenciaService, "_sellar_acta_post_commit", new=AsyncMock(return_value=None)
    )

NOW = datetime.now(tz=UTC)

# Fila del índice electrónico usada por `IndexService.fixity` (E12 H-G) en el
# gate de recepción -- `xml`/`xml_sha256` DEBEN ser consistentes entre sí
# (`hashlib.sha256(xml).hexdigest() == xml_sha256`) para que `huella_indice_ok`
# dé `True` en el camino feliz de `recibir()`.
_INDICE_XML = "<indiceElectronico/>"
_INDICE_XML_SHA256 = hashlib.sha256(_INDICE_XML.encode("utf-8")).hexdigest()


def _indice_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "estado": "firmado",
        "version": 0,
        "xml": _INDICE_XML,
        "xml_sha256": _INDICE_XML_SHA256,
    }
    base.update(kw)
    return base


def _audit_log_calls(mock_conn) -> list[tuple]:
    """args: (sql, tenant, service, actor, canal, action, object_type, object_ref,
    payload, prev_hash, hash) — mismo shape que el resto de test_expediente_*."""
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]


def _transfer_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "tipo": "primaria",
        "estado": "preparada",
        "expediente_id": uuid.uuid4(),
        "ubicacion_origen_id": None,
        "ubicacion_destino_id": None,
        "creada_por": "ana",
        "observacion": None,
        "created_at": NOW,
        "enviada_at": None,
        # `enviada_por` (migración 020, H-B/H-C): responsable de la remisión.
        "enviada_por": None,
        "recibida_at": None,
        "decidida_por": None,
        # Cabecera del Anexo FUID (migración 052): el objeto que escribió quien
        # preparó, y el snapshot de su dependencia. Van en el `_transfer_row`
        # base y no solo en los tests que los miran: son columnas de `_COLS`, y
        # una fixtura que omite una columna real esconde justo el desajuste que
        # existe para detectar.
        "objeto": "Transferencia de la serie de contratos de la vigencia",
        "oficina_productora_codigo": 120,
        "oficina_productora_nombre": "Oficina de Contratacion",
        "unidad_administrativa_codigo": 100,
        "unidad_administrativa_nombre": "Secretaria General",
    }
    base.update(kw)
    return base


def _depe_row(**kw) -> dict:
    """Lo que devuelve `TransferenciaRepository.dependencia_de_usuario`: la
    oficina productora del que prepara y su unidad administrativa (padre)."""
    base = {
        "oficina_productora_codigo": 120,
        "oficina_productora_nombre": "Oficina de Contratacion",
        "unidad_administrativa_codigo": 100,
        "unidad_administrativa_nombre": "Secretaria General",
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_create_requires_closed_expediente(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: permission ok; service raises 409
    mock_conn.fetchrow.return_value = make_expediente_record(exp_status="open")
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/transferencias",
        json={"tipo": "primaria", "expediente_id": str(uuid.uuid4()), "objeto": "Transferencia de la serie de contratos de la vigencia"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "expediente_not_closed"


@pytest.mark.asyncio
async def test_create_transferencia(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    # get_expediente (closed) + fetch radicados, insert transferencia
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        # `dependencia_de_usuario` (migración 052): la oficina productora de
        # quien prepara, resuelta antes del INSERT.
        _depe_row(),
        _transfer_row(expediente_id=exp_id),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/transferencias",
        json={"tipo": "primaria", "expediente_id": str(exp_id), "objeto": "Transferencia de la serie de contratos de la vigencia"},
        headers={**TENANT_HEADERS, "X-Username": "ana"},
    )
    assert resp.status_code == 201
    assert resp.json()["estado"] == "preparada"


@pytest.mark.asyncio
async def test_enviar_then_recibir_freezes_expediente(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    # enviar: _require.get(preparada) → _expediente_code (código para la hoja
    # de ruta/auditoría, H-B) → update_estado(expected='preparada', 'enviada').
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="preparada"),
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        _transfer_row(
            id=tid,
            expediente_id=exp_id,
            estado="enviada",
            enviada_at=NOW,
            enviada_por=str(USER_ID),
        ),
    ]
    mock_conn.fetch.return_value = []
    env = await client.post(f"/api/v1/transferencias/{tid}/enviar", headers=TENANT_HEADERS)
    assert env.status_code == 200
    assert env.json()["estado"] == "enviada"

    # recibir: get(enviada) → get_current(gate, índice firmado) →
    # fixity() [get_expediente_by_id + get_current + get_items, E12 H-G] →
    # update(recibida) → transition_expediente(transferred)
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        _indice_row(),
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        _indice_row(),
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", recibida_at=NOW),
        make_expediente_record(exp_id=exp_id, exp_status="transferred"),
    ]
    mock_conn.fetch.return_value = []
    with _mock_sellar_acta():
        rec = await client.post(
            f"/api/v1/transferencias/{tid}/recibir",
            headers={**TENANT_HEADERS, "X-Username": "jefe"},
        )
    assert rec.status_code == 200
    assert rec.json()["estado"] == "recibida"


@pytest.mark.asyncio
async def test_recibir_sin_indice_firmado_409(client_with_conn) -> None:
    """E06 Inc.1 / Acuerdo AGN 001/2024 art. 4.3.2.4: la transferencia NO puede
    congelar (`transferred`) un expediente cuyo índice quedó 'pendiente_firma'."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        {"id": uuid.uuid4(), "estado": "pendiente_firma"},
    ]
    resp = await client.post(
        f"/api/v1/transferencias/{tid}/recibir",
        headers={**TENANT_HEADERS, "X-Username": "jefe"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "indice_no_firmado"


@pytest.mark.asyncio
async def test_recibir_invalid_from_preparada(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: permission ok; service raises 409
    tid = uuid.uuid4()
    mock_conn.fetchrow.return_value = _transfer_row(id=tid, estado="preparada")

    resp = await client.post(f"/api/v1/transferencias/{tid}/recibir", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "invalid_status_transition"


@pytest.mark.asyncio
async def test_transferencia_fuid(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    # Secuencia real (RF-SEG-08 V2, no-read-up del FUID single):
    #   1) repo.get(transferencia)
    #   2) caller_clearance (TransferenciaService.fuid) -> None -> PUBLICA(1)
    #   3) get_expediente_by_id -> nivel_seguridad=1 <= PUBLICA -> visible
    #   4) caller_clearance de nuevo (dentro de FisicoService.generate_fuid)
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        None,
    ]
    # fetch: 1) _get_radicados (irrelevante al FUID, forma distinta a la fila
    # del FUID) ; 2) fuid_inventory (las filas reales del FUID).
    mock_conn.fetch.side_effect = [
        [],
        [
            {
                "expediente_id": exp_id,
                "expediente_code": "EXP-2024-0001",
                "asunto": "Contrato",
                "fecha_inicial": NOW,
                "fecha_final": None,
                "serie_code": "100",
                "serie_name": "Contratos",
                "signatura": "DEP01-C0124",
                "unidad_tipo": "caja",
                "unidad_codigo": "C0124",
                "soporte": "papel",
                "num_folios": 200,
                "folio_inicio": 1,
                "folio_fin": 200,
            },
        ],
    ]
    resp = await client.get(f"/api/v1/transferencias/{tid}/fuid", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_transferencia_fuid_no_read_up_404(client_with_conn) -> None:
    """RF-SEG-08 V2: el FUID de la transferencia es CONTENIDO del expediente
    (asunto/serie/signatura — Ley 1712/2014 arts. 18-19; Ley 594/2000 art.
    27), no un acto de custodia. Un llamante sin clearance suficiente sobre
    un expediente CLASIFICADA(3) recibe 404 `expediente_not_found`,
    indistinguible de un expediente inexistente (no revela ni existencia ni
    estado). Contraste: el MISMO expediente, con clearance suficiente, sí
    genera el FUID (200) — este test rompe si alguien quita el no-read-up
    del FUID single."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()

    # No-lector: caller_clearance -> PUBLICA(1); get_expediente_by_id no
    # matchea `nivel_seguridad <= max_clearance` sobre el expediente real
    # (CLASIFICADA=3) -> None (la BD real filtra por fila; el mock simula
    # ese resultado, mismo patrón que test_archive.py::
    # test_roles_autorizados_clearance_scoped_returns_404).
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id),
        None,
        None,
    ]
    resp = await client.get(f"/api/v1/transferencias/{tid}/fuid", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "expediente_not_found"

    # Mismo expediente/transferencia, caller root (CLASIFICADA=3): sí puede.
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id),
        {"is_root": True, "max_level": None},
        make_expediente_record(exp_id=exp_id, nivel_seguridad=3),
        {"is_root": True, "max_level": None},
    ]
    mock_conn.fetch.side_effect = [
        [],
        [
            {
                "expediente_id": exp_id,
                "expediente_code": "EXP-2024-0001",
                "asunto": "Contrato",
                "fecha_inicial": NOW,
                "fecha_final": None,
                "serie_code": "100",
                "serie_name": "Contratos",
                "signatura": "DEP01-C0124",
                "unidad_tipo": "caja",
                "unidad_codigo": "C0124",
                "soporte": "papel",
                "num_folios": 200,
                "folio_inicio": 1,
                "folio_fin": 200,
            },
        ],
    ]
    resp2 = await client.get(f"/api/v1/transferencias/{tid}/fuid", headers=TENANT_HEADERS)
    assert resp2.status_code == 200
    assert resp2.json()["total"] == 1


@pytest.mark.asyncio
async def test_transferencia_fuid_not_found_before_clearance(client_with_conn) -> None:
    """El 404 `transferencia_not_found` sigue resolviéndose ANTES que el
    no-read-up (RF-SEG-08 V2): una transferencia inexistente nunca llega a
    resolver clearance ni a tocar el expediente."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: USUA_PERM_EXPEDIENTE granted
    mock_conn.fetchrow.return_value = None  # repo.get(transferencia_id) -> no existe
    resp = await client.get(f"/api/v1/transferencias/{uuid.uuid4()}/fuid", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "transferencia_not_found"


# ---------------------------------------------------------------------------
# D-08: comprobaciones de autorización (USUA_PERM_EXPEDIENTE)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_transferencia_sin_permiso_403(client_with_conn) -> None:
    """check_permission usa fetchval; None → usuario no encontrado → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # sin fila en auth_users → sin permiso
    resp = await client.post(
        "/api/v1/transferencias",
        json={"tipo": "primaria", "expediente_id": str(uuid.uuid4()), "objeto": "Transferencia de la serie de contratos de la vigencia"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_list_transferencias_sin_permiso_403(client_with_conn) -> None:
    """GET también requiere USUA_PERM_EXPEDIENTE."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get("/api/v1/transferencias", headers=TENANT_HEADERS)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_enviar_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/transferencias/{uuid.uuid4()}/enviar",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_recibir_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/transferencias/{uuid.uuid4()}/recibir",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_transferencia_fuid_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get(
        f"/api/v1/transferencias/{uuid.uuid4()}/fuid",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_transferencia_con_permiso_201(client_with_conn) -> None:
    """fetchval truthy → permission ok; fetchrow → expediente closed → INSERT."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_EXPEDIENTE granted
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        # `dependencia_de_usuario` (migración 052): la oficina productora de
        # quien prepara, resuelta antes del INSERT.
        _depe_row(),
        _transfer_row(expediente_id=exp_id),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/transferencias",
        json={"tipo": "primaria", "expediente_id": str(exp_id), "objeto": "Transferencia de la serie de contratos de la vigencia"},
        headers={**TENANT_HEADERS, "X-Username": "ana"},
    )
    assert resp.status_code == 201
    assert resp.json()["estado"] == "preparada"


# ---------------------------------------------------------------------------
# H-B: create/enviar/rechazar SÍ dejan asiento de auditoría (antes 0 llamadas
# a `audit.append` en los tres). H-D: el actor escrito es SIEMPRE X-User-Id
# (`Actor.audit_actor`), nunca el username -- aunque la petición traiga
# X-Username, no gana la carrera de identidad (antes invertida en esta ruta).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_transferencia_audits_success(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08 gate + audit.append prev_hash
    exp_id = uuid.uuid4()
    exp_closed = make_expediente_record(exp_id=exp_id, exp_status="closed")
    mock_conn.fetchrow.side_effect = [exp_closed, _depe_row(), _transfer_row(expediente_id=exp_id)]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/transferencias",
        json={"tipo": "primaria", "expediente_id": str(exp_id), "objeto": "Transferencia de la serie de contratos de la vigencia"},
        # X-Username presente A PROPÓSITO: H-D exige que el actor escrito sea
        # el UUID (X-User-Id), NUNCA "ana" -- antes de este incremento
        # `_actor()` invertía la prioridad (X-Username primero).
        headers={**TENANT_HEADERS, "X-Username": "ana"},
    )
    assert resp.status_code == 201

    audit_calls = _audit_log_calls(mock_conn)
    creada = next(a for a in audit_calls if a[5] == "archive.transferencia_preparada")
    assert creada[6] == "transferencia"
    assert creada[3] == str(USER_ID)  # actor -- NUNCA "ana"
    payload = json.loads(creada[8])
    assert payload["expediente_code"] == exp_closed["code"]
    assert payload["estado_nuevo"] == "preparada"

    evento_call = next(
        c
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO expediente_eventos" in c.args[0]
    )
    # (expediente_id, numero, accion, actor, comentario, detalles)
    assert evento_call.args[3] == "transferencia_preparada"
    assert evento_call.args[4] == USER_ID


@pytest.mark.asyncio
async def test_create_transferencia_denied_audits_failure(client_with_conn) -> None:
    """El intento denegado MÁS FRECUENTE (expediente no cerrado) también deja
    traza -- antes `create()` no auditaba NADA, ni éxito ni fallo."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_expediente_record(exp_id=exp_id, exp_status="open")
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/transferencias",
        json={"tipo": "primaria", "expediente_id": str(exp_id), "objeto": "Transferencia de la serie de contratos de la vigencia"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409

    audit_calls = _audit_log_calls(mock_conn)
    denial = next(a for a in audit_calls if a[5] == "archive.transferencia_preparada_failed")
    assert denial[3] == str(USER_ID)
    payload = json.loads(denial[8])
    assert payload["motivo"] == "expediente_not_closed"
    assert not any(a[5] == "archive.transferencia_preparada" for a in audit_calls)


@pytest.mark.asyncio
async def test_enviar_audits_success_with_actor_uuid(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="preparada"),
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada", enviada_at=NOW),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        f"/api/v1/transferencias/{tid}/enviar",
        headers={**TENANT_HEADERS, "X-Username": "remitente"},
    )
    assert resp.status_code == 200

    audit_calls = _audit_log_calls(mock_conn)
    enviada = next(a for a in audit_calls if a[5] == "archive.transferencia_enviada")
    assert enviada[3] == str(USER_ID)  # actor -- NUNCA "remitente"
    assert enviada[7] == str(tid)  # object_ref


@pytest.mark.asyncio
async def test_rechazar_audits_success_with_motivo(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        _transfer_row(id=tid, expediente_id=exp_id, estado="rechazada", decidida_por=str(USER_ID)),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        f"/api/v1/transferencias/{tid}/rechazar",
        json={"motivo": "faltan folios"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200

    audit_calls = _audit_log_calls(mock_conn)
    rechazada = next(a for a in audit_calls if a[5] == "archive.transferencia_rechazada")
    assert rechazada[3] == str(USER_ID)
    payload = json.loads(rechazada[8])
    assert payload["motivo"] == "faltan folios"


# ---------------------------------------------------------------------------
# H-A: guarda de estado en `update_estado` (gemela de `transition_expediente`,
# H3/cf83324). `_require` puede validar la transición y aun así perder la
# carrera real contra el `WHERE` -- se simula devolviendo `None` desde el
# UPDATE (perdedor) pese a que `_require` ya la dio por válida.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enviar_pierde_la_carrera_409_y_audita_denegacion(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="preparada"),
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        None,  # update_estado: el WHERE (estado='preparada') ya no casó -- perdedor.
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/transferencias/{tid}/enviar", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "invalid_status_transition"

    audit_calls = _audit_log_calls(mock_conn)
    assert any(a[5] == "archive.transferencia_enviada_failed" for a in audit_calls)
    assert not any(a[5] == "archive.transferencia_enviada" for a in audit_calls)


@pytest.mark.asyncio
async def test_recibir_pierde_la_carrera_contra_rechazar_409(client_with_conn) -> None:
    """Caso letal cerrado por H-A: si un `rechazar()` concurrente ya commiteó
    ('enviada'->'rechazada'), el `WHERE estado='enviada'` de `recibir()` no
    casa -- 409, y el expediente NUNCA se congela."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        _indice_row(),
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        _indice_row(),
        None,  # update_estado: el WHERE (estado='enviada') ya no casó -- perdedor.
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(f"/api/v1/transferencias/{tid}/recibir", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "invalid_status_transition"

    audit_calls = _audit_log_calls(mock_conn)
    assert any(a[5] == "archive.transferencia_recibida_failed" for a in audit_calls)
    # El expediente NUNCA se congeló: ni evento ni asiento de la transición.
    assert not any(a[5] == "archive.expediente_transferred" for a in audit_calls)


# ---------------------------------------------------------------------------
# H-K: motivo de rechazo OBLIGATORIO (frontera HTTP/Pydantic -- 422 antes de
# tocar el service/repository) + NO destructivo (no pisa `observacion`) +
# expuesto en su propia columna. H-J: `rechazada_at` en la respuesta.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rechazar_sin_motivo_422(client_with_conn) -> None:
    """`TransferenciaRechazo.motivo` es obligatorio (H-K): antes de este
    incremento, `TransferenciaDecision.observacion` admitía `None` -- un
    rechazo inmotivado. Cuerpo vacío -> 422, sin tocar el service."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # D-08: permiso concedido
    tid = uuid.uuid4()

    resp = await client.post(
        f"/api/v1/transferencias/{tid}/rechazar",
        json={},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    # Nunca llegó a `_require`/`update_estado`: sin lectura de la transferencia.
    assert mock_conn.fetchrow.call_count == 0


@pytest.mark.asyncio
async def test_rechazar_motivo_en_blanco_422(client_with_conn) -> None:
    """`motivo` de solo espacios lo rechaza el `field_validator` (`_no_blank`),
    no solo `min_length` de Pydantic -- " " tiene longitud 1 pero es un
    motivo vacío en la práctica."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid = uuid.uuid4()

    resp = await client.post(
        f"/api/v1/transferencias/{tid}/rechazar",
        json={"motivo": "   "},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert mock_conn.fetchrow.call_count == 0


@pytest.mark.asyncio
async def test_rechazar_no_pisa_observacion_y_expone_motivo_rechazo(client_with_conn) -> None:
    """H-K/H-J: el motivo del rechazo va a `motivo_rechazo` (columna propia,
    NO `observacion`) y la respuesta expone `rechazada_at`. Se comprueba en
    DOS capas: (1) la SQL que `update_estado` emite NO asigna `observacion`
    -- si `rechazar()` volviera a hacer `payload.observacion or existing[...]`
    (comportamiento pre-H-K), esa columna reaparecería en el `SET`; (2) el
    cuerpo de la respuesta refleja `observacion` de PREPARACIÓN intacta."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _transfer_row(
            id=tid, expediente_id=exp_id, estado="enviada", observacion="prep original"
        ),
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        _transfer_row(
            id=tid,
            expediente_id=exp_id,
            estado="rechazada",
            decidida_por=str(USER_ID),
            observacion="prep original",
            motivo_rechazo="faltan folios 12-15",
            rechazada_at=NOW,
        ),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        f"/api/v1/transferencias/{tid}/rechazar",
        json={"motivo": "faltan folios 12-15"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["observacion"] == "prep original"
    assert body["motivo_rechazo"] == "faltan folios 12-15"
    assert body["rechazada_at"] is not None

    update_call = next(
        c
        for c in mock_conn.fetchrow.call_args_list
        if isinstance(c.args[0], str) and "UPDATE transferencias" in c.args[0]
    )
    sql = update_call.args[0]
    # Solo el tramo `SET ...` importa aquí -- `RETURNING {_COLS}` SIEMPRE
    # menciona `observacion` (es una columna real de la tabla), así que
    # buscar la palabra en la sentencia COMPLETA sería un falso positivo.
    set_clause = sql.split("WHERE", 1)[0]
    assert "observacion = $" not in set_clause
    assert "motivo_rechazo = $" in set_clause
    assert "rechazada_at = $" in set_clause


# ---------------------------------------------------------------------------
# H-G (E12, Ac. AGN 001/2024 Tít. 4.4): `recibir()` re-verifica la fixity del
# acervo (huellas de documentos + re-hash del índice) DENTRO de la tx, antes
# de congelar el expediente -- ya no basta `estado='firmado'`. Este es el
# límite HTTP/service de la garantía; la reversión REAL bajo Postgres (la
# parte que este carril mockeado no puede observar) vive en
# `tests/integration/test_atomicity.py`.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recibir_fixity_mismatch_409_no_congela(client_with_conn) -> None:
    """Un documento del acervo se alteró DESPUÉS de firmar el índice --
    `IndexService.fixity` detecta que `valor_huella` (índice) != `content_hash`
    (radicado actual) y `recibir()` responde 409 `fixity_mismatch` sin llegar
    a `update_estado`/`transition_expediente`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        _indice_row(),  # gate `firmado`, FUERA de la tx
        make_expediente_record(exp_id=exp_id, exp_status="closed"),  # fixity(): get_expediente_by_id
        _indice_row(),  # fixity(): _row_or_404 -> get_current
    ]
    mock_conn.fetch.side_effect = [
        # _get_radicados (dentro de get_expediente_by_id): huella ACTUAL.
        [
            make_radicado_link_record(
                expediente_id=exp_id, radicado_id=doc_id, content_hash="a" * 64
            )
        ],
        # get_items: huella capturada en el índice al firmar -- distinta.
        [{"document_id": doc_id, "valor_huella": "b" * 64}],
    ]

    resp = await client.post(f"/api/v1/transferencias/{tid}/recibir", headers=TENANT_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "fixity_mismatch"

    audit_calls = _audit_log_calls(mock_conn)
    failed = next(a for a in audit_calls if a[5] == "archive.transferencia_recibida_failed")
    payload = json.loads(failed[8])
    assert payload["motivo"] == "fixity_mismatch"
    assert not any(a[5] == "archive.transferencia_recibida" for a in audit_calls)
    assert not any(a[5] == "archive.expediente_transferred" for a in audit_calls)

    audit_calls = _audit_log_calls(mock_conn)
    assert any(a[5] == "archive.transferencia_recibida_failed" for a in audit_calls)
    # El expediente NUNCA se congeló: ni evento ni asiento de la transición.
    assert not any(a[5] == "archive.expediente_transferred" for a in audit_calls)


# ---------------------------------------------------------------------------
# H-I (E12, Ac. AGN 001/2024 Anexo FUID; Ley 594/2000 arts. 15/26): el FUID se
# congela como ACTA DE ENTREGA dentro de la MISMA tx de `recibir()` -- ya NO
# se regenera al vuelo. `GET /{id}/acta` sirve ESE snapshot, con no-read-up
# (mismo criterio de contenido que `fuid()`).
# ---------------------------------------------------------------------------


def _acta_row(**kw) -> dict:
    base = {
        "transferencia_id": uuid.uuid4(),
        "acta_fuid_xml": "<fuid/>",
        "acta_fuid_sha256": "a" * 64,
        "elaborado_por": str(USER_ID),
        "entregado_por": str(USER_ID),
        "recibido_por": str(USER_ID),
        "elaborado_at": NOW,
        "entregado_at": NOW,
        "recibido_at": NOW,
        "indice_version": 0,
        "congelada_at": NOW,
    }
    base.update(kw)
    return base


def _acta_firma_row(**kw) -> dict:
    """Fila de `transferencia_acta_firma` (migración 026, H-H E06/F4)."""
    base = {
        "transferencia_id": uuid.uuid4(),
        "estado": "pendiente_firma",
        "firma_id": None,
        "minio_key_firmado": None,
        "xades_level": None,
        "acreditado": False,
        "last_error": None,
        "reconcile_intentos": 0,
        "reconcile_proximo_at": None,
        "reconcile_agotado": False,
        "created_at": NOW,
        "firmado_at": None,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_recibir_congela_acta_de_entrega(client_with_conn) -> None:
    """H-I: `recibir()` inserta en `transferencia_acta` DENTRO de la tx --
    los tres responsables vienen de las columnas YA fijadas de la fila
    (`creada_por`/`enviada_por`/`decidida_por`, nunca de un valor
    recalculado), `indice_version` es `fx.version` (resuelto por `fixity()`
    en la MISMA tx, sin consulta extra) y `congelada_at` es el `generado_ts`
    del FUID -- el mismo instante embebido en `acta_fuid_xml` sobre el que se
    calculó `acta_fuid_sha256` (ya no `now()` recalculado aparte)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()

    mock_conn.fetchrow.side_effect = [
        _transfer_row(
            id=tid,
            expediente_id=exp_id,
            estado="enviada",
            creada_por="creador-uuid",
            enviada_por="remitente-uuid",
        ),
        _indice_row(),  # gate `firmado`, FUERA de la tx
        make_expediente_record(exp_id=exp_id, exp_status="closed"),  # fixity()
        _indice_row(),  # fixity(): _row_or_404 -> get_current (version=0)
        _transfer_row(
            id=tid,
            expediente_id=exp_id,
            estado="recibida",
            recibida_at=NOW,
            decidida_por=str(USER_ID),
        ),
        make_expediente_record(exp_id=exp_id, exp_status="transferred"),
    ]
    mock_conn.fetch.return_value = []  # _get_radicados / get_items / fuid_inventory

    with _mock_sellar_acta() as mock_sellar:
        resp = await client.post(f"/api/v1/transferencias/{tid}/recibir", headers=TENANT_HEADERS)
    assert resp.status_code == 200

    acta_call = next(
        c
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and c.args[0].startswith("INSERT INTO transferencia_acta (")
    )
    cols = acta_call.args[0].split("(", 2)[1].split(")", 1)[0].split(", ")
    data = dict(zip(cols, acta_call.args[1:], strict=True))

    assert data["transferencia_id"] == tid
    assert data["elaborado_por"] == "creador-uuid"
    assert data["entregado_por"] == "remitente-uuid"
    assert data["recibido_por"] == str(USER_ID)
    assert data["recibido_at"] == NOW
    assert data["indice_version"] == 0
    assert data["acta_fuid_xml"].startswith("<")
    assert len(data["acta_fuid_sha256"]) == 64
    # sha256 real del XML congelado -- no un valor cualquiera.
    assert data["acta_fuid_sha256"] == hashlib.sha256(
        data["acta_fuid_xml"].encode("utf-8")
    ).hexdigest()
    # `congelada_at` == el `generado` embebido en el propio XML.
    assert data["congelada_at"].isoformat() in data["acta_fuid_xml"]
    # Cabecera del Anexo FUID (migración 052): los tres campos con los que el
    # formato abre salen del snapshot tomado AL PREPARAR, y viajan dentro del
    # XML que se congela y se sella. Antes iban los tres en blanco: el sello era
    # exacto sobre un acta que no identificaba la entrega.
    assert (
        "<ns0:objeto>Transferencia de la serie de contratos de la vigencia</ns0:objeto>"
        in data["acta_fuid_xml"]
    )
    assert "Oficina de Contratacion" in data["acta_fuid_xml"]
    assert "Secretaria General" in data["acta_fuid_xml"]

    # H-H (E06/F4): `insert_acta_firma_pendiente` DENTRO de la MISMA tx que
    # `insert_acta` -- toda acta recibida tiene su fila de firma-tracking.
    firma_pendiente_call = next(
        c
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO transferencia_acta_firma" in c.args[0]
    )
    assert firma_pendiente_call.args[1] == tid
    # El sellado síncrono ACOTADO se intenta SIEMPRE post-commit (fuera de la
    # tx) -- best-effort, atribuido al RECEPTOR (X-User-Id de la petición).
    mock_sellar.assert_awaited_once()
    assert mock_sellar.await_args.args == (tid,)
    call_kwargs = mock_sellar.await_args.kwargs
    assert call_kwargs["indice_version"] == 0
    assert call_kwargs["actor"].id == USER_ID


@pytest.mark.asyncio
async def test_transferencia_acta_get_after_recibir(client_with_conn) -> None:
    """`GET /acta` sirve el snapshot YA CONGELADO (`repo.get_acta`), NUNCA lo
    regenera: secuencia `repo.get(transferencia)` -> `caller_clearance` ->
    `get_expediente_by_id` (no-read-up) -> `repo.get_acta` -> `repo.get_acta_firma`
    (H-H, E06/F4 — bloque `firma` aditivo)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,  # caller_clearance: usuario no resoluble -> PUBLICA(1)
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        _acta_firma_row(transferencia_id=tid, estado="pendiente_firma"),
    ]
    mock_conn.fetch.return_value = []  # _get_radicados

    resp = await client.get(f"/api/v1/transferencias/{tid}/acta", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["transferencia_id"] == str(tid)
    assert body["expediente_id"] == str(exp_id)
    assert body["acta_fuid_sha256"] == "a" * 64
    assert body["firma"]["presente"] is False


@pytest.mark.asyncio
async def test_transferencia_acta_aun_no_congelada_404(client_with_conn) -> None:
    """Una transferencia que el llamante SÍ puede leer pero que aún no fue
    `recibida` (o fue `rechazada`, que nunca produce acta) responde 404
    `acta_not_found` -- distinto del 404 de no-read-up."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        None,  # repo.get_acta -> aún no congelada
    ]
    mock_conn.fetch.return_value = []

    resp = await client.get(f"/api/v1/transferencias/{tid}/acta", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "acta_not_found"


@pytest.mark.asyncio
async def test_transferencia_acta_no_read_up_404(client_with_conn) -> None:
    """RF-SEG-08: el acta es CONTENIDO del expediente (asunto/serie/signatura
    embebidos en `acta_fuid_xml`), no un acto de custodia -- un llamante sin
    clearance suficiente sobre un expediente CLASIFICADA(3) recibe 404
    `expediente_not_found`, sin llegar siquiera a `repo.get_acta` (mismo
    criterio que `fuid()`)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid = uuid.uuid4()
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,  # caller_clearance -> PUBLICA(1)
        None,  # get_expediente_by_id: nivel_seguridad real no casa -> None
    ]
    resp = await client.get(f"/api/v1/transferencias/{tid}/acta", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "expediente_not_found"


@pytest.mark.asyncio
async def test_transferencia_acta_transferencia_not_found_before_clearance(
    client_with_conn,
) -> None:
    """El 404 `transferencia_not_found` se resuelve ANTES que el no-read-up:
    una transferencia inexistente nunca llega a resolver clearance."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = None
    resp = await client.get(
        f"/api/v1/transferencias/{uuid.uuid4()}/acta", headers=TENANT_HEADERS
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "transferencia_not_found"


@pytest.mark.asyncio
async def test_transferencia_acta_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get(
        f"/api/v1/transferencias/{uuid.uuid4()}/acta", headers=TENANT_HEADERS
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
