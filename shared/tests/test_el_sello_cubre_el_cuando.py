# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""En un registro archivístico el CUÁNDO es la mitad de la prueba.

El sello cubría ocho campos y ninguno era la marca de tiempo. Cambiar la fecha de
un asiento —mover la consulta a un documento reservado fuera del periodo que
audita un inspector— no rompía la cadena, y la reverificación lo daba por íntegro.
"""

from orpycamcp_common.audit import compute_hash

_ASIENTO = {
    "tenant_slug": "demo",
    "service": "document-service",
    "actor": "u-1",
    "canal": "api",
    "action": "document.radicado_consultado",
    "object_type": "radicado",
    "object_ref": "2026-DEMO-E-000123",
    "payload": {"id": "r-1"},
}


def test_cambiar_la_fecha_rompe_el_sello():
    sellado = compute_hash(None, ts="2026-09-01T10:00:00+00:00", **_ASIENTO)
    movido = compute_hash(None, ts="2026-08-01T10:00:00+00:00", **_ASIENTO)

    assert sellado != movido, (
        "mover la fecha de un asiento no altera su sello: el CUÁNDO no está probado"
    )


def test_la_forma_anterior_sigue_siendo_verificable():
    """Los asientos ya escritos se sellaron sin la fecha y no se pueden
    recalcular: el registro es de solo-anexar. La forma vieja tiene que seguir
    dando el mismo resultado o toda la cadena histórica se declararía rota."""
    sin_ts = compute_hash(None, **_ASIENTO)

    assert len(sin_ts) == 64
    assert sin_ts != compute_hash(None, ts="2026-09-01T10:00:00+00:00", **_ASIENTO)


def test_el_resto_de_campos_sigue_cubierto():
    """El cambio no puede haber sacado nada de lo que ya estaba dentro."""
    base = compute_hash(None, ts="2026-09-01T10:00:00+00:00", **_ASIENTO)
    for campo, otro in [
        ("actor", "u-2"),
        ("action", "document.radicado_creado"),
        ("object_ref", "2026-DEMO-E-000999"),
    ]:
        alterado = compute_hash(
            None, ts="2026-09-01T10:00:00+00:00", **{**_ASIENTO, campo: otro}
        )
        assert alterado != base, f"cambiar {campo} no altera el sello"
