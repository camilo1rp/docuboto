# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
El contexto congelado, contra Postgres real (E23 Inc.3, T-12).

Contra mocks esto no se puede probar: quien decide qué devuelve la columna es el
mock, así que la afirmación «cambiar la plantilla no reescribe lo producido»
sería una creencia sobre el código, no una comprobación. Aquí se escribe de
verdad, se cambia la plantilla de verdad, y se vuelve a leer.
"""

from __future__ import annotations

import json
import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.borrador_repository import BorradorRepository

pytestmark = pytest.mark.asyncio


async def _preparar(conn: asyncpg.Connection, slug: str) -> BorradorRepository:
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
    return BorradorRepository(conn)


async def test_la_plantilla_que_cambia_no_reescribe_lo_producido(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo = await _preparar(conn, slug)

    plantilla = await repo.insert_plantilla(
        {
            "nombre": "Oficio",
            "cuerpo": "Señor *NOM_P*, atentamente *NOM_E*.",
            "tipo_documental": "Oficio",
            "activo": True,
        }
    )
    borrador = await repo.insert_borrador(
        {
            "doc_type": "S",
            "subject": "Respuesta",
            "dest_dept": "Jurídica",
            "cuerpo": "Señor Ana Ruiz, atentamente Instituto de Entonces.",
            "plantilla_id": plantilla["id"],
            "radicado_padre_id": None,
            "creado_por": None,
        }
    )

    congelado = {"NOM_P": "Ana Ruiz", "NOM_E": "Instituto de Entonces"}
    file_id = uuid.uuid4()
    await repo.set_documento(
        borrador["id"],
        file_id=file_id,
        sha256="b" * 64,
        formato="PDF/A-2B",
        combinacion=congelado,
    )

    # La entidad se renombra y la plantilla se reescribe entera.
    await conn.execute(
        "UPDATE plantilla SET cuerpo = 'Otro texto completamente distinto' WHERE id = $1",
        plantilla["id"],
    )

    fila = await repo.get_borrador(borrador["id"])
    assert fila is not None
    # Ni el documento producido ni el contexto con que se produjo se mueven.
    assert fila["documento_file_id"] == file_id
    assert fila["documento_sha256"] == "b" * 64
    assert fila["documento_formato"] == "PDF/A-2B"
    guardado = json.loads(fila["combinacion"]) if isinstance(fila["combinacion"], str) else fila["combinacion"]
    assert guardado == congelado
    assert "Instituto de Entonces" in fila["cuerpo"]


async def test_la_combinacion_se_guarda_como_jsonb_consultable(tenant_ctx) -> None:
    """No basta con que «se guarde»: tiene que llegar como JSONB.

    `asyncpg` no infiere el tipo de un parámetro jsonb desde un dict de Python.
    Sin el `::jsonb` explícito la columna recibiría texto y el operador `->>`
    fallaría — un fallo que solo aparecería el día que alguien consultara el
    contexto congelado, que es exactamente el día en que hace falta.
    """
    slug, conn = tenant_ctx
    repo = await _preparar(conn, slug)
    borrador = await repo.insert_borrador(
        {
            "doc_type": "S",
            "subject": "X",
            "dest_dept": None,
            "cuerpo": "x",
            "plantilla_id": None,
            "radicado_padre_id": None,
            "creado_por": None,
        }
    )
    await repo.set_documento(
        borrador["id"],
        file_id=uuid.uuid4(),
        sha256="c" * 64,
        formato="PDF/A-2B",
        combinacion={"NOM_E": "Instituto", "NRO_PAGS": 3},
    )
    valor = await conn.fetchval(
        "SELECT combinacion->>'NOM_E' FROM borrador WHERE id = $1", borrador["id"]
    )
    assert valor == "Instituto"


async def test_la_huella_del_documento_es_la_que_firmaria_una_cadena(tenant_ctx) -> None:
    """RF-PRO-09: lo que se firma es el documento, no su nombre.

    `documento_sha256` es la huella de los bytes que produjo storage-service.
    Es lo que un visto bueno afirmará haber leído y lo que una firma
    comprometerá (Inc.4). Aquí solo se comprueba que existe y persiste: sin
    ella, la cadena de firma volvería a firmar la cadena `borrador:<uuid>` —el
    nombre del objeto— y la firma sobreviviría al cambio del texto.
    """
    slug, conn = tenant_ctx
    repo = await _preparar(conn, slug)
    borrador = await repo.insert_borrador(
        {
            "doc_type": "S",
            "subject": "X",
            "dest_dept": None,
            "cuerpo": "x",
            "plantilla_id": None,
            "radicado_padre_id": None,
            "creado_por": None,
        }
    )
    assert (await repo.get_borrador(borrador["id"]))["documento_sha256"] is None

    await repo.set_documento(
        borrador["id"],
        file_id=uuid.uuid4(),
        sha256="d" * 64,
        formato="PDF/A-2B",
        combinacion={},
    )
    fila = await repo.get_borrador(borrador["id"])
    assert fila["documento_sha256"] == "d" * 64
    assert len(fila["documento_sha256"]) == 64
