# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Huella VIGENTE de lo que sella una cadena de firma (E23, AREA-04-09).

Quién la produce: document (borrador, radicado) y archive (índice del
expediente). Aquí se traducen sus excepciones a las de la firma, que es lo
que el guardián de documento vigente sabe distinguir.

**Fail-closed**: no poder comprobar NO es «no ha cambiado». Si un fallo se
leyera como «sigue igual», bastaría con que el productor fallara para firmar
sobre un texto ya modificado.
"""

from __future__ import annotations

from archive import api as archive_api
from document import api as document_api


class HuellaNoVerificableError(Exception):
    """No se pudo comprobar si el documento sigue siendo el mismo."""


class ObjetoDesaparecidoError(HuellaNoVerificableError):
    """El objeto de la cadena ya no existe."""


class BorradorDesaparecidoError(ObjetoDesaparecidoError):
    """El borrador ya no existe. Subclase a propósito: el efecto es el mismo
    (denegar), pero el mensaje puede ser más útil que «no se pudo comprobar»."""


class HuellaSinContenidoError(HuellaNoVerificableError):
    """El objeto existe pero no tiene contenido que sellar (radicado sin PDF,
    expediente sin índice, anexo sin huella).

    Subclase de ``HuellaNoVerificableError`` a propósito: quien no la distinga
    cae al 503 fail-closed, nunca a «sin cambios»."""

    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


async def get_huella_borrador(borrador_id: str, tenant_slug: str) -> dict:
    """Huella del cuerpo y del documento de un borrador (la mitad síncrona de la
    invalidación; la otra es un evento, que puede retrasarse)."""
    try:
        return await document_api.draft_fingerprint(
            tenant_slug=tenant_slug, borrador_id=borrador_id
        )
    except document_api.DraftNotFoundError as exc:
        raise BorradorDesaparecidoError(f"el borrador {borrador_id} ya no existe") from exc
    except document_api.DocumentUnavailableError as exc:
        raise HuellaNoVerificableError("document no respondió") from exc


async def get_huella_radicado(radicado_ref: str, tenant_slug: str) -> dict:
    """SHA-256 del documento principal de un radicado (``huella_sha256``).

    ``radicado_ref``: número o UUID, tal como la cadena guarda ``objeto_id``.
    """
    try:
        return await document_api.radicado_fingerprint(tenant_slug=tenant_slug, ref=radicado_ref)
    except document_api.DocumentNotFoundError as exc:
        raise ObjetoDesaparecidoError("el radicado no existe") from exc
    except document_api.NothingToSealError as exc:
        raise HuellaSinContenidoError(exc.code, f"document: {exc.code}") from exc
    except document_api.DocumentUnavailableError as exc:
        raise HuellaNoVerificableError("document no respondió") from exc


async def get_huella_indice_expediente(expediente_ref: str, tenant_slug: str) -> dict:
    """SHA-256 del índice electrónico vigente de un expediente (código o UUID)."""
    try:
        return await archive_api.index_fingerprint(tenant_slug=tenant_slug, ref=expediente_ref)
    except archive_api.ExpedienteNotFoundError as exc:
        raise ObjetoDesaparecidoError("el expediente no existe") from exc
    except archive_api.IndexNotGeneratedError as exc:
        raise HuellaSinContenidoError(exc.code, f"archive: {exc.code}") from exc
    except archive_api.IndexUnavailableError as exc:
        raise HuellaNoVerificableError("archive no respondió") from exc
