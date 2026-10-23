# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Cuentas de correo contra Postgres real (E19 Increment 1).

Aquí se prueba lo que un doble no puede probar: que el recorte de visibilidad
vive de verdad en el `WHERE` de cada consulta, y que las restricciones de la
migración sostienen los invariantes cuando quien escribe es el repositorio y no
un `INSERT` a mano.

El caso que más importa es el de la **cuenta personal**: es el buzón privado de
un funcionario, y ni sus compañeros ni el administrador de la entidad tienen por
qué ver su existencia. Un fallo aquí no se manifiesta como un error, sino como
una fila de más en un listado que nadie mira dos veces.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.email_cuenta_repository import EmailCuentaRepository

pytestmark = pytest.mark.asyncio

ANA = uuid.uuid4()
BENITO = uuid.uuid4()
ADMIN = uuid.uuid4()


async def _en_tenant(conn: asyncpg.Connection, slug: str) -> EmailCuentaRepository:
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}"')
    return EmailCuentaRepository(conn)


async def _cuenta_institucional(repo: EmailCuentaRepository, nombre: str, direccion: str):
    return await repo.crear_cuenta(
        {
            "nombre": nombre,
            "tipo": "institucional",
            "proveedor": "imap",
            "host": "imap.e.gov.co",
            "direccion": direccion,
            "auth_tipo": "password",
            "secreto_cifrado": b"\x01",
            "secreto_nonce": b"\x02",
        }
    )


async def _cuenta_personal(repo: EmailCuentaRepository, dueno: uuid.UUID, direccion: str):
    return await repo.crear_cuenta(
        {
            "nombre": "Mi correo",
            "tipo": "personal",
            "proveedor": "imap",
            "host": "imap.e.gov.co",
            "direccion": direccion,
            "auth_tipo": "password",
            "propietario_user_id": dueno,
        }
    )


async def test_la_cuenta_personal_solo_la_ve_su_dueno(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo = await _en_tenant(conn, slug)
    personal = await _cuenta_personal(repo, ANA, "ana@e.gov.co")

    mias = await repo.listar(actor=ANA, es_admin=False)
    assert [c["id"] for c in mias] == [personal["id"]]

    # Un compañero no la ve...
    assert await repo.listar(actor=BENITO, es_admin=False) == []
    # ...y el administrador de la entidad TAMPOCO. Administrar la ventanilla no
    # da acceso al correo privado de un funcionario.
    assert await repo.listar(actor=ADMIN, es_admin=True) == []


async def test_la_institucional_la_ven_los_asignados_y_el_administrador(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo = await _en_tenant(conn, slug)
    cuenta = await _cuenta_institucional(repo, "Ventanilla PQRS", "pqrs@e.gov.co")

    # Sin asignar, un funcionario cualquiera no la ve.
    assert await repo.listar(actor=ANA, es_admin=False) == []
    # El administrador sí: tiene que poder configurarla.
    assert [c["id"] for c in await repo.listar(actor=ADMIN, es_admin=True)] == [cuenta["id"]]

    await repo.asignar(cuenta["id"], ANA, actor=ADMIN)
    assert [c["id"] for c in await repo.listar(actor=ANA, es_admin=False)] == [cuenta["id"]]
    assert await repo.funcionarios(cuenta["id"]) == [ANA]

    # Y al quitarla, deja de verla — el permiso no se queda pegado.
    assert await repo.desasignar(cuenta["id"], ANA) is True
    assert await repo.listar(actor=ANA, es_admin=False) == []


async def test_el_detalle_no_distingue_no_existe_de_no_alcanzo(tenant_ctx) -> None:
    """404 indistinguible: si no, el endpoint sería un oráculo de qué cuentas
    de correo tiene la entidad."""
    slug, conn = tenant_ctx
    repo = await _en_tenant(conn, slug)
    cuenta = await _cuenta_institucional(repo, "Judicial", "judicial@e.gov.co")

    inexistente = uuid.uuid4()
    assert await repo.obtener(inexistente, actor=BENITO, es_admin=False) is None
    assert await repo.obtener(cuenta["id"], actor=BENITO, es_admin=False) is None


async def test_conectarse_lleva_el_mismo_recorte_que_leer(tenant_ctx) -> None:
    """El agujero que sería fácil dejar abierto.

    `obtener_para_conectar` devuelve la fila COMPLETA, con la credencial. Si se
    hubiera escrito sin el recorte —«total, ya se comprobó al listar»—, un
    identificador adivinado bastaría para abrir una sesión IMAP con la
    credencial de una cuenta que ni siquiera se puede ver.
    """
    slug, conn = tenant_ctx
    repo = await _en_tenant(conn, slug)
    cuenta = await _cuenta_institucional(repo, "Ventanilla", "v@e.gov.co")

    assert await repo.obtener_para_conectar(cuenta["id"], actor=BENITO, es_admin=False) is None

    fila = await repo.obtener_para_conectar(cuenta["id"], actor=ADMIN, es_admin=True)
    assert fila is not None and fila["secreto_cifrado"] == b"\x01"


async def test_actualizar_tambien_recorta(tenant_ctx) -> None:
    """La escritura lleva el gate DENTRO del UPDATE, no en una consulta previa:
    autorización y mutación son el mismo acto, sin ventana entre medias."""
    slug, conn = tenant_ctx
    repo = await _en_tenant(conn, slug)
    cuenta = await _cuenta_institucional(repo, "Ventanilla", "v@e.gov.co")

    assert (
        await repo.actualizar(
            cuenta["id"], {"nombre": "Secuestrada"}, actor=BENITO, es_admin=False
        )
        is None
    )
    sin_cambios = await repo.obtener(cuenta["id"], actor=ADMIN, es_admin=True)
    assert sin_cambios is not None and sin_cambios["nombre"] == "Ventanilla"

    cambiada = await repo.actualizar(
        cuenta["id"], {"nombre": "Ventanilla única"}, actor=ADMIN, es_admin=True
    )
    assert cambiada is not None and cambiada["nombre"] == "Ventanilla única"


async def test_las_cuentas_no_se_ven_entre_entidades(two_tenant_ctx) -> None:
    """Aislamiento por esquema, comprobado y no supuesto."""
    slug_a, slug_b, conn = two_tenant_ctx

    repo_a = await _en_tenant(conn, slug_a)
    await _cuenta_institucional(repo_a, "PQRS de A", "pqrs@a.gov.co")
    assert len(await repo_a.listar(actor=ADMIN, es_admin=True)) == 1

    repo_b = await _en_tenant(conn, slug_b)
    assert await repo_b.listar(actor=ADMIN, es_admin=True) == []


async def test_la_credencial_no_sale_en_las_lecturas(tenant_ctx) -> None:
    """El listado y el detalle dicen SI hay credencial, nunca cuál.

    Un endpoint de lectura que devolviera el secreto lo esparciría por los
    registros del servidor, el navegador y la caché de la interfaz, y el cifrado
    en reposo dejaría de servir para nada.
    """
    slug, conn = tenant_ctx
    repo = await _en_tenant(conn, slug)
    cuenta = await _cuenta_institucional(repo, "Ventanilla", "v@e.gov.co")

    for fila in (
        (await repo.listar(actor=ADMIN, es_admin=True))[0],
        await repo.obtener(cuenta["id"], actor=ADMIN, es_admin=True),
    ):
        assert fila is not None
        claves = set(fila.keys())
        assert "secreto_cifrado" not in claves
        assert "oauth_refresh_cifrado" not in claves
        assert fila["tiene_credencial"] is True


# ── Autorización OAuth2 en curso ─────────────────────────────────────────────
#
# El `state` es la única defensa contra que un tercero induzca al administrador a
# completar un flujo que el atacante inició. Sus cuatro condiciones van en el
# mismo `UPDATE` que lo marca usado, y eso solo se puede comprobar contra una
# base de datos real: con un doble, el «cero filas» lo devuelve el doble.


async def _cuenta_oauth(repo: EmailCuentaRepository, conn: asyncpg.Connection):
    app_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO email_app_oauth
            (id, proveedor, client_id, secreto_cifrado, secreto_nonce, redirect_uri)
        VALUES ($1, 'gmail', 'cid', '\\x01', '\\x02', 'https://sgd/cb')
        """,
        app_id,
    )
    return await repo.crear_cuenta(
        {
            "nombre": "PQRS",
            "tipo": "institucional",
            "proveedor": "gmail",
            "host": "imap.gmail.com",
            "direccion": "pqrs@e.gov.co",
            "auth_tipo": "oauth2",
            "app_oauth_id": app_id,
        }
    )


async def _state(repo: EmailCuentaRepository, cuenta_id, actor, *, minutos: int = 10) -> str:
    valor = f"st-{uuid.uuid4().hex}"
    await repo.guardar_state(
        state=valor,
        cuenta_id=cuenta_id,
        iniciado_por=actor,
        code_verifier="v" * 64,
        redirect_uri="https://sgd/cb",
        minutos=minutos,
    )
    return valor


async def test_el_state_sirve_una_sola_vez(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo = await _en_tenant(conn, slug)
    cuenta = await _cuenta_oauth(repo, conn)
    st = await _state(repo, cuenta["id"], ANA)

    primero = await repo.consumir_state(st, cuenta_id=cuenta["id"], actor=ANA)
    assert primero is not None and primero["code_verifier"] == "v" * 64

    # Reproducir la redirección no vuelve a funcionar.
    assert await repo.consumir_state(st, cuenta_id=cuenta["id"], actor=ANA) is None


async def test_el_state_de_una_cuenta_no_autoriza_otra(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo = await _en_tenant(conn, slug)
    cuenta = await _cuenta_oauth(repo, conn)
    otra = await _cuenta_institucional(repo, "Otra", "otra@e.gov.co")
    st = await _state(repo, cuenta["id"], ANA)

    assert await repo.consumir_state(st, cuenta_id=otra["id"], actor=ANA) is None
    # Y sigue siendo válido para la suya: el intento fallido no lo quemó.
    assert await repo.consumir_state(st, cuenta_id=cuenta["id"], actor=ANA) is not None


async def test_lo_completa_quien_lo_inicio(tenant_ctx) -> None:
    """Si no, bastaría con que un administrador iniciara la autorización para
    que cualquier otro la completara apuntando a un buzón suyo."""
    slug, conn = tenant_ctx
    repo = await _en_tenant(conn, slug)
    cuenta = await _cuenta_oauth(repo, conn)
    st = await _state(repo, cuenta["id"], ANA)

    assert await repo.consumir_state(st, cuenta_id=cuenta["id"], actor=BENITO) is None
    assert await repo.consumir_state(st, cuenta_id=cuenta["id"], actor=ANA) is not None


async def test_un_state_caducado_no_sirve(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo = await _en_tenant(conn, slug)
    cuenta = await _cuenta_oauth(repo, conn)
    st = await _state(repo, cuenta["id"], ANA)
    # Se envejece en la base, no con un reloj falso: lo que se prueba es la
    # condición SQL, y un reloj de Python no la tocaría.
    #
    # Se mueven AMBAS marcas al pasado, no solo `expira_at`: el `CHECK` de
    # vigencia prohíbe que una fila exista ya caducada, y al intentar solo la
    # segunda lo rechazó — con razón. Una fila realmente caducada tiene las dos
    # en el pasado, así que envejecerla entera es además lo fiel.
    await conn.execute(
        """
        UPDATE email_oauth_state
           SET creado_at = now() - interval '2 hours',
               expira_at = now() - interval '1 hour'
         WHERE state = $1
        """,
        st,
    )
    assert await repo.consumir_state(st, cuenta_id=cuenta["id"], actor=ANA) is None


async def test_el_refresh_token_queda_guardado_y_no_sale_en_las_lecturas(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo = await _en_tenant(conn, slug)
    cuenta = await _cuenta_oauth(repo, conn)

    antes = await repo.obtener(cuenta["id"], actor=ADMIN, es_admin=True)
    assert antes is not None and antes["tiene_credencial"] is False

    await repo.guardar_refresh(cuenta["id"], cifrado=b"\x09", nonce=b"\x08")

    despues = await repo.obtener(cuenta["id"], actor=ADMIN, es_admin=True)
    assert despues is not None
    assert despues["tiene_credencial"] is True
    assert "oauth_refresh_cifrado" not in set(despues.keys())
