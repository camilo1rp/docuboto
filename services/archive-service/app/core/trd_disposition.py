# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Gate D5 (ADR-025, Increment 2) — estados que gobiernan la ELIMINACIÓN
EFECTIVA (disposición final) de expedientes/documentos.

Distinto del gate de CIERRE (`ArchiveService.close_expediente`, que admite
`convalidada` **o** `migrada`, D5/D6): la eliminación efectiva exige que el
instrumento invocado haya sido, verificablemente, CONVALIDADO en algún
momento -- sin excepción para `migrada`. `migrada` puede GOBERNAR un cierre
-- declarándolo en el snapshot -- pero el sistema no puede afirmar que un
instrumento que NUNCA se sometió al circuito de convalidación (Ac. AGN
001/2024, que compila el 004/2019) autorice destruir un documento público
(RF-FIR-15).

Corrección [Alta] del dictamen de `archival-compliance-auditor` sobre este
mismo módulo: el criterio original (`serie.estado == "convalidada"`, leyendo
la fila VIVA) era el CONTRARIO del que exige el ADR. Un expediente que se
elimine años después de cerrado queda amarrado a la versión que gobernó su
CIERRE (D3/D4, congelación); si esa versión fue posteriormente derogada
(porque el instrumento se actualizó), el criterio viejo la habría rechazado
y BLOQUEADO una eliminación legítima, conservando indefinidamente
documentación cuya disposición final era eliminar -- incumplimiento
simétrico al que motivó ADR-025 (Ley 594/2000 arts. 24-26: el instrumento
debe aplicarse). El ADR es explícito: `derogada` SÍ puede gobernar, pero
"solo la ya fijada en snapshots previos" (ver tabla D5).

Por eso este gate ya NO recibe la fila viva de `trd_series`: recibe el
SNAPSHOT CONGELADO que quedó grabado en `expedientes.disposition` al cerrar
(`serie_estado`/`acto_administrativo`, ver `RetentionSchedule` en
`app/schemas/archive.py`) -- es la prueba oponible que se citaría en el acta
de eliminación, no el estado actual (posiblemente distinto) de la fila.

Criterio: `serie_estado == "convalidada"` (el instrumento sigue vigente y
convalidado) OR (`serie_estado == "derogada"` AND `acto_administrativo IS
NOT NULL`) -- gobernó como convalidada y fue derogada DESPUÉS (el CHECK
`trd_series_migrada_sin_acto_check`, migración 037, garantiza que una fila
`migrada` derogada NUNCA tiene acto administrativo, así que
`acto_administrativo IS NOT NULL` discrimina de forma fiable "fue
convalidada y luego derogada" de "era migrada y se derogó directamente sin
pasar jamás por el circuito"). Cualquier otro `serie_estado`
(`borrador`/`aprobada`/`migrada`/`derogada` sin acto) deniega.

Hoy (2026-08) **no existe ningún job que ejecute esa eliminación** -- el
único job de disposición real en este servicio es la renovación de la
retención WORM para series de Conservación Total (ver
`app.core.disposition.CT_DISPOSITION_CODES`, que NO elimina nada, solo
renueva). Este módulo existe deliberadamente SIN caller todavía: la regla se
escribe ahora, junto al resto de los gates de disposición de este paquete
(`app.core.disposition`), para que quien implemente esa ruta en un incremento
futuro no tenga que inventar el criterio -- solo llamar a
`assert_disposicion_final_autorizada` con el snapshot congelado del
expediente ANTES de ejecutar la eliminación. Ver ADR-025 D5 y §"Orden de
incrementos" (Increment 2); el residual queda además registrado en
`documentos/specDrive/trazabilidad.md` ligado a la épica de eliminación
efectiva, para que un gate sin llamante no se pierda de vista."""

from __future__ import annotations

from fastapi import HTTPException, status

# Fuente única (mismo criterio que `CT_DISPOSITION_CODES`): el snapshot
# congelado gobierna la eliminación efectiva SOLO si describe un instrumento
# que fue convalidado -- vigente hoy (`convalidada`) o derogado DESPUÉS de
# haber sido convalidado (`derogada` + acto). Cualquier otro valor de
# `serie_estado` -- incluido `migrada`, que sí gobierna un CIERRE -- deniega.
ESTADOS_QUE_GOBIERNAN_ELIMINACION_EFECTIVA: frozenset[str] = frozenset(
    {"convalidada", "derogada"}
)


def disposicion_final_autorizada(
    *, serie_estado: str, acto_administrativo: str | None
) -> bool:
    """El criterio, en un solo sitio (2026-08-28).

    Lo extrae de `assert_disposicion_final_autorizada` sin cambiarlo, porque
    ahora hay DOS consumidores y responden preguntas distintas: el `assert`
    corta un acto (409), y la cola de vencimientos
    (`GET /trd/disposicion-vencida`) solo necesita **decir** si el snapshot de
    cada expediente gobernaría su eliminación, sin cortar nada. Copiar la
    condición en la consulta habría sido reimplementar lo que se verifica —y
    esa divergencia no fallaría al escribirla, sino el día que alguien cambie
    uno de los dos lados—: la pantalla diría «se puede eliminar» sobre un
    expediente que el gate va a rechazar, o al revés."""
    return serie_estado == "convalidada" or (
        serie_estado == "derogada" and acto_administrativo is not None
    )


def assert_disposicion_final_autorizada(
    *,
    serie_estado: str,
    acto_administrativo: str | None,
    code: str | None = None,
    serie_version: int | None = None,
) -> None:
    """Gate reutilizable (ADR-025 D5, corregido migración 038): `409` salvo
    que el SNAPSHOT CONGELADO (`expedientes.disposition`, no la fila viva de
    `trd_series`) describa un instrumento convalidado -- `serie_estado ==
    "convalidada"`, o `"derogada"` con `acto_administrativo` no nulo (D5:
    "derogada... solo la ya fijada en snapshots previos").

    Llamar ANTES de ejecutar cualquier eliminación física/efectiva de
    expedientes o documentos, pasando el snapshot que quedó congelado al
    CERRAR ese expediente concreto -- hoy sin caller (ver docstring del
    módulo); el job de disposición futuro debe invocar este helper como su
    primer chequeo. `code`/`serie_version` son opcionales, solo para un
    mensaje de error más trazable."""
    if not disposicion_final_autorizada(
        serie_estado=serie_estado, acto_administrativo=acto_administrativo
    ):
        ref = f"'{code}' version {serie_version}" if code else "la serie TRD"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "trd_version_no_convalidada_para_disposicion_final",
                "detail": (
                    f"El snapshot congelado de {ref} tiene serie_estado="
                    f"'{serie_estado}' (acto_administrativo="
                    f"{'presente' if acto_administrativo else 'ausente'}): la "
                    "eliminación efectiva (disposición final) exige que el "
                    "instrumento haya sido convalidado -- vigente como "
                    "'convalidada', o derogado DESPUÉS de haberlo sido -- sin "
                    "excepción para 'migrada' (Ac. AGN 001/2024, que compila "
                    "el 004/2019, ADR-025 D5)."
                ),
                "status": 409,
            },
        )
