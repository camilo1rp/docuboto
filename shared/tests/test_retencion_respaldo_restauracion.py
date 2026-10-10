# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Respaldo y restauración de la retención por objeto, con `s3api` falso (AREA-08-10, AREA-03-02).

`infra/lib-retencion-s3.sh` es el código que `backup.sh` y `restore.sh` comparten
para guardar y reponer la retención (Object-Lock) y la retención legal. Se prueba
aquí con un `s3api` FALSO definido como función de shell: sin docker ni almacén.

Defectos que fija:

* la restauración terminaba en verde aunque `put-object-retention` fallara
  (`|| true` + contador informativo): ahora devuelve distinto de 0 si queda sin
  reponer una retención vigente o una retención legal activa;
* la retención legal (`LegalHold`) no se guardaba ni se reponía;
* solo se recorría la versión actual (`list-objects-v2`);
* tras restaurar, el `version_id` de la BD ya no corresponde a ningún objeto y no
  había mapa viejo -> nuevo para el acta de ADR-027.

Y que `backup.sh` no ancle la cadena de auditoría sin verificarla (AREA-03-02) y
que `restore.sh` use el mismo módulo en vez de un heredoc.

Mutación: volver a `|| true` sin recuento en `reponer_retencion` -> el test del
fallo único sale 0 (rojo).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests._compose import RAIZ

LIB = RAIZ / "infra" / "lib-retencion-s3.sh"
FUTURO = "2099-01-01T00:00:00Z"
PASADO = "2001-01-01T00:00:00Z"

_FALSO = r'''
LLAMADAS="$T/llamadas.log"; : > "$LLAMADAS"
s3api() {
    echo "$*" >> "$LLAMADAS"
    case "$1" in
        put-object-retention)
            for f in $FALLAN; do case "$*" in *"--key $f "*) return 1;; esac; done; return 0 ;;
        put-object-legal-hold) [ -n "${FALLA_HOLD:-}" ] && return 1; return 0 ;;
        head-object) echo "NUEVA-VERSION"; return 0 ;;
        list-object-versions)
            case "$*" in
                *DeleteMarkers*) [ -e "$T/marcadores.txt" ] && cat "$T/marcadores.txt"; return 0 ;;
            esac
            cat "$T/versiones.txt"; return 0 ;;
        delete-object) echo "DELETE $*" >> "$T/orden.log"; return 0 ;;
        get-object-retention)
            k=""; v=""
            while [ $# -gt 0 ]; do case "$1" in --key) k="$2";; --version-id) v="$2";; esac; shift; done
            if [ -e "$T/err-$k" ]; then echo "An error occurred (SlowDown) when calling GetObjectRetention" >&2; return 1; fi
            if [ -e "$T/ret-$k-$v.json" ]; then cat "$T/ret-$k-$v.json"
            else echo "An error occurred (NoSuchObjectLockConfiguration): no ObjectLock configuration" >&2; return 254; fi ;;
        get-object-legal-hold)
            k=""; v=""
            while [ $# -gt 0 ]; do case "$1" in --key) k="$2";; --version-id) v="$2";; esac; shift; done
            if [ -e "$T/errhold-$k" ]; then echo "An error occurred (InternalError)" >&2; return 1; fi
            if [ -e "$T/hold-$k-$v.json" ]; then cat "$T/hold-$k-$v.json"
            else echo "An error occurred (NoSuchObjectLockConfiguration)" >&2; return 254; fi ;;
        get-object)
            # get-object --bucket B --key K --version-id V <dest>: el último argumento es el fichero
            args=("$@"); dest="${args[$#-1]}"; k=""; v=""
            while [ $# -gt 0 ]; do case "$1" in --key) k="$2";; --version-id) v="$2";; esac; shift; done
            if [ -e "$T/errget-$k" ]; then echo "An error occurred (InternalError)" >&2; return 1; fi
            if [ -e "$T/obj-$k-$v" ]; then cat "$T/obj-$k-$v" > "$dest"; else printf 'x' > "$dest"; fi ;;
        put-object)
            args=("$@"); k=""; body=""
            while [ $# -gt 0 ]; do case "$1" in --key) k="$2";; --body) body="$2";; esac; shift; done
            if [ -e "$T/errput-$k" ]; then return 1; fi
            n=$(( $(cat "$T/nver" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$T/nver"
            echo "PUT $k $(cat "$body") NUEVA-$n" >> "$T/orden.log"; echo "NUEVA-$n" ;;
        get-object-lock-configuration)
            if [ -e "$T/err-lock" ]; then echo "An error occurred (AccessDenied)" >&2; return 1; fi
            if [ -e "$T/lock.json" ]; then cat "$T/lock.json"
            else echo "An error occurred (ObjectLockConfigurationNotFoundError)" >&2; return 254; fi ;;
    esac
}
'''


def _bash(tmp: Path, cuerpo: str, **env: str) -> subprocess.CompletedProcess[str]:
    script = f'set -u; T="{tmp}"; FALLAN="${{FALLAN:-}}"\n{_FALSO}\n. "{LIB}"\n{cuerpo}\n'
    import os

    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        timeout=60,
    )


def _prev(tmp: Path, clave: str, ver: str, contenido: str, ultima: str = "False", lm: str = "2026-01-01T00:00:00+00:00") -> str:
    """Una fila de `list-object-versions` (4 columnas) y el contenido que devolverá `get-object`."""
    (tmp / f"obj-{clave}-{ver}").write_text(contenido)
    return f"{clave}\t{ver}\t{ultima}\t{lm}"


def _tsv(tmp: Path, lineas: list[str]) -> Path:
    f = tmp / "minio-retencion.tsv"
    f.write_text("\n".join(lineas) + "\n")
    return f


def _obj(clave: str, modo: str = "COMPLIANCE", hasta: str = FUTURO, ver: str = "v1", hold: str = "OFF", ultima: int = 1) -> str:
    return "\t".join(["OBJETO", f"orpycamcp-demo-documents/{clave}", modo, hasta, ver, hold, str(ultima)])


def test_la_bateria_tiene_los_ficheros() -> None:
    assert LIB.is_file() and (RAIZ / "infra" / "backup.sh").is_file() and (RAIZ / "infra" / "restore.sh").is_file()


def test_todo_repuesto_devuelve_cero_y_cuenta(tmp_path: Path) -> None:
    tsv = _tsv(tmp_path, [_obj("a"), _obj("b"), _obj("c")])
    r = _bash(tmp_path, f'reponer_retencion "{tsv}"; echo "rc=$?"')
    assert "rc=0" in r.stdout, r.stderr
    assert "3/3 retenciones" in r.stdout


def test_un_put_object_retention_que_falla_hace_fallar_la_restauracion(tmp_path: Path) -> None:
    """El caso del hallazgo: 3 OBJETO vigentes y un put que falla -> sale distinto de 0."""
    tsv = _tsv(tmp_path, [_obj("a"), _obj("b"), _obj("c")])
    r = _bash(tmp_path, f'reponer_retencion "{tsv}"; echo "rc=$?"', FALLAN="b")
    assert "rc=1" in r.stdout, r.stdout + r.stderr
    assert "2/3 retenciones" in r.stdout
    assert "no se repuso la retención de orpycamcp-demo-documents/b" in r.stderr


def test_la_retencion_legal_activa_se_repone_y_si_falla_se_falla(tmp_path: Path) -> None:
    tsv = _tsv(tmp_path, [_obj("a", modo="-", hasta="-", hold="ON")])
    r = _bash(tmp_path, f'reponer_retencion "{tsv}"; echo "rc=$?"')
    assert "rc=0" in r.stdout and "put-object-legal-hold --bucket orpycamcp-demo-documents --key a" in (
        tmp_path / "llamadas.log"
    ).read_text()
    r = _bash(tmp_path, f'reponer_retencion "{tsv}"; echo "rc=$?"', FALLA_HOLD="1")
    assert "rc=1" in r.stdout


def test_una_retencion_vencida_no_se_inventa_ni_cuenta_como_faltante(tmp_path: Path) -> None:
    tsv = _tsv(tmp_path, [_obj("a", hasta=PASADO), _obj("b")])
    r = _bash(tmp_path, f'reponer_retencion "{tsv}"; echo "rc=$?"')
    assert "rc=0" in r.stdout and "1/1 retenciones" in r.stdout
    assert "--key a " not in (tmp_path / "llamadas.log").read_text()


def test_un_respaldo_del_formato_anterior_se_sigue_leyendo(tmp_path: Path) -> None:
    tsv = _tsv(tmp_path, [f"OBJETO\torpycamcp-demo-documents/viejo\tCOMPLIANCE\t{FUTURO}"])
    r = _bash(tmp_path, f'reponer_retencion "{tsv}"; echo "rc=$?"')
    assert "rc=0" in r.stdout and "1/1 retenciones" in r.stdout


def test_una_version_previa_bajo_compliance_vigente_hace_fallar_la_restauracion(tmp_path: Path) -> None:
    """Hallazgo T14: la evidencia anterior (índice/AIP re-sellado) desaparecía con un AVISO y rc=0."""
    tsv = _tsv(tmp_path, [_obj("a", ver="v1", ultima=0), _obj("a", ver="v2", ultima=1)])
    r = _bash(tmp_path, f'reponer_retencion "{tsv}"; echo "rc=$?"')
    assert "rc=1" in r.stdout, r.stdout + r.stderr
    assert "1 versiones previas bajo COMPLIANCE vigente" in r.stderr
    assert "RESTORE_ACEPTAR_PERDIDA_VERSIONES=1" in r.stderr


def test_la_perdida_de_versiones_previas_solo_se_acepta_con_la_bandera_explicita(tmp_path: Path) -> None:
    tsv = _tsv(tmp_path, [_obj("a", ver="v1", ultima=0), _obj("a", ver="v2", ultima=1)])
    r = _bash(tmp_path, f'reponer_retencion "{tsv}"; echo "rc=$? previas=$RETENCION_PREVIAS_COMPLIANCE"',
              RESTORE_ACEPTAR_PERDIDA_VERSIONES="1")
    assert "rc=0 previas=1" in r.stdout and "aceptado" in r.stderr


def test_una_version_previa_gobernada_o_vencida_solo_avisa(tmp_path: Path) -> None:
    tsv = _tsv(tmp_path, [
        _obj("a", ver="v1", ultima=0, modo="GOVERNANCE"),
        _obj("b", ver="v1", ultima=0, hasta=PASADO),
        _obj("a", ver="v2", ultima=1),
    ])
    r = _bash(tmp_path, f'reponer_retencion "{tsv}"; echo "rc=$?"')
    assert "rc=0" in r.stdout and "2 versiones previas" in r.stderr


def test_se_emite_el_mapa_de_version_id_viejo_a_nuevo(tmp_path: Path) -> None:
    tsv = _tsv(tmp_path, [_obj("a", ver="vieja-7")])
    mapa = tmp_path / "mapa.tsv"
    r = _bash(tmp_path, f'reponer_retencion "{tsv}" "{mapa}"; echo "rc=$?"')
    assert "rc=0" in r.stdout
    assert mapa.read_text().strip() == "OBJETO\torpycamcp-demo-documents/a\tvieja-7\tNUEVA-VERSION"


def test_el_respaldo_guarda_versiones_y_retencion_legal(tmp_path: Path) -> None:
    (tmp_path / "versiones.txt").write_text("doc.pdf\tv1\tFalse\ndoc.pdf\tv2\tTrue\nsolohold.pdf\tw1\tTrue\n")
    ret = '{"Retention":{"Mode":"COMPLIANCE","RetainUntilDate":"2099-01-01T00:00:00+00:00"}}'
    (tmp_path / "ret-doc.pdf-v1.json").write_text(ret)
    (tmp_path / "ret-doc.pdf-v2.json").write_text(ret)
    (tmp_path / "hold-solohold.pdf-w1.json").write_text('{"LegalHold":{"Status":"ON"}}')
    salida = tmp_path / "out.tsv"
    salida.write_text("")
    r = _bash(tmp_path, f'respaldar_retencion_bucket b1 "{salida}"; echo "rc=$?"')
    assert "rc=0" in r.stdout, r.stderr
    filas = [l.split("\t") for l in salida.read_text().splitlines()]
    por_clave = {(f[1], f[4]): f for f in filas}
    assert ("b1/doc.pdf", "v1") in por_clave and por_clave[("b1/doc.pdf", "v1")][6] == "0"
    assert por_clave[("b1/doc.pdf", "v2")][6] == "1" and por_clave[("b1/doc.pdf", "v2")][2] == "COMPLIANCE"
    sh = por_clave[("b1/solohold.pdf", "w1")]
    assert sh[2] == "-" and sh[3] == "-" and sh[5] == "ON", "la retención legal sin plazo también se guarda"


def test_una_lectura_de_retencion_que_falla_aborta_el_bucket(tmp_path: Path) -> None:
    """Hallazgo T14: `|| true` tomaba un error transitorio del almacén por «sin retención»."""
    (tmp_path / "versiones.txt").write_text("doc.pdf\tv1\tTrue\n")
    (tmp_path / "err-doc.pdf").write_text("")
    salida = tmp_path / "out.tsv"
    salida.write_text("")
    r = _bash(tmp_path, f'respaldar_retencion_bucket b1 "{salida}"; echo "rc=$?"')
    assert "rc=1" in r.stdout, r.stdout + r.stderr
    assert "SlowDown" in r.stderr and salida.read_text() == ""


def test_una_lectura_de_retencion_legal_que_falla_aborta_el_bucket(tmp_path: Path) -> None:
    (tmp_path / "versiones.txt").write_text("doc.pdf\tv1\tTrue\n")
    (tmp_path / "ret-doc.pdf-v1.json").write_text(
        '{"Retention":{"Mode":"COMPLIANCE","RetainUntilDate":"2099-01-01T00:00:00+00:00"}}')
    (tmp_path / "errhold-doc.pdf").write_text("")
    salida = tmp_path / "out.tsv"
    salida.write_text("")
    r = _bash(tmp_path, f'respaldar_retencion_bucket b1 "{salida}"; echo "rc=$?"')
    assert "rc=1" in r.stdout and "InternalError" in r.stderr


def test_una_version_sin_retencion_en_un_bucket_bloqueado_es_una_anomalia(tmp_path: Path) -> None:
    """Las cuentas versiones listadas vs filas OBJETO deben cuadrar."""
    (tmp_path / "versiones.txt").write_text("doc.pdf\tv1\tTrue\nlibre.pdf\tz1\tTrue\n")
    (tmp_path / "ret-doc.pdf-v1.json").write_text(
        '{"Retention":{"Mode":"COMPLIANCE","RetainUntilDate":"2099-01-01T00:00:00+00:00"}}')
    salida = tmp_path / "out.tsv"
    salida.write_text("")
    r = _bash(tmp_path, f'respaldar_retencion_bucket b1 "{salida}"; echo "rc=$?"')
    assert "rc=1" in r.stdout and "2 versiones listadas pero 1 con retención" in r.stderr


def test_leer_json_distingue_ausencia_de_error(tmp_path: Path) -> None:
    r = _bash(tmp_path, '_leer_json_s3 x s3api get-object-lock-configuration --bucket b; echo "rc=$?"')
    assert "{}" in r.stdout and "rc=0" in r.stdout, "ausencia de configuración = vacío legítimo"
    (tmp_path / "err-lock").write_text("")
    r = _bash(tmp_path, '_leer_json_s3 x s3api get-object-lock-configuration --bucket b; echo "rc=$?"')
    assert "rc=1" in r.stdout and "AccessDenied" in r.stderr, "cualquier otro fallo NO es «sin bloqueo»"


def test_lo_que_guarda_el_respaldo_lo_repone_la_restauracion(tmp_path: Path) -> None:
    """Ida y vuelta: el formato que escribe `respaldar_*` es el que lee `reponer_*`."""
    (tmp_path / "versiones.txt").write_text("doc.pdf\tv2\tTrue\n")
    (tmp_path / "ret-doc.pdf-v2.json").write_text(
        '{"Retention":{"Mode":"COMPLIANCE","RetainUntilDate":"2099-01-01T00:00:00+00:00"}}'
    )
    (tmp_path / "hold-doc.pdf-v2.json").write_text('{"LegalHold":{"Status":"ON"}}')
    tsv = tmp_path / "rt.tsv"
    tsv.write_text("")
    r = _bash(tmp_path, f'respaldar_retencion_bucket orpycamcp-x "{tsv}"; reponer_retencion "{tsv}"; echo "rc=$?"')
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    log = (tmp_path / "llamadas.log").read_text()
    assert "put-object-retention --bucket orpycamcp-x --key doc.pdf" in log
    assert "put-object-legal-hold --bucket orpycamcp-x --key doc.pdf" in log


# ── Estructura de los scripts que usan la librería ────────────────────────────


def _sin_comentarios(ruta: Path) -> str:
    return "\n".join(l for l in ruta.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#"))


def test_restore_usa_la_libreria_y_aborta_si_falta_retencion() -> None:
    t = _sin_comentarios(RAIZ / "infra" / "restore.sh")
    assert 'lib-retencion-s3.sh' in t
    assert "if ! reponer_retencion" in t
    bloque = t[t.index("if ! reponer_retencion") :]
    assert "exit 1" in bloque.split("fi", 1)[0], "sin retención repuesta, la restauración sale con error"
    assert "put-object-retention" not in t, "la reposición vive en la librería, no inline con `|| true`"


def test_backup_usa_la_libreria_para_la_retencion() -> None:
    t = _sin_comentarios(RAIZ / "infra" / "backup.sh")
    assert "lib-retencion-s3.sh" in t and "respaldar_retencion_bucket" in t
    assert "list-objects-v2 --bucket" not in t.split("Retención del almacén")[-1], "solo la versión actual"


def test_backup_verifica_la_cadena_antes_de_anclar_y_no_aborta() -> None:
    t = _sin_comentarios(RAIZ / "infra" / "backup.sh")
    verif = t.index("app.ops.verify_audit_chains")
    anexa = t.index('>> "$ANCLAS"')
    assert verif < anexa, "el ancla se escribe DESPUÉS de verificar, nunca antes"
    assert "psql" not in t[t.index("ANCLAS=") :].split("# 4. Cifrado")[0], "el ancla ya no sale de un psql sin verificar"
    bloque = t[verif:anexa]
    assert "exit 1" not in bloque, "una rotura histórica no puede abortar el respaldo (deja un .PARCIAL en claro)"
    assert 'VERIFICACION_AUDITORIA="FALLIDA"' in bloque


def test_restore_usa_el_mismo_modulo_y_no_un_heredoc() -> None:
    t = _sin_comentarios(RAIZ / "infra" / "restore.sh")
    assert "app.ops.verify_audit_chains" in t
    assert "audit.verify_chain" not in t, "el heredoc que desempaquetaba una tupla ya no existe"


@pytest.mark.parametrize("script", ["backup.sh", "restore.sh", "lib-retencion-s3.sh"])
def test_los_scripts_son_sintacticamente_validos(script: str) -> None:
    r = subprocess.run(["bash", "-n", str(RAIZ / "infra" / script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ── Comportamiento (no solo texto) de los bloques de verificación de la cadena ────
# Los guardianes de arriba miran que exista `exit 1` entre dos marcas; no ven una
# condición INVERTIDA (`if NUEVAS=...` en vez de `if ! NUEVAS=...`: saldría con error
# cuando todo va bien y anclaría cuando la cadena está rota... o al revés). Aquí se
# EJECUTA el bloque real, extraído del script, con un `docker compose` falso.

_DOCKER_FALSO = """#!/bin/bash
printf '%s\\n' "$*" >> "$FAKE_LOG"
[ -n "${FAKE_OUT:-}" ] && printf '%s\\n' "$FAKE_OUT"
exit "${FAKE_RC:-0}"
"""


def _bloque(ruta: Path, desde: str, hasta: str) -> str:
    """Texto desde la primera línea que EMPIEZA por `desde` hasta la primera posterior que
    empieza por `hasta` (incluida)."""
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    i = next(n for n, l in enumerate(lineas) if l.startswith(desde))
    j = next(n for n, l in enumerate(lineas) if n > i and l.startswith(hasta))
    return "\n".join(lineas[i : j + 1])


def _ejecutar(tmp_path: Path, bloque: str, *, rc: int, salida: str = "") -> tuple[subprocess.CompletedProcess[str], Path, str]:
    falso = tmp_path / "docker-falso"
    falso.write_text(_DOCKER_FALSO)
    falso.chmod(0o755)
    anclas = tmp_path / "anclas.tsv"
    anclas.write_text("fecha\ttenant_slug\tasientos\tultimo_id\tultimo_hash\n")
    guion = (
        "set -euo pipefail\n"
        f'COMPOSE=("{falso}")\nFECHA=20261003-0300\nANCLAS="{anclas}"\n'
        "DB_USER=u DB_PASSWORD=p DB_NAME=d\n"
        f"{bloque}\n"
        'echo "CONTINUA"\n'
    )
    r = subprocess.run(
        ["bash", "-c", guion],
        env={"PATH": "/usr/bin:/bin", "FAKE_LOG": str(tmp_path / "log"), "FAKE_RC": str(rc), "FAKE_OUT": salida},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return r, anclas, anclas.read_text()


_LINEA_ANCLA = "20261003-0300\tdemo\t5\t99\t" + "ab" * 32


def test_backup_si_la_verificacion_falla_no_ancla_pero_el_respaldo_continua(tmp_path: Path) -> None:
    """Separar «no anclar» de «abortar»: la cadena rota no se sella como extremo de
    confianza, pero el respaldo se completa (cifrado, sello) y lo marca en el sello."""
    bloque = _bloque(RAIZ / "infra" / "backup.sh", "EXCLUIR=()", "ANCLADAS=")
    r, _, texto = _ejecutar(tmp_path, bloque + '\necho "V=$VERIFICACION_AUDITORIA"', rc=1, salida=_LINEA_ANCLA)
    assert r.returncode == 0 and "CONTINUA" in r.stdout, r.stderr
    assert "V=FALLIDA" in r.stdout
    assert _LINEA_ANCLA not in texto, "con la verificación en rojo no se anexa NINGÚN ancla, ni siquiera lo que imprimió"


def test_backup_pasa_la_linea_base_de_roturas_junto_al_ancla_por_stdin(tmp_path: Path) -> None:
    """La línea base no se monta en el contenedor (regla 3): viaja concatenada al ancla."""
    base = tmp_path / "base.tsv"
    base.write_text("ROTURA\tdemo\t41004\tincidente\n")
    bloque = _bloque(RAIZ / "infra" / "backup.sh", "EXCLUIR=()", "ANCLADAS=")
    falso = tmp_path / "docker-falso"
    falso.write_text('#!/bin/bash\ncat > "$FAKE_STDIN"\nexit 0\n')
    falso.chmod(0o755)
    anclas = tmp_path / "anclas.tsv"
    anclas.write_text("fecha\ttenant_slug\tasientos\tultimo_id\tultimo_hash\n")
    entrada = tmp_path / "stdin.txt"
    r = subprocess.run(
        ["bash", "-c", f'set -euo pipefail\nCOMPOSE=("{falso}")\nFECHA=F\nANCLAS="{anclas}"\nDB_USER=u DB_PASSWORD=p DB_NAME=d\n{bloque}\necho "V=$VERIFICACION_AUDITORIA"'],
        env={"PATH": "/usr/bin:/bin", "FAKE_STDIN": str(entrada), "BACKUP_AUDIT_ROTURAS_DECLARADAS": str(base)},
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert r.returncode == 0 and "V=OK" in r.stdout, r.stderr
    assert "ROTURA\tdemo\t41004" in entrada.read_text()
    # Una línea base inexistente = verificación FALLIDA (fail-closed), no «sin línea base».
    r = subprocess.run(
        ["bash", "-c", f'set -euo pipefail\nCOMPOSE=("{falso}")\nFECHA=F\nANCLAS="{anclas}"\nDB_USER=u DB_PASSWORD=p DB_NAME=d\n{bloque}\necho "V=$VERIFICACION_AUDITORIA"'],
        env={"PATH": "/usr/bin:/bin", "FAKE_STDIN": str(entrada), "BACKUP_AUDIT_ROTURAS_DECLARADAS": str(tmp_path / "no-existe")},
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert r.returncode == 0 and "V=FALLIDA" in r.stdout


@pytest.mark.parametrize(
    ("verif", "ret", "codigo"),
    [("OK", "SI", 0), ("FALLIDA", "SI", 3), ("OK", "NO", 4), ("FALLIDA", "NO", 3)],
)
def test_backup_sale_con_codigo_distinto_de_cero_al_final(tmp_path: Path, verif: str, ret: str, codigo: int) -> None:
    _salida_final(verif, ret, "SI", codigo)


def test_backup_sale_con_5_si_las_versiones_previas_no_quedaron_respaldadas() -> None:
    """B4: la evidencia previa bajo COMPLIANCE sin respaldar no pasa en verde."""
    _salida_final("OK", "SI", "NO", 5)
    _salida_final("OK", "NO", "NO", 4)


def _salida_final(verif: str, ret: str, prev: str, codigo: int) -> None:
    """El respaldo ya está completo y sellado: la supervisión se entera por el código de salida."""
    lineas = (RAIZ / "infra" / "backup.sh").read_text(encoding="utf-8").splitlines()
    i = next(n for n, l in enumerate(lineas) if l.startswith('echo "══ Respaldo COMPLETO'))
    r = subprocess.run(
        ["bash", "-c", f'VERIFICACION_AUDITORIA={verif}\nRETENCION_COMPLETA={ret}\nVERSIONES_PREVIAS_COMPLETAS={prev}\nDESTINO=/x\n' + "\n".join(lineas[i:])],
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert r.returncode == codigo, r.stderr


def test_el_sello_registra_verificacion_y_retencion() -> None:
    t = _sin_comentarios(RAIZ / "infra" / "backup.sh")
    assert 'echo "verificacion_auditoria=$VERIFICACION_AUDITORIA"' in t
    assert 'echo "retencion_capturada_completa=$RETENCION_COMPLETA"' in t


def test_restore_rechaza_un_respaldo_con_retencion_incompleta_salvo_aceptacion() -> None:
    t = _sin_comentarios(RAIZ / "infra" / "restore.sh")
    assert "retencion_capturada_completa=NO" in t and "RESTORE_ACEPTAR_RETENCION_INCOMPLETA" in t
    assert "app.ops.registrar_restauracion" in t, "el acto de restaurar deja asiento inmutable"


def test_backup_si_la_verificacion_pasa_anexa_el_ancla_y_continua(tmp_path: Path) -> None:
    """Control positivo: la condición no está invertida."""
    bloque = _bloque(RAIZ / "infra" / "backup.sh", "EXCLUIR=()", "ANCLADAS=")
    r, _, texto = _ejecutar(tmp_path, bloque, rc=0, salida=_LINEA_ANCLA)
    assert r.returncode == 0 and "CONTINUA" in r.stdout, r.stderr
    assert texto.rstrip("\n").endswith(_LINEA_ANCLA)
    vacio, _, texto_vacio = _ejecutar(tmp_path, bloque, rc=0, salida="")
    assert vacio.returncode == 0 and texto_vacio.count("\n") == 1, "sin líneas nuevas no se anexa nada"


def test_restore_si_la_cadena_esta_rota_no_levanta_los_servicios(tmp_path: Path) -> None:
    ruta = RAIZ / "infra" / "restore.sh"
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    ref = next(n for n, l in enumerate(lineas) if "app.ops.verify_audit_chains" in l and not l.lstrip().startswith("#"))
    i = max(n for n in range(ref) if lineas[n].startswith("if ! "))
    j = next(n for n in range(ref, len(lineas)) if lineas[n] == "fi")
    bloque = "\n".join(lineas[i : j + 1])
    for rc, sale in ((1, 1), (0, 0)):
        falso = tmp_path / f"d{rc}"
        falso.write_text(_DOCKER_FALSO)
        falso.chmod(0o755)
        anclas = tmp_path / "anclas.tsv"
        anclas.write_text("")
        r = subprocess.run(
            ["bash", "-c", f'set -euo pipefail\nCOMPOSE=("{falso}")\nANCLAS="{anclas}"\nENTRADA_VERIF="{anclas}"\nDB_USER=u DB_PASSWORD=p DB_NAME=d\n{bloque}\necho CONTINUA'],
            env={"PATH": "/usr/bin:/bin", "FAKE_LOG": str(tmp_path / "log"), "FAKE_RC": str(rc)},
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert r.returncode == sale, f"rc={rc}: se esperaba salida {sale}, fue {r.returncode}\n{r.stderr}"
        assert ("CONTINUA" in r.stdout) == (rc == 0)


# --- B4: versiones previas bajo COMPLIANCE (respaldo y restauración) ---------------------


def _previas_tmp(tmp: Path) -> tuple[Path, Path]:
    d = tmp / "dir"
    d.mkdir()
    return d, tmp / "minio-versiones.tsv"


_RET = '{"Retention":{"Mode":"COMPLIANCE","RetainUntilDate":"2099-01-01T00:00:00+00:00"}}'


def test_el_respaldo_captura_la_previa_bajo_compliance_y_su_sha256(tmp_path: Path) -> None:
    import hashlib

    (tmp_path / "versiones.txt").write_text(
        _prev(tmp_path, "indice.json", "v1", "ANTIGUO") + "\n" + _prev(tmp_path, "indice.json", "v2", "ACTUAL", ultima="True")
        + "\n" + _prev(tmp_path, "suelta.txt", "s1", "SIN-RETENCION") + "\n"
    )
    (tmp_path / "ret-indice.json-v1.json").write_text(_RET)
    (tmp_path / "ret-indice.json-v2.json").write_text(_RET)  # la actual también protegida: la copia el espejo
    d, tsv = _previas_tmp(tmp_path)
    r = _bash(tmp_path, f'respaldar_versiones_previas b1 "{d}" "{tsv}"; echo "rc=$?"')
    assert "rc=0" in r.stdout, r.stderr
    filas = [l.split("\t") for l in tsv.read_text().splitlines()]
    assert len(filas) == 1, "solo la previa protegida: ni la actual ni la previa sin retención"
    tipo, obj, vid, sha, lm = filas[0]
    assert (tipo, obj, vid, lm) == ("VERSION", "b1/indice.json", "v1", "2026-01-01T00:00:00+00:00")
    assert sha == hashlib.sha256(b"ANTIGUO").hexdigest()
    ident = hashlib.sha256(b"indice.json\tv1").hexdigest()
    assert (d / "b1" / ident).read_text() == "ANTIGUO"


def test_una_previa_con_retencion_legal_sin_plazo_tambien_se_respalda(tmp_path: Path) -> None:
    (tmp_path / "versiones.txt").write_text(_prev(tmp_path, "a", "v1", "A") + "\n")
    (tmp_path / "hold-a-v1.json").write_text('{"LegalHold":{"Status":"ON"}}')
    d, tsv = _previas_tmp(tmp_path)
    r = _bash(tmp_path, f'respaldar_versiones_previas b1 "{d}" "{tsv}"; echo "rc=$?"')
    assert "rc=0" in r.stdout and tsv.read_text().count("VERSION\t") == 1, r.stderr


def test_si_no_se_puede_descargar_la_previa_el_respaldo_falla(tmp_path: Path) -> None:
    (tmp_path / "versiones.txt").write_text(_prev(tmp_path, "a", "v1", "A") + "\n")
    (tmp_path / "ret-a-v1.json").write_text(_RET)
    (tmp_path / "errget-a").write_text("")
    d, tsv = _previas_tmp(tmp_path)
    r = _bash(tmp_path, f'respaldar_versiones_previas b1 "{d}" "{tsv}"; echo "rc=$?"')
    assert "rc=1" in r.stdout and "no se pudo descargar la versión previa" in r.stderr


def _respaldo_con_previas(tmp: Path) -> tuple[Path, Path, Path, Path, Path]:
    """Un respaldo ya hecho: dos previas de la misma clave (v1 más antigua que v2) y la actual v3."""
    import hashlib

    d = tmp / "dir"
    (d / "b1").mkdir(parents=True)
    filas = []
    for vid, contenido, lm in (("v2", "SEGUNDO", "2026-03-01T00:00:00+00:00"), ("v1", "PRIMERO", "2026-01-01T00:00:00+00:00")):
        (d / "b1" / hashlib.sha256(f"k.json\t{vid}".encode()).hexdigest()).write_text(contenido)
        filas.append(f"VERSION\tb1/k.json\t{vid}\t{hashlib.sha256(contenido.encode()).hexdigest()}\t{lm}")
    vtsv = tmp / "minio-versiones.tsv"
    vtsv.write_text("\n".join(filas) + "\n")
    rtsv = _tsv(tmp, [
        f"OBJETO\tb1/k.json\tCOMPLIANCE\t{FUTURO}\tv1\tOFF\t0",
        f"OBJETO\tb1/k.json\tCOMPLIANCE\t{FUTURO}\tv2\tON\t0",
        f"OBJETO\tb1/k.json\tCOMPLIANCE\t{FUTURO}\tv3\tOFF\t1",
    ])
    actual = tmp / "minio"
    (actual / "b1").mkdir(parents=True)
    (actual / "b1" / "k.json").write_text("ACTUAL")
    return d, vtsv, rtsv, actual, tmp / "mapa.tsv"


def test_la_restauracion_sube_por_lastmodified_ascendente_y_la_actual_la_ultima(tmp_path: Path) -> None:
    d, vtsv, rtsv, actual, mapa = _respaldo_con_previas(tmp_path)
    r = _bash(tmp_path, f'restaurar_versiones_previas "{vtsv}" "{d}" "{rtsv}" "{mapa}" "{actual}"; echo "rc=$?"')
    assert "rc=0" in r.stdout, r.stderr
    orden = [l.split()[2] for l in (tmp_path / "orden.log").read_text().splitlines()]
    assert orden == ["PRIMERO", "SEGUNDO", "ACTUAL"], "la más antigua primero y la versión actual al final, explícita"
    llamadas = (tmp_path / "llamadas.log").read_text()
    # la retención se repone sobre el VersionId DEVUELTO (NUEVA-n), no sobre el viejo
    assert "put-object-retention --bucket b1 --key k.json --version-id NUEVA-1" in llamadas
    assert "put-object-retention --bucket b1 --key k.json --version-id NUEVA-2" in llamadas
    assert "put-object-legal-hold --bucket b1 --key k.json --version-id NUEVA-2" in llamadas
    assert "--version-id v1 " not in llamadas.split("put-object-retention")[1]
    lineas = mapa.read_text().splitlines()
    assert "OBJETO\tb1/k.json\tv1\tNUEVA-1" in lineas and "OBJETO\tb1/k.json\tv2\tNUEVA-2" in lineas
    assert "OBJETO\tb1/k.json\tv3\tNUEVA-3" in lineas, "la actual también queda en el mapa"


def test_un_sha256_distinto_en_el_respaldo_hace_fallar_la_restauracion_sin_subir_nada(tmp_path: Path) -> None:
    import hashlib

    d, vtsv, rtsv, actual, mapa = _respaldo_con_previas(tmp_path)
    (d / "b1" / hashlib.sha256(b"k.json\tv1").hexdigest()).write_text("ALTERADO")
    r = _bash(tmp_path, f'restaurar_versiones_previas "{vtsv}" "{d}" "{rtsv}" "{mapa}" "{actual}"; echo "rc=$?"')
    assert "rc=1" in r.stdout and "NO coincide" in r.stderr
    assert not (tmp_path / "orden.log").exists(), "ni siquiera la primera se sube si su sha no cuadra"


def test_una_previa_respaldada_ya_no_cuenta_como_perdida_en_reponer(tmp_path: Path) -> None:
    d, vtsv, rtsv, actual, mapa = _respaldo_con_previas(tmp_path)
    sin = _bash(tmp_path, f'reponer_retencion "{rtsv}"; echo "rc=$?"')
    assert "rc=1" in sin.stdout, "sin minio-versiones.tsv la previa vigente sigue siendo pérdida"
    con = _bash(tmp_path, f'reponer_retencion "{rtsv}" "{mapa}" "{vtsv}"; echo "rc=$? previas=$RETENCION_PREVIAS_COMPLIANCE"')
    assert "rc=0 previas=0" in con.stdout, con.stdout + con.stderr


def test_backup_y_restore_cablean_las_versiones_previas() -> None:
    b = _sin_comentarios(RAIZ / "infra" / "backup.sh")
    r = _sin_comentarios(RAIZ / "infra" / "restore.sh")
    assert "respaldar_versiones_previas" in b and "minio-versiones.tsv" in b
    assert "tar -C \"$DESTINO\" -cf - minio minio-versiones" in b, "las previas viajan dentro del tar cifrado"
    assert 'echo "versiones_previas_capturadas=$VERSIONES_PREVIAS_COMPLETAS"' in b
    assert "restaurar_versiones_previas" in r
    assert r.index("restaurar_versiones_previas") < r.index("s3 sync"), "las previas se suben ANTES del sync"
    assert "versiones_previas_capturadas=NO" in r
    # nada de verify_audit_chains con el permiso de objetivo vacío en los scripts
    assert "permitir-objetivo-vacio" not in b and "permitir-objetivo-vacio" not in r


def test_la_actual_delete_marker_se_declara_en_el_respaldo_y_se_restaura(tmp_path: Path) -> None:
    """B4 baja: si la versión actual es un delete marker, el respaldo lo declara y la
    restauración repone el borrado (antes abortaba: «falta la versión actual»)."""
    (tmp_path / "versiones.txt").write_text(_prev(tmp_path, "k.json", "v1", "PRIMERO") + "\n")
    (tmp_path / "ret-k.json-v1.json").write_text(_RET)
    (tmp_path / "marcadores.txt").write_text("k.json\tdm1\t2026-05-01T00:00:00+00:00\notra\tdm2\t-\n")
    d, tsv = _previas_tmp(tmp_path)
    r = _bash(tmp_path, f'respaldar_versiones_previas b1 "{d}" "{tsv}"; echo "rc=$?"')
    assert "rc=0" in r.stdout, r.stderr
    lineas = tsv.read_text().splitlines()
    assert "MARCADOR\tb1/k.json\tdm1\t2026-05-01T00:00:00+00:00" in lineas
    assert not any("b1/otra" in l for l in lineas), "solo claves con previas capturadas"
    # restauración: sin fichero actual en el espejo
    actual = tmp_path / "minio"
    (actual / "b1").mkdir(parents=True)
    rtsv = _tsv(tmp_path, [f"OBJETO\tb1/k.json\tCOMPLIANCE\t{FUTURO}\tv1\tOFF\t0"])
    mapa = tmp_path / "mapa.tsv"
    r2 = _bash(tmp_path, f'restaurar_versiones_previas "{tsv}" "{d}" "{rtsv}" "{mapa}" "{actual}"; echo "rc=$?"')
    assert "rc=0" in r2.stdout, r2.stderr
    orden = (tmp_path / "orden.log").read_text().splitlines()
    assert orden[0].startswith("PUT k.json PRIMERO") and orden[-1].startswith("DELETE delete-object --bucket b1 --key k.json")


@pytest.mark.parametrize("clave", ["../../etc/x", "a/../b", "/abs/x", "a\x01b"])
def test_una_clave_insegura_del_respaldo_se_rechaza_sin_subir_nada(tmp_path: Path, clave: str) -> None:
    """B4 baja: path traversal desde un respaldo manipulado."""
    import hashlib

    d = tmp_path / "dir"
    (d / "b1").mkdir(parents=True)
    contenido = "X"
    (d / "b1" / hashlib.sha256(f"{clave}\tv1".encode()).hexdigest()).write_text(contenido)
    vtsv = tmp_path / "minio-versiones.tsv"
    vtsv.write_text(f"VERSION\tb1/{clave}\tv1\t{hashlib.sha256(contenido.encode()).hexdigest()}\t2026-01-01T00:00:00+00:00\n")
    rtsv = _tsv(tmp_path, [])
    actual = tmp_path / "minio"
    actual.mkdir()
    r = _bash(tmp_path, f'restaurar_versiones_previas "{vtsv}" "{d}" "{rtsv}" "" "{actual}"; echo "rc=$?"')
    assert "rc=1" in r.stdout and "clave insegura" in r.stderr
    assert not (tmp_path / "orden.log").exists()
