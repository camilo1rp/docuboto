# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Traduce una fila de radicado al lote de ingest de PPI.

La lista cerrada es número, fecha, tipo E/S/I, tipo documental, dependencia,
asunto, remitente, vencimiento y estado. El anexo viaja por localizador y solo
si ya tiene SHA-256: no se inventa una huella. La sensibilidad que pone aquí
es un hueco; el punto de paso la pisa con el nivel releído. Los metadatos
aplanados viajan en el lote y el punto de paso los quita si la entidad no
los tiene encendidos.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, date, datetime
from typing import Any

_DIRECCION = {"E": "incoming", "S": "outgoing", "I": "internal"}
_CLAVE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_MIME = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")
_OMITIR = object()
_TOPE = 100


def trozos(lote: dict[str, Any], tope: int = _TOPE) -> list[dict[str, Any]]:
    """Parte el lote en tandas que caben en un ingest (máximo 100 ítems)."""
    items = lote.get("items") or []
    if tope < 1:
        raise ValueError("tope")
    if len(items) <= tope:
        return [lote]
    return [{"items": items[i : i + tope]} for i in range(0, len(items), tope)]


def lote(fila: Any, anexos: Any) -> dict[str, Any]:
    """Arma ``{"items": [record, attachment, ...]}`` desde la fila y sus anexos."""
    record = _record(fila)
    items: list[dict[str, Any]] = [{"type": "record", "record": record}]
    padre = record["external_ref"]
    for anexo in anexos or []:
        pieza = _anexo(anexo, padre)
        if pieza is not None:
            items.append({"type": "attachment", "attachment": pieza})
    return {"items": items}


def _record(fila: Any) -> dict[str, Any]:
    direccion = _DIRECCION.get(str(fila["doc_type"]))
    if direccion is None:
        raise ValueError("direction")
    asunto = str(fila["subject"] or "").strip()
    if not asunto:
        raise ValueError("subject")
    numero = str(fila["tracking_number"] or "").strip()
    if not numero:
        raise ValueError("registration_number")
    record: dict[str, Any] = {
        "external_ref": str(fila["id"]),
        "version": _version(fila["updated_at"]),
        "sensitivity": "public",
        "registration_number": numero[:128],
        "received_at": _instante(fila["registered_at"]),
        "direction": direccion,
        "subject": asunto[:4000],
        "status": str(fila["status"] or "")[:64],
        "closed": str(fila["status"]) == "closed",
    }
    tipo = str(fila["doc_class"] or "").strip()
    if 1 <= len(tipo) <= 128:
        record["document_type"] = tipo
    if fila["dest_dept_code"] is not None:
        unidad = str(fila["dest_dept_code"]).strip()
        if 1 <= len(unidad) <= 128:
            record["unit"] = unidad
    vencimiento = _fecha(fila["due_date"])
    if vencimiento is not None:
        record["due_date"] = vencimiento
    remitente = str(fila["sender_name"] or "").strip()
    if remitente:
        parte: dict[str, Any] = {"role": "sender", "name": remitente[:512]}
        entidad = str(fila["sender_entity"] or "").strip()
        if entidad:
            parte["organization"] = entidad[:512]
        record["parties"] = [parte]
    meta = _metadata(fila["metadata"] if "metadata" in fila.keys() else None)
    if meta:
        record["metadata"] = meta
    return record


def _anexo(anexo: Any, padre: str) -> dict[str, Any] | None:
    huella = str(anexo["sha256"] or "").strip().lower()
    if not _SHA.fullmatch(huella):
        return None
    nombre = str(anexo["filename"] or "").strip()
    if not nombre:
        return None
    mime = str(anexo["mime_type"] or "").strip().lower()
    if not _MIME.fullmatch(mime):
        return None
    try:
        tamano = int(anexo["file_size"])
    except (TypeError, ValueError):
        return None
    if tamano < 0:
        return None
    identificador = str(anexo["id"])
    pieza: dict[str, Any] = {
        "external_ref": identificador,
        "parent_ref": padre,
        "version": huella,
        "sensitivity": "public",
        "filename": nombre[:512],
        "media_type": mime[:255],
        "role": "main" if anexo["es_principal"] else "attachment",
        "size_bytes": tamano,
        "sha256": huella,
        "content": {"mode": "locator", "locator": f"att:{identificador}:{huella}"},
    }
    try:
        folios = int(anexo["folios"])
    except (KeyError, TypeError, ValueError):
        folios = 0
    if folios >= 1:
        pieza["page_count"] = folios
    return pieza


def _version(valor: Any) -> str:
    texto = _instante(valor)
    if len(texto) > 128:
        raise ValueError("version")
    return texto


def _instante(valor: Any) -> str:
    if isinstance(valor, datetime):
        if valor.tzinfo is None:
            valor = valor.replace(tzinfo=UTC)
        return valor.isoformat()
    if isinstance(valor, str) and valor.strip():
        return valor.strip()
    raise ValueError("received_at")


def _fecha(valor: Any) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, str) and valor.strip():
        return valor.strip()[:10]
    return None


def _metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    salida: dict[str, Any] = {}
    for clave in sorted(raw):
        if not isinstance(clave, str) or not _CLAVE.fullmatch(clave):
            continue
        valor = _escalar(raw[clave])
        if valor is _OMITIR:
            continue
        salida[clave] = valor
        if len(salida) == 100:
            break
    return salida


def _escalar(valor: Any) -> Any:
    if isinstance(valor, bool) or valor is None:
        return valor
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float):
        return valor if math.isfinite(valor) else _OMITIR
    if isinstance(valor, str):
        return valor[:2000]
    return _OMITIR
