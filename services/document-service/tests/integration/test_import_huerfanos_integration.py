# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Importación fallida: los binarios huérfanos se PURGAN, o quedan en cola (AREA-09-14).

Postgres REAL: la atomicidad asiento+cola y el reintento del barrido solo existen en
el motor. storage va sustituido por un doble que registra lo que se le pide (el
contrato saliente real —token, ruta— lo prueba `test_import_purga_huerfanos.py`).

Mecanismo declarado: cola durable `import_binario_huerfano` + intento inmediato +
barrido. El asiento `document.import_binarios_sin_radicado` se escribe SIEMPRE y en la
misma transacción que la cola; la purga confirmada deja `document.import_binarios_purgados`.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
import pytest_asyncio

from app.clients import storage_client
from app.clients.storage_client import ResultadoPurga, StorageContentError
from app.core.database import tenant_schema
from app.services.import_huerfanos_service import ImportHuerfanosService
from app.services.import_service import ImportService
from tests.test_import import _pkg

pytestmark = pytest.mark.integration

_TRACK = "2026-OTRA-E-000001"


class _Storage:
    """Doble de storage-service: contesta lo que se le diga y apunta lo que recibe."""

    def __init__(self, resultado: str = "purgado", *, cae: bool = False) -> None:
        self.resultado, self.cae = resultado, cae
        self.pedidos: list[dict] = []

    async def purgar(self, **kw):
        self.pedidos.append(kw)
        if self.cae:
            raise StorageContentError("storage-service inalcanzable")
        return [ResultadoPurga(f, self.resultado) for f in kw["file_ids"]]


async def _importar_que_falla(conn, slug, storage: _Storage) -> uuid.UUID:
    """Importa un paquete con un anexo; el INSERT del radicado revienta (reversión total)."""
    subido = uuid.uuid4()
    with (
        patch(
            "app.services.import_service.storage_client.upload_bytes",
            new=AsyncMock(return_value=storage_client.ArchivoSubido(file_id=subido, sha256="a" * 64)),
        ),
        patch("app.services.import_service.ImportRepository.insert_radicado",
              new=AsyncMock(side_effect=RuntimeError("falla de BD"))),
        patch("app.services.import_huerfanos_service.storage_client.purgar_huerfanos", new=storage.purgar),
    ):
        with pytest.raises(RuntimeError):
            await ImportService(conn, slug).import_package(
                zip_bytes=_pkg([{"tracking": _TRACK, "anexos": [("a.pdf", b"PDF")]}]),
                max_clearance=3,
                actor=uuid.uuid4(),
            )
    return subido


async def _estado(conn, fid) -> asyncpg.Record:
    return await conn.fetchrow(
        "SELECT estado, intentos, ultimo_error FROM import_binario_huerfano WHERE file_id = $1", fid
    )


async def _acciones(conn, slug) -> list[str]:
    return [
        r["action"]
        for r in await conn.fetch(
            "SELECT action FROM public.audit_log WHERE tenant_slug = $1 AND action LIKE 'document.import_binario%' "
            "ORDER BY id",
            slug,
        )
    ]


@pytest_asyncio.fixture
async def ctx(tenant_ctx):
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    return slug, conn


@pytest.mark.asyncio
async def test_la_importacion_fallida_purga_sus_binarios_y_deja_los_dos_asientos(ctx) -> None:
    slug, conn = ctx
    st = _Storage("purgado")
    fid = await _importar_que_falla(conn, slug, st)
    (pedido,) = st.pedidos
    assert pedido["file_ids"] == [fid]
    assert pedido["tenant_slug"] == slug and pedido["subido_desde"].tzinfo is not None
    assert (await _estado(conn, fid))["estado"] == "purgado"
    assert await _acciones(conn, slug) == [
        "document.import_binarios_sin_radicado",
        "document.import_binarios_purgados",
    ]


@pytest.mark.asyncio
async def test_si_storage_cae_queda_pendiente_con_su_asiento_y_el_barrido_la_recoge(ctx) -> None:
    slug, conn = ctx
    fid = await _importar_que_falla(conn, slug, _Storage(cae=True))
    est = await _estado(conn, fid)
    assert est["estado"] == "pendiente" and est["intentos"] == 1
    assert "inalcanzable" in est["ultimo_error"]
    assert await _acciones(conn, slug) == ["document.import_binarios_sin_radicado"], (
        "sin confirmación de storage NO hay asiento de purga"
    )

    # El barrido, con storage ya disponible, la resuelve.
    st = _Storage("purgado")
    with patch("app.services.import_huerfanos_service.storage_client.purgar_huerfanos", new=st.purgar):
        resultado = await ImportHuerfanosService(conn, slug).purgar()
    assert resultado == {"purgado": 1}
    assert (await _estado(conn, fid))["estado"] == "purgado"
    assert (await _acciones(conn, slug))[-1] == "document.import_binarios_purgados"
    # Y una segunda pasada no vuelve a pedir nada ni duplica el asiento.
    st2 = _Storage("purgado")
    with patch("app.services.import_huerfanos_service.storage_client.purgar_huerfanos", new=st2.purgar):
        assert await ImportHuerfanosService(conn, slug).purgar() == {}
    assert st2.pedidos == []
    assert (await _acciones(conn, slug)).count("document.import_binarios_purgados") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resultado,estado",
    [("referenciado", "conservado"), ("preexistente", "conservado"),
     ("protegido_worm", "protegido"), ("no_encontrado", "purgado")],
)
async def test_cada_decision_de_storage_cierra_su_fila_en_su_estado(ctx, resultado, estado) -> None:
    slug, conn = ctx
    fid = await _importar_que_falla(conn, slug, _Storage(resultado))
    assert (await _estado(conn, fid))["estado"] == estado


@pytest.mark.asyncio
async def test_un_error_por_archivo_se_reintenta_y_no_se_da_por_purgado(ctx) -> None:
    """Mutación: tratar `error` como terminal → la fila sale 'purgado' y el objeto sigue
    en el almacén sin que nada lo reintente."""
    slug, conn = ctx
    fid = await _importar_que_falla(conn, slug, _Storage("error"))
    est = await _estado(conn, fid)
    assert est["estado"] == "pendiente" and est["intentos"] == 1
    assert "document.import_binarios_purgados" not in await _acciones(conn, slug)


@pytest.mark.asyncio
async def test_agotados_los_reintentos_pasa_a_agotado_para_revision(ctx) -> None:
    slug, conn = ctx
    fid = await _importar_que_falla(conn, slug, _Storage(cae=True))
    for _ in range(3):
        with (
            patch("app.services.import_huerfanos_service.storage_client.purgar_huerfanos",
                  new=_Storage(cae=True).purgar),
            patch("app.services.import_huerfanos_service.settings.purga_huerfanos_max_intentos", 3),
        ):
            await ImportHuerfanosService(conn, slug).purgar()
    est = await _estado(conn, fid)
    assert est["estado"] == "agotado" and est["intentos"] == 3
    # El paso a 'agotado' (un binario que se queda en el almacén sin dueño hasta que
    # alguien lo revise) es un acto: deja SU asiento, una sola vez.
    acciones = await _acciones(conn, slug)
    assert acciones.count("document.import_binario_agotado") == 1
    fila = await conn.fetchrow(
        "SELECT payload FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = 'document.import_binario_agotado'",
        slug,
    )
    import json

    payload = json.loads(fila["payload"]) if isinstance(fila["payload"], str) else fila["payload"]
    assert payload["binarios"][0]["file_id"] == str(fid)
    assert payload["binarios"][0]["intentos"] == 3


@pytest.mark.asyncio
async def test_si_falla_el_asiento_de_agotado_la_fila_no_pasa_a_agotado(ctx) -> None:
    """Estado y asiento son UN acto (Regla 6): sin asiento no se agota."""
    slug, conn = ctx
    fid = await _importar_que_falla(conn, slug, _Storage(cae=True))
    with (
        patch("app.services.import_huerfanos_service.storage_client.purgar_huerfanos",
              new=_Storage(cae=True).purgar),
        patch("app.services.import_huerfanos_service.settings.purga_huerfanos_max_intentos", 2),
        patch("app.services.import_huerfanos_service.audit.append",
              new=AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        with pytest.raises(RuntimeError):
            await ImportHuerfanosService(conn, slug).purgar()
    est = await _estado(conn, fid)
    assert est["estado"] == "pendiente" and est["intentos"] == 1


@pytest.mark.asyncio
async def test_si_falla_el_asiento_no_queda_nada_en_la_cola(ctx) -> None:
    """Cola y asiento son UN acto: sin asiento no hay fila (y viceversa)."""
    slug, conn = ctx
    with patch("app.services.import_service.audit.append", new=AsyncMock(side_effect=RuntimeError("boom"))):
        fid = await _importar_que_falla(conn, slug, _Storage("purgado"))
    assert await _estado(conn, fid) is None
