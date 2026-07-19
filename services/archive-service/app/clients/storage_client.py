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

`proteger_indice_worm`/`proteger_indice_worm_internal` (E10 Inc.1, cableado
WORM del índice) — gemelo ESTRUCTURADO al estilo de
`signature_client.sign_xml_outcome`/`sign_indice_internal`: dos funciones,
no una con un flag, porque tienen credenciales e identidad DISTINTAS:

- `proteger_indice_worm` la usa `IndexService.proteger_worm_indice` con
  `origen="close"` (disparo síncrono tras el sellado, dentro de
  `firmar_indice_close`) — envía `X-User-Id=actor_id`, el usuario que YA
  pasó el gate `min_crud=3` de `close_expediente` en ESTE servicio, y que
  supera el MISMO chequeo en storage-service porque ambos leen la misma
  tabla `auth_users`/`auth_permissions` del tenant (Postgres único
  compartido, ADR-002). Llama a la ruta PÚBLICA `POST /proteger-indice`
  (RBAC `require_permission(min_crud=3)`).
- `proteger_indice_worm_internal` la usa `origen="reconciliation"` (3ra
  fase de `app/jobs/indice_reconciliation.py::run_once_worm`) — SIN
  `X-User-Id` real (actúa con `SYSTEM_RECONCILER_ID`, que no tiene fila en
  `auth_users`; `require_permission` la rechazaría SIEMPRE con 403). Llama
  a la ruta INTERNA `POST /proteger-indice-internal` (D-02,
  `X-Internal-Token`), mismo patrón que `sign_indice_internal`.

Ambas devuelven `WormProtectionOutcome` (nunca lanzan): distinguen `ok`
(éxito, incluida la respuesta idempotente 200 de una segunda llamada),
`disabled` (503 `preservation_disabled` — PRESERVATION_ENABLED=false en
storage-service en este instante) de `transient` (cualquier otro fallo:
HTTP no-2xx, timeout, servicio inalcanzable) — el llamador
(`IndexService._bump_worm_backoff`) programa el backoff igual en ambos
casos NO-ok (ninguno se agota de inmediato, solo tras acumular intentos),
pero el motivo textual persistido difiere para diagnóstico operacional.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date

import httpx
from fastapi import status

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WormProtectionOutcome:
    """Resultado ESTRUCTURADO de un intento de cableado WORM del índice (E10
    Inc.1) — exactamente uno de los tres queda "poblado": `ok=True` (éxito,
    `worm_object_id` poblado), `disabled=True` (503 `preservation_disabled`)
    o `transient=True` (cualquier otro fallo — reintentable)."""

    ok: bool
    worm_object_id: uuid.UUID | None = None
    disabled: bool = False
    transient: bool = False


def _worm_outcome_from_response(resp: httpx.Response, *, contexto: str) -> WormProtectionOutcome:
    """Traduce una respuesta HTTP de storage-service a un
    `WormProtectionOutcome` — compartido por `proteger_indice_worm`
    (`/proteger-indice`) y `proteger_indice_worm_internal`
    (`/proteger-indice-internal`), mismo criterio que
    `signature_client._outcome_from_response`."""
    if resp.status_code in (200, 201):
        try:
            data = resp.json()
            worm_object_id = uuid.UUID(data["id"])
        except (ValueError, KeyError, TypeError):
            logger.warning(
                "storage-service → respuesta 2xx sin 'id' válido al proteger WORM (%s)",
                contexto,
            )
            return WormProtectionOutcome(ok=False, transient=True)
        return WormProtectionOutcome(ok=True, worm_object_id=worm_object_id)
    if resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
        logger.warning(
            "storage-service: protección WORM deshabilitada (PRESERVATION_ENABLED=false, %s)",
            contexto,
        )
        return WormProtectionOutcome(ok=False, disabled=True)
    logger.warning(
        "storage-service → HTTP %s al proteger WORM el índice (%s, transient)",
        resp.status_code,
        contexto,
    )
    return WormProtectionOutcome(ok=False, transient=True)


async def proteger_indice_worm(
    *,
    tenant_slug: str,
    actor_id: str,
    file_id: str,
    expediente_id: str,
    retencion_anios: int,
    fecha_inicio: date,
    legal_hold: bool = False,
) -> WormProtectionOutcome:
    """Disparo SÍNCRONO (usuario real, `origen="close"`) — ver el docstring
    del módulo para el contrato completo."""
    body = {
        "file_id": file_id,
        "expediente_id": expediente_id,
        "retencion_anios": retencion_anios,
        "fecha_inicio": fecha_inicio.isoformat(),
        "legal_hold": legal_hold,
    }
    headers = {"X-Tenant-Slug": tenant_slug, "X-User-Id": actor_id}
    try:
        async with httpx.AsyncClient(
            base_url=settings.storage_service_url, timeout=15.0
        ) as client:
            resp = await client.post(
                "/api/v1/preservacion/proteger-indice", json=body, headers=headers
            )
    except httpx.RequestError as exc:
        logger.warning("storage-service inalcanzable al proteger WORM el índice: %s", exc)
        return WormProtectionOutcome(ok=False, transient=True)
    return _worm_outcome_from_response(resp, contexto="close")


async def proteger_indice_worm_internal(
    *,
    tenant_slug: str,
    file_id: str,
    expediente_id: str,
    retencion_anios: int,
    fecha_inicio: date,
    legal_hold: bool = False,
) -> WormProtectionOutcome:
    """Gemelo SISTEMA (D-02, `X-Internal-Token`, `origen="reconciliation"`) —
    ver el docstring del módulo para el contrato completo."""
    body = {
        "file_id": file_id,
        "expediente_id": expediente_id,
        "retencion_anios": retencion_anios,
        "fecha_inicio": fecha_inicio.isoformat(),
        "legal_hold": legal_hold,
    }
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.storage_internal_token,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.storage_service_url, timeout=15.0
        ) as client:
            resp = await client.post(
                "/api/v1/preservacion/proteger-indice-internal", json=body, headers=headers
            )
    except httpx.RequestError as exc:
        logger.warning("storage-service inalcanzable al proteger WORM el índice: %s", exc)
        return WormProtectionOutcome(ok=False, transient=True)
    return _worm_outcome_from_response(resp, contexto="reconciliation")


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
