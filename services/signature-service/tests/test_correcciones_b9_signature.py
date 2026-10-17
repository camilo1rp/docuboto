# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Correcciones de auditoría B9, bloque S (S1, S2, S4, S5, S6, S7, S10, S11).

Cada prueba nombra la mutación que la hace caer. Las que tocan SQL (S1 bandeja,
S3 monotonía, CHECK de S7) viven en ``tests/integration`` contra Postgres real.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.repositories.cadena_repository import CadenaRepository
from app.repositories.firma_repository import FirmaRepository
from app.services import cadena_service as mod
from tests.conftest import USER
from tests.test_challenge_mismas_precondiciones_que_firmar import (
    SOL_ID,
    _error,
    _servicio,
    _sol,
    _turno,
)

REFERENCIA_404 = {
    "error": "solicitud_not_found",
    "detail": "No existe la solicitud de firma",
    "status": 404,
}


def _clearance(mp: pytest.MonkeyPatch, nivel: int) -> None:
    mp.setattr(mod, "resolve_caller_clearance", AsyncMock(return_value=nivel))


# --------------------------------------------------------------------------- S1 lecturas


@pytest.mark.lectura_vigente_real
@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["get", "get_por_objeto"])
async def test_s1_la_lectura_se_gatea_con_el_nivel_vivo_y_no_con_la_copia(
    monkeypatch: pytest.MonkeyPatch, via: str
) -> None:
    """Copia 1, objeto vivo 2, actor de clearance 1: 404 neutro en las dos lecturas
    (un read-up de metadatos de la cadena). Mutación: `_assert_can_read_vigente`
    comparando solo `sol["nivel_seguridad"]` => cae."""
    svc, _, _ = _servicio(monkeypatch)
    _clearance(monkeypatch, 1)
    svc._repo.resolver_nivel_por_objeto.return_value = 2
    svc._repo.get_solicitud_por_objeto.return_value = _sol()
    llamada = (
        svc.get(SOL_ID, USER)
        if via == "get"
        else svc.get_por_objeto("radicado", "2024-DEMO-E-000001", USER)
    )
    exc = await _error(llamada)
    assert exc.status_code == 404 and exc.detail == REFERENCIA_404
    svc._repo.list_turnos.assert_not_awaited()


@pytest.mark.lectura_vigente_real
@pytest.mark.asyncio
async def test_s1_control_positivo_con_clearance_suficiente_y_nivel_efectivo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con clearance 2 la lectura pasa y devuelve el nivel EFECTIVO (2, no la copia 1);
    con el objeto sin resolver (None) rige la copia. Mutación: devolver `dict(sol)` => cae."""
    svc, _, _ = _servicio(monkeypatch)
    _clearance(monkeypatch, 2)
    svc._repo.resolver_nivel_por_objeto.return_value = 2
    assert (await svc._assert_can_read_vigente(_sol(), USER))["nivel_seguridad"] == 2
    svc._repo.resolver_nivel_por_objeto.return_value = None
    assert (await svc._assert_can_read_vigente(_sol(), USER))["nivel_seguridad"] == 1


# --------------------------------------------------------------------------- S2 asiento


@pytest.mark.nivel_vigente_real
@pytest.mark.asyncio
async def test_s2_la_relectura_que_corrige_la_copia_deja_asiento_atomico(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si la corrección toca filas, queda `firma.solicitud_reclasificada` con el
    contrato pedido (misma transacción: la llamada va entre `nivel_vigente_for_update`
    y el retorno). Mutación: quitar el `_audit.append` => cae."""
    svc, _, _ = _servicio(monkeypatch)
    svc._repo.nivel_vigente_for_update.return_value = (2, 1)
    svc._repo.actualizar_nivel_seguridad.return_value = 1

    vigente = await svc._sol_con_nivel_vigente(_sol(), 3, USER)

    assert vigente["nivel_seguridad"] == 2 and vigente["nivel_copia"] == 1
    svc._repo.actualizar_nivel_seguridad.assert_awaited_once_with(
        "radicado", ["2024-DEMO-E-000001"], 2, solo_subir=True
    )
    [llamada] = mod._audit.append.await_args_list  # type: ignore[attr-defined]
    assert llamada.kwargs["action"] == "firma.solicitud_reclasificada"
    assert llamada.kwargs["canal"] == "system"
    assert llamada.kwargs["actor"] == str(USER)
    assert llamada.kwargs["object_ref"] == "2024-DEMO-E-000001"
    assert llamada.kwargs["payload"] == {
        "solicitud_id": str(SOL_ID),
        "nivel_anterior": 1,
        "nivel_nuevo": 2,
        "filas": 1,
        "origen": "relectura_al_firmar",
    }


@pytest.mark.nivel_vigente_real
@pytest.mark.asyncio
async def test_s2_sin_filas_corregidas_no_hay_asiento(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otra transacción ya la sincronizó (0 filas): ningún asiento. Mutación: quitar
    la guarda `filas > 0` => cae."""
    svc, _, _ = _servicio(monkeypatch)
    svc._repo.nivel_vigente_for_update.return_value = (2, 1)
    svc._repo.actualizar_nivel_seguridad.return_value = 0

    await svc._sol_con_nivel_vigente(_sol(), 3, USER)

    mod._audit.append.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.nivel_vigente_real
@pytest.mark.asyncio
async def test_h_el_nivel_anterior_del_asiento_sale_de_la_copia_BLOQUEADA(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-auditoria B9 H: la solicitud se leyo SIN bloqueo con copia 1, pero otra
    transaccion la subio a 2 antes de que esta tomara el `FOR UPDATE`; el objeto vivo
    es 3. El asiento dice `nivel_anterior: 2` (lo que habia bajo el candado), no 1.
    Mutacion: volver a `sol["nivel_seguridad"]` en el payload => `nivel_anterior` 1."""
    svc, _, _ = _servicio(monkeypatch)
    svc._repo.nivel_vigente_for_update.return_value = (3, 2)
    svc._repo.actualizar_nivel_seguridad.return_value = 1

    vigente = await svc._sol_con_nivel_vigente(_sol(), 3, USER)

    [llamada] = mod._audit.append.await_args_list  # type: ignore[attr-defined]
    assert llamada.kwargs["payload"]["nivel_anterior"] == 2
    assert llamada.kwargs["payload"]["nivel_nuevo"] == 3
    assert vigente["nivel_copia"] == 2


@pytest.mark.nivel_vigente_real
@pytest.mark.asyncio
async def test_h_si_la_copia_bloqueada_ya_esta_al_dia_no_se_corrige_ni_se_asienta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Copia leida sin bloqueo = 1, pero bajo el candado ya vale 2 y el objeto vivo
    es 2: nada que corregir (otra transaccion ya lo hizo). Mutacion: decidir con
    `sol["nivel_seguridad"]` => corrige y asienta de mas."""
    svc, _, _ = _servicio(monkeypatch)
    svc._repo.nivel_vigente_for_update.return_value = (2, 2)

    await svc._sol_con_nivel_vigente(_sol(), 3, USER)

    svc._repo.actualizar_nivel_seguridad.assert_not_awaited()
    mod._audit.append.assert_not_awaited()  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- S5 / S10


def _preparar(svc, monkeypatch: pytest.MonkeyPatch, camino: str, *, vivo: int, clearance: int):
    _clearance(monkeypatch, clearance)
    svc._repo.nivel_vigente_for_update.return_value = (vivo, 1)
    svc._repo.turno_activo.return_value = {
        **_turno(rol="visto_bueno" if camino == "vb" else "firma"), "orden": 1
    }
    svc._repo.turno_activo_for_update.return_value = svc._repo.turno_activo.return_value
    svc.get = AsyncMock()
    svc._gate_documento_vigente = AsyncMock()


def _llamar(svc, camino: str):
    return (
        svc.rechazar(SOL_ID, USER, "motivo suficiente")
        if camino == "rechazar"
        else svc.dar_visto_bueno(SOL_ID, USER, None)
    )


@pytest.mark.nivel_vigente_real
@pytest.mark.asyncio
@pytest.mark.parametrize("camino", ["rechazar", "vb"])
async def test_s5_s10_primero_la_solicitud_y_despues_el_turno(
    monkeypatch: pytest.MonkeyPatch, camino: str
) -> None:
    """El orden de bloqueo es solicitud -> turno en los cuatro caminos (si no, dos
    peticiones cruzadas se bloquean entre sí). Mutación: mover la relectura tras
    `turno_activo_for_update` => cae."""
    svc, _, _ = _servicio(monkeypatch)
    _preparar(svc, monkeypatch, camino, vivo=1, clearance=3)

    await _llamar(svc, camino)

    nombres = [c[0] for c in svc._repo.mock_calls]
    assert nombres.index("nivel_vigente_for_update") < nombres.index("turno_activo_for_update")


@pytest.mark.nivel_vigente_real
@pytest.mark.asyncio
@pytest.mark.parametrize("camino", ["rechazar", "vb"])
async def test_s10_rechazar_y_visto_bueno_releen_el_nivel_bajo_bloqueo(
    monkeypatch: pytest.MonkeyPatch, camino: str
) -> None:
    """Copia 1 deja pasar el gate previo a un actor de clearance 1; el objeto vivo es
    2: 404 neutro DENTRO de la transacción, sin resolver el turno ni dejar asiento.
    Mutación: quitar la relectura de `rechazar`/`dar_visto_bueno` => cae."""
    svc, _, _ = _servicio(monkeypatch)
    _preparar(svc, monkeypatch, camino, vivo=2, clearance=1)

    exc = await _error(_llamar(svc, camino))

    assert exc.status_code == 404 and exc.detail == REFERENCIA_404
    svc._repo.marcar_turno.assert_not_awaited()
    mod._audit.append.assert_not_awaited()  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- S4 asiento


@pytest.mark.nivel_vigente_real
@pytest.mark.asyncio
async def test_s4_turno_firmado_lleva_el_nivel_vivo_y_el_de_la_copia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Firma completa con copia 1 y objeto vivo 2: el asiento `firma.turno_firmado`
    dice `nivel_seguridad: 2` y `nivel_copia: 1`. Mutación: sustituir `sol` por el
    registro previo a la relectura en el payload => cae (nivel 1)."""
    from tests.test_cableado_t8_contratos import _challenge_row, _conn_firma, _firmar
    from tests.test_cadena_2fa import _patch_audit

    audit_mock = _patch_audit(monkeypatch)
    monkeypatch.setattr(
        CadenaRepository, "nivel_vigente_for_update", AsyncMock(return_value=(2, 1))
    )
    monkeypatch.setattr(
        CadenaRepository, "actualizar_nivel_seguridad", AsyncMock(return_value=1)
    )
    conn = _conn_firma(_challenge_row())
    secuencia = list(conn.fetchrow.side_effect)
    secuencia[1] = {"is_root": True, "max_level": None}  # clearance suficiente para el nivel 2
    conn.fetchrow.side_effect = secuencia
    resp = await _firmar(conn)
    assert resp.status_code == 200, resp.text
    [firmado] = [
        c for c in audit_mock.await_args_list if c.kwargs.get("action") == "firma.turno_firmado"
    ]
    assert firmado.kwargs["payload"]["nivel_seguridad"] == 2
    assert firmado.kwargs["payload"]["nivel_copia"] == 1


# --------------------------------------------------------------------------- S7 repositorio


@pytest.mark.asyncio
@pytest.mark.parametrize("key_version", [None, 0])
async def test_s7_una_firma_nativa_exige_key_version_1_explicito(key_version) -> None:
    """Sin default silencioso: `nativa` con key_version ausente o 0 se rechaza antes de
    tocar la base. Mutación: volver a `data.get("key_version", KEY_VERSION_SIN_HMAC)`
    => cae (se insertaría con 0)."""
    conn = AsyncMock()
    datos = {
        "objeto_tipo": "radicado", "payload_sha256": "a" * 64, "firma_blob": "b",
        "provider": "nativa", "sello_tiempo": None,
    }
    if key_version is not None:
        datos["key_version"] = key_version
    with pytest.raises(ValueError, match="key_version"):
        await FirmaRepository(conn).insert(datos)
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_s7_control_positivo_las_no_nativas_siguen_sin_hmac() -> None:
    """XAdES sin `key_version` sigue siendo 0 («sin HMAC de servicio») y la nativa con
    1 inserta: la regla no es un bloqueo general."""
    conn = AsyncMock()
    base = {
        "objeto_tipo": "indice", "payload_sha256": "a" * 64, "firma_blob": "b",
        "sello_tiempo": None,
    }
    await FirmaRepository(conn).insert({**base, "provider": "xades_local"})
    assert conn.fetchrow.await_args.args[-1] == 0
    await FirmaRepository(conn).insert({**base, "provider": "nativa", "key_version": 1})
    assert conn.fetchrow.await_args.args[-1] == 1


# --------------------------------------------------------------------------- S11 helper único


def test_s11_el_404_neutro_tiene_una_sola_definicion_en_cadena_service() -> None:
    """El literal `solicitud_not_found` vive UNA vez (el helper). Mutación: pegar una
    copia literal del cuerpo en cualquier método => cae."""
    fuente = Path(mod.__file__).read_text(encoding="utf-8")
    literales = [
        n for n in ast.walk(ast.parse(fuente))
        if isinstance(n, ast.Constant) and n.value == "solicitud_not_found"
    ]
    assert len(literales) == 1, f"{len(literales)} copias del cuerpo 404 neutro"
    exc = mod._solicitud_no_encontrada()
    assert isinstance(exc, HTTPException) and exc.status_code == 404
    assert exc.detail == REFERENCIA_404


def test_s7_la_migracion_025_es_idempotente_y_nombra_la_fila_sintetica() -> None:
    sql = (
        Path(__file__).resolve().parents[1]
        / "migrations" / "tenant" / "025_firma_nativa_exige_key_version.sql"
    ).read_text(encoding="utf-8")
    sin = re.sub(r"--[^\n]*", "", sql)
    assert "d4e62969-824d-4802-9e40-b402d977b6fc" in sin
    assert "provider <> 'nativa' OR key_version = 1" in sin
    assert "IF NOT EXISTS" in sin
