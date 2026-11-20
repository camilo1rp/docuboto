# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-6: `signature_api.artefactos_vigentes` y su admisión para una entidad suspendida.

La llama archive (fase de reempaquetado del AIP). La conservación no se detiene porque la
entidad esté suspendida (ADR-035 §8), pero la excepción es UNA operación que la pide por
nombre, y solo para `suspended` (no `inactive`): se prueba la admitida Y las vetadas.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import asyncpg
import pytest

from signature import api as signature_api
from signature.core import conservacion

F1 = uuid.UUID("11111111-aaaa-bbbb-cccc-000000000001")
F2 = uuid.UUID("11111111-aaaa-bbbb-cccc-000000000002")
FILE1 = uuid.UUID("22222222-aaaa-bbbb-cccc-000000000001")
CLAVE = f"{FILE1}/indice-v1-firmado-lta2.xml"


class _Conn:
    """Registro de la entidad por `fetchrow`; la vista de artefactos por `fetch`."""

    def __init__(self, status: str, filas: list | Exception | None = None) -> None:
        self.fila = {"status": status, "provisioning_status": "active", "hay_schema": True}
        self.filas = filas if filas is not None else []
        self.fetch = AsyncMock(side_effect=self._fetch)

    async def _fetch(self, sql: str, *args: object) -> list:
        if isinstance(self.filas, Exception):
            raise self.filas
        return self.filas

    async def fetchrow(self, sql: str, *args: object) -> dict:
        return self.fila


@pytest.fixture
def asientos(monkeypatch: pytest.MonkeyPatch) -> list:
    caja: list = []
    monkeypatch.setattr(
        conservacion.audit, "append", AsyncMock(side_effect=lambda *a, **k: caja.append(k))
    )
    monkeypatch.setattr(conservacion, "_YA_ASENTADAS", set())
    return caja


def _entidad(monkeypatch: pytest.MonkeyPatch, conn: _Conn) -> None:
    @asynccontextmanager
    async def _acquire(_slug: str):
        yield conn

    monkeypatch.setattr(signature_api, "acquire_tenant", _acquire)


async def _lote(*ids: object):
    return await signature_api.artefactos_vigentes(
        tenant_slug="demo", firma_ids=[str(i) for i in ids]
    )


@pytest.mark.asyncio
async def test_devuelve_el_xml_vigente_de_las_firmas_que_existen(monkeypatch, asientos) -> None:
    conn = _Conn(
        "active",
        [{"firma_id": F1, "minio_key": CLAVE, "xml_sha256": "a" * 64, "ordinal": 2}],
    )
    _entidad(monkeypatch, conn)

    lote = await _lote(F1, F2)

    assert lote == {
        str(F1): signature_api.ArtefactoVigente(
            firma_id=str(F1), file_id=FILE1, minio_key=CLAVE, xml_sha256="a" * 64, ordinal=2
        )
    }
    # Solo se pidió lo pedido: la consulta recibe los dos ids.
    assert conn.fetch.await_args.args[1] == [F1, F2]
    assert asientos == []  # entidad operativa: no hay conservación que asentar


@pytest.mark.asyncio
async def test_un_tenant_sin_la_migracion_26_no_tiene_nada_que_renovar(monkeypatch) -> None:
    _entidad(monkeypatch, _Conn("active", asyncpg.UndefinedTableError("firma_artefacto_vigente")))
    assert await _lote(F1) == {}


@pytest.mark.asyncio
async def test_un_error_no_es_sin_renovacion(monkeypatch) -> None:
    """Quien llama NUNCA debe leer un fallo como «sin renovación»: `None`, no `{}`."""
    _entidad(monkeypatch, _Conn("active", RuntimeError("BD caída")))
    assert await _lote(F1) is None
    assert await signature_api.artefactos_vigentes(tenant_slug="demo", firma_ids=["no-es-uuid"]) is None


@pytest.mark.asyncio
async def test_una_entidad_suspendida_si_conserva_y_deja_asiento(monkeypatch, asientos) -> None:
    _entidad(monkeypatch, _Conn("suspended"))
    assert await _lote(F1) == {}
    assert [a["action"] for a in asientos] == ["entidad.conservacion_admitida"]
    assert asientos[0]["payload"] == {"estado_entidad": "suspended", "operacion": "artefactos_vigentes"}


@pytest.mark.asyncio
async def test_una_entidad_dada_de_baja_no_conserva(monkeypatch, asientos) -> None:
    _entidad(monkeypatch, _Conn("inactive"))
    assert await _lote(F1) is None
    assert asientos == []


@pytest.mark.asyncio
async def test_lo_demas_sigue_vetado_para_una_entidad_suspendida(monkeypatch, asientos) -> None:
    """Control negativo de la excepción: el sello de sistema y el estado del circuito no conservan."""
    _entidad(monkeypatch, _Conn("suspended"))
    sello = await signature_api.sign_as_system(
        tenant_slug="demo", payload_xml="<x/>", objeto_id="e", indice_id=None, version=1
    )
    assert sello.signed is None and sello.transient
    with pytest.raises(signature_api.CircuitoNoVerificableError):
        await signature_api.get_estado_circuito(borrador_id="b", tenant_slug="demo")
    assert asientos == []
