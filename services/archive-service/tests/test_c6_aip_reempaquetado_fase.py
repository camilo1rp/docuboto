# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-6 (ADR-042): fase de reempaquetado del AIP al renovar el sello de archivo.

Mutaciones que deben caer: (1) empaquetar con el original cuando signature falla (fallback
silencioso, H2) -> `test_error_de_signature_no_empaqueta_el_original`; (2) tratar un fallo del
lote como «sin renovación» -> `test_lote_con_error_no_reempaqueta_ni_marca_revisado`;
(3) reempaquetar sin comparar la huella -> `test_misma_huella_no_llama_a_storage`; (4) no tomar el
lock del expediente -> `test_sin_lock_no_reempaqueta`; (5) versión y asiento fuera de la misma
transacción -> `test_version_estado_y_asiento_en_la_misma_tx`."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from app.clients import signature_client
from app.clients.signature_client import ERROR_ARTEFACTO, ArtefactoVigente
from app.clients.storage_client import AipPackagingOutcome, AipReempaqueOutcome
from app.core.config import settings
from app.services.aip_wiring_service import AipWiringService
from tests.test_aip_wiring_service_indice_only import (
    _expediente,
    _indice_by_id_row,
    _service,
    _wire_happy_path_prereqs,
)

pytestmark = pytest.mark.asyncio

EXP = uuid.uuid4()
ANTERIOR = uuid.uuid4()
NUEVO = uuid.uuid4()
FIRMA = uuid.uuid4()
SHA_V1 = "a" * 64
SHA_V2 = "b" * 64


def _vigente(sha: str = SHA_V2) -> ArtefactoVigente:
    fid = uuid.uuid4()
    return ArtefactoVigente(
        firma_id=str(FIRMA), file_id=fid, minio_key=f"{fid}/x.xml", xml_sha256=sha, ordinal=2
    )


class _Conn:
    """Conexión de prueba que registra el ORDEN de escrituras dentro/fuera de transacción."""

    def __init__(self, lock: bool = True) -> None:
        self.lock = lock
        self.en_tx = False
        self.eventos: list[tuple[str, bool]] = []
        self.fetchval = AsyncMock(side_effect=self._fetchval)
        self.execute = AsyncMock(return_value="UPDATE 1")

    async def _fetchval(self, *_a, **_k):
        return self.lock

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self_inner):
                conn.en_tx = True

            async def __aexit__(self_inner, *a):
                conn.en_tx = False

        return _Tx()

    def anotar(self, nombre: str) -> None:
        self.eventos.append((nombre, self.en_tx))


def _svc(conn: _Conn, *, sha_embebida: str | None = SHA_V1, agotado: bool = False):
    service = AipWiringService(conn, "demo")  # type: ignore[arg-type]
    fila = {
        "expediente_id": EXP,
        "estado": "empaquetado",
        "aip_worm_object_id": ANTERIOR,
        "aip_vigente_id": ANTERIOR,
        "indice_xml_sha256": sha_embebida,
        "reempaque_intentos": 0,
        "reempaque_agotado": agotado,
    }
    service._aip.get_para_reempaque = AsyncMock(return_value=fila)

    async def _ins(*a, **k):
        conn.anotar("insert_version")

    async def _mark(*a, **k):
        conn.anotar("mark_reempaquetado")

    service._aip.insert_version = AsyncMock(side_effect=_ins)
    service._aip.mark_reempaquetado = AsyncMock(side_effect=_mark)
    service._aip.bump_reempaque_intento = AsyncMock()
    return service


def _ok(version: int = 2) -> AipReempaqueOutcome:
    return AipReempaqueOutcome(
        ok=True, aip_id=NUEVO, version=version, sustituye_a=ANTERIOR,
        retain_until=datetime(2040, 1, 1, tzinfo=UTC),
    )


def _audit(conn: _Conn):
    async def _a(*_args, **kw):
        conn.anotar(kw["action"])

    return patch("app.services.aip_wiring_service.audit.append", new=AsyncMock(side_effect=_a))


async def test_vigente_distinto_reempaqueta_y_registra_version_2() -> None:
    conn = _Conn()
    svc = _svc(conn)
    v = _vigente()
    with (
        patch(
            "app.services.aip_wiring_service.storage_client.reempaquetar_aip_internal",
            new=AsyncMock(return_value=_ok()),
        ) as storage,
        _audit(conn),
    ):
        r = await svc.reempaquetar_por_renovacion(EXP, v)
    assert r == "reempaquetado"
    kw = storage.await_args.kwargs
    assert kw["aip_anterior_id"] == ANTERIOR
    assert kw["indice_file_id"] == v.file_id and kw["indice_xml_sha256"] == SHA_V2
    assert kw["ordinal"] == 2 and kw["firma_id"] == str(FIRMA)
    ins = svc._aip.insert_version.await_args.kwargs
    assert ins["version"] == 2 and ins["motivo"] == "renovacion_lta"
    assert ins["sustituye_a_aip_id"] == ANTERIOR
    mark = svc._aip.mark_reempaquetado.await_args.kwargs
    assert mark["aip_vigente_id"] == NUEVO and mark["indice_xml_sha256"] == SHA_V2
    # la retención del espejo viene de storage (solo sube: GREATEST en SQL)
    assert mark["worm_retain_until"] == datetime(2040, 1, 1, tzinfo=UTC)


async def test_version_estado_y_asiento_en_la_misma_tx() -> None:
    conn = _Conn()
    svc = _svc(conn)
    with (
        patch(
            "app.services.aip_wiring_service.storage_client.reempaquetar_aip_internal",
            new=AsyncMock(return_value=_ok()),
        ),
        _audit(conn),
    ):
        await svc.reempaquetar_por_renovacion(EXP, _vigente())
    assert conn.eventos == [
        ("insert_version", True),
        ("mark_reempaquetado", True),
        ("archive.aip_reempaquetado", True),
    ]


async def test_misma_huella_no_llama_a_storage() -> None:
    conn = _Conn()
    svc = _svc(conn, sha_embebida=SHA_V2)
    with patch(
        "app.services.aip_wiring_service.storage_client.reempaquetar_aip_internal",
        new=AsyncMock(),
    ) as storage:
        r = await svc.reempaquetar_por_renovacion(EXP, _vigente(SHA_V2))
    assert r == "omitido"
    storage.assert_not_awaited()


async def test_sin_lock_no_reempaqueta() -> None:
    conn = _Conn(lock=False)
    svc = _svc(conn)
    with patch(
        "app.services.aip_wiring_service.storage_client.reempaquetar_aip_internal",
        new=AsyncMock(),
    ) as storage:
        r = await svc.reempaquetar_por_renovacion(EXP, _vigente())
    assert r == "omitido"
    storage.assert_not_awaited()


async def test_huella_nula_de_aip_previo_se_trata_como_distinta() -> None:
    conn = _Conn()
    svc = _svc(conn, sha_embebida=None)
    with (
        patch(
            "app.services.aip_wiring_service.storage_client.reempaquetar_aip_internal",
            new=AsyncMock(return_value=_ok()),
        ) as storage,
        _audit(conn),
    ):
        assert await svc.reempaquetar_por_renovacion(EXP, _vigente()) == "reempaquetado"
    storage.assert_awaited_once()


@pytest.mark.parametrize(
    ("outcome", "motivo"),
    [
        (AipReempaqueOutcome(ok=False, fixity_mismatch=True), "aip_anterior_fixity_mismatch"),
        (AipReempaqueOutcome(ok=False, indice_invalido=True), "indice_no_es_renovacion_del_embebido"),
        (AipReempaqueOutcome(ok=False, not_found=True), "aip_not_found"),
        (AipReempaqueOutcome(ok=False, disabled=True), "worm_disabled"),
        (AipReempaqueOutcome(ok=False, transient=True), "reempaque_no_confirmado"),
    ],
)
async def test_rechazo_de_storage_hace_backoff_y_no_toca_estado(outcome, motivo) -> None:
    conn = _Conn()
    svc = _svc(conn)
    with patch(
        "app.services.aip_wiring_service.storage_client.reempaquetar_aip_internal",
        new=AsyncMock(return_value=outcome),
    ):
        r = await svc.reempaquetar_por_renovacion(EXP, _vigente())
    assert r == "pendiente"
    assert svc._aip.bump_reempaque_intento.await_args.kwargs["ultimo_error"] == motivo
    svc._aip.insert_version.assert_not_awaited()
    svc._aip.mark_reempaquetado.assert_not_awaited()


async def test_agotar_deja_asiento_en_la_misma_tx() -> None:
    conn = _Conn()
    svc = _svc(conn)
    svc._aip.get_para_reempaque.return_value["reempaque_intentos"] = (
        settings.indice_reconciliation_max_intentos - 1
    )
    with (
        patch(
            "app.services.aip_wiring_service.storage_client.reempaquetar_aip_internal",
            new=AsyncMock(return_value=AipReempaqueOutcome(ok=False, fixity_mismatch=True)),
        ),
        _audit(conn),
    ):
        assert await svc.reempaquetar_por_renovacion(EXP, _vigente()) == "agotado"
    assert conn.eventos == [("archive.aip_reempaque_agotado", True)]
    assert svc._aip.bump_reempaque_intento.await_args.kwargs["agotado"] is True


async def test_ya_sustituido_resincroniza_con_la_cabeza_y_reintenta_una_vez() -> None:
    conn = _Conn()
    svc = _svc(conn)
    cabeza = uuid.uuid4()
    llamadas = AsyncMock(
        side_effect=[AipReempaqueOutcome(ok=False, ya_sustituido=True), _ok(version=3)]
    )
    with (
        patch("app.services.aip_wiring_service.storage_client.reempaquetar_aip_internal", new=llamadas),
        patch(
            "app.services.aip_wiring_service.storage_client.get_aip",
            new=AsyncMock(return_value={"id": str(cabeza)}),
        ),
        _audit(conn),
    ):
        r = await svc.reempaquetar_por_renovacion(EXP, _vigente())
    assert r == "reempaquetado"
    assert [c.kwargs["aip_anterior_id"] for c in llamadas.await_args_list] == [ANTERIOR, cabeza]
    assert svc._aip.insert_version.await_args.kwargs["version"] == 3


async def test_unique_violation_es_idempotente() -> None:
    conn = _Conn()
    svc = _svc(conn)
    svc._aip.insert_version = AsyncMock(side_effect=asyncpg.UniqueViolationError("dup"))
    with (
        patch(
            "app.services.aip_wiring_service.storage_client.reempaquetar_aip_internal",
            new=AsyncMock(return_value=_ok()),
        ),
        _audit(conn),
    ):
        assert await svc.reempaquetar_por_renovacion(EXP, _vigente()) == "omitido"


# -- barrido ------------------------------------------------------------------------------


def _fila(sha: str | None = SHA_V1) -> dict:
    return {
        "expediente_id": uuid.uuid4(),
        "aip_vigente_id": uuid.uuid4(),
        "aip_worm_object_id": uuid.uuid4(),
        "indice_xml_sha256": sha,
        "reempaque_intentos": 0,
        "firma_id": FIRMA,
    }


def _barrido_svc(filas: list[dict]):
    svc = AipWiringService(MagicMock(), "demo")
    # primera lectura: las filas; luego ya revisadas -> vacío
    svc._aip.candidatos_reempaque = AsyncMock(side_effect=[filas, []])
    svc._aip.marcar_revisados = AsyncMock()
    svc._bump_reempaque_backoff = AsyncMock(return_value="pendiente")
    svc.reempaquetar_por_renovacion = AsyncMock(return_value="reempaquetado")
    return svc


async def test_barrido_reempaqueta_solo_las_que_difieren() -> None:
    sana, vieja = _fila(SHA_V2), _fila(SHA_V1)
    sana["firma_id"], vieja["firma_id"] = uuid.uuid4(), uuid.uuid4()
    svc = _barrido_svc([sana, vieja])
    lote = {
        str(sana["firma_id"]): ArtefactoVigente(str(sana["firma_id"]), uuid.uuid4(), "k/x", SHA_V2, 1),
        str(vieja["firma_id"]): ArtefactoVigente(str(vieja["firma_id"]), uuid.uuid4(), "k/x", SHA_V2, 2),
    }
    with patch("app.clients.signature_client.artefactos_vigentes", new=AsyncMock(return_value=lote)):
        res = await svc.barrer_reempaquetado(50)
    assert res["reempaquetados"] == 1 and res["sin_cambio"] == 1
    svc.reempaquetar_por_renovacion.assert_awaited_once()
    assert svc.reempaquetar_por_renovacion.await_args.args[0] == vieja["expediente_id"]
    # la sana avanza en la rotación
    assert svc._aip.marcar_revisados.await_args.args[0] == [sana["expediente_id"]]


async def test_firma_sin_renovacion_no_se_toca() -> None:
    f = _fila()
    svc = _barrido_svc([f])
    with patch("app.clients.signature_client.artefactos_vigentes", new=AsyncMock(return_value={})):
        res = await svc.barrer_reempaquetado(50)
    assert res["sin_cambio"] == 1
    svc.reempaquetar_por_renovacion.assert_not_awaited()


async def test_lote_con_error_no_reempaqueta_ni_marca_revisado() -> None:
    f = _fila()
    svc = _barrido_svc([f])
    with patch("app.clients.signature_client.artefactos_vigentes", new=AsyncMock(return_value=None)):
        res = await svc.barrer_reempaquetado(50)
    svc.reempaquetar_por_renovacion.assert_not_awaited()
    svc._aip.marcar_revisados.assert_not_awaited()
    kw = svc._bump_reempaque_backoff.await_args.kwargs
    assert kw["motivo"] == "artefacto_vigente_no_resuelto" and kw["puede_agotar"] is False
    assert res["fallidos"] == 1


# -- H2: el empaquetado inicial ------------------------------------------------------------


async def test_error_de_signature_no_empaqueta_el_original() -> None:
    service = _service(_Conn())  # type: ignore[arg-type]
    exp = _expediente(radicados=[])
    _wire_happy_path_prereqs(
        service, exp=exp, indice_row=_indice_by_id_row(firma_id=uuid.uuid4())
    )
    service._aip.bump_intento = AsyncMock()
    with (
        patch(
            "app.clients.signature_client.resolver_artefacto_vigente",
            new=AsyncMock(return_value=ERROR_ARTEFACTO),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=True, aip_id=uuid.uuid4())),
        ) as storage,
    ):
        r = await service.intentar_empaquetar(exp.id)
    assert r == "pendiente"
    storage.assert_not_awaited()
    assert service._aip.bump_intento.await_args.kwargs["ultimo_error"] == "artefacto_vigente_no_resuelto"


async def test_empaquetado_inicial_guarda_la_huella_del_vigente() -> None:
    service = _service(_Conn())  # type: ignore[arg-type]
    exp = _expediente(radicados=[])
    _wire_happy_path_prereqs(
        service, exp=exp, indice_row=_indice_by_id_row(firma_id=uuid.uuid4())
    )
    service._resolve_aip_retention = AsyncMock(return_value=(5, datetime(2026, 1, 1).date()))
    service._resolve_aip_pdfa_profile = AsyncMock(return_value=None)
    service._aip.mark_empaquetado = AsyncMock(return_value=1)
    v = _vigente()
    with (
        patch("app.clients.signature_client.resolver_artefacto_vigente", new=AsyncMock(return_value=v)),
        patch(
            "app.services.aip_wiring_service.document_client.fetch_anexos_internal",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(return_value=AipPackagingOutcome(ok=True, aip_id=uuid.uuid4())),
        ),
        patch("app.services.aip_wiring_service.audit.append", new=AsyncMock()),
    ):
        assert await service.intentar_empaquetar(exp.id) == "empaquetado"
    kw = service._aip.mark_empaquetado.await_args.kwargs
    assert kw["indice_xml_sha256"] == SHA_V2 and kw["indice_file_id"] == v.file_id


async def test_empaquetado_sin_huella_del_original_hace_backoff() -> None:
    service = _service(_Conn())  # type: ignore[arg-type]
    exp = _expediente(radicados=[])
    _wire_happy_path_prereqs(service, exp=exp, indice_row=_indice_by_id_row())
    service._aip.bump_intento = AsyncMock()
    with (
        patch("app.services.aip_wiring_service._huella_indice_original", new=AsyncMock(return_value=None)),
        patch(
            "app.services.aip_wiring_service.storage_client.empaquetar_aip_internal",
            new=AsyncMock(),
        ) as storage,
    ):
        assert await service.intentar_empaquetar(exp.id) == "pendiente"
    storage.assert_not_awaited()


async def test_cliente_error_y_sin_renovacion_son_tipos_distintos() -> None:
    assert not isinstance(ERROR_ARTEFACTO, signature_client.SinRenovacion)
    assert not isinstance(signature_client.SIN_RENOVACION, signature_client.ErrorArtefacto)
