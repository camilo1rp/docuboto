# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""T12 en storage contra Postgres REAL: lo que los mocks no ven.

- AREA-07-10 (migración 018): el CHECK de `tipo` admite las tres firmas personales del acta,
  la unicidad es POR TIPO (no abre un «N por expediente»), y como instrumentos de control NO
  postergan la disposición del contenido (el gate de purga las excluye) pero un legal hold
  sí las cubre.
- AREA-07-05: `find_evento_por_dedup` + `insert_evento` con `detalle.dedup_key`: el POST
  interno de archive es idempotente sobre un registro append-only.
- AREA-07-15: `list_eventos` con VARIOS `object_ref` (`= ANY($1::text[])`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.core.preservacion_tipos import CONTROL_INSTRUMENT_TIPOS
from app.repositories.preservacion_repository import PreservacionRepository
from app.repositories.preservacion_worm_repository import PreservacionWormRepository

pytestmark = pytest.mark.integration

_FIRMAS = ("acta_firma_elabora", "acta_firma_entrega", "acta_firma_recibe")
_FUTURE = datetime.now(tz=UTC) + timedelta(days=10)


async def _insert(repo, expediente_id: str, tipo: str, *, legal_hold: bool = False):
    return await repo.insert(
        expediente_id=expediente_id,
        tipo=tipo,
        bucket="orpycamcp-itest-preservation",
        object_key=f"{expediente_id}/{tipo}/{uuid.uuid4()}.xml",
        version_id="v1",
        sha256="a" * 64,
        retain_until=_FUTURE,
        retention_mode="COMPLIANCE",
        legal_hold=legal_hold,
        file_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_las_tres_firmas_personales_caben_en_el_mismo_expediente_pero_no_se_repiten(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    repo = PreservacionWormRepository(conn, slug)
    exp = f"EXP-{uuid.uuid4().hex[:8]}"

    for tipo in _FIRMAS:  # las tres a la vez (un expediente, una transferencia, tres roles)
        await _insert(repo, exp, tipo)
    # ... pero cada rol una sola vez: el índice único parcial por tipo
    for tipo in _FIRMAS:
        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert(repo, exp, tipo)
    # y un tipo fuera de la taxonomía lo rechaza el CHECK
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert(repo, exp, "acta_firma_inventada")


@pytest.mark.asyncio
async def test_las_firmas_personales_no_posponen_la_disposicion_pero_el_legal_hold_si_las_cubre(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    repo = PreservacionWormRepository(conn, slug)
    exp = f"EXP-{uuid.uuid4().hex[:8]}"
    for tipo in _FIRMAS:
        assert tipo in CONTROL_INSTRUMENT_TIPOS
        await _insert(repo, exp, tipo)

    # instrumentos de control: bajo retención NO bloquean la disposición del contenido
    assert (
        await repo.active_lock_for_expediente(
            exp, exclude_retention_tipos=tuple(CONTROL_INSTRUMENT_TIPOS)
        )
        is None
    )

    exp_hold = f"EXP-{uuid.uuid4().hex[:8]}"
    await _insert(repo, exp_hold, "acta_firma_recibe", legal_hold=True)
    # el legal hold es incondicional: sí bloquea
    lock = await repo.active_lock_for_expediente(
        exp_hold, exclude_retention_tipos=tuple(CONTROL_INSTRUMENT_TIPOS)
    )
    assert lock is not None and lock["tipo"] == "acta_firma_recibe"


@pytest.mark.asyncio
async def test_evento_por_dedup_y_lista_por_varios_object_ref(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    repo = PreservacionRepository(conn, slug)
    exp = f"EXP-{uuid.uuid4().hex[:8]}"
    file_id = str(uuid.uuid4())

    assert await repo.find_evento_por_dedup(object_ref=exp, tipo="FIJACION", dedup_key="k1") is None
    await repo.insert_evento(
        {"object_ref": exp, "tipo": "FIJACION", "resultado": "ok",
         "detalle": {"origen": "recepcion", "dedup_key": "k1"}},
        agente="uuid-receptor",
    )
    ya = await repo.find_evento_por_dedup(object_ref=exp, tipo="FIJACION", dedup_key="k1")
    assert ya is not None
    # otra clave, otro tipo u otro objeto: no es el mismo evento
    assert await repo.find_evento_por_dedup(object_ref=exp, tipo="FIJACION", dedup_key="k2") is None
    assert await repo.find_evento_por_dedup(object_ref=exp, tipo="WORM", dedup_key="k1") is None

    await repo.insert_evento(
        {"object_ref": file_id, "tipo": "VALIDACION_PDFA", "resultado": "ok", "detalle": {}},
        agente=None,
    )
    await repo.insert_evento(
        {"object_ref": "OTRO", "tipo": "FIJACION", "resultado": "ok", "detalle": {}}, agente=None
    )
    refs = {r["object_ref"] for r in await repo.list_eventos([exp, file_id])}
    assert refs == {exp, file_id}  # los de OTRO objeto no entran
    assert {r["object_ref"] for r in await repo.list_eventos(exp)} == {exp}  # un str sigue valiendo
