# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma de payloads (sign-xml) y verificación (E06, ADR-016, RF-FIR-11..14).

Autorización: /sign-xml requiere PERM_FIRMA verificado en la BD del tenant
(require_permission, fail-closed). El firmante se toma de X-User-Id (auth_users.id
interno, resuelto por auth-service /validate). Ya NO se valida el JWT aquí — el
api-gateway lo validó y el hash de identidad es el id interno, no el keycloak_sub.

/verify no requiere autenticación — es una operación de consulta pública.

--- Selección de proveedor (Inc.1, ADR-016) ---
``objeto_tipo == "indice"`` y ``settings.signer_provider == "xades_local"``
producen una firma XAdES-B real con el sello institucional del tenant
(:mod:`app.providers.local`); cualquier otro caso cae al HMAC nativo
preexistente (:mod:`app.providers.native`), preservando exactamente el
comportamiento de antes del Incremento 1 (incluida la cadena de firma
secuencial en ``app/routers/cadena.py``, que sigue llamando a
``app.services.signer`` directamente y no pasa por este router).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.clearance import get_actor, require_permission
from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.core.database import get_tenant_conn
from app.core.http_clients import (
    SigningKeyRevocationStatusUnavailableError,
    get_signing_key_revocation_status_remote,
)
from app.core.internal_auth import require_internal_token
from app.providers.local import verify_xades
from app.repositories.cadena_repository import CadenaRepository
from app.repositories.firma_repository import FirmaRepository
from app.schemas.firma import (
    FirmaResponse,
    InternalSignIndiceRequest,
    SignPersonalRequest,
    SignXmlRequest,
    VerifyRequest,
    VerifyResponse,
)
from app.services import signer, signing_service
from app.services.personal_signing import PersonalSigningService

_SERVICE = "signature-service"
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/signature", tags=["signature"])


@router.post(
    "/sign-xml",
    response_model=FirmaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("PERM_FIRMA"))],
)
async def sign_xml(
    payload: SignXmlRequest,
    request: Request,
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> FirmaResponse:
    """
    Firma electrónica de un payload XML o hash de documento (RF-FIR-11..14).

    - El firmante se toma exclusivamente de X-User-Id (auth_users.id interno — RF-FIR-11).
    - Se verifica PERM_FIRMA en la BD del tenant antes de llegar aquí (require_permission).
    - El sello_tiempo es generado por el servidor (no lo aporta el cliente).
    - Para ``objeto_tipo="indice"`` con el proveedor XAdES habilitado, produce una
      firma XAdES-B real con el sello institucional del tenant (ver
      ``app.providers.local``); en cualquier otro caso, HMAC-SHA256 nativo
      (RF-FIR-12), no reproducible sin conocer el secreto del servicio.
    - El acto de firma queda registrado en audit_log (RF-FIR-14).

    TODO (RF-FIR-13): reto 2FA ligado al hash_documento — diferido a F4.
    """
    tenant_slug: str = request.headers.get("X-Tenant-Slug", "")
    return await signing_service.sign_payload(
        conn=conn,
        tenant_slug=tenant_slug,
        payload=payload,
        firmante_id=actor,
        origen="manual",
    )


@router.post(
    "/internal/sign-indice",
    response_model=FirmaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_internal_token)],
)
async def sign_indice_internal(
    payload: InternalSignIndiceRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> FirmaResponse:
    """Sellado del índice electrónico o del acta de transferencia ATRIBUIDO A
    SISTEMA — reconciliación (residual Ac. AGN 001/2024 art. 4.3.2.4, ver
    archive-service `app/jobs/indice_reconciliation.py`; generalizada en H-H
    E06/F4 para `transferencia_acta`).

    Gate SOLO `require_internal_token` (X-Internal-Token, D-02) — NUNCA
    `require_permission`/`get_actor`: el llamador es un job de reconciliación
    de archive-service, no un usuario humano. `objeto_tipo` está ACOTADO al
    allow-set institucional (`InternalSignIndiceRequest`, validado en el
    schema) — el llamador NUNCA puede elegir un tipo arbitrario, a diferencia
    de `SignXmlRequest.objeto_tipo` en `/sign-xml` — estructuralmente esta
    ruta NUNCA puede firmar la cadena personal (`/requests/{id}/sign`) ni
    ningún objeto fuera de ese conjunto.

    `firmante_id=SYSTEM_RECONCILER_ID` (`app/core/constants.py`) y
    `origen="reconciliation"` en el asiento `firma.creada` — distinguible del
    sellado manual (`/sign-xml`, `origen="manual"`, firmante humano).
    Reutiliza `signing_service.sign_payload` SIN duplicar lógica: mismo
    `prepare` → `load_seal` → `provider.sign` → persistencia + auditoría +
    subida a storage que `/sign-xml`.
    """
    tenant_slug: str = request.headers.get("X-Tenant-Slug", "")
    sign_request = SignXmlRequest(
        payload_xml=payload.payload_xml,
        objeto_tipo=payload.objeto_tipo,
        objeto_id=payload.objeto_id,
        formato="XAdES",
        indice_id=payload.indice_id,
        version=payload.version,
    )
    return await signing_service.sign_payload(
        conn=conn,
        tenant_slug=tenant_slug,
        payload=sign_request,
        firmante_id=SYSTEM_RECONCILER_ID,
        origen="reconciliation",
    )


@router.post(
    "/sign-personal",
    response_model=FirmaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("PERM_FIRMA"))],
)
async def sign_personal(
    payload: SignPersonalRequest,
    request: Request,
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> FirmaResponse:
    """
    Firma PERSONAL PKI por-usuario de un ``payload_xml`` ARBITRARIO (E17/F4,
    Incremento 1) — endpoint reutilizable, primer consumidor: el acta de
    entrega de transferencia (``archive-service``,
    ``POST /transferencias/{id}/acta/firmar-personal``).

    A diferencia de ``/sign-xml`` (sello institucional/nativo del sistema),
    esta ruta SIEMPRE firma con el certificado PERSONAL del actor
    autenticado — requiere una credencial 'activo' (auth-service) y un
    ``totp_code`` válido, verificado-y-consumido atómicamente junto con la
    firma. Gate ``PERM_FIRMA`` (mismo permiso que el resto de rutas de
    firma) + ``X-User-Id``/JWT (``Depends(get_actor)``) — NUNCA
    ``require_internal_token``: el llamante trae identidad y TOTP de un
    humano real.
    """
    tenant_slug: str = request.headers.get("X-Tenant-Slug", "")
    authorization = request.headers.get("Authorization")
    return await PersonalSigningService(conn, tenant_slug).sign(payload, actor, authorization)


@router.post("/verify", response_model=VerifyResponse)
async def verify(
    payload: VerifyRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> VerifyResponse:
    """
    Verifica la integridad de una firma existente.

    No requiere autenticación — es una operación de consulta pública.

    - Firmas ``provider='xades_local'``: primero ata la verificación a ESTA
      firma_id (``signed_xml_sha256``, migración 010 — evita que el
      ``signed_xml`` de OTRO índice firmado con el mismo sello institucional
      del tenant se reporte válido para un ``firma_id`` ajeno); luego valida
      la firma XAdES-B REAL sobre ``payload_xml`` (que debe ser el XML
      **firmado** completo), pasando ``row["tsa_cert_fingerprint"]`` como
      ancla del sello de tiempo — ver ``app.providers.local.verify_xades``
      para el detalle de las comprobaciones restantes (criptografía,
      cobertura de la referencia, vigencia del certificado, huella contra el
      sello registrado al firmar y, si hay ``SignatureTimeStamp``, huella del
      certificado de la TSA contra el ``tsa_cert_fingerprint`` persistido —
      Inc.4, fail-closed cuando la fila no lo tiene registrado).
    - Firmas ``provider='xades_personal'`` (E17/F4): mismo ``verify_xades``,
      pero ``row["ca_fingerprint"]`` (si está poblado — Incremento 2, Baja
      #4) se pasa como ``expected_user_ca_fingerprint`` — NUNCA como
      ``expected_ca_fingerprint`` (esa rama es exclusiva del sello
      institucional) — para activar la RAMA PERSONAL de ``verify_xades``.
      Si la firma cripto+cadena valida, ADEMÁS se consulta
      ``GET /internal/signing-key/revocation-status`` (auth-service, en
      línea, NUNCA cacheado) por ``certificado_fingerprint`` — el veredicto
      final ``valida`` incorpora ese resultado (ver
      ``VerifyResponse.revocacion_status``/``firma_criptograficamente_valida``).
      Una firma ``xades_personal`` de Incremento 1 (``ca_fingerprint=NULL``)
      NUNCA activa la rama personal de ``verify_xades`` — retrocompat exacta.
    - Firmas ``provider='nativa'``: recomputa el HMAC con firmante_id y
      sello_tiempo almacenados en BD y lo compara con la firma_blob
      registrada (comportamiento sin cambios desde antes del Incremento 1).
    """
    row = await FirmaRepository(conn).get(payload.firma_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "firma_not_found", "detail": str(payload.firma_id), "status": 404},
        )

    if row.get("provider") in ("xades_local", "xades_personal"):
        provider_name = row["provider"]
        is_personal = provider_name == "xades_personal"
        fingerprint = row.get("certificado_fingerprint")
        if not fingerprint:
            return VerifyResponse(
                firma_id=row["id"],
                valida=False,
                provider=provider_name,
                xades_level=row.get("xades_level"),
                nivel_conformidad=row.get("nivel_conformidad"),
                acreditado=False,
                motivo="firma_sin_huella_de_certificado_registrada",
            )
        # Ata la verificación a ESTA firma_id (migración 010): sin esto, el
        # signed_xml de OTRO índice firmado con el MISMO sello institucional
        # del tenant se reportaría `valida=True` para cualquier firma_id de
        # ese tenant. `signed_xml_sha256` es NULL en filas anteriores a la
        # migración — se degrada al comportamiento previo (solo huella de
        # certificado) para no romper la verificación de firmas históricas.
        expected_xml_sha256 = row.get("signed_xml_sha256")
        if expected_xml_sha256 and hashlib.sha256(
            payload.payload_xml.encode()
        ).hexdigest() != expected_xml_sha256:
            return VerifyResponse(
                firma_id=row["id"],
                valida=False,
                provider=provider_name,
                xades_level=row.get("xades_level"),
                nivel_conformidad=row.get("nivel_conformidad"),
                acreditado=False,
                motivo="documento_no_corresponde_a_firma_id",
            )
        outcome = verify_xades(
            payload.payload_xml.encode(),
            fingerprint,
            expected_tsa_fingerprint=row.get("tsa_cert_fingerprint"),
            # `ca_fingerprint` (misma columna, dos semánticas mutuamente
            # excluyentes por `provider`, ver docstring de la función): ancla
            # LT institucional para xades_local, ancla de la sub-CA de
            # usuarios para xades_personal — NUNCA ambos kwargs a la vez.
            expected_ca_fingerprint=(None if is_personal else row.get("ca_fingerprint")),
            expected_ocsp_responder_fingerprint=row.get("ocsp_responder_fingerprint"),
            expected_user_ca_fingerprint=(row.get("ca_fingerprint") if is_personal else None),
        )

        # `firma_criptograficamente_valida`: el veredicto de `verify_xades`
        # a secas (criptografía + cadena/anclaje), SIN la dimensión de
        # revocación — NUNCA difama la cripto por un canal de revocación
        # caído (ver docstring de `VerifyResponse`).
        firma_criptograficamente_valida = outcome.valida
        valida = outcome.valida
        motivo = outcome.motivo
        revocacion_status: str | None = None

        # Revocación en línea (A2, Incremento 2) — SOLO firmas personales
        # cuya cripto+cadena YA validó (revocar no tiene sentido evaluarlo
        # sobre una firma ya inválida por otra causa, y evita una llamada de
        # red innecesaria). `verify_xades` sigue siendo PURA — esta llamada
        # vive aquí, en el router.
        if is_personal and outcome.valida:
            tenant_slug = request.headers.get("X-Tenant-Slug", "")
            try:
                status_body = await get_signing_key_revocation_status_remote(
                    fingerprint, tenant_slug
                )
                revocacion_status = status_body.get("status", "desconocido")
            except SigningKeyRevocationStatusUnavailableError:
                logger.warning(
                    "No se pudo consultar signing-key/revocation-status para "
                    "firma_id=%s; aplicando verify_personal_revocation_require",
                    row["id"],
                    exc_info=True,
                )
                revocacion_status = "no_verificable"
                if settings.verify_personal_revocation_require:
                    valida = False
                    motivo = "revocacion_no_verificable"
                # Fail-open (opt-in, NO default): la cripto ya validó, así
                # que `valida` se queda en True — solo se informa el canal
                # caído vía `revocacion_status`, sin invalidar.
            else:
                if revocacion_status != "vigente":
                    valida = False
                    motivo = (
                        "certificado_revocado"
                        if revocacion_status == "revocado"
                        # `desconocido` fail-closed (huella no encontrada en
                        # este tenant — otro tenant, forjada, o el titular
                        # borrado por CASCADE): NUNCA se reporta `vigente`.
                        else "certificado_desconocido"
                    )

        return VerifyResponse(
            firma_id=row["id"],
            valida=valida,
            provider=provider_name,
            # El nivel REALMENTE verificado para ESTE documento (RT-16, Pieza
            # 6.4) — nunca el `xades_level` "intencionado" al firmar. Se cae
            # al persistido en la fila solo si `verify_xades` no llegó a
            # evaluar ningún nivel (p. ej. certificado fuera de vigencia).
            xades_level=outcome.xades_level or row.get("xades_level"),
            nivel_conformidad=row.get("nivel_conformidad"),
            acreditado=outcome.acreditado,
            motivo=motivo,
            # (Inc.4) None cuando la firma no lleva SignatureTimeStamp (no
            # aplica, no es un fallo) — ver app.providers.local.VerifyOutcome.
            sello_tiempo_valido=outcome.sello_tiempo_valido,
            tsa_timestamp=outcome.tsa_timestamp,
            lt_material_present=outcome.lt_material_present,
            archive_timestamp_present=outcome.archive_timestamp_present,
            revocation_provenance=outcome.revocation_provenance,
            crl_next_update=outcome.crl_next_update,
            ocsp_present=outcome.ocsp_present,
            ocsp_cert_status=outcome.ocsp_cert_status,
            firma_criptograficamente_valida=firma_criptograficamente_valida,
            revocacion_status=revocacion_status,
        )

    firmante_id = str(row["firmante_id"]) if row["firmante_id"] else ""
    sello_tiempo_str = (
        row["sello_tiempo"].astimezone(UTC).isoformat()
        if row["sello_tiempo"]
        else ""
    )

    valida = signer.verificar(
        payload.payload_xml.encode(),
        firmante_id,
        row["firma_blob"],
        sello_tiempo_str,
        settings.signature_secret,
    )
    return VerifyResponse(
        firma_id=row["id"],
        valida=valida,
        provider=row["provider"],
        nivel_conformidad=row.get("nivel_conformidad"),
        acreditado=False,
        motivo=None if valida else "hmac_no_coincide",
        # 'nativa' no tiene dimensión de revocación (Incremento 2 es
        # exclusivo de xades_personal) — firma_criptograficamente_valida
        # coincide SIEMPRE con valida, revocacion_status no aplica (None).
        firma_criptograficamente_valida=valida,
    )


@router.get("/firmas/{firma_id}", response_model=FirmaResponse)
async def get_firma(
    firma_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> FirmaResponse:
    """Metadatos de una firma (certificado público, nivel de conformidad, ...).

    Bajo ``/firmas/{firma_id}`` (no ``/{firma_id}`` a secas) para no colisionar
    con las rutas estáticas GET de ``app/routers/cadena.py`` que comparten el
    mismo prefijo ``/api/v1/signature`` (``/pending``, ``/requests``,
    ``/requests/{solicitud_id}``): un catch-all de un solo segmento aquí
    interceptaría esas rutas si este router se registra antes que el de
    cadena (ver el orden de ``include_router`` en ``app/main.py``).

    Sin datos sensibles: nunca expone una clave privada (solo hay un campo de
    certificado PÚBLICO — ``certificado_pem`` no se incluye en la respuesta,
    solo su huella; el consumidor que necesite el certificado completo lo
    obtiene del propio XML firmado descargado de ``minio_key_firmado``).
    """
    row = await FirmaRepository(conn).get(firma_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "firma_not_found", "detail": str(firma_id), "status": 404},
        )
    d = dict(row)
    return FirmaResponse(
        firma_id=d["id"],
        objeto_tipo=d["objeto_tipo"],
        objeto_id=d.get("objeto_id"),
        formato_solicitado=d.get("formato_solicitado"),
        firmante_id=d.get("firmante_id"),
        firmante_nombre=d.get("firmante_nombre"),
        payload_sha256=d["payload_sha256"],
        provider=d["provider"],
        sello_tiempo=d["sello_tiempo"],
        created_at=d["created_at"],
        minio_key_firmado=d.get("minio_key_firmado"),
        xades_level=d.get("xades_level"),
        nivel_conformidad=d.get("nivel_conformidad"),
        tipo_firma=d.get("tipo_firma"),
        certificado_fingerprint=d.get("certificado_fingerprint"),
        acreditado=False,
        tsa_timestamp=d.get("tsa_timestamp"),
        tsa_acreditado=d.get("tsa_acreditado", False),
        lt_material_present=d.get("lt_material_present", False),
        archive_timestamp_present=d.get("archive_timestamp_present", False),
        revocation_provenance=d.get("revocation_provenance"),
        crl_next_update=d.get("crl_next_update"),
        ocsp_present=d.get("ocsp_present", False),
    )


@router.post(
    "/internal/circuitos/{objeto_tipo}/{objeto_id}/invalidar",
    dependencies=[Depends(require_internal_token)],
    summary=(
        "Dejar sin efecto los circuitos de un objeto cuyo texto cambió "
        "(acto de sistema; lo pide document-service al editar un borrador ya sometido a firma)"
    ),
)
async def invalidar_circuitos_internal(
    objeto_tipo: str,
    objeto_id: str,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, object]:
    """Rehacer un documento firmado es el trámite, no un abuso.

    Se recogen los vistos buenos, alguien recuerda un anexo o corrige una cifra
    a última hora, y hay que volver a empezar. Lo que no puede quedar en pie son
    las firmas anteriores, que se dieron sobre otro texto.

    La garantía DURA de eso vive en document-service, que al radicar compara la
    huella firmada con la del texto vigente y se niega si no coinciden. Esto es
    lo que hace humano el trámite: sin ello, los turnos de un circuito superado
    seguían apareciendo en la bandeja de quien los tenía, pidiéndole firmar un
    documento que ya no existe, y desaparecían después sin explicación. «Se
    esfumó» sin motivo es lo que hace que la gente desconfíe de la herramienta.

    Es **best-effort**, igual que el aviso de aprobación en sentido contrario, y
    puede serlo sin abrir ninguna ventana: si esta llamada se pierde, la
    comparación de huellas al radicar sigue impidiendo que salga el documento.
    La autoridad es la comparación; esto solo mantiene honesta la bandeja.

    Idempotente: sobre un objeto sin circuitos vivos no hace nada y devuelve 0.
    """
    repo = CadenaRepository(conn)
    invalidadas = await repo.invalidar_por_objeto(
        objeto_tipo, objeto_id, "el texto del documento cambió después de someterse a firma"
    )
    return {"objeto_tipo": objeto_tipo, "objeto_id": objeto_id, "invalidadas": invalidadas}


@router.get(
    "/internal/circuitos/{objeto_tipo}/{objeto_id}",
    dependencies=[Depends(require_internal_token)],
    summary=(
        "Estado del circuito de aprobación de un objeto "
        "(acto de sistema; lo consulta document-service antes de aprobar un borrador)"
    ),
)
async def estado_circuito_internal(
    objeto_tipo: str,
    objeto_id: str,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> dict[str, object]:
    """Si hay un circuito abierto sobre este objeto, y si alguno se completó.

    **El circuito tiene que atar** (E23, RF-PRO-09). Hasta aquí no ataba: se
    abría una cadena de tres turnos sobre un borrador, nadie resolvía ninguno,
    y quien tuviera `PERM_RADI` pulsaba «Aprobar» y radicaba. El visto bueno de
    los funcionarios era decorativo, y el gate `documento_modificado` que
    protege cada turno no protegía nada, porque nadie estaba obligado a pasar
    por él.

    La consulta va en esta dirección —document PREGUNTA— y no al revés
    (signature avisando a document al cerrar la cadena) a propósito: una marca
    copiada en `borrador` se queda rancia por caminos que no pasan por aquí
    (borrar la cadena, restaurar la base, un consumidor de eventos retrasado),
    y una copia rancia en el lado permisivo es una puerta abierta. Preguntar
    devuelve siempre el estado de ahora.

    Sin gate de clearance, por lo mismo que `GET
    /internal/documentos/borradores/{id}/huella` en document-service: el
    llamante es un servicio sellado con `X-Internal-Token` y la respuesta es
    **content-free** —dos booleanos y un identificador—, sin asunto, firmantes
    ni texto que recortar por nivel de seguridad.

    `pendiente` y `completo` no son excluyentes ni opuestos: un objeto puede no
    tener ningún circuito (ambos `false`, que es el camino directo de siempre),
    tener uno abierto (`pendiente`), o haber cerrado uno y no tener ninguno
    abierto (`completo`).
    """
    # El gate busca por OBJETO, no por el tipo que declare quien pregunta.
    #
    # El identificador es un UUID, así que identifica el objeto por sí solo; el
    # tipo es una etiqueta que elige el cliente al abrir la cadena. Filtrando por
    # ambos, una cadena declarada con otro tipo sobre el MISMO borrador quedaba
    # invisible para este gate: el borrador aparecía sin circuito y se radicaba
    # con el circuito abierto. Un gate que se puede esquivar cambiando una
    # etiqueta del propio solicitante no es un gate.
    repo = CadenaRepository(conn)
    abierta = await repo.get_solicitud_por_objeto_id(objeto_id, solo_abierta=True)
    # Se devuelve también la HUELLA que firmó el último circuito cerrado, no solo
    # el booleano de que existió. Editar un borrador ya firmado es un acto
    # legítimo y frecuente del trámite —alguien recuerda un anexo, cambia una
    # cifra a última hora—, así que el sistema no puede impedirlo. Lo que no
    # puede pasar es que el texto cambie y las firmas viejas sigan valiendo: el
    # firmado deja de servir y hay que volver a recogerlas. Sin esta huella el
    # lado de document no tenía forma de notar la diferencia, y radicaba un
    # documento oficial cuyo texto nadie había aprobado mientras los asientos
    # seguían atestiguando lo contrario.
    cerrada = await conn.fetchrow(
        "SELECT payload_sha256 FROM firma_solicitud "
        "WHERE objeto_id = $1 AND estado = 'firmado' "
        "ORDER BY created_at DESC LIMIT 1",
        objeto_id,
    )
    return {
        "objeto_tipo": objeto_tipo,
        "objeto_id": objeto_id,
        "pendiente": abierta is not None,
        "completo": cerrada is not None,
        "solicitud_id": str(abierta["id"]) if abierta is not None else None,
        # Huella del texto que se firmó. Sigue siendo content-free: es un
        # resumen, no revela asunto ni cuerpo.
        "payload_sha256": cerrada["payload_sha256"] if cerrada is not None else None,
    }
