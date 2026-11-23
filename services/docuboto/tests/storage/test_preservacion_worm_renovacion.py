# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de la renovación de retención WORM (E10 debt: series de disposición
Conservación Total, AGN 001/2024 Art. 4.3.2.6).

Sin ruta HTTP (ADR-047): archive la pide con `storage.api.renew_retention`, que
envuelve `PreservationWormService.renovar_retencion` con actor
`SYSTEM_RECONCILER_ID`. Los tests que miran campos de la respuesta llaman al
servicio; los del actor y del mapeo a `WormRenewalOutcome`, a `storage.api`.

Ningún test escribe una extensión COMPLIANCE real — MinIO está completamente
mockeado (`tests/conftest.py::_make_mock_minio`, `set_object_retention` en
particular); solo se aserta la LLAMADA (o su AUSENCIA, en el camino no-op) y
sus argumentos. Espejo de `test_preservacion_worm.py` (Increment A/protección
inicial) para el eje de renovación.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from docuboto import config as config_module
from storage import api as storage_api
from storage.core.constants import SYSTEM_RECONCILER_ID
from storage.core.preservacion_tipos import MULTI_ARTEFACTO_TIPOS
from storage.schemas.preservacion import RenovarRetencionRequest, RenovarRetencionResponse
from storage.services.preservation_worm_service import (
    InvalidRetentionError,
    PreservationWormService,
)
from tests.storage.conftest import TENANT_SLUG, make_worm_row


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.storage, "preservation_enabled", True)


def _renovar_payload(*, expediente_id: str = "EXP-1", tipo: str = "indice", **overrides) -> dict:
    payload = {
        "expediente_id": expediente_id,
        "tipo": tipo,
        "retencion_anios": 5,
        "fecha_inicio": "2026-01-01",  # -> nueva resuelta = 2031-01-01
    }
    payload.update(overrides)
    return payload


async def _renew(conn, payload: dict) -> RenovarRetencionResponse:
    """Lo que hacía la ruta interna: el servicio con actor de sistema."""
    return await PreservationWormService(conn, TENANT_SLUG).renovar_retencion(
        RenovarRetencionRequest(**payload), actor=str(SYSTEM_RECONCILER_ID)
    )


async def _renew_via_api(**overrides) -> storage_api.WormRenewalOutcome:
    payload = _renovar_payload(**overrides)
    return await storage_api.renew_retention(
        tenant_slug=TENANT_SLUG,
        expediente_id=payload["expediente_id"],
        tipo=payload["tipo"],
        retencion_anios=payload["retencion_anios"],
        fecha_inicio=datetime.fromisoformat(payload["fecha_inicio"]).date(),
        file_id=payload.get("file_id"),
    )


# ---------------------------------------------------------------------------
# Camino de EXTENSIÓN real: nueva > actual.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_retencion_extends_when_nueva_is_strictly_greater(client_with_conn) -> None:
    _client, mock_conn, mock_minio = client_with_conn
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

    body = await _renew(mock_conn, _renovar_payload())

    assert body.renovado is True
    assert body.renovaciones == existing["renovaciones"] + 1
    assert body.retain_until == datetime(2031, 1, 1, tzinfo=UTC)

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
    _client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-1",
        tipo="indice",
        retain_until=datetime(2032, 1, 1, tzinfo=UTC),  # > nueva (2031-01-01)
    )

    mock_conn.fetchrow.side_effect = [existing]

    body = await _renew(mock_conn, _renovar_payload())

    assert body.renovado is False
    assert body.retain_until == datetime(2032, 1, 1, tzinfo=UTC)  # SIN tocar

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
    _client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-1",
        tipo="indice",
        retain_until=datetime(2031, 1, 1, tzinfo=UTC),  # == nueva
    )

    mock_conn.fetchrow.side_effect = [existing]

    body = await _renew(mock_conn, _renovar_payload())

    assert body.renovado is False
    mock_minio.set_object_retention.assert_not_awaited()


# ---------------------------------------------------------------------------
# Sin fila WORM protegida -- `not_found`.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_retencion_not_found_when_no_worm_object_exists(client_with_conn) -> None:
    _client, mock_conn, mock_minio = client_with_conn

    mock_conn.fetchrow.side_effect = [None]  # get_by_expediente_and_tipo: nada

    outcome = await _renew_via_api(expediente_id="EXP-NUNCA-PROTEGIDO")

    assert outcome == storage_api.WormRenewalOutcome(ok=False, not_found=True)
    mock_minio.set_object_retention.assert_not_awaited()


# ---------------------------------------------------------------------------
# Retención inválida (piso/techo) -- se rechaza ANTES de tocar BD/MinIO.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_retencion_rejects_retention_below_floor(client_with_conn) -> None:
    """Un `retain_until` directo que ya pasó (piso de seguridad WORM,
    `preservation_min_retention_days`) se rechaza ANTES de tocar BD/MinIO --
    mismo criterio que `proteger_indice`. `resolve_retain_until()`/
    `validate_retain_until()` corren ANTES de adquirir el advisory lock, así
    que ni siquiera se llega a `get_by_expediente_and_tipo`."""
    _client, mock_conn, mock_minio = client_with_conn

    with pytest.raises(InvalidRetentionError):
        await _renew(
            mock_conn,
            {
                "expediente_id": "EXP-1",
                "tipo": "indice",
                "retain_until": "2020-01-01T00:00:00Z",  # pasado: NUNCA válido
            },
        )

    mock_conn.fetchrow.assert_not_called()
    mock_minio.set_object_retention.assert_not_awaited()


# ---------------------------------------------------------------------------
# Kill-switch: PRESERVATION_ENABLED=false -- `disabled`, ANTES de tocar BD.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_retencion_disabled_by_feature_flag(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    _client, mock_conn, mock_minio = client_with_conn
    monkeypatch.setattr(config_module.storage, "preservation_enabled", False)

    outcome = await _renew_via_api()

    assert outcome == storage_api.WormRenewalOutcome(ok=False, disabled=True)
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
    _client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-1", tipo="indice", retain_until=datetime(2028, 1, 1, tzinfo=UTC)
    )
    refreshed = dict(existing)  # estado real releído -- idéntico al leído antes

    mock_conn.fetchrow.side_effect = [
        existing,  # get_by_expediente_and_tipo
        None,  # renovar() -- WHERE no casó ninguna fila (defensa en profundidad)
        refreshed,  # re-lectura de get_by_expediente_and_tipo
    ]

    body = await _renew(mock_conn, _renovar_payload())

    assert body.renovado is False  # NO se asume la extensión

    mock_minio.set_object_retention.assert_awaited_once()  # SÍ se llegó a llamar a MinIO
    audit_calls = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 0  # sin asiento fantasma sobre un efecto no confirmado en BD


# ---------------------------------------------------------------------------
# `storage.api.renew_retention` (SYSTEM_RECONCILER_ID) -- disparador real: 5ª
# fase de reconciliación de archive.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renew_retention_api_happy_path(client_with_conn) -> None:
    _client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-CT-1",
        tipo="acta_transferencia",
        retain_until=datetime(2028, 1, 1, tzinfo=UTC),
    )
    renovada = dict(existing)
    renovada["retain_until"] = datetime(2031, 1, 1, tzinfo=UTC)
    renovada["renovaciones"] = existing["renovaciones"] + 1

    mock_conn.fetchrow.side_effect = [existing, renovada, {"id": uuid.uuid4()}]

    outcome = await _renew_via_api(expediente_id="EXP-CT-1", tipo="acta_transferencia")

    assert outcome.ok is True
    assert outcome.renovado is True
    assert outcome.retain_until == datetime(2031, 1, 1, tzinfo=UTC)

    audit_calls = [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0].args[3] == str(SYSTEM_RECONCILER_ID)  # actor SIEMPRE el sistema


@pytest.mark.asyncio
async def test_renew_retention_api_happy_path_tipo_aip(client_with_conn) -> None:
    """Gemelo EXACTO del test anterior para `tipo='aip'` (E10 debt,
    fast-follow del cableado del AIP al eje de renovación WORM,
    archive-service migración 034) -- `PreservacionWormRepository.
    get_by_expediente_and_tipo`/`renovar` son GENÉRICOS por `(expediente_id,
    tipo)` desde Increment A (migración 009): ningún cambio de esquema fue
    necesario en storage-service, solo que `RenovarRetencionRequest.tipo`
    (schema Pydantic) acepte el literal `'aip'` -- este test confirma que la
    fila WORM del AIP se identifica y extiende exactamente igual que la del
    índice/acta."""
    _client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-CT-AIP-1",
        tipo="aip",
        retain_until=datetime(2028, 1, 1, tzinfo=UTC),
    )
    renovada = dict(existing)
    renovada["retain_until"] = datetime(2031, 1, 1, tzinfo=UTC)
    renovada["renovaciones"] = existing["renovaciones"] + 1

    mock_conn.fetchrow.side_effect = [existing, renovada, {"id": uuid.uuid4()}]

    outcome = await _renew_via_api(expediente_id="EXP-CT-AIP-1", tipo="aip")

    assert outcome.ok is True
    assert outcome.renovado is True
    assert outcome.retain_until == datetime(2031, 1, 1, tzinfo=UTC)

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
@pytest.mark.parametrize("rol", ["elabora", "entrega", "recibe"])
@pytest.mark.parametrize(
    "prefijo",
    [
        "acta_firma_",
        "acta_firma_excepcion_",
        "acta_firma_excepcion_personal_",
        "acta_firma_revocacion_",
        "acta_firma_revocacion_personal_",
        "acta_firma_excepcion_sobre_",
        "acta_firma_revocacion_sobre_",
    ],
)
async def test_renew_retention_api_tipo_acta_firma_personal(client_with_conn, rol, prefijo) -> None:
    """E10 debt (AREA-07-10): el XML de cada firma PERSONAL del acta (`acta_firma_<rol>`,
    migración 018, singleton por (expediente, tipo)) se renueva igual que el índice: la fila
    se identifica por `(expediente_id, tipo)`, la extensión es monotónica y el objeto es el de
    LA FILA (no un valor del payload). Sin cambio de servicio ni de repositorio."""
    _client, mock_conn, mock_minio = client_with_conn
    tipo = f"{prefijo}{rol}"
    existing = make_worm_row(
        expediente_id="EXP-CT-FP-1",
        tipo=tipo,
        retain_until=datetime(2028, 1, 1, tzinfo=UTC),
    )
    renovada = dict(existing)
    renovada["retain_until"] = datetime(2031, 1, 1, tzinfo=UTC)
    renovada["renovaciones"] = existing["renovaciones"] + 1
    mock_conn.fetchrow.side_effect = [existing, renovada, {"id": uuid.uuid4()}]
    file_id = uuid.uuid4()
    multi = tipo in MULTI_ARTEFACTO_TIPOS
    extra = {"file_id": str(file_id)} if multi else {}

    outcome = await _renew_via_api(expediente_id="EXP-CT-FP-1", tipo=tipo, **extra)

    assert outcome.ok is True
    assert outcome.renovado is True
    lookup = next(c for c in mock_conn.fetchrow.call_args_list if "FROM preservacion_worm_objeto" in c.args[0])
    # FIRMA-2: los tipos multi-artefacto se buscan POR file_id; el resto, por (expediente, tipo).
    assert lookup.args[1:] == (("EXP-CT-FP-1", tipo, file_id) if multi else ("EXP-CT-FP-1", tipo))
    mock_minio.set_object_retention.assert_awaited_once()
    assert mock_minio.set_object_retention.await_args.args[1] == existing["object_key"]
    audit_calls = [
        c for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1 and audit_calls[0].args[3] == str(SYSTEM_RECONCILER_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", sorted(MULTI_ARTEFACTO_TIPOS))
async def test_renovar_retencion_multi_artefacto_exige_file_id(client_with_conn, tipo) -> None:
    """FIRMA-2: con N artefactos por (expediente, tipo), renovar sin `file_id` extendería solo
    el más reciente. El esquema lo rechaza y ni siquiera se consulta la base."""
    _client, mock_conn, mock_minio = client_with_conn
    with pytest.raises(ValidationError, match="file_id"):
        RenovarRetencionRequest(**_renovar_payload(expediente_id="EXP-CT-FP-3", tipo=tipo))

    outcome = await _renew_via_api(expediente_id="EXP-CT-FP-3", tipo=tipo)
    assert outcome.ok is False
    mock_conn.fetchrow.assert_not_called()
    mock_minio.set_object_retention.assert_not_awaited()


@pytest.mark.asyncio
async def test_renew_retention_api_acta_firma_no_acorta_si_nueva_no_supera(client_with_conn) -> None:
    """Monotonía: con la fecha resuelta (2031) por debajo de la vigente (2040) es no-op
    idempotente y NO se llama a MinIO."""
    _client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-CT-FP-2",
        tipo="acta_firma_recibe",
        retain_until=datetime(2040, 1, 1, tzinfo=UTC),
    )
    mock_conn.fetchrow.side_effect = [existing]

    outcome = await _renew_via_api(expediente_id="EXP-CT-FP-2", tipo="acta_firma_recibe")

    assert outcome.ok is True
    assert outcome.renovado is False
    assert outcome.retain_until == datetime(2040, 1, 1, tzinfo=UTC)
    mock_minio.set_object_retention.assert_not_awaited()


def test_renovar_retencion_rechaza_un_tipo_de_acta_inventado() -> None:
    with pytest.raises(ValidationError):
        RenovarRetencionRequest(**_renovar_payload(tipo="acta_firma_notario"))


# ---------------------------------------------------------------------------
# Advisory lock de sesión reusado (MISMO namespace que `proteger_indice`,
# `_worm_lock_key`) -- serializa una renovación concurrente con la
# protección inicial o con otra renovación del MISMO artefacto.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_retencion_acquires_and_releases_advisory_lock(client_with_conn) -> None:
    _client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-1", tipo="indice", retain_until=datetime(2028, 1, 1, tzinfo=UTC)
    )
    renovada = dict(existing)
    renovada["retain_until"] = datetime(2031, 1, 1, tzinfo=UTC)

    mock_conn.fetchrow.side_effect = [existing, renovada, {"id": uuid.uuid4()}]

    await _renew(mock_conn, _renovar_payload())

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
    from storage.services.preservation_worm_service import _worm_lock_key

    _client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-9", tipo="indice", retain_until=datetime(2028, 1, 1, tzinfo=UTC)
    )
    renovada = dict(existing)
    renovada["retain_until"] = datetime(2031, 1, 1, tzinfo=UTC)

    mock_conn.fetchrow.side_effect = [existing, renovada, {"id": uuid.uuid4()}]

    await _renew(mock_conn, _renovar_payload(expediente_id="EXP-9", tipo="indice"))

    lock_calls = [
        c for c in mock_conn.execute.call_args_list if c.args and "pg_advisory_lock" in c.args[0]
    ]
    assert len(lock_calls) == 1
    assert lock_calls[0].args[1] == _worm_lock_key(TENANT_SLUG, "EXP-9", "indice")


# ---------------------------------------------------------------------------
# Atomicidad (remediación de auditoría de seguridad, Baja): `renovar()` +
# `insert_evento` PREMIS + `audit.append` deben comprometerse JUNTOS dentro
# de `async with self._conn.transaction():`. storage-service NO tiene tier de
# integración (mismo hueco heredado de 009/010/011, ver memoria del
# proyecto) -- un mock no puede demostrar el ROLLBACK físico, pero SÍ puede
# demostrar (a) que un fallo a mitad de la tx NO se traga silenciosamente
# (no hay `try/except` que lo convierta en `renovado=True`
# fantasma) y (b) que el advisory lock de sesión se libera IGUAL pese al
# fallo -- el `finally` de `renovar_retencion` no depende de que la tx haya
# tenido éxito.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_renovar_retencion_evt_insert_failure_propagates_and_releases_lock(
    client_with_conn,
) -> None:
    _client, mock_conn, mock_minio = client_with_conn
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
        await _renew(mock_conn, _renovar_payload())

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
    darse por renovado -- el asiento de auditoría es parte del MISMO acto
    atómico, no un best-effort colgado al final del método."""
    _client, mock_conn, mock_minio = client_with_conn
    existing = make_worm_row(
        expediente_id="EXP-1", tipo="indice", retain_until=datetime(2028, 1, 1, tzinfo=UTC)
    )
    renovada = dict(existing)
    renovada["retain_until"] = datetime(2031, 1, 1, tzinfo=UTC)

    mock_conn.fetchrow.side_effect = [existing, renovada, {"id": uuid.uuid4()}]

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("boom: audit.append falla dentro de la tx")

    monkeypatch.setattr("storage.services.preservation_worm_service.audit.append", _boom)

    with pytest.raises(RuntimeError, match="boom"):
        await _renew(mock_conn, _renovar_payload())

    unlock_calls = [
        c for c in mock_conn.execute.call_args_list if c.args and "pg_advisory_unlock" in c.args[0]
    ]
    assert len(unlock_calls) == 1


# ---------------------------------------------------------------------------
# Columnas de renovación -- chequeo ESTÁTICO del SQL de la cadena única
# (ADR-053); el carril unit corre con `--no-deps`, sin Postgres.
# ---------------------------------------------------------------------------

_PRESERVACION_SQL_PATH = (
    Path(__file__).resolve().parents[3] / "docuboto" / "migrations" / "tenant" / "320_preservacion.sql"
)


def test_schema_has_renovation_columns() -> None:
    """`preservacion_worm_objeto` lleva `renovado_at` y `renovaciones`. Chequeo estático
    (sin abrir una conexión real) — mismo patrón que
    `test_aip_pdfa.py::test_preservacion_evento_check_constraint_admits_no_evaluado`."""
    sql = _PRESERVACION_SQL_PATH.read_text(encoding="utf-8")
    table_match = re.search(r"CREATE TABLE preservacion_worm_objeto \((.*?)\n\);", sql, re.DOTALL)
    assert table_match, "no se encontró CREATE TABLE preservacion_worm_objeto en 320_preservacion.sql"
    table = table_match.group(1)

    assert re.search(r"^\s*renovado_at timestamp with time zone,$", table, re.MULTILINE)
    # `renovaciones` arranca en 0 (nunca NULL) -- el repositorio hace
    # `renovaciones + 1` en el UPDATE, una fila NULL rompería esa aritmética.
    assert re.search(r"^\s*renovaciones integer DEFAULT 0 NOT NULL,$", table, re.MULTILINE)
