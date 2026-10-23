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
- El legal hold por motivo (fijar/levantar/listar) se prueba en
  `tests/test_c5_legal_hold_por_motivo.py` (C-5/C7).
"""

from __future__ import annotations

import uuid

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
async def test_renovar_retencion_internal_tambien_rechaza_legal_hold(client_with_conn) -> None:
    client, _conn, _minio = client_with_conn
    resp = await client.post(
        "/api/v1/preservacion/renovar-retencion-internal",
        json=_body({"tipo": "indice", "legal_hold": False}),
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 422
