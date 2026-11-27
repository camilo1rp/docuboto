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
procedimiento de eliminación Ac. AGN 004/2013 art. 22). Desde 2026-08-29 son
DOS: el **acta de transferencia** y el **acta de eliminación** (E04/RF-TRD-07),
que hasta entonces era el residual que este mismo docstring declaraba pendiente.

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
#
# `acta_eliminacion` entra aquí y NO en `CONTENT_TIPOS` por el mismo motivo que
# el acta de transferencia, y con más razón: es lo ÚNICO que sobrevive a lo
# eliminado. Su retención es de conservación permanente, así que tratarla como
# contenido bloquearía para siempre la disposición de cualquier expediente que
# compartiera propietario — y, sobre todo, sería el instrumento impidiendo el
# acto que él mismo documenta.
#
# Las tres firmas PERSONALES del acta de transferencia (AREA-07-10, migración 018) son
# del mismo género que el sello institucional que acompañan: evidencia del acto, no
# contenido. Lo mismo las constancias de «firma inobtenible» (migración 019): prueban por qué
# una parte no firmó, sobreviven al contenido y no postergan su disposición.
CONTROL_INSTRUMENT_TIPOS: frozenset[str] = frozenset(
    {
        "acta_transferencia",
        "acta_eliminacion",
        "acta_firma_elabora",
        "acta_firma_entrega",
        "acta_firma_recibe",
        # Constancia de «firma inobtenible» del acta (AREA-07-10, migración 019): el sello
        # institucional de cada una y la firma personal del declarante, un tipo por rol.
        "acta_firma_excepcion_elabora",
        "acta_firma_excepcion_entrega",
        "acta_firma_excepcion_recibe",
        "acta_firma_excepcion_personal_elabora",
        "acta_firma_excepcion_personal_entrega",
        "acta_firma_excepcion_personal_recibe",
        # Revocación de la constancia (FIRMA-2, migración 020): prueba de que la excepción
        # se dejó sin efecto, sellada por la entidad y firmada por quien revoca.
        "acta_firma_revocacion_elabora",
        "acta_firma_revocacion_entrega",
        "acta_firma_revocacion_recibe",
        "acta_firma_revocacion_personal_elabora",
        "acta_firma_revocacion_personal_entrega",
        "acta_firma_revocacion_personal_recibe",
        # Sobre de revelación del fundamento (FIRMA-2 ronda 3, migración 021): el texto del
        # fundamento y su sal, sellados por la entidad; uno por constancia y uno por revocación.
        # Instrumento de control: prueba del acto, no contenido del expediente.
        "acta_firma_excepcion_sobre_elabora",
        "acta_firma_excepcion_sobre_entrega",
        "acta_firma_excepcion_sobre_recibe",
        "acta_firma_revocacion_sobre_elabora",
        "acta_firma_revocacion_sobre_entrega",
        "acta_firma_revocacion_sobre_recibe",
    }
)

# Tipos con N artefactos por (expediente, tipo) (FIRMA-2, ADR-030 addendum): la constancia de
# «firma inobtenible» se puede re-declarar tras revocarla, así que un mismo rol acumula una
# cadena de constancias y revocaciones. Su identidad en el almacén es (expediente, tipo, file_id):
# buscar solo por (expediente, tipo) y quedarse con la más reciente haría «idempotente» a la
# segunda constancia (la dejaría SIN Object-Lock) y la renovación extendería solo la última.
MULTI_ARTEFACTO_TIPOS: frozenset[str] = frozenset(
    f"acta_firma_{acto}_{variante}{rol}"
    for acto in ("excepcion", "revocacion")
    for variante in ("", "personal_", "sobre_")
    for rol in ("elabora", "entrega", "recibe")
)

# CONTENIDO documental del expediente: bloquea la purga mientras esté bajo
# retención. El índice electrónico ancla su retención en `closed_at`
# (co-expira con el contenido), por lo que permanecer en el set bloqueante es
# dirección segura y NO reintroduce la postergación que el remedio corrige.
#
# `firma_lta_renovada` (B10 §8, migración 022): el XML de una firma XAdES-LTA con un
# ArchiveTimeStamp renovado. Hereda el `retain_until`, el modo y el legal hold del índice al
# que sustituye, así que co-expira con él; clasificarlo como CONTENIDO (y no como instrumento de
# control) es la dirección segura: mientras viva, bloquea la disposición igual que el original.
CONTENT_TIPOS: frozenset[str] = frozenset({"indice", "aip", "firma_lta_renovada"})

# Universo COMPLETO de `tipo` válidos en `preservacion_worm_objeto`. DEBE
# coincidir con el CHECK `preservacion_worm_objeto_tipo_check` (migración
# tenant 010); `tests/test_preservacion_tipos_guard.py` lo verifica parseando
# la migración → un `tipo` nuevo añadido al CHECK sin clasificarlo aquí
# (contenido vs control) rompe CI. La clasificación de todo `tipo` es
# EXHAUSTIVA y DISJUNTA (fail-closed: lo no clasificado nunca se excluye del
# gate, pero el guard fuerza a decidir explícitamente).
ALL_WORM_TIPOS: frozenset[str] = CONTENT_TIPOS | CONTROL_INSTRUMENT_TIPOS
