# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Alta y operación de cuentas de correo (E19 Increment 1).

El servicio es el único sitio donde una credencial existe en claro, y siempre
por el tiempo mínimo: cifrarla al crear la cuenta, o descifrarla para abrir una
sesión IMAP. No se registra, no se devuelve y no se guarda en ningún atributo.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import asyncpg
from cryptography.exceptions import InvalidTag

from app.clients.imap_adaptador import Conexion, ErrorDeCorreo, ImapAdaptador
from app.core import email_credenciales, oauth_correo
from app.core.config import settings
from app.repositories.email_cuenta_repository import EmailCuentaRepository
from app.schemas.email_cuenta import AppOAuthCrear, CuentaCrear

logger = logging.getLogger(__name__)

# Cuánto vive un consentimiento iniciado. Diez minutos son de sobra para que un
# administrador complete la pantalla del proveedor, y poco para que un `state`
# filtrado siga sirviendo.
VIGENCIA_STATE_MINUTOS = 10


class ErrorDeCuenta(RuntimeError):
    """Fallo de negocio, con el código que el router traduce a HTTP."""

    def __init__(self, codigo: str, detalle: str, *, status: int = 422) -> None:
        super().__init__(detalle)
        self.codigo = codigo
        self.detalle = detalle
        self.status = status


class EmailCuentaService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant = tenant_slug
        self._repo = EmailCuentaRepository(conn)

    # -- aplicaciones OAuth ------------------------------------------------

    async def crear_app(self, datos: AppOAuthCrear, *, actor: uuid.UUID | None) -> asyncpg.Record:
        """Registra la aplicación que la entidad creó en su Google Cloud o Azure AD.

        El identificador se genera aquí y no en la base: el AAD del cifrado lo
        necesita **antes** del `INSERT`, porque ata el secreto a esa fila
        concreta. Dejar que lo pusiera el `DEFAULT` obligaría a cifrar en dos
        pasos, con una ventana en la que el secreto estaría guardado bajo un AAD
        provisional.
        """
        app_id = uuid.uuid4()
        ct, nonce = self._cifrar(datos.client_secret, app_id)
        try:
            return await self._repo.crear_app(
                proveedor=datos.proveedor,
                client_id=datos.client_id,
                secreto_cifrado=ct,
                secreto_nonce=nonce,
                azure_directory_id=datos.azure_directory_id,
                redirect_uri=datos.redirect_uri,
                notas=datos.notas,
                actor=actor,
                app_id=app_id,
            )
        except asyncpg.UniqueViolationError as exc:
            raise ErrorDeCuenta(
                "app_ya_registrada",
                f"La entidad ya tiene una aplicación de {datos.proveedor} registrada. "
                "Una sola aplicación sirve para todas sus cuentas de ese proveedor.",
                status=409,
            ) from exc

    async def listar_apps(self) -> list[asyncpg.Record]:
        return await self._repo.listar_apps()

    # -- cuentas -----------------------------------------------------------

    async def crear_cuenta(self, datos: CuentaCrear, *, actor: uuid.UUID | None) -> asyncpg.Record:
        host, puerto, ssl = datos.conexion_efectiva()
        cuenta_id = uuid.uuid4()

        campos: dict[str, Any] = {
            "id": cuenta_id,
            "nombre": datos.nombre,
            "tipo": datos.tipo,
            "proveedor": datos.proveedor,
            "host": host,
            "puerto": puerto,
            "usa_ssl": ssl,
            "direccion": datos.direccion,
            "usuario_login": datos.usuario_login,
            "auth_tipo": datos.auth_tipo,
            "app_oauth_id": datos.app_oauth_id,
            "carpeta_origen": datos.carpeta_origen,
            "carpeta_radicados": datos.carpeta_radicados,
            "dependencia_destino": datos.dependencia_destino,
            "tipo_documental_id": datos.tipo_documental_id,
            "modo": datos.modo,
            "responsable_user_id": datos.responsable_user_id,
            "acuse_al_remitente": datos.acuse_al_remitente,
            "propietario_user_id": datos.propietario_user_id,
            "created_by": actor,
        }
        if datos.password:
            campos["secreto_cifrado"], campos["secreto_nonce"] = self._cifrar(
                datos.password, cuenta_id
            )

        if datos.app_oauth_id and not await self._repo.obtener_app_con_secreto(datos.app_oauth_id):
            raise ErrorDeCuenta(
                "app_no_encontrada",
                "La aplicación OAuth indicada no existe en esta entidad",
                status=404,
            )

        try:
            return await self._repo.crear_cuenta(campos)
        except asyncpg.UniqueViolationError as exc:
            raise ErrorDeCuenta(
                "cuenta_duplicada",
                f"Ya hay una cuenta configurada para {datos.direccion}. Dos filas para el "
                "mismo buzón serían dos bandejas del mismo correo, y reservar un mensaje "
                "en una no protegería a la otra.",
                status=409,
            ) from exc

    async def listar(self, *, actor: uuid.UUID, es_admin: bool) -> list[dict[str, Any]]:
        filas = await self._repo.listar(actor=actor, es_admin=es_admin)
        salida: list[dict[str, Any]] = []
        for fila in filas:
            item = dict(fila)
            item["funcionarios"] = (
                await self._repo.funcionarios(fila["id"])
                if fila["tipo"] == "institucional"
                else []
            )
            salida.append(item)
        return salida

    # -- conexión ----------------------------------------------------------

    async def probar(
        self, cuenta_id: uuid.UUID, *, actor: uuid.UUID, es_admin: bool, password: str | None
    ) -> tuple[bool, str, list[str]]:
        """Abre una sesión y lista las carpetas del buzón. **No toca ningún correo.**

        Las carpetas se devuelven porque son la mitad del valor de la prueba: la
        carpeta de filtrado se elige de esta lista en vez de escribirse a mano,
        y una errata al teclearla se manifestaría como «la ingesta no trae
        nada», que es indistinguible de «no ha llegado correo».
        """
        conexion = await self._resolver_conexion(
            cuenta_id, actor=actor, es_admin=es_admin, password=password
        )
        adaptador = ImapAdaptador(conexion)
        try:
            carpetas = await adaptador.listar_carpetas()
        except ErrorDeCorreo as exc:
            await self._repo.registrar_sondeo(cuenta_id, error=str(exc))
            return False, str(exc), []
        await self._repo.registrar_sondeo(cuenta_id, error=None)
        return True, f"Conexión correcta con {conexion.direccion}", carpetas

    async def _resolver_conexion(
        self, cuenta_id: uuid.UUID, *, actor: uuid.UUID, es_admin: bool, password: str | None
    ) -> Conexion:
        fila = await self._repo.obtener_para_conectar(
            cuenta_id, actor=actor, es_admin=es_admin
        )
        if fila is None:
            # Mismo colapso que las lecturas: no se distingue «no existe» de «no
            # la alcanzas».
            raise ErrorDeCuenta("cuenta_no_encontrada", "Cuenta no encontrada", status=404)

        if fila["tipo"] == "personal":
            # Su credencial no está guardada, y es a propósito: viaja en esta
            # petición y muere con ella.
            if not password:
                raise ErrorDeCuenta(
                    "password_requerida",
                    "Esta es una cuenta personal: su contraseña no se guarda, hay que "
                    "escribirla para trabajar el buzón",
                    status=400,
                )
            secreto = password
        elif fila["auth_tipo"] == "oauth2":
            secreto = await self._access_token(fila)
        else:
            secreto = self._descifrar(
                fila["secreto_cifrado"], fila["secreto_nonce"], fila["id"]
            )

        return Conexion(
            host=fila["host"],
            puerto=fila["puerto"],
            usa_ssl=fila["usa_ssl"],
            direccion=fila["direccion"],
            # El login puede diferir de la dirección; XOAUTH2 en cambio SIEMPRE
            # lleva la dirección, porque identifica al titular del buzón ante el
            # proveedor y no es una credencial de servidor.
            login=fila["usuario_login"] or fila["direccion"],
            auth_tipo="oauth2" if fila["auth_tipo"] == "oauth2" else "password",
            secreto=secreto,
        )

    async def _access_token(self, fila: asyncpg.Record) -> str:
        """Canjea el refresh token de la cuenta por uno de acceso.

        Los de acceso duran una hora; se piden en cada conexión y no se guardan.
        Guardarlos obligaría a razonar sobre su caducidad en todos los caminos, y
        el ahorro sería una petición HTTP por sondeo.
        """
        if not fila["oauth_refresh_cifrado"]:
            raise ErrorDeCuenta(
                "cuenta_sin_autorizar",
                "La cuenta está registrada pero nadie ha autorizado todavía el "
                "acceso al buzón ante el proveedor.",
                status=409,
            )
        refresh = self._descifrar(
            fila["oauth_refresh_cifrado"], fila["oauth_refresh_nonce"], fila["id"]
        )
        secreto_app = self._descifrar(
            fila["app_secreto_cifrado"], fila["app_secreto_nonce"], fila["app_oauth_id"]
        )
        try:
            datos = await oauth_correo.refrescar_token(
                proveedor=fila["proveedor"],
                client_id=fila["client_id"],
                client_secret=secreto_app,
                refresh_token=refresh,
                directorio=fila["azure_directory_id"],
            )
        except oauth_correo.ErrorOAuth as exc:
            raise ErrorDeCuenta(exc.codigo, str(exc), status=502) from exc
        return str(datos["access_token"])

    # -- flujo de consentimiento ------------------------------------------

    async def iniciar_autorizacion(
        self, cuenta_id: uuid.UUID, *, actor: uuid.UUID, es_admin: bool
    ) -> str:
        """Devuelve la URL a la que mandar al administrador para que consienta."""
        fila = await self._repo.obtener_para_conectar(cuenta_id, actor=actor, es_admin=es_admin)
        if fila is None:
            raise ErrorDeCuenta("cuenta_no_encontrada", "Cuenta no encontrada", status=404)
        if fila["auth_tipo"] != "oauth2":
            raise ErrorDeCuenta(
                "cuenta_sin_oauth",
                "Esta cuenta se conecta con contraseña; no hay nada que autorizar",
            )

        app = await self._repo.obtener_app_con_secreto(fila["app_oauth_id"])
        if app is None:
            raise ErrorDeCuenta(
                "app_no_encontrada", "La aplicación OAuth de la cuenta no existe", status=404
            )

        verifier, challenge = oauth_correo.generar_pkce()
        state = oauth_correo.generar_state()
        await self._repo.guardar_state(
            state=state,
            cuenta_id=cuenta_id,
            iniciado_por=actor,
            code_verifier=verifier,
            redirect_uri=app["redirect_uri"],
            minutos=VIGENCIA_STATE_MINUTOS,
        )
        try:
            return oauth_correo.url_autorizacion(
                proveedor=fila["proveedor"],
                client_id=app["client_id"],
                redirect_uri=app["redirect_uri"],
                state=state,
                code_challenge=challenge,
                directorio=app["azure_directory_id"],
                direccion=fila["direccion"],
            )
        except oauth_correo.ErrorOAuth as exc:
            raise ErrorDeCuenta(exc.codigo, str(exc)) from exc

    async def canjear_autorizacion(
        self, cuenta_id: uuid.UUID, *, code: str, state: str, actor: uuid.UUID, es_admin: bool
    ) -> None:
        """Cambia el código por el refresh token y lo guarda cifrado.

        El `state` se consume en el mismo `UPDATE` que lo valida, así que una
        redirección reproducida no encuentra fila y no llega a canjear nada.
        """
        fila = await self._repo.obtener_para_conectar(cuenta_id, actor=actor, es_admin=es_admin)
        if fila is None:
            raise ErrorDeCuenta("cuenta_no_encontrada", "Cuenta no encontrada", status=404)

        pendiente = await self._repo.consumir_state(state, cuenta_id=cuenta_id, actor=actor)
        if pendiente is None:
            # No se dice cuál de las cuatro condiciones falló: distinguir
            # "caducado" de "ya usado" o de "de otra cuenta" le diría a quien
            # prueba estados qué está acertando.
            raise ErrorDeCuenta(
                "state_invalido",
                "La autorización no es válida o ya caducó. Vuelva a iniciarla.",
                status=400,
            )

        app = await self._repo.obtener_app_con_secreto(fila["app_oauth_id"])
        if app is None:
            raise ErrorDeCuenta("app_no_encontrada", "La aplicación OAuth no existe", status=404)
        secreto_app = self._descifrar(
            app["secreto_cifrado"], app["secreto_nonce"], app["id"]
        )
        try:
            datos = await oauth_correo.canjear_codigo(
                proveedor=fila["proveedor"],
                client_id=app["client_id"],
                client_secret=secreto_app,
                code=code,
                redirect_uri=pendiente["redirect_uri"],
                code_verifier=pendiente["code_verifier"],
                directorio=app["azure_directory_id"],
            )
        except oauth_correo.ErrorOAuth as exc:
            raise ErrorDeCuenta(exc.codigo, str(exc), status=502) from exc

        ct, nonce = self._cifrar(str(datos["refresh_token"]), cuenta_id)
        await self._repo.guardar_refresh(cuenta_id, cifrado=ct, nonce=nonce)

    # -- cifrado -----------------------------------------------------------

    def _cifrar(self, secreto: str, fila_id: uuid.UUID) -> tuple[bytes, bytes]:
        try:
            return email_credenciales.cifrar(
                secreto,
                clave_b64=settings.email_encryption_key,
                tenant_slug=self._tenant,
                cuenta_id=str(fila_id),
            )
        except RuntimeError as exc:
            raise ErrorDeCuenta("custodia_no_configurada", str(exc), status=503) from exc

    def _descifrar(self, ct: bytes, nonce: bytes, fila_id: uuid.UUID) -> str:
        try:
            return email_credenciales.descifrar(
                ct,
                nonce=nonce,
                clave_b64=settings.email_encryption_key,
                tenant_slug=self._tenant,
                cuenta_id=str(fila_id),
            )
        except RuntimeError as exc:
            raise ErrorDeCuenta("custodia_no_configurada", str(exc), status=503) from exc
        except InvalidTag as exc:
            # La credencial existe pero no descifra: clave maestra cambiada, o
            # la fila llegó de otra entidad. No se intenta nada más — un
            # reintento «por si acaso» con otra clave sería justo lo que el AAD
            # existe para impedir.
            logger.error("credencial de correo ilegible en cuenta %s", fila_id)
            raise ErrorDeCuenta(
                "credencial_ilegible",
                "La credencial guardada no se puede descifrar. Vuelva a escribirla.",
                status=409,
            ) from exc
