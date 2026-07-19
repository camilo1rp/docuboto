# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Taxonomía de ``tipo`` para ``preservacion_worm_objeto`` (E10) — fuente única.

Distingue CONTENIDO (bloquea la purga de disposición mientras esté bajo
retención) de INSTRUMENTO DE CONTROL (evidencia del acto que sobrevive al
contenido y NO debe postergar su eliminación; Ley 594/2000 art. 26 +
procedimiento de eliminación Ac. AGN 004/2013 art. 22 — el instrumento
modelado hoy es el **acta de transferencia**, no el acta de eliminación del
Comité, que es un residual de conformidad aparte).

Motivo del remedio (observación Media, auditoría archivística E10 Inc.2): el
acta de transferencia ancla su retención en ``firmado_at`` (posterior al
``closed_at`` del índice, a veces años después en transferencias secundarias
firmadas tarde). Si el gate de disposición tratara el acta igual que el
contenido, la eliminación mandada por la TRD quedaría postergada hasta que
expire el acta — incumpliendo Ley 594 arts. 24-26 (ejecutar la disposición en
su tiempo). El acta sigue protegida por su propio Object-Lock COMPLIANCE y
nunca es el ``file_id`` que ``purge_for_disposition`` borra; solo deja de
*bloquear* el contenido de otros objetos del expediente.

Fail-closed: SOLO los tipos listados en ``CONTROL_INSTRUMENT_TIPOS`` se
excluyen del bloqueo por retención. Cualquier ``tipo`` nuevo o no clasificado
sigue bloqueando por defecto (dirección segura).
"""

from __future__ import annotations

# INSTRUMENTOS DE CONTROL: sobreviven al contenido y NO postergan su
# disposición → se excluyen del bloqueo por retención del gate (nunca del
# `legal_hold`, que es incondicional).
CONTROL_INSTRUMENT_TIPOS: frozenset[str] = frozenset({"acta_transferencia"})

# CONTENIDO documental del expediente: bloquea la purga mientras esté bajo
# retención. El índice electrónico ancla su retención en `closed_at`
# (co-expira con el contenido), por lo que permanecer en el set bloqueante es
# dirección segura y NO reintroduce la postergación que el remedio corrige.
CONTENT_TIPOS: frozenset[str] = frozenset({"indice", "aip"})

# Universo COMPLETO de `tipo` válidos en `preservacion_worm_objeto`. DEBE
# coincidir con el CHECK `preservacion_worm_objeto_tipo_check` (migración
# tenant 010); `tests/test_preservacion_tipos_guard.py` lo verifica parseando
# la migración → un `tipo` nuevo añadido al CHECK sin clasificarlo aquí
# (contenido vs control) rompe CI. La clasificación de todo `tipo` es
# EXHAUSTIVA y DISJUNTA (fail-closed: lo no clasificado nunca se excluye del
# gate, pero el guard fuerza a decidir explícitamente).
ALL_WORM_TIPOS: frozenset[str] = CONTENT_TIPOS | CONTROL_INSTRUMENT_TIPOS
