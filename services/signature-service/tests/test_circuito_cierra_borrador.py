# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cerrada la cadena, el borrador queda aprobado (E23, RF-PRO-09).

Estos tests miden el **cableado**, no la pieza. El aviso a document-service es
una llamada de una línea al final de una rama larga: es exactamente la clase de
cosa que se escribe, se prueba aislada y nunca llega a invocarse. Aquí lo que
se afirma es que la rama lo alcanza — y que la rama equivocada no.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.cadena_service import CadenaFirmaService
from tests.test_visto_bueno_circuito import HUELLA, REVISOR, _sol, _turno


def _conn() -> AsyncMock:
    conn = AsyncMock()
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    return conn


def _servicio(monkeypatch, sol: dict, *, quedan_turnos: bool):
    """Servicio listo para resolver un visto bueno, con el aviso interceptado."""
    from app.services import cadena_service as mod

    turno = _turno("visto_bueno", REVISOR)
    svc = CadenaFirmaService(_conn(), "demo")
    monkeypatch.setattr(svc, "_get_or_404", AsyncMock(return_value=sol), raising=False)
    monkeypatch.setattr(svc, "_assert_can_read", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(svc, "get", AsyncMock(return_value="ok"), raising=False)
    svc._repo.es_firmante = AsyncMock(return_value=True)
    svc._repo.turno_activo_for_update = AsyncMock(return_value=turno)
    svc._repo.turno_activo = AsyncMock(
        return_value=_turno("firma", uuid.uuid4()) if quedan_turnos else None
    )
    svc._repo.marcar_turno = AsyncMock(return_value=True)
    svc._repo.actualizar_estado_solicitud = AsyncMock(return_value=None)

    async def _huella(borrador_id: str, tenant_slug: str) -> dict:
        return {"version": 1, "cuerpo_sha256": HUELLA}

    monkeypatch.setattr(mod, "get_huella_borrador", _huella)

    avisos: list[tuple[str, str]] = []

    async def _aviso(borrador_id: str, tenant_slug: str) -> bool:
        avisos.append((borrador_id, tenant_slug))
        return True

    monkeypatch.setattr(mod, "aprobar_borrador_remote", _aviso)
    return svc, avisos


@pytest.mark.asyncio
async def test_resuelto_el_ultimo_turno_se_avisa_a_document(monkeypatch) -> None:
    """Sin esto el circuito se completa y el borrador se queda en 'borrador'."""
    sol = _sol()
    svc, avisos = _servicio(monkeypatch, sol, quedan_turnos=False)

    await svc.dar_visto_bueno(sol["id"], REVISOR, comentario=None)

    assert avisos == [(sol["objeto_id"], "demo")]


@pytest.mark.asyncio
async def test_con_turnos_pendientes_no_se_avisa(monkeypatch) -> None:
    """Avisar a mitad de circuito aprobaría el borrador saltándose el resto.

    Sería el mismo defecto que este trabajo cierra, cometido desde dentro.
    """
    sol = _sol()
    svc, avisos = _servicio(monkeypatch, sol, quedan_turnos=True)

    await svc.dar_visto_bueno(sol["id"], REVISOR, comentario=None)

    assert avisos == []


@pytest.mark.asyncio
async def test_una_cadena_que_no_es_de_borrador_no_avisa(monkeypatch) -> None:
    """Un radicado o un expediente ya existen cuando se firman.

    Su cadena no promueve nada en document-service, y mandar el aviso con un
    identificador que allí no es un borrador solo produciría un 404 por cada
    firma de índice.
    """
    sol = _sol(objeto_tipo="radicado")
    svc, avisos = _servicio(monkeypatch, sol, quedan_turnos=False)

    await svc.dar_visto_bueno(sol["id"], REVISOR, comentario=None)

    assert avisos == []


@pytest.mark.asyncio
async def test_si_document_no_responde_el_visto_bueno_sigue_dado(monkeypatch) -> None:
    """El aviso es best-effort, y tiene que poder serlo.

    Revertir un visto bueno ya dado y ya auditado porque otro servicio no
    contesta castigaría al revisor por una avería ajena. No abre ninguna
    ventana: la autoridad es la consulta que hace `aprobar`, no este aviso.
    """
    from app.services import cadena_service as mod

    sol = _sol()
    svc, _ = _servicio(monkeypatch, sol, quedan_turnos=False)

    async def _falla(borrador_id: str, tenant_slug: str) -> bool:
        return False

    monkeypatch.setattr(mod, "aprobar_borrador_remote", _falla)

    resultado = await svc.dar_visto_bueno(sol["id"], REVISOR, comentario=None)

    assert resultado == "ok"
    svc._repo.marcar_turno.assert_awaited()
