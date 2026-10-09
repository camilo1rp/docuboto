# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de transferencias documentales (E12). search_path al tenant."""

from __future__ import annotations

# `builtins.list` en las anotaciones de este módulo NO es una manía de estilo:
# la clase define un método `list`, que dentro del cuerpo de la clase sombrea al
# `list` incorporado. Sin cualificar, `-> list[...]` resuelve al MÉTODO y la
# anotación queda sin significado (mypy: "Function ... is not valid as a type").
# Se cualifica en vez de renombrar el método porque `list` es su nombre correcto
# y renombrarlo obligaría a tocar routers y servicios de otros módulos.
import builtins
import json
import uuid
from datetime import datetime

import asyncpg

from app.core.database import tenant_schema

_COLS = (
    "id, tipo, estado, expediente_id, ubicacion_origen_id, ubicacion_destino_id, "
    "creada_por, observacion, created_at, enviada_at, enviada_por, recibida_at, decidida_por, "
    # H-K/H-J (migración 021): motivo propio del rechazo (nunca pisa
    # `observacion`) + sello temporal propio del acto.
    "motivo_rechazo, rechazada_at, "
    # Migración 052 (Anexo FUID, Ac. AGN 001/2024): cabecera del acta capturada
    # en la PREPARACIÓN. Sí van en `_COLS` -- son cinco escalares cortos que
    # `recibir()` necesita para congelar el acta y la ficha necesita para
    # mostrarla, no un XML como el de `_ACTA_COLS`.
    "objeto, oficina_productora_codigo, oficina_productora_nombre, "
    "unidad_administrativa_codigo, unidad_administrativa_nombre"
)

# Migración 022 (E12 H-I): acta de entrega congelada, 1:1 con la transferencia
# RECIBIDA. Columnas propias -- deliberadamente NO se añaden a `_COLS` (el
# XML no debe arrastrarse por el hot path de `insert`/`get`/`list`/
# `update_estado`, ver docstring de la migración).
_ACTA_COLS = (
    "a.transferencia_id, a.acta_fuid_xml, a.acta_fuid_sha256, a.elaborado_por, "
    "a.entregado_por, a.recibido_por, a.elaborado_at, a.entregado_at, a.recibido_at, "
    "a.indice_version, a.indice_firma_id, a.indice_sha256, a.congelada_at"
)

#: **Los tres responsables del FUID se resuelven a un nombre aquí, no en la
#: pantalla.** El acta CONGELA el identificador interno de quien elaboró,
#: entregó y recibió, y así debe seguir: es un documento probatorio y su
#: contenido no se toca (Ley 594/2000). Pero la ficha del acta enseñaba los tres
#: como `50b64803-568d-49f7-9299-cb140279d947`, que no responde quién —séptima
#: aparición del mismo defecto en este repositorio—. Se resuelve al LEER, con el
#: mismo JOIN por TEXTO y por la IZQUIERDA que ya usa `audit_read.py`: el
#: responsable puede no ser un usuario del catálogo (actas migradas, actos del
#: sistema) y un `::uuid` haría fallar la consulta entera por una fila que ni se
#: estaba preguntando. El identificador congelado sigue viajando en la
#: respuesta: es la clave con la que se cruza el acta con el registro de firmas.
_ACTA_NOMBRES = (
    "ue.username AS elaborado_por_nombre, "
    "uen.username AS entregado_por_nombre, "
    "ur.username AS recibido_por_nombre"
)
_ACTA_JOIN_NOMBRES = (
    "LEFT JOIN auth_users ue ON ue.id::text = a.elaborado_por "
    "LEFT JOIN auth_users uen ON uen.id::text = a.entregado_por "
    "LEFT JOIN auth_users ur ON ur.id::text = a.recibido_por"
)

# Migración 026 (H-H, E06/F4): tabla HERMANA mutable de `transferencia_acta`
# -- rastrea el sellado XAdES del acta, 1:1 por transferencia. Deliberadamente
# NO fusionada en `_ACTA_COLS`: `transferencia_acta` sigue siendo append-only,
# esta tabla es la ÚNICA que admite UPDATE.
_ACTA_FIRMA_COLS = (
    "transferencia_id, estado, firma_id, minio_key_firmado, xades_level, acreditado, "
    "last_error, reconcile_intentos, reconcile_proximo_at, reconcile_agotado, "
    "created_at, firmado_at"
)

# E10 Inc.2 wiring (migración 030): estado del cableado WORM del acta —
# ORTOGONAL a `estado`/`reconcile_*` de arriba (esas son del SELLADO XAdES).
# Columnas aparte en vez de sumarlas a `_ACTA_FIRMA_COLS`: solo
# `get_acta_firma_worm`/`list_worm_pending_actas` (consumidores del cableado
# WORM) las necesitan — mismo criterio que `IndexRepository._WORM_COLS`.
_ACTA_FIRMA_WORM_COLS = (
    "worm_protegido_at, worm_object_id, worm_reconcile_intentos, "
    "worm_reconcile_proximo_at, worm_reconcile_ultimo_error, worm_reconcile_agotado"
)

# Migración 027 (E17/F4 Incremento 1 -- firma PERSONAL PKI por-usuario del
# acta): tabla ADITIVA sobre `transferencia_acta_firma` (sello institucional,
# migración 026) -- 1:N por transferencia (una fila por `rol` firmante,
# 'recibe', 'entrega' y 'elabora' -- los tres con escritor, Inc.1 a Inc.3),
# estrictamente append-only (sin columnas reconcile_*:
# la firma personal exige un TOTP interactivo, nunca se reconcilia en
# background).
_ACTA_FIRMA_PERSONAL_COLS = (
    "id, transferencia_id, rol, firmante_id, firma_id, minio_key_firmado, "
    "xades_level, ca_fingerprint, acreditado, creada_en"
)

_ACTA_FIRMA_EXCEPCION_COLS = (
    "id, transferencia_id, rol, parte_id, motivo, fundamento, referencia_documental, "
    "declarada_por, declarada_en, referencia_radicado_id, referencia_anexo_id, "
    "constancia_sha256, firma_id, minio_key_firmado, xades_level, firma_personal_id, "
    "minio_key_firmado_personal, xades_level_personal, firma_personal_requerida, "
    # Migración 062 (FIRMA-2): cadena e instantánea de identidad/soporte. Migración 063: el
    # fundamento se huella con un compromiso con sal por registro (las columnas `fundamento_hmac*`
    # de la 062 quedan como dato legado: no se proyectan).
    "ordinal, sustituye_a, constancia_version, referencia_radicado_numero, "
    "referencia_anexo_sha256, declarante_nombre, declarante_cargo, declarante_fuente, "
    "parte_nombre, parte_cargo, parte_fuente, fundamento_sal, fundamento_compromiso, "
    "fundamento_compromiso_algoritmo, "
    # Migración 064: sobre de revelación del fundamento (objeto sellado y bajo Object-Lock).
    "sobre_firma_id, sobre_minio_key, sobre_sha256, sobre_xades_level"
)

# Constancia + revocación (hecho posterior, fila aparte) + estado WORM de los artefactos de ambas
# (migraciones 061/062). `worm_*_protegido` es NULL cuando el artefacto no existe (constancia
# legada o sin firma personal). `constancia_xml`/`revocacion_xml` NO se proyectan: son pesados y
# solo los necesitan el WORM y la verificación del fundamento.
_ACTA_FIRMA_EXCEPCION_SELECT = (
    "SELECT "
    + ", ".join(f"e.{c.strip()}" for c in _ACTA_FIRMA_EXCEPCION_COLS.split(","))
    + """,
       r.id AS revocacion_id, r.revocada_por, r.revocada_en, r.fundamento AS revocacion_fundamento,
       r.revocacion_version, r.revocacion_sha256,
       r.firma_id AS revocacion_firma_id,
       r.minio_key_firmado AS revocacion_minio_key_firmado,
       r.xades_level AS revocacion_xades_level,
       r.firma_personal_id AS revocacion_firma_personal_id,
       r.minio_key_firmado_personal AS revocacion_minio_key_firmado_personal,
       r.xades_level_personal AS revocacion_xades_level_personal,
       r.revocante_nombre, r.revocante_cargo, r.revocante_fuente,
       r.fundamento_sal AS revocacion_fundamento_sal,
       r.fundamento_compromiso AS revocacion_fundamento_compromiso,
       r.fundamento_compromiso_algoritmo AS revocacion_fundamento_compromiso_algoritmo,
       r.causa AS revocacion_causa,
       r.sobre_firma_id AS revocacion_sobre_firma_id,
       r.sobre_minio_key AS revocacion_sobre_minio_key,
       r.sobre_sha256 AS revocacion_sobre_sha256,
       r.sobre_xades_level AS revocacion_sobre_xades_level,
       ws.worm_retain_until AS worm_retain_until,
       rws.worm_retain_until AS revocacion_worm_retain_until,
       CASE WHEN ws.excepcion_id IS NULL THEN NULL
            ELSE ws.worm_protegido_at IS NOT NULL END AS worm_protegido,
       CASE WHEN wp.excepcion_id IS NULL THEN NULL
            ELSE wp.worm_protegido_at IS NOT NULL END AS worm_personal_protegido,
       CASE WHEN rws.revocacion_id IS NULL THEN NULL
            ELSE rws.worm_protegido_at IS NOT NULL END AS revocacion_worm_protegido,
       CASE WHEN rwp.revocacion_id IS NULL THEN NULL
            ELSE rwp.worm_protegido_at IS NOT NULL END AS revocacion_worm_personal_protegido,
       CASE WHEN sw.excepcion_id IS NULL THEN NULL
            ELSE sw.worm_protegido_at IS NOT NULL END AS worm_sobre_protegido,
       CASE WHEN rsw.revocacion_id IS NULL THEN NULL
            ELSE rsw.worm_protegido_at IS NOT NULL END AS revocacion_worm_sobre_protegido
  FROM transferencia_acta_firma_excepcion e
  LEFT JOIN transferencia_acta_firma_excepcion_revocacion r ON r.excepcion_id = e.id
  LEFT JOIN transferencia_acta_firma_excepcion_worm ws
         ON ws.excepcion_id = e.id AND ws.artefacto = 'sello'
  LEFT JOIN transferencia_acta_firma_excepcion_worm wp
         ON wp.excepcion_id = e.id AND wp.artefacto = 'personal'
  LEFT JOIN transferencia_acta_firma_excepcion_revocacion_worm rws
         ON rws.revocacion_id = r.id AND rws.artefacto = 'sello'
  LEFT JOIN transferencia_acta_firma_excepcion_revocacion_worm rwp
         ON rwp.revocacion_id = r.id AND rwp.artefacto = 'personal'
  LEFT JOIN transferencia_acta_firma_excepcion_worm sw
         ON sw.excepcion_id = e.id AND sw.artefacto = 'sobre'
  LEFT JOIN transferencia_acta_firma_excepcion_revocacion_worm rsw
         ON rsw.revocacion_id = r.id AND rsw.artefacto = 'sobre'
"""
)

#: Tablas por ACTO del cableado WORM de la constancia: la constancia ('excepcion') y su
#: revocación ('revocacion') comparten el ciclo (protección inicial + renovación CT) y difieren
#: en tabla, clave y de dónde sale el XML firmado. Constantes de código, nunca datos del cliente.
_WORM_ACTOS: dict[str, dict[str, str]] = {
    "excepcion": {
        "worm": "transferencia_acta_firma_excepcion_worm",
        "fk": "excepcion_id",
    },
    "revocacion": {
        "worm": "transferencia_acta_firma_excepcion_revocacion_worm",
        "fk": "revocacion_id",
    },
}

# Columnas que `update_estado` NUNCA acepta en `assignments`: `estado` tiene su
# propio parámetro (para que la guarda `expected_estado` quede visible en la
# firma, no enterrada en un dict libre) y `id` identifica la fila, no un valor
# a escribir. Un solo escritor de la columna de estado — mismo principio que
# retiró `status` de `ArchiveRepository.update_expediente` (ver su docstring).
_FORBIDDEN_ASSIGNMENT_COLS = frozenset({"estado", "id"})


class TransferenciaRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug

    async def set_search_path(self) -> None:
        """Fija el `search_path` del tenant para quien lee tablas del tenant sin pasar por un
        método del repositorio (p. ej. la política de la entidad en `sgd_param_admin`)."""
        await self._sp()

    async def _sp(self) -> None:
        schema = tenant_schema(self._tenant_slug)  # valida + normaliza (fail-closed)
        await self._conn.execute(f'SET search_path TO "{schema}", public')

    async def insert(self, data: dict) -> asyncpg.Record:
        await self._sp()
        cols = list(data.keys())
        ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        row = await self._conn.fetchrow(
            f"INSERT INTO transferencias ({', '.join(cols)}) VALUES ({ph}) " f"RETURNING {_COLS}",
            *data.values(),
        )
        assert row is not None
        return row

    async def get(self, transferencia_id: uuid.UUID) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM transferencias WHERE id = $1", transferencia_id
        )

    async def list(self, *, estado: str | None, tipo: str | None) -> builtins.list[asyncpg.Record]:
        await self._sp()
        conds: builtins.list[str] = []
        params: builtins.list[object] = []
        if estado is not None:
            params.append(estado)
            conds.append(f"estado = ${len(params)}")
        if tipo is not None:
            params.append(tipo)
            conds.append(f"tipo = ${len(params)}")
        where = f"WHERE {' AND '.join(conds)}" if conds else ""
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM transferencias {where} " "ORDER BY created_at DESC",
                *params,
            )
        )

    async def update_estado(
        self,
        transferencia_id: uuid.UUID,
        *,
        expected_estado: str,
        estado: str,
        assignments: dict | None = None,
    ) -> asyncpg.Record | None:
        """Transiciona `estado` con guarda TOCTOU (H-A) — gemela de
        `ArchiveRepository.transition_expediente` (ver su docstring, H3/cf83324).

        Antes este método emitía `UPDATE ... WHERE id = $N` SIN comprobar el
        estado de origen: `TransferenciaService._require` validaba la
        transición ANTES de llamar aquí, pero esa validación quedaba FUERA de
        la sentencia — dos llamadas concurrentes (p. ej. `rechazar()` y
        `recibir()` sobre la MISMA transferencia 'enviada') podían leer ambas
        el mismo estado de origen, pasar `_require`, y comprometer AMBOS
        `UPDATE`. El desenlace letal: `rechazada` + expediente ya congelado
        `transferred` por el `recibir()` concurrente -- un estado jurídicamente
        imposible e irreversible en un `audit_log` INDELEBLE (Ley 594/2000 art.
        16; Acuerdo AGN 001/2024 Tít. 4.4).

        `expected_estado` es OBLIGATORIO y solo por *keyword*, sin default: con
        un default "permisivo" un call-site futuro podría omitirlo y el TOCTOU
        volvería en silencio; sin default, olvidarlo es un `TypeError` en la
        primera llamada, no un bug que sobrevive a los tests. `estado` sale del
        dict libre para que la guarda sea visible en la firma, no un valor más
        entre `assignments` (que además la RECHAZA explícitamente, junto con
        `id`, ver `_FORBIDDEN_ASSIGNMENT_COLS`).

        `None` (perdedor de la carrera) es responsabilidad del LLAMANTE:
        `_require` sigue siendo la validación temprana/UX (404/409 con mensaje
        de negocio); este `WHERE` es quien serializa de verdad bajo
        concurrencia real y quien decide el caso alcanzable.
        """
        assignments = assignments or {}
        forbidden = _FORBIDDEN_ASSIGNMENT_COLS & assignments.keys()
        if forbidden:
            raise ValueError(
                f"update_estado: assignments no puede incluir {sorted(forbidden)} "
                "-- usa los parámetros `estado`/`transferencia_id` dedicados."
            )
        await self._sp()
        cols = list(assignments.keys())
        set_parts = ["estado = $1"]
        params: builtins.list[object] = [estado]
        idx = 2
        for c in cols:
            set_parts.append(f"{c} = ${idx}")
            params.append(assignments[c])
            idx += 1
        id_idx = idx
        params.append(transferencia_id)
        idx += 1
        expected_idx = idx
        params.append(expected_estado)
        return await self._conn.fetchrow(
            f"UPDATE transferencias SET {', '.join(set_parts)} "
            f"WHERE id = ${id_idx} AND estado = ${expected_idx} "
            f"RETURNING {_COLS}",
            *params,
        )

    async def insert_acta(self, data: dict) -> None:
        """Congela el acta de entrega (E12 H-I): escritura ÚNICA dentro de la
        MISMA `conn.transaction()` de `TransferenciaService.recibir` -- no hay
        ruta de `UPDATE` sobre esta tabla en el repositorio (la `PRIMARY KEY`
        de la migración 022 impide además un segundo `INSERT`), así que el
        acta es append-only de facto. Sin `RETURNING`: el llamante ya tiene
        todos los valores que insertó, no necesita releerlos."""
        await self._sp()
        cols = list(data.keys())
        ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        await self._conn.execute(
            f"INSERT INTO transferencia_acta ({', '.join(cols)}) VALUES ({ph})",
            *data.values(),
        )

    async def get_acta(self, transferencia_id: uuid.UUID) -> asyncpg.Record | None:
        """Devuelve el acta YA CONGELADA -- `None` si la transferencia aún no
        se recibió (o fue rechazada, que nunca produce acta, ver
        `TransferenciaService.recibir`)."""
        await self._sp()
        # `auth_users` la crea AUTH-SERVICE, no este servicio: en el esquema de
        # una entidad real está —todas las migraciones aterrizan en el mismo
        # schema— pero un despliegue parcial, o un esquema de pruebas
        # provisionado solo con las migraciones de archivo, no la tiene. El
        # nombre del responsable es una cortesía; el acta es el documento
        # probatorio. Que la falta de una tabla de OTRO servicio tumbe la
        # lectura del acta sería cambiar un dato ausente por un error, así que
        # se comprueba antes y se degrada a leerla sin nombres.
        # Se comprueba la COLUMNA y no la tabla: `auth_users` puede existir con
        # otra forma (un esquema de pruebas la crea con lo mínimo que necesita
        # el clearance), y entonces la tabla está pero `username` no — mirar
        # solo la tabla es comprobar la mitad y fallar igual.
        if await self._conn.fetchval(
            """SELECT EXISTS (
                   SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'auth_users'
                      AND column_name = 'username')"""
        ):
            return await self._conn.fetchrow(
                f"""SELECT {_ACTA_COLS}, {_ACTA_NOMBRES}
                      FROM transferencia_acta a
                      {_ACTA_JOIN_NOMBRES}
                     WHERE a.transferencia_id = $1""",
                transferencia_id,
            )
        return await self._conn.fetchrow(
            f"SELECT {_ACTA_COLS} FROM transferencia_acta a WHERE a.transferencia_id = $1",
            transferencia_id,
        )

    async def dependencia_de_usuario(self, user_id: uuid.UUID) -> asyncpg.Record | None:
        """Oficina productora y unidad administrativa del usuario que actúa.

        La oficina es su dependencia ACTIVA (`auth_users.active_depe_id`,
        migración 006 de auth-service — el contexto que el propio usuario
        eligió) y, si no tiene contexto activo, su URD PRINCIPAL
        (`urd_usuaroldep.es_principal`). La unidad administrativa es la
        dependencia PADRE (`dependencias.parent_id`); una oficina sin padre es
        raíz y ES su propia unidad administrativa — repetirla dice la verdad,
        dejarla vacía no.

        `auth_users`, `urd_usuaroldep` y `dependencias` las crean auth-service
        y tenant-service, no este servicio: viven en el MISMO schema del tenant
        pero un despliegue parcial o un esquema de pruebas provisionado solo
        con las migraciones de archivo no las tiene. Se comprueban las COLUMNAS
        (no las tablas: `auth_users` puede existir con otra forma, ver
        `get_acta`) y se degrada a `None` — la transferencia se prepara igual y
        la cabecera del acta queda declaradamente vacía, que es lo que pasaba
        siempre antes de esta migración.
        """
        await self._sp()
        if not await self._conn.fetchval(
            """SELECT
                 (SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema = current_schema()
                     AND (table_name, column_name) IN
                         (('auth_users', 'active_depe_id'),
                          ('urd_usuaroldep', 'es_principal'),
                          ('dependencias', 'parent_id'))) = 3"""
        ):
            return None
        return await self._conn.fetchrow(
            """
            WITH depe AS (
                SELECT COALESCE(
                         u.active_depe_id,
                         (SELECT r.depe_id FROM urd_usuaroldep r
                           WHERE r.user_id = u.id AND r.es_principal AND r.is_active
                           LIMIT 1)
                       ) AS codigo
                  FROM auth_users u
                 WHERE u.id = $1
            )
            SELECT d.codigo   AS oficina_productora_codigo,
                   d.nombre   AS oficina_productora_nombre,
                   COALESCE(p.codigo, d.codigo) AS unidad_administrativa_codigo,
                   COALESCE(p.nombre, d.nombre) AS unidad_administrativa_nombre
              FROM depe
              JOIN dependencias d ON d.codigo = depe.codigo
              LEFT JOIN dependencias p ON p.id = d.parent_id
            """,
            user_id,
        )

    # -----------------------------------------------------------------
    # transferencia_acta_firma (H-H, E06/F4) -- estado MUTABLE del sellado
    # -----------------------------------------------------------------

    async def insert_acta_firma_pendiente(
        self, transferencia_id: uuid.UUID, *, fixity_premis_detalle: dict | None = None
    ) -> None:
        """Crea la fila de bookkeeping `'pendiente_firma'` -- escritura LOCAL
        de BD (sin HTTP), llamada DENTRO de la MISMA `conn.transaction()` que
        `insert_acta` en `TransferenciaService.recibir` (preserva la
        atomicidad H-A/H-I: nunca HTTP dentro de esa tx). Garantiza que TODA
        acta congelada tenga su fila de firma-tracking aunque el proceso
        muera entre el commit y el intento de sellado post-commit -- el job
        de reconciliación la recoge igual."""
        await self._sp()
        if fixity_premis_detalle is None:
            await self._conn.execute(
                "INSERT INTO transferencia_acta_firma (transferencia_id) VALUES ($1)",
                transferencia_id,
            )
            return
        # AREA-07-05 (migración 056): el detalle de la fixity de la recepción queda aquí,
        # en la MISMA tx, para poder registrar/reintentar su evento PREMIS después.
        await self._conn.execute(
            "INSERT INTO transferencia_acta_firma (transferencia_id, fixity_premis_detalle) "
            "VALUES ($1, $2::jsonb)",
            transferencia_id,
            json.dumps(fixity_premis_detalle),
        )

    async def get_fixity_premis(self, transferencia_id: uuid.UUID) -> asyncpg.Record | None:
        """Detalle de la fixity de la recepción y su estado de registro PREMIS, junto con el
        expediente y el receptor (AREA-07-05)."""
        await self._sp()
        return await self._conn.fetchrow(
            "SELECT taf.transferencia_id, taf.fixity_premis_detalle, taf.fixity_premis_at, "
            "taf.fixity_premis_intentos, t.expediente_id, t.decidida_por "
            "FROM transferencia_acta_firma taf "
            "JOIN transferencias t ON t.id = taf.transferencia_id "
            "WHERE taf.transferencia_id = $1",
            transferencia_id,
        )

    async def mark_fixity_premis_registrado(self, transferencia_id: uuid.UUID) -> int:
        """Marca el evento PREMIS del fixity-check como confirmado por storage-service. El
        `WHERE ... IS NULL` lo fija UNA sola vez (idempotente ante una carrera del disparo
        post-commit con la barrida de reconciliación)."""
        await self._sp()
        tag = await self._conn.execute(
            "UPDATE transferencia_acta_firma SET fixity_premis_at = now(), "
            "fixity_premis_proximo_at = NULL, fixity_premis_ultimo_error = NULL "
            "WHERE transferencia_id = $1 AND fixity_premis_at IS NULL",
            transferencia_id,
        )
        try:
            return int(tag.split()[-1])
        except (AttributeError, ValueError, IndexError):
            return 0

    async def bump_fixity_premis_intento(
        self,
        transferencia_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotado: bool,
    ) -> None:
        """Registra un intento FALLIDO de registrar el evento PREMIS (backoff)."""
        await self._sp()
        await self._conn.execute(
            "UPDATE transferencia_acta_firma SET "
            "fixity_premis_intentos = fixity_premis_intentos + 1, "
            "fixity_premis_proximo_at = $2, fixity_premis_ultimo_error = $3, "
            "fixity_premis_agotado = $4 WHERE transferencia_id = $1",
            transferencia_id,
            proximo_at,
            ultimo_error,
            agotado,
        )

    async def list_fixity_premis_pending(self, limit: int) -> builtins.list[asyncpg.Record]:
        """Recepciones con el evento PREMIS del fixity-check sin confirmar, no agotadas y con
        el backoff cumplido. Solo las que guardaron `fixity_premis_detalle` (las anteriores a
        la migración 056 no tienen con qué reconstruirlo)."""
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT taf.transferencia_id, taf.fixity_premis_intentos
                  FROM transferencia_acta_firma taf
                 WHERE taf.fixity_premis_detalle IS NOT NULL
                   AND taf.fixity_premis_at IS NULL
                   AND taf.fixity_premis_agotado = FALSE
                   AND (taf.fixity_premis_proximo_at IS NULL
                        OR taf.fixity_premis_proximo_at <= now())
                 ORDER BY taf.fixity_premis_proximo_at ASC NULLS FIRST
                 LIMIT $1
                """,
                limit,
            )
        )

    async def get_acta_firma(self, transferencia_id: uuid.UUID) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_ACTA_FIRMA_COLS} FROM transferencia_acta_firma WHERE transferencia_id = $1",
            transferencia_id,
        )

    async def mark_acta_firmada(
        self,
        transferencia_id: uuid.UUID,
        *,
        firma_id: uuid.UUID,
        minio_key: str | None,
        xades_level: str | None,
        acreditado: bool,
    ) -> int:
        """Marca el acta como sellada e inmutable (gemela de
        `IndexRepository.mark_signed`). `WHERE estado <> 'firmado'` es
        defensa en profundidad Y el mecanismo real de idempotencia bajo
        carrera manual-vs-job (ver `SignOutcome`/`_sellar_acta_post_commit`):
        devuelve el número de filas afectadas (0 o 1) para que el llamante
        NUNCA escriba un asiento `archive.acta_firmada` fantasma sobre un
        efecto que esta llamada no produjo. Resetea el backoff -- un acta
        recién firmada no tiene nada más que reconciliar."""
        await self._sp()
        tag = await self._conn.execute(
            "UPDATE transferencia_acta_firma SET estado='firmado', firma_id=$2, "
            "minio_key_firmado=$3, xades_level=$4, acreditado=$5, "
            "reconcile_intentos=0, reconcile_agotado=false, reconcile_proximo_at=NULL, "
            "last_error=NULL, firmado_at=now() "
            "WHERE transferencia_id=$1 AND estado <> 'firmado'",
            transferencia_id,
            firma_id,
            minio_key,
            xades_level,
            acreditado,
        )
        try:
            return int(tag.split()[-1])
        except (AttributeError, ValueError, IndexError):
            # Mismo fallback defensivo que `IndexRepository.mark_signed`
            # (docstring ahí): solo protege mocks de test sin status tag
            # realista, asyncpg SIEMPRE devuelve "UPDATE n".
            return 0

    async def mark_acta_firma_pendiente(
        self, transferencia_id: uuid.UUID, *, last_error: str | None
    ) -> None:
        """Degrada/re-arma a `'pendiente_firma'` (gemela de
        `IndexRepository.mark_pendiente`): re-arma SIEMPRE el backoff
        (`reconcile_agotado=false, reconcile_intentos=0`) y fija
        `reconcile_proximo_at=now()` -- toda degradación queda de inmediato
        elegible para el job de reconciliación, sin esperar un primer
        `bump_acta_firma_intento`. Usada tanto por el intento síncrono
        post-commit de `recibir()` (degradación) como por el reintento manual
        `POST /transferencias/{id}/acta/firmar` (re-armado explícito antes de
        reintentar) -- mismo criterio C1 que su gemelo del índice: un acta
        `reconcile_agotado=TRUE` cuyo reintento manual vuelve a fallar no debe
        quedar invisible ni para el job ni para el operador.

        `WHERE estado <> 'firmado'`: un acta ya sellada (inmutable) nunca se
        degrada de vuelta a pendiente."""
        await self._sp()
        await self._conn.execute(
            "UPDATE transferencia_acta_firma SET estado='pendiente_firma', "
            "last_error=$2, reconcile_proximo_at=now(), "
            "reconcile_agotado=false, reconcile_intentos=0 "
            "WHERE transferencia_id=$1 AND estado <> 'firmado'",
            transferencia_id,
            last_error,
        )

    async def bump_acta_firma_intento(
        self,
        transferencia_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        last_error: str,
        agotado: bool,
    ) -> None:
        """Registra un intento de sellado FALLIDO del job de reconciliación
        (gemela de `IndexRepository.bump_intento`) -- incrementa
        `reconcile_intentos` atómicamente en el propio UPDATE, sin lectura
        previa. `proximo_at=None` es válido cuando `agotado=True` (ya no hay
        "próximo intento" que programar)."""
        await self._sp()
        await self._conn.execute(
            "UPDATE transferencia_acta_firma SET "
            "reconcile_intentos = reconcile_intentos + 1, "
            "reconcile_proximo_at = $2, last_error = $3, reconcile_agotado = $4 "
            "WHERE transferencia_id = $1",
            transferencia_id,
            proximo_at,
            last_error,
            agotado,
        )

    # -----------------------------------------------------------------
    # transferencia_acta_firma_personal (E17/F4 Inc.1) -- firma PERSONAL
    # PKI por-usuario, ADITIVA sobre el sello institucional, append-only
    # -----------------------------------------------------------------

    async def get_acta_firma_personal(
        self, transferencia_id: uuid.UUID, *, rol: str
    ) -> asyncpg.Record | None:
        """Fila de firma personal para UN rol -- `None` si ese rol aún no
        firmó (idempotencia de `firmar_personal`: una fila existente
        significa "ya firmado", nunca se re-firma ni se re-quema el TOTP)."""
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_ACTA_FIRMA_PERSONAL_COLS} FROM transferencia_acta_firma_personal "
            "WHERE transferencia_id = $1 AND rol = $2",
            transferencia_id,
            rol,
        )

    async def list_acta_firma_personal(self, transferencia_id: uuid.UUID) -> builtins.list[asyncpg.Record]:
        """Todas las firmas personales de una transferencia: a lo sumo una por
        rol —'recibe' (archivista receptor), 'entrega' (co-firma del remitente) y
        'elabora' (elaborador del FUID)—, los tres con escritor en
        `TransferenciaService` (Inc.1 a Inc.3)."""
        await self._sp()
        return list(
            await self._conn.fetch(
                f"SELECT {_ACTA_FIRMA_PERSONAL_COLS} FROM transferencia_acta_firma_personal "
                "WHERE transferencia_id = $1 ORDER BY rol",
                transferencia_id,
            )
        )

    # -----------------------------------------------------------------
    # AREA-07-10 (migración 059): excepción de firma («firma inobtenible»)
    # -----------------------------------------------------------------

    async def list_acta_firma_excepciones(
        self, transferencia_id: uuid.UUID
    ) -> builtins.list[asyncpg.Record]:
        """Cadena COMPLETA de constancias de excepción de firma de una transferencia (por rol y
        ordinal), las revocadas incluidas: la revocación es un hecho que se muestra, no se
        esconde. Quien decide qué cuenta como «vigente» filtra por `revocada_en IS NULL`."""
        await self._sp()
        return list(
            await self._conn.fetch(
                _ACTA_FIRMA_EXCEPCION_SELECT
                + " WHERE e.transferencia_id = $1 ORDER BY e.rol, e.ordinal",
                transferencia_id,
            )
        )

    async def get_excepcion_vigente(
        self, transferencia_id: uuid.UUID, *, rol: str
    ) -> asyncpg.Record | None:
        """La constancia VIGENTE del rol: la que NO tiene revocación. Es la única que impide
        firmar y la única que cubre a la parte en el cálculo de estado; una revocada es historia.
        A lo sumo una por rol (la cadena es lineal: se re-declara solo tras revocar)."""
        await self._sp()
        return await self._conn.fetchrow(
            _ACTA_FIRMA_EXCEPCION_SELECT
            + " WHERE e.transferencia_id = $1 AND e.rol = $2 AND r.id IS NULL"
            " ORDER BY e.ordinal DESC LIMIT 1",
            transferencia_id,
            rol,
        )

    async def get_ultima_excepcion(
        self, transferencia_id: uuid.UUID, *, rol: str
    ) -> asyncpg.Record | None:
        """El ÚLTIMO eslabón de la cadena del rol (vigente o revocado): sobre él se decide si se
        declara (ordinal 1), si se re-declara (revocado y protegido) o si hay conflicto."""
        await self._sp()
        return await self._conn.fetchrow(
            _ACTA_FIRMA_EXCEPCION_SELECT
            + " WHERE e.transferencia_id = $1 AND e.rol = $2 ORDER BY e.ordinal DESC LIMIT 1",
            transferencia_id,
            rol,
        )

    async def get_excepcion_por_id(
        self, transferencia_id: uuid.UUID, excepcion_id: uuid.UUID
    ) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            _ACTA_FIRMA_EXCEPCION_SELECT + " WHERE e.transferencia_id = $1 AND e.id = $2",
            transferencia_id,
            excepcion_id,
        )

    async def get_xml_constancia_y_revocacion(
        self, excepcion_id: uuid.UUID
    ) -> asyncpg.Record | None:
        """Los XML canónicos guardados (constancia y, si existe, su revocación) para la
        verificación del fundamento: se recalcula el HMAC y se compara con lo que dice el XML."""
        await self._sp()
        return await self._conn.fetchrow(
            "SELECT e.constancia_xml, r.revocacion_xml "
            "FROM transferencia_acta_firma_excepcion e "
            "LEFT JOIN transferencia_acta_firma_excepcion_revocacion r ON r.excepcion_id = e.id "
            "WHERE e.id = $1",
            excepcion_id,
        )

    async def list_xml_cadena(self, transferencia_id: uuid.UUID) -> dict[uuid.UUID, asyncpg.Record]:
        """XML canónicos guardados de TODA la cadena de la transferencia (constancia y revocación),
        por id de constancia: la verificación de los enlaces los cotejan contra las filas y contra
        los artefactos firmados. Pesado: solo lo pide la verificación on-demand."""
        await self._sp()
        rows = await self._conn.fetch(
            "SELECT e.id, e.constancia_xml, r.revocacion_xml "
            "FROM transferencia_acta_firma_excepcion e "
            "LEFT JOIN transferencia_acta_firma_excepcion_revocacion r ON r.excepcion_id = e.id "
            "WHERE e.transferencia_id = $1",
            transferencia_id,
        )
        return {r["id"]: r for r in rows}

    async def get_ancla_cadena(
        self, transferencia_id: uuid.UUID, rol: str
    ) -> datetime | None:
        """Instante del ÚLTIMO acto (declaración o revocación) de la cadena del rol: ancla común
        de la retención WORM de todos sus eslabones (A3)."""
        await self._sp()
        return await self._conn.fetchval(
            "SELECT GREATEST(MAX(e.declarada_en), MAX(r.revocada_en)) "
            "FROM transferencia_acta_firma_excepcion e "
            "LEFT JOIN transferencia_acta_firma_excepcion_revocacion r ON r.excepcion_id = e.id "
            "WHERE e.transferencia_id = $1 AND e.rol = $2",
            transferencia_id,
            rol,
        )

    async def list_cadena_worm_protegidos(
        self, transferencia_id: uuid.UUID, rol: str
    ) -> builtins.list[asyncpg.Record]:
        """Todo artefacto YA protegido de la cadena del rol (constancias y revocaciones, sello y
        personal) con su clave en el almacén y el espejo de su retención, para alinearlos (A3)."""
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT 'excepcion' AS acto, e.id AS acto_id, w.artefacto, e.rol,
                       CASE w.artefacto WHEN 'personal' THEN e.minio_key_firmado_personal
                            WHEN 'sobre' THEN e.sobre_minio_key
                            ELSE e.minio_key_firmado END AS minio_key_firmado,
                       w.worm_retain_until, w.worm_renovacion_intentos
                  FROM transferencia_acta_firma_excepcion e
                  JOIN transferencia_acta_firma_excepcion_worm w ON w.excepcion_id = e.id
                 WHERE e.transferencia_id = $1 AND e.rol = $2 AND w.worm_protegido_at IS NOT NULL
                UNION ALL
                SELECT 'revocacion' AS acto, rv.id AS acto_id, w.artefacto, e.rol,
                       CASE w.artefacto WHEN 'personal' THEN rv.minio_key_firmado_personal
                            WHEN 'sobre' THEN rv.sobre_minio_key
                            ELSE rv.minio_key_firmado END AS minio_key_firmado,
                       w.worm_retain_until, w.worm_renovacion_intentos
                  FROM transferencia_acta_firma_excepcion e
                  JOIN transferencia_acta_firma_excepcion_revocacion rv ON rv.excepcion_id = e.id
                  JOIN transferencia_acta_firma_excepcion_revocacion_worm w ON w.revocacion_id = rv.id
                 WHERE e.transferencia_id = $1 AND e.rol = $2 AND w.worm_protegido_at IS NOT NULL
                """,
                transferencia_id,
                rol,
            )
        )

    async def reset_firma_excepcion_worm_agotado(
        self, acto_id: uuid.UUID, *, acto: str = "excepcion"
    ) -> builtins.list[asyncpg.Record]:
        """Salida de `agotado` (A4): repone a PENDIENTE (intentos a cero, sin backoff ni error) la
        protección inicial agotada y la renovación agotada de los artefactos del acto. Devuelve
        las filas repuestas (`artefacto`); vacío = nada estaba agotado. Va en la MISMA tx que su
        asiento."""
        await self._sp()
        t = self._acto(acto)
        return list(
            await self._conn.fetch(
                f"UPDATE {t['worm']} SET "
                "worm_reconcile_agotado = CASE WHEN worm_protegido_at IS NULL THEN FALSE "
                "ELSE worm_reconcile_agotado END, "
                "worm_reconcile_intentos = CASE WHEN worm_reconcile_agotado "
                "AND worm_protegido_at IS NULL THEN 0 ELSE worm_reconcile_intentos END, "
                "worm_reconcile_proximo_at = CASE WHEN worm_reconcile_agotado "
                "AND worm_protegido_at IS NULL THEN NULL ELSE worm_reconcile_proximo_at END, "
                "worm_reconcile_ultimo_error = CASE WHEN worm_reconcile_agotado "
                "AND worm_protegido_at IS NULL THEN NULL ELSE worm_reconcile_ultimo_error END, "
                "worm_renovacion_agotado = FALSE, "
                "worm_renovacion_intentos = CASE WHEN worm_renovacion_agotado THEN 0 "
                "ELSE worm_renovacion_intentos END, "
                "worm_renovacion_proximo_at = CASE WHEN worm_renovacion_agotado THEN NULL "
                "ELSE worm_renovacion_proximo_at END, "
                "worm_renovacion_ultimo_error = CASE WHEN worm_renovacion_agotado THEN NULL "
                "ELSE worm_renovacion_ultimo_error END "
                f"WHERE {t['fk']} = $1 AND ((worm_reconcile_agotado AND worm_protegido_at IS NULL) "
                "OR (worm_renovacion_agotado AND worm_protegido_at IS NOT NULL)) "
                "RETURNING artefacto, worm_protegido_at IS NOT NULL AS protegido",
                acto_id,
            )
        )

    async def list_firma_excepcion_worm_artefactos(
        self, acto_id: uuid.UUID, *, acto: str = "excepcion"
    ) -> builtins.list[asyncpg.Record]:
        """Artefactos (sello/personal/sobre) del acto con su estado WORM, para el reintento manual."""
        await self._sp()
        t = self._acto(acto)
        return list(
            await self._conn.fetch(
                f"SELECT artefacto, worm_protegido_at IS NOT NULL AS protegido, "
                f"worm_reconcile_agotado AS agotado FROM {t['worm']} "
                f"WHERE {t['fk']} = $1 ORDER BY artefacto",
                acto_id,
            )
        )

    async def list_sobres_huerfanos(
        self, *, limit: int, gracia_horas: int
    ) -> builtins.list[asyncpg.Record]:
        """Ficheros del almacén con el nombre de un sobre de revelación (objeto_id `sobre-<uuid>`,
        migración 064) que NINGUNA constancia ni revocación referencia en `sobre_minio_key` y que
        llevan más de `gracia_horas` subidos. `files` es de storage-service pero vive en el mismo
        esquema del tenant; si no existe (esquema parcial) no hay candidatos."""
        await self._sp()
        if await self._conn.fetchval("SELECT to_regclass('files')") is None:
            return []
        return list(
            await self._conn.fetch(
                r"""
                SELECT f.id, f.uploaded_at FROM files f
                 WHERE f.filename ~ '^acta-transferencia-sobre-[0-9a-f-]{36}-v[0-9]+-[0-9a-f-]{36}-firmado\.xml$'
                   AND f.uploaded_at < now() - ($1 * INTERVAL '1 hour')
                   AND NOT EXISTS (SELECT 1 FROM transferencia_acta_firma_excepcion e
                                    WHERE e.sobre_minio_key LIKE f.id::text || '/%')
                   AND NOT EXISTS (SELECT 1 FROM transferencia_acta_firma_excepcion_revocacion r
                                    WHERE r.sobre_minio_key LIKE f.id::text || '/%')
                 ORDER BY f.uploaded_at
                 LIMIT $2
                """,
                gracia_horas,
                limit,
            )
        )

    async def get_asiento_payload(
        self, *, tenant_slug: str, action: str, object_ref: str, clave: str, valor: str
    ) -> dict | None:
        """Payload del asiento `action` sobre `object_ref` cuyo `payload->>clave` es `valor`
        (el más antiguo). Lectura de `public.audit_log`, filtrada por tenant: la tabla se
        comparte entre entidades."""
        import json as _json

        row = await self._conn.fetchval(
            "SELECT payload FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2 AND object_ref = $3 "
            "AND payload->>$4 = $5 ORDER BY ts ASC LIMIT 1",
            tenant_slug,
            action,
            object_ref,
            clave,
            valor,
        )
        if row is None:
            return None
        return _json.loads(row) if isinstance(row, str | bytes) else dict(row)

    async def get_identidades(
        self, user_ids: builtins.list[uuid.UUID]
    ) -> dict[uuid.UUID, asyncpg.Record]:
        """Nombre, cargo y estado del certificado de firma de un lote de personas, tal como
        los resuelve el servidor (el cliente NO aporta quién es nadie). Solo columnas PÚBLICAS
        de `auth_users` / `auth_user_signing_key`: nunca el material de la clave privada.
        La persona que no está en el catálogo simplemente no aparece."""
        if not user_ids:
            return {}
        await self._sp()
        # `auth_users` y `auth_user_signing_key` las crea AUTH-SERVICE: en el esquema de una
        # entidad real están, pero un despliegue parcial o un esquema de pruebas provisionado
        # solo con las migraciones de archivo no las tiene (o las tiene con otra forma). Se
        # comprueban las COLUMNAS, igual que `get_acta`, y se degrada: sin catálogo la persona
        # queda `no_resuelta` (se declara el hueco), sin clave de firma queda sin certificado.
        presentes = await self._conn.fetchval(
            """SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND (table_name, column_name) IN
                      (('auth_users', 'username'),
                       ('auth_user_signing_key', 'user_id'),
                       ('auth_user_signing_key', 'subject_dn'),
                       ('auth_user_signing_key', 'cargo'),
                       ('auth_user_signing_key', 'estado'),
                       ('auth_user_signing_key', 'cert_pem'),
                       ('auth_user_signing_key', 'not_after'))"""
        )
        presentes = int(presentes or 0)
        if presentes == 0:
            return {}
        if presentes < 7:
            # Hay catálogo pero la forma de la clave de firma no es la esperada.
            rows = await self._conn.fetch(
                "SELECT u.id, u.username, NULL::text AS subject_dn, NULL::text AS cargo, "
                "FALSE AS certificado_activo FROM auth_users u WHERE u.id = ANY($1::uuid[])",
                user_ids,
            )
            return {r["id"]: r for r in rows}
        rows = await self._conn.fetch(
            """
            SELECT u.id, u.username, k.subject_dn, k.cargo,
                   (k.id IS NOT NULL AND k.estado = 'activo' AND k.cert_pem IS NOT NULL
                    AND (k.not_after IS NULL OR k.not_after > now())) AS certificado_activo
              FROM auth_users u
              LEFT JOIN auth_user_signing_key k
                     ON k.user_id = u.id AND k.estado = 'activo'
             WHERE u.id = ANY($1::uuid[])
            """,
            user_ids,
        )
        return {r["id"]: r for r in rows}

    async def insert_acta_firma_excepcion(self, data: dict) -> asyncpg.Record | None:
        """Inserta la constancia. `ON CONFLICT DO NOTHING`: `None` para la perdedora de una
        carrera (misma (transferencia, rol, ordinal) o misma sustituida: NO debe asentar un acto
        que no produjo). Si el rol ya firmó personalmente, el declarante es parte de la
        transferencia o la cadena no se puede continuar (revocación sin sellar/proteger), los
        triggers levantan `CheckViolationError` con `constraint_name` estable."""
        await self._sp()
        cols = list(data.keys())
        ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        return await self._conn.fetchrow(
            f"INSERT INTO transferencia_acta_firma_excepcion ({', '.join(cols)}) "
            f"VALUES ({ph}) ON CONFLICT DO NOTHING "
            f"RETURNING {_ACTA_FIRMA_EXCEPCION_COLS}",
            *data.values(),
        )

    async def insert_acta_firma_excepcion_revocacion(self, data: dict) -> asyncpg.Record | None:
        """Revocación de UNA excepción: fila nueva, nunca UPDATE. `ON CONFLICT DO NOTHING` ->
        `None` para la perdedora de una carrera (ya estaba revocada)."""
        await self._sp()
        cols = list(data.keys())
        ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        return await self._conn.fetchrow(
            f"INSERT INTO transferencia_acta_firma_excepcion_revocacion ({', '.join(cols)}) "
            f"VALUES ({ph}) ON CONFLICT DO NOTHING "
            "RETURNING id, excepcion_id, revocada_por, fundamento, revocada_en, "
            "revocacion_version, revocacion_sha256, firma_id, minio_key_firmado, xades_level, "
            "firma_personal_id, minio_key_firmado_personal, xades_level_personal, "
            "revocante_nombre, revocante_cargo, revocante_fuente, fundamento_sal, "
            "fundamento_compromiso, fundamento_compromiso_algoritmo, causa, "
            "sobre_firma_id, sobre_minio_key, sobre_sha256, sobre_xades_level",
            *data.values(),
        )

    async def insert_acta_firma_personal(self, data: dict) -> asyncpg.Record | None:
        """Inserta la fila de firma personal -- `ON CONFLICT (transferencia_id,
        rol) DO NOTHING`: bajo una carrera manual-vs-manual (dos peticiones
        concurrentes de `firmar_personal` para el MISMO rol, ambas ya
        pasaron el chequeo de idempotencia de lectura y ambas firmaron con
        signature-service, ver `TransferenciaService.firmar_personal`), solo
        UNA gana la escritura local -- `None` para la perdedora, que NUNCA
        debe escribir un asiento `archive.acta_firmada_personal` fantasma
        (misma clase de riesgo S1 que `mark_acta_firmada`)."""
        await self._sp()
        cols = list(data.keys())
        ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        return await self._conn.fetchrow(
            f"INSERT INTO transferencia_acta_firma_personal ({', '.join(cols)}) "
            f"VALUES ({ph}) ON CONFLICT (transferencia_id, rol) DO NOTHING "
            f"RETURNING {_ACTA_FIRMA_PERSONAL_COLS}",
            *data.values(),
        )

    # -----------------------------------------------------------------
    # AREA-07-10 (migración 057): cableado WORM de cada firma personal
    # -----------------------------------------------------------------

    async def insert_firma_personal_worm_pendiente(self, firma_personal_id: uuid.UUID) -> None:
        """Fila de bookkeeping WORM de una firma personal recién insertada: va en la MISMA
        tx que la firma, así toda firma personal tiene su fila aunque el proceso muera
        antes del disparo post-commit y la reconciliación la recoge igual."""
        await self._sp()
        await self._conn.execute(
            "INSERT INTO transferencia_acta_firma_personal_worm (firma_personal_id) "
            "VALUES ($1) ON CONFLICT (firma_personal_id) DO NOTHING",
            firma_personal_id,
        )

    async def get_firma_personal_worm(
        self, firma_personal_id: uuid.UUID
    ) -> asyncpg.Record | None:
        """La firma personal + su estado WORM + la transferencia (expediente) a la que
        pertenece, para `proteger_worm_firma_personal`."""
        await self._sp()
        return await self._conn.fetchrow(
            """
            SELECT fp.id, fp.transferencia_id, fp.rol, fp.minio_key_firmado, fp.creada_en,
                   t.expediente_id,
                   w.worm_protegido_at, w.worm_reconcile_intentos
              FROM transferencia_acta_firma_personal fp
              JOIN transferencias t ON t.id = fp.transferencia_id
              LEFT JOIN transferencia_acta_firma_personal_worm w ON w.firma_personal_id = fp.id
             WHERE fp.id = $1
            """,
            firma_personal_id,
        )

    async def mark_firma_personal_worm_protegido(
        self,
        firma_personal_id: uuid.UUID,
        worm_object_id: uuid.UUID | None,
        *,
        worm_retain_until: datetime | None,
    ) -> int:
        """Marca la copia WORM de la firma personal como confirmada. `WHERE ... IS NULL`:
        se fija UNA vez; devuelve las filas afectadas para no asentar un acto que esta
        llamada no produjo (carrera del disparo post-commit con la barrida)."""
        await self._sp()
        tag = await self._conn.execute(
            "UPDATE transferencia_acta_firma_personal_worm SET worm_protegido_at = now(), "
            "worm_object_id = $2, worm_retain_until = $3, worm_reconcile_proximo_at = NULL, "
            "worm_reconcile_ultimo_error = NULL "
            "WHERE firma_personal_id = $1 AND worm_protegido_at IS NULL",
            firma_personal_id,
            worm_object_id,
            worm_retain_until,
        )
        try:
            return int(tag.split()[-1])
        except (AttributeError, ValueError, IndexError):
            return 0

    async def bump_firma_personal_worm_intento(
        self,
        firma_personal_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotado: bool,
    ) -> None:
        await self._sp()
        await self._conn.execute(
            "UPDATE transferencia_acta_firma_personal_worm SET "
            "worm_reconcile_intentos = worm_reconcile_intentos + 1, "
            "worm_reconcile_proximo_at = $2, worm_reconcile_ultimo_error = $3, "
            "worm_reconcile_agotado = $4 WHERE firma_personal_id = $1",
            firma_personal_id,
            proximo_at,
            ultimo_error,
            agotado,
        )

    async def list_firma_personal_worm_pending(
        self, limit: int
    ) -> builtins.list[asyncpg.Record]:
        """Firmas personales sin proteger, no agotadas y con el backoff cumplido."""
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT w.firma_personal_id, w.worm_reconcile_intentos
                  FROM transferencia_acta_firma_personal_worm w
                 WHERE w.worm_protegido_at IS NULL
                   AND w.worm_reconcile_agotado = FALSE
                   AND (w.worm_reconcile_proximo_at IS NULL OR w.worm_reconcile_proximo_at <= now())
                 ORDER BY w.worm_reconcile_proximo_at ASC NULLS FIRST
                 LIMIT $1
                """,
                limit,
            )
        )

    # -----------------------------------------------------------------
    # E10 debt (migración 060): renovación de retención WORM CT de cada firma
    # personal del acta -- espejo de `list_worm_renovacion_*` del sello.
    # -----------------------------------------------------------------

    async def list_firma_personal_renovacion_pending(
        self, *, ventana_dias: int, ct_codes: builtins.list[str], limit: int
    ) -> builtins.list[asyncpg.Record]:
        """Firmas personales YA protegidas, de expediente CT, cuyo `worm_retain_until`
        vence dentro de `ventana_dias` y con el backoff cumplido."""
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT w.firma_personal_id, fp.transferencia_id, fp.rol, t.expediente_id,
                       w.worm_retain_until, w.worm_renovacion_intentos
                FROM transferencia_acta_firma_personal_worm w
                JOIN transferencia_acta_firma_personal fp ON fp.id = w.firma_personal_id
                JOIN transferencias t ON t.id = fp.transferencia_id
                JOIN expedientes e ON e.id = t.expediente_id
                JOIN trd_series s ON s.id = e.trd_serie_id
                WHERE w.worm_protegido_at IS NOT NULL
                  AND w.worm_renovacion_agotado = FALSE
                  AND w.worm_retain_until IS NOT NULL
                  AND w.worm_retain_until < now() + ($1 * INTERVAL '1 day')
                  AND (w.worm_renovacion_proximo_at IS NULL OR w.worm_renovacion_proximo_at <= now())
                  AND s.disposition = ANY($2::text[])
                ORDER BY w.worm_renovacion_proximo_at ASC NULLS FIRST
                LIMIT $3
                """,
                ventana_dias,
                ct_codes,
                limit,
            )
        )

    async def list_firma_personal_renovacion_visibilidad(
        self, *, max_clearance: int, ct_codes: builtins.list[str], ventana_dias: int
    ) -> builtins.list[asyncpg.Record]:
        """Firmas personales protegidas, de expediente CT, con la renovación AGOTADA o en
        riesgo de vencer; no-read-up en el `WHERE` (mismo criterio que el sello)."""
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT t.expediente_id, e.code AS expediente_code, fp.rol,
                       w.worm_retain_until, w.worm_renovacion_intentos,
                       w.worm_renovacion_agotado, w.worm_renovacion_ultimo_error
                FROM transferencia_acta_firma_personal_worm w
                JOIN transferencia_acta_firma_personal fp ON fp.id = w.firma_personal_id
                JOIN transferencias t ON t.id = fp.transferencia_id
                JOIN expedientes e ON e.id = t.expediente_id
                JOIN trd_series s ON s.id = e.trd_serie_id
                WHERE w.worm_protegido_at IS NOT NULL
                  AND s.disposition = ANY($1::text[])
                  AND e.nivel_seguridad <= $2
                  AND (
                        w.worm_renovacion_agotado = TRUE
                        OR (
                            w.worm_retain_until IS NOT NULL
                            AND w.worm_retain_until < now() + ($3 * INTERVAL '1 day')
                        )
                      )
                ORDER BY w.worm_renovacion_agotado DESC, w.worm_retain_until ASC NULLS LAST
                """,
                ct_codes,
                max_clearance,
                ventana_dias,
            )
        )

    async def mark_firma_personal_worm_renovado(
        self, firma_personal_id: uuid.UUID, *, worm_retain_until: datetime
    ) -> None:
        """Actualiza el espejo tras una renovación CONFIRMADA. Monotónico: `GREATEST`, el
        espejo nunca retrocede aunque llegue una respuesta tardía/reordenada."""
        await self._sp()
        await self._conn.execute(
            "UPDATE transferencia_acta_firma_personal_worm SET "
            "worm_retain_until = GREATEST(worm_retain_until, $2), "
            "worm_renovacion_intentos = 0, worm_renovacion_proximo_at = NULL, "
            "worm_renovacion_ultimo_error = NULL, worm_renovacion_agotado = FALSE "
            "WHERE firma_personal_id = $1",
            firma_personal_id,
            worm_retain_until,
        )

    async def bump_firma_personal_worm_renovacion_intento(
        self,
        firma_personal_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotado: bool,
    ) -> None:
        await self._sp()
        await self._conn.execute(
            "UPDATE transferencia_acta_firma_personal_worm SET "
            "worm_renovacion_intentos = worm_renovacion_intentos + 1, "
            "worm_renovacion_proximo_at = $2, worm_renovacion_ultimo_error = $3, "
            "worm_renovacion_agotado = $4 WHERE firma_personal_id = $1",
            firma_personal_id,
            proximo_at,
            ultimo_error,
            agotado,
        )

    # -----------------------------------------------------------------
    # AREA-07-10 (migraciones 061/062): WORM de los XML firmados de la constancia de firma
    # inobtenible y de su REVOCACIÓN (sello institucional + firma personal de quien actúa) y su
    # renovación CT. Espejo de los métodos de la firma personal (057/060), con `artefacto` en la
    # clave y `acto` ('excepcion' | 'revocacion') eligiendo tabla y origen del XML.
    # -----------------------------------------------------------------

    @staticmethod
    def _acto(acto: str) -> dict[str, str]:
        try:
            return _WORM_ACTOS[acto]
        except KeyError:
            raise ValueError(f"acto WORM desconocido: {acto!r}") from None

    async def insert_firma_excepcion_worm_pendiente(
        self, acto_id: uuid.UUID, artefacto: str, *, acto: str = "excepcion"
    ) -> None:
        """Fila de bookkeeping WORM del artefacto; va en la MISMA tx que la constancia (o la
        revocación)."""
        await self._sp()
        t = self._acto(acto)
        await self._conn.execute(
            f"INSERT INTO {t['worm']} ({t['fk']}, artefacto) "
            f"VALUES ($1, $2) ON CONFLICT ({t['fk']}, artefacto) DO NOTHING",
            acto_id,
            artefacto,
        )

    async def get_firma_excepcion_worm(
        self, acto_id: uuid.UUID, artefacto: str, *, acto: str = "excepcion"
    ) -> asyncpg.Record | None:
        """El artefacto (clave en el almacén según `artefacto`), su estado WORM y el
        expediente de la transferencia, para `proteger_worm_*`. `actuada_en` es la base del
        cálculo de retención: la declaración (o la revocación)."""
        await self._sp()
        t = self._acto(acto)
        if acto == "excepcion":
            sql = f"""
                SELECT e.id, e.transferencia_id, e.rol, e.declarada_en AS actuada_en,
                       CASE w.artefacto WHEN 'personal' THEN e.minio_key_firmado_personal
                            WHEN 'sobre' THEN e.sobre_minio_key
                            ELSE e.minio_key_firmado END AS minio_key_firmado,
                       tr.expediente_id, w.artefacto,
                       w.worm_protegido_at, w.worm_reconcile_intentos
                  FROM {t['worm']} w
                  JOIN transferencia_acta_firma_excepcion e ON e.id = w.excepcion_id
                  JOIN transferencias tr ON tr.id = e.transferencia_id
                 WHERE w.excepcion_id = $1 AND w.artefacto = $2
                """
        else:
            sql = f"""
                SELECT rv.id, e.transferencia_id, e.rol, rv.revocada_en AS actuada_en,
                       CASE w.artefacto WHEN 'personal' THEN rv.minio_key_firmado_personal
                            WHEN 'sobre' THEN rv.sobre_minio_key
                            ELSE rv.minio_key_firmado END AS minio_key_firmado,
                       tr.expediente_id, w.artefacto,
                       w.worm_protegido_at, w.worm_reconcile_intentos
                  FROM {t['worm']} w
                  JOIN transferencia_acta_firma_excepcion_revocacion rv ON rv.id = w.revocacion_id
                  JOIN transferencia_acta_firma_excepcion e ON e.id = rv.excepcion_id
                  JOIN transferencias tr ON tr.id = e.transferencia_id
                 WHERE w.revocacion_id = $1 AND w.artefacto = $2
                """
        return await self._conn.fetchrow(sql, acto_id, artefacto)

    async def mark_firma_excepcion_worm_protegido(
        self,
        acto_id: uuid.UUID,
        artefacto: str,
        worm_object_id: uuid.UUID | None,
        *,
        worm_retain_until: datetime | None,
        acto: str = "excepcion",
    ) -> int:
        """Marca el artefacto como confirmado. `WHERE ... IS NULL`: se fija UNA vez; devuelve
        las filas afectadas para no asentar un acto que esta llamada no produjo."""
        await self._sp()
        t = self._acto(acto)
        tag = await self._conn.execute(
            f"UPDATE {t['worm']} SET worm_protegido_at = now(), "
            "worm_object_id = $3, worm_retain_until = $4, worm_reconcile_proximo_at = NULL, "
            f"worm_reconcile_ultimo_error = NULL WHERE {t['fk']} = $1 AND artefacto = $2 "
            "AND worm_protegido_at IS NULL",
            acto_id,
            artefacto,
            worm_object_id,
            worm_retain_until,
        )
        try:
            return int(tag.split()[-1])
        except (AttributeError, ValueError, IndexError):
            return 0

    async def bump_firma_excepcion_worm_intento(
        self,
        acto_id: uuid.UUID,
        artefacto: str,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotado: bool,
        acto: str = "excepcion",
    ) -> None:
        await self._sp()
        t = self._acto(acto)
        await self._conn.execute(
            f"UPDATE {t['worm']} SET "
            "worm_reconcile_intentos = worm_reconcile_intentos + 1, "
            "worm_reconcile_proximo_at = $3, worm_reconcile_ultimo_error = $4, "
            f"worm_reconcile_agotado = $5 WHERE {t['fk']} = $1 AND artefacto = $2",
            acto_id,
            artefacto,
            proximo_at,
            ultimo_error,
            agotado,
        )

    async def list_firma_excepcion_worm_pending(
        self, limit: int, *, acto: str = "excepcion"
    ) -> builtins.list[asyncpg.Record]:
        """Artefactos sin proteger, no agotados y con el backoff cumplido (la clave de la fila
        es `excepcion_id` o `revocacion_id` según el `acto`)."""
        await self._sp()
        t = self._acto(acto)
        return list(
            await self._conn.fetch(
                f"""
                SELECT w.{t['fk']}, w.artefacto, w.worm_reconcile_intentos
                  FROM {t['worm']} w
                 WHERE w.worm_protegido_at IS NULL
                   AND w.worm_reconcile_agotado = FALSE
                   AND (w.worm_reconcile_proximo_at IS NULL OR w.worm_reconcile_proximo_at <= now())
                 ORDER BY w.worm_reconcile_proximo_at ASC NULLS FIRST
                 LIMIT $1
                """,
                limit,
            )
        )

    async def list_firma_excepcion_renovacion_pending(
        self,
        *,
        ventana_dias: int,
        ct_codes: builtins.list[str],
        limit: int,
        acto: str = "excepcion",
    ) -> builtins.list[asyncpg.Record]:
        """Artefactos YA protegidos, de expediente CT, cuyo `worm_retain_until` vence dentro de
        `ventana_dias` y con el backoff cumplido. Trae `minio_key_firmado` del artefacto: storage
        identifica estos tipos (multi-artefacto) por `file_id`, no por (expediente, tipo)."""
        await self._sp()
        t = self._acto(acto)
        if acto == "excepcion":
            origen = (
                "JOIN transferencia_acta_firma_excepcion e ON e.id = w.excepcion_id",
                "CASE w.artefacto WHEN 'personal' THEN e.minio_key_firmado_personal "
                "WHEN 'sobre' THEN e.sobre_minio_key ELSE e.minio_key_firmado END",
            )
        else:
            origen = (
                "JOIN transferencia_acta_firma_excepcion_revocacion rv ON rv.id = w.revocacion_id "
                "JOIN transferencia_acta_firma_excepcion e ON e.id = rv.excepcion_id",
                "CASE w.artefacto WHEN 'personal' THEN rv.minio_key_firmado_personal "
                "WHEN 'sobre' THEN rv.sobre_minio_key ELSE rv.minio_key_firmado END",
            )
        return list(
            await self._conn.fetch(
                f"""
                SELECT w.{t['fk']}, w.artefacto, e.transferencia_id, e.rol, tr.expediente_id,
                       {origen[1]} AS minio_key_firmado,
                       w.worm_retain_until, w.worm_renovacion_intentos
                FROM {t['worm']} w
                {origen[0]}
                JOIN transferencias tr ON tr.id = e.transferencia_id
                JOIN expedientes x ON x.id = tr.expediente_id
                JOIN trd_series s ON s.id = x.trd_serie_id
                WHERE w.worm_protegido_at IS NOT NULL
                  AND w.worm_renovacion_agotado = FALSE
                  AND w.worm_retain_until IS NOT NULL
                  AND w.worm_retain_until < now() + ($1 * INTERVAL '1 day')
                  AND (w.worm_renovacion_proximo_at IS NULL OR w.worm_renovacion_proximo_at <= now())
                  AND s.disposition = ANY($2::text[])
                ORDER BY w.worm_renovacion_proximo_at ASC NULLS FIRST
                LIMIT $3
                """,
                ventana_dias,
                ct_codes,
                limit,
            )
        )

    async def list_firma_excepcion_renovacion_visibilidad(
        self,
        *,
        max_clearance: int,
        ct_codes: builtins.list[str],
        ventana_dias: int,
        acto: str = "excepcion",
    ) -> builtins.list[asyncpg.Record]:
        """Artefactos protegidos, de expediente CT, con la renovación AGOTADA o en riesgo de
        vencer; no-read-up en el `WHERE` (mismo criterio que la firma personal)."""
        await self._sp()
        t = self._acto(acto)
        if acto == "excepcion":
            origen = "JOIN transferencia_acta_firma_excepcion e ON e.id = w.excepcion_id"
        else:
            origen = (
                "JOIN transferencia_acta_firma_excepcion_revocacion rv ON rv.id = w.revocacion_id "
                "JOIN transferencia_acta_firma_excepcion e ON e.id = rv.excepcion_id"
            )
        return list(
            await self._conn.fetch(
                f"""
                SELECT tr.expediente_id, x.code AS expediente_code, e.rol, w.artefacto,
                       w.worm_retain_until, w.worm_renovacion_intentos,
                       w.worm_renovacion_agotado, w.worm_renovacion_ultimo_error
                FROM {t['worm']} w
                {origen}
                JOIN transferencias tr ON tr.id = e.transferencia_id
                JOIN expedientes x ON x.id = tr.expediente_id
                JOIN trd_series s ON s.id = x.trd_serie_id
                WHERE w.worm_protegido_at IS NOT NULL
                  AND s.disposition = ANY($1::text[])
                  AND x.nivel_seguridad <= $2
                  AND (
                        w.worm_renovacion_agotado = TRUE
                        OR (
                            w.worm_retain_until IS NOT NULL
                            AND w.worm_retain_until < now() + ($3 * INTERVAL '1 day')
                        )
                      )
                ORDER BY w.worm_renovacion_agotado DESC, w.worm_retain_until ASC NULLS LAST
                """,
                ct_codes,
                max_clearance,
                ventana_dias,
            )
        )

    async def mark_firma_excepcion_worm_renovado(
        self,
        acto_id: uuid.UUID,
        artefacto: str,
        *,
        worm_retain_until: datetime,
        acto: str = "excepcion",
    ) -> None:
        """Espejo tras una renovación CONFIRMADA. Monotónico (`GREATEST`)."""
        await self._sp()
        t = self._acto(acto)
        await self._conn.execute(
            f"UPDATE {t['worm']} SET "
            "worm_retain_until = GREATEST(worm_retain_until, $3), "
            "worm_renovacion_intentos = 0, worm_renovacion_proximo_at = NULL, "
            "worm_renovacion_ultimo_error = NULL, worm_renovacion_agotado = FALSE "
            f"WHERE {t['fk']} = $1 AND artefacto = $2",
            acto_id,
            artefacto,
            worm_retain_until,
        )

    async def bump_firma_excepcion_worm_renovacion_intento(
        self,
        acto_id: uuid.UUID,
        artefacto: str,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotado: bool,
        acto: str = "excepcion",
    ) -> None:
        await self._sp()
        t = self._acto(acto)
        await self._conn.execute(
            f"UPDATE {t['worm']} SET "
            "worm_renovacion_intentos = worm_renovacion_intentos + 1, "
            "worm_renovacion_proximo_at = $3, worm_renovacion_ultimo_error = $4, "
            f"worm_renovacion_agotado = $5 WHERE {t['fk']} = $1 AND artefacto = $2",
            acto_id,
            artefacto,
            proximo_at,
            ultimo_error,
            agotado,
        )

    async def list_acta_firmas_pendientes(self, limit: int) -> builtins.list[asyncpg.Record]:
        """Actas `'pendiente_firma'` elegibles para el job de reconciliación
        (gemela de `IndexRepository.list_pending_signature`). A diferencia
        del índice (append-only, `DISTINCT ON` sobre versiones), el acta es
        1:1 por transferencia -- no hay versión que resolver. Excluye
        `reconcile_agotado=TRUE` y respeta el backoff
        (`reconcile_proximo_at IS NULL OR <= now()`); mismo orden
        `NULLS FIRST` que su gemela (recién degradadas primero)."""
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT taf.transferencia_id, taf.reconcile_intentos,
                       ta.acta_fuid_xml, ta.indice_version
                FROM transferencia_acta_firma taf
                JOIN transferencia_acta ta ON ta.transferencia_id = taf.transferencia_id
                WHERE taf.estado = 'pendiente_firma'
                  AND taf.reconcile_agotado = false
                  AND (taf.reconcile_proximo_at IS NULL OR taf.reconcile_proximo_at <= now())
                ORDER BY taf.reconcile_proximo_at ASC NULLS FIRST
                LIMIT $1
                """,
                limit,
            )
        )

    # -----------------------------------------------------------------
    # E10 Inc.2 wiring: cableado WORM del acta -- espejo EXACTO de
    # `IndexRepository.get_by_id`/`mark_worm_protegido`/
    # `bump_worm_reconcile_intento`/`list_worm_pending`.
    # -----------------------------------------------------------------

    async def get_acta_firma_worm(self, transferencia_id: uuid.UUID) -> asyncpg.Record | None:
        """Fila de `transferencia_acta_firma` con las columnas del cableado
        WORM (`_ACTA_FIRMA_WORM_COLS`) incluidas -- usada por
        `TransferenciaService.proteger_worm_acta` (disparo síncrono
        post-commit Y 4ª fase de reconciliación) para leer
        `minio_key_firmado`/`estado`/`firmado_at`/`worm_protegido_at` de la
        MISMA fila que se acaba de firmar (gemela de `IndexRepository.
        get_by_id`)."""
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_ACTA_FIRMA_COLS}, {_ACTA_FIRMA_WORM_COLS} "
            "FROM transferencia_acta_firma WHERE transferencia_id = $1",
            transferencia_id,
        )

    async def mark_acta_worm_protegido(
        self,
        transferencia_id: uuid.UUID,
        worm_object_id: uuid.UUID,
        *,
        worm_retain_until: datetime | None = None,
    ) -> int:
        """Marca la copia WORM del acta como confirmada (E10 Inc.2 wiring,
        gemela de `IndexRepository.mark_worm_protegido`).

        Defensa en profundidad (mismo patrón que `mark_acta_firmada`): el
        `WHERE` excluye filas ya protegidas -- `worm_protegido_at` se fija UNA
        sola vez, nunca se re-escribe. Devuelve el número de filas afectadas
        (0 o 1): `TransferenciaService.proteger_worm_acta` lo usa para no
        escribir un asiento `archive.acta_worm_protegido` fantasma si OTRA
        llamada concurrente (el disparo síncrono post-commit vs. la barrida de
        reconciliación corriendo en paralelo) ya ganó la carrera.

        `worm_retain_until` (E10 debt, migración 032) puebla el espejo local
        del `retain_until` confirmado por storage-service -- opcional
        (default `None`, mismo criterio que `IndexRepository.mark_worm_protegido`).
        """
        await self._sp()
        tag = await self._conn.execute(
            "UPDATE transferencia_acta_firma SET worm_protegido_at = now(), "
            "worm_object_id = $2, worm_retain_until = COALESCE($3, worm_retain_until) "
            "WHERE transferencia_id = $1 AND worm_protegido_at IS NULL",
            transferencia_id,
            worm_object_id,
            worm_retain_until,
        )
        try:
            return int(tag.split()[-1])
        except (AttributeError, ValueError, IndexError):
            # Mismo fallback defensivo que `mark_acta_firmada` -- solo protege
            # mocks de test cuyo `conn.execute` no configura un valor de
            # retorno realista.
            return 0

    async def bump_acta_worm_reconcile_intento(
        self,
        transferencia_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotado: bool,
    ) -> None:
        """Registra un intento FALLIDO de protección WORM del acta (E10 Inc.2
        wiring) -- gemelo de `IndexRepository.bump_worm_reconcile_intento`:
        incrementa `worm_reconcile_intentos` atómicamente en el propio UPDATE
        y fija `worm_reconcile_proximo_at`/`worm_reconcile_ultimo_error`/
        `worm_reconcile_agotado` con lo que decidió el llamador."""
        await self._sp()
        await self._conn.execute(
            "UPDATE transferencia_acta_firma SET "
            "worm_reconcile_intentos = worm_reconcile_intentos + 1, "
            "worm_reconcile_proximo_at = $2, worm_reconcile_ultimo_error = $3, "
            "worm_reconcile_agotado = $4 WHERE transferencia_id = $1",
            transferencia_id,
            proximo_at,
            ultimo_error,
            agotado,
        )

    async def list_worm_pending_actas(self, limit: int) -> builtins.list[asyncpg.Record]:
        """Actas 'firmado' elegibles para `run_once_actas_worm` (4ª fase de
        reconciliación, E10 Inc.2 wiring): sin proteger todavía
        (`worm_protegido_at IS NULL`), no agotadas, y con el backoff cumplido
        -- gemela de `IndexRepository.list_worm_pending`.

        Sin `DISTINCT ON`: el acta es 1:1 por transferencia (no versiona como
        el índice), a lo sumo una fila por `transferencia_id` puede llegar a
        `estado='firmado'`.
        """
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT taf.transferencia_id, taf.minio_key_firmado, t.expediente_id,
                       taf.worm_reconcile_intentos, taf.worm_reconcile_agotado,
                       taf.worm_reconcile_ultimo_error
                FROM transferencia_acta_firma taf
                JOIN transferencias t ON t.id = taf.transferencia_id
                WHERE taf.estado = 'firmado'
                  AND taf.worm_protegido_at IS NULL
                  AND taf.worm_reconcile_agotado = FALSE
                  AND (taf.worm_reconcile_proximo_at IS NULL OR taf.worm_reconcile_proximo_at <= now())
                ORDER BY taf.worm_reconcile_proximo_at ASC NULLS FIRST
                LIMIT $1
                """,
                limit,
            )
        )

    # -----------------------------------------------------------------
    # E10 debt: renovación de retención WORM del acta para series CT --
    # espejo EXACTO de `IndexRepository.list_worm_renovacion_pending`/
    # `mark_worm_renovado`/`bump_worm_renovacion_intento`.
    # -----------------------------------------------------------------

    async def list_worm_renovacion_pending(
        self, *, ventana_dias: int, ct_codes: builtins.list[str], limit: int
    ) -> builtins.list[asyncpg.Record]:
        """Actas CUYO EXPEDIENTE es de disposición Conservación Total (CT) y
        cuyo `worm_retain_until` vence dentro de `ventana_dias` (E10 debt,
        migración 032) -- 5ª fase de reconciliación, `run_once_worm_renovacion`.
        Gemela EXACTA de `IndexRepository.list_worm_renovacion_pending`."""
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT taf.transferencia_id, t.expediente_id, taf.worm_retain_until,
                       taf.worm_renovacion_intentos
                FROM transferencia_acta_firma taf
                JOIN transferencias t ON t.id = taf.transferencia_id
                JOIN expedientes e ON e.id = t.expediente_id
                JOIN trd_series s ON s.id = e.trd_serie_id
                WHERE taf.worm_protegido_at IS NOT NULL
                  AND taf.worm_renovacion_agotado = FALSE
                  AND taf.worm_retain_until IS NOT NULL
                  AND taf.worm_retain_until < now() + ($1 * INTERVAL '1 day')
                  AND (taf.worm_renovacion_proximo_at IS NULL OR taf.worm_renovacion_proximo_at <= now())
                  AND s.disposition = ANY($2::text[])
                ORDER BY taf.worm_renovacion_proximo_at ASC NULLS FIRST
                LIMIT $3
                """,
                ventana_dias,
                ct_codes,
                limit,
            )
        )

    async def list_worm_renovacion_visibilidad(
        self, *, max_clearance: int, ct_codes: builtins.list[str], ventana_dias: int
    ) -> builtins.list[asyncpg.Record]:
        """Actas YA protegidas bajo WORM, de expediente CT, cuya renovación
        está AGOTADA o EN RIESGO de vencer -- gemela EXACTA de
        `IndexRepository.list_worm_renovacion_visibilidad` (mismo criterio de
        no-read-up, misma ventana, mismos códigos CT); ver su docstring para
        el contraste con `list_worm_renovacion_pending` (consumida por el
        job)."""
        await self._sp()
        return list(
            await self._conn.fetch(
                """
                SELECT t.expediente_id, e.code AS expediente_code,
                       taf.worm_retain_until, taf.worm_renovacion_intentos,
                       taf.worm_renovacion_agotado, taf.worm_renovacion_ultimo_error
                FROM transferencia_acta_firma taf
                JOIN transferencias t ON t.id = taf.transferencia_id
                JOIN expedientes e ON e.id = t.expediente_id
                JOIN trd_series s ON s.id = e.trd_serie_id
                WHERE taf.worm_protegido_at IS NOT NULL
                  AND s.disposition = ANY($1::text[])
                  AND e.nivel_seguridad <= $2
                  AND (
                        taf.worm_renovacion_agotado = TRUE
                        OR (
                            taf.worm_retain_until IS NOT NULL
                            AND taf.worm_retain_until < now() + ($3 * INTERVAL '1 day')
                        )
                      )
                ORDER BY taf.worm_renovacion_agotado DESC, taf.worm_retain_until ASC NULLS LAST
                """,
                ct_codes,
                max_clearance,
                ventana_dias,
            )
        )

    async def mark_acta_worm_renovado(
        self, transferencia_id: uuid.UUID, *, worm_retain_until: datetime
    ) -> None:
        """Actualiza el espejo `worm_retain_until` del acta tras una llamada
        CONFIRMADA a `renovar-retencion-internal` -- gemela EXACTA de
        `IndexRepository.mark_worm_renovado`."""
        await self._sp()
        await self._conn.execute(
            "UPDATE transferencia_acta_firma SET worm_retain_until = $2, "
            "worm_renovacion_intentos = 0, worm_renovacion_proximo_at = NULL, "
            "worm_renovacion_ultimo_error = NULL, worm_renovacion_agotado = FALSE "
            "WHERE transferencia_id = $1",
            transferencia_id,
            worm_retain_until,
        )

    async def bump_acta_worm_renovacion_intento(
        self,
        transferencia_id: uuid.UUID,
        *,
        proximo_at: datetime | None,
        ultimo_error: str,
        agotado: bool,
    ) -> None:
        """Registra un intento FALLIDO de renovación de retención WORM del
        acta (E10 debt) -- gemela EXACTA de
        `IndexRepository.bump_worm_renovacion_intento`."""
        await self._sp()
        await self._conn.execute(
            "UPDATE transferencia_acta_firma SET "
            "worm_renovacion_intentos = worm_renovacion_intentos + 1, "
            "worm_renovacion_proximo_at = $2, worm_renovacion_ultimo_error = $3, "
            "worm_renovacion_agotado = $4 WHERE transferencia_id = $1",
            transferencia_id,
            proximo_at,
            ultimo_error,
            agotado,
        )
