# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-6 — el JOB de renovación contra Postgres REAL: el plazo es lo primero que caduque de la TSA y de su CA.

Mutaciones: que `firmas_a_renovar` use solo `tsa_cert_not_after` (cae
`test_la_ca_que_vence_antes_hace_candidata_a_la_firma`); que el paso 0 del job use solo
`tsa_cert_not_after` (cae `test_una_ca_vencida_deja_el_eslabon_caducado`); que el eslabón nuevo no
persista las columnas de C-6 (cae `test_el_eslabon_renovado_registra_cadena_fuente_y_revocacion_previa`)."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.jobs import renovacion_lta
from app.repositories.firma_repository import FirmaRepository
from tests.integration.test_b10_lta_renovacion_sql import (  # noqa: F401  (fixtures y helpers)
    _asientos,
    _sellos,
    _sembrar,
    _tsa_larga,
    storage,
)
from tests.test_xmldsig_conformidad import _tsa_local  # noqa: F401  (autouse)

pytestmark = pytest.mark.asyncio


async def _ca_vence_en(conn, firma_id, dias: int) -> None:  # noqa: ANN001
    await conn.execute(
        "UPDATE firma_sello_archivo SET tsa_cadena_not_after = now() + make_interval(days => $2)"
        " WHERE firma_id = $1",
        firma_id, dias,
    )


async def test_la_ca_que_vence_antes_hace_candidata_a_la_firma(tenant_ctx, monkeypatch, storage) -> None:  # noqa: ANN001, F811
    slug, conn = tenant_ctx
    _doc, firma_id, _key, xml = await _sembrar(conn, slug, monkeypatch)
    storage.descargar.return_value = xml
    monkeypatch.setattr(settings, "lta_renovacion_margen_dias", 90)  # la TSA vence en ~365 d: lejos

    assert await FirmaRepository(conn).firmas_a_renovar(90) == [], "sin CA que venza antes no es candidata"

    await _ca_vence_en(conn, firma_id, 20)  # la hoja sigue lejos; su CA, no
    candidatas = await FirmaRepository(conn).firmas_a_renovar(90)
    assert [c["firma_id"] for c in candidatas] == [firma_id]
    assert (candidatas[0]["vence_at"] - candidatas[0]["created_at"]).days < 60, "vence_at es el de la CA"


async def test_el_eslabon_renovado_registra_cadena_fuente_y_revocacion_previa(  # noqa: ANN201
    tenant_ctx, monkeypatch, storage  # noqa: ANN001, F811
):
    slug, conn = tenant_ctx
    _doc, firma_id, _key, xml = await _sembrar(conn, slug, monkeypatch)
    storage.descargar.return_value = xml
    monkeypatch.setattr(settings, "lta_renovacion_margen_dias", 90)
    await _ca_vence_en(conn, firma_id, 20)
    _tsa_larga(monkeypatch)

    resumen = await renovacion_lta.renovar_tenant(conn, slug)

    assert resumen["renovadas"] == 1, resumen
    s0, s1 = await _sellos(conn, firma_id)
    assert (s0["ordinal"], s0["revocacion_tsa_previa"]) == (0, None)
    # La TSA nueva (5 años) es autofirmada: sin CA ni revocación que pedir, y así lo deja anotado.
    assert (s1["ordinal"], s1["tsa_revocacion_fuente"], s1["revocacion_tsa_previa"]) == (1, "autofirmada", "autofirmada")
    assert s1["tsa_cadena_not_after"] is None
    payload = await conn.fetchval(
        "SELECT payload::text FROM public.audit_log WHERE tenant_slug = $1 AND action = 'firma.lta_renovada'", slug
    )
    assert "revocacion_tsa_previa" in payload and "vence_at" in payload


async def test_una_ca_vencida_deja_el_eslabon_caducado(tenant_ctx, monkeypatch, storage) -> None:  # noqa: ANN001, F811
    """La hoja (365 d) sigue viva pero su CA ya caducó: renovar ahora sellaría con un hueco en la cadena."""
    slug, conn = tenant_ctx
    _doc, firma_id, _key, xml = await _sembrar(conn, slug, monkeypatch)
    storage.descargar.return_value = xml
    monkeypatch.setattr(settings, "lta_renovacion_margen_dias", 90)
    await _ca_vence_en(conn, firma_id, -1)
    _tsa_larga(monkeypatch)

    resumen = await renovacion_lta.renovar_tenant(conn, slug)

    assert resumen["caducadas"] == 1 and resumen["renovadas"] == 0
    assert [(s["ordinal"], s["estado"]) for s in await _sellos(conn, firma_id)] == [(0, "caducado")]
    assert await _asientos(conn, slug, "firma.lta_cadena_caducada") == 1
    storage.subir.assert_not_awaited()
