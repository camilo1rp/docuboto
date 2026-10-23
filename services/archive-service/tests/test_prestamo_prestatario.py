# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-5 / C11: el préstamo de una unidad identifica a quien la recibe (unitarias, con mocks).

Lo que NO prueba este tier: la atomicidad ni la serialización (Postgres real, ver
`tests/integration/test_prestamo_prestatario_sql.py`). Aquí se fija el CONTRATO:
la forma del cuerpo, quién es `prestado_por`, el orden en que se explican los
rechazos, el enmascarado del documento en el asiento y que las reglas viven EN el
`INSERT ... SELECT` y no en una comprobación previa.

Mutaciones que deben ponerse en rojo (descritas, no ejecutadas):
- aceptar `prestado_por` en `PrestamoCreate` (quitar `extra="forbid"`) -> `test_prestado_por_no_viene_del_cuerpo`;
- pasar `payload`-algo en vez de `actor` como `prestado_por` -> `test_el_servidor_fija_quien_entrega`;
- volcar el documento completo al asiento -> `test_el_asiento_enmascara_el_documento_del_externo`;
- invertir el orden de `_rechazar_prestamo` (prestatario antes que quien presta) -> `test_quien_no_alcanza_el_contenido_no_aprende_del_prestatario`;
- sacar el `NOT EXISTS`/`unidad_con_prestamo_en_arbol` del SQL del INSERT -> `test_las_reglas_viven_en_el_where_del_insert`;
- tomar `unidad_lock_arbol` DESPUES del INSERT -> `test_el_bloqueo_de_arbol_precede_al_insert`.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.fisico import PrestamoCreate
from app.services.fisico_service import FisicoService
from tests.conftest import TENANT_HEADERS, TENANT_SLUG, USER_ID
from tests.test_fisico import _audit_log_calls, _diag, _prestamo_row, _unidad_row

EXTERNO = {
    "tipo": "externo",
    "nombre": "Ana Jurídica",
    "documento_tipo": "CC",
    "documento_numero": "1234567890",
}
FUTURO = (datetime.now(tz=UTC) + timedelta(days=30)).date().isoformat()


def _cuerpo(prestatario: dict | None = None, **extra) -> dict:
    return {"prestatario": prestatario or EXTERNO, "fecha_devolucion_esperada": FUTURO, **extra}


def _insert_call(mock_conn):
    llamadas = [
        c for c in mock_conn.fetchrow.call_args_list if "INSERT INTO prestamo" in str(c.args[0])
    ]
    assert len(llamadas) == 1, "debe haber exactamente un INSERT INTO prestamo"
    return llamadas[0]


# -- forma del cuerpo ----------------------------------------------------


def test_prestado_por_no_viene_del_cuerpo() -> None:
    """`extra="forbid"`: quien entrega lo resuelve el servidor. Control positivo: el
    mismo cuerpo sin ese campo es válido."""
    PrestamoCreate(**_cuerpo())
    with pytest.raises(ValidationError):
        PrestamoCreate(**_cuerpo(prestado_por="otra-persona"))


def test_el_externo_exige_nombre_y_documento() -> None:
    with pytest.raises(ValidationError):
        PrestamoCreate(**_cuerpo({"tipo": "externo", "nombre": "Ana"}))  # sin documento
    with pytest.raises(ValidationError):
        PrestamoCreate(**_cuerpo({**EXTERNO, "nombre": "  A "}))  # 3 caracteres NO blancos
    with pytest.raises(ValidationError):
        PrestamoCreate(**_cuerpo({**EXTERNO, "documento_tipo": "LIBRETA"}))  # fuera del catálogo
    with pytest.raises(ValidationError):
        PrestamoCreate(**_cuerpo({**EXTERNO, "mascota": "perro"}))  # extra="forbid" también adentro


def test_el_interno_es_solo_un_user_id() -> None:
    PrestamoCreate(**_cuerpo({"tipo": "interno", "user_id": str(uuid.uuid4())}))
    with pytest.raises(ValidationError):
        PrestamoCreate(**_cuerpo({"tipo": "interno"}))
    with pytest.raises(ValidationError):  # un interno no trae datos de tercero
        PrestamoCreate(**_cuerpo({"tipo": "interno", "user_id": str(uuid.uuid4()), "nombre": "X"}))
    with pytest.raises(ValidationError):  # y el discriminador es obligatorio
        PrestamoCreate(**_cuerpo({"user_id": str(uuid.uuid4())}))


def test_la_devolucion_esperada_no_puede_estar_en_el_pasado() -> None:
    hoy = datetime.now(tz=UTC).date()
    PrestamoCreate(prestatario=EXTERNO, fecha_devolucion_esperada=hoy)  # hoy vale
    with pytest.raises(ValidationError):
        PrestamoCreate(prestatario=EXTERNO, fecha_devolucion_esperada=hoy - timedelta(days=1))


# -- endpoint: éxito -----------------------------------------------------


@pytest.mark.asyncio
async def test_el_servidor_fija_quien_entrega(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    unidad_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _unidad_row(id=unidad_id),
        None,  # resolve_caller_clearance
        _prestamo_row(unidad_id=unidad_id),
    ]
    resp = await client.post(
        f"/api/v1/unidades/{unidad_id}/prestamos", json=_cuerpo(), headers=TENANT_HEADERS
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["prestatario_tipo"] == "externo"
    assert body["prestado_por"] == str(USER_ID)
    assert "prestado_a" not in body  # el campo viejo ya no existe

    args = _insert_call(mock_conn).args
    # (sql, unidad, tipo, user_id, nombre, doc_tipo, doc_num, entidad, cargo, correo, tel,
    #  prestado_por, fecha, observaciones, max_clearance)
    assert args[2] == "externo"
    assert args[3] is None
    assert args[11] == USER_ID, "prestado_por es el X-User-Id, no algo del cuerpo"


@pytest.mark.asyncio
async def test_prestatario_interno_viaja_como_user_id(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    unidad_id, user = uuid.uuid4(), uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _unidad_row(id=unidad_id),
        None,
        _prestamo_row(
            unidad_id=unidad_id,
            prestatario_tipo="interno",
            prestatario_user_id=user,
            prestatario_nombre="mgarcia",
            tercero_nombre=None,
            tercero_documento_tipo=None,
            tercero_documento_numero=None,
        ),
    ]
    resp = await client.post(
        f"/api/v1/unidades/{unidad_id}/prestamos",
        json=_cuerpo({"tipo": "interno", "user_id": str(user)}),
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["prestatario_user_id"] == str(user)
    assert resp.json()["prestatario_nombre"] == "mgarcia"
    args = _insert_call(mock_conn).args
    assert args[2] == "interno" and args[3] == user
    # el interno no arrastra datos de tercero
    assert args[4:11] == (None,) * 7
    payload = json.loads(_audit_log_calls(mock_conn)[0][8])
    assert payload["prestatario_user_id"] == str(user)


@pytest.mark.asyncio
async def test_el_asiento_enmascara_el_documento_del_externo(client_with_conn) -> None:
    """La fila guarda el documento completo (finalidad de custodia, Ley 1581/2012); el
    asiento inmutable NO: un dato personal no se puede borrar de un registro append-only."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    unidad_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _unidad_row(id=unidad_id),
        None,
        _prestamo_row(unidad_id=unidad_id),
    ]
    resp = await client.post(
        f"/api/v1/unidades/{unidad_id}/prestamos", json=_cuerpo(), headers=TENANT_HEADERS
    )
    assert resp.status_code == 201
    asiento = _audit_log_calls(mock_conn)[0]
    assert "1234567890" not in asiento[8]
    assert json.loads(asiento[8])["documento_numero"] == "****7890"
    # control: la fila SÍ recibió el documento completo
    assert _insert_call(mock_conn).args[6] == "1234567890"


# -- el INSERT lleva las reglas -----------------------------------------


@pytest.mark.asyncio
async def test_las_reglas_viven_en_el_where_del_insert(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    unidad_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _unidad_row(id=unidad_id),
        None,
        _prestamo_row(unidad_id=unidad_id),
    ]
    await client.post(
        f"/api/v1/unidades/{unidad_id}/prestamos", json=_cuerpo(), headers=TENANT_HEADERS
    )
    sql = _insert_call(mock_conn).args[0]
    # no-read-up de quien presta Y de quien recibe, árbol, y "lo que está saliendo"
    assert sql.count("e.nivel_seguridad >") >= 2
    assert "unidad_con_prestamo_en_arbol($1::uuid) IS NULL" in sql
    assert "FROM transferencias" in sql and "('preparada','enviada')" in sql
    assert "eliminacion_lote_items" in sql and "('borrador','publicado','avalado')" in sql
    assert "auth_users" in sql and "is_active" in sql
    # y el contenido se mide sobre el ÁRBOL, no solo sobre la unidad
    assert "unidad_arbol($1::uuid)" in sql
    # no queda ningún pre-check separado de "ya prestada"
    assert not [
        c for c in mock_conn.fetchrow.call_args_list if "FROM prestamo WHERE" in str(c.args[0])
    ]


@pytest.mark.asyncio
async def test_el_bloqueo_de_arbol_precede_al_insert(client_with_conn) -> None:
    """El `NOT EXISTS` del INSERT usa la instantánea de SU sentencia: el bloqueo por
    raíz tiene que estar tomado en una sentencia ANTERIOR."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    unidad_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _unidad_row(id=unidad_id),
        None,
        _prestamo_row(unidad_id=unidad_id),
    ]
    await client.post(
        f"/api/v1/unidades/{unidad_id}/prestamos", json=_cuerpo(), headers=TENANT_HEADERS
    )
    orden = [
        ("lock" if "unidad_lock_arbol" in str(c.args[0]) else "insert")
        for c in mock_conn.mock_calls
        if c[0] in ("execute", "fetchrow")
        and c.args
        and ("unidad_lock_arbol" in str(c.args[0]) or "INSERT INTO prestamo" in str(c.args[0]))
    ]
    assert orden == ["lock", "insert"]


# -- rechazos: qué se dice y en qué orden --------------------------------


async def _rechazado(client_with_conn, diag: dict, prestatario: dict | None = None):
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    unidad_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [_unidad_row(id=unidad_id), None, None, diag]
    resp = await client.post(
        f"/api/v1/unidades/{unidad_id}/prestamos",
        json=_cuerpo(prestatario),
        headers=TENANT_HEADERS,
    )
    return resp, mock_conn


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("diag", "status", "error"),
    [
        ({"presta_sin_acceso": True}, 403, "clearance_insuficiente"),
        ({"prestatario_inexistente": True}, 422, "prestatario_inexistente"),
        ({"prestatario_sin_acceso": True}, 403, "prestatario_sin_acceso"),
        ({"ya_prestada": True}, 409, "unidad_ya_prestada"),
        ({"en_transferencia": True}, 409, "unidad_en_transferencia"),
        ({"en_lote": True}, 409, "unidad_en_lote_eliminacion"),
    ],
)
async def test_cada_rechazo_tiene_su_codigo(client_with_conn, diag, status, error) -> None:
    resp, mock_conn = await _rechazado(client_with_conn, _diag(**diag))
    assert resp.status_code == status
    assert resp.json()["detail"]["error"] == error
    # rechazado = nada escrito: ni movimiento RETIRADO ni asiento
    assert _audit_log_calls(mock_conn) == []
    assert not [c for c in mock_conn.execute.call_args_list if "movimiento_fisico" in str(c.args[0])]


@pytest.mark.asyncio
async def test_quien_no_alcanza_el_contenido_no_aprende_del_prestatario(client_with_conn) -> None:
    """Todo a la vez: manda lo de QUIEN PRESTA. Si no, el 422 de un prestatario
    inexistente o el 409 de un lote serían un oráculo sobre una unidad que no puede ver."""
    resp, _ = await _rechazado(
        client_with_conn,
        _diag(
            presta_sin_acceso=True,
            prestatario_inexistente=True,
            prestatario_sin_acceso=True,
            ya_prestada=True,
            en_transferencia=True,
            en_lote=True,
        ),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "clearance_insuficiente"


@pytest.mark.asyncio
async def test_un_sub_de_keycloak_es_prestatario_inexistente(client_with_conn) -> None:
    """El `sub` de Keycloak también es un UUID: la forma no lo distingue, la existencia
    en `auth_users` sí. El diagnóstico lo reporta y la respuesta enseña el camino."""
    resp, _ = await _rechazado(
        client_with_conn,
        _diag(prestatario_inexistente=True),
        {"tipo": "interno", "user_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 422
    assert "prestatarios-elegibles" in resp.json()["detail"]["detail"]


@pytest.mark.asyncio
async def test_el_externo_con_contenido_no_publico_dice_por_que(client_with_conn) -> None:
    resp, _ = await _rechazado(client_with_conn, _diag(prestatario_sin_acceso=True))
    assert resp.status_code == 403
    assert "público" in resp.json()["detail"]["detail"]


@pytest.mark.asyncio
async def test_la_carrera_que_gana_el_motor_tambien_es_409(client_with_conn) -> None:
    """Defensa en profundidad: si el índice parcial o el trigger de árbol saltan
    (23505), la respuesta es el mismo `unidad_ya_prestada`, no un 500."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    unidad_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _unidad_row(id=unidad_id),
        None,
        asyncpg.UniqueViolationError("duplicate key"),
    ]
    resp = await client.post(
        f"/api/v1/unidades/{unidad_id}/prestamos", json=_cuerpo(), headers=TENANT_HEADERS
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "unidad_ya_prestada"


@pytest.mark.asyncio
async def test_sin_quien_entrega_no_hay_custodia() -> None:
    mock_conn = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=AsyncMock())
    mock_conn.fetchrow.side_effect = [_unidad_row()]
    payload = PrestamoCreate(**_cuerpo())
    with pytest.raises(HTTPException) as exc:
        await FisicoService(mock_conn, TENANT_SLUG).create_prestamo(
            uuid.uuid4(), payload, None, actor=None
        )
    assert exc.value.status_code == 403


# -- devolución ----------------------------------------------------------


@pytest.mark.asyncio
async def test_la_devolucion_fija_quien_recibe(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    pid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _prestamo_row(id=pid),
        _prestamo_row(
            id=pid, estado="devuelto", fecha_devolucion_real=datetime.now(tz=UTC),
            devuelto_recibido_por=USER_ID,
        ),
    ]
    resp = await client.post(f"/api/v1/prestamos/{pid}/devolver", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["devuelto_recibido_por"] == str(USER_ID)
    update = [c for c in mock_conn.fetchrow.call_args_list if "UPDATE prestamo" in str(c.args[0])]
    assert update and update[0].args[2] == USER_ID


# -- vincular a una unidad prestada -------------------------------------


@pytest.mark.asyncio
async def test_no_se_vincula_a_una_unidad_prestada(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    unidad_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [_unidad_row(id=unidad_id), None]  # 0 filas: está fuera
    resp = await client.post(
        f"/api/v1/unidades/{unidad_id}/expedientes",
        json={"expediente_id": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "unidad_prestada"
    assert _audit_log_calls(mock_conn) == []
    sql = [c for c in mock_conn.fetchrow.call_args_list if "INSERT INTO expediente_unidad" in str(c.args[0])]
    assert "unidad_con_prestamo_en_arbol" in sql[0].args[0]


# -- prestatarios elegibles ---------------------------------------------


@pytest.mark.asyncio
async def test_prestatarios_elegibles_pagina_y_no_expone_el_nivel(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    unidad_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [_unidad_row(id=unidad_id), None]
    mock_conn.fetch.return_value = [
        {"user_id": uuid.uuid4(), "username": "agarcia", "total": 2},
        {"user_id": uuid.uuid4(), "username": "mlopez", "total": 2},
    ]
    resp = await client.get(
        f"/api/v1/unidades/{unidad_id}/prestatarios-elegibles?q=a", headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "2"
    assert [u["username"] for u in resp.json()] == ["agarcia", "mlopez"]
    assert all(set(u) == {"user_id", "username", "nombre"} for u in resp.json())  # sin nivel


@pytest.mark.asyncio
async def test_prestatarios_elegibles_de_una_unidad_inexistente_es_404(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = None
    resp = await client.get(
        f"/api/v1/unidades/{uuid.uuid4()}/prestatarios-elegibles", headers=TENANT_HEADERS
    )
    assert resp.status_code == 404


# -- admisión 7 del lote de eliminación ---------------------------------


@pytest.mark.asyncio
async def test_un_expediente_en_unidad_prestada_no_entra_al_lote(client_with_conn) -> None:
    """Condición 7: no se propone destruir lo que físicamente no está. Se informa
    (motivo + unidad), no se descarta en silencio. Control: el otro candidato, sin
    unidad prestada, SÍ entra."""
    from tests.test_eliminacion_lote import _CLEARANCE, RUTA, _candidato, _lote_row

    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    fuera, dentro = _candidato(code="EXP-2020-0001"), _candidato(code="EXP-2020-0002")
    mock_conn.fetchrow.side_effect = [_CLEARANCE, _lote_row()]
    mock_conn.fetch.side_effect = [
        [fuera, dentro],
        [{"expediente_id": fuera["id"], "codigo": "C0124"}],  # lectura previa
        [],  # re-comprobación bajo bloqueo: nada nuevo
        [],  # C-5: constancias que citan un anexo sin huella (ninguna)
        [],  # C-5: congelar_soportes_citados (sin constancias citadas)
    ]
    resp = await client.post(
        RUTA,
        json={"codigo": "LOTE-C11", "expediente_ids": [str(fuera["id"]), str(dentro["id"])]},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code in (200, 201)
    cuerpo = resp.json()
    assert [r["motivo"] for r in cuerpo["rechazados"]] == ["unidad_prestada"]
    assert "C0124" in cuerpo["rechazados"][0]["detalle"]
    assert [i["expediente_code"] for i in cuerpo["items"]] == ["EXP-2020-0002"]


@pytest.mark.asyncio
async def test_un_prestamo_que_llega_durante_la_propuesta_la_tumba(client_with_conn) -> None:
    from tests.test_eliminacion_lote import _CLEARANCE, RUTA, _candidato

    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    cand = _candidato()
    mock_conn.fetchrow.side_effect = [_CLEARANCE]
    mock_conn.fetch.side_effect = [
        [cand],
        [],  # al leer, nada prestado
        [{"expediente_id": cand["id"], "codigo": "C0777"}],  # bajo bloqueo, ya sí
    ]
    resp = await client.post(
        RUTA,
        json={"codigo": "LOTE-C11B", "expediente_ids": [str(cand["id"])]},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "unidad_prestada"
    assert "C0777" in resp.json()["detail"]["detail"]


# -- transferencia: servicio y migración --------------------------------


@pytest.mark.asyncio
async def test_transferir_un_expediente_con_unidad_prestada_es_409() -> None:
    from app.services.transferencia_service import TransferenciaService

    conn = AsyncMock()
    svc = TransferenciaService(conn, TENANT_SLUG)
    conn.fetchval.return_value = "C0124"
    with pytest.raises(HTTPException) as exc:
        await svc._rechazar_si_unidad_prestada(uuid.uuid4())
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "unidad_prestada"
    assert "C0124" in exc.value.detail["detail"]
    # control positivo: sin unidad prestada, pasa
    conn.fetchval.return_value = None
    await svc._rechazar_si_unidad_prestada(uuid.uuid4())


def test_la_migracion_072_declara_las_garantias_del_motor() -> None:
    """Guardián de texto: cada garantía de C11 que NO es del servicio tiene que seguir
    escrita en el DDL. Quitar cualquiera de estas líneas deja pasar un caso que el
    servicio por sí solo no puede cerrar (otro cliente, un UPDATE a mano)."""
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1] / "migrations/tenant/072_prestamo_prestatario.sql"
    ).read_text()
    for pieza in (
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_prestamo_unidad_activo",
        "ON prestamo (unidad_id) WHERE estado = 'prestado'",
        "ON DELETE RESTRICT",  # la traza no la borra el borrado de la unidad
        "ck_prestamo_prestatario_forma",
        "ck_prestamo_devolucion_coherente",
        "BEFORE UPDATE OR DELETE ON prestamo",
        "BEFORE TRUNCATE ON prestamo",
        "BEFORE INSERT OR UPDATE OF estado ON transferencias",
        "BEFORE DELETE ON expedientes",
        "pg_advisory_xact_lock",
    ):
        assert pieza in sql, pieza
    assert "ON DELETE CASCADE" not in sql.split("prestamo_unidad_id_fkey")[1].split(";")[0]


# ---------------------------------------------------------------------------
# Datos personales del prestatario externo: enmascarados salvo crud >= 3 (cierre del lote C, S3)
# ---------------------------------------------------------------------------


def _externo():
    from datetime import UTC, datetime

    from app.schemas.fisico import PrestamoResponse

    return PrestamoResponse(
        id=uuid.uuid4(), unidad_id=uuid.uuid4(), prestatario_tipo="externo",
        prestatario_nombre="Ana Pérez", tercero_documento_tipo="CC",
        tercero_documento_numero="1020304050", tercero_entidad="Juzgado 5", tercero_cargo="Escribiente",
        tercero_correo="ana.perez@juzgado.gov.co", tercero_telefono="3001234567",
        prestado_por=uuid.uuid4(), fecha_prestamo=datetime.now(tz=UTC),
        fecha_devolucion_esperada=datetime.now(tz=UTC).date(), fecha_devolucion_real=None,
        estado="prestado", vencido=False,
    )


def test_enmascarar_oculta_documento_correo_y_telefono_pero_no_quien_ni_donde() -> None:
    from app.schemas.fisico import enmascarar_prestatario

    m = enmascarar_prestatario(_externo())
    assert m.tercero_documento_numero.endswith("4050") and "1020" not in m.tercero_documento_numero
    assert m.tercero_correo == "a***@juzgado.gov.co"
    assert m.tercero_telefono.endswith("4567") and "3001" not in m.tercero_telefono
    # lo que dice QUIÉN tiene la unidad no es dato de contacto
    assert (m.prestatario_nombre, m.tercero_entidad, m.tercero_cargo) == ("Ana Pérez", "Juzgado 5", "Escribiente")


@pytest.mark.asyncio
async def test_los_listados_enmascaran_sin_crud_3_y_el_nivel_3_ve_completo() -> None:
    from unittest.mock import patch

    from app.routers import fisico as r

    fila = _externo()
    with patch.object(r, "check_permission", new=AsyncMock(return_value=False)):
        (visto,) = await r._segun_nivel(MagicMock(), uuid.uuid4(), [fila])
    assert visto.tercero_correo != fila.tercero_correo
    with patch.object(r, "check_permission", new=AsyncMock(return_value=True)):
        (completo,) = await r._segun_nivel(MagicMock(), uuid.uuid4(), [fila])
    assert completo.tercero_correo == fila.tercero_correo
    (anonimo,) = await r._segun_nivel(MagicMock(), None, [fila])
    assert anonimo.tercero_correo != fila.tercero_correo, "sin identidad: fail-closed"
