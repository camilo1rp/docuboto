# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""FIRMA-2 (ADR-030 addendum): los tipos de constancia y revocación de «firma inobtenible» son
multi-artefacto. Identidad = (expediente, tipo, file_id).

Defecto que esto cierra: `get_by_expediente_and_tipo` (ORDER BY created_at DESC LIMIT 1) hacía
«idempotente» a la SEGUNDA constancia de un rol —la devolvía como ya protegida y la dejaba SIN
Object-Lock— y la renovación CT extendía solo la última.
"""

from __future__ import annotations

import pathlib
import re
import uuid
from unittest.mock import AsyncMock

import pytest

import app.core.config as config_module
from app.core.preservacion_tipos import (
    ALL_WORM_TIPOS,
    CONTROL_INSTRUMENT_TIPOS,
    MULTI_ARTEFACTO_TIPOS,
)
from app.repositories.preservacion_worm_repository import PreservacionWormRepository
from tests.conftest import INTERNAL_HEADERS, make_worm_row

_MIGRACION = (
    pathlib.Path(__file__).resolve().parent.parent
    / "migrations"
    / "tenant"
    / "021_preservacion_worm_sobre_fundamento.sql"
)


def test_son_dieciocho_y_son_instrumentos_de_control() -> None:
    assert len(MULTI_ARTEFACTO_TIPOS) == 18
    assert MULTI_ARTEFACTO_TIPOS <= CONTROL_INSTRUMENT_TIPOS <= ALL_WORM_TIPOS
    assert {t for t in MULTI_ARTEFACTO_TIPOS if "revocacion" in t} == {
        f"acta_firma_revocacion_{v}{r}"
        for v in ("", "personal_", "sobre_")
        for r in ("elabora", "entrega", "recibe")
    }


def test_la_migracion_indexa_exactamente_los_dieciocho_por_expediente_tipo_file_id() -> None:
    sql = _MIGRACION.read_text(encoding="utf-8")
    sql_020 = (_MIGRACION.parent / "020_preservacion_worm_multi_artefacto.sql").read_text(encoding="utf-8")
    m = re.search(r"ux_pwo_multi_artefacto_file\s+ON preservacion_worm_objeto \(([^)]*)\)(.*?);", sql, re.S)
    assert m is not None
    assert [c.strip() for c in m.group(1).split(",")] == ["expediente_id", "tipo", "file_id"]
    assert frozenset(re.findall(r"'([^']+)'", m.group(2))) == MULTI_ARTEFACTO_TIPOS
    # los singleton por (expediente) de la 019 desaparecen: dejarían bloqueada la 2.ª constancia
    for r in ("elabora", "entrega", "recibe"):
        assert f"DROP INDEX IF EXISTS ux_pwo_acta_firma_excepcion_{r};" in sql_020
        assert f"DROP INDEX IF EXISTS ux_pwo_acta_firma_excepcion_personal_{r};" in sql_020


@pytest.mark.asyncio
@pytest.mark.parametrize("tipo", sorted(MULTI_ARTEFACTO_TIPOS))
async def test_el_repositorio_busca_multi_artefacto_por_file_id(tipo: str) -> None:
    conn = AsyncMock()
    repo = PreservacionWormRepository(conn, "itest")
    with pytest.raises(ValueError):
        await repo.get_by_expediente_and_tipo("EXP-1", tipo)
    fid = uuid.uuid4()
    await repo.get_by_expediente_and_tipo("EXP-1", tipo, fid)
    sql, *args = conn.fetchrow.await_args.args
    assert "file_id = $3" in sql
    assert args == ["EXP-1", tipo, fid]


@pytest.mark.asyncio
async def test_el_repositorio_no_cambia_para_los_tipos_singleton() -> None:
    conn = AsyncMock()
    repo = PreservacionWormRepository(conn, "itest")
    await repo.get_by_expediente_and_tipo("EXP-1", "indice", uuid.uuid4())
    sql, *args = conn.fetchrow.await_args.args
    assert "file_id = " not in sql
    assert args == ["EXP-1", "indice"]


@pytest.mark.asyncio
async def test_la_segunda_constancia_del_rol_no_se_da_por_protegida(client_with_conn, monkeypatch) -> None:
    """Endpoint de punta a punta (mocks): con una constancia YA protegida de OTRO file_id, la
    protección de la nueva busca por SU file_id, no la encuentra y la protege (201)."""
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)
    client, mock_conn, _minio = client_with_conn
    primera = make_worm_row(
        expediente_id="EXP-9", tipo="acta_firma_excepcion_recibe", file_id=uuid.uuid4()
    )
    nueva = uuid.uuid4()

    async def _fetchrow(sql, *args):
        if "FROM preservacion_worm_objeto" in sql and "WHERE expediente_id" in sql:
            # un lookup que ignore file_id devolvería la primera: eso es el defecto
            if len(args) == 3 and args[2] == primera["file_id"]:
                return primera
            return None
        return None

    mock_conn.fetchrow.side_effect = _fetchrow
    resp = await client.post(
        "/api/v1/preservacion/proteger-artefacto-internal",
        json={
            "file_id": str(nueva),
            "expediente_id": "EXP-9",
            "tipo": "acta_firma_excepcion_recibe",
            "retencion_anios": 5,
            "fecha_inicio": "2026-01-01",
        },
        headers=INTERNAL_HEADERS,
    )
    lookups = [
        c for c in mock_conn.fetchrow.call_args_list if "FROM preservacion_worm_objeto" in c.args[0]
    ]
    assert lookups and lookups[0].args[1:] == ("EXP-9", "acta_firma_excepcion_recibe", nueva)
    # no se devolvió la fila de la primera como «ya protegida»
    assert not (resp.status_code == 200 and resp.json().get("file_id") == str(primera["file_id"]))
