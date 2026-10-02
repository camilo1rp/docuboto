# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Fusionar duplicados NO reescribe el pasado: lo resuelve al leer.

Contra Postgres REAL, y aquí no es una preferencia: **la base es quien impone la
regla**. El primer diseño de esta función repuntaba `radicado_actor.tercero_id`
del absorbido al superviviente, y el trigger de inmutabilidad de la migración
026 lo rechazó —incluye `tercero_id` entre las columnas que no se editan— con
este mensaje:

    «los datos de contacto de un actor son los USADOS en el envío y no se
     editan: cambiarlos daría información errónea sobre un acto ya ocurrido»

Y tiene razón: a QUÉ ficha del catálogo se atendió a alguien también es parte de
lo que ocurrió. Ese día existían dos fichas y se usó una. Un mock habría dejado
pasar el diseño equivocado sin decir nada.

Lo que estos tests fijan es el diseño que quedó: la fusión **añade** lo que se
supo después (son la misma persona) y quien lee resuelve a través del puntero.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema

pytestmark = pytest.mark.integration


async def _tercero(conn: asyncpg.Connection, nombre: str) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO tercero (nombre, tipo) VALUES ($1, 'ciudadano') RETURNING id", nombre
    )


async def _tercero_con_documento(
    conn: asyncpg.Connection, nombre: str, documento: str
) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO tercero (nombre, tipo, tipo_documento, numero_documento) "
        "VALUES ($1, 'ciudadano', 'CC', $2) RETURNING id",
        nombre,
        documento,
    )


async def _radicar_con(
    conn: asyncpg.Connection, slug: str, tercero_id: uuid.UUID, asunto: str
) -> uuid.UUID:
    from app.schemas.document import DocumentCreate
    from app.services.document_service import DocumentService

    doc = await DocumentService(conn, slug).create(
        DocumentCreate(
            doc_type="E",
            subject=asunto,
            sender_name="quien sea",
            actores=[{"tercero_id": tercero_id, "rol": "remitente", "principal": True}],
        ),
        registered_by=None,
    )
    return doc.id


@pytest.mark.asyncio
async def test_la_fusion_no_toca_ni_una_fila_de_radicado_actor(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El vínculo sigue nombrando la ficha que se usó ESE día.

    Es la aserción que la base enseñó: reescribirlo diría que siempre hubo una
    sola ficha, cuando el día del envío había dos.
    """
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from app.services.tercero_service import TerceroService

    vive = await _tercero(conn, "José Pérez Gómez")
    muere = await _tercero(conn, "JOSE PEREZ GOMEZ")
    await _radicar_con(conn, slug, muere, "Escrito del duplicado")

    await TerceroService(conn, slug).fusionar(vive, muere, actor=None)

    apunta_a = await conn.fetchval(
        "SELECT tercero_id FROM radicado_actor LIMIT 1"
    )
    assert apunta_a == muere, (
        "el vínculo tiene que seguir nombrando la ficha usada ese día; "
        "repuntarlo lo prohíbe el trigger de la migración 026, y con razón"
    )
    assert await conn.fetchval(
        "SELECT fusionado_en FROM tercero WHERE id = $1", muere
    ) == vive


@pytest.mark.asyncio
async def test_el_superviviente_HEREDA_los_radicados_al_leer(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Sin esto, fusionar haría DESAPARECER los radicados de la absorbida.

    Es el modo de fallo silencioso de esta función: la consolidación se vería
    correcta —un duplicado menos— y el historial de esa persona quedaría partido
    en dos, con una mitad inalcanzable.
    """
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from app.services.tercero_service import TerceroService

    vive = await _tercero(conn, "Ana Ríos")
    muere = await _tercero(conn, "ANA RIOS")
    await _radicar_con(conn, slug, vive, "Escrito de la que vive")
    await _radicar_con(conn, slug, muere, "Escrito de la que muere")

    servicio = TerceroService(conn, slug)
    resultado = await servicio.fusionar(vive, muere, actor=None)
    assert resultado["vinculos_heredados"] == 1

    asuntos = {r["subject"] for r in await servicio.radicados_de(vive, max_clearance=3)}
    assert asuntos == {"Escrito de la que vive", "Escrito de la que muere"}, (
        "el superviviente tiene que ver AMBOS historiales; si no, fusionar "
        "esconde documentos"
    )


@pytest.mark.asyncio
async def test_una_ficha_fusionada_desaparece_del_buscador(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """O el duplicado que alguien acaba de resolver reaparecería al radicar."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from app.services.tercero_service import TerceroService

    vive = await _tercero(conn, "Luis Mora")
    muere = await _tercero(conn, "LUIS MORA")
    servicio = TerceroService(conn, slug)
    await servicio.fusionar(vive, muere, actor=None)

    encontrados = [t["nombre"] for t in await servicio.buscar("luis mora", tipo=None, limit=20)]
    assert encontrados == ["Luis Mora"]


@pytest.mark.asyncio
async def test_no_se_encadenan_fusiones(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Fusionar contra una ficha ya fusionada da 409, no una cadena.

    Una cadena haría que el destino de un vínculo dependiera de recorrerla, y
    una cadena con un ciclo colgaría al lector.
    """
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from fastapi import HTTPException

    from app.services.tercero_service import TerceroService

    a = await _tercero(conn, "Uno")
    b = await _tercero(conn, "Dos")
    c = await _tercero(conn, "Tres")
    servicio = TerceroService(conn, slug)
    await servicio.fusionar(a, b, actor=None)

    with pytest.raises(HTTPException) as exc:
        await servicio.fusionar(b, c, actor=None)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "tercero_ya_fusionado"


@pytest.mark.asyncio
async def test_los_duplicados_se_detectan_sin_tildes_y_sin_mayusculas(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """La detección tiene que ver lo que un humano ve: la misma persona.

    Y no puede seguir viéndolos después de consolidarlos, o la lista de
    pendientes nunca bajaría.
    """
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from app.services.tercero_service import TerceroService

    vive = await _tercero(conn, "María Ángel")
    muere = await _tercero(conn, "MARIA ANGEL")
    await _tercero(conn, "Otra Persona")

    servicio = TerceroService(conn, slug)
    grupos = await servicio.grupos_duplicados()
    assert len(grupos) == 1, grupos
    assert grupos[0]["eje"] == "nombre"
    assert {f["nombre"] for f in grupos[0]["fichas"]} == {"María Ángel", "MARIA ANGEL"}

    await servicio.fusionar(vive, muere, actor=None)
    assert await servicio.grupos_duplicados() == []


@pytest.mark.asyncio
async def test_dos_documentos_DISTINTOS_no_son_un_duplicado(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Dos homónimos con documento distinto son dos personas, y no se fusionan.

    El alta es idempotente **por documento**: dos números distintos nacieron de
    dos altas legítimamente distintas. La pantalla ya decía que coincidir en
    documento es certeza de que son la misma persona; diferir en documento es la
    certeza contraria, y esa mitad no se estaba usando en el único punto donde
    el acto no tiene vuelta atrás.
    """
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from fastapi import HTTPException

    from app.services.tercero_service import TerceroService

    a = await _tercero_con_documento(conn, "Juan Ruiz", "111")
    b = await _tercero_con_documento(conn, "Juan Ruiz", "222")

    with pytest.raises(HTTPException) as exc:
        await TerceroService(conn, slug).fusionar(a, b, actor=None)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "documentos_distintos"
    # El mensaje nombra los DOS números: quien lo lee tiene que poder ver cuál
    # es el mal capturado sin ir a buscarlo a otra pantalla.
    assert "111" in exc.value.detail["detail"]
    assert "222" in exc.value.detail["detail"]
    # Y no dejó nada a medias.
    assert await conn.fetchval("SELECT fusionado_en FROM tercero WHERE id = $1", b) is None


# **No hay test de «mismo documento se fusiona», y no es un olvido.** Se escribió,
# y falló al construir el escenario: `ux_tercero_documento` es UNIQUE sobre
# (tipo_documento, numero_documento), así que dos fichas vivas NO PUEDEN
# compartir documento. Eso refuerza la guarda en vez de debilitarla —si las dos
# traen número, la base ya garantiza que son distintos, luego son dos personas—
# y hace del test de abajo el control positivo real: el que demuestra que la
# fusión sigue siendo posible en el caso por el que existe.


@pytest.mark.asyncio
async def test_si_solo_UNA_tiene_documento_la_fusion_sigue_siendo_posible(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El caso más común del catálogo real, y el que la guarda NO debe cerrar.

    Media entrada de ventanilla llega sin documento de identidad. Una ficha con
    número y otra sin él no son evidencia de nada: no hay contradicción que
    detectar, solo un dato que falta. Exigir el documento en las dos habría
    convertido la guarda en «no se puede fusionar casi nunca».
    """
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from app.services.tercero_service import TerceroService

    a = await _tercero_con_documento(conn, "Luis Paz", "777")
    b = await _tercero(conn, "Luis Paz")

    await TerceroService(conn, slug).fusionar(a, b, actor=None)
    assert await conn.fetchval("SELECT fusionado_en FROM tercero WHERE id = $1", b) == a
