# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de reclasificación de nivel_seguridad de radicado (E08, RF-SEG-08).

PATCH /api/v1/documents/{document_id}/security-level

Orden de llamadas a la conexión mockeada por request:

  fetchval #1 — check_permission("PERM_RECLASIFICAR")
  fetchrow #1 — resolve_caller_clearance (SELECT is_root, max_level): ANTES de la lectura,
                porque su resultado entra en el WHERE de la consulta bloqueante
  fetchrow #2 — get_security_context_for_update (... nivel_seguridad <= $2 FOR UPDATE)
  execute  #N — set_classification (UPDATE radicados) [solo si hay cambio efectivo]
  execute  #N — audit advisory lock (SELECT pg_advisory_xact_lock)  [solo si hay cambio]
  fetchval #2 — audit prev_hash (SELECT hash FROM audit_log)         [solo si hay cambio]
  execute  #N — audit INSERT INTO audit_log                          [solo si hay cambio]
  redis.xadd  — events.emit best-effort                              [solo si hay cambio]
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.conftest import TENANT_HEADERS

# Fixture IDs ----------------------------------------------------------------
DOC_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
TRACKING = "2024-DEMO-E-000001"


def _sec_ctx(
    nivel: int = 1,
    *,
    causal: str | None = None,
    fundamento: str | None = None,
    plazo: int | None = None,
    meses_generacion: int = 0,
    meses_clasificacion: int = 0,
) -> dict:
    """Simula la fila devuelta por get_security_context_for_update."""
    return {
        "clasificacion_norma": None,
        "meses_desde_generacion": meses_generacion,
        "meses_hasta_clasificacion": meses_clasificacion,
        "id": DOC_ID,
        "tracking_number": TRACKING,
        "nivel_seguridad": nivel,
        "clasificacion_causal": causal,
        "clasificacion_fundamento": fundamento,
        "clasificacion_plazo_meses": plazo,
    }


def _clearance_row(max_level: int = 1, is_root: bool = False) -> dict:
    """Simula la fila devuelta por resolve_caller_clearance."""
    return {"is_root": is_root, "max_level": max_level}


# ---------------------------------------------------------------------------
# Case 1 — 403 sin permiso PERM_RECLASIFICAR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reclassify_forbidden_without_permission(client_with_conn) -> None:
    """check_permission → None (fail-closed) → 403 forbidden."""
    client, mock_conn = client_with_conn
    # fetchval.return_value=None hace que check_permission devuelva False.
    mock_conn.fetchval.return_value = None

    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={"nivel_seguridad": 2, "motivo": "Motivo de prueba", "fundamento_juridico": "Art. 19 Ley 1712"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    # Tres `fetchval`, todas en la rama de denegación y ninguna en la lógica de
    # negocio del endpoint (que no llega a ejecutarse):
    #   1. `check_permission`;
    #   2. la traza best-effort del acceso denegado (RF-SEG/MoReq), que lee el
    #      `prev_hash` de `audit_log`;
    #   3. `nivel_efectivo`, que averigua si al llamante le falta el permiso o
    #      solo el NIVEL — son dos problemas con dos soluciones distintas, y el
    #      mensaje anterior los confundía. Solo se paga al denegar.
    assert mock_conn.fetchval.call_count == 3


# ---------------------------------------------------------------------------
# Case 2 — 404 cuando el radicado no existe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reclassify_404_when_radicado_not_found(client_with_conn) -> None:
    """get_security_context → None → DocumentNotFoundError → 404."""
    client, mock_conn = client_with_conn
    # check_permission pasa (fetchval default=1); get_security_context → None.
    mock_conn.fetchrow.return_value = None

    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={
            "nivel_seguridad": 2,
            "motivo": "Motivo de prueba",
            "fundamento_juridico": "Art. 19 Ley 1712",
            "causal_reserva": "art19_b_seguridad_publica",
            "plazo_reserva_meses": 12,
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "document_not_found"


# ---------------------------------------------------------------------------
# Case 3 — 404 neutro cuando clearance < nivel actual (no-read-up)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reclassify_neutral_404_when_clearance_below_current_level(
    client_with_conn,
) -> None:
    """clearance=1, nivel_actual=3 → 404 neutro (RF-SEG-08 no-read-up).

    El actor no puede distinguir "no existe" de "existe pero está por encima
    de su clearance" — ambos producen el mismo 404. El gate va en el `WHERE` de la
    consulta bloqueante: para el llamante la fila simplemente no vuelve (`None`), y
    NO es un 409 aunque el movimiento fuera una desclasificación (no revela).
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [1]
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=1),  # resolve_caller_clearance → clearance 1
        None,                         # get_security_context_for_update: 3 > 1 → sin fila
    ]

    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={"nivel_seguridad": 1, "motivo": "Motivo de prueba"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "document_not_found"
    # El clearance viaja a la consulta y el bloqueo con él: mutación 11 del diseño.
    consulta = mock_conn.fetchrow.await_args_list[1]
    assert "nivel_seguridad <= $2" in consulta.args[0]
    assert "FOR UPDATE" in consulta.args[0]
    assert consulta.args[2] == 1
    # Ningún execute de escritura debe haberse invocado.
    radicado_updates = [
        c for c in mock_conn.execute.call_args_list
        if "UPDATE radicados" in str(c.args[0])
    ]
    assert len(radicado_updates) == 0


# ---------------------------------------------------------------------------
# Case 4 — 403 cuando nuevo_nivel > clearance (no-write-up)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reclassify_403_when_nuevo_nivel_exceeds_clearance(
    client_with_conn,
) -> None:
    """clearance=2, nivel_actual=1, nuevo=3 → 403 reclassify_forbidden (no-write-up)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [1]   # check_permission passes
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=2), # resolve_caller_clearance → clearance 2 < nuevo 3
        _sec_ctx(nivel=1),           # get_security_context_for_update → nivel 1
    ]

    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={"nivel_seguridad": 3, "motivo": "Intento de over-clasificar", "fundamento_juridico": "Art. 18 Ley 1712", "causal_reserva": "art18_a_intimidad"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "reclassify_forbidden"


# ---------------------------------------------------------------------------
# Case 5 — Éxito: clearance 3, nivel actual 1 → nuevo 3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reclassify_success_updates_nivel_and_emits_event(
    client_with_redis,
) -> None:
    """clearance=3, nivel_actual=1, nuevo=3 → 200 con nivel_seguridad=3.

    Verifica:
    - set_classification (UPDATE radicados) invocado.
    - audit.append invocado con action="document.radicado_reclassified"
      y payload {nivel_anterior=1, nivel_nuevo=3, motivo}.
    - events.emit invocado (redis.xadd llamado una vez).
    """
    client, mock_conn, fake_redis = client_with_redis
    motivo = "Reclasificación por directivo"

    # fetchval: [check_permission(1=True), audit_prev_hash(None)]
    mock_conn.fetchval.side_effect = [1, None]
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=3),  # resolve_caller_clearance → clearance 3 ≥ nuevo 3
        _sec_ctx(nivel=1),            # get_security_context_for_update → nivel 1
    ]

    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={"nivel_seguridad": 3, "motivo": motivo, "fundamento_juridico": "Art. 18 Ley 1712", "causal_reserva": "art18_a_intimidad"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(DOC_ID)
    assert body["tracking_number"] == TRACKING
    assert body["nivel_seguridad"] == 3

    # --- el UPDATE de reclasificación (set_classification) se ejecutó ---
    radicado_updates = [
        c for c in mock_conn.execute.call_args_list
        if "UPDATE radicados" in str(c.args[0])
    ]
    assert len(radicado_updates) == 1

    # --- audit.append ejecutado con acción y payload correctos ---
    audit_inserts = [
        c for c in mock_conn.execute.call_args_list
        if "audit_log" in str(c.args[0]) and "INSERT" in str(c.args[0])
    ]
    assert len(audit_inserts) == 1
    # Posición del action en el INSERT: ($1=tenant, $2=service, $3=actor,
    # $4=canal, $5=action, $6=object_type, $7=object_ref, $8=payload, ...)
    assert audit_inserts[0].args[5] == "document.radicado_reclassified"
    audit_payload = json.loads(audit_inserts[0].args[8])
    assert audit_payload["nivel_anterior"] == 1
    assert audit_payload["nivel_nuevo"] == 3
    assert audit_payload["motivo"] == motivo

    # --- events.emit invocado (best-effort via redis.xadd) ---
    assert fake_redis.xadd.call_count == 1


# ---------------------------------------------------------------------------
# Case 6 — No-op idempotente: nuevo_nivel == nivel actual
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reclassify_noop_when_nivel_already_matches(client_with_redis) -> None:
    """nuevo_nivel == nivel_actual → 200 con estado actual, sin audit ni evento.

    Seguro para reintentos: la misma request múltiples veces no produce
    entradas duplicadas en audit_log ni eventos duplicados en Redis.
    """
    client, mock_conn, fake_redis = client_with_redis

    # Solo check_permission (fetchval #1). El audit_prev_hash (#2) NO se llama.
    mock_conn.fetchval.side_effect = [1]
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=3),  # resolve_caller_clearance → clearance 3
        _sec_ctx(
            nivel=2,
            causal="art19_a_defensa_seguridad_nacional",
            fundamento="Art. 19 Ley 1712",
            plazo=60,
        ),  # get_security_context_for_update → nivel 2, ya motivado igual
    ]

    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={
            "nivel_seguridad": 2,
            "motivo": "Reintento idempotente",
            "fundamento_juridico": "Art. 19 Ley 1712",
            "causal_reserva": "art19_a_defensa_seguridad_nacional",
            "plazo_reserva_meses": 60,
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    assert resp.json()["nivel_seguridad"] == 2

    # Ningún UPDATE a radicados.
    radicado_updates = [
        c for c in mock_conn.execute.call_args_list
        if "UPDATE radicados" in str(c.args[0])
    ]
    assert len(radicado_updates) == 0

    # Ningún INSERT en audit_log.
    audit_inserts = [
        c for c in mock_conn.execute.call_args_list
        if "audit_log" in str(c.args[0]) and "INSERT" in str(c.args[0])
    ]
    assert len(audit_inserts) == 0

    # Ningún evento emitido.
    assert fake_redis.xadd.call_count == 0


# ---------------------------------------------------------------------------
# Case 7 — emit best-effort: si Redis falla, la operación no revienta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reclassify_succeeds_even_when_redis_emit_fails(
    client_with_redis,
) -> None:
    """events.emit lanza excepción → la operación ya se cometió → 200 igual.

    El UPDATE y el audit son atómicos en BD; el evento Redis es best-effort.
    Una falla de Redis no debe deshacer ni enmascarar la reclasificación.
    """
    client, mock_conn, fake_redis = client_with_redis
    fake_redis.xadd.side_effect = ConnectionError("Redis unavailable")

    mock_conn.fetchval.side_effect = [1, None]
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=3),
        _sec_ctx(nivel=1),
    ]

    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={"nivel_seguridad": 3, "motivo": "Test best-effort", "fundamento_juridico": "Art. 18 Ley 1712", "causal_reserva": "art18_a_intimidad"},
        headers=TENANT_HEADERS,
    )

    # La falla de Redis no debe provocar un error HTTP.
    assert resp.status_code == 200
    assert resp.json()["nivel_seguridad"] == 3


# ---------------------------------------------------------------------------
# Schema — validación de payload (Pydantic)
# ---------------------------------------------------------------------------


def test_reclassify_request_rejects_nivel_out_of_range() -> None:
    """nivel_seguridad fuera de rango 1-3 → ValidationError."""
    from pydantic import ValidationError

    from app.schemas.document import ReclassifyRequest

    with pytest.raises(ValidationError):
        ReclassifyRequest(nivel_seguridad=0)

    with pytest.raises(ValidationError):
        ReclassifyRequest(nivel_seguridad=4)


def test_reclassify_request_accepts_valid_range() -> None:
    from app.schemas.document import ReclassifyRequest

    # Nivel 1 (desclasificar) no exige fundamento; nivel ≥ 2 sí (Ley 1712 art. 19/28).
    assert ReclassifyRequest(nivel_seguridad=1, motivo="Motivo").nivel_seguridad == 1
    for nivel, causal, plazo in (
        (2, "art19_b_seguridad_publica", 12),
        (3, "art18_c_secretos", None),
    ):
        req = ReclassifyRequest(
            nivel_seguridad=nivel,
            motivo="Motivo obligatorio",
            fundamento_juridico="Art. Ley 1712",
            causal_reserva=causal,
            plazo_reserva_meses=plazo,
        )
        assert req.nivel_seguridad == nivel


# ---------------------------------------------------------------------------
# Compliance #1 — motivo obligatorio (Ley 1712 art.19/28)
# ---------------------------------------------------------------------------


def test_reclassify_request_rejects_missing_motivo() -> None:
    """motivo ausente → ValidationError (Ley 1712 art.19/28: el acto debe ser motivado)."""
    from pydantic import ValidationError

    from app.schemas.document import ReclassifyRequest

    with pytest.raises(ValidationError):
        ReclassifyRequest(nivel_seguridad=2)  # motivo ausente


def test_reclassify_request_rejects_empty_motivo() -> None:
    """motivo vacío → ValidationError (min_length=1)."""
    from pydantic import ValidationError

    from app.schemas.document import ReclassifyRequest

    with pytest.raises(ValidationError):
        ReclassifyRequest(nivel_seguridad=2, motivo="")


@pytest.mark.asyncio
async def test_reclassify_422_when_motivo_absent(client_with_conn) -> None:
    """PATCH sin motivo → 422 Unprocessable Entity (validación Pydantic antes del auth)."""
    client, mock_conn = client_with_conn

    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={"nivel_seguridad": 2},  # motivo ausente
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Compliance #2 — campos opcionales de fundamentación jurídica en audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reclassify_compliance_fields_recorded_in_audit(
    client_with_redis,
) -> None:
    """causal_reserva, fundamento_juridico, plazo_reserva_meses → presentes en audit_log payload."""
    client, mock_conn, fake_redis = client_with_redis

    mock_conn.fetchval.side_effect = [1, None]
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=3),
        _sec_ctx(nivel=1),
    ]

    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={
            "nivel_seguridad": 2,
            "motivo": "Contiene información de seguridad nacional",
            "causal_reserva": "art19_a_defensa_seguridad_nacional",
            "fundamento_juridico": "Ley 1712/2014 art.19",
            "plazo_reserva_meses": 60,
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200

    audit_inserts = [
        c for c in mock_conn.execute.call_args_list
        if "audit_log" in str(c.args[0]) and "INSERT" in str(c.args[0])
    ]
    assert len(audit_inserts) == 1
    # Posición $8 = payload en el INSERT de audit_log (ver comentario en Case 5).
    audit_payload = json.loads(audit_inserts[0].args[8])
    assert audit_payload["causal_reserva"] == "art19_a_defensa_seguridad_nacional"
    assert audit_payload["fundamento_juridico"] == "Ley 1712/2014 art.19"
    assert audit_payload["plazo_reserva_meses"] == 60


def test_reclassify_request_compliance_fields_are_optional() -> None:
    """`causal_reserva` y `plazo` son opcionales; `fundamento_juridico` es
    OBLIGATORIO al clasificar (nivel ≥ 2, Ley 1712 art. 19/28) pero opcional al
    desclasificar (nivel 1)."""
    from pydantic import ValidationError

    from app.schemas.document import ReclassifyRequest

    # Nivel 1: fundamento opcional; causal/plazo opcionales.
    req = ReclassifyRequest(nivel_seguridad=1, motivo="Desclasificar")
    assert req.causal_reserva is None
    assert req.fundamento_juridico is None
    assert req.plazo_reserva_meses is None

    # Nivel ≥ 2 sin fundamento → ValidationError (motivación obligatoria).
    with pytest.raises(ValidationError):
        ReclassifyRequest(
            nivel_seguridad=2, motivo="Solo motivo, sin fundamento",
            causal_reserva="art19_b_seguridad_publica", plazo_reserva_meses=12,
        )

    # Nivel ≥ 2: la causal del catálogo es OBLIGATORIA (C12); el plazo, solo art. 19.
    with pytest.raises(ValidationError):
        ReclassifyRequest(nivel_seguridad=2, motivo="m", fundamento_juridico="Art. 19")


# ---------------------------------------------------------------------------
# Auditoría de conformidad/seguridad (2026-08-13): clasificar retira la publicación
#
# El acto de publicación cubrió la ruta de escritura y NO la de reclasificación:
# un radicado publicado y clasificado después conservaba `publicable = TRUE`
# latente —inofensivo mientras el recorte exige las dos condiciones— y la
# siguiente desclasificación, hecha con otro propósito, lo devolvía a los canales
# de cosecha sin acto, sin fundamento y sin asiento. Es el modo de fallo que la
# migración 019 dice querer evitar, entrando por la otra puerta.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clasificar_retira_la_publicacion(client_with_redis) -> None:
    client, mock_conn, _ = client_with_redis
    mock_conn.fetchval.side_effect = [1, None]
    mock_conn.fetchrow.side_effect = [_clearance_row(max_level=3), _sec_ctx(nivel=1)]

    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={
            "nivel_seguridad": 3,
            "motivo": "Reserva sobrevenida",
            "fundamento_juridico": "Art. 18 lit. a Ley 1712",
            "causal_reserva": "art18_a_intimidad",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    updates = [str(c.args[0]) for c in mock_conn.execute.call_args_list]
    clasificar = [q for q in updates if "clasificado_at = CASE" in q]
    assert clasificar, "no se ejecutó la clasificación"
    assert "publicable = FALSE" in clasificar[0], (
        "clasificar deja la marca de publicación latente: la próxima "
        "desclasificación republicaría sin que nadie lo decida"
    )


@pytest.mark.asyncio
async def test_desclasificar_no_republica(client_with_redis) -> None:
    """La otra mitad: al bajar a nivel público NO se enciende `publicable`.

    Sin esta comprobación, "arreglar" el fallo anterior encendiendo la marca al
    desclasificar produciría el mismo defecto con signo contrario.
    """
    client, mock_conn, _ = client_with_redis
    mock_conn.fetchval.side_effect = [1, None]
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=3),
        _sec_ctx(nivel=2, causal="art19_i_salud_publica", fundamento="F", plazo=12),
    ]

    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={"nivel_seguridad": 1, "motivo": "Vencimiento del plazo de reserva"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    updates = [str(c.args[0]) for c in mock_conn.execute.call_args_list]
    desclasificar = [q for q in updates if "clasificado_at = NULL" in q]
    assert desclasificar, "no se ejecutó la desclasificación"
    assert "publicable = TRUE" not in desclasificar[0], (
        "desclasificar no puede publicar: publicar es un acto propio"
    )


# ---------------------------------------------------------------------------
# Case 8 — reclasificar exige NIVEL DE ESCRITURA, no de consulta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reclasificar_exige_nivel_de_escritura(client_with_conn) -> None:
    """`PERM_RECLASIFICAR` con nivel de LECTURA no basta para reclasificar.

    El endpoint pedía el permiso con el `min_crud` por defecto —1, leer—, de
    modo que quien lo tuviera solo para consultar podía subir o bajar la reserva
    de un radicado oficial. Sus dos gemelos ya exigían 3 y su comentario dice
    por qué: `publicacion` usa el MISMO permiso con nivel de escritura porque
    publicar y clasificar son las dos caras de la misma competencia sobre el
    régimen de acceso; `disposition` se corrigió antes por lo mismo. Este era el
    tercer hermano y el único que se quedó en 1.

    Se comprueba por el nivel que el gate EXIGE, no por el que el usuario tiene:
    `check_permission` recibe `min_crud` como parámetro de la consulta, así que
    mirar ahí es mirar el contrato y no el escenario.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # denegado: solo interesa la llamada

    await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={
            "nivel_seguridad": 1,
            "motivo": "Desclasificar",
        },
        headers=TENANT_HEADERS,
    )

    llamada = mock_conn.fetchval.await_args_list[0]
    assert "PERM_RECLASIFICAR" in llamada.args, "no se comprobó el permiso esperado"
    assert 3 in llamada.args, (
        "el gate se conformó con nivel de lectura: desclasificar un radicado "
        "reservado no puede exigir menos que publicarlo"
    )


# ---------------------------------------------------------------------------
# C4 — bajar desde «clasificada» (nivel 3) exige solicitud y aprobación de otra persona
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("destino", [1, 2])
async def test_bajar_desde_nivel_3_es_409_y_no_toca_nada(client_with_redis, destino) -> None:
    client, mock_conn, fake_redis = client_with_redis
    mock_conn.fetchval.side_effect = [1]
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=3),
        _sec_ctx(nivel=3, causal="art18_a_intimidad", fundamento="F"),
    ]
    cuerpo = {"nivel_seguridad": destino, "motivo": "Se pide bajar el nivel"}
    if destino == 2:
        cuerpo |= {
            "causal_reserva": "art19_i_salud_publica",
            "fundamento_juridico": "Art. 19 lit. i",
            "plazo_reserva_meses": 12,
        }

    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level", json=cuerpo, headers=TENANT_HEADERS
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "desclasificacion_requiere_aprobacion"
    assert "desclasificación" in resp.json()["detail"]["detail"]
    assert not [c for c in mock_conn.execute.call_args_list if "UPDATE radicados" in str(c.args[0])]
    assert fake_redis.xadd.call_count == 0


@pytest.mark.asyncio
async def test_bajar_de_2_a_1_sigue_siendo_directo_y_emite_evento(client_with_redis) -> None:
    """Reservada (art. 19) -> pública: UNA persona, con asiento y evento (decisión del
    usuario: el umbral es NIVEL_DOBLE_FIRMA = 3, no 2)."""
    client, mock_conn, fake_redis = client_with_redis
    mock_conn.fetchval.side_effect = [1, None]
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=3),
        _sec_ctx(nivel=2, causal="art19_i_salud_publica", fundamento="F", plazo=12),
    ]

    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={"nivel_seguridad": 1, "motivo": "Cesó la causa de la reserva"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    assert resp.json()["nivel_seguridad"] == 1
    assert fake_redis.xadd.call_count == 1


@pytest.mark.asyncio
async def test_subir_1_a_3_es_directo(client_with_redis) -> None:
    client, mock_conn, fake_redis = client_with_redis
    mock_conn.fetchval.side_effect = [1, None]
    mock_conn.fetchrow.side_effect = [_clearance_row(max_level=3), _sec_ctx(nivel=1)]

    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={
            "nivel_seguridad": 3,
            "motivo": "Contiene datos de salud",
            "causal_reserva": "art18_b_vida_salud_seguridad",
            "fundamento_juridico": "Ley 1581 de 2012",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200 and resp.json()["nivel_seguridad"] == 3
    assert fake_redis.xadd.call_count == 1


@pytest.mark.asyncio
async def test_remotivar_al_mismo_nivel_no_emite_evento_ni_reinicia_el_reloj(
    client_with_redis,
) -> None:
    """Re-motivación (sacar una fila `por_clasificar` de su limbo): UPDATE de metadata
    SIN `nivel_seguridad` ni `clasificado_at`, asiento propio y SIN evento."""
    client, mock_conn, fake_redis = client_with_redis
    mock_conn.fetchval.side_effect = [1, None]
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=3),
        _sec_ctx(nivel=3, causal="por_clasificar", fundamento="PENDIENTE DE MOTIVAR"),
    ]

    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={
            "nivel_seguridad": 3,
            "motivo": "Se motiva la clasificación pendiente",
            "causal_reserva": "art18_a_intimidad",
            "fundamento_juridico": "Ley 1581 de 2012",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200 and resp.json()["nivel_seguridad"] == 3
    updates = [str(c.args[0]) for c in mock_conn.execute.call_args_list if "UPDATE radicados" in str(c.args[0])]
    assert len(updates) == 1
    assert "clasificado_at" not in updates[0], "re-motivar no puede reiniciar el reloj del plazo"
    assert "SET nivel_seguridad" not in updates[0]
    acciones = [
        c.args[5] for c in mock_conn.execute.call_args_list
        if "audit_log" in str(c.args[0]) and "INSERT" in str(c.args[0])
    ]
    assert acciones == ["document.radicado_remotivado"]
    assert fake_redis.xadd.call_count == 0


@pytest.mark.asyncio
async def test_set_classification_no_reinicia_clasificado_at_si_el_nivel_no_cambia() -> None:
    """Mutación 7 del diseño: volver a `clasificado_at = now()` incondicional deja que
    una re-clasificación al mismo nivel perpetúe una reserva más allá de los 15 años."""
    from unittest.mock import AsyncMock

    from app.repositories.document_repository import DocumentRepository

    conn = AsyncMock()
    conn.execute.return_value = "UPDATE 1"
    repo = DocumentRepository(conn, "demo")
    await repo.set_classification(
        DOC_ID, 2, nivel_esperado=2, fundamento="F", causal="art19_i_salud_publica", plazo_meses=12
    )
    sql = str(conn.execute.await_args_list[0].args[0])
    assert "clasificado_at = CASE WHEN nivel_seguridad = $2" in sql
    assert "clasificado_at = now()" not in sql
    assert "nivel_seguridad = $6" in sql, "el nivel esperado va en el WHERE del UPDATE"


def test_la_causal_debe_corresponder_al_nivel_y_al_plazo() -> None:
    from pydantic import ValidationError

    from app.schemas.document import ReclassifyRequest

    base = {"motivo": "m", "fundamento_juridico": "F"}
    # art. 18 con nivel 2, art. 19 con nivel 3
    with pytest.raises(ValidationError):
        ReclassifyRequest(nivel_seguridad=2, causal_reserva="art18_a_intimidad", **base)
    with pytest.raises(ValidationError):
        ReclassifyRequest(
            nivel_seguridad=3, causal_reserva="art19_i_salud_publica", plazo_reserva_meses=12, **base
        )
    # art. 19 sin plazo / con 181; art. 18 con plazo
    with pytest.raises(ValidationError):
        ReclassifyRequest(nivel_seguridad=2, causal_reserva="art19_i_salud_publica", **base)
    with pytest.raises(ValidationError):
        ReclassifyRequest(
            nivel_seguridad=2, causal_reserva="art19_i_salud_publica", plazo_reserva_meses=181, **base
        )
    with pytest.raises(ValidationError):
        ReclassifyRequest(
            nivel_seguridad=3, causal_reserva="art18_a_intimidad", plazo_reserva_meses=12, **base
        )
    # por_clasificar y causales inventadas no entran por la API
    for malo in ("por_clasificar", "Ley 1712 art. 19 lit. a"):
        with pytest.raises(ValidationError):
            ReclassifyRequest(nivel_seguridad=2, causal_reserva=malo, plazo_reserva_meses=12, **base)
    # nivel 1 no lleva causal
    with pytest.raises(ValidationError):
        ReclassifyRequest(nivel_seguridad=1, motivo="m", causal_reserva="art18_a_intimidad")


# ---------------------------------------------------------------------------
# Tope de 15 años DESDE LA GENERACIÓN (Ley 1712 art. 22; cierre del lote C, ARCH #1)
# ---------------------------------------------------------------------------


async def _reservar(client_with_conn, *, meses_generacion: int, plazo: int):
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [1, None]
    mock_conn.fetchrow.side_effect = [
        _clearance_row(max_level=3),
        _sec_ctx(nivel=1, meses_generacion=meses_generacion),
    ]
    resp = await client.patch(
        f"/api/v1/documents/{DOC_ID}/security-level",
        json={
            "nivel_seguridad": 2,
            "motivo": "Reserva del documento",
            "fundamento_juridico": "Art. 19 Ley 1712",
            "causal_reserva": "art19_b_seguridad_publica",
            "plazo_reserva_meses": plazo,
        },
        headers=TENANT_HEADERS,
    )
    return resp, mock_conn


@pytest.mark.asyncio
async def test_el_plazo_no_puede_pasar_de_15_anios_desde_la_generacion(client_with_conn) -> None:
    """Un documento de 10 años (120 meses) solo admite 60 meses más; 61 es 422 y no escribe."""
    resp, conn = await _reservar(client_with_conn, meses_generacion=120, plazo=61)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "plazo_excede_tope_legal"
    assert not [c for c in conn.execute.call_args_list if "UPDATE radicados" in str(c.args[0])]


@pytest.mark.asyncio
async def test_el_limite_exacto_de_lo_que_resta_se_admite(client_with_conn) -> None:
    resp, _ = await _reservar(client_with_conn, meses_generacion=120, plazo=60)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_una_reserva_agotada_es_422_con_codigo_propio(client_with_conn) -> None:
    """A los 15 años cumplidos ya no se reserva nada (ni 1 mes)."""
    resp, conn = await _reservar(client_with_conn, meses_generacion=180, plazo=1)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "reserva_agotada"
    assert not [c for c in conn.execute.call_args_list if "UPDATE radicados" in str(c.args[0])]
