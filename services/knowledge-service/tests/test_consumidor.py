# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El consumidor empuja por el punto de paso y la carga inicial resume con cursor.

El nivel del sobre no manda. Una reclasificación hacia un nivel apagado ordena
borrar, no reenvía el contenido. Con el proveedor en `local` no hay llamadas.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.core.consumidor import atender, cargar_pagina
from app.core.paso import destino_de
from app.workers.intelligence_consumer import GROUP, STREAMS

RADICADO = uuid.UUID("00000000-0000-4000-8000-0000000000b2")
OTRO = uuid.UUID("00000000-0000-4000-8000-0000000000c4")
SHA = "ab" * 32


def _fila(ident: uuid.UUID, cuando: datetime, **cambios: Any) -> dict[str, Any]:
    fila: dict[str, Any] = {
        "id": ident,
        "tracking_number": "2026-DEMO-E-000001",
        "doc_type": "E",
        "subject": "Solicitud de copia",
        "doc_class": "peticion",
        "dest_dept_code": 12,
        "status": "registered",
        "registered_at": datetime(2026, 10, 31, 15, 0, tzinfo=UTC),
        "due_date": date(2026, 11, 15),
        "sender_name": "Ana",
        "sender_entity": None,
        "metadata": {"ciudad": "Bogotá"},
        "updated_at": cuando,
    }
    fila.update(cambios)
    return fila


class _Conn:
    def __init__(self, **datos: Any) -> None:
        self.datos = datos
        self.sql: list[str] = []
        self.escrituras: list[tuple[str, tuple[Any, ...]]] = []

    def _nivel(self, ident: Any) -> Any:
        por_id = self.datos.get("niveles_por_id") or {}
        if ident in por_id:
            return por_id[ident]
        return self.datos.get("nivel", 1)

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.sql.append(sql)
        if "public.tenants" in sql:
            return {
                "asistente_habilitado": self.datos.get("bandera", True),
                "intelligence_provider": self.datos.get("proveedor", "ppi"),
                "intelligence_levels": self.datos.get("niveles", [1]),
                "intelligence_send_metadata": self.datos.get("metadata", False),
            }
        if "send_suspended" in sql:
            return {"send_suspended": self.datos.get("suspendido", False)}
        if "cursor-carga" in sql:
            return {"backfill_cursor": self.datos.get("cursor")}
        if "paso:estado" in sql:
            if self.datos.get("fila_ausente"):
                return None
            return {
                "nivel_seguridad": self._nivel(args[0]),
                "status": self.datos.get("status", "registered"),
            }
        if "paso:nivel" in sql:
            if self.datos.get("fila_ausente") or self.datos.get("status") == "anulado":
                return None
            return {"nivel_seguridad": self._nivel(args[0])}
        if "fila-oficial" in sql:
            if self.datos.get("fila_ausente"):
                return None
            return self.datos["fila"]
        raise AssertionError(sql)

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        self.sql.append(sql)
        if "anexos-del-radicado" in sql:
            return self.datos.get("anexos", [])
        if "carga-inicial" in sql:
            return self.datos.get("pagina", [])
        raise AssertionError(sql)

    async def execute(self, sql: str, *args: Any) -> str:
        self.escrituras.append((sql, args))
        return "OK"


class _Transporte:
    def __init__(self) -> None:
        self.cuerpos: list[dict[str, Any]] = []

    async def request(self, method: str, url: str, *, json: dict[str, Any]) -> None:
        self.cuerpos.append(json)


def _sobre(tipo: str, **payload: Any) -> dict[str, Any]:
    return {"event_type": tipo, "payload": payload}


def _cliente(transporte: _Transporte) -> Any:
    return destino_de("http://ppi.internal", transporte)


@pytest.mark.asyncio
async def test_un_alta_publica_sale_aunque_el_sobre_diga_otra_cosa() -> None:
    transporte = _Transporte()
    conn = _Conn(
        fila=_fila(RADICADO, datetime(2026, 10, 31, 15, 1, tzinfo=UTC)),
        anexos=[
            {
                "id": uuid.UUID("00000000-0000-4000-8000-0000000000c3"),
                "filename": "solicitud.pdf",
                "mime_type": "application/pdf",
                "file_size": 120,
                "es_principal": True,
                "folios": 2,
                "sha256": SHA,
            }
        ],
    )
    decision = await atender(
        conn,
        "demo",
        _sobre("document.radicado.created", id=str(RADICADO), nivel_seguridad=3),
        _cliente(transporte),
    )
    assert decision.sale is True
    assert len(transporte.cuerpos) == 1
    record = transporte.cuerpos[0]["items"][0]["record"]
    assert record["sensitivity"] == "public"
    assert record["direction"] == "incoming"
    assert "metadata" not in record
    anexo = transporte.cuerpos[0]["items"][1]["attachment"]
    assert anexo["content"]["mode"] == "locator" and anexo["sha256"] == SHA
    assert conn.escrituras[0][1][0] == "push"


@pytest.mark.asyncio
async def test_en_local_un_alta_no_llama() -> None:
    transporte = _Transporte()
    conn = _Conn(
        proveedor="local",
        fila=_fila(RADICADO, datetime(2026, 10, 31, 15, 1, tzinfo=UTC)),
    )
    decision = await atender(
        conn, "demo", _sobre("document.radicado.created", id=str(RADICADO)), _cliente(transporte)
    )
    assert decision.sale is False and decision.motivo == "local"
    assert transporte.cuerpos == []
    assert conn.escrituras == []


@pytest.mark.asyncio
async def test_reclasificar_a_un_nivel_apagado_borra_sin_contenido() -> None:
    transporte = _Transporte()
    conn = _Conn(
        nivel=2,
        fila=_fila(RADICADO, datetime(2026, 10, 31, 15, 1, tzinfo=UTC)),
    )
    decision = await atender(
        conn,
        "demo",
        _sobre("document.radicado.reclassified", id=str(RADICADO), nivel_nuevo=2),
        _cliente(transporte),
    )
    assert decision.sale is True
    assert len(transporte.cuerpos) == 1
    item = transporte.cuerpos[0]["items"][0]
    assert item["type"] == "deletion"
    assert item["deletion"]["reason"] == "reclassified"
    assert item["deletion"]["include_children"] is True
    assert "record" not in item
    assert conn.escrituras[0][1][0] == "purge"


@pytest.mark.asyncio
async def test_reclasificar_dentro_de_lo_habilitado_empuja() -> None:
    transporte = _Transporte()
    conn = _Conn(fila=_fila(RADICADO, datetime(2026, 10, 31, 15, 1, tzinfo=UTC)))
    decision = await atender(
        conn,
        "demo",
        _sobre("document.radicado.reclassified", id=str(RADICADO), nivel_nuevo=1),
        _cliente(transporte),
    )
    assert decision.sale is True
    assert transporte.cuerpos[0]["items"][0]["type"] == "record"
    assert conn.escrituras[0][1][0] == "push"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tipo", "razon", "extra"),
    [
        ("document.radicado.annulled", "annulled", {"status": "anulado"}),
        ("document.radicado.purged", "purged", {"fila_ausente": True}),
    ],
)
async def test_anular_o_purgar_ordena_borrar(tipo: str, razon: str, extra: dict[str, Any]) -> None:
    transporte = _Transporte()
    conn = _Conn(**extra)
    decision = await atender(conn, "demo", _sobre(tipo, id=str(RADICADO)), _cliente(transporte))
    assert decision.sale is True
    assert transporte.cuerpos[0]["items"][0]["deletion"]["reason"] == razon
    operacion, _ref, nivel = conn.escrituras[0][1][:3]
    assert operacion == "purge"
    assert nivel == (None if extra.get("fila_ausente") else 1)


@pytest.mark.asyncio
async def test_un_paso_de_tramite_usa_el_id_del_radicado() -> None:
    transporte = _Transporte()
    conn = _Conn(fila=_fila(RADICADO, datetime(2026, 10, 31, 15, 1, tzinfo=UTC)))
    decision = await atender(
        conn,
        "demo",
        _sobre("workflow.step.completed", radicado_id=str(RADICADO)),
        _cliente(transporte),
    )
    assert decision.sale is True
    assert transporte.cuerpos[0]["items"][0]["record"]["external_ref"] == str(RADICADO)


@pytest.mark.asyncio
async def test_un_evento_ajeno_no_toca_la_base_ni_el_proveedor() -> None:
    transporte = _Transporte()
    conn = _Conn()
    decision = await atender(
        conn,
        "demo",
        _sobre("workflow.step.overdue", id=str(RADICADO)),
        _cliente(transporte),
    )
    assert decision.motivo == "ajeno"
    assert conn.sql == [] and transporte.cuerpos == []


@pytest.mark.asyncio
async def test_la_carga_salta_el_nivel_apagado_y_deja_el_cursor() -> None:
    transporte = _Transporte()
    primero = datetime(2026, 10, 31, 15, 1, tzinfo=UTC)
    segundo = datetime(2026, 10, 31, 16, 0, tzinfo=UTC)
    conn = _Conn(
        niveles_por_id={RADICADO: 1, OTRO: 2},
        pagina=[_fila(RADICADO, primero), _fila(OTRO, segundo)],
    )
    hechos = await cargar_pagina(conn, "demo", _cliente(transporte))
    assert hechos == 2
    assert len(transporte.cuerpos) == 1
    assert transporte.cuerpos[0]["items"][0]["record"]["external_ref"] == str(RADICADO)
    pagina = next(sql for sql in conn.sql if "carga-inicial" in sql)
    assert "nivel_seguridad = ANY" in pagina and "anulado" in pagina
    cursores = [args[0] for sql, args in conn.escrituras if "backfill_cursor" in sql]
    assert cursores[-1] == f"{segundo.isoformat()}|{OTRO}"
    assert [args[0] for sql, args in conn.escrituras if "intelligence_send_log" in sql] == ["push"]


@pytest.mark.asyncio
async def test_la_carga_apagada_no_avanza_ni_llama() -> None:
    transporte = _Transporte()
    conn = _Conn(
        proveedor="local",
        pagina=[_fila(RADICADO, datetime(2026, 10, 31, 15, 1, tzinfo=UTC))],
    )
    assert await cargar_pagina(conn, "demo", _cliente(transporte)) == 0
    assert transporte.cuerpos == []
    assert not any("carga-inicial" in sql for sql in conn.sql)
    assert not any("backfill_cursor" in sql for sql, _args in conn.escrituras)


def test_el_grupo_es_intelligence_gateway() -> None:
    assert GROUP == "intelligence-gateway"
    assert STREAMS == ("orpycamcp.document.events", "orpycamcp.workflow.events")
