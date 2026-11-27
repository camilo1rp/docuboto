# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de suscripciones de webhooks (E11). search_path ya fijado por la capa de conexión."""

from __future__ import annotations

# `builtins.list` en las anotaciones de este módulo NO es una manía de estilo:
# la clase define un método `list`, que dentro del cuerpo de la clase sombrea al
# `list` incorporado. Sin cualificar, `-> list[...]` resuelve al MÉTODO y la
# anotación queda sin significado (mypy: "Function ... is not valid as a type").
# Se cualifica en vez de renombrar el método porque `list` es su nombre correcto
# y renombrarlo obligaría a tocar routers y servicios de otros módulos.
import builtins
import uuid

import asyncpg


class WebhookRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert(
        self,
        *,
        webhook_id: uuid.UUID,
        url: str,
        event_types: list[str],
        secret_cifrado: bytes | None = None,
        secret_nonce: bytes | None = None,
        secret_kek_version: int | None = None,
    ) -> asyncpg.Record:
        """El `id` lo acuña la aplicación (no la base) porque el AAD del secreto
        cifrado lo incluye y hay que conocerlo ANTES de cifrar. El secreto llega
        ya cifrado: este repositorio nunca ve texto claro (y `secret` en claro
        queda NULL por el CHECK `chk_webhook_secret_sin_claro`)."""
        row = await self._conn.fetchrow(
            "INSERT INTO webhook_subscription "
            "(id, url, event_types, secret_cifrado, secret_nonce, secret_kek_version) "
            "VALUES ($1, $2, $3, $4, $5, $6) "
            "RETURNING id, url, event_types, active, created_at",
            webhook_id,
            url,
            event_types,
            secret_cifrado,
            secret_nonce,
            secret_kek_version,
        )
        assert row is not None
        return row

    async def list(self) -> builtins.list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                "SELECT id, url, event_types, active, created_at "
                "FROM webhook_subscription ORDER BY created_at DESC"
            )
        )

    async def delete(self, webhook_id: uuid.UUID) -> asyncpg.Record | None:
        """Borra y devuelve lo borrado (url, event_types) para el asiento; None si no existía."""
        return await self._conn.fetchrow(
            "DELETE FROM webhook_subscription WHERE id = $1 RETURNING id, url, event_types",
            webhook_id,
        )

    async def active_for_event(self, event_type: str) -> builtins.list[asyncpg.Record]:
        """Suscripciones activas que reciben este event_type (o todos si vacío)."""
        return list(
            await self._conn.fetch(
                # NUNCA `secret` (el texto claro heredado): solo el cifrado y una
                # bandera de "esta fila aún está en claro" para no entregarla.
                "SELECT id, url, secret_cifrado, secret_nonce, secret_kek_version, "
                "       (secret IS NOT NULL) AS secret_en_claro, event_types "
                "FROM webhook_subscription "
                "WHERE active AND (event_types = '{}' OR $1 = ANY(event_types))",
                event_type,
            )
        )

    # ------------------------------------------------------------------
    # Canal de ops (cifrado de heredados y rotación de la KEK). Son los ÚNICOS
    # métodos que tocan el secreto; ningún router los usa.
    # ------------------------------------------------------------------
    async def count_plaintext(self) -> int:
        return int(
            await self._conn.fetchval(
                "SELECT count(*) FROM webhook_subscription WHERE secret IS NOT NULL"
            )
        )

    async def fetch_plaintext_ids(self) -> builtins.list[uuid.UUID]:
        return [
            r["id"]
            for r in await self._conn.fetch(
                "SELECT id FROM webhook_subscription WHERE secret IS NOT NULL ORDER BY id"
            )
        ]

    async def get_plaintext_for_update(self, webhook_id: uuid.UUID) -> asyncpg.Record | None:
        """El secreto en claro de UNA fila, bajo FOR UPDATE (dentro de una tx);
        None si otra corrida ya la cifró."""
        return await self._conn.fetchrow(
            "SELECT id, secret FROM webhook_subscription "
            "WHERE id = $1 AND secret IS NOT NULL FOR UPDATE",
            webhook_id,
        )

    async def seal_legacy(
        self,
        webhook_id: uuid.UUID,
        *,
        secret_cifrado: bytes,
        secret_nonce: bytes,
        secret_kek_version: int,
    ) -> None:
        """Guarda el cifrado y deja `secret` en NULL en el MISMO UPDATE."""
        await self._conn.execute(
            "UPDATE webhook_subscription "
            "SET secret_cifrado = $2, secret_nonce = $3, secret_kek_version = $4, "
            "    secret = NULL "
            "WHERE id = $1 AND secret IS NOT NULL",
            webhook_id,
            secret_cifrado,
            secret_nonce,
            secret_kek_version,
        )

    async def fetch_ids_to_rewrap(self, active_version: int) -> builtins.list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                "SELECT id, secret_kek_version FROM webhook_subscription "
                "WHERE secret_cifrado IS NOT NULL AND secret_kek_version <> $1 ORDER BY id",
                active_version,
            )
        )

    async def get_for_rewrap(self, webhook_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            "SELECT id, secret_cifrado, secret_nonce, secret_kek_version "
            "FROM webhook_subscription WHERE id = $1 AND secret_cifrado IS NOT NULL FOR UPDATE",
            webhook_id,
        )

    async def rewrap(
        self,
        webhook_id: uuid.UUID,
        *,
        secret_cifrado: bytes,
        secret_nonce: bytes,
        secret_kek_version: int,
    ) -> None:
        await self._conn.execute(
            "UPDATE webhook_subscription "
            "SET secret_cifrado = $2, secret_nonce = $3, secret_kek_version = $4 "
            "WHERE id = $1",
            webhook_id,
            secret_cifrado,
            secret_nonce,
            secret_kek_version,
        )

    async def count_by_kek_version(self) -> builtins.list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                "SELECT secret_kek_version AS key_version, count(*) AS n "
                "FROM webhook_subscription WHERE secret_cifrado IS NOT NULL "
                "GROUP BY secret_kek_version ORDER BY secret_kek_version"
            )
        )
