# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Respaldo horario dump-then-files y cubo de preservación fuera del servidor."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from tests._compose import COMPOSE_PROD, RAIZ, servicios

BACKUP = RAIZ / "infra" / "backup.sh"
POLITICA = RAIZ / "infra" / "politica-s3-app.json"
STORAGE = RAIZ / "services" / "storage-service" / "storage" / "services" / "storage_service.py"
REPLICA = RAIZ / "services" / "storage-service" / "storage" / "services" / "aip_replica_service.py"


def _sin_comentarios(ruta: Path) -> str:
    return "\n".join(
        linea
        for linea in ruta.read_text(encoding="utf-8").splitlines()
        if not linea.lstrip().startswith("#")
    )


def _metodo(ruta: Path, nombre: str) -> ast.AST:
    arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)) and nodo.name == nombre:
            return nodo
    raise AssertionError(f"no está {nombre} en {ruta}")


def _nombres(nodo: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(nodo) if isinstance(n, ast.Name)}


def test_el_cron_del_respaldo_es_horario_y_la_prueba_semanal_existe() -> None:
    texto = BACKUP.read_text(encoding="utf-8")
    assert "0 * * * *" in texto
    assert "verify-restore-semanal.sh" in texto
    assert (RAIZ / "infra" / "verify-restore-semanal.sh").is_file()


def test_el_volcado_termina_antes_de_copiar_archivos() -> None:
    t = _sin_comentarios(BACKUP)
    assert t.index("pg_dump") < t.index("s3 sync")
    etapa_pg = t.index("PostgreSQL (pg_dump")
    etapa_obj = t.index("Almacén de objetos")
    assert etapa_pg < etapa_obj


def test_la_politica_niega_borrar_preservacion_y_cambiar_el_cubo() -> None:
    politica = json.loads(POLITICA.read_text(encoding="utf-8"))
    deny = [s for s in politica["Statement"] if s["Effect"] == "Deny"]
    acciones = {a for s in deny for a in s["Action"]}
    assert "s3:DeleteObject" in acciones
    assert "s3:DeleteObjectVersion" in acciones
    assert "s3:PutBucketPolicy" in acciones
    assert "s3:PutObjectLockConfiguration" in acciones or "s3:PutBucketObjectLockConfiguration" in acciones
    recursos = " ".join(r for s in deny for r in s["Resource"])
    assert "preservation" in recursos
    allow = [s for s in politica["Statement"] if s["Effect"] == "Allow"]
    assert allow, "tiene que poder escribir y retener"


def test_la_descarga_lee_el_almacen_de_trabajo() -> None:
    for nombre in ("get_object_stream", "get_presigned_url"):
        nombres = _nombres(_metodo(STORAGE, nombre))
        assert "get_minio" in nombres, nombre
        assert "_get_secondary_minio" not in nombres, nombre


def test_la_replica_usa_el_mismo_cliente_minio() -> None:
    texto = REPLICA.read_text(encoding="utf-8")
    assert "from miniopy_async import Minio" in texto
    assert "boto3" not in texto
    nombres = _nombres(_metodo(REPLICA, "_get_secondary_minio"))
    assert "Minio" in nombres


def test_produccion_exige_el_almacen_secundario() -> None:
    for nombre in ("app", "worker", "storage-service"):
        entorno = servicios(COMPOSE_PROD)[nombre]["environment"]
        valor = str(entorno["MINIO_SECONDARY_ENDPOINT"])
        assert "${MINIO_SECONDARY_ENDPOINT:?" in valor, nombre
        assert str(entorno["PRESERVATION_REPLICA_ENABLED"]) == "true", nombre


def test_restore_para_el_proceso_unificado() -> None:
    t = _sin_comentarios(RAIZ / "infra" / "restore.sh")
    i = t.index(" stop \\")
    bloque = t[i : i + 500]
    assert re.search(r"\bapp\b", bloque)
    assert re.search(r"\bworker\b", bloque)
    assert "auth.ops.verify_audit_chains" in t
    assert "auth.ops.registrar_restauracion" in t


def test_la_prueba_semanal_usa_el_app_unificado() -> None:
    t = _sin_comentarios(RAIZ / "infra" / "verify-restore-semanal.sh")
    assert "auth.ops.verify_audit_chains" in t
    assert " app python -m auth.ops.verify_audit_chains" in t.replace("\n", " ")
    assert "@sha256:" in t
