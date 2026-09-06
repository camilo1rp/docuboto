# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La ventanilla ciudadana alimenta el directorio — pero solo cuando puede.

Es la puerta de más volumen y hasta ahora no producía ninguna ficha: los datos
del remitente se guardaban como JSON suelto en `metadata.remitente`, así que el
mismo ciudadano escribiendo tres veces eran tres textos distintos y ningún
tercero.

**La regla tiene dos ramas y las dos importan:**

* **Con identificación** → `crear_o_recuperar` por documento. Es idempotente,
  así que quien se identifica converge SIEMPRE a la misma ficha por muchas veces
  que escriba. Ahí es donde el duplicado se evita de verdad.
* **Sin identificación** → **ninguna ficha**. Una petición anónima es legítima
  (Ley 1755/2015 art. 16, denuncia anónima) y crear un tercero por cada una
  sería una fábrica de duplicados: exactamente lo contrario del objetivo, más un
  directorio de «anónimos» que no le sirve a nadie.

Contra Postgres real porque lo que se afirma es que hay —o no hay— filas.
"""

from __future__ import annotations

import asyncpg
import pytest

from app.core.database import tenant_schema

pytestmark = pytest.mark.integration


async def _radicar_por_ventanilla(
    conn: asyncpg.Connection, slug: str, **campos: str | None
) -> None:
    """Radica por la ventanilla ciudadana con el mínimo viable.

    Se llama al SERVICIO y no al endpoint: lo que se comprueba es el modelo que
    queda en la base, no el transporte HTTP —que tiene sus propios tests—.
    """
    from app.schemas.public import VentanillaRadicacion
    from app.services.document_service import DocumentService
    from app.services.ventanilla_service import VentanillaService

    payload = VentanillaRadicacion(
        tipo_documental=campos.get("tipo_documental") or "DEM-PET",
        nombre=campos["nombre"],
        entidad=campos.get("entidad"),
        tipo_identificacion=campos.get("tipo_identificacion"),
        identificacion=campos.get("identificacion"),
        email=campos.get("email"),
        telefono=campos.get("telefono"),
        direccion=campos.get("direccion"),
        asunto=campos.get("asunto") or "Petición de prueba",
        descripcion="Cuerpo de la petición",
    )
    # El único doble de este test: la validación de la dependencia contra
    # tenant-service, que es HTTP a otro servicio. Todo lo demás va a Postgres
    # de verdad — lo que se afirma aquí es qué filas quedan, y eso un mock no lo
    # puede decir.
    class _TenantStub:
        async def get_dependencia(self, *, tenant_slug: str, codigo: int) -> dict:
            return {"codigo": codigo, "nombre": "Ventanilla Única"}

    await VentanillaService(conn, slug).radicar(
        payload, DocumentService(conn, slug, tenant_client=_TenantStub())
    )


async def _preparar_ventanilla(conn: asyncpg.Connection) -> None:
    """Las dos tablas de OTROS servicios que la ventanilla lee.

    `dependencias` la crea tenant-service y `tipos_documentales` archive-service;
    el fixture de este tier solo aplica las migraciones de document-service, así
    que aquí se declaran con la forma mínima que la consulta usa. Se crean a mano
    y no se importan las migraciones ajenas a propósito: copiarlas ataría este
    test a los cambios de otro servicio, y lo que se prueba aquí no es su
    esquema — es qué queda en `tercero` y `radicado_actor`.
    """
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS dependencias ("
        "  codigo int PRIMARY KEY, nombre text NOT NULL,"
        "  activo boolean NOT NULL DEFAULT true,"
        "  depe_rad_tp2 boolean NOT NULL DEFAULT false)"
    )
    await conn.execute(
        "INSERT INTO dependencias (codigo, nombre, activo, depe_rad_tp2) "
        "VALUES (901, 'Ventanilla Única', true, true) ON CONFLICT DO NOTHING"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS tipos_documentales ("
        "  code text PRIMARY KEY, nombre text NOT NULL,"
        "  activo boolean NOT NULL DEFAULT true,"
        "  admite_ventanilla_publica boolean NOT NULL DEFAULT false)"
    )
    await conn.execute(
        "INSERT INTO tipos_documentales (code, nombre, activo, admite_ventanilla_publica) "
        "VALUES ('DEM-PET', 'Petición', true, true) ON CONFLICT DO NOTHING"
    )


@pytest.mark.asyncio
async def test_el_mismo_ciudadano_identificado_converge_a_UNA_ficha(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Escribir tres veces no crea tres fichas. Es el caso que motivó todo esto."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    await _preparar_ventanilla(conn)

    for asunto in ("Primera", "Segunda", "Tercera"):
        await _radicar_por_ventanilla(
            conn,
            slug,
            nombre="José Pérez",
            tipo_identificacion="CC",
            identificacion="1020304050",
            email="jose@ejemplo.co",
            asunto=asunto,
        )

    fichas = await conn.fetchval(
        "SELECT count(*) FROM tercero WHERE numero_documento = '1020304050'"
    )
    assert fichas == 1, "tres peticiones del mismo ciudadano tienen que dar UNA ficha"

    vinculos = await conn.fetchval(
        "SELECT count(*) FROM radicado_actor WHERE rol = 'remitente'"
    )
    assert vinculos == 3, "las tres peticiones tienen que quedar atadas a esa ficha"


@pytest.mark.asyncio
async def test_una_peticion_ANONIMA_no_crea_ninguna_ficha(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """La denuncia anónima es legítima, y no puede ensuciar el directorio.

    Sin documento no hay nada que reconocer: una ficha por petición anónima no
    sería reutilizable nunca y convertiría la puerta de más volumen en la mayor
    fuente de duplicados del sistema.
    """
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    await _preparar_ventanilla(conn)

    await _radicar_por_ventanilla(conn, slug, nombre="Anónimo", asunto="Denuncia")

    assert await conn.fetchval("SELECT count(*) FROM tercero") == 0
    assert await conn.fetchval("SELECT count(*) FROM radicado_actor") == 0
    # Pero el radicado SÍ existe y conserva lo que el ciudadano escribió.
    assert await conn.fetchval("SELECT count(*) FROM radicados") == 1
    assert await conn.fetchval("SELECT sender_name FROM radicados") == "Anónimo"


# ---------------------------------------------------------------------------
# La tercera puerta: la ingesta de correo
# ---------------------------------------------------------------------------
# Aquí la identidad del corresponsal es su DIRECCIÓN, igual que en la ventanilla
# es su documento. Es la puerta de más volumen: sin ficha, el mismo remitente
# escribiendo cada semana eran cincuenta textos distintos en `sender_name`.


@pytest.mark.asyncio
async def test_el_mismo_remitente_de_correo_reutiliza_su_ficha(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from app.services.tercero_service import TerceroService

    servicio = TerceroService(conn, slug)
    a = await servicio.resolver_por_email("ana@ejemplo.co", nombre="Ana Ruiz", actor=None)
    # La segunda vez firma distinto — pasa constantemente — y aun así es la misma
    # ficha: la dirección manda, no el texto del nombre.
    b = await servicio.resolver_por_email("ANA@ejemplo.co", nombre="ana ruiz", actor=None)

    assert a is not None and b is not None
    assert a["id"] == b["id"], "el mismo correo tiene que converger a UNA ficha"
    assert await conn.fetchval("SELECT count(*) FROM tercero") == 1


@pytest.mark.asyncio
async def test_dos_fichas_con_el_mismo_correo_NO_se_eligen_solas(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Un duplicado sin resolver no puede decidirse por `ORDER BY`.

    Elegir una sería decidir en silencio a quién pertenece el documento. El
    correo se radica sin ficha —como antes— y la ambigüedad se resuelve en
    `/admin/terceros`, que para eso existe.
    """
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from app.services.tercero_service import TerceroService

    for nombre in ("Luis A", "Luis B"):
        await conn.execute(
            "INSERT INTO tercero (nombre, tipo, email) VALUES ($1, 'ciudadano', 'luis@ejemplo.co')",
            nombre,
        )

    resuelto = await TerceroService(conn, slug).resolver_por_email(
        "luis@ejemplo.co", nombre="Luis", actor=None
    )
    assert resuelto is None
    # Y no se inventa una tercera para salir del paso.
    assert await conn.fetchval("SELECT count(*) FROM tercero") == 2


@pytest.mark.asyncio
async def test_un_correo_sin_direccion_o_sin_nombre_no_crea_ficha(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Una ficha sin nombre no se puede buscar ni elegir después: no sirve."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    from app.services.tercero_service import TerceroService

    servicio = TerceroService(conn, slug)
    assert await servicio.resolver_por_email(None, nombre="Alguien", actor=None) is None
    assert await servicio.resolver_por_email("x@y.co", nombre=None, actor=None) is None
    assert await servicio.resolver_por_email("  ", nombre="  ", actor=None) is None
    assert await conn.fetchval("SELECT count(*) FROM tercero") == 0
