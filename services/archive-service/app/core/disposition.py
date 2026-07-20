# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Códigos de disposición final AGN compartidos (E04 TRD + E10 debt:
renovación de retención WORM para series de Conservación Total).

`trd_series.disposition` (columna `VARCHAR(20)`, SIN `CHECK` a nivel BD --
verificado contra `migrations/tenant/001_create_archive.sql`/`006_trd_ccd.sql`)
admite tanto los códigos AGN (``CT``/``E``/``S``/``M``) como alias de texto
libre heredados (``conserve``/``eliminate``/``digitize``/``select``) — ver
``app.services.archive_service._agn_disposition`` (visualización). La
renovación de retención WORM (E10 debt, `app/services/index_service.py`::
`renovar_worm_indice` / `app/services/transferencia_service.py::
renovar_worm_acta`) necesita la MISMA equivalencia para decidir qué series
son PERPETUAS: vive aquí, en `app/core/` (sin dependencias), como fuente
única — ni `archive_service.py` (visualización) ni los repositorios (filtro
SQL del job de reconciliación) deben mantener su propia copia de esta lista,
o divergirían si se agrega un alias nuevo.

Fail-closed por construcción en cada consumidor: SOLO estos códigos habilitan
la renovación perpetua. Una serie con disposición ``E``/``S``/``M`` (o
cualquier código futuro no listado aquí) expira y se dispone legítimamente —
renovarla sería una violación de la disposición documental declarada (Ley
594/2000 arts. 24-26), no un descuido a corregir.

Verificación de completitud (E10 debt, re-chequeo de conformidad): contra
``_DISPOSITION`` (``app/schemas/archive.py``, el Literal completo -- ``CT``/
``E``/``S``/``M`` + sus 4 alias) y ``_AGN_DISPOSITION``
(``app/services/archive_service.py``, la vista AGN), este conjunto captura
TODA la disposición de conservación permanente a nivel de serie: ``CT``/
``conserve`` son los ÚNICOS códigos que ``_AGN_DISPOSITION`` mapea a
``("CT", "Conservación Total")``. ``S``/``select`` (Selección) queda
DELIBERADAMENTE FUERA de este set, aunque produzca una muestra que SÍ termina
en conservación permanente: esa reclasificación ocurre al cabo del proceso de
valoración documental (E04/E12, un acto humano explícito que cambia
``trd_series.disposition``/reclasifica el expediente a una serie CT), no es
una propiedad intrínseca de la serie ``S`` en sí -- mientras una serie siga
declarada ``S``, la muestra AÚN NO valorada debe expirar y disponerse según
lo que decida esa valoración, nunca renovarse por adelantado bajo el supuesto
de que terminará en CT. ``M``/``digitize`` (Microfilmación/Digitalización)
también queda fuera por la misma razón: es un MEDIO de disposición del
soporte físico, no una declaración de conservación permanente del artefacto
WORM que protege este servicio. Si se detecta un código de conservación
perpetua no cubierto aquí, añadirlo a este set (nunca a una copia local) --
el fail-closed actual es seguro contra sobre-fijar un artefacto no-CT, pero
sub-retendría (dejaría vencer) un CT genuino mal etiquetado con ese código.
"""

from __future__ import annotations

CT_DISPOSITION_CODES: frozenset[str] = frozenset({"CT", "conserve"})
