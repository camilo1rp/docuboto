# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cliente interno del storage-service para mediar la descarga de anexos.

Cierra la fuga de no-read-up (RF-SEG-08) en `GET /storage/files/{id}/download`:
ese endpoint devolvía una presigned URL de MinIO SIN validar el
`nivel_seguridad` del radicado dueño del anexo. document-service es quien SÍ
conoce esa clasificación (`radicados.nivel_seguridad`), así que MEDIA la
descarga: resuelve el clearance del llamante (ver
`app/routers/documents.py::download_anexo`) y solo entonces pide los BYTES a
storage-service — NUNCA una presigned URL, que el cliente podría reenviar sin
pasar de nuevo por el gate.

storage-service protege el endpoint de bytes con `X-Internal-Token` (D-02,
mismo patrón que `knowledge-service./ingest`); este cliente es el único lugar
de document-service que conoce ese secreto. document-service NUNCA habla con
MinIO directamente — mismo aislamiento que archive-service/signature-service.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings

_CHUNK_SIZE = 65536


class StorageContentError(Exception):
    """storage-service no respondió (o respondió con error) al pedir los bytes."""


@dataclass(frozen=True)
class ArchivoSubido:
    """Lo que devuelve storage al guardar un fichero, ya traducido.

    Existe porque devolver el JSON crudo obligaba a cada llamante a adivinar el
    nombre de la clave, y **dos adivinaron mal la misma**: el importador (E11
    INT-05) y la ventanilla pública leían `result["file_id"]` cuando el contrato
    (`FileResponse`) expone `id`. Los dos tests lo tapaban porque su mock
    devolvía la clave inventada: comprobaban que el código coincide con la
    creencia de quien lo escribió, no con el servicio.

    Un único punto traduce el formato de cable, y si la clave cambia falla aquí
    —en voz alta y una sola vez— en vez de en cada llamante y en producción.
    """

    file_id: uuid.UUID
    sha256: str | None = None
    file_size: int | None = None

    @classmethod
    def desde_respuesta(cls, cuerpo: dict) -> ArchivoSubido:
        try:
            crudo = cuerpo["id"]
        except KeyError as exc:  # pragma: no cover — contrato roto, no dato de usuario
            raise StorageContentError(
                f"storage-service devolvió una respuesta sin 'id': {sorted(cuerpo)}"
            ) from exc
        return cls(
            file_id=uuid.UUID(str(crudo)),
            sha256=cuerpo.get("sha256"),
            file_size=cuerpo.get("file_size"),
        )


class StorageContentStream:
    """Envoltorio del stream HTTP hacia storage-service.

    Mantiene abiertos el `httpx.AsyncClient` y la respuesta durante la vida de
    la `StreamingResponse` del router; `aclose()` libera ambos. Trade-off MVP:
    una conexión httpx dedicada por descarga (sin pool compartido) — igual que
    los demás clients del servicio (`tenant_client.py`). El streaming en sí ES
    real de punta a punta: los bytes del anexo NUNCA se acumulan enteros en
    memoria de document-service (a diferencia, por ejemplo, de
    `signature-service.app.clients.storage_client.upload_signed_xml`, que sí
    carga el contenido completo por tratarse de un XML de índice, del orden de
    kilobytes).
    """

    def __init__(self, client: httpx.AsyncClient, response: httpx.Response) -> None:
        self._client = client
        self._response = response

    @property
    def content_type(self) -> str | None:
        return self._response.headers.get("content-type")

    def aiter_bytes(self) -> AsyncIterator[bytes]:
        return self._response.aiter_bytes(_CHUNK_SIZE)

    async def aclose(self) -> None:
        await self._response.aclose()
        await self._client.aclose()


async def upload_bytes(
    *,
    tenant_slug: str,
    filename: str,
    mime_type: str | None,
    data: bytes,
    uploaded_by: uuid.UUID | None = None,
) -> ArchivoSubido:
    """Sube `data` a storage-service (`POST /upload`) y devuelve el registro del
    fichero (`file_id`, `sha256`, …). Lo usa la IMPORTACIÓN (E11/INT-05) para
    persistir los binarios del paquete. Va scoped al `tenant_slug` del llamante →
    el objeto aterriza en el bucket de ESE tenant. Lanza `StorageContentError`
    ante fallo de red o HTTP != 2xx (el servicio de import lo traduce a error del
    ítem sin dejar estado parcial en BD)."""
    headers = {"X-Tenant-Slug": tenant_slug}
    if uploaded_by is not None:
        headers["X-User-Id"] = str(uploaded_by)
    files = {"file": (filename, data, mime_type or "application/octet-stream")}
    async with httpx.AsyncClient(base_url=settings.storage_service_url, timeout=60.0) as client:
        try:
            resp = await client.post("/api/v1/storage/upload", headers=headers, files=files)
        except httpx.RequestError as exc:
            raise StorageContentError(f"storage-service inalcanzable: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise StorageContentError(
            f"storage-service → HTTP {resp.status_code} al subir '{filename}': "
            f"{resp.text[:300]!r}"
        )
    return ArchivoSubido.desde_respuesta(resp.json())


async def open_content_stream(
    *, tenant_slug: str, file_id: uuid.UUID
) -> StorageContentStream | None:
    """Abre un stream de los bytes de `file_id`. `None` si storage-service
    responde 404 (objeto purgado/inexistente pese a que el anexo sigue
    referenciado — no debería ocurrir en operación normal, pero el llamante
    lo traduce al mismo 404 neutro que cualquier otro camino de denegación).

    Lanza `StorageContentError` ante cualquier otro fallo (red o HTTP != 200):
    el router lo traduce en 503, nunca en un 200 con contenido parcial/vacío.
    """
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.storage_internal_token,
    }
    client = httpx.AsyncClient(base_url=settings.storage_service_url, timeout=30.0)
    try:
        request = client.build_request(
            "GET", f"/api/v1/storage/files/{file_id}/content", headers=headers
        )
        response = await client.send(request, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        raise StorageContentError(f"storage-service inalcanzable: {exc}") from exc

    if response.status_code == 404:
        await response.aclose()
        await client.aclose()
        return None
    if response.status_code != 200:
        body = await response.aread()
        await response.aclose()
        await client.aclose()
        raise StorageContentError(
            f"storage-service → HTTP {response.status_code} al pedir el contenido "
            f"de {file_id}: {body[:300]!r}"
        )

    return StorageContentStream(client, response)


async def upload_anexo_externo(
    *, tenant_slug: str, filename: str, mime_type: str | None, data: bytes
) -> ArchivoSubido:
    """Sube un anexo llegado por la VENTANILLA PÚBLICA, sin usuario detrás.

    No usa `/upload` porque ese endpoint exige `PERM_RADI` y quien radica por la
    ventanilla no tiene identidad en el sistema; ni `/internal/artefacto`, que
    aplica la lista de formatos de los artefactos que produce el SISTEMA (admite
    XML) y no la de anexos. Va a `/internal/anexo-externo`, sellado con
    `X-Internal-Token` y con la lista de anexos (RF-DIG-04).

    No se inventa un `uploaded_by`: atribuir el fichero a un usuario del sistema
    falsearía la traza de quién lo aportó.
    """
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.storage_internal_token,
    }
    files = {"file": (filename, data, mime_type or "application/octet-stream")}
    async with httpx.AsyncClient(base_url=settings.storage_service_url, timeout=60.0) as client:
        try:
            resp = await client.post(
                "/api/v1/storage/internal/anexo-externo", headers=headers, files=files
            )
        except httpx.RequestError as exc:
            raise StorageContentError(f"storage-service inalcanzable: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise StorageContentError(
            f"storage-service → HTTP {resp.status_code} al subir el anexo "
            f"'{filename}': {resp.text[:300]!r}"
        )
    return ArchivoSubido.desde_respuesta(resp.json())


async def upload_artefacto(
    *, tenant_slug: str, filename: str, mime_type: str, data: bytes
) -> ArchivoSubido:
    """Sube un artefacto que produce el SISTEMA, no un anexo aportado.

    Lo usa la bandeja de correo (E19) para conservar el mensaje original en su
    formato (`message/rfc822`, Ley 527/1999 art. 12). Va a
    `/internal/artefacto`, sellado con `X-Internal-Token`, que aplica la lista de
    formatos del sistema en vez de la de anexos (RF-DIG-04).

    La distinción no es burocrática: admitir `.eml` como anexo dejaría subir
    correos completos —con sus adjuntos incrustados— por la puerta que valida
    formatos de anexo, y esa puerta la alcanza cualquiera con `PERM_RADI`.
    """
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.storage_internal_token,
    }
    files = {"file": (filename, data, mime_type)}
    async with httpx.AsyncClient(base_url=settings.storage_service_url, timeout=60.0) as client:
        try:
            resp = await client.post(
                "/api/v1/storage/internal/artefacto", headers=headers, files=files
            )
        except httpx.RequestError as exc:
            raise StorageContentError(f"storage-service inalcanzable: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise StorageContentError(
            f"storage-service → HTTP {resp.status_code} al guardar el artefacto "
            f"'{filename}': {resp.text[:300]!r}"
        )
    return ArchivoSubido.desde_respuesta(resp.json())


# ── Generación del documento de salida (E23, RF-PRO-03) ────────────────────


class RenderNoDisponibleError(Exception):
    """Este despliegue no tiene renderizador (503 de storage-service).

    Se separa de `RenderFallidoError` a propósito: son dos problemas con dos
    soluciones distintas —configurar `RENDER_ENGINE` frente a mirar por qué un
    cuerpo concreto no se deja pintar— y colapsarlos manda a depurar el sitio
    equivocado. Es la misma separación que hace el propio `MigracionOutcome`.
    """


class RenderFallidoError(Exception):
    """El renderizador existe y no pudo producir ESTE documento (422)."""


@dataclass(frozen=True)
class DocumentoRendido:
    """El documento generado, ya decodificado.

    Lleva `formato` y `herramienta` porque los dos van a la traza de
    procedencia: dentro de diez años, saber con qué se produjo un documento de
    archivo es parte de poder sostener que es el que se produjo.
    """

    contenido: bytes
    formato: str
    herramienta: str


async def render_documento(
    *,
    tenant_slug: str,
    cuerpo_html: str,
    pagina: dict[str, Any],
    perfil: str | None = None,
) -> DocumentoRendido:
    """Pide a storage-service el PDF/A del cuerpo ya combinado.

    Sellado con `X-Internal-Token`: la autorización del funcionario ya ocurrió
    aquí, antes de componer el cuerpo. Un `503` se traduce a
    `RenderNoDisponibleError` y NO se degrada a «radicar sin documento» — un
    oficio que la entidad cree haber producido y no existe es peor que un error
    visible, porque el fallo aparece cuando el ciudadano reclama.
    """
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.storage_internal_token,
    }
    cuerpo: dict[str, Any] = {"cuerpo_html": cuerpo_html, "pagina": pagina}
    if perfil:
        cuerpo["perfil"] = perfil
    async with httpx.AsyncClient(base_url=settings.storage_service_url, timeout=120.0) as client:
        try:
            resp = await client.post(
                "/api/v1/render/documento-internal", headers=headers, json=cuerpo
            )
        except httpx.RequestError as exc:
            raise RenderNoDisponibleError(f"storage-service inalcanzable: {exc}") from exc

    if resp.status_code == 503:
        raise RenderNoDisponibleError(
            "storage-service no tiene renderizador de documentos configurado"
        )
    if resp.status_code != 200:
        raise RenderFallidoError(
            f"storage-service → HTTP {resp.status_code} al generar el documento: "
            f"{resp.text[:300]!r}"
        )

    datos = resp.json()
    try:
        contenido = base64.b64decode(datos["contenido_b64"], validate=True)
    except (KeyError, ValueError, binascii.Error) as exc:
        raise RenderFallidoError(
            f"respuesta de render ilegible: {sorted(datos) if isinstance(datos, dict) else type(datos)}"
        ) from exc
    if not contenido:
        raise RenderFallidoError("el renderizador devolvió un documento vacío")
    return DocumentoRendido(
        contenido=contenido,
        formato=str(datos.get("formato", "")),
        herramienta=str(datos.get("herramienta", "")),
    )


class StoragePurgaError(Exception):
    """El borrado físico no se pudo completar en storage-service.

    **Nunca se degrada a un aviso**: a diferencia de subir un artefacto o
    publicar un evento, aquí el fallo significa que los bytes SIGUEN EXISTIENDO.
    Quien orquesta la eliminación tiene que abortar el expediente entero y no
    escribir su lápida — una lápida sobre bytes vivos es peor que no eliminar.
    """


async def purgar_disposicion(
    *,
    tenant_slug: str,
    file_ids: list[uuid.UUID],
    expediente_id: str,
    motivo: str,
    actor: uuid.UUID | None,
) -> list[uuid.UUID]:
    """Borra físicamente los objetos de MinIO y sus filas (E04, RF-TRD-07 fase 5).

    Devuelve los `file_id` **realmente purgados**. Los que storage no encontró
    se consideran ya ausentes y no se cuentan: un reintento tras un fallo a
    medias tiene que poder converger, y contar como purgado lo que no estaba
    inflaría la cifra que después afirma la lápida.

    Un `409 worm_lock_activo` llega aquí como `StoragePurgaError`: el candado
    aborta la purga del expediente ENTERO, porque eliminar el resto dejaría un
    expediente medio destruido.
    """
    if not file_ids:
        return []
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.storage_internal_token,
    }
    body = {
        "file_ids": [str(f) for f in file_ids],
        "expediente_id": expediente_id,
        "motivo": motivo,
        "actor": str(actor) if actor else None,
    }
    async with httpx.AsyncClient(base_url=settings.storage_service_url, timeout=120.0) as client:
        try:
            resp = await client.post(
                "/api/v1/storage/internal/purgar-disposicion", headers=headers, json=body
            )
        except httpx.RequestError as exc:
            raise StoragePurgaError(f"storage-service inalcanzable: {exc}") from exc
    if resp.status_code != 200:
        raise StoragePurgaError(
            f"storage-service → HTTP {resp.status_code} al purgar: {resp.text[:300]!r}"
        )
    return [uuid.UUID(f) for f in resp.json().get("purgados", [])]
