# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Rutas de protección WORM selladas por token interno y legal hold como acto
explícito (AREA-08-16, AREA-08-06, AREA-08-07).

- `/proteger-indice`, `/proteger-artefacto` y `/aip` ya no se autorizan por el
  RBAC de un humano: sin `X-Internal-Token` -> 401, aunque lleven identidad y
  tengan el permiso.
- La segunda protección del mismo (expediente, tipo) con OTRO `file_id` es 409
  y no sube nada.
- `legal_hold` no es un campo de ningún body de protección (422).
- `POST /legal-hold-internal` fija/levanta el hold sobre la version_id exacta,
  con asiento atómico con la fila.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from app.core import config as config_module
from tests.conftest import INTERNAL_HEADERS, TENANT_HEADERS, conceder_permisos, make_worm_row


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)


_RUTAS = [
    ("/api/v1/preservacion/proteger-indice", {"file_id": str(uuid.uuid4())}),
    ("/api/v1/preservacion/proteger-artefacto", {"file_id": str(uuid.uuid4())}),
    (
        "/api/v1/preservacion/aip",
        {
            "documentos": [
                {"document_id": "R-1", "nombre": "a.pdf", "file_id": str(uuid.uuid4())}
            ]
        },
    ),
]


def _body(extra: dict) -> dict:
    return {
        "expediente_id": "EXP-1",
        "retencion_anios": 5,
        "fecha_inicio": "2026-01-01",
        **extra,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("ruta", "extra"), _RUTAS)
async def test_ruta_externa_sin_token_interno_es_401_aunque_tenga_permiso(
    client_with_conn, ruta, extra
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    conceder_permisos(mock_conn)  # el usuario TIENE USUA_PERM_EXPEDIENTE:3
    resp = await client.post(ruta, json=_body(extra), headers=TENANT_HEADERS)
    assert resp.status_code == 401
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("ruta", "extra"), _RUTAS)
async def test_ruta_externa_con_token_equivocado_es_403(client_with_conn, ruta, extra) -> None:
    client, _conn, mock_minio = client_with_conn
    resp = await client.post(
        ruta,
        json=_body(extra),
        headers={**TENANT_HEADERS, "X-Internal-Token": "otro"},
    )
    assert resp.status_code in (401, 403)
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", ["proteger-indice", "proteger-artefacto", "aip"])
async def test_legal_hold_en_el_body_es_422_en_toda_ruta_de_proteccion(
    client_with_conn, ruta
) -> None:
    client, _conn, mock_minio = client_with_conn
    extra = dict(next(e for r, e in _RUTAS if r.endswith(ruta)))
    resp = await client.post(
        f"/api/v1/preservacion/{ruta}",
        json=_body({**extra, "legal_hold": True}),
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 422
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_segunda_proteccion_con_otro_file_id_es_409_sin_subir(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    existente = make_worm_row(expediente_id="EXP-1", file_id=uuid.uuid4())
    mock_conn.fetchrow.side_effect = [existente]
    resp = await client.post(
        "/api/v1/preservacion/proteger-indice-internal",
        json=_body({"file_id": str(uuid.uuid4())}),
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "worm_singleton_conflict"
    mock_minio.get_object.assert_not_called()
    mock_minio.put_object.assert_not_awaited()


# -- legal hold ----------------------------------------------------------


def _preparar(mock_conn, mock_minio, filas: list[dict]):
    eventos: list[str] = []

    class _Tx:
        async def __aenter__(self):
            eventos.append("tx_enter")

        async def __aexit__(self, *exc):
            eventos.append("tx_exit")
            return False

    async def _execute(sql, *a, **k):
        if "INSERT INTO public.audit_log" in sql:
            eventos.append("audit")
        return None

    async def _fetchrow(sql, *a, **k):
        if "UPDATE preservacion_worm_objeto" in sql and "legal_hold" in sql:
            eventos.append("update_fila")
        return None

    mock_conn.transaction = lambda: _Tx()
    mock_conn.execute.side_effect = _execute
    mock_conn.fetchrow.side_effect = _fetchrow
    mock_conn.fetch.return_value = filas

    async def _on(*a, **k):
        eventos.append("minio_on")

    async def _off(*a, **k):
        eventos.append("minio_off")

    mock_minio.enable_object_legal_hold = AsyncMock(side_effect=_on)
    mock_minio.disable_object_legal_hold = AsyncMock(side_effect=_off)
    return eventos


_HOLD = {
    "expediente_id": "EXP-1",
    "activo": False,
    "motivo": "Se levanta por orden del juzgado 12",
    "acta_ref": "ACTA-2026-044",
}


@pytest.mark.asyncio
async def test_levantar_hold_llama_al_almacen_con_la_version_exacta_y_asienta_en_la_tx(
    client_with_conn,
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    fila = make_worm_row(expediente_id="EXP-1", legal_hold=True, file_id=uuid.uuid4())
    eventos = _preparar(mock_conn, mock_minio, [fila])

    resp = await client.post(
        "/api/v1/preservacion/legal-hold-internal", json=_HOLD, headers=INTERNAL_HEADERS
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["objetos_afectados"] == 1
    mock_minio.disable_object_legal_hold.assert_awaited_once_with(
        fila["bucket"], fila["object_key"], version_id=fila["version_id"]
    )
    # Orden al LEVANTAR: asiento de solicitud -> almacén -> fila + asiento final.
    # (`audit.append` abre su propio savepoint: hay tx_enter anidados.)
    assert eventos.index("audit") < eventos.index("minio_off") < eventos.index("update_fila")
    assert eventos.index("update_fila") < len(eventos) - 1 - eventos[::-1].index("audit")
    acciones = [
        c.args[5]
        for c in mock_conn.execute.call_args_list
        if "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert acciones == [
        "worm.legal_hold_levantamiento_solicitado",
        "worm.legal_hold_levantado",
    ]
    audit_sql = [
        c for c in mock_conn.execute.call_args_list if "INSERT INTO public.audit_log" in c.args[0]
    ][-1]
    payload = json.loads(audit_sql.args[8])
    assert payload["motivo"] == _HOLD["motivo"]
    assert payload["acta_ref"] == _HOLD["acta_ref"]


@pytest.mark.asyncio
async def test_fijar_hold_asienta_fijado_y_fila_ya_en_estado_no_se_toca(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    ya = make_worm_row(expediente_id="EXP-1", legal_hold=True, tipo="indice")
    nueva = make_worm_row(expediente_id="EXP-1", legal_hold=False, tipo="aip")
    eventos = _preparar(mock_conn, mock_minio, [ya, nueva])

    resp = await client.post(
        "/api/v1/preservacion/legal-hold-internal",
        json={**_HOLD, "activo": True},
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["objetos_afectados"], body["objetos_sin_cambio"]) == (1, 1)
    # La fila `aip` nueva alcanza primaria + réplica; la fila ya en hold no se toca.
    assert mock_minio.enable_object_legal_hold.await_count == 2
    assert eventos.count("audit") == 1
    audit_sql = next(
        c for c in mock_conn.execute.call_args_list if "INSERT INTO public.audit_log" in c.args[0]
    )
    assert audit_sql.args[5] == "worm.legal_hold_fijado"


@pytest.mark.asyncio
async def test_hold_sin_motivo_o_sin_token_se_rechaza(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    sin_motivo = await client.post(
        "/api/v1/preservacion/legal-hold-internal",
        json={**_HOLD, "motivo": "corto"},
        headers=INTERNAL_HEADERS,
    )
    assert sin_motivo.status_code == 422
    sin_token = await client.post(
        "/api/v1/preservacion/legal-hold-internal", json=_HOLD, headers=TENANT_HEADERS
    )
    assert sin_token.status_code == 401
    mock_minio.disable_object_legal_hold.assert_not_called()


@pytest.mark.asyncio
async def test_hold_de_expediente_sin_objetos_es_404(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    _preparar(mock_conn, mock_minio, [])
    resp = await client.post(
        "/api/v1/preservacion/legal-hold-internal", json=_HOLD, headers=INTERNAL_HEADERS
    )
    assert resp.status_code == 404


# -- huecos cerrados en la revision de pruebas -----------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ruta", ["proteger-indice-internal", "proteger-artefacto-internal"]
)
async def test_reintento_con_el_mismo_file_id_sigue_siendo_200_idempotente(
    client_with_conn, ruta
) -> None:
    """Control positivo del 409: la comparacion de file_id NO puede convertir el
    reintento legitimo (mismo file_id) en conflicto (UUID vs UUID, no str)."""
    client, mock_conn, mock_minio = client_with_conn
    fid = uuid.uuid4()
    existente = make_worm_row(expediente_id="EXP-1", file_id=fid)
    mock_conn.fetchrow.side_effect = [existente]
    resp = await client.post(
        f"/api/v1/preservacion/{ruta}",
        json=_body({"file_id": str(fid)}),
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_fila_previa_sin_file_id_conserva_el_200(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    mock_conn.fetchrow.side_effect = [make_worm_row(expediente_id="EXP-1", file_id=None)]
    resp = await client.post(
        "/api/v1/preservacion/proteger-indice-internal",
        json=_body({"file_id": str(uuid.uuid4())}),
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_conflicto_de_singleton_tambien_en_proteger_artefacto_internal(
    client_with_conn,
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    mock_conn.fetchrow.side_effect = [make_worm_row(expediente_id="EXP-1", file_id=uuid.uuid4())]
    resp = await client.post(
        "/api/v1/preservacion/proteger-artefacto-internal",
        json=_body({"file_id": str(uuid.uuid4()), "tipo": "acta_transferencia"}),
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 409, resp.text
    mock_minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_hold_ya_en_el_estado_pedido_no_toca_almacen_ni_asienta(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    fila = make_worm_row(expediente_id="EXP-1", legal_hold=False)
    eventos = _preparar(mock_conn, mock_minio, [fila])
    resp = await client.post(
        "/api/v1/preservacion/legal-hold-internal", json=_HOLD, headers=INTERNAL_HEADERS
    )  # _HOLD.activo = False y la fila ya esta sin hold
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["objetos_afectados"], body["objetos_sin_cambio"]) == (0, 1)
    mock_minio.disable_object_legal_hold.assert_not_called()
    assert "audit" not in eventos and "update_fila" not in eventos


@pytest.mark.asyncio
async def test_fallo_del_almacen_al_levantar_deja_solicitud_y_fallo_sin_tocar_la_fila(
    client_with_conn,
) -> None:
    """Si el almacen falla al levantar, la fila no cambia (sigue bloqueando) y
    quedan asentados la solicitud y el fallo: el acto es reintentable."""
    client, mock_conn, mock_minio = client_with_conn
    fila = make_worm_row(expediente_id="EXP-1", legal_hold=True)
    eventos = _preparar(mock_conn, mock_minio, [fila])
    mock_minio.disable_object_legal_hold = AsyncMock(side_effect=RuntimeError("s3 caido"))
    with pytest.raises(RuntimeError, match="s3 caido"):
        await client.post(
            "/api/v1/preservacion/legal-hold-internal", json=_HOLD, headers=INTERNAL_HEADERS
        )
    assert "update_fila" not in eventos
    acciones = [
        c.args[5]
        for c in mock_conn.execute.call_args_list
        if "INSERT INTO public.audit_log" in c.args[0]
    ]
    # Quedó constancia del intento y del fallo; la fila sigue con hold (reintentable).
    assert acciones == [
        "worm.legal_hold_levantamiento_solicitado",
        "worm.legal_hold_levantamiento_fallido",
    ]


@pytest.mark.asyncio
async def test_hold_con_preservacion_desactivada_es_503(client_with_conn, monkeypatch) -> None:
    monkeypatch.setattr(config_module.settings, "preservation_enabled", False)
    client, mock_conn, mock_minio = client_with_conn
    resp = await client.post(
        "/api/v1/preservacion/legal-hold-internal", json=_HOLD, headers=INTERNAL_HEADERS
    )
    assert resp.status_code == 503
    mock_minio.disable_object_legal_hold.assert_not_called()


@pytest.mark.asyncio
async def test_renovar_retencion_internal_tambien_rechaza_legal_hold(client_with_conn) -> None:
    client, _conn, _minio = client_with_conn
    resp = await client.post(
        "/api/v1/preservacion/renovar-retencion-internal",
        json=_body({"tipo": "indice", "legal_hold": False}),
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_fallo_del_almacen_al_fijar_no_asienta_nada(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    fila = make_worm_row(expediente_id="EXP-1", legal_hold=False)
    eventos = _preparar(mock_conn, mock_minio, [fila])
    mock_minio.enable_object_legal_hold = AsyncMock(side_effect=RuntimeError("s3 caido"))
    with pytest.raises(RuntimeError, match="s3 caido"):
        await client.post(
            "/api/v1/preservacion/legal-hold-internal",
            json={**_HOLD, "activo": True},
            headers=INTERNAL_HEADERS,
        )
    assert "audit" not in eventos and "update_fila" not in eventos


@pytest.mark.asyncio
async def test_levantar_no_libera_el_almacen_si_no_se_puede_asentar_la_solicitud(
    client_with_conn, monkeypatch
) -> None:
    """Mutacion AREA-08-07: si el asiento de solicitud falla, el almacen NO se
    toca (antes se liberaba primero y un fallo dejaba el objeto libre sin
    ningun asiento)."""
    from app.services import preservation_worm_service as svc

    client, mock_conn, mock_minio = client_with_conn
    fila = make_worm_row(expediente_id="EXP-1", legal_hold=True)
    _preparar(mock_conn, mock_minio, [fila])
    monkeypatch.setattr(svc.audit, "append", AsyncMock(side_effect=RuntimeError("audit caido")))
    with pytest.raises(RuntimeError, match="audit caido"):
        await client.post(
            "/api/v1/preservacion/legal-hold-internal", json=_HOLD, headers=INTERNAL_HEADERS
        )
    mock_minio.disable_object_legal_hold.assert_not_called()


@pytest.mark.asyncio
async def test_hold_toma_y_libera_el_advisory_lock_por_expediente(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    fila = make_worm_row(expediente_id="EXP-1", legal_hold=True)
    _preparar(mock_conn, mock_minio, [fila])
    resp = await client.post(
        "/api/v1/preservacion/legal-hold-internal", json=_HOLD, headers=INTERNAL_HEADERS
    )
    assert resp.status_code == 200, resp.text
    sqls = [c.args[0] for c in mock_conn.execute.call_args_list]
    assert any("pg_advisory_lock" in q for q in sqls)
    assert any("pg_advisory_unlock" in q for q in sqls)
    assert sqls.index(next(q for q in sqls if "pg_advisory_lock" in q)) < sqls.index(
        next(q for q in sqls if "INSERT INTO public.audit_log" in q)
    )


def _s3_error(code: str):
    from miniopy_async import S3Error

    return S3Error(code, "msg", "res", "rid", "hid", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("activo", [True, False])
async def test_hold_del_aip_alcanza_tambien_la_replica(client_with_conn, activo) -> None:
    """AREA-08-07: el freeze ya no deja la copia replicada sujeta solo a su
    retain_until. Dos llamadas al almacen: primaria (version exacta) y replica."""
    client, mock_conn, mock_minio = client_with_conn
    fila = make_worm_row(expediente_id="EXP-1", legal_hold=not activo, tipo="aip")
    _preparar(mock_conn, mock_minio, [fila])
    op = mock_minio.enable_object_legal_hold if activo else mock_minio.disable_object_legal_hold

    resp = await client.post(
        "/api/v1/preservacion/legal-hold-internal",
        json={**_HOLD, "activo": activo},
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert op.await_count == 2
    primaria, replica = op.await_args_list
    assert primaria.args == (fila["bucket"], fila["object_key"])
    assert primaria.kwargs == {"version_id": fila["version_id"]}
    assert replica.args[0].endswith("-replica") or replica.args[0] != fila["bucket"]
    assert replica.args[1] == fila["object_key"]
    audit_sql = [
        c for c in mock_conn.execute.call_args_list if "INSERT INTO public.audit_log" in c.args[0]
    ][-1]
    assert len(json.loads(audit_sql.args[8])["copias_alcanzadas"]) == 2


@pytest.mark.asyncio
async def test_replica_ausente_se_tolera_y_otro_error_aborta(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    fila = make_worm_row(expediente_id="EXP-1", legal_hold=False, tipo="aip")
    eventos = _preparar(mock_conn, mock_minio, [fila])

    async def _ausente(bucket, key, **kw):
        if bucket != fila["bucket"]:
            raise _s3_error("NoSuchKey")

    mock_minio.enable_object_legal_hold = AsyncMock(side_effect=_ausente)
    resp = await client.post(
        "/api/v1/preservacion/legal-hold-internal",
        json={**_HOLD, "activo": True},
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    audit_sql = [
        c for c in mock_conn.execute.call_args_list if "INSERT INTO public.audit_log" in c.args[0]
    ][-1]
    assert len(json.loads(audit_sql.args[8])["copias_alcanzadas"]) == 1

    # Un error distinto de «no existe» en la replica aborta sin asentar.
    mock_conn.execute.reset_mock()
    eventos.clear()

    async def _acceso_denegado(bucket, key, **kw):
        if bucket != fila["bucket"]:
            raise _s3_error("AccessDenied")

    mock_minio.enable_object_legal_hold = AsyncMock(side_effect=_acceso_denegado)
    with pytest.raises(Exception, match="AccessDenied|msg"):
        await client.post(
            "/api/v1/preservacion/legal-hold-internal",
            json={**_HOLD, "activo": True},
            headers=INTERNAL_HEADERS,
        )
    assert "update_fila" not in eventos
