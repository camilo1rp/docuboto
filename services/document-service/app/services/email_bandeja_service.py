# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Sincronización de la bandeja y radicación desde ella (E19 Increment 2).

**Dos fechas distintas, y no hay que confundirlas.** La *fecha de radicación* la
pone el sistema al asignar el número: da fe del acto administrativo y ningún
cliente puede fijarla — permitirlo abriría la puerta a falsear cuándo radicó la
entidad. La *fecha de recibo* es cuándo entró la comunicación en el buzón, sale
del `INTERNALDATE` del servidor de correo y viaja como dato de la comunicación,
que es de lo que la Circular Externa AGN 001/2020 exige dejar constancia.

**Orden de las operaciones al radicar.** Primero se suben los binarios, luego se
crea el radicado y por último se mueve el correo en el buzón. Se hace así porque
los tres pasos tienen reversibilidad distinta: un binario subido de más queda
huérfano (molesto, recuperable), un número de radicado consumido **no se
libera**, y un correo movido de carpeta se puede devolver a mano. Lo irreversible
va en medio, cuando ya se sabe que los soportes existen, y lo último es lo que
menos duele si falla.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
from orpycamcp_common import audit

from app.clients import storage_client
from app.clients.email_parser import CorreoLeido, leer
from app.clients.imap_adaptador import ErrorDeCorreo, ImapAdaptador
from app.core.datos_minimos import faltantes, mensaje_faltantes
from app.repositories.email_mensaje_repository import EmailMensajeRepository
from app.schemas.document import AnexoInput, DocumentCreate
from app.services import acuse_correo
from app.services.document_service import DocumentService
from app.services.email_cuenta_service import EmailCuentaService, ErrorDeCuenta
from app.services.tercero_service import TerceroService

logger = logging.getLogger(__name__)

_SERVICE = "document-service"

# Cuánto dura la reserva de un correo abierto. Suficiente para revisarlo y
# radicarlo con calma; poco para que un navegador cerrado no lo bloquee media
# jornada.
RESERVA_MINUTOS = 15

# Tope de correos que trae una sincronización. La carpeta de filtrado de una
# ventanilla puede acumular meses de correspondencia el día que se activa: sin
# tope, el primer sondeo descargaría el buzón entero en una sola petición HTTP.
# Se declara en la respuesta (`hay_mas`) en vez de recortar en silencio.
MAX_POR_SINCRONIZACION = 50


class EmailBandejaService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant = tenant_slug
        self._repo = EmailMensajeRepository(conn)
        self._cuentas = EmailCuentaService(conn, tenant_slug)

    async def _exigir_cuenta(self, cuenta_id: uuid.UUID, *, actor: uuid.UUID, es_admin: bool):
        """Único sitio que decide si este funcionario alcanza este buzón.

        **El hueco que cierra.** El gate de `PERM_RADI` dice quién puede trabajar
        correo; no dice QUÉ buzón. Ese recorte vivía —bien escrito y con su
        test— en el `WHERE` de `email_cuenta_repository`, y las operaciones de
        bandeja no pasaban por él: listar, reservar, liberar y descartar iban
        directas al mensaje por su id. Cualquier funcionario con `PERM_RADI` de
        la entidad podía, conociendo un `cuenta_id`, leer la bandeja de una
        cuenta **personal de otro** —la que el proyecto cuidó hasta el punto de
        ocultarla al propio administrador— y apartar sus correos.

        Es la segunda vez en este repositorio que un recorte correcto no alcanza
        a la puerta de al lado. Por eso hay UN método y no una comprobación
        repetida en cada endpoint: la que se repite es la que un día falta.

        `404` y no `403`: distinguirlos convertiría el endpoint en un oráculo de
        qué cuentas de correo tiene la entidad y quién las trabaja — el mismo
        criterio, y las mismas palabras, que `EmailCuentaRepository.obtener`.
        """
        cuenta = await self._cuentas._repo.obtener(cuenta_id, actor=actor, es_admin=es_admin)
        if cuenta is None:
            raise ErrorDeCuenta(
                "cuenta_no_encontrada",
                "No existe un buzón con ese identificador, o no está a su alcance.",
                status=404,
            )
        return cuenta


    # -- lecturas ----------------------------------------------------------
    #
    # Pasan por el mismo guardián que las escrituras. No es simetría estética:
    # la bandeja lista ASUNTO y REMITENTE de cada correo, así que leer la de una
    # cuenta ajena es la fuga, no un paso previo a ella.

    async def listar(
        self, cuenta_id: uuid.UUID, *, actor: uuid.UUID, es_admin: bool, estado: str = "pendiente"
    ) -> tuple[list[Any], int]:
        await self._exigir_cuenta(cuenta_id, actor=actor, es_admin=es_admin)
        filas = await self._repo.bandeja(cuenta_id, estado=estado)
        return list(filas), await self._repo.contar(cuenta_id, estado=estado)

    async def estado(
        self, cuenta_id: uuid.UUID, *, actor: uuid.UUID, es_admin: bool
    ) -> dict[str, Any]:
        await self._exigir_cuenta(cuenta_id, actor=actor, es_admin=es_admin)
        return await self._repo.estado_bandeja(cuenta_id)

    # -- sincronización ----------------------------------------------------

    async def sincronizar(
        self, cuenta_id: uuid.UUID, *, actor: uuid.UUID, es_admin: bool, password: str | None
    ) -> dict[str, Any]:
        """Trae de la carpeta de origen los correos que aún no están en la bandeja.

        Primero se listan los identificadores —que no descarga nada— y solo se
        traen los que faltan: en el caso normal, sondear un buzón sin novedades
        no mueve un solo byte de mensaje.
        """
        conexion = await self._cuentas._resolver_conexion(
            cuenta_id, actor=actor, es_admin=es_admin, password=password
        )
        fila = await self._cuentas._repo.obtener_para_conectar(
            cuenta_id, actor=actor, es_admin=es_admin
        )
        if fila is None:
            raise ErrorDeCuenta("cuenta_no_encontrada", "Cuenta no encontrada", status=404)

        carpeta = fila["carpeta_origen"]
        adaptador = ImapAdaptador(conexion)
        try:
            uidvalidity, uids = await adaptador.listar_uids(carpeta)
        except ErrorDeCorreo as exc:
            await self._cuentas._repo.registrar_sondeo(cuenta_id, error=str(exc))
            raise ErrorDeCuenta(exc.codigo, str(exc), status=502) from exc

        conocidos = await self._uids_conocidos(cuenta_id, carpeta, uidvalidity)
        pendientes = [u for u in uids if u not in conocidos]
        hay_mas = len(pendientes) > MAX_POR_SINCRONIZACION

        nuevos = 0
        for uid in pendientes[:MAX_POR_SINCRONIZACION]:
            if await self._traer_uno(adaptador, cuenta_id, carpeta, uidvalidity, uid):
                nuevos += 1

        await self._cuentas._repo.registrar_sondeo(cuenta_id, error=None)
        return {
            "nuevos": nuevos,
            "en_bandeja": await self._repo.contar(cuenta_id),
            "hay_mas": hay_mas,
            "carpeta": carpeta,
        }

    async def _uids_conocidos(
        self, cuenta_id: uuid.UUID, carpeta: str, uidvalidity: int
    ) -> set[int]:
        filas = await self._conn.fetch(
            """
            SELECT uid FROM email_mensaje
             WHERE cuenta_id = $1 AND carpeta = $2 AND uidvalidity = $3
            """,
            cuenta_id, carpeta, uidvalidity,
        )
        return {int(f["uid"]) for f in filas}

    async def _traer_uno(
        self,
        adaptador: ImapAdaptador,
        cuenta_id: uuid.UUID,
        carpeta: str,
        uidvalidity: int,
        uid: int,
    ) -> bool:
        """Descarga un correo y lo registra. `False` si ya estaba o si falló.

        Un correo que no se puede leer **no tumba la sincronización**: se anota y
        se sigue con los demás. Un MIME roto en el buzón dejaría la ventanilla
        sin poder traer nada más, y ese correo bloquearía a todos los que vengan
        detrás.
        """
        try:
            crudo, recibido = await adaptador.traer(carpeta, uid)
        except ErrorDeCorreo:
            logger.warning("no se pudo traer el mensaje uid=%s de %s", uid, carpeta)
            return False

        respaldo = f"orpyca-local:{cuenta_id}/{carpeta}/{uidvalidity}/{uid}"
        try:
            correo = leer(crudo, respaldo_id=respaldo)
        except Exception:  # noqa: BLE001 — un MIME roto no debe parar la bandeja
            logger.warning("mensaje ilegible uid=%s de %s", uid, carpeta, exc_info=True)
            return False

        registrado = await self._repo.registrar_si_es_nuevo(
            {
                "cuenta_id": cuenta_id,
                "message_id": correo.message_id[:998],
                "carpeta": carpeta,
                "uidvalidity": uidvalidity,
                "uid": uid,
                "remitente_nombre": correo.remitente_nombre[:500] or None,
                "remitente_email": correo.remitente_email[:320] or None,
                "asunto": (correo.asunto or "(correo sin asunto)")[:2000],
                # Sin `INTERNALDATE` se usa el momento de la sincronización, y la
                # respuesta lo dirá: es lo único honesto cuando el servidor no
                # informa la hora de recibo, pero no puede pasar por ser la real.
                "fecha_recibo": recibido or datetime.now(UTC),
                "num_adjuntos": len(correo.adjuntos),
            }
        )
        return registrado is not None

    # -- trabajo sobre un correo -------------------------------------------

    async def reservar(
        self,
        mensaje_id: uuid.UUID,
        cuenta_id: uuid.UUID,
        *,
        actor: uuid.UUID,
        es_admin: bool,
    ):
        await self._exigir_cuenta(cuenta_id, actor=actor, es_admin=es_admin)
        reservado = await self._repo.reservar(
            mensaje_id, cuenta_id, actor=actor, minutos=RESERVA_MINUTOS
        )
        if reservado is None:
            # No se dice QUIÉN lo tiene: el listado ya lo muestra, y aquí el
            # mensaje sirve para explicar por qué no se puede seguir.
            raise ErrorDeCuenta(
                "correo_ocupado",
                "Otro funcionario está trabajando este correo. Actualice la bandeja "
                "para ver quién lo tiene.",
                status=409,
            )
        return reservado

    async def liberar(
        self,
        mensaje_id: uuid.UUID,
        cuenta_id: uuid.UUID,
        *,
        actor: uuid.UUID,
        es_admin: bool,
    ) -> None:
        """Suelta la reserva propia.

        **No distingue «solté algo» de «no había nada que soltar», y es
        deliberado.** Las dos situaciones acaban igual —el correo queda libre
        para quien lo necesite— y la diferencia solo serviría para decirle a un
        funcionario que la reserva la tiene otro, cosa que la bandeja ya muestra.
        Un 404 aquí convertiría en error una reserva que simplemente había
        caducado mientras leía.

        No toca la reserva de otro: el `WHERE` del repositorio exige que sea la
        suya.
        """
        await self._exigir_cuenta(cuenta_id, actor=actor, es_admin=es_admin)
        await self._repo.liberar(mensaje_id, cuenta_id, actor=actor)

    async def radicar(
        self,
        mensaje_id: uuid.UUID,
        cuenta_id: uuid.UUID,
        *,
        actor: uuid.UUID,
        es_admin: bool,
        password: str | None,
        datos: dict[str, Any],
        servicio: DocumentService,
    ) -> dict[str, Any]:
        """Convierte el correo reservado en un radicado de Entrada."""
        mensaje = await self._repo.obtener(mensaje_id, cuenta_id)
        if mensaje is None:
            raise ErrorDeCuenta("correo_no_encontrado", "Correo no encontrado", status=404)
        if mensaje["estado"] != "pendiente":
            raise ErrorDeCuenta(
                "correo_ya_resuelto",
                f"Este correo ya está {mensaje['estado']}"
                + (f" con el número {mensaje['tracking_number']}" if mensaje["tracking_number"] else "")
                + ".",
                status=409,
            )
        if mensaje["reservado_por"] != actor or not mensaje["reservado_vigente"]:
            raise ErrorDeCuenta(
                "sin_reserva",
                "Su reserva sobre este correo caducó o la tiene otro funcionario. "
                "Vuelva a abrirlo antes de radicar.",
                status=409,
            )

        # Ac. AGN 060/2001 §5.2 (AREA-09-02): el correo es una Entrada y exige
        # tipo documental y dependencia destino. Se comprueba ANTES de traer el
        # correo y de subir ningún binario: faltar aquí no cuesta ni un fichero.
        codigo_dest = datos.get("dest_dept_code")
        falta = faltantes(
            "E",
            doc_class=datos.get("doc_class") or None,
            origin_dept_code=None,
            dest_dept_code=int(str(codigo_dest)) if codigo_dest not in (None, "") else None,
        )
        if falta:
            raise ErrorDeCuenta(
                "datos_minimos_incompletos", mensaje_faltantes("E", falta), status=422
            )

        cuenta = await self._cuentas._repo.obtener_para_conectar(
            cuenta_id, actor=actor, es_admin=es_admin
        )
        if cuenta is None:
            raise ErrorDeCuenta("cuenta_no_encontrada", "Cuenta no encontrada", status=404)

        conexion = await self._cuentas._resolver_conexion(
            cuenta_id, actor=actor, es_admin=es_admin, password=password
        )
        adaptador = ImapAdaptador(conexion)
        try:
            # Se exige el mismo UIDVALIDITY con el que se registró: si la carpeta
            # se recreó, este `uid` ya no es este mensaje.
            crudo, _ = await adaptador.traer(
                mensaje["carpeta"],
                int(mensaje["uid"]),
                uidvalidity_esperado=int(mensaje["uidvalidity"]),
            )
        except ErrorDeCorreo as exc:
            raise ErrorDeCuenta(exc.codigo, str(exc), status=502) from exc
        correo = leer(crudo, respaldo_id=mensaje["message_id"])

        # 1) Binarios primero: si algo falla aquí, no se ha consumido ningún
        #    número de radicado.
        anexos = await self._subir_soportes(correo, crudo, mensaje, actor=actor)

        # 2) El acto irreversible.
        payload = await self._componer(mensaje, correo, datos, anexos)
        resultado = await servicio.create(payload, registered_by=actor)

        # 3) Cierre en la bandeja, exigiendo otra vez la reserva: entre la
        #    comprobación de arriba y este punto pasó una subida de ficheros y
        #    una radicación, y en ese rato la reserva pudo caducar.
        cerrado = await self._repo.marcar_radicado(
            mensaje_id,
            cuenta_id,
            actor=actor,
            radicado_id=resultado.id,
            tracking_number=resultado.tracking_number,
        )
        if cerrado is None:
            # El radicado YA existe: no se deshace (el número es inmutable). Se
            # informa para que quede constancia en vez de fingir que no pasó.
            logger.error(
                "radicado %s creado pero el correo %s no se pudo cerrar en la bandeja",
                resultado.tracking_number, mensaje_id,
            )

        # 4) Mover el correo es lo último y lo más recuperable: si falla, el
        #    radicado existe igual y el correo se puede mover a mano.
        movido = await self._mover(adaptador, mensaje, cuenta["carpeta_radicados"])

        # 5) La constancia al remitente, si la entidad la activó para este buzón.
        #    Va al final y es best-effort a propósito: el número ya está asignado
        #    y es inmutable, así que un fallo de correo no puede deshacer nada.
        acuse = await self._acusar(mensaje, cuenta, resultado.tracking_number)

        return {
            "tracking_number": resultado.tracking_number,
            "radicado_id": str(resultado.id),
            "anexos": len(anexos),
            "movido_a": cuenta["carpeta_radicados"] if movido else None,
            "acuse": acuse,
        }

    async def _acusar(
        self, mensaje: asyncpg.Record, cuenta: asyncpg.Record, tracking_number: str
    ) -> dict[str, Any]:
        """Constancia de radicación al remitente. Nunca lanza.

        Devuelve qué pasó para que la interfaz pueda decirlo: «no se envió porque
        el correo no traía remitente» y «no se pudo enviar» son cosas distintas, y
        el operador necesita distinguirlas — en el segundo caso el ciudadano se
        quedó sin su número.
        """
        if not cuenta["acuse_al_remitente"]:
            return {"enviado": False, "motivo": None}

        veredicto = acuse_correo.destinatario_valido(
            mensaje["remitente_email"], direccion_cuenta=cuenta["direccion"]
        )
        if not veredicto.enviado:
            await self._repo.registrar_acuse(mensaje["id"], error=veredicto.motivo)
            return {"enviado": False, "motivo": veredicto.motivo}

        recibido: datetime = mensaje["fecha_recibo"]
        resultado = await acuse_correo.enviar(
            tenant_slug=self._tenant,
            destinatario=str(mensaje["remitente_email"]),
            tracking_number=tracking_number,
            asunto=str(mensaje["asunto"] or "(sin asunto)"),
            recibido=recibido.strftime("%d/%m/%Y %H:%M"),
        )
        await self._repo.registrar_acuse(
            mensaje["id"], error=None if resultado.enviado else resultado.motivo
        )
        if not resultado.enviado:
            logger.warning(
                "el radicado %s se creó pero la constancia no salió: %s",
                tracking_number, resultado.motivo,
            )
        return {"enviado": resultado.enviado, "motivo": resultado.motivo}

    async def _subir_soportes(
        self, correo: CorreoLeido, crudo: bytes, mensaje: asyncpg.Record, *, actor: uuid.UUID
    ) -> list[dict[str, Any]]:
        """Sube los adjuntos y el mensaje original.

        El `.eml` íntegro se guarda **siempre**, aunque el correo no traiga
        adjuntos: la Ley 527/1999 art. 12 exige conservar el mensaje en el
        formato en que se recibió, con su origen, destino, fecha y hora — datos
        que viven en las cabeceras y que una transcripción a texto pierde.

        Los ficheros se atribuyen al funcionario que radica (`uploaded_by`), no
        al sistema: es él quien los incorpora al expediente y quien responde del
        acto. Sin ese dato storage rechaza la subida —exige `PERM_RADI` de un
        usuario concreto—, lo que además impide que un proceso sin identidad meta
        soportes en el archivo de la entidad.
        """
        subidos: list[dict[str, Any]] = []
        for adjunto in correo.adjuntos:
            archivo = await storage_client.upload_bytes(
                tenant_slug=self._tenant,
                filename=adjunto.nombre,
                mime_type=adjunto.tipo_mime,
                data=adjunto.contenido,
                uploaded_by=actor,
            )
            subidos.append(
                {"file_id": archivo.file_id, "filename": adjunto.nombre, "sha256": adjunto.sha256}
            )

        # El mensaje original va por la puerta de ARTEFACTOS, no por la de
        # anexos: no es un soporte que aporte el ciudadano, es lo que el sistema
        # conserva para poder acreditar origen, destino y hora.
        original = await storage_client.upload_artefacto(
            tenant_slug=self._tenant,
            filename=f"mensaje-original-{mensaje['uid']}.eml",
            mime_type="message/rfc822",
            data=crudo,
        )
        await self._repo.guardar_crudo(mensaje["id"], file_id=original.file_id)
        return subidos

    async def _componer(
        self,
        mensaje: asyncpg.Record,
        correo: CorreoLeido,
        datos: dict[str, Any],
        anexos: list[dict[str, Any]],
    ) -> DocumentCreate:
        """Arma el radicado con lo del correo y lo que el funcionario completó.

        Los datos mínimos del Acuerdo 060/2001 art. 2 —remitente, asunto,
        anexos— salen del correo; el funcionario aporta lo que el correo no
        puede saber: el tipo documental y la dependencia competente.

        **`dest_dept_code` y no solo `dest_dept`.** `dest_dept` es un nombre de
        texto libre; `dest_dept_code` es el código del organigrama, que
        document-service valida contra tenant-service y con el que denormaliza el
        nombre (RF-RAD). Radicando solo con el nombre, un radicado nacido de un
        correo quedaba SIN vínculo con la dependencia: no aparecía al repartir por
        código, y bastaba una tilde de diferencia para que fueran dos áreas
        distintas. Los radicados nacidos en ventanilla nunca tuvieron ese
        problema porque el asistente de `/radicar` siempre mandó el código.
        """
        recibido: datetime = mensaje["fecha_recibo"]
        codigo_destino: Any = datos.get("dest_dept_code")

        # El remitente entra al DIRECTORIO. En este canal la dirección de correo
        # ES la identidad del corresponsal —igual que el documento lo es en la
        # ventanilla—, así que se reutiliza la ficha que ya la tenga en vez de
        # dejar otro nombre suelto en `sender_name`. Es la puerta de más
        # volumen: sin esto, el mismo remitente escribiendo cada semana eran
        # cincuenta textos distintos y ninguna ficha.
        #
        # `resolver_por_email` devuelve `None` —y se radica como antes— si falta
        # la dirección o el nombre, o si hay varias fichas con ese correo: ahí
        # elegir una sería decidir en silencio a quién pertenece el documento.
        nombre_remitente = datos.get("sender_name") or correo.remitente_nombre or None
        ficha = await TerceroService(self._conn, self._tenant).resolver_por_email(
            correo.remitente_email, nombre=nombre_remitente, actor=None
        )

        return DocumentCreate(
            doc_type="E",
            subject=(datos.get("subject") or mensaje["asunto"] or "(correo sin asunto)")[:2000],
            sender_name=(datos.get("sender_name") or correo.remitente_nombre or None),
            sender_entity=datos.get("sender_entity"),
            # El nombre se conserva como respaldo para las cuentas que traen su
            # `dependencia_destino` escrita: si viene el código, document-service
            # denormaliza el nombre bueno y este queda sobrescrito.
            dest_dept=datos.get("dest_dept"),
            dest_dept_code=int(codigo_destino) if codigo_destino not in (None, "") else None,
            doc_class=datos.get("doc_class") or None,
            observations=(correo.cuerpo or None) and correo.cuerpo[:4000],
            metadata={
                "source": "email",
                "sender_email": correo.remitente_email,
                "message_id": mensaje["message_id"],
                # La fecha de RECIBO de la comunicación, distinta de la de
                # radicación: esta la pone el sistema y da fe del acto.
                "recibido_at": recibido.isoformat(),
                "buzon_carpeta": mensaje["carpeta"],
                "anexos_sha256": [a["sha256"] for a in anexos],
            },
            anexos=[
                AnexoInput(file_id=a["file_id"], filename=a["filename"]) for a in anexos
            ],
            actores=(
                [{"tercero_id": ficha["id"], "rol": "remitente", "principal": True}]
                if ficha
                else []
            ),
        )

    async def _mover(
        self, adaptador: ImapAdaptador, mensaje: asyncpg.Record, destino: str
    ) -> bool:
        try:
            await adaptador.mover(
                mensaje["carpeta"],
                int(mensaje["uid"]),
                destino,
                uidvalidity_esperado=int(mensaje["uidvalidity"]),
            )
            return True
        except ErrorDeCorreo:
            logger.warning(
                "el correo %s se radicó pero no se pudo mover a «%s»", mensaje["id"], destino
            )
            return False

    async def descartar(
        self,
        mensaje_id: uuid.UUID,
        cuenta_id: uuid.UUID,
        *,
        actor: uuid.UUID,
        es_admin: bool,
        motivo: str,
    ):
        """Aparta un correo que llegó a la carpeta y no debía radicarse.

        **El único acto de la bandeja que no produce nada.** Radicar deja un
        radicado, con su número inmutable y su asiento; descartar dejaba solo un
        `UPDATE` sobre `email_mensaje`, que es una tabla corriente: quién lo
        descartó y por qué se podían reescribir después sin rastro. Y decidir que
        una comunicación recibida **no** se registra es tan decisión como
        registrarla — de hecho es la que un tercero querría poder revisar, porque
        es la que hace desaparecer el documento del archivo.

        Por eso el asiento va en la MISMA transacción que el descarte y **no es
        best-effort**: si la cadena inmutable no puede recibirlo, el correo no se
        descarta. Es el criterio de `anulacion_service`, y por la misma razón —
        aquí el asiento no acompaña al acto, ES el registro del acto.

        El asiento no lleva el asunto ni el remitente. Un correo descartado nunca
        se radicó, así que no tiene nivel de clasificación con el que recortar su
        lectura: meter su contenido en `audit_log` lo publicaría a todo
        `USUA_PERM_ADMIN`. Queda en `email_mensaje`, donde lo recorta el
        `WHERE` de la cuenta. El motivo sí va: lo escribe el funcionario y es el
        texto de su decisión, no el del documento.
        """
        await self._exigir_cuenta(cuenta_id, actor=actor, es_admin=es_admin)
        async with self._conn.transaction():
            fuera = await self._repo.descartar(mensaje_id, cuenta_id, actor=actor, motivo=motivo)
            if fuera is None:
                raise ErrorDeCuenta(
                    "sin_reserva",
                    "Para descartar un correo hay que tenerlo abierto: su reserva caducó "
                    "o la tiene otro funcionario.",
                    status=409,
                )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant,
                service=_SERVICE,
                action="email.mensaje.descartado",
                # NO está en la lista de admisión de `audit_query_repository`, y
                # eso es lo correcto: un correo sin clasificar se trata como lo
                # más restrictivo, no como configuración.
                object_type="email_mensaje",
                object_ref=str(mensaje_id),
                actor=str(actor),
                payload={"cuenta_id": str(cuenta_id), "motivo": motivo},
                canal=audit.CANAL_PETICION,
            )
        return fuera
