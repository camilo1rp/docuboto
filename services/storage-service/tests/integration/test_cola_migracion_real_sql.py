# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La cola del barrido de migración, contra PostgreSQL real — E10, RF-PRE-04.

**Por qué este carril y no dobles.** Lo que decide qué se convierte cada noche
es una consulta: con un doble, la cola la devolvería el doble y la consulta
podría decir cualquier cosa. La distinción que importa vive entera dentro del
`NOT EXISTS`:

* `fallido` dice algo del ARCHIVO —no se pudo convertir, o el resultado no era
  conforme— y reintentarlo cada noche cuesta un subproceso por archivo sin
  arreglar nada;
* `no_verificado` dice algo del DESPLIEGUE —se convirtió y no había validador
  con qué comprobarlo—, así que el día que veraPDF esté instalado la barrida
  siguiente lo resuelve sola.

Confundirlas tiene consecuencias opuestas y ninguna se ve: una deja la máquina
reconvirtiendo lo imposible, la otra abandona para siempre archivos que solo
esperaban a que instalaran una herramienta.

Corre con: `docker compose run --rm -e RUN_INTEGRATION=1 storage-service
pytest -q -m integration tests/integration`
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.preservacion_repository import PreservacionRepository

pytestmark = pytest.mark.integration

_SHA = "b" * 64
_DESTINO = "PDF/A-2b"
_EN_RIESGO = "application/msword"


async def _en_el_tenant(conn: asyncpg.Connection, slug: str) -> None:
    """El fixture aplica las migraciones con `SET LOCAL` dentro de su propia
    transacción, así que la conexión vuelve a `public` al cederla. Los tests que
    escriben con SQL directo —no a través del repositorio, que lo fija solo—
    tienen que posicionarse."""
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')


async def _fichero(conn: asyncpg.Connection, *, mime: str = _EN_RIESGO) -> uuid.UUID:
    return await conn.fetchval(
        """
        INSERT INTO files (filename, mime_type, file_size, sha256, bucket, object_key)
        VALUES ('oficio.doc', $1, 10, $2, 'b', $3) RETURNING id
        """,
        mime,
        _SHA,
        uuid.uuid4().hex,
    )


async def _migracion(conn: asyncpg.Connection, file_id: uuid.UUID, estado: str) -> None:
    destino = uuid.uuid4() if estado == "migrado" else None
    await conn.execute(
        """
        INSERT INTO preservacion_migracion
            (file_id_origen, file_id_destino, formato_origen, formato_destino,
             sha256_origen, sha256_destino, herramienta, estado, motivo, iniciada_por)
        VALUES ($1, $2, $3, $4, $5, $6, 'gs', $7, NULL, NULL)
        """,
        file_id,
        destino,
        _EN_RIESGO,
        _DESTINO,
        _SHA,
        ("c" * 64) if estado == "migrado" else None,
        estado,
    )


async def _cola(conn: asyncpg.Connection, slug: str) -> list[uuid.UUID]:
    filas = await PreservacionRepository(conn, slug).list_pendientes_migracion(
        [_EN_RIESGO], formato_destino=_DESTINO, limit=50
    )
    return [f["id"] for f in filas]


@pytest.mark.asyncio
async def test_sin_politica_la_cola_es_vacia_no_TODO(tenant_ctx) -> None:
    """La dirección segura ante la omisión. Una lista vacía interpretada como
    «todos» convertiría el archivo entero de una entidad la primera noche."""
    slug, conn = tenant_ctx
    await _en_el_tenant(conn, slug)
    await _fichero(conn)
    filas = await PreservacionRepository(conn, slug).list_pendientes_migracion(
        [], formato_destino=_DESTINO, limit=50
    )
    assert filas == []


@pytest.mark.asyncio
async def test_solo_entran_los_formatos_declarados(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await _en_el_tenant(conn, slug)
    en_riesgo = await _fichero(conn)
    await _fichero(conn, mime="application/pdf")
    assert await _cola(conn, slug) == [en_riesgo]


@pytest.mark.asyncio
async def test_lo_ya_migrado_no_vuelve_a_la_cola(tenant_ctx) -> None:
    """El índice único parcial de la migración 015 ya impediría la segunda
    copia, pero llegar hasta la conversión para chocar contra él costaría un
    subproceso por archivo y barrida."""
    slug, conn = tenant_ctx
    await _en_el_tenant(conn, slug)
    file_id = await _fichero(conn)
    await _migracion(conn, file_id, "migrado")
    assert await _cola(conn, slug) == []


@pytest.mark.asyncio
async def test_lo_FALLIDO_no_se_reintenta_cada_noche(tenant_ctx) -> None:
    """Es del archivo: reintentarlo no lo va a arreglar, hace falta que alguien
    lo mire."""
    slug, conn = tenant_ctx
    await _en_el_tenant(conn, slug)
    file_id = await _fichero(conn)
    await _migracion(conn, file_id, "fallido")
    assert await _cola(conn, slug) == []


@pytest.mark.asyncio
async def test_lo_NO_VERIFICADO_si_vuelve_a_intentarse(tenant_ctx) -> None:
    """**El otro lado de la misma moneda, y el que se pierde si se tratan
    igual.** La causa es del despliegue —faltaba el validador—, así que el día
    que esté instalado la barrida siguiente lo resuelve sin que nadie tenga que
    acordarse de estos archivos."""
    slug, conn = tenant_ctx
    await _en_el_tenant(conn, slug)
    file_id = await _fichero(conn)
    await _migracion(conn, file_id, "no_verificado")
    assert await _cola(conn, slug) == [file_id]


@pytest.mark.asyncio
async def test_un_fichero_eliminado_no_se_migra(tenant_ctx) -> None:
    """Migrar lo que ya no está sería resucitarlo por la puerta de atrás: el
    derivado seguiría vivo con el contenido del borrado."""
    slug, conn = tenant_ctx
    await _en_el_tenant(conn, slug)
    file_id = await _fichero(conn)
    await conn.execute("UPDATE files SET deleted_at = now() WHERE id = $1", file_id)
    assert await _cola(conn, slug) == []


@pytest.mark.asyncio
async def test_el_MIME_se_compara_SIN_distinguir_mayusculas(tenant_ctx) -> None:
    """El tipo MIME guardado es el que mandó quien subió el archivo, y los
    tipos MIME son case-insensitive (RFC 2045 §5.1). Comparando en crudo, un
    "Application/MSWord" quedaba fuera de una política que sí lo nombra: la
    barrida diría cero candidatos sin que nada pareciera roto — el fallo que
    importa aquí, que es creer que se convirtió."""
    slug, conn = tenant_ctx
    await _en_el_tenant(conn, slug)
    file_id = await _fichero(conn, mime="Application/MSWord")
    assert await _cola(conn, slug) == [file_id]


@pytest.mark.asyncio
async def test_la_politica_declarada_tambien_se_normaliza(tenant_ctx) -> None:
    """El otro extremo de lo mismo: lo que llega del plan o del despliegue."""
    slug, conn = tenant_ctx
    await _en_el_tenant(conn, slug)
    file_id = await _fichero(conn)
    filas = await PreservacionRepository(conn, slug).list_pendientes_migracion(
        [" APPLICATION/MSWORD "], formato_destino=_DESTINO, limit=50
    )
    assert [f["id"] for f in filas] == [file_id]
