# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Cliente del storage-service: descarga del índice YA FIRMADO (E06 Inc.1) y,
desde E10 Increment C, lectura mediada del AIP de preservación.

`download_signed_xml` la usa solo `IndexService.verify` para contrastar la
firma XAdES-B contra el XML realmente sellado (no el XML en claro que
persiste `expediente_indice.xml` — la canonicalización C14N 2.0 de exhibición
nunca coincidirá con el digest exc-c14n de XAdES, ver
`app/services/index_builder.py` y el docstring de `app.providers.local` en
signature-service; no se intenta reconciliar).

archive-service NUNCA habla con MinIO directamente (aislamiento por servicio,
igual que signature-service): pide una URL prefirmada a storage-service y
sigue esa URL. Best-effort: cualquier fallo devuelve ``None`` — el llamador
no declara conforme una firma que no pudo verificar (RF-FIR-15).

D-02 (remediación no-read-up en anexos, RF-SEG-08): `GET /files/{id}/download`
en storage-service ahora exige `X-Internal-Token` — este cliente es una
llamada de servicio-a-servicio legítima (descarga el índice ya firmado para
verificar su firma, no un anexo de usuario) y envía el mismo secreto
compartido que storage-service espera (`settings.storage_internal_token`).

`get_aip` (E10 Increment C) cierra el mismo tipo de fuga sobre
`GET /preservacion/aip/{expediente_id}`: ese endpoint también exige ahora
`X-Internal-Token`, y SOLO `ArchiveService.get_aip` (el mediador que resuelve
el clearance del llamante contra el `nivel_seguridad` VIGENTE del expediente,
que storage-service no modela) puede alcanzarlo — a diferencia de
`download_signed_xml`, que es best-effort (`None` en cualquier fallo),
`get_aip` SÍ distingue "no hay AIP" (``None``, 404 de storage) de "storage-service
falló" (``StorageAipError``, el router lo traduce a 503) porque el llamador
necesita esa distinción para decidir el código de respuesta — aunque el 404 de
"no hay AIP" y el 404 de "expediente no visible" SE FUNDEN en el mismo 404
neutro en `ArchiveService.get_aip` (no-read-up, ver ese método).

`list_eventos` (continuación de E10 Increment C, RF-SEG-08) cierra el MISMO
tipo de fuga sobre `GET /preservacion/eventos?object_ref=`: ese endpoint
también exige ahora `X-Internal-Token`, y SOLO
`ArchiveService.get_eventos_preservacion` (mismo mediador que `get_aip`)
puede alcanzarlo. A diferencia de `get_aip` (que distingue "no hay AIP" de
"storage-service falló"), aquí storage-service SIEMPRE responde 200 con una
lista (vacía si no hay eventos) — no hay 404 legítimo que distinguir; solo
``StorageEventosError`` ante un fallo real de infraestructura.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class StorageAipError(Exception):
    """storage-service no respondió (o respondió con error) al pedir el AIP."""


class StorageEventosError(Exception):
    """storage-service no respondió (o respondió con error) al pedir los
    eventos de preservación de un objeto."""


async def download_signed_xml(*, tenant_slug: str, minio_key_firmado: str) -> str | None:
    """Descarga el XML firmado identificado por `minio_key_firmado`.

    `minio_key_firmado` tiene la forma ``"{file_id}/{filename}"`` (ver
    ``signature-service.app.clients.storage_client.upload_signed_xml``): el
    primer segmento es el ``file_id`` que espera el endpoint de descarga de
    storage-service. Ese endpoint no expone bytes directamente, solo una URL
    prefirmada de MinIO (`GET /files/{file_id}/download`), que se sigue con
    una segunda petición.
    """
    file_id = minio_key_firmado.split("/", 1)[0]
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.storage_internal_token,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.storage_service_url, timeout=10.0
        ) as client:
            resp = await client.get(
                f"/api/v1/storage/files/{file_id}/download", headers=headers
            )
            if resp.status_code != 200:
                logger.warning(
                    "storage-service → HTTP %s al presignar el índice firmado",
                    resp.status_code,
                )
                return None
            url = resp.json()["url"]
            download = await client.get(url)
            if download.status_code != 200:
                logger.warning(
                    "MinIO → HTTP %s al descargar el índice firmado", download.status_code
                )
                return None
            return download.text
    except httpx.RequestError as exc:
        logger.warning("storage-service inalcanzable al descargar el índice firmado: %s", exc)
    return None


async def get_aip(*, tenant_slug: str, expediente_id: str) -> dict | None:
    """Obtiene el AIP vigente de ``expediente_id`` desde storage-service
    (E10 Increment C, único llamador legítimo de
    ``GET /preservacion/aip/{expediente_id}`` tras el cierre D-02 de ese
    endpoint).

    ``expediente_id`` es ``str(Expediente.id)`` (el UUID de archive-service
    en texto) — el mismo string que deberá usar cualquier futuro llamador de
    ``POST /preservacion/aip`` (empaquetado, todavía sin wiring en este
    servicio) para que la lectura mediada aquí encuentre el AIP correcto
    (mismo criterio que ``IndexService._data``: ``expediente_id=str(exp.id)``).

    Devuelve ``None`` si storage-service responde 404 (el expediente existe
    pero aún no tiene ningún AIP empaquetado) — el llamador (``ArchiveService.
    get_aip``) funde este caso con el 404 de "expediente no visible" en la
    MISMA respuesta neutra (no-read-up): no se distingue "no hay AIP" de
    "no autorizado".

    Lanza ``StorageAipError`` ante cualquier otro fallo (red, timeout, HTTP
    fuera de {200, 404}) — a diferencia de ``download_signed_xml`` (best-effort,
    ``None`` en todo fallo), aquí el llamador necesita distinguir un fallo de
    infraestructura (503) de un 404 legítimo.
    """
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.storage_internal_token,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.storage_service_url, timeout=10.0
        ) as client:
            resp = await client.get(
                f"/api/v1/preservacion/aip/{expediente_id}", headers=headers
            )
    except httpx.RequestError as exc:
        raise StorageAipError(f"storage-service inalcanzable al pedir el AIP: {exc}") from exc

    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise StorageAipError(
            f"storage-service → HTTP {resp.status_code} al pedir el AIP de "
            f"{expediente_id}: {resp.text[:300]!r}"
        )
    return resp.json()


async def list_eventos(*, tenant_slug: str, object_ref: str) -> list[dict]:
    """Obtiene los eventos PREMIS de preservación de ``object_ref`` desde
    storage-service (continuación de E10 Increment C, único llamador
    legítimo de ``GET /preservacion/eventos`` tras el cierre D-02 de ese
    endpoint).

    ``object_ref`` es ``str(Expediente.id)`` (mismo criterio que ``get_aip``)
    — el filtro es exacto: eventos keyed por ``file_id``/``aip`` id del
    expediente NO se incluyen en este MVP (refinamiento semántico diferido,
    ver ``ArchiveService.get_eventos_preservacion``).

    Devuelve ``[]`` si no hay eventos para ese ``object_ref`` (storage-service
    responde 200 con lista vacía, nunca 404 para esta variante) — a
    diferencia de ``get_aip``, no hay aquí un caso "ausente" que fundir con
    el 404 de no-read-up.

    Lanza ``StorageEventosError`` ante cualquier fallo (red, timeout, HTTP
    distinto de 200) — el llamador (``ArchiveService.get_eventos_preservacion``)
    lo traduce a 503, ortogonal a la autorización.
    """
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.storage_internal_token,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.storage_service_url, timeout=10.0
        ) as client:
            resp = await client.get(
                "/api/v1/preservacion/eventos",
                params={"object_ref": object_ref},
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise StorageEventosError(
            f"storage-service inalcanzable al pedir los eventos de preservación: {exc}"
        ) from exc

    if resp.status_code != 200:
        raise StorageEventosError(
            f"storage-service → HTTP {resp.status_code} al pedir los eventos de "
            f"{object_ref}: {resp.text[:300]!r}"
        )
    return resp.json()
