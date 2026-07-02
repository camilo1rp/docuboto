# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Bookkeeping informativo del sello institucional activo por tenant (Inc.1).

Ver el comentario de la migración 009: esta tabla NO es la fuente de verdad
histórica de qué certificado firmó una firma concreta (eso vive en
``firma.certificado_fingerprint`` por fila, migración 008) — solo refleja el
sello VIGENTE al momento de la última firma xades_local, para un futuro panel
de administración. La escritura es best-effort (no debe poder tumbar
``/sign-xml`` si falla — ver el llamador en ``app/routers/signature.py``).
"""

from __future__ import annotations

import asyncpg


class SignerConfigRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def upsert(
        self,
        *,
        tenant_id: str,
        provider: str,
        cert_fingerprint: str | None,
        acreditado: bool,
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO signer_provider_config (tenant_id, provider, cert_fingerprint, acreditado, actualizado_ts)
            VALUES ($1, $2, $3, $4, now())
            ON CONFLICT (tenant_id) DO UPDATE
                SET provider = EXCLUDED.provider,
                    cert_fingerprint = EXCLUDED.cert_fingerprint,
                    acreditado = EXCLUDED.acreditado,
                    actualizado_ts = now()
            """,
            tenant_id,
            provider,
            cert_fingerprint,
            acreditado,
        )
