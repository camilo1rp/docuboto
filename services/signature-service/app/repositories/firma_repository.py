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

_COLS = (
    "id, objeto_tipo, objeto_id, formato_solicitado, firmante_id, "
    "payload_sha256, firma_blob, provider, sello_tiempo, created_at, "
    "certificado_pem, minio_key_firmado, nivel_conformidad, xades_level, "
    "tipo_firma, certificado_fingerprint, signed_xml_sha256, "
    "tsa_token, tsa_timestamp, tsa_cert_fingerprint, tsa_acreditado"
)


class FirmaRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert(self, data: dict) -> asyncpg.Record:
        """Inserta una fila `firma`.

        Los campos XAdES (``certificado_pem``, ``minio_key_firmado``,
        ``nivel_conformidad``, ``xades_level``, ``certificado_fingerprint``)
        son ``None`` para firmas ``nativa`` — las columnas admiten NULL
        (migración 008, aditiva). ``tipo_firma`` se fija explícitamente en
        cada INSERT (``'nativa'`` o ``'sello_sistema'``) en vez de depender
        del DEFAULT de columna, que quedó en ``'electronica-nativa'`` desde la
        migración 002 por compatibilidad histórica. ``signed_xml_sha256``
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
        """
        row = await self._conn.fetchrow(
            f"""INSERT INTO firma (objeto_tipo, objeto_id, formato_solicitado,
                firmante_id, payload_sha256, firma_blob, provider, sello_tiempo,
                certificado_pem, minio_key_firmado, nivel_conformidad, xades_level,
                tipo_firma, certificado_fingerprint, signed_xml_sha256,
                tsa_token, tsa_timestamp, tsa_cert_fingerprint, tsa_acreditado)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
                RETURNING {_COLS}""",
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
            data.get("tipo_firma"),
            data.get("certificado_fingerprint"),
            data.get("signed_xml_sha256"),
            data.get("tsa_token"),
            data.get("tsa_timestamp"),
            data.get("tsa_cert_fingerprint"),
            data.get("tsa_acreditado", False),
        )
        assert row is not None
        return row

    async def get(self, firma_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(f"SELECT {_COLS} FROM firma WHERE id = $1", firma_id)
