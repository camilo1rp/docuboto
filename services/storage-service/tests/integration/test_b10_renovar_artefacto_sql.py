# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""B10 §8 contra Postgres REAL: la renovación del ArchiveTimeStamp de una firma LTA.

Lo que un mock no ve: que la fila WORM del XML renovado hereda `retain_until` / modo / legal hold
del anterior y apunta a él (`renueva_a`); que la llamada es idempotente por `dedup_key`; y que la
fila WORM, el evento PREMIS `RENOVACION` y el asiento se comprometen JUNTOS (si el INSERT del
evento falla, no queda fila WORM). MinIO va con un doble (lo probado es el SQL)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

import app.services.preservation_worm_service as svc_module
from app.repositories.preservacion_repository import PreservacionRepository
from app.repositories.preservacion_worm_repository import PreservacionWormRepository
from app.schemas.preservacion import RenovarArtefactoRequest
from app.services.preservation_worm_service import PreservationWormService

pytestmark = pytest.mark.integration

_XML = b"<firma>renovada</firma>"
_SHA = hashlib.sha256(_XML).hexdigest()
_RETAIN = datetime.now(tz=UTC) + timedelta(days=3650)


def _schema(slug: str) -> str:
    return f"tenant_{slug.replace('-', '_')}"


def _minio() -> MagicMock:
    minio = MagicMock()
    minio.bucket_exists = AsyncMock(return_value=True)
    minio.make_bucket = AsyncMock()
    minio.get_object_lock_config = AsyncMock(return_value=MagicMock())
    minio._ret = None

    def _put(*_a, **kw):
        minio._ret = kw.get("retention")
        minio._hold = kw.get("legal_hold")
        return MagicMock(version_id="v-ren")

    minio.put_object = AsyncMock(side_effect=_put)
    minio.get_object_retention = AsyncMock(side_effect=lambda *a, **k: minio._ret)

    async def _get_object(_b, _k):
        obj = MagicMock()
        obj.read = AsyncMock(return_value=_XML)
        obj.close = MagicMock()
        return obj

    minio.get_object = AsyncMock(side_effect=_get_object)

    def _set_ret(_b, _k, retention, **_kw):
        minio._ret = retention

    minio.set_object_retention = AsyncMock(side_effect=_set_ret)
    return minio


async def _file(
    conn: asyncpg.Connection, slug: str, file_id: uuid.UUID, nombre: str, mime: str = "application/xml"
) -> None:
    await conn.execute(f'SET search_path = "{_schema(slug)}", public')
    await conn.execute(
        "INSERT INTO files (id, filename, mime_type, file_size, sha256, bucket, object_key)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7)",
        file_id, nombre, mime, len(_XML), _SHA, f"orpycamcp-{slug}-documents", f"{file_id}/{nombre}",
    )


async def _escenario(
    conn, slug, *, con_worm: bool = True, legal_hold: bool = False, tipo: str = "indice", mime_nuevo: str = "application/xml"
):
    anterior, nuevo, firma = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _file(conn, slug, anterior, "ind-firmado.xml")
    await _file(conn, slug, nuevo, "ind-firmado-lta1.xml", mime_nuevo)
    fila = None
    if con_worm:
        fila = await PreservacionWormRepository(conn, slug).insert(
            expediente_id="EXP-1", tipo=tipo, bucket=f"orpycamcp-{slug}-preservation",
            object_key=f"{anterior}/ind-firmado.xml", version_id="v0", sha256=_SHA,
            retain_until=_RETAIN, retention_mode="COMPLIANCE", legal_hold=legal_hold,
            file_id=anterior,
        )
    req = RenovarArtefactoRequest(
        file_id_anterior=anterior, file_id_nuevo=nuevo, sha256_nuevo=_SHA, firma_id=firma, ordinal=1
    )
    return req, fila


@pytest.fixture(autouse=True)
def _mundo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc_module.settings, "preservation_enabled", True)
    monkeypatch.setattr(svc_module, "get_minio", lambda: _minio())


async def _cuenta(conn, slug, sql: str, *args) -> int:
    await conn.execute(f'SET search_path = "{_schema(slug)}", public')
    return await conn.fetchval(sql, *args)


@pytest.mark.asyncio
async def test_hereda_retain_until_modo_y_legal_hold_y_apunta_al_anterior(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    req, fila = await _escenario(conn, slug, legal_hold=True)
    resp = await PreservationWormService(conn, slug).renovar_artefacto(req, actor="sys")

    assert resp.protegido and resp.retain_until == fila["retain_until"] and not resp.ya_registrada
    nueva = await PreservacionWormRepository(conn, slug).get_by_file_id(req.file_id_nuevo)
    assert nueva["tipo"] == "firma_lta_renovada"
    assert nueva["retain_until"] == fila["retain_until"]
    assert nueva["retention_mode"] == fila["retention_mode"]
    assert nueva["legal_hold"] is True
    assert nueva["expediente_id"] == "EXP-1"
    await conn.execute(f'SET search_path = "{_schema(slug)}", public')
    assert await conn.fetchval(
        "SELECT renueva_a FROM preservacion_worm_objeto WHERE file_id = $1", req.file_id_nuevo
    ) == fila["id"]
    ev = await conn.fetchrow(
        "SELECT tipo, hash_before, hash_after, detalle FROM preservacion_evento WHERE object_ref = $1",
        str(req.file_id_nuevo),
    )
    assert ev["tipo"] == "RENOVACION" and ev["hash_after"] == _SHA


@pytest.mark.asyncio
async def test_es_idempotente_por_dedup_key(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    req, _ = await _escenario(conn, slug)
    svc = PreservationWormService(conn, slug)
    primera = await svc.renovar_artefacto(req, actor="sys")
    segunda = await svc.renovar_artefacto(req, actor="sys")

    assert segunda.ya_registrada and segunda.evento_id == primera.evento_id
    assert await _cuenta(
        conn, slug, "SELECT count(*) FROM preservacion_worm_objeto WHERE tipo='firma_lta_renovada'"
    ) == 1
    assert await _cuenta(
        conn, slug, "SELECT count(*) FROM preservacion_evento WHERE tipo='RENOVACION'"
    ) == 1


@pytest.mark.asyncio
async def test_si_el_evento_premis_falla_no_queda_la_fila_worm(
    tenant_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    req, _ = await _escenario(conn, slug)

    async def _falla(self, data, *, agente):
        raise RuntimeError("fallo forzado del INSERT del evento")

    monkeypatch.setattr(PreservacionRepository, "insert_evento", _falla)
    with pytest.raises(RuntimeError):
        await PreservationWormService(conn, slug).renovar_artefacto(req, actor="sys")

    assert await _cuenta(
        conn, slug, "SELECT count(*) FROM preservacion_worm_objeto WHERE tipo='firma_lta_renovada'"
    ) == 0


@pytest.mark.asyncio
async def test_sin_worm_en_el_anterior_solo_asienta_premis(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    req, _ = await _escenario(conn, slug, con_worm=False)
    resp = await PreservationWormService(conn, slug).renovar_artefacto(req, actor="sys")

    assert resp.protegido is False and resp.retain_until is None
    assert await _cuenta(
        conn, slug, "SELECT count(*) FROM preservacion_worm_objeto WHERE tipo='firma_lta_renovada'"
    ) == 0
    assert await _cuenta(
        conn, slug, "SELECT count(*) FROM preservacion_evento WHERE tipo='RENOVACION'"
    ) == 1


# --- B10 audit lane L: cascada de la retención + validación del anterior -------------------


@pytest.mark.asyncio
async def test_renovar_retencion_extiende_tambien_el_xml_renovado(tenant_ctx) -> None:
    """SEG #3 / ARCH H5: el XML vigente (`firma_lta_renovada`) no puede salir de COMPLIANCE antes
    que el índice del que hereda. Mutación: quitar `_extender_cadena_renovada` -> cae."""
    from app.schemas.preservacion import RenovarRetencionRequest

    slug, conn = tenant_ctx
    req, fila = await _escenario(conn, slug)
    svc = PreservationWormService(conn, slug)
    await svc.renovar_artefacto(req, actor="sys")

    nueva = _RETAIN + timedelta(days=3650)
    resp = await svc.renovar_retencion(
        RenovarRetencionRequest(expediente_id="EXP-1", tipo="indice", retain_until=nueva), actor="sys"
    )
    assert resp.renovado
    repo = PreservacionWormRepository(conn, slug)
    assert (await repo.get_by_file_id(req.file_id_nuevo))["retain_until"] == nueva
    assert await _cuenta(
        conn, slug,
        "SELECT count(*) FROM preservacion_evento WHERE tipo='RENOVACION' AND detalle->>'subtipo'='retencion_en_cascada'",
    ) == 1


@pytest.mark.asyncio
async def test_la_cascada_corre_aunque_la_raiz_ya_este_al_dia(tenant_ctx) -> None:
    """Una pasada que extendió la raíz y se cortó antes de la cascada no se reintenta (archive
    solo mira la raíz): la siguiente llamada, con la raíz en no-op, tiene que cerrar la cascada."""
    from app.schemas.preservacion import RenovarRetencionRequest

    slug, conn = tenant_ctx
    req, fila = await _escenario(conn, slug)
    svc = PreservationWormService(conn, slug)
    await svc.renovar_artefacto(req, actor="sys")
    nueva = _RETAIN + timedelta(days=3650)
    await conn.execute(f'SET search_path = "{_schema(slug)}", public')
    await conn.execute("UPDATE preservacion_worm_objeto SET retain_until = $1 WHERE id = $2", nueva, fila["id"])

    resp = await svc.renovar_retencion(
        RenovarRetencionRequest(expediente_id="EXP-1", tipo="indice", retain_until=nueva), actor="sys"
    )
    assert not resp.renovado  # la raíz estaba al día
    assert (await PreservacionWormRepository(conn, slug).get_by_file_id(req.file_id_nuevo))["retain_until"] == nueva


@pytest.mark.asyncio
async def test_la_segunda_renovacion_hereda_el_mayor_retain_until_de_la_cadena(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    req, fila = await _escenario(conn, slug)
    svc = PreservationWormService(conn, slug)
    await svc.renovar_artefacto(req, actor="sys")
    # La raíz se extiende SIN cascada (p. ej. antes de existir la corrección): el eslabón 1 queda atrás.
    mayor = _RETAIN + timedelta(days=5000)
    await conn.execute(f'SET search_path = "{_schema(slug)}", public')
    await conn.execute("UPDATE preservacion_worm_objeto SET retain_until = $1 WHERE id = $2", mayor, fila["id"])

    nuevo2, firma = uuid.uuid4(), req.firma_id
    await _file(conn, slug, nuevo2, "ind-firmado-lta2.xml")
    req2 = RenovarArtefactoRequest(
        file_id_anterior=req.file_id_nuevo, file_id_nuevo=nuevo2, sha256_nuevo=_SHA, firma_id=firma, ordinal=2
    )
    resp = await svc.renovar_artefacto(req2, actor="sys")
    assert resp.retain_until == mayor
    assert (await PreservacionWormRepository(conn, slug).get_by_file_id(nuevo2))["retain_until"] == mayor


@pytest.mark.asyncio
async def test_el_anterior_que_no_es_artefacto_de_firma_se_rechaza(tenant_ctx) -> None:
    from app.services.preservation_worm_service import ArtefactoNoEsDeFirmaError

    slug, conn = tenant_ctx
    req, _ = await _escenario(conn, slug, tipo="aip")
    with pytest.raises(ArtefactoNoEsDeFirmaError):
        await PreservationWormService(conn, slug).renovar_artefacto(req, actor="sys")
    assert await _cuenta(
        conn, slug, "SELECT count(*) FROM preservacion_worm_objeto WHERE tipo='firma_lta_renovada'"
    ) == 0


@pytest.mark.asyncio
async def test_el_nuevo_que_no_es_xml_se_rechaza(tenant_ctx) -> None:
    from app.services.preservation_worm_service import ArtefactoNoEsDeFirmaError

    slug, conn = tenant_ctx
    req, _ = await _escenario(conn, slug, mime_nuevo="application/pdf")
    with pytest.raises(ArtefactoNoEsDeFirmaError):
        await PreservationWormService(conn, slug).renovar_artefacto(req, actor="sys")
