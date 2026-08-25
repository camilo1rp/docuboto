# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Adaptador IMAP para las cuentas de correo (E19 Increment 1).

Sustituye a `imap_client.ImaplibClient`, que tenía tres defectos que solo se
manifiestan cuando el buzón es de verdad — es decir, cuando lo trabaja más de
una persona:

**1. Usaba números de secuencia creyendo que eran UID.** `conn.search(...)` y
`conn.store(...)` sin el prefijo `UID` devuelven y aceptan *números de
secuencia*: la posición del mensaje dentro de la carpeta. Esa posición **se
renumera** en cuanto alguien borra o mueve un mensaje. Con dos operadores
trabajando el mismo buzón, la marca se aplicaba al correo equivocado. El campo
se llamaba `uid` y no lo era. Aquí todo va por `conn.uid(...)`.

**2. Ignoraba UIDVALIDITY, que es lo que hace único a un UID.** Un UID solo
identifica un mensaje dentro de una carpeta *mientras* el `UIDVALIDITY` de esa
carpeta no cambie (RFC 3501 §2.3.1.1). Si el servidor recrea la carpeta —una
restauración, una migración de buzón—, `UIDVALIDITY` cambia y **los UID se
reutilizan desde cero**. Guardar solo el UID significa que, tras una
restauración, el correo nº 7 nuevo se confundiría con el nº 7 viejo y se daría
por radicado sin estarlo. Por eso `Carpeta` expone `uidvalidity` y la identidad
de un mensaje es la terna `(carpeta, uidvalidity, uid)` — más el `Message-ID`,
que es único globalmente (RFC 5322 §3.6.4) y sobrevive incluso a que el correo
se mueva de carpeta.

**3. Solo sabía autenticarse con contraseña.** Eso ya no conecta con las dos
plataformas que usan la mayoría de las entidades: Exchange Online retiró la
autenticación básica de IMAP a finales de 2022, y Google Workspace dejó de
aceptar usuario+contraseña —incluidas las contraseñas de aplicación— el
2025-05-01. Sin XOAUTH2 la ingesta no es que vaya lenta: no se conecta.

El adaptador NO decide nada de radicación: trae, lista y mueve. Quién radica y
con qué número es de `DocumentService`.
"""

from __future__ import annotations

import asyncio
import calendar
import imaplib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

# Un nombre de carpeta puede traer cualquier cosa; se cita para el protocolo en
# vez de interpolarse. IMAP no tiene inyección de SQL, pero sí tiene comandos
# separados por CRLF: una carpeta con un salto de línea partiría el comando.
_CRLF = re.compile(r"[\r\n]")

# `* OK [UIDVALIDITY 1234] ...` en la respuesta al SELECT.
_UIDVALIDITY = re.compile(rb"UIDVALIDITY\s+(\d+)", re.IGNORECASE)


def _internaldate(respuesta: bytes) -> datetime | None:
    """Extrae el `INTERNALDATE` de la respuesta a un FETCH.

    Devuelve `None` si el servidor no lo trae, en vez de sustituirlo por la hora
    actual: una fecha de recibo inventada es peor que ninguna, porque nadie
    volvería a mirarla y de ella dependen los plazos del derecho de petición.
    """
    marca = imaplib.Internaldate2tuple(respuesta)
    if marca is None:
        return None
    # `Internaldate2tuple` devuelve una tupla en hora local; se normaliza a UTC,
    # que es como el sistema almacena y compara todas las fechas.
    # `calendar.timegm` y no `time.mktime`: la tupla ya viene normalizada a UTC
    # por `Internaldate2tuple`, así que interpretarla como hora local
    # desplazaría la fecha de recibo tantas horas como el huso del servidor —
    # y en Colombia son cinco, suficientes para cambiar el día de un plazo.
    return datetime.fromtimestamp(calendar.timegm(marca), tz=UTC)


class ErrorDeCorreo(RuntimeError):
    """Fallo al hablar con el servidor de correo, ya traducido a algo legible."""

    def __init__(self, mensaje: str, *, codigo: str = "correo_error") -> None:
        super().__init__(mensaje)
        self.codigo = codigo


@dataclass(frozen=True)
class Conexion:
    """Todo lo necesario para abrir una sesión, ya resuelto.

    El secreto llega en claro **en memoria y para una sola operación**: quien
    construye esta estructura lo acaba de descifrar y no lo retiene. Ver
    `app/core/email_credenciales.py`.
    """

    host: str
    puerto: int
    usa_ssl: bool
    direccion: str
    auth_tipo: str  # 'password' | 'oauth2'
    secreto: str  # contraseña, o access token si auth_tipo == 'oauth2'
    # Con qué se autentica. Suele ser la dirección, pero no siempre: Exchange
    # admite `DOMINIO\usuario` y algunos servidores quieren solo la parte
    # local. Se declara en vez de derivarlo del proveedor —que es lo que hacía
    # el Orfeo legacy, cortando por el «@»— porque adivinar acierta hasta que no
    # acierta, y entonces el error es «credenciales rechazadas».
    login: str = ""


@dataclass(frozen=True)
class Carpeta:
    nombre: str
    uidvalidity: int
    total: int


@dataclass(frozen=True)
class Resumen:
    """Cabecera de un mensaje, SIN el cuerpo.

    La bandeja lista cientos de correos; traerse el cuerpo de todos para
    enseñar remitente y asunto sería descargar el buzón entero en cada refresco.
    El cuerpo se pide por `traer_crudo` cuando alguien abre uno concreto.
    """

    uid: str
    message_id: str
    remitente: str
    asunto: str
    fecha: str
    tiene_adjuntos: bool


def _citar(nombre: str) -> str:
    """Cita un nombre de carpeta para el protocolo.

    Rechaza CR/LF en vez de escaparlos: no hay ningún nombre de carpeta legítimo
    que los lleve, y aceptarlos sería permitir inyectar un comando IMAP.
    """
    if _CRLF.search(nombre):
        raise ErrorDeCorreo("Nombre de carpeta inválido", codigo="carpeta_invalida")
    return '"' + nombre.replace("\\", "\\\\").replace('"', '\\"') + '"'


def cadena_xoauth2(direccion: str, access_token: str) -> bytes:
    """Cadena SASL XOAUTH2 (formato de Google y Microsoft, con \x01 de separador)."""
    return f"user={direccion}\x01auth=Bearer {access_token}\x01\x01".encode()


class ImapAdaptador:
    """Operaciones sobre un buzón. Bloqueante por dentro, `await` por fuera."""

    def __init__(self, conexion: Conexion) -> None:
        self._c = conexion

    # -- sesión ------------------------------------------------------------

    def _abrir(self) -> imaplib.IMAP4:
        try:
            cls = imaplib.IMAP4_SSL if self._c.usa_ssl else imaplib.IMAP4
            conn = cls(self._c.host, self._c.puerto)
        except Exception as exc:  # noqa: BLE001 — cualquier fallo de red es el mismo caso
            raise ErrorDeCorreo(
                f"No se pudo conectar a {self._c.host}:{self._c.puerto}",
                codigo="conexion_fallida",
            ) from exc
        try:
            if self._c.auth_tipo == "oauth2":
                conn.authenticate(
                    "XOAUTH2", lambda _: cadena_xoauth2(self._c.direccion, self._c.secreto)
                )
            else:
                conn.login(self._c.login or self._c.direccion, self._c.secreto)
        except imaplib.IMAP4.error as exc:
            # El mensaje del servidor puede traer la dirección o pistas del
            # token; no se propaga tal cual a la interfaz.
            raise ErrorDeCorreo(
                "El servidor rechazó las credenciales de la cuenta",
                codigo="credenciales_rechazadas",
            ) from exc
        return conn

    def _seleccionar(self, conn: imaplib.IMAP4, carpeta: str, *, solo_lectura: bool) -> int:
        estado, datos = conn.select(_citar(carpeta), readonly=solo_lectura)
        if estado != "OK":
            raise ErrorDeCorreo(
                f"No se pudo abrir la carpeta «{carpeta}»", codigo="carpeta_no_encontrada"
            )
        # El UIDVALIDITY viaja como código en la respuesta sin etiqueta
        # `* OK [UIDVALIDITY 123]`, e imaplib lo guarda ya extraído: devuelve
        # `('UIDVALIDITY', [b'123'])`, **solo el número**, no la línea entera.
        #
        # La primera versión de esto buscaba la palabra «UIDVALIDITY» dentro del
        # contenido y por tanto no encontraba nada nunca — el servidor real lo
        # destapó al primer intento. Un doble de pruebas habría devuelto la
        # forma que yo suponía y el defecto habría llegado a producción, que es
        # exactamente por qué esto se prueba contra un IMAP de verdad.
        #
        # Se aceptan las dos formas: el número suelto (lo que hacen imaplib y
        # los servidores conformes) y la línea completa, por si algún servidor
        # la entrega sin analizar. `response()` CONSUME la respuesta, así que se
        # llama una sola vez.
        for parte in conn.response("UIDVALIDITY")[1] or []:
            if not isinstance(parte, (bytes, bytearray)):
                continue
            crudo = bytes(parte).strip()
            if crudo.isdigit():
                return int(crudo)
            m = _UIDVALIDITY.search(crudo)
            if m:
                return int(m.group(1))
        # Un servidor sin UIDVALIDITY incumple RFC 3501; no se inventa un valor
        # por defecto, porque un 0 fijo haría creer que la identidad es estable
        # cuando no se sabe.
        raise ErrorDeCorreo(
            "El servidor no informa UIDVALIDITY de la carpeta", codigo="uidvalidity_ausente"
        )

    # -- operaciones -------------------------------------------------------

    def _probar_sync(self) -> str:
        conn = self._abrir()
        try:
            return self._c.direccion
        finally:
            self._cerrar(conn)

    def _listar_carpetas_sync(self) -> list[str]:
        """Nombres de carpeta del buzón, para que la pantalla los ofrezca.

        Sin esto, configurar la carpeta de filtrado sería escribirla a mano y
        descubrir la errata cuando la ingesta no trae nada.
        """
        conn = self._abrir()
        try:
            estado, datos = conn.list()
            if estado != "OK":
                raise ErrorDeCorreo("No se pudieron listar las carpetas", codigo="list_fallido")
            nombres: list[str] = []
            for linea in datos or []:
                if not isinstance(linea, bytes):
                    continue
                texto = linea.decode("utf-7", errors="replace")
                # `(\HasNoChildren) "/" "INBOX/Radicar"` → el último token citado.
                partes = texto.split(' "')
                if len(partes) >= 2:
                    nombres.append(partes[-1].rstrip('"'))
            return nombres
        finally:
            self._cerrar(conn)

    def _cerrar(self, conn: imaplib.IMAP4) -> None:
        """Cierra sin convertir un fallo de despedida en un fallo de la operación."""
        try:
            conn.logout()
        except Exception:  # noqa: BLE001 — ya tenemos el resultado; el adiós da igual
            pass

    def _listar_uids_sync(self, carpeta: str) -> tuple[int, list[int]]:
        """`(uidvalidity, uids)` de la carpeta. No descarga ningún mensaje.

        Se listan primero los identificadores y solo después se traen los que
        faltan: la carpeta de filtrado de una ventanilla puede acumular meses de
        correspondencia, y descargarla entera en cada sondeo para descubrir que
        no hay nada nuevo sería absurdo.
        """
        conn = self._abrir()
        try:
            uidvalidity = self._seleccionar(conn, carpeta, solo_lectura=True)
            estado, datos = conn.uid("SEARCH", "ALL")
            if estado != "OK":
                raise ErrorDeCorreo(
                    f"No se pudo listar la carpeta «{carpeta}»", codigo="search_fallido"
                )
            crudo = datos[0] if datos and isinstance(datos[0], bytes) else b""
            return uidvalidity, [int(x) for x in crudo.split() if x.isdigit()]
        finally:
            self._cerrar(conn)

    def _traer_sync(self, carpeta: str, uid: int) -> tuple[bytes, datetime | None]:
        """El mensaje ÍNTEGRO en su formato original (RFC 822).

        Se conserva tal cual, sin convertirlo a texto: la Ley 527/1999 art. 12
        exige guardar el mensaje «en el formato en que se haya generado, enviado
        o recibido», con su origen, destino, fecha y hora. Una transcripción a
        texto plano —que es lo que hacía la implementación anterior— pierde las
        cabeceras que prueban esas cuatro cosas.

        `BODY.PEEK[]` y no `BODY[]`: leerlo NO debe marcarlo como visto. La marca
        `\\Seen` es compartida por todos los clientes del buzón, y tocarla aquí
        cambiaría lo que ven las personas que trabajan ese correo desde su
        gestor habitual.

        **Devuelve además el `INTERNALDATE`, y esa es la fecha que cuenta.** No
        la cabecera `Date`, que es cuándo dice el REMITENTE que lo envió: la pone
        su cliente de correo, depende de su reloj, puede faltar —el servidor de
        pruebas lo demostró al primer intento: `Date` llegó vacía— y, sobre todo,
        **la controla él**. Un ciudadano podría fecharla en el pasado para ganar
        plazo en un derecho de petición.

        El `INTERNALDATE` lo pone el servidor de correo de la ENTIDAD cuando el
        mensaje entra en el buzón, y es literalmente «la fecha y hora de recibo»
        de la que la Circular Externa AGN 001/2020 exige dejar constancia. Es el
        dato que fija el inicio de los plazos.
        """
        conn = self._abrir()
        try:
            self._seleccionar(conn, carpeta, solo_lectura=True)
            estado, datos = conn.uid("FETCH", str(uid), "(INTERNALDATE BODY.PEEK[])")
            if estado != "OK" or not datos:
                raise ErrorDeCorreo(
                    f"No se pudo traer el mensaje {uid}", codigo="fetch_fallido"
                )
            for parte in datos:
                if isinstance(parte, tuple) and isinstance(parte[1], (bytes, bytearray)):
                    # El prefijo de la respuesta trae el INTERNALDATE; imaplib
                    # sabe interpretarlo, pero devuelve `None` si el servidor no
                    # lo incluye — y entonces se dice, no se inventa una fecha.
                    encabezado = parte[0] if isinstance(parte[0], (bytes, bytearray)) else b""
                    return bytes(parte[1]), _internaldate(bytes(encabezado))
            raise ErrorDeCorreo(
                f"El servidor no devolvió el cuerpo del mensaje {uid}",
                codigo="fetch_sin_cuerpo",
            )
        finally:
            self._cerrar(conn)

    def _mover_sync(self, carpeta: str, uid: int, destino: str) -> None:
        """Mueve un mensaje a otra carpeta, creándola si no existe.

        Es lo que hace que un correo radicado **salga de la vista de todos a la
        vez**. Frente a marcar `\\Seen`: la marca es compartida, cualquiera puede
        quitarla desde su gestor de correo, y no distingue «lo leí» de «lo
        radiqué».

        `UID MOVE` (RFC 6851) si el servidor lo anuncia; si no, la secuencia
        clásica COPY + marcar borrado + EXPUNGE. El respaldo importa: hay
        servidores IMAP en producción —y paneles de hosting— que no implementan
        MOVE, y sin él la carpeta de trabajo nunca se vaciaría.
        """
        conn = self._abrir()
        try:
            self._seleccionar(conn, carpeta, solo_lectura=False)
            # Crear la carpeta destino es idempotente en la práctica: si existe,
            # el servidor responde NO y se sigue adelante.
            try:
                conn.create(_citar(destino))
            except imaplib.IMAP4.error:
                pass

            if b"MOVE" in (conn.capabilities or ()):
                estado, _ = conn.uid("MOVE", str(uid), _citar(destino))
                if estado == "OK":
                    return
            estado, _ = conn.uid("COPY", str(uid), _citar(destino))
            if estado != "OK":
                raise ErrorDeCorreo(
                    f"No se pudo copiar el mensaje a «{destino}»", codigo="copy_fallido"
                )
            conn.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
            conn.expunge()
        finally:
            self._cerrar(conn)

    async def probar(self) -> str:
        return await asyncio.to_thread(self._probar_sync)

    async def listar_uids(self, carpeta: str) -> tuple[int, list[int]]:
        return await asyncio.to_thread(self._listar_uids_sync, carpeta)

    async def traer(self, carpeta: str, uid: int) -> tuple[bytes, datetime | None]:
        """`(mensaje íntegro, fecha de recibo en el buzón)`."""
        return await asyncio.to_thread(self._traer_sync, carpeta, uid)

    async def mover(self, carpeta: str, uid: int, destino: str) -> None:
        await asyncio.to_thread(self._mover_sync, carpeta, uid, destino)

    async def listar_carpetas(self) -> list[str]:
        return await asyncio.to_thread(self._listar_carpetas_sync)
