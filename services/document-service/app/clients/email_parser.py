# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Lectura de un mensaje de correo para la bandeja (E19 Increment 2).

Sustituye a `imap_client.parse_email`, que servía para la ruta heredada pero se
quedaba corto en dos cosas que aquí importan: no extraía el `Message-ID` —la
identidad estable del mensaje— y de los adjuntos solo guardaba **el nombre**,
tirando los bytes. Radicar una comunicación sin sus soportes incumple el Acuerdo
060/2001 art. 2, que incluye «Anexos» entre los datos mínimos del registro.
"""

from __future__ import annotations

import email
import hashlib
import logging
import re
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr

logger = logging.getLogger(__name__)

#: Topes del correo entrante. Lo escribe cualquiera de fuera y esto lo decodifica
#: TODO a memoria; sin ellos, un solo mensaje detiene la ingesta de todos los
#: buzones de la entidad. El cap de mensajes por vuelta no ayuda: acota cuántos
#: se leen, no lo que cada uno pesa. Los valores acompañan a los de la ventanilla
#: pública, que es la otra puerta por la que entra un documento sin credenciales.
MAX_ADJUNTOS_POR_CORREO = 20
MAX_BYTES_POR_ADJUNTO = 10 * 1024 * 1024
MAX_BYTES_TOTAL_CORREO = 25 * 1024 * 1024

# Nombre de fichero seguro para storage: sin rutas ni caracteres de control.
# No se "limpia" un nombre peligroso conservando su forma — se sustituye, porque
# un nombre que casi funciona es más difícil de detectar que uno evidente.
_NOMBRE_INSEGURO = re.compile(r"[^\w\s.,()\-\[\]áéíóúüñÁÉÍÓÚÜÑ]", re.UNICODE)
_MAX_NOMBRE = 200


@dataclass(frozen=True)
class Adjunto:
    nombre: str
    contenido: bytes
    tipo_mime: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.contenido).hexdigest()


@dataclass
class CorreoLeido:
    message_id: str
    remitente_email: str
    remitente_nombre: str
    asunto: str
    cuerpo: str
    adjuntos: list[Adjunto] = field(default_factory=list)


def _decodificar(valor: str | None) -> str:
    if not valor:
        return ""
    try:
        return str(make_header(decode_header(valor)))
    except Exception:  # noqa: BLE001 — una cabecera rota no debe perder el correo
        return valor


def _nombre_seguro(bruto: str, *, respaldo: str) -> str:
    """Nombre utilizable para guardar el anexo.

    Un nombre que trae separadores de ruta o `..` **se sustituye entero**, no se
    maquilla: `../../etc/passwd` convertido en `.._.._etc_passwd` ya no compone
    una ruta, pero conserva la forma del original y es más difícil de reconocer
    como manipulado que un `anexo-3.pdf` evidente. Y un nombre así no es un
    descuido de quien envía: es una señal.
    """
    nombre = _decodificar(bruto).strip()
    if not nombre:
        return respaldo
    if any(marca in nombre for marca in ("/", "\\", "..")) or nombre.startswith("."):
        return respaldo
    limpio = _NOMBRE_INSEGURO.sub("_", nombre).strip()
    if not limpio or set(limpio) <= {".", "_", " "}:
        return respaldo
    return limpio[:_MAX_NOMBRE]


def _es_adjunto(parte: Message) -> bool:
    """Un adjunto de verdad, no una parte del cuerpo.

    Se mira `Content-Disposition` además del nombre: una imagen incrustada en un
    correo HTML (`inline`, con `Content-ID`) es parte del cuerpo, no un soporte
    documental, y radicarla como anexo llenaría el expediente de logotipos de
    firma.
    """
    disposicion = (parte.get("Content-Disposition") or "").lower()
    if "attachment" in disposicion:
        return True
    return bool(parte.get_filename()) and "inline" not in disposicion


def _cuerpo(msg: Message) -> str:
    """Texto plano del mensaje. Si solo viene HTML, se devuelve tal cual.

    No se convierte el HTML a texto aquí: el cuerpo se guarda como observación
    del radicado y quien lo lea necesita el contenido íntegro. El original
    completo queda conservado aparte (Ley 527/1999 art. 12).
    """
    if not msg.is_multipart():
        return _texto_de(msg)
    plano = ""
    html = ""
    for parte in msg.walk():
        if parte.is_multipart() or _es_adjunto(parte):
            continue
        tipo = parte.get_content_type()
        if tipo == "text/plain" and not plano:
            plano = _texto_de(parte)
        elif tipo == "text/html" and not html:
            html = _texto_de(parte)
    return plano or html


def _texto_de(parte: Message) -> str:
    carga = parte.get_payload(decode=True)
    if not isinstance(carga, bytes):
        return ""
    juego = parte.get_content_charset() or "utf-8"
    return carga.decode(juego, errors="replace").strip()


def leer(crudo: bytes, *, respaldo_id: str) -> CorreoLeido:
    """Convierte un mensaje RFC 822 en lo que la bandeja necesita.

    `respaldo_id` se usa cuando el correo no trae `Message-ID`. Es raro pero
    posible —lo produce algún cliente mal hecho—, y **no puede quedar vacío**:
    es la clave que impide que el mismo correo entre dos veces en la bandeja. Se
    sintetiza a partir del localizador (cuenta, carpeta, uidvalidity, uid), que
    para un mismo mensaje es estable mientras no cambie el `UIDVALIDITY`.
    """
    msg = email.message_from_bytes(crudo)
    nombre, direccion = parseaddr(msg.get("From", ""))

    adjuntos: list[Adjunto] = []
    total_bytes = 0
    if msg.is_multipart():
        for indice, parte in enumerate(msg.walk(), start=1):
            if parte.is_multipart() or not _es_adjunto(parte):
                continue
            if len(adjuntos) >= MAX_ADJUNTOS_POR_CORREO:
                logger.warning(
                    "Correo con más de %d adjuntos: se ignoran los restantes.",
                    MAX_ADJUNTOS_POR_CORREO,
                )
                break
            contenido = parte.get_payload(decode=True)
            if not isinstance(contenido, bytes) or not contenido:
                continue
            if len(contenido) > MAX_BYTES_POR_ADJUNTO:
                logger.warning(
                    "Adjunto de %d bytes ignorado: supera el máximo admitido.", len(contenido)
                )
                continue
            total_bytes += len(contenido)
            if total_bytes > MAX_BYTES_TOTAL_CORREO:
                logger.warning("El conjunto de adjuntos supera el máximo; se corta aquí.")
                break
            adjuntos.append(
                Adjunto(
                    nombre=_nombre_seguro(
                        parte.get_filename() or "", respaldo=f"anexo-{indice}"
                    ),
                    contenido=contenido,
                    tipo_mime=parte.get_content_type() or "application/octet-stream",
                )
            )

    return CorreoLeido(
        message_id=(msg.get("Message-ID") or "").strip() or respaldo_id,
        remitente_email=direccion,
        remitente_nombre=_decodificar(nombre) or direccion,
        asunto=_decodificar(msg.get("Subject")),
        cuerpo=_cuerpo(msg),
        adjuntos=adjuntos,
    )
