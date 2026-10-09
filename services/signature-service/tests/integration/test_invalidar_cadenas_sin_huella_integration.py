# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""`app.ops.invalidar_cadenas_sin_huella` contra Postgres REAL (AREA-04-09).

Lo que un doble no ve: el índice único parcial `ux_firma_solicitud_objeto_abierta`
(una cadena `pendiente` sin huella BLOQUEA abrir la de reemplazo hasta que se
invalide), el CHECK de estado `invalidada` y la atomicidad cambio+asiento (si el
asiento falla, la cadena NO queda invalidada). El asiento se sustituye por un
registrador para no ensuciar la cadena inmutable viva con filas de prueba.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import _schema as tenant_schema
from app.ops import invalidar_cadenas_sin_huella as ops

pytestmark = pytest.mark.asyncio

HUELLA = "a" * 64
OPERADOR_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
OP = {"operador_id": OPERADOR_ID, "operador": "ANA"}


async def _solicitud(conn, tipo, objeto, *, estado="pendiente", huella=None) -> uuid.UUID:
    sid = await conn.fetchval(
        "INSERT INTO firma_solicitud (objeto_tipo, objeto_id, estado, payload_sha256) "
        "VALUES ($1, $2, $3, $4) RETURNING id",
        tipo, objeto, estado, huella,
    )
    for orden, est in ((1, "firmado"), (2, "pendiente"), (3, "pendiente")):
        await conn.execute(
            "INSERT INTO firma_cadena (solicitud_id, orden, firmante_id, estado) "
            "VALUES ($1, $2, $3, $4)",
            sid, orden, uuid.uuid4(), est,
        )
    return sid


async def _estado(conn, sid) -> tuple[str, str | None, list[str]]:
    sol = await conn.fetchrow(
        "SELECT estado, invalidada_motivo FROM firma_solicitud WHERE id = $1", sid
    )
    turnos = [
        r["estado"]
        for r in await conn.fetch(
            "SELECT estado FROM firma_cadena WHERE solicitud_id = $1 ORDER BY orden", sid
        )
    ]
    return sol["estado"], sol["invalidada_motivo"], turnos


@pytest.fixture
def asientos(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    vistos: list[dict] = []

    class _Falso:
        @staticmethod
        async def append(conn, **kw):  # noqa: ANN001, ANN003
            vistos.append(kw)
            return "h"

    monkeypatch.setattr(ops, "_audit", _Falso)
    return vistos


async def _en_tenant(tenant_ctx):
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    return slug, conn


async def test_invalida_solo_pendientes_sin_huella_de_radicado_y_expediente(
    tenant_ctx, asientos
) -> None:
    slug, conn = await _en_tenant(tenant_ctx)
    vieja_r = await _solicitud(conn, "radicado", "R-1")
    vieja_e = await _solicitud(conn, "expediente", "E-1")
    con_huella = await _solicitud(conn, "radicado", "R-2", huella=HUELLA)
    cerrada = await _solicitud(conn, "radicado", "R-3", estado="firmado")
    borrador = await _solicitud(conn, "borrador", "B-1")

    hechas, fallidas = await ops.invalidar_en_tenant(
        conn, schema=tenant_schema(slug), slug=slug, dry_run=False, **OP
    )
    assert (hechas, fallidas) == (2, 0)
    for sid in (vieja_r, vieja_e):
        estado, motivo, turnos = await _estado(conn, sid)
        assert (estado, motivo) == ("invalidada", "cadena_sin_huella")
        # El turno ya firmado se conserva; los pendientes caen.
        assert turnos == ["firmado", "invalidada", "invalidada"]
    assert (await _estado(conn, con_huella))[0] == "pendiente"
    assert (await _estado(conn, cerrada))[0] == "firmado"
    assert (await _estado(conn, borrador))[0] == "pendiente"
    # Un asiento por cadena, escrito por la aplicación, con el motivo.
    cadenas = [a for a in asientos if a["action"] == "firma.cadena_invalidada"]
    assert sorted(a["object_ref"] for a in cadenas) == ["E-1", "R-1"]
    assert all(a["actor"] == str(OPERADOR_ID) and a["payload"]["operador"] == "ANA" for a in cadenas)
    assert all(
        a["action"] == "firma.cadena_invalidada"
        and a["canal"] == "system"
        and a["payload"]["motivo"] == "cadena_sin_huella"
        and a["payload"]["turnos_invalidados"] == 2
        and a["tenant_slug"] == slug
        for a in cadenas
    )


async def test_libera_el_indice_para_abrir_la_cadena_de_reemplazo(tenant_ctx, asientos) -> None:
    slug, conn = await _en_tenant(tenant_ctx)
    await _solicitud(conn, "radicado", "R-9")
    with pytest.raises(asyncpg.UniqueViolationError):
        await _solicitud(conn, "radicado", "R-9", huella=HUELLA)
    await ops.invalidar_en_tenant(
        conn, schema=tenant_schema(slug), slug=slug, dry_run=False, **OP
    )
    nueva = await _solicitud(conn, "radicado", "R-9", huella=HUELLA)  # ya cabe
    assert (await _estado(conn, nueva))[0] == "pendiente"


async def test_es_idempotente(tenant_ctx, asientos) -> None:
    slug, conn = await _en_tenant(tenant_ctx)
    await _solicitud(conn, "radicado", "R-1")
    args = dict(schema=tenant_schema(slug), slug=slug, dry_run=False, **OP)
    assert await ops.invalidar_en_tenant(conn, **args) == (1, 0)
    assert await ops.invalidar_en_tenant(conn, **args) == (0, 0)
    assert len([a for a in asientos if a["action"] == "firma.cadena_invalidada"]) == 1


async def test_dry_run_no_toca_nada(tenant_ctx, asientos) -> None:
    slug, conn = await _en_tenant(tenant_ctx)
    sid = await _solicitud(conn, "radicado", "R-1")
    await _solicitud(conn, "radicado", "R-2", huella=HUELLA)  # vigente: no se cuenta
    await _solicitud(conn, "radicado", "R-3", estado="firmado")  # historia: no se cuenta
    res = await ops.invalidar_en_tenant(
        conn, schema=tenant_schema(slug), slug=slug, dry_run=True, **OP
    )
    assert res == (1, 0)
    assert (await _estado(conn, sid))[0] == "pendiente"
    assert not [a for a in asientos if a["action"] == "firma.cadena_invalidada"]


async def test_si_el_asiento_falla_la_cadena_no_queda_invalidada(
    tenant_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomicidad: nunca hay cadena invalidada sin asiento."""
    slug, conn = await _en_tenant(tenant_ctx)
    sid = await _solicitud(conn, "radicado", "R-1")

    class _Roto:
        @staticmethod
        async def append(conn, **kw):  # noqa: ANN001, ANN003
            if kw["action"] == "firma.cadena_invalidada":
                raise RuntimeError("audit_log caído")
            return "h"

    monkeypatch.setattr(ops, "_audit", _Roto)
    res = await ops.invalidar_en_tenant(
        conn, schema=tenant_schema(slug), slug=slug, dry_run=False, **OP
    )
    assert res == (0, 1)
    assert await _estado(conn, sid) == ("pendiente", None, ["firmado", "pendiente", "pendiente"])


async def test_el_slug_se_deriva_del_schema() -> None:
    assert ops.slug_de("tenant_mi_tenant") == "mi-tenant"


async def test_la_corrida_deja_asiento_de_inicio_y_fin_con_conteos(tenant_ctx, asientos) -> None:
    slug, conn = await _en_tenant(tenant_ctx)
    await _solicitud(conn, "radicado", "R-1")
    await ops.invalidar_en_tenant(
        conn, schema=tenant_schema(slug), slug=slug, dry_run=False, **OP
    )
    marco = [a for a in asientos if a["action"] == ops.ACCION_CORRIDA]
    assert [a["payload"]["fase"] for a in marco] == ["inicio", "fin"]
    assert len({a["payload"]["corrida_id"] for a in marco}) == 1
    assert all(a["actor"] == str(OPERADOR_ID) and a["payload"]["dry_run"] is False for a in marco)
    assert marco[0]["payload"]["candidatas"] == 1
    assert (marco[1]["payload"]["invalidadas"], marco[1]["payload"]["fallidas"]) == (1, 0)


async def test_el_dry_run_tambien_asienta_la_corrida(tenant_ctx, asientos) -> None:
    slug, conn = await _en_tenant(tenant_ctx)
    await _solicitud(conn, "radicado", "R-1")
    await ops.invalidar_en_tenant(conn, schema=tenant_schema(slug), slug=slug, dry_run=True, **OP)
    marco = [a for a in asientos if a["action"] == ops.ACCION_CORRIDA]
    assert [a["payload"]["fase"] for a in marco] == ["inicio", "fin"]
    assert all(a["payload"]["dry_run"] is True for a in marco)


async def test_run_sin_operador_sale_distinto_de_cero() -> None:
    with pytest.raises(SystemExit) as exc:
        await ops.run(["--dry-run"])
    assert exc.value.code != 0


async def test_operador_inexistente_aborta_sin_escribir(tenant_ctx, asientos, monkeypatch) -> None:
    from tests.integration.conftest import DSN

    slug, conn = await _en_tenant(tenant_ctx)
    sid = await _solicitud(conn, "radicado", "R-1")
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS auth_users (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "username varchar(80) NOT NULL UNIQUE, is_active boolean NOT NULL DEFAULT true)"
    )
    monkeypatch.setattr(ops, "_DSN", DSN)
    codigo = await ops.run(["--operador", "NADIE", "--tenant", slug])
    assert codigo != 0
    assert (await _estado(conn, sid))[0] == "pendiente"
    assert asientos == []
    # Con el operador dado de alta, la misma corrida procede.
    await conn.execute("INSERT INTO auth_users (id, username) VALUES ($1, 'ANA')", OPERADOR_ID)
    assert await ops.run(["--operador", "ana", "--tenant", slug]) == 0
    assert (await _estado(conn, sid))[0] == "invalidada"


async def test_si_el_asiento_de_inicio_falla_no_se_toca_nada(
    tenant_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = await _en_tenant(tenant_ctx)
    sid = await _solicitud(conn, "radicado", "R-1")

    class _Roto:
        @staticmethod
        async def append(conn, **kw):  # noqa: ANN001, ANN003
            raise RuntimeError("audit_log caído")

    monkeypatch.setattr(ops, "_audit", _Roto)
    with pytest.raises(RuntimeError):
        await ops.invalidar_en_tenant(
            conn, schema=tenant_schema(slug), slug=slug, dry_run=False, **OP
        )
    assert (await _estado(conn, sid))[0] == "pendiente"
