# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Claim loop: duplicate event_id does not deliver twice; intelligence stays quiet."""

from __future__ import annotations

from typing import Any

import pytest
from orpycamcp_common.trabajo import (
    COLA_APP,
    TIPO_INTELLIGENCE,
    TIPO_WEBHOOK,
    Fila,
    Nueva,
    intelligence_se_omite,
    recorrer,
)


class _Memoria:
    def __init__(self) -> None:
        self._filas: dict[str, dict[str, Any]] = {}
        self._seq = 0

    async def encolar(self, nueva: Nueva) -> bool:
        if nueva.event_id in self._filas:
            return False
        self._seq += 1
        self._filas[nueva.event_id] = {
            "id": self._seq,
            "event_id": nueva.event_id,
            "tipo": nueva.tipo,
            "cola": nueva.cola,
            "tenant_slug": nueva.tenant_slug,
            "payload": dict(nueva.payload),
            "intentos": 0,
            "ultimo_error": None,
            "reclamado_at": None,
            "hecho_at": None,
        }
        return True

    async def reclamar(self, cola: str, reclamador: str, limite: int) -> list[Fila]:
        del reclamador
        salen: list[Fila] = []
        for fila in self._filas.values():
            if fila["cola"] != cola or fila["hecho_at"] is not None:
                continue
            if fila["reclamado_at"] is not None:
                continue
            fila["reclamado_at"] = "now"
            fila["intentos"] += 1
            salen.append(_de(fila))
            if len(salen) >= limite:
                break
        return salen

    async def marcar_hecho(self, fila_id: int) -> None:
        for fila in self._filas.values():
            if fila["id"] == fila_id:
                fila["hecho_at"] = "now"
                fila["ultimo_error"] = None
                return

    async def marcar_error(self, fila_id: int, error: str) -> None:
        for fila in self._filas.values():
            if fila["id"] == fila_id:
                fila["ultimo_error"] = error
                fila["reclamado_at"] = None
                return

    async def reintentar(self, event_id: str) -> bool:
        fila = self._filas.get(event_id)
        if fila is None or fila["hecho_at"] is not None:
            return False
        fila["reclamado_at"] = None
        fila["ultimo_error"] = None
        return True

    async def listar_fallidos(self, limite: int = 200) -> list[Fila]:
        return [_de(f) for f in self._filas.values() if f["hecho_at"] is None and f["ultimo_error"]][
            :limite
        ]


def _de(fila: dict[str, Any]) -> Fila:
    return Fila(
        id=fila["id"],
        event_id=fila["event_id"],
        tipo=fila["tipo"],
        cola=fila["cola"],
        tenant_slug=fila["tenant_slug"],
        payload=fila["payload"],
        intentos=fila["intentos"],
        ultimo_error=fila["ultimo_error"],
    )


def _nueva(event_id: str, tipo: str = TIPO_WEBHOOK) -> Nueva:
    return Nueva(
        event_id=event_id,
        tipo=tipo,
        cola=COLA_APP,
        tenant_slug="demo",
        payload={"event_type": "document.radicado.created"},
    )


@pytest.mark.asyncio
async def test_un_event_id_duplicado_no_manda_el_webhook_dos_veces() -> None:
    repo = _Memoria()
    enviados: list[str] = []

    async def enviar(_conn: object, fila: Fila) -> None:
        enviados.append(fila.event_id)

    assert await repo.encolar(_nueva("evt-1")) is True
    assert await repo.encolar(_nueva("evt-1")) is False
    await recorrer(repo, cola=COLA_APP, reclamador="app", manejadores={TIPO_WEBHOOK: enviar})
    await recorrer(repo, cola=COLA_APP, reclamador="app", manejadores={TIPO_WEBHOOK: enviar})
    assert enviados == ["evt-1"]


@pytest.mark.asyncio
async def test_sin_proveedor_intelligence_se_marca_y_nadie_es_llamado() -> None:
    repo = _Memoria()
    llamados: list[str] = []

    async def llamar(_conn: object, fila: Fila) -> None:
        if intelligence_se_omite(proveedor_url=""):
            return
        llamados.append(fila.event_id)
        raise AssertionError("no debía llamar al proveedor")

    await repo.encolar(_nueva("intel-1", TIPO_INTELLIGENCE))
    hechos = await recorrer(
        repo, cola=COLA_APP, reclamador="app", manejadores={TIPO_INTELLIGENCE: llamar}
    )
    assert hechos == 1
    assert llamados == []
    assert intelligence_se_omite(proveedor_url="")
    assert not intelligence_se_omite(proveedor_url="https://ppi.example")


@pytest.mark.asyncio
async def test_reintentar_suelta_una_fila_fallida() -> None:
    repo = _Memoria()

    async def fallar(_conn: object, fila: Fila) -> None:
        del fila
        raise RuntimeError("smtp caído")

    await repo.encolar(_nueva("evt-fallo"))
    await recorrer(repo, cola=COLA_APP, reclamador="app", manejadores={TIPO_WEBHOOK: fallar})
    fallidos = await repo.listar_fallidos()
    assert [f.event_id for f in fallidos] == ["evt-fallo"]
    assert await repo.reintentar("evt-fallo") is True
    enviados: list[str] = []

    async def enviar(_conn: object, fila: Fila) -> None:
        enviados.append(fila.event_id)

    await recorrer(repo, cola=COLA_APP, reclamador="app", manejadores={TIPO_WEBHOOK: enviar})
    assert enviados == ["evt-fallo"]
    assert await repo.listar_fallidos() == []
