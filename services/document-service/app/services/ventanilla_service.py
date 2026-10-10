# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Ventanilla única de radicación — la puerta por la que entra lo de fuera.

Sustituye a la radicación «de PQRS», que asumía que lo único que llega sin
autenticar es una petición ciudadana. Una ventanilla única recibe **toda** la
correspondencia externa: tutelas, oficios de otras entidades, facturas,
invitaciones, hojas de vida.

Tres decisiones de fondo, cada una con su motivo:

1. **El vocabulario sale del catálogo de tipos documentales de la entidad**, que
   es el que gobierna la TRD. Lo que entra por la ventanilla queda clasificado
   desde el primer momento y hereda retención y disposición final (Ley 594/2000
   arts. 21-24), en vez de entrar sin serie a la espera de que alguien lo
   clasifique. Opt-in por `admite_ventanilla_publica`: un tipo interno no
   aparece en un formulario público solo porque exista.

2. **Lo radicado se enruta a la dependencia de ventanilla**, no queda sin
   destino. Antes entraba con `dest_dept` nulo: no estaba en la bandeja de
   nadie, y un derecho de petición con quince días hábiles de plazo que nadie
   ve es exactamente cómo se incumple un término legal. La dependencia se
   resuelve por `dependencias.depe_rad_tp2` —la bandera «habilita radicación
   web» que el modelo traía desde Orfeo y que nadie usaba—, así que **la
   entidad declara qué ventanilla recibe**, y si no ha declarado ninguna la
   radicación pública se niega en vez de dejar el documento en el limbo.

3. **No se crea el paso de flujo aquí.** El radicado entra con su dependencia
   destino y la distribución la hace una persona de correspondencia, igual que
   con lo que se radica en mostrador. Inventar el paso desde este servicio
   sería además una escritura sobre el dominio de workflow-service.

El catálogo de tipos y el de dependencias viven en el MISMO schema de tenant
(ADR-002/012), así que se leen con la conexión que ya está en curso, sin salto
HTTP — el mismo criterio que `resolve_caller_clearance` con las tablas RBAC.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import asyncpg
from fastapi import HTTPException, status

from app.clients.storage_client import StorageContentError, upload_anexo_externo
from app.clients.tenant_client import TenantServiceError
from app.core.anexos_publicos import (
    AnexoRechazado,
    tipo_por_contenido,
    validar_conjunto,
)
from app.core.politica import Politica, politica_de
from app.schemas.document import AnexoInput, DocumentCreate
from app.schemas.public import (
    PoliticaTratamiento,
    TipoVentanilla,
    VentanillaEstado,
    VentanillaRadicacion,
    VentanillaRespuesta,
)
from app.services.document_service import DuplicateSubmissionError
from app.services.tercero_service import TerceroService

#: Canal con el que queda marcado lo que entra por aquí. Distingue en los
#: metadatos —y en los informes— lo presentado desde fuera de lo radicado en
#: mostrador por un funcionario.
CANAL = "ventanilla_publica"


class VentanillaService:
    def __init__(
        self, conn: asyncpg.Connection, tenant_slug: str, tenant_client: Any = None
    ) -> None:
        self._conn = conn
        self._tenant = tenant_slug
        self._tenant_client = tenant_client

    # -- Política de tratamiento (Ley 1581/2012, ADR-031) -------------------

    async def _politica(self) -> Politica:
        """La política vigente de la entidad, con lectura ESTRICTA.

        `get_params` traga los fallos y devolvería `{}`: un error de tenant-service
        se leería como «la entidad no tiene política». Aquí un fallo es 503
        `politica_no_verificable`, distinto del 409 de la política ausente.
        """
        if self._tenant_client is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "politica_no_verificable",
                    "detail": "No se pudo verificar la política de tratamiento.",
                    "status": 503,
                },
            )
        try:
            params = await self._tenant_client.get_params_estricto(tenant_slug=self._tenant)
        except TenantServiceError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "politica_no_verificable",
                    "detail": (
                        "No se pudo verificar la política de tratamiento de datos de la "
                        "entidad. No se radicó nada: reintente en un momento."
                    ),
                    "status": 503,
                },
            ) from exc
        return politica_de(params)

    async def politica(self) -> PoliticaTratamiento:
        """Lo que el formulario debe mostrar antes de pedir la autorización."""
        pol = await self._politica()
        if not pol.publicada:
            return PoliticaTratamiento(publicada=False)
        return PoliticaTratamiento(
            publicada=True, version=pol.version, url=pol.url, texto=pol.texto, huella=pol.huella
        )

    async def _exigir_politica_vigente(self, version_mostrada: str) -> Politica:
        """409 si no hay política publicada o si la mostrada ya no es la vigente."""
        pol = await self._politica()
        if not pol.publicada:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "politica_no_publicada",
                    "detail": (
                        "Esta entidad todavía no ha publicado su política de tratamiento "
                        "de datos personales: sin aviso de privacidad no hay autorización "
                        "informada (Ley 1581/2012 art. 12). Preséntese en la ventanilla "
                        "de la entidad."
                    ),
                    "status": 409,
                },
            )
        if version_mostrada.strip() != pol.version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "politica_desactualizada",
                    "detail": (
                        "La política de tratamiento cambió mientras diligenciaba el "
                        "formulario. Léala de nuevo y autorice otra vez. No se radicó nada."
                    ),
                    "politica_vigente": pol.version,
                    "status": 409,
                },
            )
        return pol

    # -- Catálogo -----------------------------------------------------------

    async def tipos_admitidos(self) -> list[TipoVentanilla]:
        """Lo que la entidad declara recibir por ventanilla pública.

        Es público a propósito: sin él, el formulario no se puede construir y
        quien radica tendría que adivinar el código de un tipo documental. No
        expone nada que la entidad no haya marcado explícitamente.
        """
        filas = await self._conn.fetch(
            "SELECT code, nombre FROM tipos_documentales "
            "WHERE activo AND admite_ventanilla_publica ORDER BY nombre"
        )
        return [TipoVentanilla(code=f["code"], nombre=f["nombre"]) for f in filas]

    async def estado(self) -> VentanillaEstado:
        """¿Puede esta entidad recibir radicación pública YA? (AREA-09-07).

        Misma condición que `radicar` aplica por dentro —una dependencia activa con
        `depe_rad_tp2` y al menos un tipo admitido—, calculada sin radicar nada.
        """
        receptora = bool(
            await self._conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM dependencias WHERE activo AND depe_rad_tp2)"
            )
        )
        tipos = int(
            await self._conn.fetchval(
                "SELECT COUNT(*) FROM tipos_documentales WHERE activo AND admite_ventanilla_publica"
            )
            or 0
        )
        return VentanillaEstado(
            habilitada=receptora and tipos > 0,
            dependencia_receptora=receptora,
            tipos_admitidos=tipos,
            politica_publicada=(await self._politica()).publicada,
        )

    async def _validar_tipo(self, code: str) -> str:
        """El tipo tiene que estar declarado admisible. Nunca se reclasifica solo.

        Aceptar un código no admisible «poniéndole otro tipo» cambiaría la serie
        TRD y con ella la retención del documento, en silencio y sin que nadie
        lo decida.
        """
        nombre = await self._conn.fetchval(
            "SELECT nombre FROM tipos_documentales "
            "WHERE code = $1 AND activo AND admite_ventanilla_publica",
            code,
        )
        if nombre is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "tipo_no_admitido_en_ventanilla",
                    "detail": (
                        f"La entidad no recibe '{code}' por la ventanilla pública. "
                        "Consulte los tipos disponibles antes de radicar."
                    ),
                    "status": 422,
                },
            )
        return str(nombre)

    # -- Destino ------------------------------------------------------------

    async def _dependencia_de_ventanilla(self) -> tuple[int, str]:
        """La dependencia que recibe lo presentado desde fuera.

        `depe_rad_tp2` es la bandera «habilita radicación web» del modelo
        heredado de Orfeo. Si la entidad no ha marcado ninguna, la radicación
        pública **se niega**: dejar entrar el documento sin destino lo pondría
        fuera de la bandeja de todos, que es peor que no admitirlo — quien
        radica se iría con un número creyendo que su trámite empezó.

        Si hay varias marcadas se toma la de menor código, de forma
        determinista, y se deja que la entidad lo corrija en su organigrama.
        """
        fila = await self._conn.fetchrow(
            "SELECT codigo, nombre FROM dependencias "
            "WHERE activo AND depe_rad_tp2 ORDER BY codigo LIMIT 1"
        )
        if fila is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "ventanilla_no_habilitada",
                    "detail": (
                        "Esta entidad todavía no ha habilitado la radicación en línea. "
                        "Preséntese en la ventanilla de radicación de la entidad."
                    ),
                    "status": 503,
                },
            )
        return int(fila["codigo"]), str(fila["nombre"])

    # -- Radicación ---------------------------------------------------------

    async def _subir_anexos(self, ficheros: list[tuple[str, bytes]]) -> list[AnexoInput]:
        """Traslada a storage lo que llegó y devuelve los anexos del radicado.

        El orden importa: primero se valida TODO el conjunto y el contenido de
        cada fichero, y solo después se sube. Subir mientras se valida dejaría
        objetos huérfanos en el almacén cuando el tercero es el que falla — y en
        una superficie anónima, huérfanos es otra forma de decir almacenamiento
        gratuito.
        """
        if not ficheros:
            return []
        validar_conjunto([len(datos) for _, datos in ficheros])

        # El MIME sale de la firma del contenido, NO del `Content-Type` que
        # escribió el cliente: si no se comprueba, la lista de formatos es una
        # cortesía que se esquiva cambiando una cabecera.
        verificados = [(nombre, datos, tipo_por_contenido(datos)) for nombre, datos in ficheros]

        anexos: list[AnexoInput] = []
        for nombre, datos, mime in verificados:
            try:
                registro = await upload_anexo_externo(
                    tenant_slug=self._tenant, filename=nombre, mime_type=mime, data=datos
                )
            except StorageContentError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "error": "anexo_no_almacenado",
                        "detail": (
                            "No se pudo guardar uno de los archivos adjuntos. No se radicó "
                            "nada: vuelva a intentarlo para no quedarse con un número de "
                            "radicado al que le faltan sus soportes."
                        ),
                        "status": 503,
                    },
                ) from exc
            anexos.append(
                AnexoInput(
                    file_id=registro.file_id,
                    filename=nombre,
                    file_size=len(datos),
                    mime_type=mime,
                    es_principal=not anexos,
                )
            )
        return anexos

    async def _respuesta_existente(self, clave: str, huella: str) -> VentanillaRespuesta | None:
        """Lo que ya radicó esta `Idempotency-Key`, si lo hay.

        Con el MISMO contenido es un reintento y se devuelve el mismo radicado. Con
        contenido distinto es un mal uso de la clave: 409, no un radicado ajeno.
        """
        fila = await self._conn.fetchrow(
            "SELECT k.huella, r.id, r.tracking_number, r.verification_code, r.dest_dept "
            "FROM public_submission_keys k JOIN radicados r ON r.id = k.radicado_id "
            "WHERE k.key = $1",
            clave,
        )
        if fila is None:
            return None
        if fila["huella"] != huella:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "idempotency_key_reutilizada",
                    "detail": "Esa clave ya se usó con un contenido distinto.",
                    "status": 409,
                },
            )
        return VentanillaRespuesta(
            radicado_id=fila["id"],
            tracking_number=fila["tracking_number"],
            verification_code=fila["verification_code"],
            dependencia_destino=fila["dest_dept"],
        )

    async def radicar(
        self,
        payload: VentanillaRadicacion,
        document_service: Any,
        ficheros: list[tuple[str, bytes]] | None = None,
        *,
        idempotency_key: str | None = None,
        ip: str | None = None,
    ) -> VentanillaRespuesta:
        await self._validar_tipo(payload.tipo_documental)
        codigo_dep, nombre_dep = await self._dependencia_de_ventanilla()

        # Idempotencia (AREA-02-07). La huella es del contenido VALIDADO, no del
        # texto crudo: dos serializaciones del mismo formulario son el mismo envío.
        submission_key: tuple[str, str] | None = None
        if idempotency_key:
            huella = hashlib.sha256(
                payload.model_dump_json(exclude={"sitio_web"}).encode()
            ).hexdigest()
            # Camino barato ANTES de subir anexos: un reintento no vuelve a pasar
            # por storage. La garantía real (carrera) la da la reclamación dentro
            # de la transacción del radicado.
            previo = await self._respuesta_existente(idempotency_key, huella)
            if previo is not None:
                return previo
            submission_key = (idempotency_key, huella)

        # Política vigente (Ley 1581, ADR-031): el servidor la relee y contrasta
        # la versión que el cliente mostró. Va ANTES de subir anexos, de reclamar
        # la `Idempotency-Key` y de consumir el consecutivo: un 409 no deja
        # rastro. Va DESPUÉS del camino de reintento, que devuelve lo ya radicado
        # (la autorización se dio bajo la política que regía entonces).
        politica = await self._exigir_politica_vigente(payload.politica_version)

        # Los anexos se resuelven ANTES de crear el radicado: si algo falla, no
        # queda un número asignado —inmutable por Ley 594— sin sus soportes.
        try:
            anexos = await self._subir_anexos(ficheros or [])
        except AnexoRechazado as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "anexo_rechazado", "detail": str(exc), "status": 422},
            ) from exc

        # ── El remitente entra al DIRECTORIO, si se identificó ──────────────
        #
        # Hasta ahora sus datos se guardaban como JSON suelto en
        # `metadata.remitente` y no producían ninguna ficha: la ventanilla
        # ciudadana, que es la puerta de más volumen, no alimentaba el catálogo.
        # El mismo ciudadano que escribe tres veces era tres textos distintos.
        #
        # **Solo con identificación.** El alta es idempotente POR DOCUMENTO
        # (`crear_o_recuperar`), así que quien se identifica converge siempre a
        # la misma ficha. Sin documento no hay nada que reconocer, y crear una
        # ficha por cada petición anónima sería una fábrica de duplicados —
        # exactamente lo contrario de lo que esto persigue— además de construir
        # un directorio de «anónimos» que no sirve a nadie. La petición anónima
        # es legítima (Ley 1755/2015 art. 16, denuncia anónima) y se queda como
        # está: con su `metadata.remitente` y sin ficha.
        actores: list[dict[str, Any]] = []
        if payload.identificacion and payload.identificacion.strip():
            tercero, _ = await TerceroService(
                self._conn, self._tenant
            ).crear_o_recuperar(
                {
                    "tipo": "entidad_publica" if payload.entidad else "ciudadano",
                    "nombre": payload.nombre,
                    "entidad": payload.entidad,
                    "tipo_documento": payload.tipo_identificacion,
                    "numero_documento": payload.identificacion.strip(),
                    "email": payload.email,
                    "telefono": payload.telefono,
                    "direccion": payload.direccion,
                },
                actor=None,
            )
            actores = [
                {"tercero_id": tercero["id"], "rol": "remitente", "principal": True}
            ]

        doc = DocumentCreate(
            doc_type="E",
            subject=payload.asunto,
            doc_class=payload.tipo_documental,
            dest_dept_code=codigo_dep,
            sender_name=payload.nombre,
            sender_entity=payload.entidad,
            observations=payload.descripcion,
            anexos=anexos,
            actores=actores,
            metadata={
                "canal": CANAL,
                # Ley 1581/2012 (AREA-09-08, ADR-031): constancia de la autorización
                # de tratamiento. Versión, huella y URL de la política salen del
                # servidor (no del cliente). `ip` es la que el gateway FIRMÓ
                # (saltos de confianza); sin firma válida va nula y
                # `ip_verificada=false`: la IP es evidencia complementaria, no
                # requisito de la autorización. Las constancias v1 (`ip_declarada`)
                # se conservan tal cual.
                "consentimiento": {
                    "norma": "Ley 1581/2012",
                    "version_constancia": 2,
                    "politica_version": politica.version,
                    "politica_huella": politica.huella,
                    "politica_url": politica.url,
                    "instante": datetime.now(UTC).isoformat(),
                    "ip": ip,
                    "ip_verificada": ip is not None,
                },
                "remitente": {
                    "nombre": payload.nombre,
                    "entidad": payload.entidad,
                    "tipo_identificacion": payload.tipo_identificacion,
                    "identificacion": payload.identificacion,
                    "email": payload.email,
                    "telefono": payload.telefono,
                    "direccion": payload.direccion,
                },
            },
        )
        try:
            result = await document_service.create(
                doc, registered_by=None, submission_key=submission_key
            )
        except DuplicateSubmissionError:
            # Carrera: otra petición con la misma clave ganó mientras esta preparaba
            # el envío. Ya hay un radicado confirmado; se devuelve ese.
            assert submission_key is not None
            previo = await self._respuesta_existente(*submission_key)
            if previo is None:  # imposible tras el commit del ganador; fail-closed
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "idempotency_key_en_curso",
                        "detail": "Esa clave está siendo procesada. Reintente en un momento.",
                        "status": 409,
                    },
                ) from None
            return previo
        code = await self._conn.fetchval(
            "SELECT verification_code FROM radicados WHERE id = $1", result.id
        )
        return VentanillaRespuesta(
            radicado_id=result.id,
            tracking_number=result.tracking_number,
            verification_code=code,
            dependencia_destino=nombre_dep,
        )
