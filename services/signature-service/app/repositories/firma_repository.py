# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de firmas (signature-service). search_path fijado por la conexión."""

from __future__ import annotations

import uuid

import asyncpg

from app.core.constants import CADENA_NIVEL_AUTORITATIVO
from app.core.secret_derivation import KEY_VERSION_LEGACY

_COLS = (
    "id, objeto_tipo, objeto_id, formato_solicitado, firmante_id, "
    "payload_sha256, firma_blob, provider, sello_tiempo, created_at, "
    "certificado_pem, minio_key_firmado, nivel_conformidad, xades_level, "
    "tipo_firma, certificado_fingerprint, signed_xml_sha256, "
    "tsa_token, tsa_timestamp, tsa_cert_fingerprint, tsa_acreditado, "
    "lt_material_present, ca_fingerprint, crl_fingerprint, crl_this_update, "
    "crl_next_update, archive_timestamp_present, archive_timestamp_at, "
    "archive_tsa_cert_fingerprint, revocation_provenance, archive_timestamp_token, "
    "ocsp_present, ocsp_responder_fingerprint, ocsp_cert_status, ocsp_produced_at, "
    "ocsp_this_update, ocsp_next_update, ocsp_response_der, key_version"
)


class FirmaRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert(self, data: dict) -> asyncpg.Record:
        """Inserta una fila `firma`.

        Los campos XAdES (``certificado_pem``, ``minio_key_firmado``,
        ``nivel_conformidad``, ``xades_level``, ``certificado_fingerprint``)
        son ``None`` para firmas ``nativa`` — las columnas admiten NULL
        (migración 008, aditiva). ``tipo_firma`` **se resuelve aquí**
        (``or 'nativa'``), y no en cada llamante como decía esta frase: la
        rama nativa de la cadena de firma no lo pasaba, y como la columna es
        NOT NULL y un NULL explícito ignora el DEFAULT, firmar sin credencial
        PKI personal reventaba con ``NotNullViolationError``. El DEFAULT de
        columna, además, pertenecía a un vocabulario muerto
        (``'electronica-nativa'``, migración 002) hasta la 019. ``signed_xml_sha256``
        (migración 010) ata ``/verify`` a esta firma_id concreta — ``None``
        para ``nativa``.

        Los campos TSA (``tsa_token``, ``tsa_timestamp``,
        ``tsa_cert_fingerprint``; migración 012, E06 Inc.4) son ``None`` para
        firmas ``nativa`` o para ``xades_local`` sin sello de tiempo
        (kill-switch ``signer_timestamp_enabled=False``). ``tsa_token`` es el
        ``TimeStampToken`` DER en base64 — es material PÚBLICO (ver
        ``app.core.tsa``), no un secreto, así que persistirlo es correcto.
        ``tsa_acreditado`` es NOT NULL (default ``False`` en la migración):
        se pasa siempre ``False`` explícito (RF-FIR-15) salvo que el
        llamador indique lo contrario.

        Los campos LT/LTA (``lt_material_present``, ``ca_fingerprint``,
        ``crl_fingerprint``, ``crl_this_update``, ``crl_next_update``,
        ``archive_timestamp_present``, ``archive_timestamp_at``,
        ``archive_tsa_cert_fingerprint``, ``revocation_provenance``,
        ``archive_timestamp_token``; migración 013) son ``None``/``False``
        para cualquier firma que no escaló a XAdES-LTA (``nativa``, XAdES-B,
        XAdES-T sin material de CA local, o degradación
        ``firma.lt_downgrade_no_ca``) — mismo patrón aditivo que las columnas
        de migraciones anteriores. Ninguno de estos campos transporta material
        PRIVADO (RT-16): solo certificados/CRL públicos, huellas y fechas.

        Los campos OCSP (``ocsp_present``, ``ocsp_responder_fingerprint``,
        ``ocsp_cert_status``, ``ocsp_produced_at``, ``ocsp_this_update``,
        ``ocsp_next_update``, ``ocsp_response_der``; migración 014) son
        ``None``/``False`` para cualquier firma sin ``xades:OCSPValues``
        (``nativa``, XAdES-B/-T/-LT/-LTA sin OCSP, kill-switch
        ``settings.signer_ocsp_enabled=False``, o degradación
        ``firma.ocsp_downgrade_no_responder``) — mismo patrón aditivo.
        ``ocsp_response_der`` es la ``BasicOCSPResponse`` DER en base64 —
        material PÚBLICO (igual que ``archive_timestamp_token``), NUNCA la
        clave privada del responder OCSP.

        ``key_version`` (migración 020, AREA-04-11) es el esquema del HMAC de la
        firma nativa: 1 para lo que se firma hoy, 0 para lo anterior. Sin él,
        ``/verify`` no sabría con qué clave recomputar.
        """
        row = await self._conn.fetchrow(
            f"""INSERT INTO firma (id, objeto_tipo, objeto_id, formato_solicitado,
                firmante_id, payload_sha256, firma_blob, provider, sello_tiempo,
                certificado_pem, minio_key_firmado, nivel_conformidad, xades_level,
                tipo_firma, certificado_fingerprint, signed_xml_sha256,
                tsa_token, tsa_timestamp, tsa_cert_fingerprint, tsa_acreditado,
                lt_material_present, ca_fingerprint, crl_fingerprint,
                crl_this_update, crl_next_update, archive_timestamp_present,
                archive_timestamp_at, archive_tsa_cert_fingerprint,
                revocation_provenance, archive_timestamp_token,
                ocsp_present, ocsp_responder_fingerprint, ocsp_cert_status,
                ocsp_produced_at, ocsp_this_update, ocsp_next_update,
                ocsp_response_der, key_version)
                VALUES (COALESCE($1, gen_random_uuid()),
                        $2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
                        $18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,
                        $33,$34,$35,$36,$37,$38)
                RETURNING {_COLS}""",
            data.get("id"),
            data["objeto_tipo"],
            data.get("objeto_id"),
            data.get("formato_solicitado"),
            data.get("firmante_id"),
            data["payload_sha256"],
            data["firma_blob"],
            data["provider"],
            data["sello_tiempo"],
            data.get("certificado_pem"),
            data.get("minio_key_firmado"),
            data.get("nivel_conformidad"),
            data.get("xades_level"),
            # `or "nativa"`, y no `.get(...)` a secas: la columna es NOT NULL
            # (migración 002) y un NULL **explícito** en el INSERT **ignora el
            # DEFAULT** — el default solo actúa cuando la columna se omite. Y
            # este INSERT la nombra siempre.
            #
            # Con eso, firmar por el camino NATIVO —el de cualquiera que no
            # tenga credencial PKI personal— reventaba con
            # `NotNullViolationError` y un 500 crudo en la cara del firmante.
            # La rama personal sí fijaba el valor, así que el defecto solo
            # existía para el caso más común.
            #
            # No lo vio ninguna suite porque todas doblan la conexión con
            # `AsyncMock`: un mock acepta cualquier fila. En la base de
            # demostración no hay una sola firma `nativa` — ese camino nunca
            # llegó a Postgres.
            data.get("tipo_firma") or "nativa",
            data.get("certificado_fingerprint"),
            data.get("signed_xml_sha256"),
            data.get("tsa_token"),
            data.get("tsa_timestamp"),
            data.get("tsa_cert_fingerprint"),
            data.get("tsa_acreditado", False),
            data.get("lt_material_present", False),
            data.get("ca_fingerprint"),
            data.get("crl_fingerprint"),
            data.get("crl_this_update"),
            data.get("crl_next_update"),
            data.get("archive_timestamp_present", False),
            data.get("archive_timestamp_at"),
            data.get("archive_tsa_cert_fingerprint"),
            data.get("revocation_provenance"),
            data.get("archive_timestamp_token"),
            data.get("ocsp_present", False),
            data.get("ocsp_responder_fingerprint"),
            data.get("ocsp_cert_status"),
            data.get("ocsp_produced_at"),
            data.get("ocsp_this_update"),
            data.get("ocsp_next_update"),
            data.get("ocsp_response_der"),
            # Esquema del HMAC de la firma NATIVA (migración 020, AREA-04-11).
            # El llamante de la rama nativa lo pasa SIEMPRE desde `signer.firmar`;
            # las demás (XAdES) no tienen HMAC de servicio y quedan en 0, que es
            # también el DEFAULT de la columna — escrito aquí explícito porque un
            # NULL explícito ignoraría el DEFAULT y la columna es NOT NULL.
            data.get("key_version", KEY_VERSION_LEGACY),
        )
        assert row is not None
        return row

    async def get(
        self, firma_id: uuid.UUID, *, max_clearance: int | None = None
    ) -> asyncpg.Record | None:
        """La firma, con el NOMBRE de quien firmó resuelto.

        ``max_clearance`` (AREA-04-10): con valor, la firma de un objeto
        (radicado o expediente) cuyo nivel de seguridad supera ese clearance NO
        se devuelve — la consulta da ``None``, indistinguible de un UUID que no
        existe. El nivel viaja **dentro del ``WHERE``** y no en una comprobación
        posterior: así quien no alcanza el objeto no puede distinguir «existe y
        no puedo verlo» de «no existe» ni por el código ni por el cuerpo. Una
        firma sobre un objeto que no resuelve contra radicados/expedientes
        (índice, acta: no tienen nivel propio aquí) es visible, igual que en
        ``/sign-xml``. ``None`` (llamada de servicio a servicio sin usuario
        detrás, p. ej. archive-service verificando el sellado del índice) no
        aplica el gate.

        El comprobante de firma titulaba «Firmante» y enseñaba
        `50b64803-568d-49f7-9299-cb140279d947`: un certificado que no dice
        quién firmó no certifica nada. La fila guarda el identificador y así
        debe seguir —una firma es un hecho fechado y su fila no se toca—, así
        que el nombre se resuelve al LEER. `LEFT JOIN` y por TEXTO, mismo
        criterio que los lectores de la pista de acceso: el firmante puede no
        ser un usuario del catálogo (firmas del sistema, sellos
        institucionales) y un `::uuid` haría fallar la consulta entera por una
        fila que ni se estaba preguntando.

        El JOIN va SOLO aquí y no en `_COLS`: las demás consultas de este
        repositorio son del motor de firma, no de una pantalla, y no necesitan
        pagar la unión.
        """
        cols = ", ".join(f"f.{c.strip()}" for c in _COLS.split(","))
        # `auth_users` la crea AUTH-SERVICE: en una entidad real está en el
        # mismo schema, pero un despliegue parcial no la tiene. Una firma se
        # lee para verificarla, y que falte una tabla de otro servicio no puede
        # impedirlo — el nombre se degrada a ausente, que es lo mismo que
        # devuelve el JOIN para un sello del sistema.
        # Se comprueba la COLUMNA y no la tabla, por el mismo motivo que en
        # `TransferenciaRepository.get_acta`: la tabla puede existir sin ella.
        con_nombre = bool(
            await self._conn.fetchval(
                """SELECT EXISTS (
                       SELECT 1 FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'auth_users'
                          AND column_name = 'username')"""
            )
        )
        select = f"SELECT {cols}, u.username AS firmante_nombre" if con_nombre else f"SELECT {cols}"
        join = "LEFT JOIN auth_users u ON u.id::text = f.firmante_id::text" if con_nombre else ""
        args: list[object] = [firma_id]
        gate = ""
        if max_clearance is not None:
            args.append(max_clearance)
            try:
                return await self._conn.fetchrow(
                    f"{select} FROM firma f {join} WHERE f.id = $1 "
                    f"{self._gate_nivel(CADENA_NIVEL_AUTORITATIVO)}",
                    *args,
                )
            except asyncpg.UndefinedTableError:
                # Alguna tabla dueña del nivel falta en este schema (tenant sin
                # document- o archive-service): se descartan SOLO las ausentes.
                presentes = await self._tablas_presentes(
                    [t for t, _ in CADENA_NIVEL_AUTORITATIVO.values()]
                )
                fuentes = {
                    k: v for k, v in CADENA_NIVEL_AUTORITATIVO.items() if v[0] in presentes
                }
                gate = self._gate_nivel(fuentes)
                # Sin NINGUNA tabla dueña del nivel no hay `$2` en la consulta:
                # pasarlo igual lo rechaza el servidor (lo cazó el tier real).
                return await self._conn.fetchrow(
                    f"{select} FROM firma f {join} WHERE f.id = $1 {gate}",
                    *(args if fuentes else args[:1]),
                )
        return await self._conn.fetchrow(f"{select} FROM firma f {join} WHERE f.id = $1", *args)

    @staticmethod
    def _gate_nivel(fuentes: dict[str, tuple[str, tuple[str, ...]]]) -> str:
        """Fragmento ``AND NOT EXISTS (...)`` del no-read-up, con ``$2`` = clearance.

        Tablas y columnas salen de ``CADENA_NIVEL_AUTORITATIVO`` (constante del
        módulo), jamás del request. Se compara contra ``f.objeto_id`` por la
        identidad natural y por el UUID, igual que ``resolver_nivel_por_objeto``.
        """
        partes = []
        for tabla, id_cols in fuentes.values():
            on = " OR ".join(f"o.{c} = f.objeto_id" for c in id_cols)
            partes.append(
                f"AND NOT EXISTS (SELECT 1 FROM {tabla} o WHERE ({on}) AND o.nivel_seguridad > $2)"
            )
        return " ".join(partes)

    async def _tablas_presentes(self, tablas: list[str]) -> set[str]:
        rows = await self._conn.fetch(
            """SELECT table_name FROM information_schema.tables
                WHERE table_schema = current_schema() AND table_name = ANY($1::text[])""",
            tablas,
        )
        return {r["table_name"] for r in rows}
