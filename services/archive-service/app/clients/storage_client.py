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

`proteger_artefacto_worm`/`proteger_artefacto_worm_internal` (E10 Inc.2
wiring, fast-follow declarado en el diseño de Inc.1) — GENERALIZACIÓN de las
dos funciones de arriba: mismo contrato ESTRUCTURADO (`WormProtectionOutcome`),
mismas dos identidades (`X-User-Id` real vs. `X-Internal-Token` sistema), pero
reciben `tipo` explícito y llaman a las rutas generalizadas de storage-service
(`POST /proteger-artefacto[-internal]`) en vez de `/proteger-indice[-internal]`.
`proteger_indice_worm`/`proteger_indice_worm_internal` se CONSERVAN intactas
(alias del índice, mismo criterio que storage-service mantiene
`/proteger-indice` fijo a `tipo='indice'`) — `TransferenciaService.
proteger_worm_acta` usa las nuevas, `IndexService.proteger_worm_indice` sigue
usando las de siempre.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from fastapi import status

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WormProtectionOutcome:
    """Resultado ESTRUCTURADO de un intento de cableado WORM del índice (E10
    Inc.1) — exactamente uno de los tres queda "poblado": `ok=True` (éxito,
    `worm_object_id`/`retain_until` poblados), `disabled=True` (503
    `preservation_disabled`) o `transient=True` (cualquier otro fallo —
    reintentable).

    `retain_until` (E10 debt: renovación de retención para series CT) se
    añade AQUÍ, no en un tipo nuevo, porque `IndexService.proteger_worm_indice`/
    `TransferenciaService.proteger_worm_acta` ya reciben este mismo outcome y
    lo necesitan para poblar el espejo `worm_retain_until` (migraciones
    031/032) al confirmar la protección — antes la respuesta de storage-service
    (`ProtegerIndiceResponse.retain_until`, siempre presente) se descartaba
    sin leer."""

    ok: bool
    worm_object_id: uuid.UUID | None = None
    retain_until: datetime | None = None
    disabled: bool = False
    transient: bool = False

    @property
    def protected_object_id(self) -> uuid.UUID:
        """El identificador del objeto WORM confirmado por storage-service.

        El invariante que ya declara el docstring de la clase —``ok=True``
        implica ``worm_object_id`` poblado— vive en la prosa y no en el tipo,
        así que los dos llamantes (``IndexService.proteger_worm_indice`` y
        ``TransferenciaService.proteger_worm_acta``) lo daban por supuesto
        tras comprobar ``ok``. Este accesor lo declara una vez y falla
        ruidosamente en vez de marcar la fila como protegida con ``NULL``,
        que dejaría el espejo de protección WORM mintiendo.
        """
        if self.worm_object_id is None:
            raise ValueError("WormProtectionOutcome(ok=True) sin worm_object_id")
        return self.worm_object_id


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
        # `retain_until` SIEMPRE viene en `ProtegerIndiceResponse` — un fallo
        # al parsearlo NO degrada la protección ya confirmada (el objeto YA
        # quedó inmutable en MinIO): solo deja el espejo local sin poblar
        # (`mark_worm_protegido`/`mark_acta_worm_protegido` toleran `None`,
        # ver `COALESCE` en esos UPDATE) — la renovación (E10 debt) lo
        # recuperará más tarde vía `PreservacionWormRepository.
        # get_by_expediente_and_tipo` del lado de storage-service, que sigue
        # siendo la fuente de verdad legal.
        retain_until: datetime | None = None
        try:
            retain_until = datetime.fromisoformat(data["retain_until"])
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "storage-service → respuesta 2xx sin 'retain_until' válido al "
                "proteger WORM (%s) — espejo local quedará sin poblar",
                contexto,
            )
        return WormProtectionOutcome(
            ok=True, worm_object_id=worm_object_id, retain_until=retain_until
        )
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


async def proteger_artefacto_worm(
    *,
    tenant_slug: str,
    actor_id: str,
    file_id: str,
    expediente_id: str,
    tipo: str,
    retencion_anios: int,
    fecha_inicio: date,
    legal_hold: bool = False,
    # De QUÉ es `expediente_id` (storage, migración 014): el acta de
    # eliminación pertenece a un LOTE. El default conserva el contrato de
    # todos los llamadores anteriores.
    objeto_tipo: str = "expediente",
) -> WormProtectionOutcome:
    """Disparo SÍNCRONO GENERALIZADO (usuario real) — gemelo de
    `proteger_indice_worm` que acepta `tipo` explícito y llama a
    `POST /proteger-artefacto` (E10 Inc.2 wiring). Ver el docstring del
    módulo para el contrato completo."""
    body = {
        "file_id": file_id,
        "expediente_id": expediente_id,
        "tipo": tipo,
        "retencion_anios": retencion_anios,
        "fecha_inicio": fecha_inicio.isoformat(),
        "legal_hold": legal_hold,
        "objeto_tipo": objeto_tipo,
    }
    headers = {"X-Tenant-Slug": tenant_slug, "X-User-Id": actor_id}
    try:
        async with httpx.AsyncClient(
            base_url=settings.storage_service_url, timeout=15.0
        ) as client:
            resp = await client.post(
                "/api/v1/preservacion/proteger-artefacto", json=body, headers=headers
            )
    except httpx.RequestError as exc:
        logger.warning(
            "storage-service inalcanzable al proteger WORM (%s, close): %s", tipo, exc
        )
        return WormProtectionOutcome(ok=False, transient=True)
    return _worm_outcome_from_response(resp, contexto=f"close:{tipo}")


async def proteger_artefacto_worm_internal(
    *,
    tenant_slug: str,
    file_id: str,
    expediente_id: str,
    tipo: str,
    retencion_anios: int,
    fecha_inicio: date,
    legal_hold: bool = False,
    # De QUÉ es `expediente_id` (storage, migración 014): el acta de
    # eliminación pertenece a un LOTE. El default conserva el contrato de
    # todos los llamadores anteriores.
    objeto_tipo: str = "expediente",
) -> WormProtectionOutcome:
    """Gemelo SISTEMA GENERALIZADO (D-02, `X-Internal-Token`) — llama a
    `POST /proteger-artefacto-internal` (E10 Inc.2 wiring). Ver el docstring
    del módulo para el contrato completo."""
    body = {
        "file_id": file_id,
        "expediente_id": expediente_id,
        "tipo": tipo,
        "retencion_anios": retencion_anios,
        "fecha_inicio": fecha_inicio.isoformat(),
        "legal_hold": legal_hold,
        "objeto_tipo": objeto_tipo,
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
                "/api/v1/preservacion/proteger-artefacto-internal", json=body, headers=headers
            )
    except httpx.RequestError as exc:
        logger.warning(
            "storage-service inalcanzable al proteger WORM (%s, reconciliation): %s",
            tipo,
            exc,
        )
        return WormProtectionOutcome(ok=False, transient=True)
    return _worm_outcome_from_response(resp, contexto=f"reconciliation:{tipo}")


# ---------------------------------------------------------------------------
# E10 debt — renovación de retención WORM para series de disposición
# Conservación Total (CT)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WormRenewalOutcome:
    """Resultado ESTRUCTURADO de un intento de renovación de retención WORM
    (E10 debt) — mismo criterio de "exactamente un flag queda poblado" que
    `WormProtectionOutcome`: `ok=True` (llamada confirmada — `renovado`
    distingue extensión real de no-op idempotente, `retain_until` es SIEMPRE
    el valor vigente tras la llamada), `not_found=True` (404
    `worm_object_not_found` — nada que renovar, artefacto nunca protegido),
    `disabled=True` (503 `preservation_disabled`) o `transient=True`
    (cualquier otro fallo — reintentable)."""

    ok: bool
    retain_until: datetime | None = None
    renovado: bool = False
    not_found: bool = False
    disabled: bool = False
    transient: bool = False


def _worm_renewal_outcome_from_response(
    resp: httpx.Response, *, contexto: str
) -> WormRenewalOutcome:
    """Traduce una respuesta HTTP de storage-service a un
    `WormRenewalOutcome` — compartido por la (futura) variante pública y
    `renovar_retencion_worm_internal`, mismo criterio que
    `_worm_outcome_from_response`."""
    if resp.status_code == status.HTTP_200_OK:
        try:
            data = resp.json()
            retain_until = datetime.fromisoformat(data["retain_until"])
            renovado = bool(data["renovado"])
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "storage-service → respuesta 200 sin 'retain_until'/'renovado' "
                "válidos al renovar retención WORM (%s)",
                contexto,
            )
            return WormRenewalOutcome(ok=False, transient=True)
        return WormRenewalOutcome(ok=True, retain_until=retain_until, renovado=renovado)
    if resp.status_code == status.HTTP_404_NOT_FOUND:
        logger.warning(
            "storage-service: sin objeto WORM protegido para renovar (%s, 404)", contexto
        )
        return WormRenewalOutcome(ok=False, not_found=True)
    if resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
        logger.warning(
            "storage-service: protección WORM deshabilitada al renovar "
            "(PRESERVATION_ENABLED=false, %s)",
            contexto,
        )
        return WormRenewalOutcome(ok=False, disabled=True)
    logger.warning(
        "storage-service → HTTP %s al renovar retención WORM (%s, transient)",
        resp.status_code,
        contexto,
    )
    return WormRenewalOutcome(ok=False, transient=True)


async def renovar_retencion_worm_internal(
    *,
    tenant_slug: str,
    expediente_id: str,
    tipo: str,
    retencion_anios: int,
    fecha_inicio: date,
) -> WormRenewalOutcome:
    """Gemelo SISTEMA (D-02, `X-Internal-Token`) de `POST
    /renovar-retencion-internal` — único llamador en este incremento: la 5ª
    fase de reconciliación (`run_once_worm_renovacion`,
    `app/jobs/indice_reconciliation.py`), sin `X-User-Id` real (actúa con
    `SYSTEM_RECONCILER_ID`). No existe variante pública en este cliente
    porque nada en archive-service dispara una renovación síncrona con un
    usuario real detrás (a diferencia de la protección inicial, que sí ocurre
    al cierre) — la renovación es puramente reactiva al vencimiento próximo,
    detectado solo por el job."""
    body = {
        "expediente_id": expediente_id,
        "tipo": tipo,
        "retencion_anios": retencion_anios,
        "fecha_inicio": fecha_inicio.isoformat(),
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
                "/api/v1/preservacion/renovar-retencion-internal", json=body, headers=headers
            )
    except httpx.RequestError as exc:
        logger.warning(
            "storage-service inalcanzable al renovar retención WORM (%s, %s): %s",
            tipo,
            expediente_id,
            exc,
        )
        return WormRenewalOutcome(ok=False, transient=True)
    return _worm_renewal_outcome_from_response(resp, contexto=f"renovacion:{tipo}")


@dataclass(slots=True)
class AipPackagingOutcome:
    """Resultado ESTRUCTURADO de un intento de empaquetado del AIP (E10 debt,
    cableado del AIP al cierre del expediente) — mismo criterio de
    "exactamente un flag poblado" que `WormProtectionOutcome`: `ok=True`
    (éxito, `aip_id` poblado — el `id` de la fila `aip` en storage-service,
    ver `AipResponse`), `fixity_mismatch=True` (409 — la fixity REAL de un
    anexo, `files.sha256` capturado en storage-service al momento de la
    SUBIDA original, ya no coincide con los bytes que descarga y re-hashea
    `AipService._resolve_and_verify` al empaquetar; `payload.documentos`
    (`AipDocInput`, ver `app/services/aip_wiring_service.py`) solo aporta
    `document_id`/`nombre`/`file_id` — NO lleva ningún hash propio, la
    comparación es siempre contra `files.sha256`, nunca contra un valor
    aportado por este cliente), `disabled=True` (503 `preservation_disabled`)
    o `transient=True` (cualquier otro fallo — reintentable).

    `retain_until` (E10 debt: extensión de la renovación WORM al AIP) se
    añade AQUÍ, mismo criterio que `WormProtectionOutcome.retain_until` —
    `AipWiringService.intentar_empaquetar` la necesita para poblar el espejo
    `expediente_aip.worm_retain_until` (migración 034) al confirmar el
    empaquetado. `AipResponse.retain_until` SIEMPRE viene poblado en un 201
    real (`AipService.empaquetar_aip` la resuelve de `payload.
    resolve_retain_until()` antes de construir la respuesta) — un fallo al
    parsearla NO degrada el empaquetado ya confirmado (el AIP YA quedó
    inmutable en MinIO), solo deja el espejo local sin poblar (mismo
    `COALESCE` defensivo que `IndexRepository.mark_worm_protegido`)."""

    ok: bool
    aip_id: uuid.UUID | None = None
    retain_until: datetime | None = None
    disabled: bool = False
    fixity_mismatch: bool = False
    transient: bool = False


async def empaquetar_aip_internal(
    *,
    tenant_slug: str,
    expediente_id: str,
    documentos: list[dict],
    indice_file_id: uuid.UUID | None,
    retencion_anios: int,
    fecha_inicio: date,
    legal_hold: bool = False,
    pdfa_profile: str | None = None,
) -> AipPackagingOutcome:
    """Gemelo SISTEMA (D-02, `X-Internal-Token`) de `POST /preservacion/aip`
    — único llamador: `AipWiringService` (reconciliación-only, actúa con
    `SYSTEM_RECONCILER_ID`, sin `X-User-Id` real — la ruta pública exige
    `require_permission(min_crud=3)`, que SIEMPRE rechazaría con 403 un actor
    sin fila en `auth_users`, mismo motivo que `proteger_indice_worm_internal`).

    Timeout deliberadamente MAYOR (120s) que el resto de este cliente: a
    diferencia de `proteger_indice_worm[_internal]` (sube un XML ya en
    `files`), este endpoint DESCARGA los bytes reales de cada documento desde
    MinIO, los re-hashea y arma un ZIP server-side antes de responder — coste
    proporcional al tamaño total de los anexos del expediente, no una
    operación barata.

    `documentos` ya viene armado por el llamador con la forma que exige
    `AipCreate.documentos` (`[{"document_id": ..., "nombre": ..., "file_id":
    ...}, ...]`) — este cliente no valida su contenido, solo lo reenvía.

    `pdfa_profile` (E10 debt "perfil PDF/A por serie", RF-FIR-15): resuelto
    por el llamador (`AipWiringService._resolve_aip_pdfa_profile`) a partir
    de `trd_series.pdfa_profile` de la serie del expediente. `None` = la
    serie no tiene política propia -- se omite del payload y storage-service
    cae a su default global (`AipCreate.pdfa_profile` es opcional,
    retrocompat)."""
    body: dict = {
        "expediente_id": expediente_id,
        "documentos": documentos,
        "retencion_anios": retencion_anios,
        "fecha_inicio": fecha_inicio.isoformat(),
        "legal_hold": legal_hold,
    }
    if indice_file_id is not None:
        body["indice_file_id"] = str(indice_file_id)
    if pdfa_profile is not None:
        body["pdfa_profile"] = pdfa_profile
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.storage_internal_token,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.storage_service_url, timeout=120.0
        ) as client:
            resp = await client.post(
                "/api/v1/preservacion/aip-internal", json=body, headers=headers
            )
    except httpx.RequestError as exc:
        logger.warning("storage-service inalcanzable al empaquetar el AIP: %s", exc)
        return AipPackagingOutcome(ok=False, transient=True)

    if resp.status_code == status.HTTP_201_CREATED:
        try:
            data = resp.json()
            aip_id = uuid.UUID(data["id"])
        except (ValueError, KeyError, TypeError):
            logger.warning("storage-service → 201 sin 'id' válido al empaquetar el AIP")
            return AipPackagingOutcome(ok=False, transient=True)
        retain_until: datetime | None = None
        try:
            if data.get("retain_until"):
                retain_until = datetime.fromisoformat(data["retain_until"])
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "storage-service → 201 sin 'retain_until' válido al empaquetar el "
                "AIP de %s — espejo local quedará sin poblar",
                expediente_id,
            )
        return AipPackagingOutcome(ok=True, aip_id=aip_id, retain_until=retain_until)
    if resp.status_code == status.HTTP_409_CONFLICT:
        logger.warning(
            "storage-service: fixity_mismatch al empaquetar el AIP de %s "
            "(un anexo mutó desde que se vinculó)",
            expediente_id,
        )
        return AipPackagingOutcome(ok=False, fixity_mismatch=True)
    if resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
        logger.warning(
            "storage-service: empaquetado de AIP deshabilitado "
            "(PRESERVATION_ENABLED=false, expediente_id=%s)",
            expediente_id,
        )
        return AipPackagingOutcome(ok=False, disabled=True)
    logger.warning(
        "storage-service → HTTP %s al empaquetar el AIP de %s (transient)",
        resp.status_code,
        expediente_id,
    )
    return AipPackagingOutcome(ok=False, transient=True)


class StorageAipError(Exception):
    """storage-service no respondió (o respondió con error) al pedir el AIP."""


class StorageEventosError(Exception):
    """storage-service no respondió (o respondió con error) al pedir los
    eventos de preservación de un objeto."""


class StorageExportError(Exception):
    """storage-service no pudo ensamblar el ZIP de exportación del expediente."""


async def export_zip_internal(
    *,
    tenant_slug: str,
    items: list[dict],
    extra_files: list[dict],
) -> bytes:
    """Pide a storage el ZIP de exportación (D-02, `X-Internal-Token`) a partir
    del inventario de anexos (`{file_id, arcname}`) + archivos generados por
    archive (`{arcname, contenido_b64}`). Devuelve los bytes del ZIP. Timeout
    amplio: descargar N anexos de MinIO + comprimir es costoso."""
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.storage_internal_token,
    }
    body = {"items": items, "extra_files": extra_files}
    try:
        async with httpx.AsyncClient(
            base_url=settings.storage_service_url, timeout=120.0
        ) as client:
            resp = await client.post(
                "/api/v1/preservacion/export-zip-internal", json=body, headers=headers
            )
    except httpx.RequestError as exc:
        raise StorageExportError(f"storage-service inalcanzable: {exc}") from exc
    if resp.status_code != 200:
        raise StorageExportError(
            f"storage-service respondió {resp.status_code} al ensamblar el ZIP"
        )
    return resp.content


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
