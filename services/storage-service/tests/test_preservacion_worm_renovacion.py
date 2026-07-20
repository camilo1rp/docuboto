# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de POST /api/v1/preservacion/renovar-retencion-internal (E10 debt:
renovación de retención WORM para series de disposición Conservación Total,
AGN 001/2024 Art. 4.3.2.6).

Solo la ruta INTERNA (D-02, `X-Internal-Token`) existe: la ruta pública
equivalente se retiró (remediación de auditoría de seguridad, Media) --
storage-service no modela la disposición TRD y no puede validar que un
artefacto sea realmente de una serie CT, así que exponerla públicamente
permitía a cualquier portador de `USUA_PERM_EXPEDIENTE min_crud=3` fijar el
lock COMPLIANCE de un artefacto arbitrario hasta el techo de
`preservation_max_retention_years`, sin importar su disposición real. Único
llamador: la 5ª fase de reconciliación de archive-service
(`run_once_worm_renovacion`), que actúa con `SYSTEM_RECONCILER_ID`.

Ningún test escribe una extensión COMPLIANCE real — MinIO está completamente
mockeado (`tests/conftest.py::_make_mock_minio`, `set_object_retention` en
particular); solo se aserta la LLAMADA (o su AUSENCIA, en el camino no-op) y
sus argumentos. Espejo de `test_preservacion_worm.py` (Increment A/protección
inicial) para el eje de renovación.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

import app.core.config as config_module
from app.core.constants import SYSTEM_RECONCILER_ID
from tests.conftest import INTERNAL_HEADERS, TENANT_HEADERS, TENANT_SLUG, make_worm_row


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)


def _renovar_payload(*, expediente_id: str = "EXP-1", tipo: str = "indice", **overrides) -> dict:
    payload = {
        "expediente_id": expediente_id,
        "tipo": tipo,
        "retencion_anios": 5,
        "fecha_inicio": "2026-01-01",  # -> nueva resuelta = 2031-01-01
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Camino de EXTENSIÓN real: nueva > actual.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_retencion_extends_when_nueva_is_strictly_greater(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-1",
        tipo="indice",
        retain_until=datetime(2028, 1, 1, tzinfo=UTC),  # < nueva (2031-01-01)
        bucket="orpycamcp-demo-preservation",
        object_key="EXP-1/indice.xml",
        version_id="v1",
        retention_mode="COMPLIANCE",
    )
    renovada = dict(existing)
    renovada["retain_until"] = datetime(2031, 1, 1, tzinfo=UTC)
    renovada["renovaciones"] = existing["renovaciones"] + 1
    renovada["renovado_at"] = datetime.now(tz=UTC)

    mock_conn.fetchrow.side_effect = [
        existing,  # get_by_expediente_and_tipo
        renovada,  # renovar (UPDATE ... RETURNING)
        {"id": uuid.uuid4()},  # insert_evento RENOVACION ok
    ]

    resp = await client.post(
        "/api/v1/preservacion/renovar-retencion-internal",
        json=_renovar_payload(),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["renovado"] is True
    assert body["renovaciones"] == existing["renovaciones"] + 1
    assert body["retain_until"].startswith("2031-01-01")

    # MinIO SÍ fue llamado: COMPLIANCE en sí mismo también rechazaría acortar,
    # pero el servicio ya filtra antes de llegar aquí (ver el test no-op).
    mock_minio.set_object_retention.assert_awaited_once()
    call = mock_minio.set_object_retention.await_args
    assert call.args[0] == existing["bucket"]
    assert call.args[1] == existing["object_key"]
    retention_arg = call.args[2]
    # `Retention` (miniopy_async) expone atributos privados `_mode`/
    # `_retain_until_date` -- reusado de la fila (`existing["retention_mode"]`),
    # NUNCA del payload (`RenovarRetencionRequest` ni siquiera lo declara).
    assert retention_arg._mode == "COMPLIANCE"
    assert retention_arg._retain_until_date == datetime(2031, 1, 1, tzinfo=UTC)
    assert call.kwargs["version_id"] == existing["version_id"]

    audit_calls = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0].args[5] == "file.worm_retencion_renovada"
    payload = json.loads(audit_calls[0].args[8])
    assert payload["retain_until_anterior"] == existing["retain_until"].isoformat()
    assert payload["retain_until_nuevo"] == "2031-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Camino NO-OP idempotente: nueva <= actual -- MinIO NO se toca.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_retencion_noop_when_nueva_does_not_exceed_actual(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-1",
        tipo="indice",
        retain_until=datetime(2032, 1, 1, tzinfo=UTC),  # > nueva (2031-01-01)
    )

    mock_conn.fetchrow.side_effect = [existing]

    resp = await client.post(
        "/api/v1/preservacion/renovar-retencion-internal",
        json=_renovar_payload(),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["renovado"] is False
    assert body["retain_until"] == "2032-01-01T00:00:00Z"  # SIN tocar

    mock_minio.set_object_retention.assert_not_awaited()
    # No-op: ni evento ni asiento -- ninguna extensión real ocurrió.
    audit_calls = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 0


@pytest.mark.asyncio
async def test_renovar_retencion_noop_when_nueva_equals_actual_exactly(client_with_conn) -> None:
    """Frontera: `nueva == actual` (ni mayor ni menor) también es no-op --
    la comparación es ESTRICTAMENTE mayor (`nueva <= existing`), no
    `nueva < existing`."""
    client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-1",
        tipo="indice",
        retain_until=datetime(2031, 1, 1, tzinfo=UTC),  # == nueva
    )

    mock_conn.fetchrow.side_effect = [existing]

    resp = await client.post(
        "/api/v1/preservacion/renovar-retencion-internal",
        json=_renovar_payload(),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["renovado"] is False
    mock_minio.set_object_retention.assert_not_awaited()


# ---------------------------------------------------------------------------
# Sin fila WORM protegida -- 404.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_retencion_404_when_no_worm_object_exists(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn

    mock_conn.fetchrow.side_effect = [None]  # get_by_expediente_and_tipo: nada

    resp = await client.post(
        "/api/v1/preservacion/renovar-retencion-internal",
        json=_renovar_payload(expediente_id="EXP-NUNCA-PROTEGIDO"),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "worm_object_not_found"
    mock_minio.set_object_retention.assert_not_awaited()


# ---------------------------------------------------------------------------
# Retención inválida (piso/techo) -- 400, ANTES de tocar BD/MinIO.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_retencion_rejects_retention_below_floor(client_with_conn) -> None:
    """Un `retain_until` directo que ya pasó (piso de seguridad WORM,
    `preservation_min_retention_days`) se rechaza ANTES de tocar BD/MinIO --
    mismo criterio que `proteger_indice`. `resolve_retain_until()`/
    `validate_retain_until()` corren ANTES de adquirir el advisory lock, así
    que ni siquiera se llega a `get_by_expediente_and_tipo`."""
    client, mock_conn, mock_minio = client_with_conn

    resp = await client.post(
        "/api/v1/preservacion/renovar-retencion-internal",
        json={
            "expediente_id": "EXP-1",
            "tipo": "indice",
            "retain_until": "2020-01-01T00:00:00Z",  # pasado: NUNCA válido
        },
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_retention"
    mock_conn.fetchrow.assert_not_called()
    mock_minio.set_object_retention.assert_not_awaited()


# ---------------------------------------------------------------------------
# Kill-switch: PRESERVATION_ENABLED=false -- 503, ANTES de tocar BD.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_retencion_disabled_by_feature_flag(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    monkeypatch.setattr(config_module.settings, "preservation_enabled", False)

    resp = await client.post(
        "/api/v1/preservacion/renovar-retencion-internal",
        json=_renovar_payload(),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "preservation_disabled"
    mock_conn.fetchrow.assert_not_called()


# ---------------------------------------------------------------------------
# Defensa en profundidad: `renovar()` no afecta ninguna fila bajo el lock de
# sesión (no debería ocurrir en la práctica) -- se relee el estado real en
# vez de asumir que la extensión ya confirmada en MinIO quedó en BD.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_retencion_rereads_real_state_if_update_matched_no_row(
    client_with_conn,
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-1", tipo="indice", retain_until=datetime(2028, 1, 1, tzinfo=UTC)
    )
    refreshed = dict(existing)  # estado real releído -- idéntico al leído antes

    mock_conn.fetchrow.side_effect = [
        existing,  # get_by_expediente_and_tipo
        None,  # renovar() -- WHERE no casó ninguna fila (defensa en profundidad)
        refreshed,  # re-lectura de get_by_expediente_and_tipo
    ]

    resp = await client.post(
        "/api/v1/preservacion/renovar-retencion-internal",
        json=_renovar_payload(),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["renovado"] is False  # NO se asume la extensión

    mock_minio.set_object_retention.assert_awaited_once()  # SÍ se llegó a llamar a MinIO
    audit_calls = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 0  # sin asiento fantasma sobre un efecto no confirmado en BD


# ---------------------------------------------------------------------------
# Ruta interna (D-02, SYSTEM_RECONCILER_ID) -- disparador real: 5ª fase de
# reconciliación de archive-service.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_retencion_internal_happy_path(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-CT-1",
        tipo="acta_transferencia",
        retain_until=datetime(2028, 1, 1, tzinfo=UTC),
    )
    renovada = dict(existing)
    renovada["retain_until"] = datetime(2031, 1, 1, tzinfo=UTC)
    renovada["renovaciones"] = existing["renovaciones"] + 1

    mock_conn.fetchrow.side_effect = [existing, renovada, {"id": uuid.uuid4()}]

    resp = await client.post(
        "/api/v1/preservacion/renovar-retencion-internal",
        json=_renovar_payload(expediente_id="EXP-CT-1", tipo="acta_transferencia"),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["renovado"] is True

    audit_calls = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0].args[3] == str(SYSTEM_RECONCILER_ID)  # actor SIEMPRE el sistema


@pytest.mark.asyncio
async def test_renovar_retencion_internal_happy_path_tipo_aip(client_with_conn) -> None:
    """Gemelo EXACTO del test anterior para `tipo='aip'` (E10 debt,
    fast-follow del cableado del AIP al eje de renovación WORM,
    archive-service migración 034) -- `PreservacionWormRepository.
    get_by_expediente_and_tipo`/`renovar` son GENÉRICOS por `(expediente_id,
    tipo)` desde Increment A (migración 009): ningún cambio de esquema fue
    necesario en storage-service, solo que `RenovarRetencionRequest.tipo`
    (schema Pydantic) acepte el literal `'aip'` -- este test confirma que la
    fila WORM del AIP se identifica y extiende exactamente igual que la del
    índice/acta."""
    client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-CT-AIP-1",
        tipo="aip",
        retain_until=datetime(2028, 1, 1, tzinfo=UTC),
    )
    renovada = dict(existing)
    renovada["retain_until"] = datetime(2031, 1, 1, tzinfo=UTC)
    renovada["renovaciones"] = existing["renovaciones"] + 1

    mock_conn.fetchrow.side_effect = [existing, renovada, {"id": uuid.uuid4()}]

    resp = await client.post(
        "/api/v1/preservacion/renovar-retencion-internal",
        json=_renovar_payload(expediente_id="EXP-CT-AIP-1", tipo="aip"),
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["renovado"] is True
    assert body["retain_until"].startswith("2031-01-01")

    # MinIO fue llamado sobre el objeto WORM del AIP identificado (bucket/key
    # de LA FILA encontrada, no un valor inventado del payload).
    mock_minio.set_object_retention.assert_awaited_once()
    call = mock_minio.set_object_retention.await_args
    assert call.args[0] == existing["bucket"]
    assert call.args[1] == existing["object_key"]

    audit_calls = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0].args[3] == str(SYSTEM_RECONCILER_ID)  # actor SIEMPRE el sistema


@pytest.mark.asyncio
async def test_renovar_retencion_internal_rejects_without_token(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn

    resp = await client.post(
        "/api/v1/preservacion/renovar-retencion-internal",
        json=_renovar_payload(),
        headers=TENANT_HEADERS,  # sin X-Internal-Token
    )

    assert resp.status_code == 401
    mock_conn.fetchrow.assert_not_called()


# ---------------------------------------------------------------------------
# Advisory lock de sesión reusado (MISMO namespace que `proteger_indice`,
# `_worm_lock_key`) -- serializa una renovación concurrente con la
# protección inicial o con otra renovación del MISMO artefacto.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_retencion_acquires_and_releases_advisory_lock(client_with_conn) -> None:
    client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-1", tipo="indice", retain_until=datetime(2028, 1, 1, tzinfo=UTC)
    )
    renovada = dict(existing)
    renovada["retain_until"] = datetime(2031, 1, 1, tzinfo=UTC)

    mock_conn.fetchrow.side_effect = [existing, renovada, {"id": uuid.uuid4()}]

    resp = await client.post(
        "/api/v1/preservacion/renovar-retencion-internal",
        json=_renovar_payload(),
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 200, resp.text

    lock_calls = [
        c for c in mock_conn.execute.call_args_list if c.args and "pg_advisory_lock" in c.args[0]
    ]
    unlock_calls = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "pg_advisory_unlock" in c.args[0]
    ]
    assert len(lock_calls) == 1
    assert len(unlock_calls) == 1
    assert lock_calls[0].args[1] == unlock_calls[0].args[1]


@pytest.mark.asyncio
async def test_renovar_retencion_lock_key_matches_proteger_indice_lock_key(
    client_with_conn,
) -> None:
    """El candado de renovación es el MISMO `(tenant, tipo, expediente)` que
    `proteger_indice`/`proteger_artefacto` -- no un namespace propio -- así
    una renovación y una protección/renovación concurrentes del MISMO
    artefacto se serializan entre sí."""
    from app.services.preservation_worm_service import _worm_lock_key

    client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-9", tipo="indice", retain_until=datetime(2028, 1, 1, tzinfo=UTC)
    )
    renovada = dict(existing)
    renovada["retain_until"] = datetime(2031, 1, 1, tzinfo=UTC)

    mock_conn.fetchrow.side_effect = [existing, renovada, {"id": uuid.uuid4()}]

    resp = await client.post(
        "/api/v1/preservacion/renovar-retencion-internal",
        json=_renovar_payload(expediente_id="EXP-9", tipo="indice"),
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 200, resp.text

    lock_calls = [
        c for c in mock_conn.execute.call_args_list if c.args and "pg_advisory_lock" in c.args[0]
    ]
    assert len(lock_calls) == 1
    assert lock_calls[0].args[1] == _worm_lock_key(TENANT_SLUG, "EXP-9", "indice")


# ---------------------------------------------------------------------------
# Regresión: la ruta PÚBLICA (`/renovar-retencion`, sin `-internal`) fue
# retirada -- delta de remediación bajo prueba en esta iteración.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_renovar_retencion_route_no_longer_exists(client_with_conn) -> None:
    """Regresión: la ruta PÚBLICA `/renovar-retencion` (sin `-internal`) fue
    retirada (remediación de auditoría de seguridad, Media, ver docstring
    del módulo) -- si algún día reapareciera por accidente (merge, copy-paste
    de `/proteger-indice` + `/proteger-indice-internal`), este test debe
    fallar en vez de dejar pasar en silencio una superficie pública que
    permite fijar el lock COMPLIANCE de un artefacto arbitrario."""
    client, mock_conn, mock_minio = client_with_conn

    resp = await client.post(
        "/api/v1/preservacion/renovar-retencion",
        json=_renovar_payload(),
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 404
    mock_conn.fetchrow.assert_not_called()
    mock_minio.set_object_retention.assert_not_awaited()


# ---------------------------------------------------------------------------
# Atomicidad (remediación de auditoría de seguridad, Baja): `renovar()` +
# `insert_evento` PREMIS + `audit.append` deben comprometerse JUNTOS dentro
# de `async with self._conn.transaction():`. storage-service NO tiene tier de
# integración (mismo hueco heredado de 009/010/011, ver memoria del
# proyecto) -- un mock no puede demostrar el ROLLBACK físico, pero SÍ puede
# demostrar (a) que un fallo a mitad de la tx NO se traga silenciosamente
# (no hay `try/except` que lo convierta en un 200 con `renovado=True`
# fantasma) y (b) que el advisory lock de sesión se libera IGUAL pese al
# fallo -- el `finally` de `renovar_retencion` no depende de que la tx haya
# tenido éxito.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_retencion_evt_insert_failure_propagates_and_releases_lock(
    client_with_conn,
) -> None:
    client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-1", tipo="indice", retain_until=datetime(2028, 1, 1, tzinfo=UTC)
    )
    renovada = dict(existing)
    renovada["retain_until"] = datetime(2031, 1, 1, tzinfo=UTC)

    mock_conn.fetchrow.side_effect = [
        existing,  # get_by_expediente_and_tipo
        renovada,  # renovar (UPDATE ... RETURNING)
        RuntimeError("boom: insert_evento falla dentro de la tx"),
    ]

    with pytest.raises(RuntimeError, match="boom"):
        await client.post(
            "/api/v1/preservacion/renovar-retencion-internal",
            json=_renovar_payload(),
            headers=INTERNAL_HEADERS,
        )

    # Ningún asiento fantasma: `audit.append` nunca se alcanza si
    # `insert_evento` reventó antes dentro del MISMO bloque de tx.
    audit_calls = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 0

    unlock_calls = [
        c for c in mock_conn.execute.call_args_list if c.args and "pg_advisory_unlock" in c.args[0]
    ]
    assert len(unlock_calls) == 1


@pytest.mark.asyncio
async def test_renovar_retencion_audit_append_failure_propagates_and_releases_lock(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gemelo del anterior para el ÚLTIMO paso de la tx (`audit.append`, tras
    `renovar()` + `insert_evento` ya exitosos): un fallo ahí tampoco debe
    traducirse en un 200 -- el asiento de auditoría es parte del MISMO acto
    atómico, no un best-effort colgado al final del método."""
    client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-1", tipo="indice", retain_until=datetime(2028, 1, 1, tzinfo=UTC)
    )
    renovada = dict(existing)
    renovada["retain_until"] = datetime(2031, 1, 1, tzinfo=UTC)

    mock_conn.fetchrow.side_effect = [existing, renovada, {"id": uuid.uuid4()}]

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("boom: audit.append falla dentro de la tx")

    monkeypatch.setattr("app.services.preservation_worm_service.audit.append", _boom)

    with pytest.raises(RuntimeError, match="boom"):
        await client.post(
            "/api/v1/preservacion/renovar-retencion-internal",
            json=_renovar_payload(),
            headers=INTERNAL_HEADERS,
        )

    unlock_calls = [
        c for c in mock_conn.execute.call_args_list if c.args and "pg_advisory_unlock" in c.args[0]
    ]
    assert len(unlock_calls) == 1


# ---------------------------------------------------------------------------
# Migración 011 (idempotencia + columnas) -- storage-service NO tiene tier de
# integración contra Postgres real (mismo hueco heredado que la 009/010, ver
# memoria del proyecto) -- chequeo ESTÁTICO del SQL.
# ---------------------------------------------------------------------------

_MIGRATION_011_PATH = (
    Path(__file__).resolve().parent.parent / "migrations" / "tenant" / "011_preservacion_worm_renovacion.sql"
)


def test_migration_011_is_additive_and_idempotent_by_construction() -> None:
    """`ADD COLUMN IF NOT EXISTS` para ambas columnas nuevas -- re-ejecutar
    la migración sobre un schema ya migrado no debe fallar. Chequeo estático
    (sin abrir una conexión real, el run mandatado usa `--no-deps`) — mismo
    patrón que `test_aip_pdfa.py::test_migration_008_check_constraint_admits_no_evaluado`."""
    sql = _MIGRATION_011_PATH.read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS renovado_at" in sql
    assert "ADD COLUMN IF NOT EXISTS renovaciones" in sql
    assert "ALTER TABLE preservacion_worm_objeto" in sql
    # `renovaciones` arranca en 0 (nunca NULL) -- el repositorio hace
    # `renovaciones + 1` en el UPDATE, una fila NULL rompería esa aritmética.
    assert "renovaciones  INT NOT NULL DEFAULT 0" in sql or "renovaciones INT NOT NULL DEFAULT 0" in sql
