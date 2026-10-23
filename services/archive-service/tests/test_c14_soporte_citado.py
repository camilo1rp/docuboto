# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C14 (ADR-043), unidad: la baja de soporte que se congela al proponer el lote, su aviso en la
respuesta, su paso al acta de eliminación y el contrato SQL con storage.

La parte que necesita Postgres real (vista, triggers, `FOR SHARE`) está en
`integration/test_c5_c14_soporte_citado_sql.py`.

Mutaciones que deben caer:
  * no llamar a `congelar_soportes_citados` al proponer -> `test_proponer_congela_...`;
  * incluir los soportes en un acta SIN soportes (acta distinta de la de siempre) ->
    `test_sin_soportes_el_acta_es_la_de_siempre`;
  * volcar `file_id`/hash del anexo al acta -> `test_el_acta_no_lleva_el_anexo`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.services.eliminacion_acta import construir_acta_xml
from tests.conftest import TENANT_HEADERS
from tests.test_eliminacion_lote import _CLEARANCE, RUTA, _candidato, _lote_row, _proponer


def _soporte(**kw) -> dict:
    return {
        "expediente_id": kw.get("expediente_id", uuid.uuid4()),
        "excepcion_id": kw.get("excepcion_id", uuid.uuid4()),
        "transferencia_id": kw.get("transferencia_id", uuid.uuid4()),
        "rol": kw.get("rol", "recibe"),
        "ordinal": kw.get("ordinal", 1),
    }


# --- proponer ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proponer_congela_las_constancias_y_las_avisa_sin_rechazar(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    cand = _candidato(code="EXP-2020-0042")
    sop = _soporte(expediente_id=cand["id"])
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_CLEARANCE, _lote_row()]
    # candidatos, prestadas, tardías, constancias sin huella (ninguna), constancias congeladas
    mock_conn.fetch.side_effect = [[cand], [], [], [], [sop]]
    resp = await client.post(
        RUTA,
        json={"codigo": "LOTE-2026-001", "expediente_ids": [str(cand["id"])]},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201, resp.text
    cuerpo = resp.json()
    # aviso, no rechazo: el expediente ENTRA y la constancia se informa
    assert cuerpo["total_items"] == 1 and cuerpo["rechazados"] == []
    (aviso,) = cuerpo["soportes_citados"]
    assert aviso["expediente_code"] == "EXP-2020-0042"
    assert aviso["excepcion_id"] == str(sop["excepcion_id"])
    assert aviso["transferencia_id"] == str(sop["transferencia_id"])
    assert aviso["rol"] == "recibe" and aviso["ordinal"] == 1
    assert aviso["soporte_eliminado_at"] is None
    sql = " ".join(str(c.args[0]) for c in mock_conn.fetch.call_args_list)
    assert "INSERT INTO eliminacion_lote_soporte_citado" in sql
    # y el aviso viaja al asiento de la propuesta
    asiento = next(
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == "archive.eliminacion_lote_propuesto"
    )
    assert any("soportes_citados" in str(a) for a in asiento)


@pytest.mark.asyncio
async def test_sin_constancias_citadas_el_aviso_viene_vacio(client_with_conn) -> None:
    """Control positivo: lote normal, sin avisos."""
    client, mock_conn = client_with_conn
    resp = await _proponer(client, mock_conn, [_candidato()])
    assert resp.status_code == 201 and resp.json()["soportes_citados"] == []


def test_el_sql_de_congelacion_solo_toma_vigentes_y_una_vez_por_constancia() -> None:
    import inspect

    from app.repositories.archive_repository import ArchiveRepository

    src = inspect.getsource(ArchiveRepository.congelar_soportes_citados)
    assert "NOT EXISTS (SELECT 1 FROM transferencia_acta_firma_excepcion_revocacion" in src
    assert "ON CONFLICT (lote_id, excepcion_id) DO NOTHING" in src
    assert "DISTINCT ON (e.id)" in src
    assert "referencia_anexo_id IS NOT NULL" in src


# --- el acta de eliminación ----------------------------------------------------------------------


_LOTE_ID = uuid.uuid4()  # fijos: el acta se compara byte a byte entre llamadas
_AVALADO_POR = uuid.uuid4()


def _acta(**kw) -> str:
    lote = {
        "codigo": "L-1", "id": _LOTE_ID, "acta_comite": "Acta 9", "fecha_aval": "2026-09-01",
        "acto_administrativo": "Res. 5", "avalado_por": _AVALADO_POR,
        "publicado_at": "2026-08-01", "publicado_hasta": "2026-08-31",
    }
    item = {
        "expediente_code": "EXP-1", "expediente_nombre": "n", "trd_serie_code": "S",
        "disposition_code": "E", "fecha_vencimiento": "2026-01-01",
        "fecha_extrema_inicial": None, "fecha_extrema_final": None, "nivel_seguridad": 1,
        "radicados_incluidos": 1,
    }
    return construir_acta_xml(
        lote=lote, items=[item], tenant_slug="demo", metodo_borrado="borrado seguro",
        observaciones=None, generado_por="u", generado_ts=datetime(2026, 9, 2, tzinfo=UTC), **kw,
    )


def test_sin_soportes_el_acta_es_la_de_siempre() -> None:
    """Sin constancias citadas el acta es byte a byte la anterior (el SHA no cambia)."""
    base = _acta()
    assert "soportesCitados" not in base
    assert _acta(soportes=[]) == _acta(soportes=None) == _acta()


def test_con_soportes_el_acta_los_lista_en_orden_estable_y_sin_el_anexo() -> None:
    a, b = _soporte(rol="recibe"), _soporte(rol="entrega")
    for r in (a, b):
        r["expediente_code"] = "EXP-1"
    xml = _acta(soportes=[a, b])
    assert 'soportesCitados total="2"' in xml
    assert xml == _acta(soportes=[b, a]), "el orden de entrada no cambia el SHA del acta"
    assert str(a["excepcion_id"]) in xml and str(b["transferencia_id"]) in xml


def test_el_acta_no_lleva_el_anexo() -> None:
    """Content-free: identifica la constancia, jamás el anexo (ni su id ni su huella)."""
    file_id = uuid.uuid4()
    s = _soporte()
    s["expediente_code"] = "EXP-1"
    s["file_id"] = file_id
    s["anexo_sha256"] = "ab" * 32
    xml = _acta(soportes=[s])
    assert str(file_id) not in xml and "ab" * 32 not in xml


# --- asegurar_soporte_citable (FOR SHARE + lote vivo) ----------------------------------------------


def _repo_transferencia(valores: list):
    from unittest.mock import AsyncMock

    from app.repositories.transferencia_repository import TransferenciaRepository

    conn = AsyncMock()
    conn.fetchval.side_effect = valores
    return TransferenciaRepository(conn, "demo"), conn


@pytest.mark.asyncio
async def test_anexo_vivo_y_radicado_fuera_de_lotes_se_puede_citar() -> None:
    repo, conn = _repo_transferencia([True, 1, None])
    assert await repo.asegurar_soporte_citable(uuid.uuid4(), uuid.uuid4()) is None
    sql = conn.fetchval.call_args_list[1].args[0]
    assert "FOR SHARE" in sql and "deleted_at IS NULL" in sql  # mutación: quitar FOR SHARE


@pytest.mark.asyncio
async def test_anexo_borrado_o_inexistente_se_niega_sin_mirar_los_lotes() -> None:
    repo, conn = _repo_transferencia([True, None])
    assert await repo.asegurar_soporte_citable(uuid.uuid4(), uuid.uuid4()) == "anexo_no_disponible"
    assert conn.fetchval.await_count == 2


@pytest.mark.asyncio
async def test_radicado_en_lote_vivo_se_niega_con_el_codigo_del_lote() -> None:
    repo, conn = _repo_transferencia([False, "LOTE-2026-007"])  # sin tabla files: se omite el bloqueo
    assert await repo.asegurar_soporte_citable(uuid.uuid4(), uuid.uuid4()) == "lote:LOTE-2026-007"
    sql = conn.fetchval.call_args_list[1].args[0]
    assert "'borrador', 'publicado', 'avalado'" in sql and "'anulado'" not in sql


@pytest.mark.asyncio
async def test_proponer_rechaza_una_constancia_cuyo_anexo_no_tiene_huella(client_with_conn) -> None:
    """Sin SHA-256 no hay qué avalar destruir (cierre del lote C): 409 ANTES de crear el lote."""
    client, mock_conn = client_with_conn
    cand = _candidato(code="EXP-2020-0042")
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_CLEARANCE, _lote_row()]
    tid = uuid.uuid4()
    mock_conn.fetch.side_effect = [[cand], [], [], [{"transferencia_id": tid}]]
    resp = await client.post(
        RUTA,
        json={"codigo": "LOTE-2026-002", "expediente_ids": [str(cand["id"])]},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "soporte_sin_huella"
    assert str(tid) in resp.json()["detail"]["detail"]
    sql = " ".join(str(c.args[0]) for c in mock_conn.execute.call_args_list)
    assert "INSERT INTO eliminacion_lotes" not in sql
