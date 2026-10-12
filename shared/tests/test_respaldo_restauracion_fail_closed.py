# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Respaldo y restauración fail-closed (B7: R2, R5 y R5b).

Las decisiones viven en funciones de shell de `backup.sh`/`restore.sh` y aquí se
EJECUTAN de verdad (se extraen del script y se corren con `bash`), no se buscan
como texto:

* R2: `pg_restore` que sale ≠ 0 SIN ninguna línea `pg_restore: error:` (contenedor
  caído, OOM) se tomaba por inocuo («ignoradas 0»). Solo se admite si hay al menos un
  error y TODOS son la PK heredada de las particiones de `audit_log`.
* R5: `list-buckets ... || true` dejaba el espejo vacío y el respaldo salía bien.
  Ahora el espejo tiene que traer el bucket de documentos de cada tenant activo.
* R5b: `put-object-lock-configuration ... || true` escondía un bucket que ya existía
  SIN Object-Lock; se comprueba con `get-object-lock-configuration` antes de tocar datos.

* SEG-M2: solo se admite rc == 1 con `errors ignored on restore: N` y N == número de PK
  heredadas (> 0); cualquier otro código, o un N mayor, es fallo.
* ARC-M3: el pre-vuelo del Object-Lock corre ANTES de parar servicios y de `pg_restore`,
  de modo que «no se ha tocado ningún dato» es verdad.
* ARC-M4: `RESTORE_OPERADOR` obligatorio y fail-closed antes de confirmar; el extremo de
  `audit_log` se anota ANTES de `pg_restore` y viaja al acta.

Mutaciones: `-gt 0` -> `-ge 0` (R2), quitar `[ "${2:-}" = "1" ]` (SEG-M2), mover el
pre-vuelo tras `pg_restore` (ARC-M3), quitar el chequeo de `RESTORE_OPERADOR` (ARC-M4), quitar el `|| exit 1` o el chequeo del estado
(R5b), añadir `|| true` a `list-buckets` o vaciar `exigir_espejo_completo` (R5).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from tests._compose import RAIZ

BACKUP = RAIZ / "infra" / "backup.sh"
RESTORE = RAIZ / "infra" / "restore.sh"

HEREDADA = (
    'pg_restore: error: could not execute query: ERROR:  cannot drop inherited '
    'constraint "audit_log_2026_10_pkey" of relation "audit_log_2026_10"'
)
OTRO = 'pg_restore: error: could not execute query: ERROR:  relation "x" already exists'


def _funcion(ruta: Path, nombre: str) -> str:
    """Texto de `nombre() { ... }` (cierra en la primera `}` al inicio de línea)."""
    texto = ruta.read_text(encoding="utf-8")
    m = re.search(rf"^{nombre}\(\) \{{\n.*?^\}}\n", texto, re.S | re.M)
    assert m, f"{nombre} no está en {ruta.name}"
    return m.group(0)


def _bash(guion: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + guion],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _sin_comentarios(ruta: Path) -> str:
    return "\n".join(
        l for l in ruta.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#")
    )


# ── R2 ────────────────────────────────────────────────────────────────────────


def _ignorados(n: int) -> str:
    return f"pg_restore: warning: errors ignored on restore: {n}\n"


def _inocuo(tmp_path: Path, stderr: str, rc: int = 1) -> bool:
    f = tmp_path / "err.txt"
    f.write_text(stderr)
    r = _bash(f'{_funcion(RESTORE, "errores_de_pg_restore_inocuos")}\nerrores_de_pg_restore_inocuos "{f}" {rc}')
    return r.returncode == 0


def test_pg_restore_que_sale_distinto_de_cero_sin_lineas_de_error_no_es_inocuo(tmp_path: Path) -> None:
    """Contenedor caído / `compose exec` fallido / OOM: ni una línea `pg_restore: error:`."""
    assert not _inocuo(tmp_path, "")
    assert not _inocuo(tmp_path, "Killed\n" + _ignorados(0))


def test_solo_las_pk_heredadas_son_inocuas(tmp_path: Path) -> None:
    assert _inocuo(tmp_path, f"{HEREDADA}\nCommand was: ...\n{HEREDADA}\n" + _ignorados(2))


def test_un_error_ajeno_junto_a_las_heredadas_no_es_inocuo(tmp_path: Path) -> None:
    assert not _inocuo(tmp_path, f"{HEREDADA}\n{OTRO}\n" + _ignorados(2))
    assert not _inocuo(tmp_path, f"{OTRO}\n" + _ignorados(1))


def test_el_codigo_de_salida_debe_ser_exactamente_1(tmp_path: Path) -> None:
    """SEG-M2: 137 (OOM), 2, 126 o 127 con un stderr que SE PARECE al inocuo no pasan."""
    ok = f"{HEREDADA}\n" + _ignorados(1)
    assert _inocuo(tmp_path, ok, rc=1)
    for rc in (2, 3, 126, 127, 137):
        assert not _inocuo(tmp_path, ok, rc=rc), rc


def test_la_cuenta_declarada_debe_coincidir_con_las_heredadas(tmp_path: Path) -> None:
    """N ignorados != nº de PK heredadas: hubo errores sin línea `error:` (o falta el resumen)."""
    assert not _inocuo(tmp_path, f"{HEREDADA}\n" + _ignorados(2))
    assert not _inocuo(tmp_path, f"{HEREDADA}\n{HEREDADA}\n" + _ignorados(1))
    assert not _inocuo(tmp_path, f"{HEREDADA}\n"), "sin la línea «errors ignored on restore»"
    assert not _inocuo(tmp_path, _ignorados(0)), "N == 0 no es «inocuo»: no hay heredadas"


def test_restore_aborta_si_los_errores_no_son_inocuos() -> None:
    texto = _sin_comentarios(RESTORE)
    m = re.search(
        r'if ! errores_de_pg_restore_inocuos "\$ERRORES_PG" "\$PG_RC"; then(.*?)\n    fi\n', texto, re.S
    )
    assert m, "restore.sh no decide con errores_de_pg_restore_inocuos"
    assert "exit 1" in m.group(1)


# ── R5b ───────────────────────────────────────────────────────────────────────


def _lock(respuesta: str) -> subprocess.CompletedProcess[str]:
    return _bash(
        f"s3api() {{ {respuesta}; }}\n"
        f'{_funcion(RESTORE, "exigir_object_lock_habilitado")}\n'
        "exigir_object_lock_habilitado bucket-x"
    )


def test_un_bucket_con_object_lock_habilitado_pasa() -> None:
    assert _lock("echo Enabled").returncode == 0


def test_un_bucket_sin_object_lock_aborta_antes_de_tocar_datos() -> None:
    for respuesta in ("return 254", "echo None", "echo Disabled", "true"):
        r = _lock(respuesta)
        assert r.returncode == 1, respuesta
        assert "no quedó con Object-Lock habilitado" in r.stderr
        assert "No se ha parado ningún servicio ni se ha restaurado nada" in r.stderr


def test_restore_comprueba_el_bloqueo_antes_de_seguir() -> None:
    texto = _sin_comentarios(RESTORE)
    i_cfg = texto.index("put-object-lock-configuration")
    i_chk = texto.index('exigir_object_lock_habilitado "$BUCKET" || exit 1')
    i_ver = texto.index("restaurar_versiones_previas")
    assert i_cfg < i_chk < i_ver, "la comprobación va tras configurar y antes de subir versiones"


def test_el_preflight_del_bloqueo_corre_antes_de_parar_y_de_pg_restore() -> None:
    """ARC-M3: «no se ha tocado ningún dato» solo es cierto si nada se ha parado ni restaurado."""
    texto = _sin_comentarios(RESTORE)
    i_chk = texto.index('exigir_object_lock_habilitado "$BUCKET" || exit 1')
    i_s3 = texto.index("s3api() {")
    i_stop = texto.index(" stop \\")
    i_pg = texto.index("pg_restore -U")
    i_sync = texto.index("s3 sync")
    assert i_s3 < i_chk < i_stop < i_pg < i_sync, "pre-vuelo -> parar servicios -> pg_restore -> espejo"


# ── ARC-M4 ────────────────────────────────────────────────────────────────────


def test_restore_exige_operador_antes_de_confirmar() -> None:
    texto = _sin_comentarios(RESTORE)
    i_op = texto.index('"${#RESTORE_OPERADOR}" -lt 3')
    assert i_op < texto.index("read -r -p"), "el operador se exige ANTES de la confirmación"
    assert i_op < texto.index(" stop \\")
    bloque = texto[i_op : i_op + 400]
    assert "exit 1" in bloque


def test_restore_sin_operador_sale_1_sin_tocar_nada(tmp_path: Path) -> None:
    (tmp_path / "OK").write_text("ok\n")
    (tmp_path / "postgres.dump").write_text("x")
    env_file = tmp_path / "env"
    env_file.write_text("DB_USER=u\nDB_NAME=d\nMINIO_ROOT_USER=a\nMINIO_ROOT_PASSWORD=b\n")
    for operador in ({}, {"RESTORE_OPERADOR": "ab"}):
        r = subprocess.run(
            ["bash", str(RESTORE), str(tmp_path)],
            env={"PATH": "/usr/bin:/bin", "ENV_FILE": str(env_file), **operador},
            capture_output=True, text=True, check=False, timeout=30, input="RESTAURAR\n",
        )
        assert r.returncode == 1 and "RESTORE_OPERADOR es obligatorio" in r.stderr, r.stderr


def test_el_extremo_se_anota_antes_de_pg_restore_y_viaja_al_acta() -> None:
    texto = _sin_comentarios(RESTORE)
    i_cap = texto.index("EXTREMO_PREVIO")
    assert texto.index(" stop \\") < i_cap < texto.index("pg_restore -U")
    acta = texto[texto.index("app.ops.registrar_restauracion") - 300 :]
    assert 'cat "$EXTREMOS_PREVIOS"' in acta and '--operador "$RESTORE_OPERADOR"' in acta
    # la captura es fail-closed: si falla, no se restaura
    cap = texto[i_cap : texto.index("pg_restore -U")]
    assert "exit 1" in cap


# ── R5 ────────────────────────────────────────────────────────────────────────


def _espejo(buckets: str, slugs: str) -> subprocess.CompletedProcess[str]:
    return _bash(
        f'{_funcion(BACKUP, "exigir_espejo_completo")}\n'
        f'exigir_espejo_completo "{buckets}" "{slugs}"'
    )


def test_el_espejo_con_el_bucket_de_cada_tenant_activo_pasa() -> None:
    assert _espejo("orpycamcp-demo-documents\norpycamcp-b-documents\notro", "demo\nb").returncode == 0


def test_un_espejo_vacio_o_incompleto_falla_el_respaldo() -> None:
    for buckets in ("", "orpycamcp-demo-documents", "orpycamcp-demo-documents-viejo"):
        r = _espejo(buckets, "demo\nb")
        assert r.returncode == 1, buckets
        assert "el espejo no contiene" in r.stderr


def test_backup_no_se_traga_el_fallo_de_listar_los_buckets() -> None:
    texto = _sin_comentarios(BACKUP)
    [linea] = [l for l in texto.splitlines() if l.startswith("BUCKETS_ESPEJO=")]
    assert "list-buckets" in linea
    assert "|| true" not in linea, "un almacén sin responder dejaba el espejo vacío con el respaldo OK"
    assert 'exigir_espejo_completo "$BUCKETS_ESPEJO" "$SLUGS_ACTIVOS" || exit 1' in texto
    # ARC-B3: también los `suspended`; solo se excluye la baja (`inactive`).
    assert "status <> 'inactive'" in texto and "status = 'active'" not in texto


def test_los_scripts_siguen_siendo_validos() -> None:
    for ruta in (BACKUP, RESTORE):
        r = subprocess.run(["bash", "-n", str(ruta)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stderr
