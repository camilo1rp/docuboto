# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Generación del documento de salida (E23 Inc.2, RF-PRO-03/04).

Los tests de contenido corren contra el renderizador **REAL**, no contra un
mock. Un mock del renderizador es una afirmación sobre él, no una comprobación:
en este proyecto un mock que inventaba una clave dejó dos tests en verde sobre
un campo que nunca existió.
"""

from __future__ import annotations

import re
import zlib
from types import SimpleNamespace

import pytest

from app.services.render.factory import get_document_renderer
from app.services.render.weasyprint_renderer import WeasyPrintRenderer
from tests.conftest import INTERNAL_HEADERS, TENANT_HEADERS

PAGINA = {
    "entidad": "Instituto de Prueba",
    "dependencia": "Oficina Jurídica",
    "radicado": "2026-DEMO-S-000007",
    "fecha": "1 de septiembre de 2026",
    "pie": "Calle 1 No. 2-3, Bogota D.C.",
}


def _texto(pdf: bytes) -> str:
    """El texto visible del PDF, extraído de verdad."""
    from io import BytesIO

    from pypdf import PdfReader

    lector = PdfReader(BytesIO(pdf))
    return "\n".join(p.extract_text() or "" for p in lector.pages)


def _aplanar(pdf: bytes) -> bytes:
    """Todo el PDF con sus flujos descomprimidos.

    Un PDF 1.7 mete los objetos en *object streams* comprimidos, así que buscar
    una clave en los bytes crudos da un falso negativo — me pasó al escribir
    esto: `OutputIntent` y `FontFile` parecían ausentes y estaban los dos.
    """
    todo = pdf
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        datos = m.group(1)
        try:
            datos = zlib.decompress(datos)
        except zlib.error:
            pass
        todo += datos
    return todo


class TestFactoria:
    def test_sin_motor_declarado_no_hay_renderizador(self) -> None:
        # Y esto es lo correcto: sin herramienta se responde 503, no se entrega
        # un PDF vacío. No hay stub — tendría que inventarse el documento.
        assert get_document_renderer(SimpleNamespace(render_engine="none")) is None

    def test_un_motor_desconocido_no_se_adivina(self) -> None:
        assert get_document_renderer(SimpleNamespace(render_engine="lo_que_sea")) is None

    def test_sin_el_atributo_tampoco_revienta(self) -> None:
        # Los `settings`-like de despliegues anteriores a este incremento no
        # declaran el campo: eso debe resolver a «no hay renderizador», no a un
        # AttributeError que tumbe el arranque.
        assert get_document_renderer(SimpleNamespace()) is None

    def test_weasyprint_se_resuelve_cuando_esta_instalado(self) -> None:
        renderer = get_document_renderer(SimpleNamespace(render_engine="weasyprint"))
        assert isinstance(renderer, WeasyPrintRenderer)


class TestRenderReal:
    @pytest.mark.asyncio
    async def test_produce_un_pdf(self) -> None:
        r = await WeasyPrintRenderer().render(
            "<p>Cordial saludo.</p>", pagina=PAGINA, perfil="pdf/a-2b"
        )
        assert r.outcome == "generado", r.detalle
        assert r.contenido is not None
        assert r.contenido.startswith(b"%PDF-")
        assert r.formato == "PDF/A-2B"

    @pytest.mark.asyncio
    async def test_el_documento_nace_en_formato_de_conservacion(self) -> None:
        """D-2: PDF/A directo, no PDF más una migración posterior.

        Las tres marcas que separan «un PDF que dice ser PDF/A» de uno que lo
        es: la declaración XMP, el intento de salida con su perfil de color y
        las fuentes incrustadas. Declarar conformidad sin las otras dos es peor
        que no declararla: el documento afirma algo que un validador desmiente.
        """
        r = await WeasyPrintRenderer().render("<p>Hola</p>", pagina=PAGINA, perfil="pdf/a-2b")
        plano = _aplanar(r.contenido or b"")
        assert b"pdfaid:part" in plano
        assert b"OutputIntent" in plano
        assert b"ICCBased" in plano
        assert b"FontFile" in plano

    @pytest.mark.asyncio
    async def test_el_membrete_y_el_numero_van_en_el_documento(self) -> None:
        """RF-PRO-04 (Ac. AGN 060/2001 art. 10; GTC 185).

        La identificación de la entidad, la dependencia productora y el número
        los pone la PLANTILLA DE PÁGINA, no el editor: no pueden quedar a merced
        de que el funcionario se acuerde de escribirlos (R-1).

        Se comprueba EXTRAYENDO el texto, no buscándolo en los bytes: el texto
        va codificado con la fuente subconjuntada y una búsqueda literal daría
        un falso negativo — que es exactamente lo que me pasó al escribir esto.
        """
        r = await WeasyPrintRenderer().render("<p>Cuerpo del oficio.</p>", pagina=PAGINA, perfil="pdf/a-2b")
        texto = _texto(r.contenido or b"")
        for esperado in (
            "Instituto de Prueba",
            "Oficina Jurídica",
            "2026-DEMO-S-000007",
            "1 de septiembre de 2026",
            "Cuerpo del oficio.",
            "Calle 1 No. 2-3",  # el pie
        ):
            assert esperado in texto, f"falta {esperado!r} en:\n{texto}"

    @pytest.mark.asyncio
    async def test_el_pie_numera_las_paginas(self) -> None:
        # Un oficio de varias hojas sin «Pagina N de M» no permite saber si
        # llegó completo, que es justo lo que una constancia de envío necesita.
        cuerpo = "<p>Parrafo.</p>" * 400
        r = await WeasyPrintRenderer().render(cuerpo, pagina=PAGINA, perfil="pdf/a-2b")
        texto = _texto(r.contenido or b"")
        assert "Pagina 1 de" in texto

    @pytest.mark.asyncio
    async def test_lo_que_viene_del_contexto_se_escapa(self) -> None:
        """Los datos de la entidad y del radicado son DATO, no marcado.

        El cuerpo sí es HTML a propósito (llega ya saneado por el proxy BFF),
        pero un nombre de dependencia con `<` no puede abrir una etiqueta.
        """
        pagina = {**PAGINA, "dependencia": "<b>Juridica</b>"}
        r = await WeasyPrintRenderer().render("<p>x</p>", pagina=pagina, perfil="pdf/a-2b")
        assert r.outcome == "generado"
        # Se ve el marcado como TEXTO, no interpretado: si `<b>` se hubiera
        # tratado como etiqueta, el texto extraído diría solo «Juridica».
        assert "<b>Juridica</b>" in _texto(r.contenido or b"")

    @pytest.mark.asyncio
    async def test_un_perfil_no_producible_falla_declarandolo(self) -> None:
        # `pdf/a-2a` exige texto etiquetado que un render automático no puede
        # prometer. Prometer conformidad que no se comprueba es peor que no
        # ofrecerla, así que se rechaza en vez de generar y cruzar los dedos.
        r = await WeasyPrintRenderer().render("<p>x</p>", pagina=PAGINA, perfil="pdf/a-2a")
        assert r.outcome == "fallido"
        assert r.contenido is None, "un resultado fallido NUNCA lleva bytes dentro"
        assert "no producible" in r.detalle["motivo"]

    @pytest.mark.asyncio
    async def test_el_contrato_es_no_lanzar_nunca(self) -> None:
        # HTML roto: el renderizador devuelve un resultado, no una excepción que
        # tumbe la radicación a medias.
        r = await WeasyPrintRenderer().render(
            "<p>sin cerrar <div", pagina=PAGINA, perfil="pdf/a-2b"
        )
        assert r.outcome in ("generado", "fallido")


class TestConformidadReal:
    """La única prueba que de verdad dice que lo generado es de archivo.

    Corre **solo donde veraPDF existe** (imagen `production`); en la imagen de
    desarrollo se salta declarándolo. Saltarse un test no es probarlo: por eso
    los de arriba comprueban las tres marcas estructurales, que sí corren
    siempre. Este cierra el círculo cuando la herramienta está.
    """

    @pytest.mark.asyncio
    async def test_veraPDF_valida_el_documento_generado(self, tmp_path) -> None:
        import os

        binario = os.environ.get("VERAPDF_BINARY", "/opt/verapdf/verapdf")
        if not (os.path.isfile(binario) and os.access(binario, os.X_OK)):
            pytest.skip(f"veraPDF no disponible en {binario} (imagen de desarrollo)")

        from app.services.pdfa.subprocess_verapdf import SubprocessVeraPdfValidator

        r = await WeasyPrintRenderer().render(
            "<p>Cordial saludo.</p>", pagina=PAGINA, perfil="pdf/a-2b"
        )
        assert r.contenido is not None
        destino = tmp_path / "salida.pdf"
        destino.write_bytes(r.contenido)

        validador = SubprocessVeraPdfValidator(
            binary=binario, timeout_seconds=180, max_concurrency=1, jvm_max_heap_mb=512
        )
        resultado = await validador.validate(str(destino), profile="2b")
        assert resultado.outcome == "conformant", resultado


class TestEndpointSellado:
    """`POST /api/v1/render/documento-internal` — puerta y degradación honesta."""

    @pytest.mark.asyncio
    async def test_sin_token_interno_no_se_renderiza(self, client) -> None:
        # Un endpoint inter-servicio se SELLA con token, no se gatea por
        # permiso: el único llamante es document-service, que ya autorizó al
        # funcionario antes de componer el cuerpo.
        resp = await client.post(
            "/api/v1/render/documento-internal",
            json={"cuerpo_html": "<p>x</p>", "pagina": {}},
            headers=TENANT_HEADERS,
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_sin_renderizador_responde_503_y_lo_dice(self, client, monkeypatch) -> None:
        # Lo importante no es el 503: es que NO se entregue un documento vacío.
        # Un oficio que la entidad cree haber producido y no existe es peor que
        # un error visible, porque el fallo aparece cuando el ciudadano reclama.
        from app.routers import render as router_render

        monkeypatch.setattr(router_render, "get_document_renderer", lambda _s: None)
        resp = await client.post(
            "/api/v1/render/documento-internal",
            json={"cuerpo_html": "<p>x</p>", "pagina": {}},
            headers=INTERNAL_HEADERS,
        )
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "render_no_disponible"

    @pytest.mark.asyncio
    async def test_con_renderizador_devuelve_el_pdf(self, client, monkeypatch) -> None:
        import base64

        from app.routers import render as router_render

        monkeypatch.setattr(
            router_render, "get_document_renderer", lambda _s: WeasyPrintRenderer()
        )
        resp = await client.post(
            "/api/v1/render/documento-internal",
            json={"cuerpo_html": "<p>Cuerpo</p>", "pagina": PAGINA, "perfil": "pdf/a-2b"},
            headers=INTERNAL_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()
        assert cuerpo["formato"] == "PDF/A-2B"
        assert base64.b64decode(cuerpo["contenido_b64"]).startswith(b"%PDF-")
        assert cuerpo["bytes"] > 0

    @pytest.mark.asyncio
    async def test_un_render_fallido_no_devuelve_bytes(self, client, monkeypatch) -> None:
        from app.routers import render as router_render

        monkeypatch.setattr(
            router_render, "get_document_renderer", lambda _s: WeasyPrintRenderer()
        )
        resp = await client.post(
            "/api/v1/render/documento-internal",
            json={"cuerpo_html": "<p>x</p>", "pagina": PAGINA, "perfil": "pdf/a-2a"},
            headers=INTERNAL_HEADERS,
        )
        assert resp.status_code == 422
        assert "contenido_b64" not in resp.json()
