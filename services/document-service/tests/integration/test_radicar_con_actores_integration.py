# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Radicar con actores los PERSISTE, y en el mismo acto que el radicado.

Contra Postgres REAL, y la elección importa: con mocks, «se llamó a
`vincular_actores`» y «hay filas en `radicado_actor`» son indistinguibles. Esa
confusión es exactamente la que dejó la tabla **vacía sobre 182 radicados**
mientras el catálogo tenía 40 terceros, con un comentario en la pantalla de
radicar afirmando que los destinatarios «viven en su propia tabla, cada uno con
su contacto congelado».

Tres cosas que solo se pueden comprobar aquí:

1. **Que las filas existen.** Es la afirmación que nadie hacía.
2. **Que una Salida con VARIOS destinatarios los conserva todos.** El
   `CLAUDE.md` lo declara como regla de negocio —«a salida document can have
   multiple recipients»— y hasta ahora la pantalla aplanaba al primero en
   `sender_name` y perdía el resto en silencio.
3. **Que la vinculación es ATÓMICA con la radicación.** Un `tercero_id`
   inexistente tiene que dejar CERO radicados, no un radicado huérfano de
   actores. Se comprueba contando radicados después del fallo: si la
   vinculación viviera fuera de la transacción, el radicado quedaría — con su
   número consumido — y este test lo vería.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema

pytestmark = pytest.mark.integration


async def _tercero(conn: asyncpg.Connection, nombre: str, **campos: str) -> uuid.UUID:
    """Un tercero del catálogo, por SQL: aquí el sujeto es lo que se radica."""
    return await conn.fetchval(
        "INSERT INTO tercero (nombre, entidad, email, direccion, tipo) "
        "VALUES ($1, $2, $3, $4, 'ciudadano') RETURNING id",
        nombre,
        campos.get("entidad"),
        campos.get("email"),
        campos.get("direccion"),
    )


@pytest.mark.asyncio
async def test_radicar_una_entrada_congela_su_remitente(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El caso que no existía: el remitente de una Entrada como ENTIDAD.

    Hasta ahora era texto libre en dos columnas del radicado, así que el
    directorio solo podía aprender de las Salidas — y de esas tampoco, porque
    el vínculo no se persistía. De ahí los duplicados.
    """
    slug, conn = tenant_ctx
    # El fixture aplica las migraciones con `SET LOCAL`, así que no deja el
    # `search_path` puesto: cada test lo fija, igual que los demás de este tier.
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from app.schemas.document import DocumentCreate
    from app.services.document_service import DocumentService

    tercero_id = await _tercero(
        conn, "José Pérez", email="jose@ejemplo.co", direccion="Calle 1 #2-3"
    )

    servicio = DocumentService(conn, slug)
    creado = await servicio.create(
        DocumentCreate(
            doc_type="E",
            doc_class="Petición",
            dest_dept_code=1,
            subject="Solicitud de certificación",
            sender_name="José Pérez",
            actores=[{"tercero_id": tercero_id, "rol": "remitente", "principal": True}],
        ),
        registered_by=None,
    )

    filas = await conn.fetch(
        "SELECT rol, principal, nombre, email, direccion, tercero_id "
        "FROM radicado_actor WHERE radicado_id = $1",
        creado.id,
    )
    assert len(filas) == 1, "el remitente no quedó persistido"
    (fila,) = filas
    assert fila["rol"] == "remitente"
    assert fila["principal"] is True
    assert fila["tercero_id"] == tercero_id
    # La COPIA CONGELADA se rellenó desde la ficha vigente: es lo que dice a
    # quién se atendió y con qué datos, y no una clave foránea que cambiaría
    # sola el día que esa persona se mude.
    assert fila["nombre"] == "José Pérez"
    assert fila["email"] == "jose@ejemplo.co"
    assert fila["direccion"] == "Calle 1 #2-3"


@pytest.mark.asyncio
async def test_una_salida_con_tres_destinatarios_conserva_los_tres(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """La regla de negocio del `CLAUDE.md`, que hasta ahora se perdía callada.

    Comprobar solo que hay «más de uno» no bastaría: el defecto era quedarse
    con el PRIMERO, así que el test fija los tres nombres y su orden.
    """
    slug, conn = tenant_ctx
    # El fixture aplica las migraciones con `SET LOCAL`, así que no deja el
    # `search_path` puesto: cada test lo fija, igual que los demás de este tier.
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from app.schemas.document import DocumentCreate
    from app.services.document_service import DocumentService

    ids = [
        await _tercero(conn, "Ana Gómez"),
        await _tercero(conn, "Luis Ríos"),
        await _tercero(conn, "Marta Díaz"),
    ]

    creado = await DocumentService(conn, slug).create(
        DocumentCreate(
            doc_type="S",
            doc_class="Petición",
            origin_dept_code=1,
            subject="Respuesta a varios",
            sender_name="Ana Gómez",
            actores=[
                {"tercero_id": ids[0], "rol": "destinatario", "principal": True},
                {"tercero_id": ids[1], "rol": "destinatario"},
                {"tercero_id": ids[2], "rol": "destinatario"},
            ],
        ),
        registered_by=None,
    )

    nombres = [
        r["nombre"]
        for r in await conn.fetch(
            "SELECT nombre FROM radicado_actor WHERE radicado_id = $1 ORDER BY orden",
            creado.id,
        )
    ]
    assert nombres == ["Ana Gómez", "Luis Ríos", "Marta Díaz"], (
        "una Salida con varios destinatarios tiene que conservarlos todos; "
        "quedarse con el primero es el defecto que este test cierra"
    )


@pytest.mark.asyncio
async def test_un_tercero_inexistente_no_deja_radicado_a_medias(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Atomicidad: o el radicado con sus actores, o nada.

    Es la aserción que distingue «se vincula dentro de la transacción» de «se
    vincula después». Si la vinculación viviera fuera, el radicado quedaría
    creado —con su número de radicado ya consumido— y sin remitente, que es
    justo el medio-estado que no puede existir en un registro legal.
    """
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from fastapi import HTTPException

    from app.schemas.document import DocumentCreate
    from app.services.document_service import DocumentService

    antes = await conn.fetchval("SELECT count(*) FROM radicados")

    with pytest.raises(HTTPException) as exc:
        await DocumentService(conn, slug).create(
            DocumentCreate(
                doc_type="E",
                doc_class="Petición",
                dest_dept_code=1,
                subject="Con un tercero que no existe",
                sender_name="Fantasma",
                actores=[{"tercero_id": uuid.uuid4(), "rol": "remitente"}],
            ),
            registered_by=None,
        )
    assert exc.value.status_code == 404

    despues = await conn.fetchval("SELECT count(*) FROM radicados")
    assert despues == antes, (
        "la radicación tiene que revertirse entera: un radicado sin sus actores "
        "consume un número y no dice de quién es"
    )
