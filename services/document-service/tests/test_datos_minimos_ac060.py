# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Datos mínimos del radicado — Acuerdo AGN 060/2001 §5.2 (AREA-09-02).

Mutaciones (todas verificadas, ver el CHANGELOG):
- quitar la rama `E` de `REQUERIDOS_POR_TIPO` → `test_entrada_sin_dependencia_destino_es_422` cae;
- quitar `doc_class` de las tres ramas → `test_censo_*` y `test_sin_tipo_documental_*` caen;
- quitar la validación de `BatchDocumentCreate` → el lote acepta el ítem y
  `test_el_lote_exige_los_mismos_datos_minimos` cae.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.datos_minimos import REQUERIDOS_POR_TIPO, exigir_o_422, faltantes
from app.schemas.batch import BatchDocumentCreate
from app.services import batch_service as batch_mod
from tests.conftest import TENANT_HEADERS, make_radicado_record

_BASE = {"subject": "Solicitud", "doc_class": "Petición"}


def _cuerpo(doc_type: str, **extra) -> dict:
    return {"doc_type": doc_type, **_BASE, **extra}


def test_censo_de_campos_del_5_2_por_tipo() -> None:
    """Cada tipo declara el tipo documental y su dependencia; nada se queda sin regla."""
    assert set(REQUERIDOS_POR_TIPO) == {"E", "S", "I"}
    for tipo, campos in REQUERIDOS_POR_TIPO.items():
        assert "doc_class" in campos, tipo
    assert "dest_dept_code" in REQUERIDOS_POR_TIPO["E"]
    assert "dest_dept_code" in REQUERIDOS_POR_TIPO["I"]
    assert "origin_dept_code" in REQUERIDOS_POR_TIPO["S"]


def test_faltantes_trata_el_tipo_en_blanco_como_ausente() -> None:
    assert faltantes("E", doc_class="  ", origin_dept_code=None, dest_dept_code=1) == ["doc_class"]
    assert faltantes("S", doc_class="x", origin_dept_code=None, dest_dept_code=9) == [
        "origin_dept_code"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("doc_type", "extra"),
    [
        ("E", {"origin_dept_code": 1}),  # falta el DESTINO
        ("I", {"origin_dept_code": 1}),
        ("S", {"dest_dept_code": 1}),  # falta el ORIGEN
    ],
)
async def test_sin_dependencia_es_422_y_no_gasta_consecutivo(
    client_with_conn, doc_type: str, extra: dict
) -> None:
    client, mock_conn = client_with_conn
    resp = await client.post(
        "/api/v1/documents", json=_cuerpo(doc_type, **extra), headers=TENANT_HEADERS
    )
    assert resp.status_code == 422
    assert "Acuerdo AGN 060/2001" in json.dumps(resp.json())
    consultas = [c.args[0] for c in mock_conn.fetchval.call_args_list if c.args]
    assert not any("tracking_sequences" in q for q in consultas)


# Alias con el nombre que cita el CHANGELOG para la mutación de la rama E.
test_entrada_sin_dependencia_destino_es_422 = test_sin_dependencia_es_422_y_no_gasta_consecutivo


@pytest.mark.asyncio
@pytest.mark.parametrize("doc_type", ["E", "S", "I"])
async def test_sin_tipo_documental_es_422(client_with_conn, doc_type: str) -> None:
    client, _ = client_with_conn
    cuerpo = _cuerpo(doc_type, origin_dept_code=1, dest_dept_code=1)
    cuerpo.pop("doc_class")
    resp = await client.post("/api/v1/documents", json=cuerpo, headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_con_los_datos_mínimos_se_radica(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.side_effect = [None, {"code": "DEMO"}, make_radicado_record()]
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = []
    resp = await client.post(
        "/api/v1/documents", json=_cuerpo("E", dest_dept_code=1), headers=TENANT_HEADERS
    )
    assert resp.status_code == 201


# -- Lote: la segunda puerta ------------------------------------------------


def test_el_lote_exige_los_mismos_datos_minimos() -> None:
    with pytest.raises(ValueError, match="Acuerdo AGN 060/2001"):
        BatchDocumentCreate(doc_type="E", subject="x", doc_class="Petición")
    with pytest.raises(ValueError):
        BatchDocumentCreate(doc_type="S", subject="x", doc_class="Petición", dest_dept_code=1)
    ok = BatchDocumentCreate(
        doc_type="S", subject="x", doc_class="Petición", origin_dept_code=3
    )
    assert ok.origin_dept_code == 3


@pytest.mark.asyncio
async def test_el_lote_rechaza_la_peticion_entera_si_un_item_no_cumple(client_with_conn) -> None:
    client, _ = client_with_conn
    resp = await client.post(
        "/api/v1/batch/documents",
        json={
            "documents": [
                {"doc_type": "E", "subject": "ok", "doc_class": "P", "dest_dept_code": 1},
                {"doc_type": "E", "subject": "sin destino", "doc_class": "P"},
            ]
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_un_item_heredado_sin_codigos_falla_como_item_y_no_radica() -> None:
    """Un lote encolado ANTES de este cambio (payload sin códigos) no se radica a medias:
    el ítem queda `failed` con su motivo y no se toca `tracking_sequences`."""
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    item = {
        "id": uuid.uuid4(),
        "item_index": 0,
        "payload": json.dumps({"doc_type": "E", "subject": "Heredado"}),
    }
    conn.fetch.return_value = [item]
    consultas: list[str] = []

    async def _fetchval(query: str, *a: object, **k: object):
        consultas.append(query)
        if "pg_try_advisory_lock" in query or "pg_advisory_unlock" in query:
            return True
        if "RETURNING COALESCE(reprocesado_por, created_by)" in query:
            return uuid.uuid4()
        return None

    async def _fetchrow(query: str, *a: object, **k: object):
        if "SELECT total_items" in query:
            return {"total_items": 1, "processed_items": 0, "failed_items": 1}
        raise AssertionError(query)

    conn.fetchval.side_effect = _fetchval
    conn.fetchrow.side_effect = _fetchrow
    await batch_mod.BatchService(conn, "demo").process_batch_documents(uuid.uuid4())
    fallidos = [
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "UPDATE batch_job_items" in c.args[0]
        and "status = 'failed'" in c.args[0]
    ]
    assert len(fallidos) == 1
    assert not any("tracking_sequences" in q for q in consultas)


# -- Productores internos ---------------------------------------------------


def test_exigir_o_422_nombra_los_campos() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        exigir_o_422("S", doc_class=None, origin_dept_code=None, dest_dept_code=None)
    assert exc.value.status_code == 422
    assert exc.value.detail["campos"] == ["doc_class", "origin_dept_code"]


@pytest.mark.asyncio
async def test_la_respuesta_rapida_hereda_origen_y_tipo_del_antecedente(client_with_deps) -> None:
    client, mock_conn, fake_tenant = client_with_deps
    fake_tenant.dependencias[7] = {"codigo": 7, "nombre": "Jurídica", "activo": True}
    entrada = make_radicado_record(dest_dept_code=7, doc_class="Tutela")
    salida = make_radicado_record(doc_type="S", tracking_number="2024-DEMO-S-000001")
    mock_conn.fetchval.return_value = True
    # La Salida hereda origen 7 del antecedente: el actor tiene URD activa ahí (create).

    async def _fetch(sql: str, *a: object):
        return [{"depe_id": 7}] if "urd_usuaroldep" in sql else []

    mock_conn.fetch.side_effect = _fetch
    insertados: list[tuple] = []

    async def _fetchrow(query: str, *a: object, **k: object):
        if "INSERT INTO radicados" in query:
            insertados.append(a)
            return salida
        if "FROM metadata_templates" in query:
            return None
        if "SELECT code" in query:
            return {"code": "DEMO"}
        if "is_root" in query:
            return {"is_root": False, "max_level": 3}
        return entrada

    mock_conn.fetchrow.side_effect = _fetchrow
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/respuesta",
        json={"subject": "RE: tutela"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    # create(): ($5 subject, $6 doc_class, ..., $9 origin_dept_code)
    args = insertados[0]
    assert "Tutela" in args  # doc_class heredado
    assert 7 in args  # origin_dept_code heredado del destino de la Entrada


@pytest.mark.asyncio
async def test_la_respuesta_rapida_sin_datos_heredables_es_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    entrada = make_radicado_record(dest_dept_code=None, doc_class=None)
    mock_conn.fetchval.return_value = True

    async def _fetchrow(query: str, *a: object, **k: object):
        if "is_root" in query:
            return {"is_root": False, "max_level": 3}
        return entrada

    mock_conn.fetchrow.side_effect = _fetchrow
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/respuesta",
        json={"subject": "RE: algo"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "datos_minimos_incompletos"


@pytest.mark.asyncio
async def test_la_respuesta_con_origen_heredado_suprimido_es_400_no_500(client_with_deps) -> None:
    client, mock_conn, fake_tenant = client_with_deps
    fake_tenant.dependencias[7] = {"codigo": 7, "nombre": "Vieja", "activo": False}
    entrada = make_radicado_record(dest_dept_code=7, doc_class="Tutela")
    mock_conn.fetchval.return_value = True

    async def _fetchrow(query: str, *a: object, **k: object):
        if "is_root" in query:
            return {"is_root": False, "max_level": 3}
        return entrada

    mock_conn.fetchrow.side_effect = _fetchrow
    resp = await client.post(
        f"/api/v1/documents/{uuid.uuid4()}/respuesta",
        json={"subject": "RE: algo"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_dependencia"
