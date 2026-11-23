# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Retenciones legales por MOTIVO y retención diferida (C-5/C7, ADR-043). Unitarias (conexión y
almacén de mentira); la atomicidad y el SQL real viven en `tests/integration/`.

Sin rutas HTTP (ADR-047): se llama a  (holds, retención diferida, AIP) o, cuando
hace falta un campo que la API no devuelve, al servicio que envolvía la ruta interna.

Cada prueba dice qué MUTACIÓN del código la pondría en rojo (no se ejecutaron: la verificación se
hace una vez al cierre del lote C).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from docuboto import config as config_module
from storage import api as storage_api
from storage.api import StorageLegalHoldError
from storage.core.constants import SYSTEM_RECONCILER_ID
from storage.schemas.preservacion import (
    AipCreate,
    HoldFijarRequest,
    HoldLevantarRequest,
    ProtegerIndiceRequest,
    ProtegerIndiceResponse,
    RenovarRetencionRequest,
)
from storage.services.aip_service import AipService, AipYaExisteError
from storage.services.preservation_worm_service import (
    PreservationWormService,
    RetencionDiferidaSinHoldError,
    SinRetencionQueRenovarError,
)
from tests.storage.conftest import TENANT_SLUG, make_file_record, make_worm_row

_EXP = "EXP-1"
_ACTOR = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _preservation_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.storage, "preservation_enabled", True)


async def _fijar(*, actor_id: str | None = _ACTOR, **overrides: str) -> dict:
    cuerpo = {**_CUERPO_FIJAR, **overrides}
    return await storage_api.set_legal_hold(tenant_slug=TENANT_SLUG, actor_id=actor_id, **cuerpo)


async def _levantar(hold_id: uuid.UUID, motivo: str, *, actor_id: str | None = _ACTOR) -> dict:
    return await storage_api.lift_legal_hold(
        tenant_slug=TENANT_SLUG, actor_id=actor_id, hold_id=hold_id, **_cuerpo_levantar(motivo)
    )


def _hold(
    motivo: str = "judicial",
    *,
    expediente_id: str = _EXP,
    levantado: bool = False,
    hold_id: uuid.UUID | None = None,
) -> dict:
    ahora = datetime.now(tz=UTC)
    return {
        "id": hold_id or uuid.uuid4(),
        "expediente_id": expediente_id,
        "motivo_tipo": motivo,
        "fundamento": "Orden del juzgado 12 por proceso en curso",
        "acta_ref": "ACTA-2026-044",
        "fijado_por": None if motivo == "valoracion_pendiente" else uuid.uuid4(),
        "fijado_at": ahora,
        "levantado_por": None,
        "levantado_at": ahora if levantado else None,
        "levantado_fundamento": "x" * 12 if levantado else None,
        "levantado_acta_ref": "ACTA-L" if levantado else None,
    }


class _Tx:
    def __init__(self, db: _Db) -> None:
        self._db = db

    async def __aenter__(self) -> None:
        self._db.eventos.append("tx_enter")

    async def __aexit__(self, *exc: object) -> bool:
        self._db.eventos.append("tx_exit")
        return False


class _Db:
    """Doble en memoria del registro de holds y de `preservacion_worm_objeto`, despachando por la
    forma del SQL. Registra el ORDEN de lo que importa (almacén, filas, asientos)."""

    def __init__(self, filas: list[dict] | None = None, holds: list[dict] | None = None) -> None:
        self.filas = [dict(f) for f in (filas or [])]
        self.holds = {h["id"]: dict(h) for h in (holds or [])}
        self.eventos: list[str] = []
        self.acciones: list[str] = []

    def instalar(self, conn: AsyncMock) -> _Db:
        conn.transaction = lambda: _Tx(self)
        conn.execute.side_effect = self.execute
        conn.fetch.side_effect = self.fetch
        conn.fetchrow.side_effect = self.fetchrow
        conn.fetchval.return_value = None
        return self

    async def execute(self, sql: str, *args: object) -> None:
        if "INSERT INTO public.audit_log" in sql:
            self.eventos.append("audit")
            self.acciones.append(str(args[4]))
        return None

    def _activos(self, exp: str) -> list[dict]:
        return [h for h in self.holds.values() if h["expediente_id"] == exp and h["levantado_at"] is None]

    async def fetch(self, sql: str, *args: object) -> list[dict]:
        if "advisory" in sql:
            return []
        if "FROM preservacion_worm_objeto" in sql:
            filas = [f for f in self.filas if f["expediente_id"] == args[0]]
            if "retencion_origen = 'valoracion_pendiente'" in sql:
                filas = [
                    f
                    for f in filas
                    if f["retencion_origen"] == "valoracion_pendiente" and f["retain_until"] is None
                ]
            return filas
        if "SELECT motivo_tipo FROM preservacion_legal_hold" in sql:
            return [{"motivo_tipo": h["motivo_tipo"]} for h in self._activos(str(args[0]))]
        if "id <> $2" in sql:
            return [h for h in self._activos(str(args[0])) if h["id"] != args[1]]
        if "FROM preservacion_legal_hold" in sql and "ORDER BY" in sql:
            return [h for h in self.holds.values() if h["expediente_id"] == args[0]]
        raise AssertionError(f"fetch no previsto: {sql[:90]}")

    async def fetchrow(self, sql: str, *args: object) -> dict | None:
        if "INSERT INTO preservacion_legal_hold" in sql:
            h = _hold(str(args[1]), expediente_id=str(args[0]))
            h["fundamento"], h["acta_ref"], h["fijado_por"] = args[2], args[3], args[4]
            self.holds[h["id"]] = h
            self.eventos.append("insert_hold")
            return h
        if "UPDATE preservacion_legal_hold" in sql:
            h = self.holds[args[0]]
            if h["levantado_at"] is not None:
                return None
            h["levantado_at"] = datetime.now(tz=UTC)
            self.eventos.append("levantar_hold")
            return h
        if "motivo_tipo = 'valoracion_pendiente'" in sql and "FROM preservacion_legal_hold" in sql:
            return next(
                (h for h in self._activos(str(args[0])) if h["motivo_tipo"] == "valoracion_pendiente"),
                None,
            )
        if "FROM preservacion_legal_hold WHERE id = $1" in sql:
            return self.holds.get(args[0])
        if "UPDATE preservacion_worm_objeto" in sql and "SET legal_hold" in sql:
            f = next(f for f in self.filas if f["id"] == args[0])
            f["legal_hold"] = args[1]
            if args[2] and f["retencion_origen"] == "valoracion_pendiente":
                f["retencion_origen"] = "valorada_sin_retencion"
            self.eventos.append("update_fila")
            return f
        if "UPDATE preservacion_worm_objeto" in sql and "SET retain_until = $2" in sql:
            f = next(f for f in self.filas if f["id"] == args[0])
            if f["retain_until"] is not None or f["retencion_origen"] != "valoracion_pendiente":
                return None
            f["retain_until"], f["retention_mode"], f["retencion_origen"] = args[1], args[2], "calendario"
            self.eventos.append("fijar_retencion_fila")
            return f
        if "INSERT INTO preservacion_evento" in sql:
            return {"id": uuid.uuid4()}
        raise AssertionError(f"fetchrow no previsto: {sql[:90]}")


def _almacen(db: _Db, mock_minio: MagicMock) -> None:
    async def _on(*a: object, **k: object) -> None:
        db.eventos.append("minio_on")

    async def _off(*a: object, **k: object) -> None:
        db.eventos.append("minio_off")

    mock_minio.enable_object_legal_hold = AsyncMock(side_effect=_on)
    mock_minio.disable_object_legal_hold = AsyncMock(side_effect=_off)


_CUERPO_FIJAR = {
    "expediente_id": _EXP,
    "motivo_tipo": "judicial",
    "fundamento": "Orden del juzgado 12 por proceso en curso",
    "acta_ref": "ACTA-2026-044",
}


def _cuerpo_levantar(motivo: str) -> dict:
    return {
        "motivo_tipo_esperado": motivo,
        "fundamento": "Se levanta por orden del juzgado 12",
        "acta_ref": "ACTA-L-1",
    }


# ── fijar ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fijar_hold_sobre_expediente_sin_objetos_lo_crea(client_with_conn) -> None:
    """H2: antes era 404 `sin_objetos_protegidos`. Un expediente sin AIP/índice todavía se puede
    congelar, y deja asiento aunque no haya objeto que alcanzar.

    Mutación: restaurar `if not filas: raise WormObjectNotFoundError` -> lanza y este test cae."""
    _client, conn, minio = client_with_conn
    db = _Db(filas=[]).instalar(conn)
    _almacen(db, minio)

    cuerpo = await _fijar()

    assert cuerpo["creado"] is True and cuerpo["objetos_afectados"] == 0
    minio.enable_object_legal_hold.assert_not_awaited()
    assert db.acciones == ["preservacion.legal_hold_fijado"]
    assert len(db._activos(_EXP)) == 1


@pytest.mark.asyncio
async def test_fijar_hold_pone_el_hold_en_s3_solo_en_las_filas_que_aun_no_lo_tienen(
    client_with_conn,
) -> None:
    _client, conn, minio = client_with_conn
    ya = make_worm_row(expediente_id=_EXP, legal_hold=True, tipo="indice")
    nueva = make_worm_row(expediente_id=_EXP, legal_hold=False, tipo="indice")
    db = _Db(filas=[ya, nueva]).instalar(conn)
    _almacen(db, minio)

    cuerpo = await _fijar()

    assert cuerpo["objetos_afectados"] == 1
    minio.enable_object_legal_hold.assert_awaited_once_with(
        nueva["bucket"], nueva["object_key"], version_id=nueva["version_id"]
    )
    # El almacén va ANTES que el registro (un exceso de hold es seguro; lo contrario no).
    assert db.eventos.index("minio_on") < db.eventos.index("insert_hold")


@pytest.mark.asyncio
async def test_fijar_hold_registra_a_la_persona_que_lo_fija(client_with_conn) -> None:
    """`fijado_por` sale del `actor_id` que pasa archive, no de una cabecera."""
    _client, conn, minio = client_with_conn
    db = _Db().instalar(conn)
    _almacen(db, minio)

    cuerpo = await _fijar(actor_id=_ACTOR)

    assert db.holds[uuid.UUID(cuerpo["hold_id"])]["fijado_por"] == uuid.UUID(_ACTOR)


@pytest.mark.asyncio
async def test_fijar_hold_judicial_sin_actor_identificado_es_actor_requerido(
    client_with_conn,
) -> None:
    """Un hold que no es de sistema necesita persona detrás (CHECK `ck_plh_fijado_por`).
    Mutación: quitar `HoldActorRequeridoError` -> fallo genérico (503) por violación del CHECK."""
    _client, conn, minio = client_with_conn
    _almacen(_Db().instalar(conn), minio)

    with pytest.raises(StorageLegalHoldError) as exc:
        await _fijar(actor_id=None)

    assert exc.value.status == 422 and exc.value.code == "actor_requerido"


@pytest.mark.asyncio
async def test_fijar_valoracion_pendiente_es_idempotente_y_sin_actor(client_with_conn) -> None:
    """El hold de SISTEMA nace sin persona; repetirlo no crea otro (`creado=false`).
    Mutación: quitar la consulta `valoracion_activa` -> doble alta y rebota `ux_plh_valoracion_activa`."""
    _client, conn, minio = client_with_conn
    db = _Db().instalar(conn)
    _almacen(db, minio)
    cuerpo = {"motivo_tipo": "valoracion_pendiente", "acta_ref": "TVD S-1 v2"}

    primero = await _fijar(actor_id=None, **cuerpo)
    segundo = await _fijar(actor_id=None, **cuerpo)

    assert primero["creado"] is True and segundo["creado"] is False
    assert primero["hold_id"] == segundo["hold_id"]
    assert len(db._activos(_EXP)) == 1
    assert db.holds[uuid.UUID(primero["hold_id"])]["fijado_por"] is None


@pytest.mark.asyncio
async def test_fijar_con_un_motivo_fuera_del_catalogo_es_422(client_with_conn) -> None:
    _client, conn, _ = client_with_conn
    _Db().instalar(conn)
    with pytest.raises(StorageLegalHoldError) as exc:
        await _fijar(motivo_tipo="capricho")
    assert exc.value.status == 422


@pytest.mark.asyncio
async def test_hold_con_preservacion_desactivada_es_503(client_with_conn, monkeypatch) -> None:
    monkeypatch.setattr(config_module.storage, "preservation_enabled", False)
    _client, conn, _ = client_with_conn
    _Db().instalar(conn)
    with pytest.raises(StorageLegalHoldError) as exc:
        await _fijar()
    assert exc.value.status == 503 and exc.value.code == "preservation_disabled"


# ── levantar ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_levantar_valoracion_con_un_hold_judicial_vivo_NO_libera_s3(client_with_conn) -> None:
    """H1, el defecto central: levantar la valoración no puede liberar lo que sostiene un hold
    judicial. Se verifica el almacén, la fila y la respuesta.

    Mutación que debe caer: liberar S3 sin contar los holds restantes (quitar `restantes == 0`)
    -> `disable_object_legal_hold` se espera y este test cae."""
    _client, conn, minio = client_with_conn
    judicial = _hold("judicial")
    valoracion = _hold("valoracion_pendiente")
    fila = make_worm_row(expediente_id=_EXP, diferida=True)
    db = _Db(filas=[fila], holds=[judicial, valoracion]).instalar(conn)
    _almacen(db, minio)

    res = await _levantar(valoracion["id"], "valoracion_pendiente")

    assert res == {
        "hold_id": str(valoracion["id"]),
        "objetos_liberados_s3": 0,
        "holds_restantes": 1,
    }
    minio.disable_object_legal_hold.assert_not_awaited()
    assert db.filas[0]["legal_hold"] is True  # sigue con hold (judicial)
    assert db.filas[0]["retencion_origen"] == "valorada_sin_retencion"  # ya no «pendiente»
    assert db.holds[judicial["id"]]["levantado_at"] is None


@pytest.mark.asyncio
async def test_levantar_el_ultimo_hold_libera_s3_con_solicitud_antes_y_la_fila_en_la_tx(
    client_with_conn,
) -> None:
    """Orden al LEVANTAR: asiento de solicitud -> almacén -> registro + fila + asiento final.
    Mutación: mover el almacén antes del asiento de solicitud -> el índice de 'audit' ya no
    precede a 'minio_off'."""
    _client, conn, minio = client_with_conn
    judicial = _hold("judicial")
    fila = make_worm_row(expediente_id=_EXP, legal_hold=True, file_id=uuid.uuid4())
    db = _Db(filas=[fila], holds=[judicial]).instalar(conn)
    _almacen(db, minio)

    res = await _levantar(judicial["id"], "judicial")

    assert res["objetos_liberados_s3"] == 1 and res["holds_restantes"] == 0
    minio.disable_object_legal_hold.assert_awaited_once_with(
        fila["bucket"], fila["object_key"], version_id=fila["version_id"]
    )
    assert db.eventos.index("audit") < db.eventos.index("minio_off") < db.eventos.index("levantar_hold")
    assert db.acciones == [
        "worm.legal_hold_levantamiento_solicitado",
        "worm.legal_hold_levantado",
        "preservacion.legal_hold_levantado",
    ]
    assert db.filas[0]["legal_hold"] is False


@pytest.mark.asyncio
async def test_levantar_confirmando_otro_motivo_es_409_y_no_toca_nada(client_with_conn) -> None:
    """Un acto no puede levantar el hold de otro. Mutación: quitar la comparación de
    `motivo_tipo` -> el hold judicial queda levantado por un acto de valoración."""
    _client, conn, minio = client_with_conn
    judicial = _hold("judicial")
    db = _Db(filas=[make_worm_row(expediente_id=_EXP, legal_hold=True)], holds=[judicial]).instalar(conn)
    _almacen(db, minio)

    with pytest.raises(StorageLegalHoldError) as exc:
        await _levantar(judicial["id"], "valoracion_pendiente")

    assert exc.value.status == 409 and exc.value.code == "motivo_no_coincide"
    minio.disable_object_legal_hold.assert_not_awaited()
    assert db.holds[judicial["id"]]["levantado_at"] is None
    assert db.acciones == []


@pytest.mark.asyncio
async def test_levantar_un_hold_ya_levantado_es_409_y_uno_inexistente_404(client_with_conn) -> None:
    _client, conn, minio = client_with_conn
    viejo = _hold("judicial", levantado=True)
    db = _Db(holds=[viejo]).instalar(conn)
    _almacen(db, minio)

    with pytest.raises(StorageLegalHoldError) as ya:
        await _levantar(viejo["id"], "judicial")
    with pytest.raises(StorageLegalHoldError) as no:
        await _levantar(uuid.uuid4(), "judicial")

    assert ya.value.status == 409 and ya.value.code == "hold_ya_levantado"
    assert no.value.status == 404 and no.value.code == "hold_not_found"


@pytest.mark.asyncio
async def test_fallo_del_almacen_al_levantar_deja_solicitud_y_fallo_y_el_hold_sigue_activo(
    client_with_conn,
) -> None:
    _client, conn, minio = client_with_conn
    judicial = _hold("judicial")
    db = _Db(filas=[make_worm_row(expediente_id=_EXP, legal_hold=True)], holds=[judicial]).instalar(conn)
    minio.disable_object_legal_hold = AsyncMock(side_effect=RuntimeError("s3 caido"))

    with pytest.raises(StorageLegalHoldError, match="s3 caido") as exc:
        await _levantar(judicial["id"], "judicial")

    assert exc.value.status == 503
    assert db.holds[judicial["id"]]["levantado_at"] is None
    assert "levantar_hold" not in db.eventos and "update_fila" not in db.eventos
    assert db.acciones == [
        "worm.legal_hold_levantamiento_solicitado",
        "worm.legal_hold_levantamiento_fallido",
    ]


@pytest.mark.asyncio
async def test_levantar_un_hold_del_aip_alcanza_tambien_la_replica(client_with_conn) -> None:
    """AREA-08-07: el freeze del AIP cubre primaria (version exacta) Y réplica."""
    _client, conn, minio = client_with_conn
    judicial = _hold("judicial")
    fila = make_worm_row(expediente_id=_EXP, legal_hold=True, tipo="aip")
    db = _Db(filas=[fila], holds=[judicial]).instalar(conn)
    _almacen(db, minio)

    await _levantar(judicial["id"], "judicial")

    assert minio.disable_object_legal_hold.await_count == 2


@pytest.mark.asyncio
async def test_listar_devuelve_activos_y_levantados(client_with_conn) -> None:
    _client, conn, _ = client_with_conn
    a, b = _hold("judicial"), _hold("fiscal", levantado=True)
    _Db(holds=[a, b]).instalar(conn)

    holds = await storage_api.list_legal_holds(tenant_slug=TENANT_SLUG, expediente_id=_EXP)

    assert {h["motivo_tipo"] for h in holds} == {"judicial", "fiscal"}
    assert sum(1 for h in holds if h["levantado_at"] is not None) == 1


# ── protección: el objeto nace con el hold y la retención diferida exige hold ──────────


def _payload_indice(file_id: uuid.UUID, **extra: object) -> dict:
    return {"file_id": str(file_id), "expediente_id": _EXP, **extra}


async def _proteger_indice(conn: AsyncMock, payload: dict) -> tuple[ProtegerIndiceResponse, bool]:
    """Lo que hacía la ruta interna del índice: tipo fijo y actor de sistema."""
    return await PreservationWormService(conn, TENANT_SLUG).proteger_indice(
        ProtegerIndiceRequest(**payload).model_copy(update={"tipo": "indice"}),
        actor=str(SYSTEM_RECONCILER_ID),
    )


def _registro_indice(file_id: uuid.UUID) -> dict:
    import hashlib

    contenido = b"<indice>firmado</indice>"
    return make_file_record(
        file_id=file_id,
        filename="indice.xml",
        mime_type="application/xml",
        sha256=hashlib.sha256(contenido).hexdigest(),
        object_key=f"{file_id}/indice.xml",
    ), contenido


def _get_object(contenido: bytes) -> AsyncMock:
    obj = MagicMock()
    obj.read = AsyncMock(return_value=contenido)
    obj.close = MagicMock()
    return AsyncMock(return_value=obj)


def _fetch_con_holds(conn: AsyncMock, motivos: list[str]) -> None:
    async def _fetch(sql: str, *a: object) -> list[dict]:
        if "SELECT motivo_tipo FROM preservacion_legal_hold" in sql:
            return [{"motivo_tipo": m} for m in motivos]
        return []

    conn.fetch.side_effect = _fetch


@pytest.mark.asyncio
async def test_un_objeto_protegido_con_un_hold_vivo_nace_con_legal_hold(client_with_conn) -> None:
    """H2b: antes `legal_hold` era siempre False al proteger. Ahora = existe un hold activo.

    Mutación: fijar `legal_hold=False` en el put/insert -> este test cae (put_object y fila)."""
    _client, conn, minio = client_with_conn
    fid = uuid.uuid4()
    registro, contenido = _registro_indice(fid)
    _fetch_con_holds(conn, ["judicial"])
    conn.fetchrow.side_effect = [
        None,
        registro,
        make_worm_row(expediente_id=_EXP, legal_hold=True),
        {"id": uuid.uuid4()},
        {"id": uuid.uuid4()},
    ]
    minio.get_object = _get_object(contenido)

    outcome = await storage_api.protect_index_as_system(
        tenant_slug=TENANT_SLUG,
        file_id=str(fid),
        expediente_id=_EXP,
        retencion_anios=5,
        fecha_inicio=date(2026, 1, 1),
    )

    assert outcome.ok is True, outcome
    assert minio.put_object.await_args.kwargs["legal_hold"] is True
    insert = next(
        c for c in conn.fetchrow.call_args_list if "INSERT INTO preservacion_worm_objeto" in c.args[0]
    )
    assert insert.args[9] is True  # legal_hold
    minio.is_object_legal_hold_enabled.assert_awaited()  # se RELEE, no se cree al almacén


@pytest.mark.asyncio
async def test_retencion_diferida_sin_hold_de_valoracion_falla_y_no_sube_nada(
    client_with_conn,
) -> None:
    """Un objeto sin retención y sin hold quedaría borrable. Mutación: quitar la comprobación
    `valoracion_pendiente in holds` -> put_object se espera y el error desaparece."""
    _client, conn, minio = client_with_conn
    fid = uuid.uuid4()
    registro, contenido = _registro_indice(fid)
    _fetch_con_holds(conn, ["judicial"])  # hay un hold, pero NO el de valoración
    conn.fetchrow.side_effect = [None, registro]
    minio.get_object = _get_object(contenido)

    with pytest.raises(RetencionDiferidaSinHoldError):
        await _proteger_indice(conn, _payload_indice(fid, retencion_diferida="valoracion_pendiente"))

    minio.put_object.assert_not_awaited()


@pytest.mark.asyncio
async def test_retencion_diferida_con_hold_escribe_solo_hold_sin_retencion(client_with_conn) -> None:
    """Control positivo: con el hold de valoración, el objeto se escribe SIN `Retention`, con
    legal hold, y la fila nace `valoracion_pendiente` con retain_until/mode NULL.

    Mutación: pasar una `Retention` calculada (el clamp viejo) -> `retention is None` cae."""
    _client, conn, minio = client_with_conn
    fid = uuid.uuid4()
    registro, contenido = _registro_indice(fid)
    _fetch_con_holds(conn, ["valoracion_pendiente"])
    conn.fetchrow.side_effect = [
        None,
        registro,
        make_worm_row(expediente_id=_EXP, diferida=True),
        {"id": uuid.uuid4()},
        {"id": uuid.uuid4()},
    ]
    minio.get_object = _get_object(contenido)

    cuerpo, creado = await _proteger_indice(
        conn, _payload_indice(fid, retencion_diferida="valoracion_pendiente")
    )

    assert creado is True
    assert cuerpo.retain_until is None and cuerpo.retention_mode is None
    assert cuerpo.retencion_origen == "valoracion_pendiente" and cuerpo.legal_hold is True
    put = minio.put_object.await_args
    assert put.kwargs["retention"] is None and put.kwargs["legal_hold"] is True
    minio.get_object_retention.assert_not_awaited()  # no hay fecha que releer


def test_retencion_diferida_es_excluyente_y_solo_del_indice() -> None:
    fid = uuid.uuid4()
    with pytest.raises(ValidationError):  # mezclada con calendario
        ProtegerIndiceRequest(
            **_payload_indice(fid, retencion_diferida="valoracion_pendiente", retencion_anios=5)
        )
    with pytest.raises(ValidationError):  # un acta no admite retención diferida
        ProtegerIndiceRequest(
            **_payload_indice(fid, tipo="acta_transferencia", retencion_diferida="valoracion_pendiente")
        )
    with pytest.raises(ValidationError):  # renovar solo extiende fechas
        RenovarRetencionRequest(
            expediente_id=_EXP, tipo="indice", retencion_diferida="valoracion_pendiente"
        )


@pytest.mark.asyncio
async def test_renovar_una_fila_sin_retain_until_es_sin_retencion_que_renovar(
    client_with_conn,
) -> None:
    """`nueva <= None` reventaba con TypeError. Mutación: quitar el guard -> TypeError."""
    _client, conn, minio = client_with_conn
    conn.fetchrow.side_effect = [make_worm_row(expediente_id=_EXP, diferida=True)]

    with pytest.raises(SinRetencionQueRenovarError):
        await PreservationWormService(conn, TENANT_SLUG).renovar_retencion(
            RenovarRetencionRequest(
                expediente_id=_EXP, tipo="indice", retencion_anios=5, fecha_inicio="2026-01-01"
            ),
            actor=str(SYSTEM_RECONCILER_ID),
        )

    minio.set_object_retention.assert_not_awaited()


# ── set_deferred_retention (valoración CT) ─────────────────────────────────


async def _fijar_retencion(retencion_anios: int = 10, fecha_inicio: date = date(2026, 10, 1)) -> dict:
    return await storage_api.set_deferred_retention(
        tenant_slug=TENANT_SLUG,
        expediente_id=_EXP,
        retencion_anios=retencion_anios,
        fecha_inicio=fecha_inicio,
    )


@pytest.mark.asyncio
async def test_fijar_retencion_solo_toca_filas_pendientes_nunca_calendario(client_with_conn) -> None:
    """Fila `calendario` + fila `valoracion_pendiente`: solo la segunda recibe retención (en el
    almacén y en la fila); la primera no se toca (la retención no se reasigna).

    Mutación: quitar `retain_until IS NULL AND retencion_origen='valoracion_pendiente'` del
    repositorio -> el doble (que lo modela) dejaría pasar la fila `calendario` y este test cae."""
    _client, conn, minio = client_with_conn
    calendario = make_worm_row(expediente_id=_EXP, tipo="aip")
    pendiente = make_worm_row(expediente_id=_EXP, tipo="indice", diferida=True)
    db = _Db(filas=[calendario, pendiente]).instalar(conn)
    original = calendario["retain_until"]

    res = await _fijar_retencion()

    assert res["filas_fijadas"] == 1 and res["filas_pendientes_restantes"] == 0
    minio.set_object_retention.assert_awaited_once()
    assert minio.set_object_retention.await_args.args[1] == pendiente["object_key"]
    assert db.filas[0]["retain_until"] == original
    assert db.filas[1]["retencion_origen"] == "calendario" and db.filas[1]["retain_until"] is not None
    # Se relee la retención recién escrita (no se cree al almacén).
    minio.get_object_retention.assert_awaited()


@pytest.mark.asyncio
async def test_fijar_retencion_es_idempotente(client_with_conn) -> None:
    _client, conn, minio = client_with_conn
    db = _Db(filas=[make_worm_row(expediente_id=_EXP, diferida=True)]).instalar(conn)

    primera = await _fijar_retencion()
    segunda = await _fijar_retencion()

    assert primera["filas_fijadas"] == 1 and segunda["filas_fijadas"] == 0
    assert minio.set_object_retention.await_count == 1
    assert db.filas[0]["retencion_origen"] == "calendario"


@pytest.mark.asyncio
async def test_fijar_retencion_respeta_el_piso_y_techo_de_storage(client_with_conn) -> None:
    _client, conn, minio = client_with_conn
    _Db(filas=[make_worm_row(expediente_id=_EXP, diferida=True)]).instalar(conn)
    with pytest.raises(StorageLegalHoldError) as exc:
        await _fijar_retencion(retencion_anios=100, fecha_inicio=date(2100, 1, 1))
    # Fuera del techo `preservation_max_retention_years`.
    assert exc.value.code == "invalid_retention"
    minio.set_object_retention.assert_not_awaited()


# ── AIP: mismo contrato que el índice ───────────────────────────────────────


def _documentos(doc_file_id: uuid.UUID) -> list[dict]:
    return [{"document_id": "R-1", "nombre": "oficio.pdf", "file_id": str(doc_file_id)}]


@pytest.mark.asyncio
async def test_aip_con_retencion_diferida_escribe_solo_hold_y_la_fila_nace_pendiente(
    client_with_conn,
) -> None:
    """Mutación: pasar `Retention(...)` (el clamp viejo `hoy+400`) -> `retention is None` cae;
    pasar `legal_hold=False` -> `legal_hold is True` cae."""
    from tests.storage.conftest import minio_get_object
    from tests.storage.test_aip_advisory_lock import (
        _DOC_CONTENT,
        _aip_insert_row,
        _doc_record,
        _evento_row,
    )

    _client, conn, minio = client_with_conn
    doc_id, aip_id = uuid.uuid4(), uuid.uuid4()
    conn.fetchval.return_value = True
    _fetch_con_holds(conn, ["valoracion_pendiente"])
    conn.fetchrow.side_effect = [
        _doc_record(doc_id),
        None,
        _aip_insert_row(aip_id, datetime.now(tz=UTC)) | {"retain_until": None, "retention_mode": None, "legal_hold": True},
        make_worm_row(expediente_id=_EXP, tipo="aip", diferida=True),
        _evento_row("FIJACION"),
        _evento_row("WORM"),
        _evento_row("INGESTA"),
    ]
    minio.get_object = minio_get_object(_DOC_CONTENT)

    outcome = await storage_api.package_aip(
        tenant_slug=TENANT_SLUG,
        expediente_id=_EXP,
        documentos=_documentos(doc_id),
        indice_file_id=None,
        retencion_diferida="valoracion_pendiente",
    )

    assert outcome.ok is True, outcome
    assert outcome.aip_id == aip_id and outcome.retain_until is None
    put = minio.put_object.await_args
    assert put.kwargs["retention"] is None and put.kwargs["legal_hold"] is True
    insert_worm = next(
        c for c in conn.fetchrow.call_args_list if "INSERT INTO preservacion_worm_objeto" in c.args[0]
    )
    assert insert_worm.args[7] is None and insert_worm.args[8] is None  # retain_until, mode
    assert insert_worm.args[13] == "valoracion_pendiente"


@pytest.mark.asyncio
async def test_aip_con_retencion_diferida_sin_hold_falla_y_no_sube(client_with_conn) -> None:
    from tests.storage.conftest import minio_get_object
    from tests.storage.test_aip_advisory_lock import _DOC_CONTENT, _doc_record

    _client, conn, minio = client_with_conn
    doc_id = uuid.uuid4()
    conn.fetchval.return_value = True
    _fetch_con_holds(conn, [])
    conn.fetchrow.side_effect = [_doc_record(doc_id), None]
    minio.get_object = minio_get_object(_DOC_CONTENT)

    with pytest.raises(RetencionDiferidaSinHoldError):
        await AipService(conn, TENANT_SLUG).empaquetar_aip(
            AipCreate(
                expediente_id=_EXP,
                documentos=_documentos(doc_id),
                retencion_diferida="valoracion_pendiente",
            ),
            actor=str(SYSTEM_RECONCILER_ID),
        )

    minio.put_object.assert_not_awaited()


def test_levantar_ddhh_exige_el_acto_de_la_autoridad_explicado() -> None:
    """`ddhh_memoria_historica` (Ley 1448/2011 art. 144 [confirmar]) es un motivo válido y su
    levantamiento exige un fundamento de al menos 50 caracteres (defensa en profundidad del
    mismo criterio que archive-service)."""
    HoldFijarRequest(
        expediente_id=_EXP, motivo_tipo="ddhh_memoria_historica",
        fundamento="Orden de preservación de archivos de DDHH", acta_ref="A-1",
    )
    with pytest.raises(ValidationError):
        HoldLevantarRequest(motivo_tipo_esperado="ddhh_memoria_historica", fundamento="x" * 20, acta_ref="A")
    HoldLevantarRequest(motivo_tipo_esperado="ddhh_memoria_historica", fundamento="x" * 50, acta_ref="A")
    HoldLevantarRequest(motivo_tipo_esperado="judicial", fundamento="x" * 10, acta_ref="A")


@pytest.mark.asyncio
@pytest.mark.parametrize("vigente", [uuid.uuid4(), None])
async def test_aip_ya_existe_lleva_el_aip_vigente(
    client_with_conn, monkeypatch: pytest.MonkeyPatch, vigente: uuid.UUID | None
) -> None:
    """`ya_existe` devuelve el AIP vigente para que archive reconcilie (o `None` si no lo sabe)."""
    monkeypatch.setattr(
        AipService, "empaquetar_aip",
        AsyncMock(side_effect=AipYaExisteError("ya tiene un AIP", aip_id=vigente)),
    )

    outcome = await storage_api.package_aip(
        tenant_slug=TENANT_SLUG,
        expediente_id=_EXP,
        documentos=_documentos(uuid.uuid4()),
        indice_file_id=None,
        retencion_anios=5,
        fecha_inicio=date(2026, 1, 1),
    )

    assert outcome == storage_api.AipPackagingOutcome(ok=False, ya_existe=True, aip_id=vigente)
