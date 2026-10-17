# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El XML del acta de eliminación documental — RF-TRD-07, fase 4 (E04).

**Función pura y módulo aparte**, igual que `_fuid_to_xml`: lo que se firma
tiene que poder construirse y compararse sin base de datos ni HTTP delante. El
sellado XAdES cubre exactamente esta cadena, y su SHA-256 queda anclado en
`audit_log`; si la serialización viviera enredada con la capa de servicio, el
día que alguien cambiara un orden de campos las actas viejas dejarían de
verificar sin que ningún test lo dijera.

**Se serializa canónicamente (C14N)** por el mismo motivo: dos ejecuciones con
los mismos datos tienen que dar byte a byte el mismo documento, o el SHA no
significa nada.

Lo que el acta debe contener (Ley 594/2000 art. 24; Ac. AGN 001/2024 art.
4.3.2.5): **qué** se elimina —el inventario completo—, **bajo qué instrumento**,
**quién lo autorizó** —acta del Comité y acto administrativo—, **cómo** se
destruye y **desde cuándo** era públicamente conocido, que es lo que acredita
que hubo publicidad previa y ocasión de oponerse.

**El inventario va COMPLETO, sin recortar por clearance.** El acta es la
constancia, no una vista: un acta a la que le faltan los expedientes
clasificados no da fe de lo que se destruyó. Lo que se acota es **quién puede
leerla** — ver `ArchiveService.get_acta_eliminacion`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

NS = "urn:orpycamcp:acta-eliminacion:v1"


def _texto(padre: ET.Element, etiqueta: str, valor: Any) -> None:
    """Un hijo con texto. `None` se escribe como cadena vacía y **el elemento
    sí sale**: un acta en la que un campo ausente y un campo vacío se ven igual
    obliga a adivinar, y la comparación de dos actas dejaría de ser posicional.
    """
    ET.SubElement(padre, f"{{{NS}}}{etiqueta}").text = "" if valor is None else str(valor)


def construir_acta_xml(
    *,
    lote: Any,
    items: list[Any],
    tenant_slug: str,
    metodo_borrado: str,
    observaciones: str | None,
    generado_por: str,
    generado_ts: datetime,
    plazo_efectivo_hasta: Any = None,
    dias_interrumpido: int = 0,
) -> str:
    """El acta de eliminación como XML canónico.

    `generado_ts` se **recibe**, no se toma de `now()` aquí: el mismo instante
    tiene que quedar dentro del documento y en la fila que lo guarda, o el SHA
    anclado en la auditoría no correspondería a lo persistido. Es la misma
    precaución que el acta de transferencia tomó con su `generado`.
    """
    root = ET.Element(f"{{{NS}}}actaEliminacion", attrib={"generado": generado_ts.isoformat()})

    ident = ET.SubElement(root, f"{{{NS}}}identificacion")
    _texto(ident, "entidad", tenant_slug)
    _texto(ident, "loteCodigo", lote["codigo"])
    _texto(ident, "loteId", lote["id"])
    _texto(ident, "generadoPor", generado_por)

    # El fundamento: sin esto el acta sería un inventario, no un acta. Los tres
    # campos del aval son obligatorios desde la fase 3 (CHECK de la 047), así
    # que aquí nunca pueden faltar -- se escriben sin condicional a propósito,
    # para que un acta sin fundamento fuera imposible de construir y no
    # simplemente rara.
    fund = ET.SubElement(root, f"{{{NS}}}fundamento")
    _texto(fund, "actaComite", lote["acta_comite"])
    _texto(fund, "fechaAval", lote["fecha_aval"])
    _texto(fund, "actoAdministrativo", lote["acto_administrativo"])
    _texto(fund, "avaladoPor", lote["avalado_por"])

    # La publicidad previa es parte del fundamento, no un detalle de trámite:
    # es lo que acredita que hubo ocasión de oponerse antes de destruir.
    pub = ET.SubElement(root, f"{{{NS}}}publicidadPrevia")
    _texto(pub, "publicadoDesde", lote["publicado_at"])
    _texto(pub, "plazoOposicionHasta", lote["publicado_hasta"])
    # ADR-035: solo si la entidad dejo de ser operativa dentro del plazo. En el
    # caso normal el acta es byte a byte la de siempre.
    if dias_interrumpido > 0:
        _texto(pub, "plazoOposicionEfectivoHasta", plazo_efectivo_hasta)
        _texto(pub, "diasPlazoInterrumpido", dias_interrumpido)

    ET.SubElement(root, f"{{{NS}}}metodoBorrado").text = metodo_borrado
    _texto(root, "observaciones", observaciones)

    inv = ET.SubElement(
        root, f"{{{NS}}}inventario", attrib={"total": str(len(items))}
    )
    # Orden explícito por código: el inventario se serializa siempre igual
    # aunque la consulta cambie de plan. Sin esto, dos actas del mismo lote
    # podrían diferir en el SHA sin diferir en el contenido.
    for orden, it in enumerate(sorted(items, key=lambda i: i["expediente_code"]), start=1):
        e = ET.SubElement(inv, f"{{{NS}}}expediente", attrib={"orden": str(orden)})
        _texto(e, "codigo", it["expediente_code"])
        _texto(e, "denominacion", it["expediente_nombre"])
        _texto(e, "serie", it["trd_serie_code"])
        _texto(e, "disposicion", it["disposition_code"])
        _texto(e, "fechaVencimiento", it["fecha_vencimiento"])
        _texto(e, "fechaExtremaInicial", it["fecha_extrema_inicial"])
        _texto(e, "fechaExtremaFinal", it["fecha_extrema_final"])
        _texto(e, "nivelSeguridad", it["nivel_seguridad"])
        _texto(e, "radicadosIncluidos", it["radicados_incluidos"])

    return ET.canonicalize(ET.tostring(root, encoding="unicode"))
