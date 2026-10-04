# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests unitarios (mockeados) del límite de validación de obligatoriedad
condicionada + cotejo de origen/destino contra la tenencia física LOCALIZADA
del expediente (E12/E17 residual RF-FIR-15, Ley 594/2000 arts. 15/26, Anexo
FUID Ac. AGN 001/2024).

Carril mockeado (`tests/conftest.py`, sin Postgres) -- basta para validar la
lógica PURA sobre las filas leídas: la determinación de `current_ubicaciones`
(a partir de `FisicoRepository.units_of_expediente`, un `conn.fetch`) y las
dos ramas (A: con tenencia localizada -- obligatorio+cotejado; B: sin
tenencia -- lógica, sin cotejo). El cotejo contra `expediente_unidad` REAL
(Postgres, vínculo M2M de verdad) vive en
`tests/integration/test_transferencia_tenencia_fisica.py` -- este archivo no
lo duplica, solo fija el contrato de la capa de servicio con `fetch` mockeado.

Orden de llamadas dentro de `create()` (ver `transferencia_service.py`):
  1. `ArchiveRepository.get_expediente_by_id`          -- SIEMPRE (fetchrow).
  1b. `ArchiveRepository._get_radicados`               -- SIEMPRE, DENTRO de
      `get_expediente_by_id` (fetch) -- primer `fetch`, distinto del de
      `units_of_expediente` (1c). Fácil de pasar por alto: NO es opcional.
  1c. `FisicoRepository.units_of_expediente`           -- SIEMPRE (fetch, el
      SEGUNDO).
  2. `FisicoRepository.get_ubicacion(origen_id)`       -- solo si el PAR fue aportado.
  3. `FisicoRepository.get_ubicacion(destino_id)`      -- y sobrevivió a "origen==destino".
  4. `TransferenciaRepository.insert` (RETURNING)      -- solo si cotejo+coherencia pasaron.
`mock_conn.fetchrow.side_effect` refleja la secuencia de `fetchrow`.
`mock_conn.fetch.side_effect` refleja la secuencia de `fetch`: SIEMPRE
`[radicados_vacío, unidades_del_expediente]` -- `mock_conn.fetch.return_value`
(un único valor para TODAS las llamadas) solo sirve cuando ambas respuestas
coinciden (p. ej. `[]`/`[]`, como en `test_transferencia_create_coherencia.py`).
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


def _unidad_de_expediente_row(**kw) -> dict:
    """Fila de `FisicoRepository.units_of_expediente` (SELECT plano, sin
    clearance): `unidad_id, signatura, ubicacion_id, folio_inicio, folio_fin`."""
    base = {
        "unidad_id": uuid.uuid4(),
        "signatura": "UN-1",
        "ubicacion_id": uuid.uuid4(),
        "folio_inicio": None,
        "folio_fin": None,
    }
    base.update(kw)
    return base


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


async def _post(client, *, tipo="primaria", exp_id, origen_id=None, destino_id=None):
    body = {
        "tipo": tipo,
        "expediente_id": str(exp_id),
        # Obligatorio desde la migración 052 (cabecera del Anexo FUID).
        "objeto": "Transferencia de la serie de contratos de la vigencia",
    }
    if origen_id is not None:
        body["ubicacion_origen_id"] = str(origen_id)
    if destino_id is not None:
        body["ubicacion_destino_id"] = str(destino_id)
    return await client.post(
        "/api/v1/transferencias",
        json=body,
        headers=TENANT_HEADERS,
    )


# ---------------------------------------------------------------------------
# Rama A -- CON tenencia física localizada: origen/destino OBLIGATORIOS.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rama_a_sin_origen_ni_destino_422(client_with_conn) -> None:
    """El expediente tiene una unidad con `ubicacion_id` resuelta -> tenencia
    localizada -> el par completo es obligatorio (subsume "ambos o
    ninguno" de la rama B: aquí NINGUNO no basta)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
    ]
    mock_conn.fetch.side_effect = [[], [_unidad_de_expediente_row()]]

    resp = await _post(client, exp_id=exp_id)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ubicaciones_requeridas_fisico"
    assert _denial_motivo(mock_conn) == "ubicaciones_requeridas_fisico"


@pytest.mark.asyncio
async def test_create_rama_a_solo_origen_sin_destino_422(client_with_conn) -> None:
    """Un solo lado aportado en rama A: sigue siendo `ubicaciones_requeridas_fisico`
    (NO el `ubicaciones_par_incompleto` de la rama B -- son mensajes/códigos
    distintos aunque el disparador se parezca)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    origen_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
    ]
    mock_conn.fetch.side_effect = [[], [_unidad_de_expediente_row()]]

    resp = await _post(client, exp_id=exp_id, origen_id=origen_id)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ubicaciones_requeridas_fisico"


@pytest.mark.asyncio
async def test_create_rama_a_origen_no_cotea_422(client_with_conn) -> None:
    """Corazón del cotejo: el origen aportado es del TIPO correcto
    ('gestion', lo que H-M exige para 'primaria') pero NO es ninguna de las
    ubicaciones ACTUALES del expediente -> rechazado por cotejo, ANTES de
    llegar siquiera a evaluarse la coherencia de tipo."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    ubicacion_actual_id = uuid.uuid4()
    origen_ajeno_id = uuid.uuid4()
    destino_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
        _ubicacion_row(id=origen_ajeno_id, tipo_archivo="gestion"),
        _ubicacion_row(id=destino_id, tipo_archivo="central"),
    ]
    mock_conn.fetch.side_effect = [
        [],
        [_unidad_de_expediente_row(ubicacion_id=ubicacion_actual_id)],
    ]

    resp = await _post(client, exp_id=exp_id, origen_id=origen_ajeno_id, destino_id=destino_id)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ubicacion_origen_no_actual"
    assert _denial_motivo(mock_conn) == "ubicacion_origen_no_actual"


@pytest.mark.asyncio
async def test_create_rama_a_origen_cotea_y_coherente_201(client_with_conn) -> None:
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
    mock_conn.fetch.side_effect = [[], [_unidad_de_expediente_row(ubicacion_id=origen_id)]]

    resp = await _post(client, exp_id=exp_id, origen_id=origen_id, destino_id=destino_id)
    assert resp.status_code == 201
    body = resp.json()
    assert body["ubicacion_origen_id"] == str(origen_id)
    assert body["ubicacion_destino_id"] == str(destino_id)
    assert not any(
        a[5] == "archive.transferencia_preparada_failed" for a in _audit_log_calls(mock_conn)
    )


@pytest.mark.asyncio
async def test_create_rama_a_multiplicidad_origen_cotea_con_cualquiera_201(
    client_with_conn,
) -> None:
    """Un expediente en VARIAS unidades/ubicaciones (M2M real de
    `expediente_unidad`): el origen válido es CUALQUIERA de las actuales, no
    "la única" -- un expediente repartido en varios depósitos admite
    transferir desde uno de ellos."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    ubicacion_a = uuid.uuid4()
    ubicacion_b = uuid.uuid4()
    destino_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
        _ubicacion_row(id=ubicacion_b, tipo_archivo="gestion"),
        _ubicacion_row(id=destino_id, tipo_archivo="central"),
        # `dependencia_de_usuario` (migración 052): la oficina productora de
        # quien prepara, resuelta antes del INSERT.
        _depe_row(),
        _transfer_row(
            expediente_id=exp_id,
            ubicacion_origen_id=ubicacion_b,
            ubicacion_destino_id=destino_id,
        ),
    ]
    mock_conn.fetch.side_effect = [
        [],
        [
            _unidad_de_expediente_row(ubicacion_id=ubicacion_a),
            _unidad_de_expediente_row(ubicacion_id=ubicacion_b),
        ],
    ]

    # Origen = la SEGUNDA ubicación actual (no la primera) -> también cotea.
    resp = await _post(client, exp_id=exp_id, origen_id=ubicacion_b, destino_id=destino_id)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_create_rama_a_multiplicidad_origen_no_es_ninguna_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    ubicacion_a = uuid.uuid4()
    ubicacion_b = uuid.uuid4()
    origen_ajeno = uuid.uuid4()
    destino_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
        _ubicacion_row(id=origen_ajeno, tipo_archivo="gestion"),
        _ubicacion_row(id=destino_id, tipo_archivo="central"),
    ]
    mock_conn.fetch.side_effect = [
        [],
        [
            _unidad_de_expediente_row(ubicacion_id=ubicacion_a),
            _unidad_de_expediente_row(ubicacion_id=ubicacion_b),
        ],
    ]

    resp = await _post(client, exp_id=exp_id, origen_id=origen_ajeno, destino_id=destino_id)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ubicacion_origen_no_actual"


@pytest.mark.asyncio
async def test_create_rama_a_cotejo_pasa_pero_coherencia_falla_422(client_with_conn) -> None:
    """Borde de composición (contrato §4): el expediente está SOLO en una
    ubicación 'central', tipo='primaria' (espera origen 'gestion'). El origen
    aportado ES la ubicación actual (cotejo PASA) pero es del tipo
    equivocado -> la coherencia H-M sigue vigente y rechaza. Cotejo y
    coherencia COMPONEN, no se sustituyen."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    ubicacion_central_id = uuid.uuid4()
    destino_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
        _ubicacion_row(id=ubicacion_central_id, tipo_archivo="central"),
        _ubicacion_row(id=destino_id, tipo_archivo="central"),
    ]
    mock_conn.fetch.side_effect = [
        [],
        [_unidad_de_expediente_row(ubicacion_id=ubicacion_central_id)],
    ]

    resp = await _post(
        client, exp_id=exp_id, origen_id=ubicacion_central_id, destino_id=destino_id
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ubicacion_incoherente_con_tipo"
    assert _denial_motivo(mock_conn) == "ubicacion_incoherente_con_tipo"


# ---------------------------------------------------------------------------
# Rama B -- SIN tenencia física localizada: lógica, sin cotejo (status-quo
# previo al incremento).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rama_b_electronico_sin_unidades_201(client_with_conn) -> None:
    """Expediente puramente electrónico (sin filas en `expediente_unidad`):
    el caso de uso central de un DMS moderno NO debe romperse por exigir
    topología física. (Complementa `test_transferencias.py::test_create_transferencia`,
    que ya cubre este camino desde antes de este incremento -- se repite aquí
    con nombre explícito porque es el punto de referencia de la matriz de
    tenencia física)."""
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

    resp = await _post(client, exp_id=exp_id)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_create_rama_b_unidad_sin_ubicacion_resuelta_no_cuenta_como_tenencia_201(
    client_with_conn,
) -> None:
    """Una unidad vinculada al expediente pero con `ubicacion_id` NULL (E17
    parcial, `ON DELETE SET NULL`) NO cuenta como tenencia localizada -- no
    hay ubicación contra la cual cotejar. Cae en rama B: el par sigue siendo
    opcional, sin cotejo."""
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
    mock_conn.fetch.side_effect = [[], [_unidad_de_expediente_row(ubicacion_id=None)]]

    resp = await _post(client, exp_id=exp_id)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_create_rama_b_par_incompleto_sigue_422(client_with_conn) -> None:
    """Regresión: el 'ambos o ninguno' de la rama B (status-quo previo al
    incremento) sigue vigente cuando NO hay tenencia localizada."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    origen_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
    ]
    mock_conn.fetch.return_value = []

    resp = await _post(client, exp_id=exp_id, origen_id=origen_id)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ubicaciones_par_incompleto"
