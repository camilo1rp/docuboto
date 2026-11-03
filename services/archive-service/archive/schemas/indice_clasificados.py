# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Schemas del índice público de información clasificada y reservada POR SERIE
(Ley 1712/2014 art. 20; Decreto 103/2015 art. 40, hoy DUR 1081/2015, C12, ADR-039).

**Lista blanca por construcción.** Ninguna fila lleva números de radicado, asuntos,
identificadores internos, conteos ni el `fundamento_juridico` libre: son los que
podrían transportar contenido de lo reservado. `tests/test_public_indice.py` fija el
conjunto EXACTO de claves.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class IndiceSeriePublico(BaseModel):
    #: `None` = «Sin serie documental asignada»: radicados clasificados que aún no están
    #: en ningún expediente con serie. Se publica honestamente, no se oculta.
    serie_codigo: str | None
    serie_nombre: str
    subserie_codigo: str | None
    subserie_nombre: str | None
    #: «clasificada» (art. 18, nivel 3) | «reservada» (art. 19, nivel 2).
    categoria: str
    causal_codigo: str
    #: Artículo, literal y descripción del catálogo cerrado; `None` en `por_clasificar`.
    articulo: str | None
    literal: str | None
    causal_descripcion: str
    #: La excepción cubre el documento completo (no hay reserva parcial modelada).
    excepcion: str
    #: Rango de la fecha de GENERACIÓN de la información del grupo (Decreto 103/2015
    #: art. 40). Es la fecha del documento, no la de su calificación.
    fecha_generacion_desde: date | None
    fecha_generacion_hasta: date | None
    fecha_calificacion_desde: date | None
    fecha_calificacion_hasta: date | None
    #: Cita normativa PUBLICABLE (controlada por formato, p. ej. «Ley 1581 de 2012, art. 5»):
    #: «fundamento constitucional o legal» del art. 40. NO es el fundamento libre interno,
    #: que podría transportar contenido y no sale nunca. `None` si no se registró.
    fundamento_norma: str | None
    #: `None` = mientras subsista el derecho que protege (art. 18); en art. 19, el mayor
    #: plazo del grupo.
    plazo_maximo_meses: int | None
    #: «Responsable de la información» (Decreto 103/2015 art. 40): la(s) dependencia(s)
    #: productora(s) de la serie en la TRD (no la del radicado).
    responsable_informacion: str | None
