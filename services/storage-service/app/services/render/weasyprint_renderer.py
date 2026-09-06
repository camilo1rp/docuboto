# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Renderizador HTML → PDF/A con WeasyPrint (E23, RF-PRO-03/04)."""

from __future__ import annotations

import asyncio
import html
import logging
from typing import Any

from app.services.render.types import RenderResult

logger = logging.getLogger(__name__)

#: Perfiles que WeasyPrint sabe producir. Las variantes `a` (accesible) exigen
#: texto etiquetado que una conversión automática no puede prometer, así que no
#: se ofrecen: prometer conformidad que no se comprueba es peor que no ofrecerla.
_PERFILES = ("pdf/a-2b", "pdf/a-3b")

#: Plantilla de página: membrete, márgenes y pie (RF-PRO-04; Ac. AGN 060/2001
#: art. 10; GTC 185). Vive aquí y NO en el editor a propósito (R-1): el
#: funcionario escribe el cuerpo, y la identificación de la entidad no puede
#: quedar a merced de que se acuerde de ponerla.
_CSS_PAGINA = """
@page {{
    size: {tamano};
    margin: {margen_sup} {margen_der} {margen_inf} {margen_izq};
    @top-center {{ content: element(membrete); }}
    @bottom-center {{
        content: "{pie}";
        font-size: 8pt;
        color: #444;
    }}
    @bottom-right {{
        content: "Pagina " counter(page) " de " counter(pages);
        font-size: 8pt;
        color: #444;
    }}
}}
#op-membrete {{ position: running(membrete); }}
body {{ font-family: "DejaVu Sans", sans-serif; font-size: 11pt; line-height: 1.4; }}
.op-encabezado {{ margin-bottom: 1.2em; }}
.op-encabezado .op-radicado {{ font-weight: bold; }}
"""


def _fallido(perfil: str, motivo: str, **extra: Any) -> RenderResult:
    return RenderResult(
        outcome="fallido",
        formato=perfil.upper(),
        herramienta="weasyprint",
        detalle={"motivo": motivo, **extra},
    )


def _documento(cuerpo_html: str, pagina: dict[str, Any]) -> str:
    """Envuelve el cuerpo en la estructura de página.

    Todo lo que viene de `pagina` se ESCAPA: son datos de la entidad y del
    radicado, no marcado. El cuerpo NO se escapa —es HTML a propósito— pero
    llega ya saneado por el proxy BFF, que es donde vive esa defensa.
    """

    def esc(clave: str) -> str:
        valor = pagina.get(clave)
        return html.escape("" if valor is None else str(valor))

    membrete = (
        f'<div id="op-membrete">'
        f"<strong>{esc('entidad')}</strong><br>"
        f"{esc('dependencia')}"
        f"</div>"
    )
    encabezado = (
        f'<div class="op-encabezado">'
        f'<div class="op-radicado">{esc("radicado")}</div>'
        f"<div>{esc('fecha')}</div>"
        f"</div>"
    )
    return (
        "<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'>"
        f"<title>{esc('radicado') or esc('asunto')}</title>"
        "</head><body>"
        f"{membrete}{encabezado}{cuerpo_html}"
        "</body></html>"
    )


def _sin_red(url: str, *args: Any, **kwargs: Any) -> Any:
    """Corta TODA resolución de URL del renderizador. Solo admite `data:`.

    WeasyPrint, sin `url_fetcher` propio, resuelve lo que encuentre en el
    marcado —`http(s)://`, `file://`, `//host`— al pintar la página. El
    renderizador corre dentro de la red de contenedores y es el proceso que
    custodia las credenciales de MinIO, así que un `<img src>` en el cuerpo del
    oficio bastaba para hacerle peticiones a servicios internos o al servicio de
    metadatos de la nube, y para incrustar un fichero local en el PDF oficial.

    El cuerpo venía saneado, sí, pero **en otro proceso**: ese saneamiento vive
    en el proxy de SvelteKit (`frontend/src/lib/server/sanitizeHtml.js`), y el
    gateway enruta `PATCH /api/v1/borradores/{id}` directo a document-service,
    que acepta el campo `cuerpo` sin tocarlo. Quien llama a la API no pasa por
    el navegador. Es lo mismo que ya dejó escrito `borrador_service.py` sobre la
    aprobación: un control que vive solo en el cliente no es un control.

    Se corta aquí, en el renderizador, y no saneando mejor aguas arriba, porque
    esta es la única capa por la que pasan TODAS las rutas de render: la
    previsualización, la producción del oficio y cualquier llamante futuro. Un
    filtro de marcado es una lista de lo prohibido y siempre se queda corta;
    esto es una lista de lo admitido.

    `data:` se conserva porque es la vía por la que se incrustan logos y firmas
    ya embebidos en el propio documento, que no salen a la red.
    """
    if url.startswith("data:"):
        from weasyprint.urls import default_url_fetcher  # noqa: PLC0415

        # Se reenvían los argumentos tal cual: la firma del fetcher la fija
        # WeasyPrint (hoy `timeout`, `ssl_context`, `http_headers`...) y fijarla
        # aquí a mano rompería el render en la próxima versión que añada uno.
        return default_url_fetcher(url, *args, **kwargs)

    raise ValueError(
        f"El renderizador no resuelve recursos externos (se pidió: {url[:80]!r}). "
        "Incrusta la imagen en el propio documento con una URL 'data:'."
    )


class WeasyPrintRenderer:
    """Contrato `DocumentRenderer`: nunca lanza."""

    def __init__(self, *, timeout_seconds: int = 60, max_concurrency: int = 2) -> None:
        self._timeout = timeout_seconds
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def render(
        self, cuerpo_html: str, *, pagina: dict[str, Any], perfil: str
    ) -> RenderResult:
        perfil_norm = (perfil or "").lower()
        try:
            if perfil_norm not in _PERFILES:
                return _fallido(
                    perfil,
                    f"perfil {perfil!r} no producible por WeasyPrint ({list(_PERFILES)}); "
                    "las variantes 'a' exigen texto etiquetado que un render "
                    "automático no puede prometer",
                )
            async with self._semaphore:
                return await asyncio.wait_for(
                    asyncio.to_thread(self._render_sincrono, cuerpo_html, pagina, perfil_norm),
                    timeout=self._timeout,
                )
        except TimeoutError:
            return _fallido(perfil, f"el render excedio {self._timeout} s")
        except Exception as exc:  # noqa: BLE001 — contrato: NUNCA lanza
            logger.warning("WeasyPrintRenderer: fallo inesperado: %s", exc)
            return _fallido(perfil, f"excepcion inesperada en el renderizador: {exc}")

    def _render_sincrono(
        self, cuerpo_html: str, pagina: dict[str, Any], perfil: str
    ) -> RenderResult:
        # Import diferido: WeasyPrint carga librerías nativas (Pango, Cairo) al
        # importarse. Hacerlo aquí y no arriba deja que el servicio ARRANQUE en
        # un despliegue sin ellas —la factory ya lo declara `None`— en vez de
        # tumbar el proceso entero por una capacidad opcional.
        from weasyprint import CSS, HTML  # noqa: PLC0415

        css = CSS(
            string=_CSS_PAGINA.format(
                tamano=pagina.get("tamano", "Letter"),
                margen_sup=pagina.get("margen_sup", "3cm"),
                margen_der=pagina.get("margen_der", "2.5cm"),
                margen_inf=pagina.get("margen_inf", "2.5cm"),
                margen_izq=pagina.get("margen_izq", "3cm"),
                pie=html.escape(str(pagina.get("pie", ""))).replace('"', "'"),
            )
        )
        documento = HTML(string=_documento(cuerpo_html, pagina), url_fetcher=_sin_red)
        contenido = documento.write_pdf(stylesheets=[css], pdf_variant=perfil)
        if not contenido:
            return _fallido(perfil, "el renderizador devolvio un documento vacio")
        return RenderResult(
            outcome="generado",
            formato=perfil.upper(),
            herramienta="weasyprint",
            contenido=contenido,
            detalle={"bytes": len(contenido)},
        )
