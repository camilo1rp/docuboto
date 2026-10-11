# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ADR-029: la ubicación actual del radicado — función pura `derivar_ubicacion`.

La lectura de los pasos, la escritura atómica y el backfill contra Postgres real
están en `tests/integration/test_ubicacion_radicado_integration.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.services.estado_radicado import (
    PasoUbicacion,
    Ubicacion,
    derivar_ubicacion,
)

T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
U1, U2 = uuid.uuid4(), uuid.uuid4()


def _paso(n: int, status: str, dept: str, *, minutos: int | None = None,
          usuario: uuid.UUID | None = None, codigo: int | None = None) -> PasoUbicacion:
    return PasoUbicacion(
        step_number=n, status=status, to_dept=dept, assigned_to=usuario,
        completed_at=None if minutos is None else T0 + timedelta(minutes=minutos),
        to_dept_code=codigo,
    )


def test_sin_pasos_no_hay_ubicacion_de_flujo() -> None:
    assert derivar_ubicacion("registered", []) is None


def test_el_paso_activo_manda() -> None:
    pasos = [_paso(1, "returned", "Tecnología", minutos=5),
             _paso(2, "pending", "Talento Humano", usuario=U2, codigo=7)]
    assert derivar_ubicacion("distributed", pasos) == Ubicacion("Talento Humano", 7, U2)


def test_con_dos_activos_anomalos_gana_el_de_mayor_numero() -> None:
    pasos = [_paso(1, "pending", "A"), _paso(2, "in_progress", "B")]
    assert derivar_ubicacion("in_progress", pasos).dept == "B"  # type: ignore[union-attr]


def test_devolver_deja_el_radicado_en_la_dependencia_destino() -> None:
    """Caso 2026-DEMO-E-000117: radicado en Tecnología, repartido a Talento Humano,
    devuelto a Control Interno. El paso nuevo (sin dueño) es el activo."""
    pasos = [
        _paso(1, "returned", "Talento Humano", minutos=10, usuario=U1),
        _paso(2, "pending", "Control Interno", codigo=3),
    ]
    assert derivar_ubicacion("distributed", pasos) == Ubicacion("Control Interno", 3, None)


def test_sin_activo_gana_el_cerrado_mas_reciente_no_el_de_mayor_numero() -> None:
    """Rollback y luego completar: el paso 2 (cancelado, mayor número) se cerró
    ANTES que el paso 1 (reactivado y completado después). Ordenar por número de
    paso pondría el radicado en la dependencia que lo canceló."""
    pasos = [_paso(1, "completed", "Jurídica", minutos=30, usuario=U1),
             _paso(2, "cancelled", "Archivo", minutos=10, usuario=U2)]
    assert derivar_ubicacion("distributed", pasos).dept == "Jurídica"  # type: ignore[union-attr]


def test_empate_de_cierre_desempata_por_numero_de_paso() -> None:
    pasos = [_paso(1, "completed", "A", minutos=5), _paso(2, "completed", "B", minutos=5)]
    assert derivar_ubicacion("distributed", pasos).dept == "B"  # type: ignore[union-attr]


def test_un_paso_sin_fecha_de_cierre_pierde_frente_a_uno_con_fecha() -> None:
    pasos = [_paso(3, "cancelled", "SinFecha"), _paso(1, "completed", "ConFecha", minutos=1)]
    assert derivar_ubicacion("distributed", pasos).dept == "ConFecha"  # type: ignore[union-attr]


def test_finalizar_deja_la_dependencia_donde_se_cerro() -> None:
    pasos = [_paso(1, "completed", "Jurídica", minutos=9, usuario=U1, codigo=5)]
    assert derivar_ubicacion("distributed", pasos) == Ubicacion("Jurídica", 5, U1)


@pytest.mark.parametrize("estado", ["closed", "anulado"])
def test_un_terminal_con_ubicacion_escrita_la_congela(estado: str) -> None:
    congelada = Ubicacion("Jurídica", 5, U1)
    pasos = [_paso(1, "completed", "Jurídica", minutos=1), _paso(2, "pending", "Archivo")]
    assert derivar_ubicacion(estado, pasos, congelada=congelada) is congelada


@pytest.mark.parametrize("estado", ["closed", "anulado"])
def test_un_terminal_sin_ubicacion_escrita_se_deriva_del_historial(estado: str) -> None:
    """Es lo que hace el backfill con un cerrado/anulado anterior a ADR-029."""
    pasos = [_paso(1, "cancelled", "Jurídica", minutos=1, codigo=5)]
    assert derivar_ubicacion(estado, pasos) == Ubicacion("Jurídica", 5, None)


def test_un_no_terminal_ignora_la_congelada() -> None:
    pasos = [_paso(1, "pending", "Archivo", codigo=9)]
    assert derivar_ubicacion("distributed", pasos, congelada=Ubicacion("X", 1, None)).dept == "Archivo"  # type: ignore[union-attr]


def test_el_codigo_es_el_del_paso_no_se_adivina_por_el_nombre() -> None:
    """ADR-032: el código es un DATO del paso. Un paso cuyo nombre ya no coincide con el
    catálogo (la dependencia se renombró) conserva su código; uno sin código lo deja NULL,
    nunca el de otra dependencia que hoy se llame igual."""
    renombrada = derivar_ubicacion("distributed", [_paso(1, "pending", "Nombre viejo", codigo=4)])
    assert renombrada == Ubicacion("Nombre viejo", 4, None)
    sin_codigo = derivar_ubicacion("distributed", [_paso(1, "pending", "Historico")])
    assert sin_codigo is not None and sin_codigo.dept == "Historico" and sin_codigo.dept_code is None


# ---------------------------------------------------------------------------
# El cableado REAL de `sincronizar_estado_radicado` (carril mock).
#
# El fixture autouse de conftest reemplaza la función por un AsyncMock en TODA la
# suite unitaria: sin esto, quitar la escritura de `flujo_*` solo caería en el carril
# de integración (opt-in). La original se captura al importar, antes de los parches.
# ---------------------------------------------------------------------------

from unittest.mock import AsyncMock  # noqa: E402

from app.repositories.workflow_repository import WorkflowRepository  # noqa: E402

_SINCRONIZAR_REAL = WorkflowRepository.sincronizar_estado_radicado


def _repo_falso(estado: str, flujo: tuple[str | None, int | None, uuid.UUID | None], paso: dict | None):
    conn = AsyncMock()
    fila = {"status": estado, "flujo_dept": flujo[0], "flujo_dept_code": flujo[1], "flujo_usuario": flujo[2]}
    conn.fetchrow.side_effect = [fila, {"activo": "pending", "tiene_pasos": True}]
    conn.fetch.return_value = [paso] if paso else []
    repo = WorkflowRepository.__new__(WorkflowRepository)
    repo._conn = conn  # type: ignore[attr-defined]
    repo._set_search_path = AsyncMock()  # type: ignore[method-assign]
    return repo, conn


async def test_sincronizar_escribe_la_ubicacion_en_el_mismo_update_que_el_estado() -> None:
    paso = {"step_number": 2, "status": "pending", "to_dept": "Control Interno",
            "assigned_to": U2, "completed_at": None, "to_dept_code": 907}
    repo, conn = _repo_falso("registered", (None, None, None), paso)
    rid = uuid.uuid4()

    assert await _SINCRONIZAR_REAL(repo, rid) == ("registered", "distributed")

    conn.execute.assert_awaited_once()
    sql, *args = conn.execute.await_args.args
    assert "status = $2" in sql and "flujo_dept = $3" in sql and "flujo_usuario = $5" in sql
    assert args == [rid, "distributed", "Control Interno", 907, U2]


@pytest.mark.parametrize("estado", ["closed", "anulado"])
async def test_sincronizar_no_escribe_nada_en_un_terminal(estado: str) -> None:
    repo, conn = _repo_falso(estado, ("Archivo", 200, None), None)
    assert await _SINCRONIZAR_REAL(repo, uuid.uuid4()) == (estado, estado)
    conn.execute.assert_not_awaited()
    conn.fetch.assert_not_awaited()  # ni siquiera lee los pasos


async def test_sincronizar_con_un_paso_sin_codigo_deja_el_codigo_nulo() -> None:
    paso = {"step_number": 1, "status": "pending", "to_dept": "Historica",
            "assigned_to": None, "completed_at": None, "to_dept_code": None}
    repo, conn = _repo_falso("registered", (None, None, None), paso)
    await _SINCRONIZAR_REAL(repo, uuid.uuid4())
    assert conn.execute.await_args.args[3:5] == ("Historica", None)
