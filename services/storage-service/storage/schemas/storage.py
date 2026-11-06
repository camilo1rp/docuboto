# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    mime_type: str | None
    file_size: int
    sha256: str
    uploaded_at: datetime
    version: int = 1
    is_current: bool = True


class PresignedUrlResponse(BaseModel):
    url: str
    expires_in: int


class IntegrityResponse(BaseModel):
    file_id: uuid.UUID
    expected_sha256: str
    actual_sha256: str
    integrity_ok: bool


class HealthResponse(BaseModel):
    status: str
    database: str
    storage: str


class FileHashesRequest(BaseModel):
    """`POST /api/v1/storage/internal/files/hashes` (E15 T-08).

    Lote y no uno-a-uno: el índice electrónico se regenera al vincular y al
    cerrar, y resolver la huella con una llamada por anexo convertiría el
    cierre de un expediente de cien documentos en cien viajes de red.
    """

    file_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)


class FileHashesResponse(BaseModel):
    """Huellas encontradas, por id de fichero.

    Un id que no aparece **no es un error**: puede estar borrado o no existir.
    Quien pregunta lo declara como «sin huella» en vez de inventar una — la
    fixity se predica de lo que tiene huella, y lo que falta se dice
    (ISO 16363; Ley 594/2000 art. 16).
    """

    hashes: dict[str, str]


class FileProcedenciaRequest(BaseModel):
    """`POST /api/v1/storage/internal/files/procedencia` (B2, ADR-031)."""

    file_id: uuid.UUID


class FileProcedenciaResponse(BaseModel):
    """Quién subió un fichero, su huella y quién lo referencia hoy.

    Lo consume document-service al registrar el acuse de un envío: la constancia de
    entrega solo vale si la subió quien la registra y nadie la usa ya como otra cosa
    (un anexo ajeno no puede pasar por acuse propio). `referencias` vacío = nadie;
    una tabla ausente cuenta como referenciado (`tabla(ausente)`, fail-closed).
    """

    sha256: str | None
    uploaded_by: uuid.UUID | None
    referencias: list[str]


class PurgaDisposicionRequest(BaseModel):
    """`POST /api/v1/storage/internal/purgar-disposicion` (E04, RF-TRD-07 fase 5).

    `expediente_id` no es decorativo: es la llave del **gate WORM**, que se
    evalúa por expediente y no por archivo. Sin él, la purga se saltaría la red
    de seguridad contra la eliminación prematura.

    `motivo` queda en el asiento de auditoría de cada archivo purgado — es lo
    único que después dirá bajo qué acto desapareció ese objeto, porque el
    archivo ya no está para preguntárselo.
    """

    file_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    expediente_id: str = Field(min_length=1, max_length=200)
    motivo: str = Field(min_length=3, max_length=500)
    # Actor imputable del acto irreversible. Opcional en el tipo porque el
    # asiento lo admite, pero quien orquesta SIEMPRE lo manda: un borrado
    # físico sin actor sería un acto sin responsable.
    actor: uuid.UUID | None = None


class PurgaDisposicionResponse(BaseModel):
    """Qué se fue y qué no estaba.

    Se devuelven las dos listas y no un contador: quien orquesta escribe una
    lápida con lo que de verdad ocurrió, y «se purgaron 12 de 14» sin decir
    cuáles no permite afirmar nada.
    """

    purgados: list[uuid.UUID]
    # No es un error: un archivo ya purgado (reintento tras un fallo a medias)
    # es el estado que se buscaba.
    no_encontrados: list[uuid.UUID]


class PurgaHuerfanosRequest(BaseModel):
    """`POST /api/v1/storage/internal/purgar-huerfanos` (AREA-09-14).

    Binarios que una importación fallida subió y que ningún radicado referencia.
    A diferencia de la purga por disposición NO hay expediente ni gate WORM por
    expediente: la seguridad es la comprobación de referencias del tenant entero
    (storage deduplica por SHA-256) y `subido_desde`, que acota el borrado a los
    objetos CREADOS por esa importación: una coincidencia de hash con un objeto
    anterior no se toca nunca.
    """

    file_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    # Identificador de la importación (import_uuid) que queda en el asiento.
    referencia: str = Field(min_length=1, max_length=200)
    motivo: str = Field(min_length=3, max_length=500)
    subido_desde: datetime
    actor: uuid.UUID | None = None

    @field_validator("subido_desde")
    @classmethod
    def _con_zona(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("subido_desde debe llevar zona horaria")
        return v


class PurgaHuerfanoItem(BaseModel):
    file_id: uuid.UUID
    resultado: str  # purgado | no_encontrado | preexistente | referenciado | protegido_worm | error
    detalle: str | None = None


class PurgaHuerfanosResponse(BaseModel):
    """Un resultado POR archivo: quien orquesta decide qué reintentar (`error`) y
    qué dar por resuelto (todo lo demás)."""

    resultados: list[PurgaHuerfanoItem]
