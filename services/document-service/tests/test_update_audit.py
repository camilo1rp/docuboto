# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El PATCH de radicado no dejaba rastro y revivía anulados.

Dos defectos en el mismo método de dos líneas:

1. **Sin traza.** Modificar el asunto, el destino, el estado o los folios de un
   documento oficial con número inmutable era invisible para la traza legal
   forense. El contraste lo delataba: **leer** un radicado sí se auditaba.
2. **Revivía anulados.** El `WHERE` del `UPDATE` no excluía `status = 'anulado'` y
   `DocumentUpdate.status` admite `registered`, así que un PATCH gateado solo por
   `PERM_RADI` deshacía una anulación aprobada por el circuito de dos pasos con
   visto bueno de supervisor — sin causal, sin aprobador y sin rastro.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.conftest import TENANT_HEADERS, make_radicado_record

pytestmark = pytest.mark.asyncio

_USER = "11111111-1111-1111-1111-111111111111"
_DOC_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _clearance_row(max_level: int = 3) -> dict:
    return {"is_root": False, "max_level": max_level}


def _sqls(mock_conn: AsyncMock) -> list[str]:
    """Todo el SQL que pasó por `fetchrow`, normalizado a una línea."""
    return [" ".join(str(call.args[0]).split()) for call in mock_conn.fetchrow.call_args_list]


def _executes(mock_conn: AsyncMock) -> list:
    """Llamadas a `execute` — por ahí escribe `audit.append` su INSERT."""
    return list(mock_conn.execute.call_args_list)


# ---------------------------------------------------------------------------
# El asiento
# ---------------------------------------------------------------------------


async def test_el_patch_deja_asiento_con_antes_y_despues(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Un PATCH exitoso asienta `document.radicado_actualizado`.

    Se comprueba que el asiento registra el **antes y el después** del campo
    tocado: una traza que dice «se cambió el asunto» sin decir a qué no permite
    reconstruir nada, y es indistinguible de no auditar para quien investiga.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # gate RBAC
    previo = make_radicado_record(doc_id=_DOC_ID, subject="Asunto original")
    despues = make_radicado_record(doc_id=_DOC_ID, subject="Asunto corregido")
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),  # resolve_caller_clearance
        previo,  # lectura previa (diff del asiento)
        despues,  # UPDATE ... RETURNING
    ]

    resp = await client.patch(
        f"/api/v1/documents/{_DOC_ID}",
        json={"subject": "Asunto corregido"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 200
    insert = next(
        (call for call in _executes(mock_conn) if "audit_log" in str(call.args[0])), None
    )
    assert insert is not None, "el PATCH debe dejar asiento en audit_log"
    # El payload del asiento viaja como parámetro del INSERT de auditoría.
    serializado = str(insert.args)
    assert "radicado_actualizado" in serializado
    assert "Asunto original" in serializado, "falta el valor ANTES del cambio"
    assert "Asunto corregido" in serializado, "falta el valor DESPUES del cambio"


async def test_el_asiento_va_en_la_misma_transaccion_que_la_mutacion(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Un fallo del asiento NO se traga: aborta el PATCH.

    A diferencia de las lecturas —que auditan best-effort para no convertir la
    auditoría en un punto de caída—, aquí el asiento va DENTRO de la transacción:
    preferimos que un cambio falle a que ocurra sin dejar rastro. Mismo criterio
    que `AnulacionService`.

    **LÍMITE DECLARADO**: con la conexión mockeada no hay transacción real, así que
    esto NO demuestra el rollback — demuestra que la excepción del asiento se
    propaga (no hay `except` que la absorba) y que la mutación ocurrió dentro de un
    `conn.transaction()`. Que el rollback suceda de verdad pertenece al carril de
    integración contra Postgres real, igual que en `tests/integration/`.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        make_radicado_record(doc_id=_DOC_ID, subject="Asunto original"),
        make_radicado_record(doc_id=_DOC_ID, subject="Asunto corregido"),
    ]

    async def _execute(sql, *args):
        if "audit_log" in str(sql):
            raise RuntimeError("audit_log caido")
        return None

    mock_conn.execute.side_effect = _execute

    # El fallo del asiento sale del handler: en ningún caso el cliente recibe un
    # 200 confirmando un cambio del que no quedó traza.
    with pytest.raises(RuntimeError, match="audit_log"):
        await client.patch(
            f"/api/v1/documents/{_DOC_ID}",
            json={"subject": "Asunto corregido"},
            headers={**TENANT_HEADERS, "X-User-Id": _USER},
        )

    mock_conn.transaction.assert_called()


# ---------------------------------------------------------------------------
# El guard de anulado
# ---------------------------------------------------------------------------


async def test_no_se_puede_modificar_un_radicado_anulado(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """409 `radicado_anulado`, y ningún UPDATE llega a ejecutarse."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        make_radicado_record(doc_id=_DOC_ID, status="anulado"),
    ]

    resp = await client.patch(
        f"/api/v1/documents/{_DOC_ID}",
        json={"subject": "Reescritura de un anulado"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "radicado_anulado"
    assert not any("UPDATE radicados" in s for s in _sqls(mock_conn))


async def test_no_se_puede_desanular_por_patch(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El caso concreto: `{"status": "registered"}` sobre un anulado.

    Era la ruta lateral completa — deshacer una anulación aprobada por supervisor
    con un PATCH gateado solo por `PERM_RADI`. Desde ADR-028 la puerta está
    cerrada ANTES: el PATCH rechaza `status` con 422 (el 409 de `anulado` sigue
    cubierto por el test de arriba, con un campo editable).
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        make_radicado_record(doc_id=_DOC_ID, status="anulado"),
    ]

    resp = await client.patch(
        f"/api/v1/documents/{_DOC_ID}",
        json={"status": "registered"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "status_managed_by_workflow"
    assert not any("UPDATE radicados" in s for s in _sqls(mock_conn))


async def test_update_document_gate_y_guard_van_en_el_where(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El clearance Y el guard de anulado viajan en el `WHERE` del propio UPDATE.

    No basta con la comprobación previa: entre leer y escribir hay una ventana en
    la que otro puede anular o reclasificar el radicado. Que ambas condiciones
    estén en el `WHERE` hace que autorizar y mutar sean el mismo acto.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        make_radicado_record(doc_id=_DOC_ID),
        make_radicado_record(doc_id=_DOC_ID, subject="Nuevo"),
        None,
    ]

    await client.patch(
        f"/api/v1/documents/{_DOC_ID}",
        json={"subject": "Nuevo"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    update_sql = next(s for s in _sqls(mock_conn) if "UPDATE radicados" in s)
    assert "nivel_seguridad <=" in update_sql
    assert "status <> 'anulado'" in update_sql
