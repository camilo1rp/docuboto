# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate D5 (ADR-025, Increment 2): `assert_disposicion_final_autorizada`.

Reescrito íntegro tras el hallazgo [Alta] del dictamen de
`archival-compliance-auditor`: el criterio ORIGINAL (`serie.estado ==
"convalidada"` sobre la fila VIVA) era el CONTRARIO del que exige el ADR --
bloqueaba eliminaciones legítimas de expedientes cuyo instrumento gobernante
fue convalidado y LUEGO derogado (D5 dice explícitamente que `derogada`
gobierna "solo la ya fijada en snapshots previos"). El gate ahora recibe el
SNAPSHOT CONGELADO (`serie_estado`/`acto_administrativo`, como quedan en
`expedientes.disposition`), no la fila viva -- estos tests lo ejercitan
DIRECTAMENTE (sin caller todavía en producción, ver docstring del módulo)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.trd_disposition import (
    ESTADOS_QUE_GOBIERNAN_ELIMINACION_EFECTIVA,
    assert_disposicion_final_autorizada,
)


def test_estados_que_gobiernan_eliminacion_efectiva_incluye_derogada_con_acto() -> None:
    """D5, corregido: `convalidada` Y `derogada` (esta última condicionada a
    tener acto -- el discriminante real está en la función, no solo en el
    frozenset)."""
    assert ESTADOS_QUE_GOBIERNAN_ELIMINACION_EFECTIVA == frozenset({"convalidada", "derogada"})


def test_convalidada_pasa_el_gate() -> None:
    # No debe lanzar: el instrumento sigue vigente y convalidado.
    assert_disposicion_final_autorizada(
        serie_estado="convalidada", acto_administrativo="Resolución 0123 de 2026"
    )


def test_derogada_con_acto_pasa_el_gate() -> None:
    """El caso CENTRAL que corrige el hallazgo: un expediente cerrado bajo
    una version que ERA convalidada y que, años después, se derogó (porque
    el instrumento se actualizó) sigue teniendo un fundamento oponible para
    su eliminación -- D5: "derogada... solo la ya fijada en snapshots
    previos". El criterio viejo (estado ACTUAL == 'convalidada') habría
    bloqueado esto y conservado indefinidamente documentación cuya
    disposición final era eliminar."""
    assert_disposicion_final_autorizada(
        serie_estado="derogada", acto_administrativo="Resolución 0123 de 2026"
    )


def test_derogada_sin_acto_rechazada() -> None:
    """Distingue "convalidada y luego derogada" (pasa) de "migrada y
    derogada directamente, sin acto jamás" (rechaza) -- el CHECK
    `trd_series_migrada_sin_acto_check` (migración 037) garantiza que este
    segundo caso SIEMPRE tiene `acto_administrativo IS NULL`, así que el
    discriminante es fiable."""
    with pytest.raises(HTTPException) as exc_info:
        assert_disposicion_final_autorizada(serie_estado="derogada", acto_administrativo=None)
    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.detail["error"] == "trd_version_no_convalidada_para_disposicion_final"
    )


@pytest.mark.parametrize("serie_estado", ["borrador", "aprobada", "migrada"])
def test_estados_no_convalidables_rechazados(serie_estado: str) -> None:
    """`migrada` es el caso que importa probar explícitamente: gobierna un
    CIERRE (declarándolo) pero NUNCA la eliminación efectiva -- es la
    distinción central de D5. `borrador`/`aprobada` nunca gobiernan nada."""
    with pytest.raises(HTTPException) as exc_info:
        assert_disposicion_final_autorizada(
            serie_estado=serie_estado, acto_administrativo=None
        )
    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.detail["error"] == "trd_version_no_convalidada_para_disposicion_final"
    )


def test_migrada_con_acto_igualmente_rechazada() -> None:
    """Caso adversarial: el CHECK de BD impide que `migrada` tenga acto en
    la fila VIVA, pero este gate no debe confiar ciegamente en eso -- si
    algún snapshot histórico llegara con `serie_estado='migrada'` y un
    `acto_administrativo` no nulo (dato corrupto o de un snapshot anterior a
    esa garantía), el criterio exige `serie_estado` en {'convalidada',
    'derogada'} de todas formas; 'migrada' nunca pasa, tenga acto o no."""
    with pytest.raises(HTTPException):
        assert_disposicion_final_autorizada(
            serie_estado="migrada", acto_administrativo="Resolución fantasma"
        )


def test_mensaje_incluye_referencia_cuando_se_provee() -> None:
    with pytest.raises(HTTPException) as exc_info:
        assert_disposicion_final_autorizada(
            serie_estado="borrador",
            acto_administrativo=None,
            code="100-25",
            serie_version=3,
        )
    assert "100-25" in exc_info.value.detail["detail"]
    assert "3" in exc_info.value.detail["detail"]
