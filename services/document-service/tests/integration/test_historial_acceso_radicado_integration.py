# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Quién leyó y quién descargó un radicado — y quién no puede ni preguntarlo.

La traza existía desde siempre (`document.radicado_consultado`,
`document.anexo_descargado`, con actor, canal e instante) y **no había forma de
pedirla**: solo desde `/admin/auditoria` —que exige `USUA_PERM_ADMIN` y saber
por qué acción filtrar— o, si el radicado estaba en un expediente, desde el
historial de ESE expediente. Un radicado suelto, que son la mayoría, no tenía
respuesta. Otra vez el dato guardado que nadie podía leer.

Contra Postgres real porque lo que se afirma es qué filas de `public.audit_log`
salen y cuáles no — y sobre todo, que **el historial no es un oráculo**: quien
no puede ver el radicado tampoco puede enterarse por aquí de que existe.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema

pytestmark = pytest.mark.integration


async def _radicar(
    conn: asyncpg.Connection, slug: str, *, nivel: int = 1, por: uuid.UUID | None = None
) -> tuple:
    """Radica. `por` hace falta para los niveles ≥ 2: hay **no-write-up** — nadie
    puede originar un documento por encima de su propio clearance."""
    from app.schemas.document import DocumentCreate
    from app.services.document_service import DocumentService

    doc = await DocumentService(conn, slug).create(
        DocumentCreate(
            doc_type="E",
            subject="Documento con historial",
            sender_name="Quien sea",
            nivel_seguridad=nivel,
            clasificacion_fundamento="Reserva de prueba" if nivel >= 2 else None,
        ),
        registered_by=por,
    )
    return doc.id, doc.tracking_number


async def _tablas_rbac(conn: asyncpg.Connection) -> None:
    """Las tablas de auth-service que la resolución de clearance lee.

    Las crea auth-service; el fixture de este tier solo aplica las migraciones de
    document-service. Se declaran aquí con la forma MÍNIMA que usa
    `resolve_caller_clearance` —`auth_users`, `auth_memberships`,
    `role_clearance`— en vez de importar migraciones ajenas, que ataría este test
    a los cambios de otro servicio. Lo que se prueba aquí no es su esquema.

    Ojo: sin ellas `resolve_caller_clearance` **no falla**, devuelve PUBLICA
    (fail-closed, atrapa `UndefinedTableError`). Es correcto, y también es la
    razón por la que este test tiene que crearlas: sin ellas todos los usuarios
    serían nivel 1 y el caso del oráculo pasaría por el motivo equivocado.
    """
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS auth_users ("
        "  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), username text, email text,"
        "  is_root boolean NOT NULL DEFAULT false)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS auth_groups ("
        "  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS auth_memberships (user_id uuid, group_id uuid)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS role_clearance (group_id uuid PRIMARY KEY, max_level int)"
    )


async def _clearance(
    conn: asyncpg.Connection, nivel: int, *, username: str | None = None
) -> uuid.UUID:
    """Un usuario con el clearance pedido, por SQL: aquí el sujeto es el acceso."""
    await _tablas_rbac(conn)
    user_id = await conn.fetchval(
        "INSERT INTO auth_users (username, email) VALUES ($1, $2) RETURNING id",
        username or f"u{nivel}{uuid.uuid4().hex[:6]}",
        f"u{uuid.uuid4().hex[:6]}@ejemplo.test",
    )
    group_id = await conn.fetchval(
        "INSERT INTO auth_groups (name) VALUES ($1) RETURNING id", f"g{uuid.uuid4().hex[:8]}"
    )
    await conn.execute(
        "INSERT INTO role_clearance (group_id, max_level) VALUES ($1, $2)", group_id, nivel
    )
    await conn.execute(
        "INSERT INTO auth_memberships (user_id, group_id) VALUES ($1, $2)", user_id, group_id
    )
    return user_id


@pytest.mark.asyncio
async def test_leer_un_radicado_aparece_en_su_historial(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El caso base, que hasta ahora no se podía preguntar."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from app.services.document_service import DocumentService

    doc_id, _ = await _radicar(conn, slug)
    lector = await _clearance(conn, 3)
    servicio = DocumentService(conn, slug)

    # `canal="ui"`: la traza se escribe solo para los canales de persona, no
    # para las lecturas internas del sistema — si no, el historial sería ruido.
    await servicio.get_by_id(doc_id, 3, user_id=lector, canal="ui")

    entradas, total = await servicio.historial_acceso(
        doc_id, user_id=lector, page=1, size=20
    )
    assert total == 1
    assert entradas[0].action == "document.radicado_consultado"
    assert entradas[0].actor == str(lector)
    assert entradas[0].canal == "ui"


@pytest.mark.asyncio
async def test_el_historial_NO_es_un_oraculo_sobre_lo_reservado(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """La aserción que de verdad importa.

    Sin el no-read-up ANTES de tocar `audit_log`, quien no puede ver un radicado
    RESERVADO se enteraría igualmente de que existe y de cuánta gente lo ha
    mirado — que en un documento reservado es justo lo que hay que ocultar. Y el
    404 tiene que ser el MISMO que el de un radicado inexistente: si fuera un
    403, negar ya sería confirmar.
    """
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from app.services.document_service import DocumentNotFoundError, DocumentService

    archivista = await _clearance(conn, 3)  # puede originar RESERVADA
    doc_id, _ = await _radicar(conn, slug, nivel=2, por=archivista)
    curioso = await _clearance(conn, 1)  # solo Pública
    servicio = DocumentService(conn, slug)

    with pytest.raises(DocumentNotFoundError):
        await servicio.historial_acceso(doc_id, user_id=curioso, page=1, size=20)

    # Y exactamente lo mismo con un identificador que no existe: las dos
    # respuestas son indistinguibles, que es la propiedad buscada.
    with pytest.raises(DocumentNotFoundError):
        await servicio.historial_acceso(uuid.uuid4(), user_id=curioso, page=1, size=20)


@pytest.mark.asyncio
async def test_el_historial_solo_trae_ACCESOS_no_todo_lo_ocurrido(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Crear no es leer.

    El radicado nace con su asiento `document.radicado_created`, que es un acto
    SOBRE el documento y no una lectura DE él. Mezclarlos convertiría esto en un
    historial de todo, que responde otra pregunta — y enterraría los accesos,
    que es lo que alguien viene a buscar aquí.
    """
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from app.services.document_service import DocumentService

    doc_id, tracking = await _radicar(conn, slug)
    lector = await _clearance(conn, 3)
    servicio = DocumentService(conn, slug)

    # El asiento de creación existe...
    creados = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log "
        "WHERE object_ref = $1 AND action = 'document.radicado_created'",
        tracking,
    )
    assert creados == 1

    # ...y el historial de ACCESO no lo incluye.
    _, total = await servicio.historial_acceso(doc_id, user_id=lector, page=1, size=20)
    assert total == 0, "crear un radicado no es acceder a él"


@pytest.mark.asyncio
async def test_el_historial_dice_un_NOMBRE_no_un_identificador(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """«Quién ha visto este radicado» tiene que responderse con un quién.

    El asiento guarda el identificador interno, y así debe seguir: un `username`
    cambia y un asiento no se toca nunca. Pero la pantalla enseñaba ese UUID en
    crudo, de modo que la sección construida para responder la pregunta no la
    respondía — el dato estaba guardado y nadie podía leerlo.
    """
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from app.services.document_service import DocumentService

    doc_id, _ = await _radicar(conn, slug)
    lector = await _clearance(conn, 3, username="ARCHIVISTA_DE_TURNO")
    servicio = DocumentService(conn, slug)
    await servicio.get_by_id(doc_id, 3, user_id=lector, canal="ui")

    entradas, _ = await servicio.historial_acceso(doc_id, user_id=lector, page=1, size=20)
    assert entradas[0].actor_nombre == "ARCHIVISTA_DE_TURNO"
    # El identificador sigue viajando: es lo que permite cruzar este historial
    # con `/admin/auditoria`, donde un nombre repetido no serviría.
    assert entradas[0].actor == str(lector)


@pytest.mark.asyncio
async def test_un_actor_que_no_es_un_usuario_no_rompe_el_historial(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Control negativo del JOIN: sin nombre se cae al identificador, y ya.

    `actor` no siempre es un usuario del catálogo —hay asientos escritos por el
    propio sistema— y castear a `uuid` habría hecho fallar la consulta ENTERA
    por una fila que ni siquiera se estaba preguntando. Con `LEFT JOIN` por
    texto, la fila aparece con el nombre vacío: decir menos es aceptable,
    perder el historial no.
    """
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from orpycamcp_common import audit

    from app.services.document_service import DocumentService

    doc_id, tracking = await _radicar(conn, slug)
    lector = await _clearance(conn, 3)
    await audit.append(
        conn,
        tenant_slug=slug,
        service="document-service",
        action="document.radicado_consultado",
        object_type="radicado",
        object_ref=tracking,
        actor="proceso-de-migracion",
        canal="api",
    )

    entradas, total = await DocumentService(conn, slug).historial_acceso(
        doc_id, user_id=lector, page=1, size=20
    )
    assert total == 1
    assert entradas[0].actor == "proceso-de-migracion"
    assert entradas[0].actor_nombre is None
