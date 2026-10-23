# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El acta de entrega dice QUIÉN elaboró, entregó y recibió — y sigue leyéndose
si el catálogo de usuarios no está.

La ficha del acta enseñaba a sus tres responsables como
`50b64803-568d-49f7-9299-cb140279d947`: séptima aparición del mismo defecto.
El acta CONGELA el identificador y así debe seguir —es probatoria y no se
toca—, así que el nombre se resuelve al leer, con `LEFT JOIN` por TEXTO.

**La otra mitad, que es la que se prueba aquí.** `auth_users` la crea
auth-service, no este servicio: en el esquema de una entidad real está —todas
las migraciones aterrizan en el mismo schema— pero un despliegue parcial no la
tiene, y un esquema de pruebas la crea con lo MÍNIMO que necesita el clearance,
sin `username`. Si la lectura del acta muriera por eso, se habría cambiado un
dato ausente por un error sobre un documento probatorio — al revés de lo que
hay que hacer. Por eso la guarda mira la COLUMNA y no la tabla: mirar la tabla
es comprobar la mitad y fallar igual.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.transferencia_repository import TransferenciaRepository

pytestmark = pytest.mark.asyncio

_ACTA = {
    "acta_fuid_xml": "<fuid/>",
    "acta_fuid_sha256": "0" * 64,
}


async def _sp(conn: asyncpg.Connection, slug: str) -> None:
    """El repositorio pone el `search_path` por su cuenta (`_sp`); el SQL crudo
    de este test, no."""
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')


async def _crear_auth_users(conn: asyncpg.Connection, slug: str, columnas: str) -> None:
    """Crea el catálogo de usuarios **con el esquema escrito en la sentencia**.

    Un `CREATE TABLE` sin cualificar depende del `search_path`, y si el primer
    esquema de la lista no está donde se cree, la tabla aterriza en `public` —
    que es el único esquema COMPARTIDO por todas las entidades—. Escribiéndolo
    así no hay ninguna suposición que pueda fallar en silencio y contaminar los
    demás tests (pasó: dejó un `public.auth_users` sin `is_root` que tumbó ocho
    pruebas de otros ficheros, y el fallo apareció lejos de aquí).
    """
    await conn.execute(
        f'CREATE TABLE IF NOT EXISTS "{tenant_schema(slug)}".auth_users ({columnas})'
    )


async def _acta_de_prueba(conn: asyncpg.Connection, slug: str, responsable: str):
    await _sp(conn, slug)
    repo = TransferenciaRepository(conn, slug)
    transferencia_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO transferencias (id, tipo, estado, expediente_id, objeto)
             VALUES ($1, 'primaria', 'recibida', $2, 'Transferencia de prueba')
        """,
        transferencia_id,
        uuid.uuid4(),
    )
    await repo.insert_acta(
        {
            "transferencia_id": transferencia_id,
            **_ACTA,
            "elaborado_por": responsable,
            "entregado_por": responsable,
            "recibido_por": responsable,
            "recibido_at": datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
        }
    )
    return repo, transferencia_id


async def test_los_tres_responsables_salen_con_su_nombre(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    user_id = uuid.uuid4()
    await _crear_auth_users(conn, slug, "id UUID PRIMARY KEY, username TEXT")
    await _sp(conn, slug)
    await conn.execute(
        "INSERT INTO auth_users (id, username) VALUES ($1, 'ARCHIVISTA')", user_id
    )

    repo, transferencia_id = await _acta_de_prueba(conn, slug, str(user_id))
    acta = await repo.get_acta(transferencia_id)

    assert acta is not None
    assert acta["elaborado_por"] == str(user_id), "el identificador congelado no se toca"
    assert acta["elaborado_por_nombre"] == "ARCHIVISTA"
    assert acta["entregado_por_nombre"] == "ARCHIVISTA"
    assert acta["recibido_por_nombre"] == "ARCHIVISTA"


async def test_un_responsable_que_no_es_usuario_no_rompe_la_lectura(tenant_ctx) -> None:
    """Actas migradas y actos del sistema: la unión es por la IZQUIERDA y por
    TEXTO a propósito — un `::uuid` haría fallar la consulta entera por una
    fila que ni se estaba preguntando."""
    slug, conn = tenant_ctx
    await _crear_auth_users(conn, slug, "id UUID PRIMARY KEY, username TEXT")

    repo, transferencia_id = await _acta_de_prueba(conn, slug, "migracion-2019")
    acta = await repo.get_acta(transferencia_id)

    assert acta is not None
    assert acta["elaborado_por"] == "migracion-2019"
    assert acta["elaborado_por_nombre"] is None


async def test_sin_catalogo_de_usuarios_el_acta_se_sigue_leyendo(tenant_ctx) -> None:
    """El esquema de este tier NO tiene `auth_users`: es el caso del despliegue
    parcial. El acta es el documento probatorio; el nombre, una cortesía."""
    slug, conn = tenant_ctx
    responsable = str(uuid.uuid4())

    repo, transferencia_id = await _acta_de_prueba(conn, slug, responsable)
    acta = await repo.get_acta(transferencia_id)

    assert acta is not None, "la falta de una tabla de OTRO servicio tumbó el acta"
    assert acta["acta_fuid_sha256"] == _ACTA["acta_fuid_sha256"]
    assert acta["elaborado_por"] == responsable


async def test_auth_users_sin_columna_username_tampoco_rompe(tenant_ctx) -> None:
    """El caso exacto que cazó este fallo: la tabla EXISTE con otra forma. Una
    guarda que mirara `to_regclass('auth_users')` daría verde aquí y la
    consulta moriría igual en `ue.username`."""
    slug, conn = tenant_ctx
    responsable = str(uuid.uuid4())
    await _crear_auth_users(conn, slug, "id UUID PRIMARY KEY, is_root BOOLEAN")

    repo, transferencia_id = await _acta_de_prueba(conn, slug, responsable)
    acta = await repo.get_acta(transferencia_id)

    assert acta is not None
    assert acta["elaborado_por"] == responsable
