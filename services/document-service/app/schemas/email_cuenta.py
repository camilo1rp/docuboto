# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Esquemas de cuentas de correo y aplicaciones OAuth (E19 Increment 1).

**Ningún esquema de respuesta lleva un secreto.** Ni la contraseña, ni el
*client secret*, ni el *refresh token*: solo si están puestos. Un endpoint de
lectura que devolviera la credencial la esparciría por los registros del
servidor, el navegador y la caché de la interfaz — y el motivo por el que se
cifra en reposo desaparecería por la puerta de la API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


@dataclass(frozen=True)
class Preset:
    """Conexión conocida de un proveedor.

    Un `dataclass` y no un `dict` suelto: con un diccionario de valores mixtos
    el tipo de cada campo se pierde y `int(preset["puerto"])` deja de poder
    comprobarse — el gate de tipos lo señaló, y tenía razón.
    """

    host: str
    puerto: int
    usa_ssl: bool


# Se aplican al crear si el llamante no da host/puerto, y quedan escritos en la
# fila: si mañana cambia el preset, una cuenta que ya funcionaba no se
# reconfigura sola a su espalda.
PRESETS: dict[str, Preset] = {
    "gmail": Preset(host="imap.gmail.com", puerto=993, usa_ssl=True),
    "microsoft": Preset(host="outlook.office365.com", puerto=993, usa_ssl=True),
}

# Proveedores que EXIGEN OAuth2, y por qué. No es una preferencia: con
# usuario y contraseña estas dos plataformas rechazan la conexión.
SOLO_OAUTH2 = {
    "microsoft": (
        "Microsoft 365 no acepta usuario y contraseña por IMAP desde que "
        "Exchange Online retiró la autenticación básica (2022)"
    ),
    "gmail": (
        "Google Workspace no acepta usuario y contraseña por IMAP desde el "
        "1 de mayo de 2025, ni siquiera contraseñas de aplicación"
    ),
}


class AppOAuthCrear(BaseModel):
    """Aplicación que la ENTIDAD registra en su propio Google Cloud o Azure AD."""

    proveedor: str = Field(pattern="^(gmail|microsoft)$")
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: str = Field(min_length=1)
    azure_directory_id: str | None = Field(default=None, max_length=255)
    redirect_uri: str = Field(min_length=1, max_length=500)
    notas: str | None = None

    @model_validator(mode="after")
    def _directorio_segun_proveedor(self) -> AppOAuthCrear:
        """Microsoft necesita el directorio; Google no lo usa.

        Se comprueba aquí además de en la base de datos para poder decir cuál
        es el campo y qué significa: «azure_directory_id» no se adivina, y un
        error de restricción de la base sería un 500 sin explicación.
        """
        if self.proveedor == "microsoft" and not self.azure_directory_id:
            raise ValueError(
                "Microsoft 365 exige el identificador del directorio de Azure AD "
                "(el «Directory (tenant) ID» de la aplicación registrada)"
            )
        if self.proveedor == "gmail" and self.azure_directory_id:
            raise ValueError("azure_directory_id es de Microsoft; Google no lo usa")
        return self


class AppOAuthResponse(BaseModel):
    id: UUID
    proveedor: str
    client_id: str
    azure_directory_id: str | None
    redirect_uri: str
    notas: str | None
    tiene_secreto: bool
    created_at: datetime


class CuentaCrear(BaseModel):
    nombre: str = Field(min_length=1, max_length=200)
    tipo: str = Field(pattern="^(institucional|personal)$")
    proveedor: str = Field(pattern="^(imap|gmail|microsoft)$")
    direccion: str = Field(min_length=3, max_length=320)
    # Con qué se autentica, si el servidor no acepta la dirección completa.
    # Exchange admite `DOMINIO\usuario`; algunos Zimbra y paneles de hosting
    # quieren solo la parte local. Vacío = usar la dirección.
    usuario_login: str | None = Field(default=None, max_length=320)

    host: str | None = Field(default=None, max_length=255)
    puerto: int | None = Field(default=None, ge=1, le=65535)
    usa_ssl: bool | None = None

    auth_tipo: str = Field(pattern="^(password|oauth2)$")
    # Solo para cuentas institucionales con contraseña. Nunca se devuelve.
    password: str | None = None
    app_oauth_id: UUID | None = None

    carpeta_origen: str = Field(default="INBOX", min_length=1, max_length=255)
    carpeta_radicados: str = Field(default="Radicados", min_length=1, max_length=255)

    dependencia_destino: str | None = Field(default=None, max_length=255)
    tipo_documental_id: UUID | None = None
    modo: str = Field(default="asistida", pattern="^(asistida|automatica)$")
    propietario_user_id: UUID | None = None
    # Funcionario bajo cuya autoría radica una cuenta automática (Ac. 060/2001
    # arts. 2 y 13). Sin él no hay «responsable del trámite» ni autoría para los
    # anexos, que storage exige.
    responsable_user_id: UUID | None = None
    # Constancia al remitente. Opt-in: la Ley 1755/2015 art. 15 la exige a
    # PETICIÓN del interesado, no de oficio.
    acuse_al_remitente: bool = False

    @field_validator("direccion")
    @classmethod
    def _parece_una_direccion(cls, v: str) -> str:
        if "@" not in v or v.startswith("@") or v.endswith("@"):
            raise ValueError("La dirección de correo no es válida")
        return v.strip()

    @model_validator(mode="after")
    def _coherencia(self) -> CuentaCrear:
        """Las mismas reglas que la base de datos, dichas con palabras.

        La base es la que garantiza el invariante —ahí no se puede colar nadie—
        pero un `CHECK` violado sale como un 500 opaco. Aquí se explica qué
        falta y por qué, que es lo que el administrador necesita para
        arreglarlo.
        """
        if self.carpeta_origen == self.carpeta_radicados:
            raise ValueError(
                "La carpeta de origen y la de radicados no pueden ser la misma: "
                "el correo se movería a donde ya estaba y volvería a ofrecerse"
            )

        if self.tipo == "personal" and not self.propietario_user_id:
            raise ValueError("Una cuenta personal necesita el usuario al que pertenece")
        if self.tipo == "institucional" and self.propietario_user_id:
            raise ValueError(
                "Una cuenta institucional no tiene propietario: es de la entidad, y "
                "quién puede trabajarla se declara asignando funcionarios"
            )
        if self.tipo == "personal" and self.password:
            raise ValueError(
                "La contraseña de una cuenta personal no se guarda. Se pide al "
                "funcionario cuando entra a su buzón y vive solo en su sesión"
            )

        if self.modo == "automatica" and not self.responsable_user_id:
            raise ValueError(
                "Una cuenta que radica automáticamente necesita un funcionario "
                "responsable: es quien responde de lo que entre por ese buzón sin "
                "revisión previa, y bajo cuya autoría se registran los anexos"
            )
        if self.auth_tipo == "oauth2" and not self.app_oauth_id:
            raise ValueError(
                "Una cuenta OAuth2 necesita la aplicación que la entidad registró "
                "ante su proveedor de correo"
            )
        if self.auth_tipo == "password":
            if self.app_oauth_id:
                raise ValueError("app_oauth_id es solo para cuentas OAuth2")
            if self.tipo == "institucional" and not self.password:
                raise ValueError("Una cuenta institucional con contraseña necesita la contraseña")
            motivo = SOLO_OAUTH2.get(self.proveedor)
            if motivo:
                # Se rechaza en vez de dejar crear una cuenta que fallará en el
                # primer sondeo con «credenciales rechazadas», que haría buscar
                # el problema en la contraseña y no en la plataforma.
                raise ValueError(f"{motivo}. Registre una aplicación OAuth2 y use auth_tipo=oauth2")
        return self

    def conexion_efectiva(self) -> tuple[str, int, bool]:
        """Host, puerto y SSL, con el preset del proveedor como respaldo."""
        preset = PRESETS.get(self.proveedor)
        host = self.host or (preset.host if preset else "")
        if not host:
            raise ValueError("Una cuenta IMAP genérica necesita el servidor (host)")
        puerto = self.puerto or (preset.puerto if preset else 993)
        if self.usa_ssl is not None:
            ssl = self.usa_ssl
        else:
            ssl = preset.usa_ssl if preset else True
        return host, puerto, ssl


class CuentaActualizar(BaseModel):
    """Edición parcial. Solo viajan las claves presentes."""

    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    host: str | None = Field(default=None, max_length=255)
    puerto: int | None = Field(default=None, ge=1, le=65535)
    usa_ssl: bool | None = None
    password: str | None = None
    carpeta_origen: str | None = Field(default=None, min_length=1, max_length=255)
    carpeta_radicados: str | None = Field(default=None, min_length=1, max_length=255)
    dependencia_destino: str | None = Field(default=None, max_length=255)
    tipo_documental_id: UUID | None = None
    modo: str | None = Field(default=None, pattern="^(asistida|automatica)$")
    responsable_user_id: UUID | None = None
    acuse_al_remitente: bool | None = None
    activa: bool | None = None


class CuentaResponse(BaseModel):
    id: UUID
    nombre: str
    tipo: str
    proveedor: str
    direccion: str
    usuario_login: str | None
    host: str
    puerto: int
    usa_ssl: bool
    auth_tipo: str
    app_oauth_id: UUID | None
    carpeta_origen: str
    carpeta_radicados: str
    dependencia_destino: str | None
    tipo_documental_id: UUID | None
    modo: str
    activa: bool
    acuse_al_remitente: bool
    responsable: UUID | None
    propietario_user_id: UUID | None
    ultimo_sondeo_at: datetime | None
    ultimo_error: str | None
    created_at: datetime

    # Nunca el secreto; solo si está puesto. Una cuenta OAuth2 sin autorización
    # todavía es un estado normal —se acaba de crear y falta consentir—, y la
    # interfaz tiene que poder distinguirlo de una que ya funciona.
    tiene_credencial: bool
    funcionarios: list[UUID] = Field(default_factory=list)


class PruebaConexion(BaseModel):
    """Resultado de probar una cuenta, sin efectos sobre el buzón."""

    ok: bool
    detalle: str
    carpetas: list[str] = Field(default_factory=list)
