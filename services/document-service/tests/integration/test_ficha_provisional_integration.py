# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Ficha provisional del remitente anónimo y su conciliación — contra Postgres REAL (C17).

La garantía que importa vive ENTRE piezas (la ventanilla, la transacción del
radicado, los CHECK/trigger de la 049 y la cola con su clearance), así que un doble
no la prueba. Qué fija este módulo:

1. Quien escribe la cédula de OTRO no queda atado a SU ficha: nace una provisional
   nueva, la existente queda intacta, y el documento declarado NO se ocupa.
2. La ficha provisional es atómica con el radicado (sin radicado no hay ficha huérfana).
3. Una provisional nunca contamina el directorio (búsqueda, correo, duplicados) ni se
   puede vincular desde el radicador interno ni fusionar.
4. La base impone lo declarado: documento inmutable, estados terminales sin vuelta.
5. Conciliar: vincular / confirmar / descartar, con asiento atómico, sin tocar el
   radicado, y con el clearance en el `WHERE` (cola, conteo y UPDATE).
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.database import tenant_schema
from app.repositories.tercero_repository import TerceroRepository
from app.services.tercero_service import TerceroService
from tests.integration.test_ventanilla_directorio_integration import (
    _preparar_ventanilla,
    _radicar_por_ventanilla,
)

pytestmark = pytest.mark.integration

ACTOR = uuid.uuid4()


async def _entorno(tenant_ctx: tuple[str, asyncpg.Connection]) -> tuple[str, asyncpg.Connection]:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    await _preparar_ventanilla(conn)
    return slug, conn


async def _existente(conn: asyncpg.Connection, documento: str = "1020304050") -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO tercero (nombre, tipo, tipo_documento, numero_documento, email, direccion) "
        "VALUES ('María Gómez Real', 'ciudadano', 'CC', $1, 'maria@real.co', 'Calle Real 1') "
        "RETURNING id",
        documento,
    )


async def _anonimo(conn, slug, documento="1020304050", asunto="Escrito", nivel=1, **kw):
    """Radica por la ventanilla como un anónimo que escribe ESE documento."""
    await _radicar_por_ventanilla(
        conn,
        slug,
        nombre=kw.get("nombre", "Suplantador"),
        tipo_identificacion=kw.get("tipo", "CC"),
        identificacion=documento,
        email="otro@falso.co",
        asunto=asunto,
    )
    ficha = await conn.fetchval(
        "SELECT t.id FROM tercero t JOIN radicado_actor ra ON ra.tercero_id = t.id "
        "JOIN radicados r ON r.id = ra.radicado_id WHERE r.subject = $1", asunto
    )
    if nivel > 1:
        await conn.execute(
            # `por_clasificar` es la causal que el CHECK `radicados_causal_catalogo` (050)
            # admite en cualquier nivel >= 2 sin plazo: la fixture no inventa una causal.
            "UPDATE radicados SET nivel_seguridad = $2, "
            "clasificacion_fundamento = 'Ley 1712/2014 art. 19 (prueba)', "
            "clasificacion_causal = 'por_clasificar' WHERE subject = $1",
            asunto,
            nivel,
        )
    return ficha


async def _asientos(conn, slug: str, accion: str) -> list[dict]:
    filas = await conn.fetch(
        "SELECT actor, object_ref, payload FROM public.audit_log "
        "WHERE tenant_slug = $1 AND action = $2 ORDER BY id",
        slug,
        accion,
    )
    return [
        {"actor": f["actor"], "object_ref": f["object_ref"], "payload": json.loads(f["payload"])}
        for f in filas
    ]


# ---------------------------------------------------------------- 1. la puerta


@pytest.mark.asyncio
async def test_escribir_la_cedula_de_otro_no_lo_ata_a_su_ficha(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    real = await _existente(conn)
    antes = await conn.fetchrow("SELECT * FROM tercero WHERE id = $1", real)

    provisional = await _anonimo(conn, slug)

    # Control positivo: el documento SÍ es de la ficha existente y se encuentra.
    assert (await TerceroRepository(conn, slug).get_por_documento("CC", "1020304050"))["id"] == real
    assert provisional != real, "el radicado NO se ató a la ficha ajena"
    assert await conn.fetchval("SELECT count(*) FROM radicado_actor WHERE tercero_id = $1", real) == 0
    # La ficha existente quedó intacta, hasta en `actualizado_at`.
    assert await conn.fetchrow("SELECT * FROM tercero WHERE id = $1", real) == antes
    fila = await conn.fetchrow("SELECT * FROM tercero WHERE id = $1", provisional)
    assert (fila["identidad"], fila["conciliacion"]) == ("provisional", "pendiente")
    assert (fila["tipo_documento"], fila["numero_documento"]) == (None, None)
    assert (fila["documento_declarado_tipo"], fila["documento_declarado_numero"]) == (
        "CC",
        "1020304050",
    )
    assert fila["creado_por"] is None


@pytest.mark.asyncio
async def test_el_numero_inexistente_hace_lo_mismo_que_el_existente(tenant_ctx) -> None:
    """Un solo camino: la forma del resultado no delata si el documento existía."""
    slug, conn = await _entorno(tenant_ctx)
    await _existente(conn, "111")
    a = await _anonimo(conn, slug, documento="111", asunto="Con documento existente")
    b = await _anonimo(conn, slug, documento="999", asunto="Con documento nuevo")

    cols = "identidad, conciliacion, tipo_documento, numero_documento, creado_por"
    fa = await conn.fetchrow(f"SELECT {cols} FROM tercero WHERE id = $1", a)
    fb = await conn.fetchrow(f"SELECT {cols} FROM tercero WHERE id = $1", b)
    assert dict(fa) == dict(fb)


@pytest.mark.asyncio
async def test_el_asiento_de_la_provisional_no_lleva_el_documento(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    provisional = await _anonimo(conn, slug, documento="55667788", asunto="Asiento")

    (asiento,) = await _asientos(conn, slug, "document.tercero_provisional_creado")
    assert asiento["object_ref"] == str(provisional)
    assert asiento["actor"] is None
    assert asiento["payload"]["radicado"].startswith("20")
    assert "55667788" not in json.dumps(asiento), "el audit_log es inmutable: sin datos personales"


@pytest.mark.asyncio
async def test_sin_radicado_no_queda_ficha_huerfana(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    from app.services.document_service import DocumentService

    async def _falla(self, radicado, actor):
        raise RuntimeError("falla después de crear la ficha")

    with patch.object(DocumentService, "_encolar_created", _falla):
        with pytest.raises(RuntimeError):
            await _anonimo(conn, slug, asunto="Se cae")

    assert await conn.fetchval("SELECT count(*) FROM radicados") == 0
    assert await conn.fetchval("SELECT count(*) FROM tercero") == 0, "ficha huérfana"
    assert await _asientos(conn, slug, "document.tercero_provisional_creado") == []


# ----------------------------------------------------- 3. no contamina nada


@pytest.mark.asyncio
async def test_una_provisional_no_aparece_en_el_directorio(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    provisional = await _anonimo(conn, slug, nombre="Suplantador Único", asunto="Dir")
    await conn.execute("UPDATE tercero SET email = 'unico@falso.co' WHERE id = $1", provisional)
    # Control positivo con una ficha confirmada equivalente.
    await conn.execute(
        "INSERT INTO tercero (nombre, tipo, email) VALUES ('Suplantador Único', 'ciudadano', "
        "'otro-unico@real.co')"
    )
    servicio = TerceroService(conn, slug)

    encontrados = await servicio.buscar("Suplantador", tipo=None, limit=20)
    assert len(encontrados) == 1 and encontrados[0]["id"] != provisional
    # El correo de la provisional NO se reconoce: se crea una ficha confirmada nueva.
    resuelta = await servicio.resolver_por_email("unico@falso.co", nombre="Zoila Pérez", actor=None)
    assert resuelta is not None and resuelta["id"] != provisional
    assert resuelta["identidad"] == "confirmada"
    ids = {g_f["id"] for g in await servicio.grupos_duplicados() for g_f in g["fichas"]}
    assert str(provisional) not in {str(i) for i in ids}


@pytest.mark.asyncio
async def test_el_radicador_interno_no_puede_vincular_una_provisional(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    provisional = await _anonimo(conn, slug, asunto="Vinc")
    from app.schemas.document import DocumentCreate
    from app.services.document_service import DocumentService

    otro = (
        await DocumentService(conn, slug).create(
            DocumentCreate(
                doc_type="E", doc_class="Petición", dest_dept_code=1,
                subject="otro radicado", sender_name="x",
            ),
            registered_by=None,
        )
    ).id
    with pytest.raises(HTTPException) as exc:
        await TerceroService(conn, slug).vincular_actores(
            otro, [{"tercero_id": provisional, "rol": "destinatario"}], actor=None
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "tercero_provisional"


@pytest.mark.asyncio
async def test_una_provisional_no_se_fusiona(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    real = await _existente(conn)
    provisional = await _anonimo(conn, slug, asunto="Fus")
    with pytest.raises(HTTPException) as exc:
        await TerceroService(conn, slug).fusionar(real, provisional, actor=ACTOR)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "tercero_provisional"


# ------------------------------------------------------- 4. la base lo impone


@pytest.mark.asyncio
async def test_la_base_impone_las_reglas_de_la_provisional(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    provisional = await _anonimo(conn, slug, asunto="Reglas")

    async def _falla(sql: str, *args) -> None:
        with pytest.raises(asyncpg.PostgresError):
            async with conn.transaction():
                await conn.execute(sql, *args)

    # Una provisional NO puede recibir identidad por la puerta de atrás.
    await _falla("UPDATE tercero SET tipo_documento='CC', numero_documento='1' WHERE id=$1", provisional)
    # Lo declarado es evidencia.
    await _falla("UPDATE tercero SET documento_declarado_numero='2' WHERE id=$1", provisional)
    # Conciliar sin autor no se puede registrar.
    await _falla(
        "UPDATE tercero SET conciliacion='descartada', "
        "conciliacion_motivo='motivo largo suficiente' WHERE id=$1",
        provisional,
    )
    # Control positivo: la conciliación completa SÍ pasa.
    await conn.execute(
        "UPDATE tercero SET conciliacion='descartada', conciliada_at=now(), conciliada_por=$2, "
        "conciliacion_motivo='motivo largo suficiente' WHERE id=$1",
        provisional,
        ACTOR,
    )
    # Un estado terminal no vuelve a `pendiente` ni salta a otro terminal.
    await _falla(
        "UPDATE tercero SET conciliacion='pendiente', conciliada_at=NULL, conciliada_por=NULL, "
        "conciliacion_motivo=NULL WHERE id=$1",
        provisional,
    )


@pytest.mark.asyncio
async def test_una_provisional_no_choca_con_el_indice_del_documento(tenant_ctx) -> None:
    """Dos provisionales con el mismo número declarado conviven, y con la ficha real."""
    slug, conn = await _entorno(tenant_ctx)
    await _existente(conn)
    await _anonimo(conn, slug, asunto="Uno")
    await _anonimo(conn, slug, asunto="Dos")
    assert await conn.fetchval("SELECT count(*) FROM tercero WHERE conciliacion = 'pendiente'") == 2


# ------------------------------------------------------- 5. la conciliación


def _conciliar(conn, slug, ficha, accion, *, clearance=3, actor=ACTOR, **kw):
    # Vincular y confirmar exigen el medio de verificación; por omisión, el más fuerte.
    medio = kw.get(
        "medio_verificacion", "presencial_documento" if accion in ("vincular", "confirmar") else None
    )
    return TerceroService(conn, slug).conciliar(
        ficha,
        accion=accion,
        destino_id=kw.get("destino_id"),
        motivo=kw.get("motivo"),
        medio_verificacion=medio,
        actor=actor,
        max_clearance=clearance,
    )


@pytest.mark.asyncio
async def test_vincular_no_toca_el_radicado_y_el_destino_lo_hereda(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    real = await _existente(conn)
    provisional = await _anonimo(conn, slug, asunto="Vincular esto")
    antes = await conn.fetchrow(
        "SELECT r.tracking_number, ra.tercero_id, ra.nombre, ra.email FROM radicados r "
        "JOIN radicado_actor ra ON ra.radicado_id = r.id"
    )

    res = await _conciliar(conn, slug, provisional, "vincular", destino_id=real)

    assert res["conciliacion"] == "vinculada"
    despues = await conn.fetchrow(
        "SELECT r.tracking_number, ra.tercero_id, ra.nombre, ra.email FROM radicados r "
        "JOIN radicado_actor ra ON ra.radicado_id = r.id"
    )
    assert dict(despues) == dict(antes), "el número y el vínculo congelado no cambian"
    visibles = await TerceroService(conn, slug).radicados_de(real, max_clearance=3)
    assert [r["tracking_number"] for r in visibles] == [antes["tracking_number"]]
    fila = await conn.fetchrow("SELECT * FROM tercero WHERE id = $1", provisional)
    assert fila["fusionado_en"] == real
    assert fila["conciliada_por"] == ACTOR and fila["fusionado_por"] == ACTOR
    (asiento,) = await _asientos(conn, slug, "document.tercero_conciliado")
    assert asiento["actor"] == str(ACTOR)
    p = asiento["payload"]
    assert (p["accion"], p["destino_id"], p["documento_coincide"]) == ("vincular", str(real), True)
    assert p["radicados"] == [antes["tracking_number"]]
    assert p["medio_verificacion"] == "presencial_documento"
    assert fila["conciliacion_medio"] == "presencial_documento"
    # El asiento es inmutable: solo identificadores, ni documento ni nombres.
    assert "1020304050" not in json.dumps(asiento)
    assert not {"nombre_declarado", "destino_nombre"} & set(p)
    assert "Suplantador" not in json.dumps(asiento) and "María" not in json.dumps(asiento)


@pytest.mark.asyncio
async def test_vincular_a_una_ficha_de_otro_documento_exige_motivo(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    otra = await _existente(conn, "777")
    provisional = await _anonimo(conn, slug, asunto="Otro doc")

    with pytest.raises(HTTPException) as exc:
        await _conciliar(conn, slug, provisional, "vincular", destino_id=otra)
    assert exc.value.detail["error"] == "motivo_requerido"
    assert await conn.fetchval("SELECT conciliacion FROM tercero WHERE id=$1", provisional) == "pendiente"

    await _conciliar(
        conn, slug, provisional, "vincular", destino_id=otra,
        motivo="Digitó mal la cédula; es la misma persona",
    )
    (asiento,) = await _asientos(conn, slug, "document.tercero_conciliado")
    assert asiento["payload"]["documento_coincide"] is False
    assert asiento["payload"]["motivo"].startswith("Digitó")


@pytest.mark.asyncio
async def test_destinos_invalidos_y_doble_conciliacion(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    real = await _existente(conn)
    a = await _anonimo(conn, slug, asunto="A")
    b = await _anonimo(conn, slug, asunto="B")

    for destino, codigo in ((a, "destino_invalido"), (b, "destino_invalido")):  # provisionales / sí misma
        with pytest.raises(HTTPException) as exc:
            await _conciliar(conn, slug, a, "vincular", destino_id=destino)
        assert exc.value.detail["error"] == codigo
    with pytest.raises(HTTPException) as exc:
        await _conciliar(conn, slug, a, "vincular", destino_id=uuid.uuid4())
    assert exc.value.status_code == 404

    await _conciliar(conn, slug, a, "vincular", destino_id=real)
    with pytest.raises(HTTPException) as exc:
        await _conciliar(conn, slug, a, "descartar", motivo="ya no importa, motivo largo")
    assert (exc.value.status_code, exc.value.detail["error"]) == (409, "tercero_ya_conciliado")
    # Una ficha NO provisional tampoco se «concilia».
    with pytest.raises(HTTPException) as exc:
        await _conciliar(conn, slug, real, "confirmar")
    assert exc.value.status_code == 409
    # Y tampoco se vincula hacia una ficha que ya se fusionó en otra.
    with pytest.raises(HTTPException) as exc:
        await _conciliar(conn, slug, b, "vincular", destino_id=a)
    assert exc.value.detail["error"] == "destino_invalido"


@pytest.mark.asyncio
async def test_confirmar_pasa_a_ficha_definitiva_o_choca_con_la_existente(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    libre = await _anonimo(conn, slug, documento="4040", asunto="Libre")
    res = await _conciliar(conn, slug, libre, "confirmar")
    assert res["conciliacion"] == "confirmada"
    fila = await conn.fetchrow("SELECT * FROM tercero WHERE id = $1", libre)
    assert (fila["identidad"], fila["tipo_documento"], fila["numero_documento"]) == (
        "confirmada",
        "CC",
        "4040",
    )
    # Ahora SÍ es una ficha del directorio.
    assert [t["id"] for t in await TerceroService(conn, slug).buscar("Suplantador", tipo=None, limit=5)] == [libre]

    await _existente(conn, "5050")
    choca = await _anonimo(conn, slug, documento="5050", asunto="Choca")
    with pytest.raises(HTTPException) as exc:
        await _conciliar(conn, slug, choca, "confirmar")
    assert (exc.value.status_code, exc.value.detail["error"]) == (409, "documento_ya_registrado")
    assert await conn.fetchval("SELECT conciliacion FROM tercero WHERE id=$1", choca) == "pendiente"
    assert len(await _asientos(conn, slug, "document.tercero_conciliado")) == 1, "el rollback no deja asiento"


@pytest.mark.asyncio
async def test_descartar_exige_motivo_y_deja_la_ficha_sin_identidad(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    provisional = await _anonimo(conn, slug, asunto="Descartar")
    for motivo in (None, "", "corto"):
        with pytest.raises(HTTPException) as exc:
            await _conciliar(conn, slug, provisional, "descartar", motivo=motivo)
        assert exc.value.status_code == 422
    await _conciliar(conn, slug, provisional, "descartar", motivo="Sospecha de suplantación")
    fila = await conn.fetchrow("SELECT * FROM tercero WHERE id = $1", provisional)
    assert (fila["identidad"], fila["conciliacion"], fila["numero_documento"]) == (
        "provisional", "descartada", None,
    )
    assert fila["documento_declarado_numero"] == "1020304050", "lo declarado se conserva"


@pytest.mark.asyncio
async def test_si_falla_el_asiento_no_queda_conciliada(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    real = await _existente(conn)
    provisional = await _anonimo(conn, slug, asunto="Rollback")

    async def _falla(*a, **k):
        raise RuntimeError("audit caído")

    with patch("app.services.tercero_service.audit.append", _falla):
        with pytest.raises(RuntimeError):
            await _conciliar(conn, slug, provisional, "vincular", destino_id=real)

    fila = await conn.fetchrow("SELECT conciliacion, fusionado_en FROM tercero WHERE id=$1", provisional)
    assert (fila["conciliacion"], fila["fusionado_en"]) == ("pendiente", None)


# --------------------------------------------------- clearance (RF-SEG-08)


@pytest.mark.asyncio
async def test_la_cola_y_su_conteo_respetan_el_clearance(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    publica = await _anonimo(conn, slug, asunto="Publica", documento="1")
    reservada = await _anonimo(conn, slug, asunto="Reservada", documento="2", nivel=3)
    servicio = TerceroService(conn, slug)

    bajas, total_bajo = await servicio.por_conciliar(max_clearance=1, limit=20, offset=0)
    altas, total_alto = await servicio.por_conciliar(max_clearance=3, limit=20, offset=0)

    assert [f["id"] for f in bajas] == [publica] and total_bajo == 1, (
        "ni la fila ni el conteo confirman la reservada"
    )
    assert {f["id"] for f in altas} == {publica, reservada} and total_alto == 2
    assert [r["tracking_number"] for r in altas[0]["radicados"]]  # el jsonb se decodifica


@pytest.mark.asyncio
async def test_conciliar_fuera_del_clearance_es_404_y_no_toca_nada(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    real = await _existente(conn)
    reservada = await _anonimo(conn, slug, asunto="Reservada", nivel=3)

    with pytest.raises(HTTPException) as exc:
        await _conciliar(conn, slug, reservada, "vincular", destino_id=real, clearance=1)
    assert (exc.value.status_code, exc.value.detail["error"]) == (404, "tercero_no_encontrado")
    with pytest.raises(HTTPException) as exc2:
        await _conciliar(conn, slug, uuid.uuid4(), "descartar", motivo="no existe, motivo largo", clearance=1)
    assert exc2.value.status_code == 404, "invisible e inexistente son indistinguibles"
    assert await conn.fetchval("SELECT conciliacion FROM tercero WHERE id=$1", reservada) == "pendiente"

    await _conciliar(conn, slug, reservada, "vincular", destino_id=real, clearance=3)  # control positivo
    assert await conn.fetchval("SELECT conciliacion FROM tercero WHERE id=$1", reservada) == "vinculada"


@pytest.mark.asyncio
async def test_el_update_lleva_su_propio_gate_de_clearance(tenant_ctx) -> None:
    """El `UPDATE` no se apoya en el bloqueo previo: sin alcance, no afecta filas."""
    slug, conn = await _entorno(tenant_ctx)
    reservada = await _anonimo(conn, slug, asunto="Reservada UPDATE", nivel=3)
    repo = TerceroRepository(conn, slug)
    motivo = "motivo suficientemente largo"

    assert await repo.conciliar_descartar(reservada, actor=ACTOR, motivo=motivo, max_clearance=1) is None
    assert await repo.conciliar_confirmar(
        reservada, actor=ACTOR, motivo=None, medio="presencial_documento", max_clearance=1
    ) is None
    assert await repo.conciliar_vincular(
        reservada, uuid.uuid4(), actor=ACTOR, motivo=None, medio="presencial_documento",
        max_clearance=1,
    ) is None
    assert await conn.fetchval("SELECT conciliacion FROM tercero WHERE id=$1", reservada) == "pendiente"
    # Control positivo: con alcance, el mismo UPDATE sí actúa.
    assert await repo.conciliar_descartar(reservada, actor=ACTOR, motivo=motivo, max_clearance=3) is not None


# ------------------------------------------- medio de verificación (archivística #1)


@pytest.mark.asyncio
async def test_vincular_y_confirmar_exigen_el_medio_de_verificacion(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    real = await _existente(conn)
    a = await _anonimo(conn, slug, asunto="Sin medio A", documento="31")
    b = await _anonimo(conn, slug, asunto="Sin medio B", documento="32")
    for accion, ficha, kw in (("vincular", a, {"destino_id": real}), ("confirmar", b, {})):
        for medio in (None, "adivinanza"):
            with pytest.raises(HTTPException) as exc:
                await _conciliar(conn, slug, ficha, accion, medio_verificacion=medio, **kw)
            assert exc.value.detail["error"] == "medio_verificacion_requerido"
    # `otro` no se explica solo.
    with pytest.raises(HTTPException) as exc:
        await _conciliar(conn, slug, b, "confirmar", medio_verificacion="otro")
    assert exc.value.detail["error"] == "motivo_requerido"
    # Descartar no verifica nada: no lleva medio.
    with pytest.raises(HTTPException) as exc:
        await _conciliar(
            conn, slug, a, "descartar", motivo="motivo largo bastante",
            medio_verificacion="presencial_documento",
        )
    assert exc.value.detail["error"] == "medio_verificacion_no_aplica"
    assert await conn.fetchval("SELECT count(*) FROM tercero WHERE conciliacion='pendiente'") == 2
    # Control positivo: con `otro` + motivo pasa y queda en la columna y en el asiento.
    await _conciliar(
        conn, slug, b, "confirmar", medio_verificacion="otro", motivo="Oficio del juzgado 12"
    )
    assert await conn.fetchval("SELECT conciliacion_medio FROM tercero WHERE id=$1", b) == "otro"
    (asiento,) = await _asientos(conn, slug, "document.tercero_conciliado")
    assert asiento["payload"]["medio_verificacion"] == "otro"


@pytest.mark.asyncio
async def test_la_base_exige_el_medio_al_vincular_o_confirmar(tenant_ctx) -> None:
    slug, conn = await _entorno(tenant_ctx)
    p = await _anonimo(conn, slug, asunto="Medio en la base")
    with pytest.raises(asyncpg.PostgresError):  # confirmar sin medio
        await conn.execute(
            "UPDATE tercero SET conciliacion='confirmada', identidad='confirmada', "
            "tipo_documento=documento_declarado_tipo, numero_documento=documento_declarado_numero,"
            " conciliada_at=now(), conciliada_por=$2 WHERE id=$1",
            p,
            ACTOR,
        )
    with pytest.raises(asyncpg.PostgresError):  # medio fuera del enum
        await conn.execute(
            "UPDATE tercero SET conciliacion_medio='adivinanza' WHERE id=$1", p
        )
