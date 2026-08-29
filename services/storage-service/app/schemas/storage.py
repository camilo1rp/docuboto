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

from pydantic import BaseModel, ConfigDict, Field


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
