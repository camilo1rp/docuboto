# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Política de tratamiento de datos personales de la entidad (B2, ADR-031).

La autorización que da quien radica por la ventanilla pública (Ley 1581/2012
art. 12) solo es informada si se le mostró el aviso de privacidad de ESTA
entidad (DUR 1074/2015 art. 2.2.2.25.3.2). La política vive en los parámetros de
la entidad (`sgd_param_admin`, sembrados por la migración 012 de tenant-service),
la sirve este servicio y el servidor la relee al radicar: la versión, la URL y la
huella de la constancia las pone el servidor, nunca el cliente.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

PARAM_VERSION = "politica_tratamiento_version"
PARAM_URL = "politica_tratamiento_url"
PARAM_TEXTO = "politica_tratamiento_texto"

#: Separador de registro: no puede aparecer en el texto de una política escrita
#: por una persona, así que (a, b) y (ab, "") no dan la misma huella.
_SEP = "\x1e"


@dataclass(frozen=True, slots=True)
class Politica:
    version: str
    url: str
    texto: str

    @property
    def publicada(self) -> bool:
        """Hay versión y algo que mostrar (URL o texto): un aviso que no apunta a
        nada no informa."""
        return bool(self.version) and bool(self.url or self.texto)

    @property
    def huella(self) -> str:
        return huella_politica(self.version, self.texto, self.url)


def huella_politica(version: str, texto: str, url: str) -> str:
    return hashlib.sha256(_SEP.join((version, texto, url)).encode()).hexdigest()


def _cadena(valor: Any) -> str:
    return valor.strip() if isinstance(valor, str) else ""


_URL_SEGURA = re.compile(r"^https?://[^\s<>\"']+$", re.IGNORECASE)


def _url_segura(valor: Any) -> str:
    """Solo `http(s)://`: la URL se enlaza en la página pública y un `javascript:`
    o `data:` sería XSS. Cualquier otra cosa se trata como ausente."""
    texto = _cadena(valor)
    return texto if _URL_SEGURA.match(texto) else ""


def politica_de(params: dict[str, Any]) -> Politica:
    """Extrae la política de los parámetros de la entidad (vacía si no hay)."""
    return Politica(
        version=_cadena(params.get(PARAM_VERSION)),
        url=_url_segura(params.get(PARAM_URL)),
        texto=_cadena(params.get(PARAM_TEXTO)),
    )
