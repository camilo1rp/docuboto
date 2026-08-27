# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Acceso a datos de cuentas de correo y aplicaciones OAuth (E19 Increment 1).

asyncpg + SQL crudo (ADR-003).

**La visibilidad va en el `WHERE`, nunca en una comprobación previa.** Una
cuenta *personal* es el buzón privado de un funcionario: verla, aunque sea solo
su existencia y su dirección, no es asunto de sus compañeros ni del
administrador. Y una *institucional* la trabajan los funcionarios asignados —la
«asignación de responsabilidades» del Acuerdo 060/2001 art. 13 hecha dato—. Si
ese recorte viviera en el servicio, cualquier consulta nueva que se olvidara de
llamarlo devolvería de más; puesto en el `WHERE`, la única forma de saltárselo
es escribir otra consulta a propósito.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

# Columnas de la cuenta que salen a la API. `secreto_cifrado`, `secreto_nonce`,
# `oauth_refresh_cifrado` y `oauth_refresh_nonce` NO están, a propósito: los
# secretos se leen por su método dedicado, que existe para un solo uso y deja
# claro en la pila de llamadas quién los pidió.
_COLUMNAS = """
    c.id, c.nombre, c.tipo, c.proveedor, c.direccion, c.usuario_login,
    c.host, c.puerto, c.usa_ssl,
    c.auth_tipo, c.app_oauth_id, c.carpeta_origen, c.carpeta_radicados,
    c.dependencia_destino, c.tipo_documental_id, c.modo, c.activa,
    c.acuse_al_remitente, c.responsable_user_id AS responsable,
    c.propietario_user_id, c.ultimo_sondeo_at, c.ultimo_error, c.created_at,
    (c.secreto_cifrado IS NOT NULL OR c.oauth_refresh_cifrado IS NOT NULL) AS tiene_credencial
"""

# Quién alcanza una cuenta. Se escribe UNA vez y la usan el listado, el detalle
# y toda operación sobre una cuenta concreta.
#
#   $N   = user_id del solicitante
#   $N+1 = si es administrador de la entidad
#
# Un administrador ve las institucionales aunque no esté asignado —tiene que
# poder configurarlas— pero NO ve las personales de nadie: administrar la
# ventanilla no da acceso al correo privado de un funcionario.
_VISIBLE = """
    (
        (c.tipo = 'personal' AND c.propietario_user_id = {actor})
        OR (
            c.tipo = 'institucional' AND (
                {es_admin}
                OR EXISTS (
                    SELECT 1 FROM email_cuenta_usuario cu
                     WHERE cu.cuenta_id = c.id AND cu.user_id = {actor}
                )
            )
        )
    )
"""


def _visible(actor: str, es_admin: str) -> str:
    return _VISIBLE.format(actor=actor, es_admin=es_admin)


class EmailCuentaRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # -- aplicaciones OAuth de la entidad ----------------------------------

    async def crear_app(
        self,
        *,
        proveedor: str,
        client_id: str,
        secreto_cifrado: bytes,
        secreto_nonce: bytes,
        azure_directory_id: str | None,
        redirect_uri: str,
        notas: str | None,
        actor: UUID | None,
        app_id: UUID,
    ) -> asyncpg.Record:
        return await self._conn.fetchrow(
            """
            INSERT INTO email_app_oauth
                (id, proveedor, client_id, secreto_cifrado, secreto_nonce,
                 azure_directory_id, redirect_uri, notas, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id, proveedor, client_id, azure_directory_id, redirect_uri,
                      notas, created_at, (secreto_cifrado IS NOT NULL) AS tiene_secreto
            """,
            app_id, proveedor, client_id, secreto_cifrado, secreto_nonce,
            azure_directory_id, redirect_uri, notas, actor,
        )

    async def listar_apps(self) -> list[asyncpg.Record]:
        return await self._conn.fetch(
            """
            SELECT id, proveedor, client_id, azure_directory_id, redirect_uri, notas,
                   created_at, (secreto_cifrado IS NOT NULL) AS tiene_secreto
              FROM email_app_oauth
             ORDER BY proveedor
            """
        )

    async def obtener_app_con_secreto(self, app_id: UUID) -> asyncpg.Record | None:
        """Único punto que saca el *client secret*. Ver la nota de `_COLUMNAS`."""
        return await self._conn.fetchrow(
            "SELECT * FROM email_app_oauth WHERE id = $1", app_id
        )

    # -- cuentas -----------------------------------------------------------

    async def crear_cuenta(self, campos: dict[str, Any]) -> asyncpg.Record:
        nombres = list(campos.keys())
        marcadores = ", ".join(f"${i}" for i in range(1, len(nombres) + 1))
        return await self._conn.fetchrow(
            f"""
            INSERT INTO email_cuenta ({", ".join(nombres)})
            VALUES ({marcadores})
            RETURNING {_COLUMNAS.replace("c.", "")}
            """,
            *campos.values(),
        )

    async def listar(self, *, actor: UUID, es_admin: bool) -> list[asyncpg.Record]:
        return await self._conn.fetch(
            f"""
            SELECT {_COLUMNAS}
              FROM email_cuenta c
             WHERE {_visible("$1", "$2")}
             ORDER BY c.tipo, c.nombre
            """,
            actor, es_admin,
        )

    async def obtener(
        self, cuenta_id: UUID, *, actor: UUID, es_admin: bool
    ) -> asyncpg.Record | None:
        """Devuelve `None` tanto si no existe como si no se alcanza.

        Distinguir los dos casos convertiría el endpoint en un oráculo de qué
        cuentas de correo tiene la entidad y quién las trabaja.
        """
        return await self._conn.fetchrow(
            f"""
            SELECT {_COLUMNAS}
              FROM email_cuenta c
             WHERE c.id = $1 AND {_visible("$2", "$3")}
            """,
            cuenta_id, actor, es_admin,
        )

    async def obtener_para_conectar(
        self, cuenta_id: UUID, *, actor: UUID, es_admin: bool
    ) -> asyncpg.Record | None:
        """La fila COMPLETA, secretos incluidos, para abrir una sesión IMAP.

        Lleva el mismo recorte de visibilidad que las lecturas: sin él, un
        identificador adivinado bastaría para conectarse con la credencial de
        una cuenta que no se puede ni ver.
        """
        return await self._conn.fetchrow(
            f"""
            SELECT c.*, a.client_id, a.secreto_cifrado AS app_secreto_cifrado,
                   a.secreto_nonce AS app_secreto_nonce, a.azure_directory_id
              FROM email_cuenta c
              LEFT JOIN email_app_oauth a ON a.id = c.app_oauth_id
             WHERE c.id = $1 AND {_visible("$2", "$3")}
            """,
            cuenta_id, actor, es_admin,
        )

    async def actualizar(
        self, cuenta_id: UUID, campos: dict[str, Any], *, actor: UUID, es_admin: bool
    ) -> asyncpg.Record | None:
        if not campos:
            return await self.obtener(cuenta_id, actor=actor, es_admin=es_admin)
        asignaciones = ", ".join(f"{k} = ${i}" for i, k in enumerate(campos, start=4))
        return await self._conn.fetchrow(
            f"""
            UPDATE email_cuenta c
               SET {asignaciones}, updated_at = now()
             WHERE c.id = $1 AND {_visible("$2", "$3")}
            RETURNING {_COLUMNAS}
            """,
            cuenta_id, actor, es_admin, *campos.values(),
        )

    async def registrar_sondeo(
        self, cuenta_id: UUID, *, error: str | None
    ) -> None:
        """Deja constancia del último intento, con o sin fallo.

        Sin esto, una cuenta mal configurada se manifiesta como «no llega
        nada», que es indistinguible de «no ha llegado correo».
        """
        await self._conn.execute(
            "UPDATE email_cuenta SET ultimo_sondeo_at = now(), ultimo_error = $2 WHERE id = $1",
            cuenta_id, error,
        )

    # -- funcionarios asignados -------------------------------------------

    async def funcionarios(self, cuenta_id: UUID) -> list[UUID]:
        filas = await self._conn.fetch(
            "SELECT user_id FROM email_cuenta_usuario WHERE cuenta_id = $1 ORDER BY asignado_at",
            cuenta_id,
        )
        return [f["user_id"] for f in filas]

    async def asignar(self, cuenta_id: UUID, user_id: UUID, *, actor: UUID | None) -> None:
        await self._conn.execute(
            """
            INSERT INTO email_cuenta_usuario (cuenta_id, user_id, asignado_by)
            VALUES ($1, $2, $3)
            ON CONFLICT (cuenta_id, user_id) DO NOTHING
            """,
            cuenta_id, user_id, actor,
        )

    async def desasignar(self, cuenta_id: UUID, user_id: UUID) -> bool:
        estado = await self._conn.execute(
            "DELETE FROM email_cuenta_usuario WHERE cuenta_id = $1 AND user_id = $2",
            cuenta_id, user_id,
        )
        return estado.endswith(" 1")

    # -- autorización OAuth2 en curso --------------------------------------

    async def guardar_state(
        self,
        *,
        state: str,
        cuenta_id: UUID,
        iniciado_por: UUID,
        code_verifier: str,
        redirect_uri: str,
        minutos: int,
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO email_oauth_state
                (state, cuenta_id, iniciado_por, code_verifier, redirect_uri, expira_at)
            VALUES ($1, $2, $3, $4, $5, now() + make_interval(mins => $6))
            """,
            state, cuenta_id, iniciado_por, code_verifier, redirect_uri, minutos,
        )

    async def consumir_state(self, state: str, *, cuenta_id: UUID, actor: UUID):
        """Marca el `state` usado y lo devuelve, o `None` si no sirve.

        Las cuatro condiciones van en el **mismo `UPDATE`**, no en un `SELECT`
        previo: entre comprobar y marcar cabría una segunda petición con el mismo
        `state`, que es exactamente el ataque de reproducción que el `state`
        existe para impedir. Al ir juntas, la segunda petición no encuentra fila.

        - `usado_at IS NULL` — un solo uso.
        - `expira_at > now()` — un consentimiento abandonado no queda vivo.
        - `cuenta_id` — el `state` de una cuenta no autoriza otra.
        - `iniciado_por` — lo completa quien lo empezó, no un tercero.
        """
        return await self._conn.fetchrow(
            """
            UPDATE email_oauth_state
               SET usado_at = now()
             WHERE state = $1
               AND cuenta_id = $2
               AND iniciado_por = $3
               AND usado_at IS NULL
               AND expira_at > now()
            RETURNING code_verifier, redirect_uri
            """,
            state, cuenta_id, actor,
        )

    async def guardar_refresh(
        self, cuenta_id: UUID, *, cifrado: bytes, nonce: bytes
    ) -> None:
        await self._conn.execute(
            """
            UPDATE email_cuenta
               SET oauth_refresh_cifrado = $2, oauth_refresh_nonce = $3,
                   ultimo_error = NULL, updated_at = now()
             WHERE id = $1
            """,
            cuenta_id, cifrado, nonce,
        )
