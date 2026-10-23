# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-6 (ADR-042) contra Postgres REAL: el AIP se versiona y la RAÍZ es la identidad de retención.

Cubre lo que un mock no puede: las restricciones de la migración 023 (`ux_pwo_aip_raiz`,
`ux_aip_sustituye_a`, `aip_version_coherente`), el SQL de `get_by_expediente_and_tipo('aip')` y la
cascada de `renovar_retencion` sobre dos versiones.

Mutación (H4): volver a `ORDER BY created_at DESC` en la rama `tipo='aip'` de
`get_by_expediente_and_tipo` hace que devuelva la v2 y la renovación deje a la v1 sin extender:
cae `test_renovar_retencion_extiende_las_dos_versiones`. El almacén y el audit_log están dobles
(el log inmutable del entorno vivo no se ensucia).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

import app.core.config as config_module
import app.services.aip_service as aip_service_module
import app.services.preservation_worm_service as worm_service_module
from app.repositories.preservacion_repository import PreservacionRepository
from app.repositories.preservacion_worm_repository import PreservacionWormRepository
from app.schemas.preservacion import AipCreate, AipReempaquetarRequest, RenovarRetencionRequest
from app.services.aip_service import AipService
from app.services.preservation_worm_service import PreservationWormService

pytestmark = pytest.mark.integration

_DOC = b"oficio de prueba (itest C-6)"
_IDX0 = b"<indice v='0'/>"
_IDX2 = b"<indice v='2'/>"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class _Almacen:
    """Un S3 en memoria que CUMPLE: recuerda objetos y retenciones, y las devuelve al releer."""

    def __init__(self) -> None:
        self.objetos: dict[str, bytes] = {}
        self.retenciones: dict[str, object] = {}
        self.set_retention_keys: list[str] = []
        m = MagicMock()
        m.bucket_exists = AsyncMock(return_value=True)
        m.get_object_lock_config = AsyncMock(return_value=MagicMock())
        m.copy_object = AsyncMock()

        def _put(bucket, key, stream, **kw):
            self.objetos[key] = stream.read()
            self.retenciones[key] = kw["retention"]
            return MagicMock(version_id=f"v-{key}")

        async def _get(bucket, key, **kw):
            obj = MagicMock()
            obj.read = AsyncMock(return_value=self.objetos[key])
            obj.close = MagicMock()
            return obj

        def _set(bucket, key, retention, **kw):
            self.retenciones[key] = retention
            self.set_retention_keys.append(key)

        async def _ret(bucket, key, **kw):
            return self.retenciones.get(key)

        m.put_object = AsyncMock(side_effect=_put)
        m.get_object = AsyncMock(side_effect=_get)
        m.set_object_retention = AsyncMock(side_effect=_set)
        m.get_object_retention = AsyncMock(side_effect=_ret)
        self.minio = m


async def _seed_file(conn, slug, *, content: bytes, nombre: str, mime: str, bucket: str = "b") -> uuid.UUID:
    fid = uuid.uuid4()
    await conn.execute(f'SET search_path = "tenant_{slug.replace("-", "_")}"')
    await conn.execute(
        "INSERT INTO files (id, filename, mime_type, file_size, sha256, bucket, object_key) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7)",
        fid, nombre, mime, len(content), _sha(content), bucket, f"{fid}/{nombre}",
    )
    return fid


@pytest.fixture
async def mundo(tenant_ctx, monkeypatch):
    """v1 empaquetado de verdad + índice renovado protegido; devuelve lo necesario para la v2."""
    slug, conn = tenant_ctx
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)
    monkeypatch.setattr(config_module.settings, "preservation_replica_enabled", False)
    alm = _Almacen()
    exp = f"EXP-{uuid.uuid4().hex[:8]}"
    doc_id = await _seed_file(conn, slug, content=_DOC, nombre="oficio.pdf", mime="application/pdf")
    idx0_id = await _seed_file(conn, slug, content=_IDX0, nombre="indice.xml", mime="application/xml")
    idx2_id = await _seed_file(conn, slug, content=_IDX2, nombre="indice2.xml", mime="application/xml")
    for fid, content, nombre in ((doc_id, _DOC, "oficio.pdf"), (idx0_id, _IDX0, "indice.xml"), (idx2_id, _IDX2, "indice2.xml")):
        alm.objetos[f"{fid}/{nombre}"] = content

    worm = PreservacionWormRepository(conn, slug)
    vence = datetime.now(tz=UTC) + timedelta(days=400)
    raiz_idx = await worm.insert(
        expediente_id=exp, tipo="indice", bucket="b", object_key="i0", version_id="v0",
        sha256=_sha(_IDX0), retain_until=vence, retention_mode="COMPLIANCE", legal_hold=False,
        file_id=idx0_id,
    )
    await worm.insert(
        expediente_id=exp, tipo="firma_lta_renovada", bucket="b", object_key="i2", version_id="v2",
        sha256=_sha(_IDX2), retain_until=vence, retention_mode="COMPLIANCE", legal_hold=False,
        file_id=idx2_id, renueva_a=raiz_idx["id"],
    )
    with (
        patch.object(aip_service_module, "get_minio", return_value=alm.minio),
        patch.object(worm_service_module, "get_minio", return_value=alm.minio),
        patch.object(aip_service_module.audit, "append", new=AsyncMock()),
        patch.object(worm_service_module.audit, "append", new=AsyncMock()),
    ):
        svc = AipService(conn, slug)
        v1 = await svc.empaquetar_aip(
            AipCreate(
                expediente_id=exp,
                documentos=[{"document_id": "R-1", "nombre": "oficio.pdf", "file_id": doc_id}],
                indice_file_id=idx0_id,
                retencion_anios=1, fecha_inicio=(datetime.now(tz=UTC) + timedelta(days=700)).date(),
            ),
            actor="itest",
        )
        yield {"slug": slug, "conn": conn, "alm": alm, "exp": exp, "v1": v1, "idx2": idx2_id, "svc": svc}


def _req(m, **kw):
    base = dict(
        expediente_id=m["exp"], aip_anterior_id=m["v1"].id, indice_file_id=m["idx2"],
        indice_xml_sha256=_sha(_IDX2), firma_id=uuid.uuid4(), ordinal=1,
    )
    base.update(kw)
    return AipReempaquetarRequest(**base)


@pytest.mark.asyncio
async def test_reempaquetar_crea_la_v2_y_la_cadena(mundo) -> None:
    m = mundo
    v2, creado = await m["svc"].reempaquetar_aip(_req(m), actor="sys")
    assert creado and v2.version == 2 and v2.sustituye_a == m["v1"].id and v2.motivo == "renovacion_lta"
    assert v2.retain_until >= m["v1"].retain_until  # nunca menor
    repo = PreservacionRepository(m["conn"], m["slug"])
    cabeza = await repo.get_aip_cabeza(m["exp"])
    assert cabeza["id"] == v2.id
    assert [r["version"] for r in await repo.listar_versiones_aip(m["exp"])] == [1, 2]
    # idempotente: el reintento devuelve la misma v2 sin subir nada más
    puts = m["alm"].minio.put_object.await_count
    again, creado2 = await m["svc"].reempaquetar_aip(_req(m), actor="sys")
    assert creado2 is False and again.id == v2.id and m["alm"].minio.put_object.await_count == puts
    # el AIP anterior sigue ahí, intacto
    assert (await repo.get_aip(m["v1"].id))["aip_sha256"] == m["v1"].aip_sha256


@pytest.mark.asyncio
async def test_la_identidad_de_retencion_es_la_raiz(mundo) -> None:
    m = mundo
    await m["svc"].reempaquetar_aip(_req(m), actor="sys")
    worm = PreservacionWormRepository(m["conn"], m["slug"])
    fila = await worm.get_by_expediente_and_tipo(m["exp"], "aip")
    assert fila["sha256"] == m["v1"].aip_sha256  # la RAÍZ (v1), no la más reciente


@pytest.mark.asyncio
async def test_renovar_retencion_extiende_las_dos_versiones(mundo) -> None:
    m = mundo
    v2, _ = await m["svc"].reempaquetar_aip(_req(m), actor="sys")
    wsvc = PreservationWormService(m["conn"], m["slug"])
    nueva = datetime.now(tz=UTC) + timedelta(days=5000)
    with (
        patch.object(worm_service_module, "get_minio", return_value=m["alm"].minio),
        patch.object(worm_service_module.audit, "append", new=AsyncMock()),
    ):
        res = await wsvc.renovar_retencion(
            RenovarRetencionRequest(expediente_id=m["exp"], tipo="aip", retain_until=nueva),
            actor="sys",
        )
    assert res.renovado
    assert set(m["alm"].set_retention_keys) == {m["v1"].object_key, v2.object_key}
    worm = PreservacionWormRepository(m["conn"], m["slug"])
    for sha in (m["v1"].aip_sha256, v2.aip_sha256):
        assert (await worm.get_aip_por_sha256(m["exp"], sha))["retain_until"] >= nueva - timedelta(seconds=1)


@pytest.mark.asyncio
async def test_segunda_raiz_aip_del_mismo_expediente_es_imposible(mundo) -> None:
    m = mundo
    worm = PreservacionWormRepository(m["conn"], m["slug"])
    with pytest.raises(asyncpg.UniqueViolationError):
        await worm.insert(
            expediente_id=m["exp"], tipo="aip", bucket="b", object_key="otro", version_id="x",
            sha256="e" * 64, retain_until=datetime.now(tz=UTC) + timedelta(days=9),
            retention_mode="COMPLIANCE", legal_hold=False,
        )


@pytest.mark.asyncio
async def test_un_aip_no_puede_tener_dos_sucesores_ni_version_incoherente(mundo) -> None:
    m = mundo
    await m["svc"].reempaquetar_aip(_req(m), actor="sys")
    conn = m["conn"]
    schema = f"tenant_{m['slug'].replace('-', '_')}"
    await conn.execute(f'SET search_path = "{schema}"')
    base = (
        "INSERT INTO aip (expediente_id, premis_xml, aip_sha256, num_documentos, version, "
        "sustituye_a, motivo) VALUES ($1, '<p/>', $2, 1, $3, $4, $5)"
    )
    with pytest.raises(asyncpg.UniqueViolationError):  # v1 ya tiene sucesor
        await conn.execute(base, m["exp"], "f" * 64, 9, m["v1"].id, "renovacion_lta")
    with pytest.raises(asyncpg.CheckViolationError):  # versión 1 con sustituye_a
        await conn.execute(base, "otro", "f" * 64, 1, m["v1"].id, "ingesta")
    with pytest.raises(asyncpg.CheckViolationError):  # versión 2 sin sustituye_a
        await conn.execute(base, "otro", "f" * 64, 2, None, "ingesta")


@pytest.mark.asyncio
async def test_ingesta_de_contenido_distinto_con_aip_existente_es_409(mundo) -> None:
    """Antes era «un re-empaquetado legítimo»; ahora sería una segunda v1 (mig. 023)."""
    m = mundo
    doc2 = await _seed_file(m["conn"], m["slug"], content=b"otro", nombre="otro.pdf", mime="application/pdf")
    m["alm"].objetos[f"{doc2}/otro.pdf"] = b"otro"
    with (
        patch.object(aip_service_module, "get_minio", return_value=m["alm"].minio),
        patch.object(aip_service_module.audit, "append", new=AsyncMock()),
        pytest.raises(aip_service_module.AipYaExisteError) as exc,
    ):
        await m["svc"].empaquetar_aip(
            AipCreate(
                expediente_id=m["exp"],
                documentos=[{"document_id": "R-2", "nombre": "otro.pdf", "file_id": doc2}],
                retencion_anios=2, fecha_inicio=datetime.now(tz=UTC).date(),
            ),
            actor="itest",
        )
    # El 409 informa el AIP VIGENTE: quien perdió la respuesta de un empaquetado reconcilia con él
    # (cierre del lote C, S2) en vez de tomarlo por un fallo de fixity.
    assert exc.value.aip_id == m["v1"].id
