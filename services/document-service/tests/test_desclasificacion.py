# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Desclasificación con segunda persona (C4, ADR-039): orquestación y contrato HTTP.

Con la conexión simulada se prueba QUÉ hace el servicio y en qué orden (asientos, outbox,
mapeo de errores, permisos exigidos); las garantías que viven en SQL —los `WHERE`, los CHECK,
el trigger de frontera, la atomicidad— las prueba, contra Postgres real,
`tests/integration/test_desclasificacion_integration.py`.

Orden de las llamadas simuladas (`fetchval` / `fetchrow`):

  fetchval #1 — check_permission
  fetchrow #1 — resolve_caller_clearance
  [aprobar/rechazar/retirar] fetchval #2 — radicado_de(solicitud)
  fetchrow    — get_security_context_for_update (clearance en el WHERE, FOR UPDATE)
  fetchrow    — la guarda: INSERT / UPDATE ... RETURNING (None = no aplicó)
  [si no aplicó] fetchrow — diagnostico (también con clearance en el WHERE)
  fetchval    — por cada asiento: prev_hash de audit_log
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS

DOC_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
SOL_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
TRACKING = "2026-DEMO-E-000001"
YO = uuid.UUID(TENANT_HEADERS["X-User-Id"])
OTRO = uuid.UUID("99999999-9999-9999-9999-999999999999")
AHORA = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

_RUTA_DOC = f"/api/v1/documents/{DOC_ID}/desclasificacion"
_RUTA_COLA = "/api/v1/desclasificaciones"


def _clearance(n: int) -> dict:
    return {"is_root": False, "max_level": n}


def _ctx(nivel: int = 3, meses: int = 0) -> dict:
    return {
        "clasificacion_norma": None,
        "meses_desde_generacion": meses,
        "meses_hasta_clasificacion": meses,
        "id": DOC_ID,
        "tracking_number": TRACKING,
        "nivel_seguridad": nivel,
        "clasificacion_fundamento": "F",
        "clasificacion_causal": "art18_a_intimidad" if nivel == 3 else None,
        "clasificacion_plazo_meses": None,
    }


def _fila(**over) -> dict:
    base = {
        "id": SOL_ID,
        "radicado_id": DOC_ID,
        "nivel_anterior": 3,
        "nivel_nuevo": 1,
        "motivo": "Cesó la causa de la clasificación",
        "causal_reserva": None,
        "fundamento_juridico": None,
        "fundamento_norma": None,
        "plazo_reserva_meses": None,
        "estado": "pendiente",
        "estado_efectivo": "pendiente",
        "solicitado_por": OTRO,
        "solicitado_at": AHORA,
        "vence_at": AHORA,
        "resuelto_por": None,
        "resuelto_at": None,
        "resolucion_motivo": None,
    }
    return base | over


_PEDIDO = {"nivel_seguridad": 1, "motivo": "Cesó la causa de la clasificación"}


def _acciones(conn) -> list[str]:
    return [
        c.args[5]
        for c in conn.execute.call_args_list
        if "audit_log" in str(c.args[0]) and "INSERT" in str(c.args[0])
    ]


def _outbox(conn) -> list:
    return [c for c in conn.execute.call_args_list if "eventos_salientes" in str(c.args[0])]


def _sql(conn, trozo: str) -> list[str]:
    return [str(c.args[0]) for c in conn.fetchrow.await_args_list if trozo in str(c.args[0])]


# ---------------------------------------------------------------------------
# Solicitar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solicitar_crea_la_solicitud_la_asienta_y_no_emite_evento(client_with_redis) -> None:
    client, conn, redis = client_with_redis
    conn.fetchval.side_effect = [1, None]
    conn.fetchrow.side_effect = [
        _clearance(3),
        _ctx(3),
        None,  # pendiente_de
        _fila(solicitado_por=YO),  # INSERT ... RETURNING
    ]

    resp = await client.post(_RUTA_DOC, json=_PEDIDO, headers=TENANT_HEADERS)

    assert resp.status_code == 201, resp.text
    assert resp.json()["estado"] == "pendiente" and resp.json()["tracking_number"] == TRACKING
    assert _acciones(conn) == ["document.desclasificacion_solicitada"]
    assert not _outbox(conn) and redis.xadd.call_count == 0, "solicitar no mueve el acceso: sin evento"
    assert not [c for c in conn.execute.call_args_list if "UPDATE radicados" in str(c.args[0])]


@pytest.mark.asyncio
async def test_solicitar_desde_nivel_2_es_409_use_el_patch(client_with_redis) -> None:
    client, conn, _ = client_with_redis
    conn.fetchval.side_effect = [1]
    conn.fetchrow.side_effect = [_clearance(3), _ctx(2)]

    resp = await client.post(_RUTA_DOC, json=_PEDIDO, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "no_requiere_aprobacion"
    assert "security-level" in resp.json()["detail"]["detail"]


@pytest.mark.asyncio
async def test_solicitar_con_otra_pendiente_es_409(client_with_redis) -> None:
    client, conn, _ = client_with_redis
    conn.fetchval.side_effect = [1]
    conn.fetchrow.side_effect = [_clearance(3), _ctx(3), _fila()]

    resp = await client.post(_RUTA_DOC, json=_PEDIDO, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "desclasificacion_pendiente"
    assert not _acciones(conn)


@pytest.mark.asyncio
async def test_solicitar_sin_alcanzar_el_radicado_es_el_404_neutro(client_with_redis) -> None:
    client, conn, _ = client_with_redis
    conn.fetchval.side_effect = [1]
    conn.fetchrow.side_effect = [_clearance(2), None]

    resp = await client.post(_RUTA_DOC, json=_PEDIDO, headers=TENANT_HEADERS)

    assert resp.status_code == 404
    consulta = conn.fetchrow.await_args_list[1]
    assert "nivel_seguridad <= $2" in consulta.args[0] and "FOR UPDATE" in consulta.args[0]


@pytest.mark.asyncio
async def test_solicitar_exige_crud_3(client_with_redis) -> None:
    client, conn, _ = client_with_redis
    conn.fetchval.return_value = None
    await client.post(_RUTA_DOC, json=_PEDIDO, headers=TENANT_HEADERS)
    primera = conn.fetchval.await_args_list[0]
    assert "PERM_RECLASIFICAR" in primera.args and 3 in primera.args


# ---------------------------------------------------------------------------
# Aprobar — aprobar EJECUTA
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aprobar_baja_el_nivel_asienta_dos_veces_y_encola_un_evento(client_with_redis) -> None:
    client, conn, redis = client_with_redis
    conn.fetchval.side_effect = [1, DOC_ID, None, None]
    conn.fetchrow.side_effect = [
        _clearance(3),
        _ctx(3),
        _fila(estado="aprobada", estado_efectivo="aprobada", resuelto_por=YO, resuelto_at=AHORA),
    ]

    resp = await client.post(f"{_RUTA_COLA}/{SOL_ID}/aprobar", headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    assert resp.json()["nivel_seguridad"] == 1
    # La guarda es el WHERE del UPDATE (SoD, vigencia, nivel, clearance): mutaciones 1, 3 y 4.
    guarda = _sql(conn, "UPDATE reclasificacion_solicitudes")[0]
    for trozo in (
        "s.solicitado_por <> $2",
        "s.vence_at > now()",
        "s.estado = 'pendiente'",
        "r.nivel_seguridad = s.nivel_anterior",
        "r.nivel_seguridad <= $3",
    ):
        assert trozo in guarda, f"falta en la guarda de aprobar: {trozo}"
    assert _acciones(conn) == ["document.desclasificacion_aprobada", "document.radicado_reclassified"]
    assert len(_outbox(conn)) >= 1 and redis.xadd.call_count == 1
    bajada = [c for c in conn.execute.call_args_list if "UPDATE radicados" in str(c.args[0])]
    assert len(bajada) == 1 and "AND nivel_seguridad = $3" in str(bajada[0].args[0])


@pytest.mark.asyncio
async def test_aprobar_la_propia_es_409_separacion_de_funciones(client_with_redis) -> None:
    client, conn, redis = client_with_redis
    conn.fetchval.side_effect = [1, DOC_ID]
    conn.fetchrow.side_effect = [
        _clearance(3),
        _ctx(3),
        None,  # el UPDATE no aplicó
        _fila(solicitado_por=YO, nivel_actual=3, vencida=False),  # diagnóstico
    ]

    resp = await client.post(f"{_RUTA_COLA}/{SOL_ID}/aprobar", headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "separacion_de_funciones"
    assert not _acciones(conn) and not _outbox(conn) and redis.xadd.call_count == 0
    assert not [c for c in conn.execute.call_args_list if "UPDATE radicados" in str(c.args[0])]
    # El diagnóstico también lleva el clearance en el WHERE.
    assert "r.nivel_seguridad <= $2" in _sql(conn, "AS nivel_actual")[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("diag", "codigo"),
    [
        (dict(estado="pendiente", vencida=True), "solicitud_caducada"),
        (dict(estado="aprobada", vencida=False), "solicitud_ya_resuelta"),
        (dict(estado="rechazada", vencida=False), "solicitud_ya_resuelta"),
        (dict(estado="pendiente", vencida=False), "nivel_cambio"),
    ],
)
async def test_aprobar_sin_efecto_explica_por_que(client_with_redis, diag, codigo) -> None:
    client, conn, _ = client_with_redis
    conn.fetchval.side_effect = [1, DOC_ID]
    conn.fetchrow.side_effect = [
        _clearance(3), _ctx(3), None, _fila(nivel_actual=3, **diag),
    ]

    resp = await client.post(f"{_RUTA_COLA}/{SOL_ID}/aprobar", headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == codigo
    assert not _outbox(conn)


@pytest.mark.asyncio
async def test_aprobar_con_clearance_2_es_404_y_no_toca_la_solicitud(client_with_redis) -> None:
    client, conn, _ = client_with_redis
    conn.fetchval.side_effect = [1, DOC_ID]
    conn.fetchrow.side_effect = [_clearance(2), None]  # el radicado (nivel 3) no sale

    resp = await client.post(f"{_RUTA_COLA}/{SOL_ID}/aprobar", headers=TENANT_HEADERS)

    assert resp.status_code == 404 and resp.json()["detail"]["error"] == "solicitud_not_found"
    assert not _sql(conn, "UPDATE reclasificacion_solicitudes")


@pytest.mark.asyncio
async def test_aprobar_solicitud_inexistente_es_el_mismo_404(client_with_redis) -> None:
    client, conn, _ = client_with_redis
    conn.fetchval.side_effect = [1, None]
    conn.fetchrow.side_effect = [_clearance(3)]

    resp = await client.post(f"{_RUTA_COLA}/{SOL_ID}/aprobar", headers=TENANT_HEADERS)

    assert resp.status_code == 404 and resp.json()["detail"]["error"] == "solicitud_not_found"


@pytest.mark.asyncio
async def test_si_la_bajada_no_aplica_se_revierte_todo(client_with_redis) -> None:
    client, conn, redis = client_with_redis
    conn.fetchval.side_effect = [1, DOC_ID]
    conn.fetchrow.side_effect = [
        _clearance(3), _ctx(3),
        _fila(estado="aprobada", resuelto_por=YO, resuelto_at=AHORA),
    ]
    conn.execute.return_value = "UPDATE 0"  # el nivel esperado ya no es el de la fila

    resp = await client.post(f"{_RUTA_COLA}/{SOL_ID}/aprobar", headers=TENANT_HEADERS)

    assert resp.status_code == 409 and resp.json()["detail"]["error"] == "nivel_cambio"
    assert not _acciones(conn) and not _outbox(conn) and redis.xadd.call_count == 0


@pytest.mark.asyncio
async def test_aprobar_y_la_cola_exigen_crud_4(client_with_redis) -> None:
    client, conn, _ = client_with_redis
    conn.fetchval.return_value = None
    for metodo, ruta in (("post", f"{_RUTA_COLA}/{SOL_ID}/aprobar"),
                         ("post", f"{_RUTA_COLA}/{SOL_ID}/rechazar"),
                         ("get", _RUTA_COLA)):
        conn.fetchval.reset_mock()
        conn.fetchval.return_value = None
        kw = {"json": {"resolucion_motivo": "no procede la desclasificación"}} if "rechazar" in ruta else {}
        await getattr(client, metodo)(ruta, headers=TENANT_HEADERS, **kw)
        primera = conn.fetchval.await_args_list[0]
        assert "PERM_RECLASIFICAR" in primera.args and 4 in primera.args, ruta


# ---------------------------------------------------------------------------
# Rechazar / retirar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rechazar_asienta_y_no_emite_evento(client_with_redis) -> None:
    client, conn, redis = client_with_redis
    conn.fetchval.side_effect = [1, DOC_ID, None]
    conn.fetchrow.side_effect = [
        _clearance(3), _ctx(3),
        _fila(estado="rechazada", estado_efectivo="rechazada", resuelto_por=YO, resuelto_at=AHORA,
              resolucion_motivo="Sigue vigente la causa"),
    ]

    resp = await client.post(
        f"{_RUTA_COLA}/{SOL_ID}/rechazar",
        json={"resolucion_motivo": "Sigue vigente la causa"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200 and resp.json()["estado"] == "rechazada"
    assert _acciones(conn) == ["document.desclasificacion_rechazada"]
    assert not _outbox(conn) and redis.xadd.call_count == 0
    guarda = _sql(conn, "UPDATE reclasificacion_solicitudes")[0]
    assert "s.solicitado_por <> $2" in guarda and "s.vence_at > now()" in guarda


@pytest.mark.asyncio
async def test_rechazar_pide_motivo_de_10_caracteres(client_with_redis) -> None:
    client, _, _ = client_with_redis
    resp = await client.post(
        f"{_RUTA_COLA}/{SOL_ID}/rechazar", json={"resolucion_motivo": "no"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_retirar_es_solo_del_solicitante(client_with_redis) -> None:
    client, conn, redis = client_with_redis
    conn.fetchval.side_effect = [1, DOC_ID]
    conn.fetchrow.side_effect = [
        _clearance(3), _ctx(3), None,
        _fila(solicitado_por=OTRO, nivel_actual=3, vencida=False),
    ]

    resp = await client.post(f"{_RUTA_COLA}/{SOL_ID}/retirar", headers=TENANT_HEADERS)

    assert resp.status_code == 409 and resp.json()["detail"]["error"] == "solo_el_solicitante"
    assert "s.solicitado_por = $2" in _sql(conn, "UPDATE reclasificacion_solicitudes")[0]
    assert not _outbox(conn)


@pytest.mark.asyncio
async def test_retirar_la_propia_asienta_sin_evento(client_with_redis) -> None:
    client, conn, redis = client_with_redis
    conn.fetchval.side_effect = [1, DOC_ID, None]
    conn.fetchrow.side_effect = [
        _clearance(3), _ctx(3),
        _fila(solicitado_por=YO, estado="retirada", estado_efectivo="retirada",
              resuelto_por=YO, resuelto_at=AHORA),
    ]

    resp = await client.post(f"{_RUTA_COLA}/{SOL_ID}/retirar", headers=TENANT_HEADERS)

    assert resp.status_code == 200 and resp.json()["estado"] == "retirada"
    assert _acciones(conn) == ["document.desclasificacion_retirada"]
    assert not _outbox(conn) and redis.xadd.call_count == 0


# ---------------------------------------------------------------------------
# Cola e historial
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cola_filas_y_total_comparten_la_condicion_y_el_clearance(client_with_redis) -> None:
    """Mutación 10 del diseño: contar con otra condición delata solicitudes ajenas."""
    client, conn, _ = client_with_redis
    conn.fetchval.side_effect = [1, 0]
    conn.fetchrow.side_effect = [_clearance(2)]
    conn.fetch.return_value = []

    resp = await client.get(_RUTA_COLA, headers=TENANT_HEADERS)

    assert resp.status_code == 200 and resp.json() == []
    assert resp.headers["X-Total-Count"] == "0"
    from app.repositories.reclasificacion_repository import ReclasificacionRepository as R

    filas = str(conn.fetch.await_args_list[0].args[0])
    total = str(conn.fetchval.await_args_list[1].args[0])
    assert R._VISIBLE_LISTA in filas and R._VISIBLE_LISTA in total
    assert conn.fetch.await_args_list[0].args[1] == 2, "el clearance del llamante viaja a la consulta"
    assert conn.fetchval.await_args_list[1].args[1] == 2


@pytest.mark.asyncio
async def test_cola_marca_las_propias(client_with_redis) -> None:
    client, conn, _ = client_with_redis
    conn.fetchval.side_effect = [1, 1]
    conn.fetchrow.side_effect = [_clearance(3)]
    conn.fetch.return_value = [
        _fila(solicitado_por=YO) | {"tracking_number": TRACKING, "subject": "Asunto", "doc_type": "E", "nivel_seguridad": 3, "propia": True}
    ]

    resp = await client.get(_RUTA_COLA, headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert resp.json()[0]["propia"] is True and resp.json()[0]["subject"] == "Asunto"
    assert resp.headers["X-Total-Count"] == "1"


@pytest.mark.asyncio
async def test_historial_lleva_el_clearance_en_el_where(client_with_redis) -> None:
    client, conn, _ = client_with_redis
    conn.fetchval.side_effect = [1]
    conn.fetchrow.side_effect = [_clearance(1)]
    conn.fetch.return_value = []

    resp = await client.get(_RUTA_DOC, headers=TENANT_HEADERS)

    assert resp.status_code == 200 and resp.json() == []
    assert "r.nivel_seguridad <= $2" in str(conn.fetch.await_args_list[0].args[0])
    assert conn.fetch.await_args_list[0].args[2] == 1


# ---------------------------------------------------------------------------
# Esquema
# ---------------------------------------------------------------------------


def test_el_pedido_a_nivel_2_exige_causal_art19_fundamento_y_plazo() -> None:
    from pydantic import ValidationError

    from app.schemas.desclasificacion import DesclasificacionRequest as D

    base = {"motivo": "Cesó la causa de la clasificación"}
    assert D(nivel_seguridad=1, **base).nivel_seguridad == 1
    ok = D(nivel_seguridad=2, causal_reserva="art19_i_salud_publica", plazo_reserva_meses=24,
           fundamento_juridico="Art. 19 lit. i", **base)
    assert ok.plazo_reserva_meses == 24
    for malo in (
        dict(nivel_seguridad=2),
        dict(nivel_seguridad=2, causal_reserva="art18_a_intimidad", fundamento_juridico="F"),
        dict(nivel_seguridad=2, causal_reserva="art19_i_salud_publica", fundamento_juridico="F"),
        dict(nivel_seguridad=2, causal_reserva="art19_i_salud_publica", plazo_reserva_meses=24),
        dict(nivel_seguridad=3),
        dict(nivel_seguridad=1, causal_reserva="art18_a_intimidad"),
    ):
        with pytest.raises(ValidationError):
            D(**base, **malo)
    with pytest.raises(ValidationError):
        D(nivel_seguridad=1, motivo="corto")


def test_el_json_de_auditoria_de_aprobar_no_pierde_a_quien_pidio() -> None:
    from app.services.desclasificacion_service import DesclasificacionService as S

    p = S._payload(_fila())
    json.dumps(p)
    assert p["solicitado_por"] == str(OTRO) and p["nivel_anterior"] == 3


@pytest.mark.asyncio
async def test_solicitar_con_destino_reservado_respeta_el_tope_desde_la_generacion(
    client_with_redis,
) -> None:
    """3 -> 2 también mide el plazo contra la GENERACIÓN: 2→3→2 no reinicia el reloj."""
    client, conn, _ = client_with_redis
    conn.fetchval.side_effect = [1]
    conn.fetchrow.side_effect = [_clearance(3), _ctx(3, meses=170)]

    resp = await client.post(
        _RUTA_DOC,
        json={
            "nivel_seguridad": 2,
            "motivo": "Cesó la causa de la clasificación",
            "causal_reserva": "art19_b_seguridad_publica",
            "fundamento_juridico": "Decreto 1081 de 2015",
            "plazo_reserva_meses": 24,
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "plazo_excede_tope_legal"
    assert not [c for c in conn.execute.call_args_list if "INSERT INTO reclasificacion" in str(c.args[0])]
