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

from app.clients import storage_client
from app.clients.email_parser import CorreoLeido, leer
from app.clients.imap_adaptador import ErrorDeCorreo, ImapAdaptador
from app.repositories.email_mensaje_repository import EmailMensajeRepository
from app.schemas.document import AnexoInput, DocumentCreate
from app.services.document_service import DocumentService
from app.services.email_cuenta_service import EmailCuentaService, ErrorDeCuenta

logger = logging.getLogger(__name__)

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

    async def reservar(self, mensaje_id: uuid.UUID, cuenta_id: uuid.UUID, *, actor: uuid.UUID):
        reservado = await self._repo.reservar(
            mensaje_id, actor=actor, minutos=RESERVA_MINUTOS
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
        payload = self._componer(mensaje, correo, datos, anexos)
        resultado = await servicio.create(payload, registered_by=actor)

        # 3) Cierre en la bandeja, exigiendo otra vez la reserva: entre la
        #    comprobación de arriba y este punto pasó una subida de ficheros y
        #    una radicación, y en ese rato la reserva pudo caducar.
        cerrado = await self._repo.marcar_radicado(
            mensaje_id,
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

        return {
            "tracking_number": resultado.tracking_number,
            "radicado_id": str(resultado.id),
            "anexos": len(anexos),
            "movido_a": cuenta["carpeta_radicados"] if movido else None,
        }

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

    def _componer(
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
        """
        recibido: datetime = mensaje["fecha_recibo"]
        return DocumentCreate(
            doc_type="E",
            subject=(datos.get("subject") or mensaje["asunto"] or "(correo sin asunto)")[:2000],
            sender_name=(datos.get("sender_name") or correo.remitente_nombre or None),
            sender_entity=datos.get("sender_entity"),
            dest_dept=datos.get("dest_dept"),
            doc_class=datos.get("doc_class"),
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
        self, mensaje_id: uuid.UUID, cuenta_id: uuid.UUID, *, actor: uuid.UUID, motivo: str
    ):
        fuera = await self._repo.descartar(mensaje_id, actor=actor, motivo=motivo)
        if fuera is None:
            raise ErrorDeCuenta(
                "sin_reserva",
                "Para descartar un correo hay que tenerlo abierto: su reserva caducó "
                "o la tiene otro funcionario.",
                status=409,
            )
        return fuera
