# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests unitarios (mockeados) de la coherencia origen/destino por tipo de
transferencia (E12 H-M, Ley 594/2000 art. 23 -- ciclo vital del documento).

Carril mockeado (`tests/conftest.py`, sin Postgres) -- basta para validar la
lógica de coherencia (`_TIPO_ARCHIVO_ESPERADO`) en `TransferenciaService.create()`,
que es pura sobre las filas leídas. La unicidad de transferencia activa (H-L) y
la lectura de ubicaciones REALES viven en
`tests/integration/test_transferencia_creacion_integridad.py` -- este archivo
NO las duplica.

Orden de `fetchrow` dentro de `create()` (ver `transferencia_service.py`):
  1. `ArchiveRepository.get_expediente_by_id`         -- SIEMPRE.
  2. `FisicoRepository.get_ubicacion(origen_id)`       -- solo si el PAR fue aportado
  3. `FisicoRepository.get_ubicacion(destino_id)`      -- y sobrevivió al check "origen==destino".
  4. `TransferenciaRepository.insert` (RETURNING)      -- solo si la coherencia pasó.
`mock_conn.fetchrow.side_effect` refleja esa secuencia en cada test.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import (
    TENANT_HEADERS,
    USER_ID,
    indice_firmado_row,
    make_expediente_record,
)

NOW = datetime.now(tz=UTC)


def _ubicacion_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "parent_id": None,
        "nivel": "deposito",
        "codigo": "UB-1",
        "nombre": "itest ubicación",
        "tipo_archivo": "gestion",
        "capacidad_ml": None,
        "activo": True,
        "created_at": NOW,
    }
    base.update(kw)
    return base


def _unidad(ubicacion_id: uuid.UUID) -> dict:
    """Una unidad de conservación del expediente EN `ubicacion_id`: su tenencia física
    localizada. Con el par origen/destino aportado hace falta (AREA-07-06): sin tenencia
    el par es 422 `ubicacion_sin_tenencia` y estos tests no llegarían a la coherencia."""
    return {
        "unidad_id": uuid.uuid4(),
        "tipo": "caja",
        "codigo": "C-1",
        "signatura": "S-1",
        "ubicacion_id": ubicacion_id,
        "folio_inicio": 1,
        "folio_fin": 10,
    }


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


def _audit_log_calls(mock_conn) -> list[tuple]:
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]


def _denial_motivo(mock_conn) -> str:
    audit_calls = _audit_log_calls(mock_conn)
    denial = next(a for a in audit_calls if a[5] == "archive.transferencia_preparada_failed")
    assert denial[3] == str(USER_ID)  # H-D: el asiento de denegación también unifica el actor
    payload = json.loads(denial[8])
    return payload["motivo"]


# ---------------------------------------------------------------------------
# (4) primaria con origen/destino incoherentes -> 422.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_primaria_origen_incoherente_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    origen_id = uuid.uuid4()
    destino_id = uuid.uuid4()
    # primaria exige gestion->central; aquí origen es 'central' (incoherente).
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
        _ubicacion_row(id=origen_id, tipo_archivo="central"),
        _ubicacion_row(id=destino_id, tipo_archivo="central"),
    ]
    mock_conn.fetch.side_effect = [[], [_unidad(origen_id)]]

    resp = await client.post(
        "/api/v1/transferencias",
        json={
            "tipo": "primaria",
            "objeto": "Transferencia de la serie de contratos de la vigencia",
            "expediente_id": str(exp_id),
            "ubicacion_origen_id": str(origen_id),
            "ubicacion_destino_id": str(destino_id),
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ubicacion_incoherente_con_tipo"
    assert _denial_motivo(mock_conn) == "ubicacion_incoherente_con_tipo"


@pytest.mark.asyncio
async def test_create_primaria_destino_incoherente_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    origen_id = uuid.uuid4()
    destino_id = uuid.uuid4()
    # origen correcto (gestion), destino incoherente (esperado central, es historico).
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
        _ubicacion_row(id=origen_id, tipo_archivo="gestion"),
        _ubicacion_row(id=destino_id, tipo_archivo="historico"),
    ]
    mock_conn.fetch.side_effect = [[], [_unidad(origen_id)]]

    resp = await client.post(
        "/api/v1/transferencias",
        json={
            "tipo": "primaria",
            "objeto": "Transferencia de la serie de contratos de la vigencia",
            "expediente_id": str(exp_id),
            "ubicacion_origen_id": str(origen_id),
            "ubicacion_destino_id": str(destino_id),
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ubicacion_incoherente_con_tipo"
    assert _denial_motivo(mock_conn) == "ubicacion_incoherente_con_tipo"


# ---------------------------------------------------------------------------
# (5) secundaria con origen/destino incoherentes -> 422.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_secundaria_origen_incoherente_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    origen_id = uuid.uuid4()
    destino_id = uuid.uuid4()
    # secundaria exige central->historico; origen aquí es 'gestion' (incoherente).
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
        _ubicacion_row(id=origen_id, tipo_archivo="gestion"),
        _ubicacion_row(id=destino_id, tipo_archivo="historico"),
    ]
    mock_conn.fetch.side_effect = [[], [_unidad(origen_id)]]

    resp = await client.post(
        "/api/v1/transferencias",
        json={
            "tipo": "secundaria",
            "objeto": "Transferencia de la serie de contratos de la vigencia",
            "expediente_id": str(exp_id),
            "ubicacion_origen_id": str(origen_id),
            "ubicacion_destino_id": str(destino_id),
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ubicacion_incoherente_con_tipo"
    assert _denial_motivo(mock_conn) == "ubicacion_incoherente_con_tipo"


@pytest.mark.asyncio
async def test_create_secundaria_destino_incoherente_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    origen_id = uuid.uuid4()
    destino_id = uuid.uuid4()
    # origen correcto (central), destino incoherente (esperado historico, es gestion).
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
        _ubicacion_row(id=origen_id, tipo_archivo="central"),
        _ubicacion_row(id=destino_id, tipo_archivo="gestion"),
    ]
    mock_conn.fetch.side_effect = [[], [_unidad(origen_id)]]

    resp = await client.post(
        "/api/v1/transferencias",
        json={
            "tipo": "secundaria",
            "objeto": "Transferencia de la serie de contratos de la vigencia",
            "expediente_id": str(exp_id),
            "ubicacion_origen_id": str(origen_id),
            "ubicacion_destino_id": str(destino_id),
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ubicacion_incoherente_con_tipo"
    assert _denial_motivo(mock_conn) == "ubicacion_incoherente_con_tipo"


# ---------------------------------------------------------------------------
# (6) origen == destino -> 422, ANTES de cualquier lectura de ubicación (el
# check de igualdad de IDs es puramente sintáctico, no necesita ir a BD).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_origen_igual_destino_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    same_id = uuid.uuid4()
    # Solo el expediente se lee -- el check origen==destino corta ANTES de
    # llegar a `FisicoRepository.get_ubicacion` (sin fetchrow adicional).
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
    ]
    mock_conn.fetch.side_effect = [[], [_unidad(same_id)]]

    resp = await client.post(
        "/api/v1/transferencias",
        json={
            "tipo": "primaria",
            "objeto": "Transferencia de la serie de contratos de la vigencia",
            "expediente_id": str(exp_id),
            "ubicacion_origen_id": str(same_id),
            "ubicacion_destino_id": str(same_id),
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ubicaciones_origen_destino_iguales"
    assert _denial_motivo(mock_conn) == "ubicaciones_origen_destino_iguales"


# ---------------------------------------------------------------------------
# ubicacion_not_found: un id aportado no existe -> 422 limpio (no un
# ForeignKeyViolationError crudo del INSERT).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_ubicacion_origen_not_found_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    origen_id = uuid.uuid4()
    destino_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
        None,  # ubicacion_origen_id no existe
        _ubicacion_row(id=destino_id, tipo_archivo="central"),
    ]
    mock_conn.fetch.side_effect = [[], [_unidad(origen_id)]]

    resp = await client.post(
        "/api/v1/transferencias",
        json={
            "tipo": "primaria",
            "objeto": "Transferencia de la serie de contratos de la vigencia",
            "expediente_id": str(exp_id),
            "ubicacion_origen_id": str(origen_id),
            "ubicacion_destino_id": str(destino_id),
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ubicacion_not_found"
    assert _denial_motivo(mock_conn) == "ubicacion_not_found"


# ---------------------------------------------------------------------------
# (7) camino feliz: primaria gestion->central Y secundaria central->historico
# -> 201, con los ids de ubicación ecoados en la respuesta.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_primaria_coherente_201(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    origen_id = uuid.uuid4()
    destino_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
        _ubicacion_row(id=origen_id, tipo_archivo="gestion"),
        _ubicacion_row(id=destino_id, tipo_archivo="central"),
        # `dependencia_de_usuario` (migración 052): la oficina productora de
        # quien prepara, resuelta antes del INSERT.
        _depe_row(),
        _transfer_row(
            expediente_id=exp_id,
            ubicacion_origen_id=origen_id,
            ubicacion_destino_id=destino_id,
        ),
    ]
    mock_conn.fetch.side_effect = [[], [_unidad(origen_id)]]

    resp = await client.post(
        "/api/v1/transferencias",
        json={
            "tipo": "primaria",
            "objeto": "Transferencia de la serie de contratos de la vigencia",
            "expediente_id": str(exp_id),
            "ubicacion_origen_id": str(origen_id),
            "ubicacion_destino_id": str(destino_id),
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["estado"] == "preparada"
    assert body["ubicacion_origen_id"] == str(origen_id)
    assert body["ubicacion_destino_id"] == str(destino_id)
    assert not any(
        a[5] == "archive.transferencia_preparada_failed" for a in _audit_log_calls(mock_conn)
    )


@pytest.mark.asyncio
async def test_create_secundaria_coherente_201(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    origen_id = uuid.uuid4()
    destino_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
        _ubicacion_row(id=origen_id, tipo_archivo="central"),
        _ubicacion_row(id=destino_id, tipo_archivo="historico"),
        # `dependencia_de_usuario` (migración 052): la oficina productora de
        # quien prepara, resuelta antes del INSERT.
        _depe_row(),
        _transfer_row(
            tipo="secundaria",
            expediente_id=exp_id,
            ubicacion_origen_id=origen_id,
            ubicacion_destino_id=destino_id,
        ),
    ]
    mock_conn.fetch.side_effect = [[], [_unidad(origen_id)]]

    resp = await client.post(
        "/api/v1/transferencias",
        json={
            "tipo": "secundaria",
            "objeto": "Transferencia de la serie de contratos de la vigencia",
            "expediente_id": str(exp_id),
            "ubicacion_origen_id": str(origen_id),
            "ubicacion_destino_id": str(destino_id),
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["estado"] == "preparada"
    assert body["ubicacion_origen_id"] == str(origen_id)
    assert body["ubicacion_destino_id"] == str(destino_id)


# ---------------------------------------------------------------------------
# (8) obligatoriedad del par -- DECISIÓN: "ambos o ninguno" (no obligatorio).
#   - un solo lado aportado -> 422 ubicaciones_par_incompleto.
#   - ninguno aportado -> 201 (ya cubierto también por
#     `test_transferencias.py::test_create_transferencia`; se repite aquí de
#     forma explícita, con docstring, para documentar la decisión junto al
#     resto de la matriz H-M).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_solo_origen_sin_destino_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    origen_id = uuid.uuid4()
    # Ningún `get_ubicacion` se llega a invocar -- el check "ambos o ninguno"
    # corta antes.
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/transferencias",
        json={
            "tipo": "primaria",
            "objeto": "Transferencia de la serie de contratos de la vigencia",
            "expediente_id": str(exp_id),
            "ubicacion_origen_id": str(origen_id),
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ubicaciones_par_incompleto"
    assert _denial_motivo(mock_conn) == "ubicaciones_par_incompleto"


@pytest.mark.asyncio
async def test_create_solo_destino_sin_origen_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    destino_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/transferencias",
        json={
            "tipo": "primaria",
            "objeto": "Transferencia de la serie de contratos de la vigencia",
            "expediente_id": str(exp_id),
            "ubicacion_destino_id": str(destino_id),
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ubicaciones_par_incompleto"
    assert _denial_motivo(mock_conn) == "ubicaciones_par_incompleto"


@pytest.mark.asyncio
async def test_create_sin_ninguna_ubicacion_201_topologia_opcional(client_with_conn) -> None:
    """DECISIÓN de obligatoriedad (contrato E12 H-M): el par es OPCIONAL --
    "ambos o ninguno". Un traspaso de custodia sin topología física modelada
    (E17 parcial en el roadmap) sigue siendo admisible."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
        # `dependencia_de_usuario` (migración 052): la oficina productora de
        # quien prepara, resuelta antes del INSERT.
        _depe_row(),
        _transfer_row(expediente_id=exp_id),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/transferencias",
        json={"tipo": "primaria", "expediente_id": str(exp_id), "objeto": "Transferencia de la serie de contratos de la vigencia"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["ubicacion_origen_id"] is None
    assert resp.json()["ubicacion_destino_id"] is None
