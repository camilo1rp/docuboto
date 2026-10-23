# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-6 (ADR-042): `mark_empaquetado` guarda la huella del índice embebido e inserta la VERSIÓN 1
en la misma llamada (el servicio la ejecuta dentro de su `conn.transaction()`).

Mutaciones: quitar el `insert_version` de `mark_empaquetado` (cae `test_inserta_version_1`);
insertar la versión aunque otra llamada ganó la carrera (cae `test_sin_filas_afectadas_no_inserta`)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.aip_repository import AipRepository

pytestmark = pytest.mark.asyncio


def _conn(tag: str) -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=tag)
    return conn


async def test_inserta_version_1() -> None:
    conn = _conn("UPDATE 1")
    exp, aip, idx, firma = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    n = await AipRepository(conn).mark_empaquetado(
        exp, aip, indice_file_id=idx, indice_xml_sha256="d" * 64, firma_id=firma
    )
    assert n == 1
    assert conn.execute.await_count == 2
    upd, ins = conn.execute.await_args_list
    assert "indice_xml_sha256=$4" in upd.args[0] and "aip_vigente_id=$2" in upd.args[0]
    assert upd.args[4] == "d" * 64
    assert "INSERT INTO expediente_aip_version" in ins.args[0]
    # (expediente, version, aip_id, indice_file_id, sha, firma_id, ordinal, motivo, sustituye_a)
    assert ins.args[1:] == (exp, 1, aip, idx, "d" * 64, firma, None, "ingesta", None)


async def test_sin_filas_afectadas_no_inserta() -> None:
    conn = _conn("UPDATE 0")
    n = await AipRepository(conn).mark_empaquetado(
        uuid.uuid4(), uuid.uuid4(), indice_file_id=uuid.uuid4(), indice_xml_sha256="d" * 64
    )
    assert n == 0
    assert conn.execute.await_count == 1  # solo el UPDATE
