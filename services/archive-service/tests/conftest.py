# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Test fixtures for archive-service.

DB dependency (get_db_conn) is mocked so tests run without real infrastructure.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db_conn
from app.main import app
from app.services.transferencia_service import TransferenciaService as _TransferenciaService

# Las pruebas unitarias llaman a la aplicación DIRECTAMENTE, sin gateway.
#
# Desde que la identidad va firmada, eso es exactamente lo que el servicio
# rechaza en producción: una petición con cabeceras de usuario que nadie firmó.
# Aquí se corre en el modo «firma no activada», que es una configuración
# legítima —la que tiene un despliegue que aún no puso el secreto— y no un
# atajo: la firma en sí la cubren `shared/tests/test_la_identidad_viene_firmada.py`
# y la comprobación contra la pila levantada.
os.environ.pop("IDENTITY_SIGNING_SECRET", None)


TENANT_SLUG = "demo"
USER_ID = uuid.uuid4()
TENANT_HEADERS = {
    "X-Tenant-Slug": TENANT_SLUG,
    "X-User-Id": str(USER_ID),
}

EXPEDIENTE_ID = uuid.uuid4()
RADICADO_ID = uuid.uuid4()
SERIE_ID = uuid.uuid4()
TRACKING_NUMBER = "2024-DEMO-E-000001"
NOW = datetime.now(tz=UTC)


def indice_firmado_row(*, version: int = 1, firma_id: uuid.UUID | None = None) -> dict:
    """Fila de `expediente_indice` ya FIRMADA: lo que `get_current` devuelve al gate de
    `TransferenciaService.create`/`recibir` (AREA-07-03/04). Sin ella el expediente
    cerrado «no tiene índice firmado» y la transferencia no se prepara."""
    return {
        "id": uuid.uuid4(),
        "version": version,
        "estado": "firmado",
        "firma_id": firma_id or uuid.uuid4(),
        "xml_sha256": "a" * 64,
        "xml": "<indice/>",
    }


def make_expediente_record(
    *,
    exp_id: uuid.UUID | None = None,
    code: str = "EXP-2024-0001",
    name: str = "Contrato 2024-001",
    description: str | None = "Contrato de prestación de servicios",
    trd_serie_id: uuid.UUID | None = None,
    exp_status: str = "open",
    opened_by: uuid.UUID | None = None,
    opened_at: datetime | None = None,
    closed_at: datetime | None = None,
    updated_at: datetime | None = None,
    metadata: dict | None = None,
    metadata_template_id: uuid.UUID | None = None,
    disposition: dict | None = None,
    nivel_seguridad: int = 1,
    # ADR-025 (Increment 1): denormalización de clasificación (congelada al
    # cerrar) + marca honesta de revisión pendiente. `None`/`False` por
    # defecto -- el expediente `open` típico de los tests aún no tiene amarre
    # congelado.
    trd_serie_code: str | None = None,
    trd_revision_requerida: bool = False,
    trd_revision_motivo: str | None = None,
    # ADR-026 (Increment 2): amarre de fondo acumulado. 'corriente' por
    # defecto -- espejo del comportamiento previo a la 041.
    origen: str = "corriente",
    fecha_extrema_inicial: date | None = None,
    fecha_extrema_final: date | None = None,
) -> dict:
    return {
        "id": exp_id or EXPEDIENTE_ID,
        "code": code,
        "name": name,
        "description": description,
        "trd_serie_id": trd_serie_id,
        "status": exp_status,
        "opened_by": opened_by or USER_ID,
        "opened_at": opened_at or NOW,
        "closed_at": closed_at,
        "updated_at": updated_at or NOW,
        "metadata": metadata if metadata is not None else {},
        "metadata_template_id": metadata_template_id,
        "disposition": disposition,
        # E15 perfil v3 (RT-15 art. 4.3.2.3): <listaControlAcceso> hereda este
        # nivel. 1=PUBLICA (default), 2=RESERVADA, 3=CLASIFICADA.
        "nivel_seguridad": nivel_seguridad,
        "trd_serie_code": trd_serie_code,
        "trd_revision_requerida": trd_revision_requerida,
        "trd_revision_motivo": trd_revision_motivo,
        "origen": origen,
        "fecha_extrema_inicial": fecha_extrema_inicial,
        "fecha_extrema_final": fecha_extrema_final,
    }


def make_expediente_update_row(*, old: dict, new: dict) -> dict:
    """Fila mockeada del nuevo `ArchiveRepository.update_expediente` (metadatos):
    el estado NUEVO (`new`, `e.*`) más los valores PREVIOS (`old_*`) que la
    sentencia real trae del `FOR UPDATE` de la MISMA query — ver su docstring
    (el "before" auditado NO sale de una pre-lectura separada, H3)."""
    return {
        **new,
        "old_name": old["name"],
        "old_description": old["description"],
        "old_trd_serie_id": old["trd_serie_id"],
        "old_status": old["status"],
    }


def make_radicado_link_record(
    *,
    expediente_id: uuid.UUID | None = None,
    radicado_id: uuid.UUID | None = None,
    tracking_number: str = TRACKING_NUMBER,
    added_at: datetime | None = None,
    content_hash: str | None = None,
    formato: str | None = None,
    tamano_bytes: int | None = None,
    folios: int | None = None,
    orden_documental: int | None = 1,
    estado: str = "vinculado",
    excluido_at: datetime | None = None,
    excluido_causal: str | None = None,
    dependencia_productora_codigo: int | None = None,
    dependencia_productora_nombre: str | None = None,
    tipologia: str | None = None,
) -> dict:
    return {
        "expediente_id": expediente_id or EXPEDIENTE_ID,
        "radicado_id": radicado_id or RADICADO_ID,
        "tracking_number": tracking_number,
        "added_at": added_at or NOW,
        "content_hash": content_hash,
        # Metadatos RT-15 del índice electrónico (E15 Inc.3); NULL por defecto
        # = no declarado al vincular. `tipologia` (perfil v4) entra por la misma
        # puerta: la fila real la trae SIEMPRE tras la migración 051, así que la
        # fixtura la trae también — un `.get` tolerante en el repositorio
        # escondería justo el desajuste que esta fixtura existe para detectar.
        "tipologia": tipologia,
        "formato": formato,
        "tamano_bytes": tamano_bytes,
        "folios": folios,
        # E15 perfil v3 (RT-15 art. 4.3.2.3, migración 018): orden_documental
        # ESTABLE (default 1 = primer/único vínculo del expediente en la
        # mayoría de los tests) + estado del vínculo (soft-delete).
        "orden_documental": orden_documental,
        "estado": estado,
        "excluido_at": excluido_at,
        "excluido_causal": excluido_causal,
        # E15 perfil v4 (R1, migración 019): dependencia productora del
        # documento, aportada al vincular (Opción A); NULL por defecto = no
        # declarada. Ambos campos van juntos o ninguno (CHECK de pareja + Pydantic).
        "dependencia_productora_codigo": dependencia_productora_codigo,
        "dependencia_productora_nombre": dependencia_productora_nombre,
    }


def make_trd_serie_record(
    *,
    serie_id: uuid.UUID | None = None,
    code: str = "100",
    name: str = "Contratos",
    description: str | None = "Series contractuales",
    retention_years: int = 5,
    total_retention: int = 10,
    disposition: str = "conserve",
    is_active: bool = True,
    created_at: datetime | None = None,
    parent_id: uuid.UUID | None = None,
    archivo_gestion_years: int | None = 5,
    archivo_central_years: int | None = 5,
    version: int = 1,
    valid_from=None,
    pdfa_profile: str | None = None,
    # ADR-025 (Increment 1): default 'migrada' -- la MAYORÍA de los tests
    # existentes asumen una serie ya "usable" (gobierna un cierre) sin ejercer
    # el circuito de convalidación; espejo del backfill de la migración 037.
    # Los tests que SÍ ejercen el append-only/gate pasan `estado=` explícito.
    estado: str = "migrada",
    valid_to=None,
    acto_administrativo: str | None = None,
    acta_comite: str | None = None,
    fecha_aprobacion_comite=None,
    fecha_convalidacion=None,
    instancia_convalidante: str | None = None,
    rusd_radicado: str | None = None,
    fecha_publicacion=None,
    supersede_a: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    tipo_instrumento: str = "TRD",
    fondo_nombre: str | None = None,
    fecha_extrema_inicial=None,
    fecha_extrema_final=None,
    justificacion_valoracion: str | None = None,
) -> dict:
    return {
        "id": serie_id or SERIE_ID,
        "code": code,
        "name": name,
        "description": description,
        "retention_years": retention_years,
        "total_retention": total_retention,
        "disposition": disposition,
        "is_active": is_active,
        "created_at": created_at or NOW,
        "parent_id": parent_id,
        "archivo_gestion_years": archivo_gestion_years,
        "archivo_central_years": archivo_central_years,
        "version": version,
        "valid_from": valid_from or NOW.date(),
        "pdfa_profile": pdfa_profile,
        "estado": estado,
        "valid_to": valid_to,
        "acto_administrativo": acto_administrativo,
        "acta_comite": acta_comite,
        "fecha_aprobacion_comite": fecha_aprobacion_comite,
        "fecha_convalidacion": fecha_convalidacion,
        "instancia_convalidante": instancia_convalidante,
        "rusd_radicado": rusd_radicado,
        "fecha_publicacion": fecha_publicacion,
        "supersede_a": supersede_a,
        "created_by": created_by,
        # ADR-026 (Increment 1): discriminador TVD, aditivo -- default 'TRD'
        # conserva el comportamiento de TODOS los tests preexistentes que
        # construyen filas TRD sin conocer este campo.
        "tipo_instrumento": tipo_instrumento,
        "fondo_nombre": fondo_nombre,
        "fecha_extrema_inicial": fecha_extrema_inicial,
        "fecha_extrema_final": fecha_extrema_final,
        "justificacion_valoracion": justificacion_valoracion,
    }


def make_tvd_agrupacion_record(
    *,
    serie_id: uuid.UUID | None = None,
    code: str = "FONDO-100",
    name: str = "Fondo acumulado Dependencia X",
    description: str | None = "Documentacion de dependencia suprimida",
    total_retention: int = 10,
    disposition: str = "CT",
    is_active: bool = True,
    created_at: datetime | None = None,
    parent_id: uuid.UUID | None = None,
    archivo_central_years: int | None = 10,
    version: int = 1,
    valid_from=None,
    pdfa_profile: str | None = None,
    estado: str = "borrador",
    valid_to=None,
    acto_administrativo: str | None = None,
    acta_comite: str | None = None,
    fecha_aprobacion_comite=None,
    fecha_convalidacion=None,
    instancia_convalidante: str | None = None,
    rusd_radicado: str | None = None,
    fecha_publicacion=None,
    supersede_a: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    fondo_nombre: str = "Fondo Instituto Departamental de X (liquidado 2003)",
    fecha_extrema_inicial=date(1975, 1, 1),
    fecha_extrema_final=date(1998, 12, 31),
    justificacion_valoracion: str = "Valoracion secundaria: sin valores primarios vigentes.",
) -> dict:
    """ADR-026 (Increment 1): gemela de `make_trd_serie_record` para TVD --
    `tipo_instrumento='TVD'`, `archivo_gestion_years=None` (la TVD no tiene
    fase de archivo de gestión, `retention_years=0`) y las cuatro columnas de
    contenido valorado SIEMPRE pobladas (CHECK `trd_series_tvd_forma_check`).
    Estado por defecto `borrador` (nunca `migrada`: una TVD nace vacía, D6 no
    aplica -- a diferencia del default `migrada` de `make_trd_serie_record`,
    que espeja el backfill de la 037)."""
    return make_trd_serie_record(
        serie_id=serie_id,
        code=code,
        name=name,
        description=description,
        retention_years=0,
        total_retention=total_retention,
        disposition=disposition,
        is_active=is_active,
        created_at=created_at,
        parent_id=parent_id,
        archivo_gestion_years=None,
        archivo_central_years=archivo_central_years,
        version=version,
        valid_from=valid_from,
        pdfa_profile=pdfa_profile,
        estado=estado,
        valid_to=valid_to,
        acto_administrativo=acto_administrativo,
        acta_comite=acta_comite,
        fecha_aprobacion_comite=fecha_aprobacion_comite,
        fecha_convalidacion=fecha_convalidacion,
        instancia_convalidante=instancia_convalidante,
        rusd_radicado=rusd_radicado,
        fecha_publicacion=fecha_publicacion,
        supersede_a=supersede_a,
        created_by=created_by,
        tipo_instrumento="TVD",
        fondo_nombre=fondo_nombre,
        fecha_extrema_inicial=fecha_extrema_inicial,
        fecha_extrema_final=fecha_extrema_final,
        justificacion_valoracion=justificacion_valoracion,
    )


def _make_mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = 0
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    # `async with conn.transaction():` → context manager (índice E15, etc.).
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


@pytest_asyncio.fixture(scope="function")
async def client_with_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    """Yields (client, mock_conn) so tests can configure mock return values per-call."""
    mock_conn = _make_mock_conn()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_get_db_conn
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, mock_conn

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Simple client — use when you don't need to inspect mock internals."""
    mock_conn = _make_mock_conn()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_get_db_conn
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# document-service como COLABORADOR de `link_radicado` (AREA-07-02/06-08).
#
# Vincular coteja el radicado con document-service y falla cerrado si no puede
# (404 si no existe, 503 si no responde). Un test que vincula sin declarar qué
# dice document-service del radicado afirma sobre un mundo que no existe, así
# que la respuesta de ese colaborador es un dato EXPLÍCITO de cada test:
# `registrar_radicado(...)` lo declara y el fixture autouse lo sirve. El
# radicado de los fixtures (`RADICADO_ID` / `TRACKING_NUMBER`) ya viene dado.
# ---------------------------------------------------------------------------

_EN_DOCUMENT_SERVICE: dict[str, dict[str, Any]] = {}


def registrar_radicado(
    tracking_number: str,
    *,
    radicado_id: uuid.UUID | None = None,
    **meta: Any,
) -> uuid.UUID:
    """Declara qué guarda document-service de un radicado y devuelve su id.
    `meta` admite `doc_class`, `origin_dept(_code)`, `formato`, `tamano_bytes`."""
    rid = radicado_id or uuid.uuid4()
    _EN_DOCUMENT_SERVICE[str(rid)] = {
        "subject": "Asunto de prueba",
        "doc_type": "E",
        "tracking_number": tracking_number,
        "doc_class": "COR-PET",
        "origin_dept": None,
        "origin_dept_code": None,
        "formato": None,
        "tamano_bytes": None,
        **meta,
    }
    return rid


def datos_radicado(radicado_id: uuid.UUID | None = None, tracking_number: str | None = None) -> dict[str, Any]:
    """`radicado_id` + `tracking_number` de un radicado que document-service
    SÍ conoce, listos para `RadicadoLink(**datos_radicado())`."""
    rid = radicado_id or uuid.uuid4()
    tracking = tracking_number or f"2026-ITEST-E-{uuid.uuid4().hex[:6]}"
    registrar_radicado(tracking, radicado_id=rid)
    return {"radicado_id": rid, "tracking_number": tracking}


@pytest.fixture(autouse=True)
def _document_service_responde() -> Iterator[None]:
    _EN_DOCUMENT_SERVICE.clear()
    registrar_radicado(TRACKING_NUMBER, radicado_id=RADICADO_ID)

    async def _fake(
        *, tenant_slug: str, radicado_ids: list[uuid.UUID], max_clearance: int
    ) -> dict[str, dict[str, Any]]:
        return {
            str(r): _EN_DOCUMENT_SERVICE[str(r)]
            for r in radicado_ids
            if str(r) in _EN_DOCUMENT_SERVICE
        }

    with patch(
        "app.clients.document_client.fetch_radicado_metadata_internal",
        AsyncMock(side_effect=_fake),
    ):
        yield


# ---------------------------------------------------------------------------
# AREA-07-05: el evento PREMIS del fixity-check de la recepción lo registra
# `TransferenciaService.registrar_fixity_premis` POST-commit (HTTP a storage-service).
# Los tests unitarios de `recibir()` no versan sobre eso: se sustituye por un doble para
# no depender de un storage real ni consumir las secuencias de `fetchrow` que arman. El
# método REAL se prueba aparte (`tests/test_fixity_premis_recepcion.py`) y en integración.
# `REGISTRAR_FIXITY_PREMIS_REAL` conserva el original para esos tests.
# ---------------------------------------------------------------------------

REGISTRAR_FIXITY_PREMIS_REAL = _TransferenciaService.registrar_fixity_premis


@pytest.fixture(autouse=True)
def _fixity_premis_doble() -> Iterator[AsyncMock]:
    doble = AsyncMock(return_value="registrado")
    with patch.object(_TransferenciaService, "registrar_fixity_premis", doble):
        yield doble


# AREA-07-10: igual con el Object-Lock de la firma personal (`proteger_worm_firma_personal`,
# post-commit, HTTP a storage-service). El método real se prueba en
# `tests/test_firma_personal_worm.py`.
PROTEGER_WORM_FIRMA_PERSONAL_REAL = _TransferenciaService.proteger_worm_firma_personal


@pytest.fixture(autouse=True)
def _firma_personal_worm_doble() -> Iterator[AsyncMock]:
    doble = AsyncMock(return_value="protegido")
    with patch.object(_TransferenciaService, "proteger_worm_firma_personal", doble):
        yield doble
