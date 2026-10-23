# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""B10 ítem 2 contra Postgres REAL: predicado de huella vigente, asiento y atomicidad.

Lo que un doble no ve: que el `IS DISTINCT FROM` trate bien las filas sin huella,
que el CHECK de estado admita `invalidada`, y que si el asiento falla la solicitud
siga `pendiente` (el rollback lo produce Postgres). El asiento se sustituye por un
registrador para no ensuciar la cadena inmutable viva.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.database import _schema as tenant_schema
from app.services import invalidacion

pytestmark = pytest.mark.asyncio

H0 = "0" * 64
H1 = "1" * 64
H2 = "2" * 64


async def _en_tenant(tenant_ctx):
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    return slug, conn


async def _solicitud(conn, objeto: str, huella: str | None, estado: str = "pendiente") -> uuid.UUID:
    sid = await conn.fetchval(
        "INSERT INTO firma_solicitud (objeto_tipo, objeto_id, estado, payload_sha256) "
        "VALUES ('borrador', $1, $2, $3) RETURNING id",
        objeto, estado, huella,
    )
    for orden, est in ((1, "firmado"), (2, "pendiente")):
        await conn.execute(
            "INSERT INTO firma_cadena (solicitud_id, orden, firmante_id, estado) "
            "VALUES ($1, $2, $3, $4)",
            sid, orden, uuid.uuid4(), est,
        )
    return sid


async def _estado(conn, sid) -> str:
    return await conn.fetchval("SELECT estado FROM firma_solicitud WHERE id = $1", sid)


@pytest.fixture
def asientos(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    vistos: list[dict] = []

    class _Falso:
        @staticmethod
        async def append(conn, **kw):  # noqa: ANN001, ANN003
            vistos.append(kw)
            return "h"

    monkeypatch.setattr(invalidacion, "audit", _Falso)
    return vistos


async def _invalidar(conn, slug, vigente, *, origen="evento", event_id="ev-1") -> int:
    return await invalidacion.invalidar_por_contenido(
        conn, tenant_slug=slug, objeto_id="b-1", vigente=vigente,
        origen=origen, event_id=event_id,
    )


async def test_invalida_lo_que_no_es_la_huella_vigente_y_deja_asiento(tenant_ctx, asientos) -> None:
    slug, conn = await _en_tenant(tenant_ctx)
    vieja = await _solicitud(conn, "b-1", H0)
    cerrada = await _solicitud(conn, "b-1", H0, estado="firmado")
    sin_huella = await _solicitud(conn, "b-1", None, estado="firmado")
    ajena = await _solicitud(conn, "b-2", H0)

    n = await _invalidar(conn, slug, H1)

    assert n == 3
    assert [await _estado(conn, s) for s in (vieja, cerrada, sin_huella)] == ["invalidada"] * 3
    assert await _estado(conn, ajena) == "pendiente"  # otro borrador: intacto
    turnos = [
        r["estado"] for r in await conn.fetch(
            "SELECT estado FROM firma_cadena WHERE solicitud_id = $1 ORDER BY orden", vieja
        )
    ]
    assert turnos == ["firmado", "invalidada"]  # el resuelto se conserva, el pendiente cae
    assert len(asientos) == 1
    a = asientos[0]
    assert a["action"] == "firma.circuito_invalidado" and a["object_ref"] == "b-1"
    assert a["actor"] == "system" and a["canal"] == "system"
    assert a["payload"]["vigente"] == H1 and a["payload"]["event_id"] == "ev-1"
    assert sorted(a["payload"]["solicitudes"]) == sorted(str(s) for s in (vieja, cerrada, sin_huella))


async def test_evento_tardio_no_tumba_el_circuito_sobre_el_texto_vigente(
    tenant_ctx, asientos
) -> None:
    """h0→h1→h2: el aviso de h1 llega tarde y el circuito abierto sobre h2 sobrevive."""
    slug, conn = await _en_tenant(tenant_ctx)
    sobre_h2 = await _solicitud(conn, "b-1", H2)

    n = await _invalidar(conn, slug, H2)  # la vigente, preguntada AHORA, es h2

    assert n == 0
    assert await _estado(conn, sobre_h2) == "pendiente"
    assert asientos == []


async def test_reentrega_no_duplica_asiento(tenant_ctx, asientos) -> None:
    slug, conn = await _en_tenant(tenant_ctx)
    await _solicitud(conn, "b-1", H0)

    primera = await _invalidar(conn, slug, H1)
    segunda = await _invalidar(conn, slug, H1)

    assert (primera, segunda) == (1, 0)
    assert len(asientos) == 1


async def test_borrador_desaparecido_invalida_todo_lo_vivo(tenant_ctx, asientos) -> None:
    slug, conn = await _en_tenant(tenant_ctx)
    con = await _solicitud(conn, "b-1", H0)
    sin = await _solicitud(conn, "b-1", None, estado="firmado")  # IS DISTINCT FROM NULL la dejaría viva

    n = await _invalidar(conn, slug, None)

    assert n == 2
    assert await _estado(conn, con) == await _estado(conn, sin) == "invalidada"
    motivo = await conn.fetchval("SELECT invalidada_motivo FROM firma_solicitud WHERE id=$1", con)
    assert motivo == invalidacion.MOTIVO_DESAPARECIDO
    assert asientos[0]["payload"]["vigente"] is None


async def test_si_el_asiento_falla_la_solicitud_sigue_pendiente(
    tenant_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UPDATE y asiento son UNA transacción: si el asiento revienta, nada queda invalidado."""
    slug, conn = await _en_tenant(tenant_ctx)
    sid = await _solicitud(conn, "b-1", H0)

    class _Roto:
        @staticmethod
        async def append(conn, **kw):  # noqa: ANN001, ANN003
            raise RuntimeError("audit caído")

    monkeypatch.setattr(invalidacion, "audit", _Roto)
    with pytest.raises(RuntimeError):
        await _invalidar(conn, slug, H1)

    assert await _estado(conn, sid) == "pendiente"
    turno = await conn.fetchval(
        "SELECT estado FROM firma_cadena WHERE solicitud_id = $1 AND orden = 2", sid
    )
    assert turno == "pendiente"
