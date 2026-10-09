# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""AREA-07-10 (migración 061): Object-Lock de la CONSTANCIA de «firma inobtenible» y su renovación.

Dos artefactos por constancia (`sello` / `personal`), un `tipo` de storage por rol y artefacto
(`acta_firma_excepcion_<rol>` / `acta_firma_excepcion_personal_<rol>`). Espejo de
`tests/test_firma_personal_worm.py` y `tests/test_firma_personal_renovacion.py`. La forma real
del SQL está en `tests/integration/test_firma_inobtenible_sql.py`.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.storage_client import WormProtectionOutcome, WormRenewalOutcome
from app.core.config import settings
from app.jobs import indice_reconciliation
from app.services.index_service import _TIPO_FIRMA_EXCEPCION, _TIPO_FIRMA_REVOCACION
from app.services.transferencia_service import TransferenciaService

TENANT = "demo"


def _conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _sql(conn: AsyncMock, fragmento: str) -> list:
    return [c for c in conn.execute.call_args_list if c.args and fragmento in str(c.args[0])]


def _fila(artefacto: str = "sello", **kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "transferencia_id": uuid.uuid4(),
        "rol": "recibe",
        "actuada_en": datetime(2026, 10, 1, tzinfo=UTC),
        "minio_key_firmado": f"{uuid.uuid4()}/constancia-firmada.xml",
        "expediente_id": uuid.uuid4(),
        "artefacto": artefacto,
        "worm_protegido_at": None,
        "worm_reconcile_intentos": 0,
    }
    base.update(kw)
    return base


def _svc(conn: AsyncMock, *, retencion: tuple[int, date] | None = (25, date(2026, 10, 1))):
    svc = TransferenciaService(conn, TENANT)
    svc._resolve_worm_retention_acta = AsyncMock(return_value=retencion)  # type: ignore[method-assign]
    svc._resolve_ct_retencion_anios_acta = AsyncMock(  # type: ignore[method-assign]
        return_value=retencion[0] if retencion else None
    )
    return svc


# --- protección inicial ------------------------------------------------------------------


_ACTOS = [
    # (acto, método de protección, método de renovación, tabla WORM, prefijo de asiento)
    ("excepcion", "proteger_worm_firma_excepcion", "renovar_worm_firma_excepcion",
     "transferencia_acta_firma_excepcion_worm", "archive.acta_firma_excepcion_worm"),
    ("revocacion", "proteger_worm_revocacion", "renovar_worm_revocacion",
     "transferencia_acta_firma_excepcion_revocacion_worm", "archive.acta_firma_revocacion_worm"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("acto,proteger,_renovar,tabla,asiento", _ACTOS)
@pytest.mark.parametrize("rol", ["elabora", "entrega", "recibe"])
@pytest.mark.parametrize("artefacto", ["sello", "personal"])
async def test_protege_con_el_tipo_del_rol_y_el_artefacto_y_el_file_id_sale_de_la_clave(
    acto: str, proteger: str, _renovar: str, tabla: str, asiento: str, rol: str, artefacto: str
) -> None:
    prefijo = f"acta_firma_{acto}_" + ("personal_" if artefacto == "personal" else "")
    conn = _conn()
    fila = _fila(artefacto, rol=rol)
    conn.fetchrow.return_value = fila
    retain = datetime(2053, 1, 1, tzinfo=UTC)
    worm_id = uuid.uuid4()
    conn.execute.return_value = "UPDATE 1"
    with patch(
        "app.clients.storage_client.proteger_artefacto_worm_internal",
        new=AsyncMock(return_value=WormProtectionOutcome(ok=True, worm_object_id=worm_id, retain_until=retain)),
    ) as st:
        res = await getattr(_svc(conn), proteger)(
            fila["id"], artefacto, actor_id=None, origen="reconciliation"
        )

    assert res == "protegido"
    # cada acto escribe en SU tabla: una revocación protegida no puede marcar la constancia
    assert any(tabla in str(c.args[0]) for c in conn.fetchrow.call_args_list) or acto == "excepcion"
    assert any(tabla + " SET worm_protegido_at" in str(c.args[0]) for c in conn.execute.call_args_list)
    kw = st.await_args.kwargs
    assert kw["tipo"] == f"{prefijo}{rol}"
    assert kw["file_id"] == fila["minio_key_firmado"].split("/", 1)[0]  # nunca de una entrada externa
    assert kw["expediente_id"] == str(fila["expediente_id"]) and kw["retencion_anios"] == 25
    marca = _sql(conn, "worm_protegido_at = now()")
    assert len(marca) == 1 and marca[0].args[1:4] == (fila["id"], artefacto, worm_id)
    asientos = _sql(conn, "INSERT INTO public.audit_log")
    assert len(asientos) == 1
    assert asientos[0].args[5] == f"{asiento}_protegido"


@pytest.mark.asyncio
async def test_en_el_cierre_dispara_con_el_usuario_y_en_la_barrida_con_el_sistema() -> None:
    conn = _conn()
    fila = _fila()
    conn.fetchrow.return_value = fila
    conn.execute.return_value = "UPDATE 1"
    ok = WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4(), retain_until=datetime(2053, 1, 1, tzinfo=UTC))
    with (
        patch("app.clients.storage_client.proteger_artefacto_worm", new=AsyncMock(return_value=ok)) as user,
        patch("app.clients.storage_client.proteger_artefacto_worm_internal", new=AsyncMock(return_value=ok)) as sys_,
    ):
        await _svc(conn).proteger_worm_firma_excepcion(fila["id"], "sello", actor_id="u-1", origen="close")
        await _svc(conn).proteger_worm_firma_excepcion(
            fila["id"], "sello", actor_id=None, origen="reconciliation"
        )
    assert user.await_args.kwargs["actor_id"] == "u-1"
    sys_.assert_awaited_once()


@pytest.mark.asyncio
async def test_ya_protegida_o_sin_clave_se_omite_y_nunca_lanza() -> None:
    conn = _conn()
    svc = _svc(conn)
    with patch("app.clients.storage_client.proteger_artefacto_worm", new=AsyncMock()) as st:
        conn.fetchrow.return_value = _fila(worm_protegido_at=datetime.now(tz=UTC))
        assert await svc.proteger_worm_firma_excepcion(uuid.uuid4(), "sello", actor_id="x", origen="close") == "omitido"
        conn.fetchrow.return_value = _fila(minio_key_firmado=None)
        assert await svc.proteger_worm_firma_excepcion(uuid.uuid4(), "sello", actor_id="x", origen="close") == "omitido"
        conn.fetchrow.return_value = None
        assert await svc.proteger_worm_firma_excepcion(uuid.uuid4(), "sello", actor_id="x", origen="close") == "omitido"
        conn.fetchrow.side_effect = RuntimeError("la base se cayó")
        assert await svc.proteger_worm_firma_excepcion(uuid.uuid4(), "sello", actor_id="x", origen="close") == "pendiente"
    st.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "motivo"),
    [
        (WormProtectionOutcome(ok=False, disabled=True), "worm_disabled"),
        (WormProtectionOutcome(ok=False, transient=True), "worm_no_confirmado"),
    ],
)
async def test_un_fallo_de_storage_programa_backoff_y_la_retencion_sin_resolver_tambien(outcome, motivo) -> None:
    conn = _conn()
    conn.fetchrow.return_value = _fila()
    with patch(
        "app.clients.storage_client.proteger_artefacto_worm_internal", new=AsyncMock(return_value=outcome)
    ):
        res = await _svc(conn).proteger_worm_firma_excepcion(
            uuid.uuid4(), "sello", actor_id=None, origen="reconciliation"
        )
    assert res == "pendiente"
    bump = _sql(conn, "worm_reconcile_intentos = worm_reconcile_intentos + 1")
    assert len(bump) == 1 and bump[0].args[4] == motivo and bump[0].args[5] is False

    conn = _conn()
    conn.fetchrow.return_value = _fila()
    res = await _svc(conn, retencion=None).proteger_worm_firma_excepcion(
        uuid.uuid4(), "personal", actor_id=None, origen="reconciliation"
    )
    assert res == "pendiente"
    assert _sql(conn, "worm_reconcile_intentos = worm_reconcile_intentos + 1")[0].args[4] == "trd_sin_retencion"


@pytest.mark.asyncio
async def test_al_agotar_los_reintentos_deja_asiento_de_sistema() -> None:
    conn = _conn()
    conn.fetchrow.return_value = _fila(worm_reconcile_intentos=settings.indice_reconciliation_max_intentos - 1)
    with patch(
        "app.clients.storage_client.proteger_artefacto_worm_internal",
        new=AsyncMock(return_value=WormProtectionOutcome(ok=False, transient=True)),
    ):
        res = await _svc(conn).proteger_worm_firma_excepcion(
            uuid.uuid4(), "sello", actor_id=None, origen="reconciliation"
        )
    assert res == "agotado"
    asientos = _sql(conn, "INSERT INTO public.audit_log")
    assert asientos and asientos[0].args[5] == "archive.acta_firma_excepcion_worm_reconcile_agotado"


# --- renovación CT ------------------------------------------------------------------------


def _clave() -> str:
    return f"{uuid.uuid4()}/constancia-firmada.xml"


@pytest.mark.asyncio
@pytest.mark.parametrize("acto,_proteger,renovar,tabla,asiento", _ACTOS)
@pytest.mark.parametrize("rol", ["elabora", "entrega", "recibe"])
@pytest.mark.parametrize("artefacto", ["sello", "personal"])
async def test_renueva_con_el_tipo_marca_el_espejo_monotonico_y_asienta(
    acto, _proteger, renovar, tabla, asiento, rol, artefacto
) -> None:
    prefijo = f"acta_firma_{acto}_" + ("personal_" if artefacto == "personal" else "")
    conn = _conn()
    eid, tid, xid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    clave = _clave()
    nueva = datetime(2052, 1, 1, tzinfo=UTC)
    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=True, retain_until=nueva, renovado=True)),
    ) as st:
        res = await getattr(_svc(conn), renovar)(
            eid, artefacto, tid, xid, rol, intentos_previos=0, minio_key_firmado=clave
        )

    assert res == "renovado"
    kw = st.await_args.kwargs
    assert kw["tipo"] == f"{prefijo}{rol}" and kw["expediente_id"] == str(xid)
    # multi-artefacto: sin `file_id` storage extendería solo el más reciente
    assert kw["file_id"] == clave.split("/", 1)[0]
    assert kw["fecha_inicio"] == datetime.now(tz=UTC).date()  # ventana rodante desde HOY
    marca = _sql(conn, "worm_renovacion_intentos = 0")
    assert len(marca) == 1 and marca[0].args[1:] == (eid, artefacto, nueva)
    assert "GREATEST(worm_retain_until" in marca[0].args[0] and tabla in marca[0].args[0]
    asientos = _sql(conn, "INSERT INTO public.audit_log")
    assert len(asientos) == 1
    assert asientos[0].args[5] == f"{asiento}_retencion_renovada"
    assert asientos[0].args[6] == (
        "transferencia_acta_firma_excepcion" if acto == "excepcion"
        else "transferencia_acta_firma_excepcion_revocacion"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("renovar", ["renovar_worm_firma_excepcion", "renovar_worm_revocacion"])
async def test_sin_la_clave_del_artefacto_no_se_renueva_a_ciegas(renovar: str) -> None:
    """Sin `file_id` no hay a qué objeto extenderle la retención: backoff, nunca una llamada a
    storage que tomaría «el más reciente»."""
    conn = _conn()
    with patch("app.clients.storage_client.renovar_retencion_worm_internal", new=AsyncMock()) as st:
        res = await getattr(_svc(conn), renovar)(
            uuid.uuid4(), "sello", uuid.uuid4(), uuid.uuid4(), "recibe",
            intentos_previos=0, minio_key_firmado=None,
        )
    assert res == "pendiente"
    st.assert_not_awaited()
    assert _sql(conn, "worm_renovacion_intentos = worm_renovacion_intentos + 1")[0].args[4] == (
        "sin_minio_key_firmado"
    )


@pytest.mark.asyncio
async def test_el_no_op_confirmado_marca_pero_no_asienta() -> None:
    conn = _conn()
    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=True, retain_until=datetime(2052, 1, 1, tzinfo=UTC), renovado=False)),
    ):
        res = await _svc(conn).renovar_worm_firma_excepcion(
            uuid.uuid4(), "sello", uuid.uuid4(), uuid.uuid4(), "recibe",
            intentos_previos=0, minio_key_firmado=_clave(),
        )
    assert res == "omitido"
    assert len(_sql(conn, "worm_renovacion_intentos = 0")) == 1
    assert _sql(conn, "INSERT INTO public.audit_log") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "motivo"),
    [
        (WormRenewalOutcome(ok=False, disabled=True), "worm_disabled"),
        (WormRenewalOutcome(ok=False, not_found=True), "worm_object_not_found"),
        (WormRenewalOutcome(ok=False, transient=True), "worm_no_confirmado"),
    ],
)
async def test_los_fallos_de_la_renovacion_programan_backoff(outcome, motivo) -> None:
    conn = _conn()
    with patch("app.clients.storage_client.renovar_retencion_worm_internal", new=AsyncMock(return_value=outcome)):
        res = await _svc(conn).renovar_worm_firma_excepcion(
            uuid.uuid4(), "personal", uuid.uuid4(), uuid.uuid4(), "entrega",
            intentos_previos=0, minio_key_firmado=_clave(),
        )
    assert res == "pendiente"
    bump = _sql(conn, "worm_renovacion_intentos = worm_renovacion_intentos + 1")
    assert len(bump) == 1 and bump[0].args[4] == motivo and bump[0].args[5] is False


@pytest.mark.asyncio
async def test_la_renovacion_agotada_deja_asiento_urgente() -> None:
    conn = _conn()
    with patch(
        "app.clients.storage_client.renovar_retencion_worm_internal",
        new=AsyncMock(return_value=WormRenewalOutcome(ok=False, transient=True)),
    ):
        res = await _svc(conn).renovar_worm_firma_excepcion(
            uuid.uuid4(), "sello", uuid.uuid4(), uuid.uuid4(), "recibe",
            intentos_previos=settings.indice_reconciliation_max_intentos - 1,
            minio_key_firmado=_clave(),
        )
    assert res == "agotado"
    assert _sql(conn, "INSERT INTO public.audit_log")[0].args[5] == (
        "archive.acta_firma_excepcion_worm_renovacion_agotado"
    )


# --- el job -------------------------------------------------------------------------------


def _pool() -> MagicMock:
    pool = MagicMock()
    gc = AsyncMock()
    gc.fetch.return_value = [{"slug": "tenant-a"}]
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=gc)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


@pytest.mark.asyncio
async def test_el_job_renueva_constancias_y_revocaciones_con_el_file_id_y_libera_el_lock() -> None:
    conn = _conn()
    conn.fetchval.return_value = True
    clave = _clave()
    base = {
        "artefacto": "personal", "transferencia_id": uuid.uuid4(), "expediente_id": uuid.uuid4(),
        "rol": "recibe", "worm_retain_until": None, "worm_renovacion_intentos": 3,
        "minio_key_firmado": clave,
    }
    fila_exc = {**base, "excepcion_id": uuid.uuid4()}
    fila_rev = {**base, "revocacion_id": uuid.uuid4()}

    @asynccontextmanager
    async def fake(slug: str):
        yield conn

    async def _lista(self, **kw):
        return [fila_rev] if kw.get("acto") == "revocacion" else [fila_exc]

    renovar_exc = AsyncMock(return_value="renovado")
    renovar_rev = AsyncMock(return_value="renovado")
    vacio = AsyncMock(return_value=[])
    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=_pool()),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake),
        patch("app.jobs.indice_reconciliation.IndexRepository.list_worm_renovacion_pending", new=vacio),
        patch("app.jobs.indice_reconciliation.TransferenciaRepository.list_worm_renovacion_pending", new=vacio),
        patch("app.jobs.indice_reconciliation.AipRepository.list_worm_renovacion_pending", new=vacio),
        patch("app.jobs.indice_reconciliation.TransferenciaRepository.list_firma_personal_renovacion_pending", new=vacio),
        patch(
            "app.jobs.indice_reconciliation.TransferenciaRepository.list_firma_excepcion_renovacion_pending",
            new=_lista,
        ),
        patch("app.jobs.indice_reconciliation.TransferenciaService.renovar_worm_firma_excepcion", new=renovar_exc),
        patch("app.jobs.indice_reconciliation.TransferenciaService.renovar_worm_revocacion", new=renovar_rev),
    ):
        resumen = await indice_reconciliation.run_once_worm_renovacion()

    assert resumen["pendientes_vistos"] == 2 and resumen["renovados"] == 2
    for renovar, id_esperado in ((renovar_exc, fila_exc["excepcion_id"]), (renovar_rev, fila_rev["revocacion_id"])):
        args = renovar.await_args
        assert args.args[0] == id_esperado
        assert args.args[1] == "personal" and args.args[4] == "recibe"
        assert args.kwargs == {"intentos_previos": 3, "minio_key_firmado": clave}
    unlock = [
        c for c in conn.execute.call_args_list
        if c.args and "worm_renovacion_firma_excepcion" in c.args[0] and "unlock" in c.args[0]
    ]
    assert len(unlock) == 1


@pytest.mark.asyncio
async def test_el_job_de_proteccion_inicial_tambien_recoge_las_revocaciones() -> None:
    """Sin esta barrida una revocación cuyo Object-Lock falló quedaría sin proteger para siempre
    y la cadena no se podría continuar (el trigger exige la baja protegida)."""
    conn = _conn()
    conn.fetchval.return_value = True
    rev_id = uuid.uuid4()

    @asynccontextmanager
    async def fake(slug: str):
        yield conn

    async def _pendientes(self, limit, *, acto="excepcion"):
        if acto == "revocacion":
            return [{"revocacion_id": rev_id, "artefacto": "sello", "worm_reconcile_intentos": 0}]
        return []

    proteger_rev = AsyncMock(return_value="protegido")
    vacio = AsyncMock(return_value=[])
    with (
        patch("app.jobs.indice_reconciliation.get_pool", return_value=_pool()),
        patch("app.jobs.indice_reconciliation.acquire_tenant", fake),
        patch("app.jobs.indice_reconciliation.TransferenciaRepository.list_firma_personal_worm_pending", new=vacio),
        patch("app.jobs.indice_reconciliation.TransferenciaRepository.list_firma_excepcion_worm_pending", new=_pendientes),
        patch("app.jobs.indice_reconciliation.TransferenciaService.proteger_worm_revocacion", new=proteger_rev),
    ):
        resumen = await indice_reconciliation.run_once_firmas_personales_worm()

    assert resumen["protegidos"] == 1
    assert proteger_rev.await_args.args[:2] == (rev_id, "sello")
    assert proteger_rev.await_args.kwargs == {"actor_id": None, "origen": "reconciliation"}


@pytest.mark.asyncio
async def test_la_visibilidad_nombra_el_tipo_de_storage_de_cada_artefacto() -> None:
    assert _TIPO_FIRMA_EXCEPCION[("sello", "recibe")] == "acta_firma_excepcion_recibe"
    assert _TIPO_FIRMA_EXCEPCION[("personal", "elabora")] == "acta_firma_excepcion_personal_elabora"
    assert len(_TIPO_FIRMA_EXCEPCION) == 9  # sello, personal y sobre (064) por rol
    assert _TIPO_FIRMA_REVOCACION[("sello", "recibe")] == "acta_firma_revocacion_recibe"
    assert _TIPO_FIRMA_REVOCACION[("personal", "elabora")] == "acta_firma_revocacion_personal_elabora"
    assert len(_TIPO_FIRMA_REVOCACION) == 9
