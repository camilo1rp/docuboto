# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`_causa_remota`: rescatar la explicación de un 5xx de signature-service.

Es la pieza de la que depende que el mensaje de error diga algo o no diga nada.
Un 5xx puede llegar en tres formas muy distintas —el sobre del proyecto, el
`{"detail": "Internal Server Error"}` que sirve FastAPI ante una excepción no
capturada, o HTML de un proxy— y las tres tienen que producir texto legible,
porque la alternativa que había era acusar al servicio equivocado.
"""

from __future__ import annotations

import httpx

from app.clients.signature_client import _causa_remota


def _resp(contenido: bytes | str, tipo: str = "application/json") -> httpx.Response:
    return httpx.Response(
        500,
        content=contenido,
        headers={"content-type": tipo},
        request=httpx.Request("POST", "http://signature-service:8008/x"),
    )


def test_sobre_de_error_del_proyecto_da_codigo_y_texto() -> None:
    r = _resp(
        b'{"detail": {"error": "artefacto_no_almacenado", '
        b'"detail": "MinIO rechazo el objeto", "status": 500}}'
    )
    assert _causa_remota(r) == "artefacto_no_almacenado: MinIO rechazo el objeto"


def test_detail_de_texto_plano_de_fastapi_se_conserva() -> None:
    assert _causa_remota(_resp(b'{"detail": "Internal Server Error"}')) == "Internal Server Error"


def test_cuerpo_no_json_cae_al_texto_crudo_acotado() -> None:
    causa = _causa_remota(_resp("<html><body>502 Bad Gateway</body></html>", "text/html"))
    assert "502 Bad Gateway" in causa
    assert len(causa) <= 300


def test_cuerpo_vacio_no_produce_cadena_vacia() -> None:
    # Una causa vacía reintroduce el defecto por otra puerta: el usuario vuelve
    # a quedarse sin saber qué pasó.
    assert _causa_remota(_resp("", "text/html")) == "sin cuerpo"


def test_texto_largo_se_acota() -> None:
    r = _resp(('{"detail": "' + "x" * 900 + '"}').encode())
    assert len(_causa_remota(r)) == 300
