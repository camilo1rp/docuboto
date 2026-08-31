# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La cascada DIVIPOLA contra PostgreSQL real — E01, RF-RAD-05.

**Por qué este carril.** Lo que hay que demostrar es que la geografía dejó de
ser algo que se escribe: la clave foránea, el CHECK de pertenencia y la
AUSENCIA deliberada de clave foránea en la copia congelada los impone la base.
Con dobles, cada «no se pudo» lo diría el doble.

El invariante menos obvio es el último, y es el que sostiene todo lo demás: la
ficha viva se ata al catálogo y **la copia congelada no**, porque un envío
histórico tiene que sobrevivir a que el municipio deje de existir.

Gating: `RUN_INTEGRATION=1` + Postgres real.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema

pytestmark = pytest.mark.integration

# Códigos DIVIPOLA reales, verificados contra el catálogo cargado por
# `global/003_divipola.sql`. Se escriben literales a propósito: si la carga se
# rompiera, un test que leyera el código de la propia tabla seguiría pasando.
MEDELLIN = "05001"
BOGOTA = "11001"
ANTIOQUIA = "05"
BOGOTA_DC = "11"


async def _en_el_tenant(conn: asyncpg.Connection, schema: str) -> None:
    await conn.execute(f"SET search_path TO {tenant_schema(schema)}, public")


async def _preparar(conn: asyncpg.Connection, schema: str) -> uuid.UUID:
    await _en_el_tenant(conn, schema)
    return await conn.fetchval(
        """
        INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, tenant_code)
        VALUES ($1, 'S', 2026, 1, 'Oficio de prueba', 'IT') RETURNING id
        """,
        f"2026-IT-S-{uuid.uuid4().hex[:6]}",
    )


@pytest.mark.asyncio
async def test_el_catalogo_esta_cargado_y_es_el_de_Colombia(tenant_ctx) -> None:
    """Control de la carga. 33 departamentos son los de DIVIPOLA —32 más
    Bogotá, D.C.—; que Medellín sea 05001 confirma que los códigos son los
    oficiales y no una numeración inventada por el volcado de origen."""
    _, conn = tenant_ctx
    assert await conn.fetchval("SELECT count(*) FROM public.divipola_departamento") == 33
    assert (
        await conn.fetchval(
            "SELECT nombre FROM public.divipola_municipio WHERE codigo = $1", MEDELLIN
        )
        == "Medellín"
    )


@pytest.mark.asyncio
async def test_un_municipio_fuera_del_catalogo_no_entra_en_la_ficha(tenant_ctx) -> None:
    """La ficha viva se ata al catálogo: es lo que impide volver al texto libre
    por la puerta de atrás inventando un código."""
    schema, conn = tenant_ctx
    await _en_el_tenant(conn, schema)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await conn.execute(
            "INSERT INTO tercero (tipo, nombre, departamento_codigo, municipio_codigo) "
            "VALUES ('ciudadano', 'Nombre Valido', $1, '05999')",
            ANTIOQUIA,
        )


@pytest.mark.asyncio
async def test_un_municipio_de_OTRO_departamento_es_imposible(tenant_ctx) -> None:
    """El error que una cascada mal programada produce en silencio: se cambia
    el departamento y queda el municipio del anterior. El código DIVIPOLA lleva
    dentro el de su departamento, así que la base lo comprueba sin consultar
    nada."""
    schema, conn = tenant_ctx
    await _en_el_tenant(conn, schema)
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "INSERT INTO tercero (tipo, nombre, departamento_codigo, municipio_codigo) "
            "VALUES ('ciudadano', 'Nombre Valido', $1, $2)",
            BOGOTA_DC,
            MEDELLIN,
        )


@pytest.mark.asyncio
async def test_sin_codigo_la_ficha_sigue_siendo_valida(tenant_ctx) -> None:
    """El control positivo, y no es un detalle: una dirección en el extranjero
    no tiene DIVIPOLA, y las fichas anteriores al catálogo tampoco. Exigir el
    código habría cerrado la ventanilla por un dato de presentación."""
    schema, conn = tenant_ctx
    await _en_el_tenant(conn, schema)
    creado = await conn.fetchval(
        "INSERT INTO tercero (tipo, nombre, pais, municipio) "
        "VALUES ('ciudadano', 'Jean Dupont', 'FR', 'Lyon') RETURNING id"
    )
    assert creado is not None


@pytest.mark.asyncio
async def test_la_copia_congelada_sobrevive_a_que_el_municipio_desaparezca(tenant_ctx) -> None:
    """**El invariante que justifica la asimetría.** La ficha viva lleva clave
    foránea al catálogo y la copia congelada NO, a propósito: si el envío se
    atara al catálogo, una reorganización territorial corrompería el registro
    histórico —o, con un CASCADE, se lo llevaría por delante—.

    Se prueba con un código que NO está en el catálogo: si hubiera clave
    foránea, esta inserción fallaría."""
    schema, conn = tenant_ctx
    radicado_id = await _preparar(conn, schema)
    tercero_id = await conn.fetchval(
        "INSERT INTO tercero (tipo, nombre) VALUES ('ciudadano', 'Ana Ruiz') RETURNING id"
    )
    await conn.execute(
        """
        INSERT INTO radicado_actor (radicado_id, tercero_id, rol, nombre, direccion,
                                    municipio_codigo, departamento_codigo, municipio)
        VALUES ($1, $2, 'destinatario', 'Ana Ruiz', 'Calle 10 #5-20',
                '99999', '99', 'Municipio suprimido')
        """,
        radicado_id,
        tercero_id,
    )
    assert (
        await conn.fetchval(
            "SELECT municipio FROM radicado_actor WHERE radicado_id = $1", radicado_id
        )
        == "Municipio suprimido"
    )


@pytest.mark.asyncio
async def test_el_codigo_congelado_tampoco_se_puede_reescribir(tenant_ctx) -> None:
    """Sin ampliar el trigger quedaría el hueco exacto que la copia existe para
    cerrar: el NOMBRE del municipio protegido y su CÓDIGO libre, de modo que un
    UPDATE movería el envío a otro sitio sin tocar una letra del texto."""
    schema, conn = tenant_ctx
    radicado_id = await _preparar(conn, schema)
    tercero_id = await conn.fetchval(
        "INSERT INTO tercero (tipo, nombre) VALUES ('ciudadano', 'Ana Ruiz') RETURNING id"
    )
    await conn.execute(
        """
        INSERT INTO radicado_actor (radicado_id, tercero_id, rol, nombre, direccion,
                                    municipio_codigo, departamento_codigo)
        VALUES ($1, $2, 'destinatario', 'Ana Ruiz', 'Calle 10 #5-20', $3, $4)
        """,
        radicado_id,
        tercero_id,
        MEDELLIN,
        ANTIOQUIA,
    )
    with pytest.raises(asyncpg.PostgresError):
        await conn.execute(
            "UPDATE radicado_actor SET municipio_codigo = $1 WHERE radicado_id = $2",
            BOGOTA,
            radicado_id,
        )


@pytest.mark.asyncio
async def test_la_misma_direccion_en_DOS_municipios_es_legitimo(tenant_ctx) -> None:
    """«Calle 10 #5-20» existe en medio país. Antes de que el municipio entrara
    en la deduplicación, dos envíos legítimos a esa dirección en dos municipios
    distintos se rechazaban como duplicado."""
    schema, conn = tenant_ctx
    radicado_id = await _preparar(conn, schema)
    tercero_id = await conn.fetchval(
        "INSERT INTO tercero (tipo, nombre) VALUES ('ciudadano', 'Ana Ruiz') RETURNING id"
    )

    async def vincular(municipio: str, departamento: str, orden: int) -> None:
        await conn.execute(
            """
            INSERT INTO radicado_actor (radicado_id, tercero_id, rol, orden, nombre,
                                        direccion, municipio_codigo, departamento_codigo)
            VALUES ($1, $2, 'destinatario', $3, 'Ana Ruiz', 'Calle 10 #5-20', $4, $5)
            """,
            radicado_id,
            tercero_id,
            orden,
            municipio,
            departamento,
        )

    await vincular(MEDELLIN, ANTIOQUIA, 0)
    await vincular(BOGOTA, BOGOTA_DC, 1)
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM radicado_actor WHERE radicado_id = $1", radicado_id
        )
        == 2
    )

    # Y el duplicado de verdad —misma dirección, MISMO municipio— sigue siendo
    # un duplicado: sin este control el test de arriba lo pasaría un índice
    # borrado.
    with pytest.raises(asyncpg.UniqueViolationError):
        await vincular(MEDELLIN, ANTIOQUIA, 2)


@pytest.mark.asyncio
async def test_el_filtro_de_municipios_ignora_los_acentos(tenant_ctx) -> None:
    """Quien escribe «monteria» tiene que dar con «Montería». Es la misma
    defensa blanda que la búsqueda de terceros: el duplicado no nace de saltarse
    una restricción, nace de no encontrar lo que ya está."""
    _, conn = tenant_ctx
    from app.repositories.tercero_repository import TerceroRepository

    repo = TerceroRepository(conn, "itest")
    encontrados = await repo.list_municipios("23", texto="monteria")
    assert [m["nombre"] for m in encontrados] == ["Montería"]
