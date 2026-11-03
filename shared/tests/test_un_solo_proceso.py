# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El proceso unificado monta los routers de dominio y no arranca intelligence.

AST y manifiestos, sin importar FastAPI: CI de `test-unit-shared` no instala
las apps. Un `include_router` de health de cada módulo duplicaría `/health`;
el consumidor de intelligence no debe vivir en este lifespan (ADR-045).
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._compose import COMPOSE_DEV, RAIZ, ruta_src, servicios

_DOMINIO = (
    "auth-service",
    "tenant-service",
    "document-service",
    "archive-service",
    "storage-service",
    "workflow-service",
    "notification-service",
    "signature-service",
    "knowledge-service",
)
_GATEWAY_URLS = (
    "AUTH_SERVICE_URL",
    "TENANT_SERVICE_URL",
    "DOCUMENT_SERVICE_URL",
    "ARCHIVE_SERVICE_URL",
    "STORAGE_SERVICE_URL",
    "WORKFLOW_SERVICE_URL",
    "NOTIFICATION_SERVICE_URL",
    "SIGNATURE_SERVICE_URL",
    "KNOWLEDGE_SERVICE_URL",
)


def _arbol(ruta: Path) -> ast.AST:
    return ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))


def _nombres_asignados(arbol: ast.AST, nombre: str) -> ast.AST | None:
    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign):
            for destino in nodo.targets:
                if isinstance(destino, ast.Name) and destino.id == nombre:
                    return nodo.value
        if isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name):
            if nodo.target.id == nombre and nodo.value is not None:
                return nodo.value
    return None


def test_cada_modulo_exporta_domain_routers_sin_health_ni_metrics() -> None:
    faltan: list[str] = []
    for servicio in _DOMINIO:
        arbol = _arbol(ruta_src(servicio, "main.py"))
        valor = _nombres_asignados(arbol, "DOMAIN_ROUTERS")
        if valor is None:
            faltan.append(f"{servicio}: sin DOMAIN_ROUTERS")
            continue
        texto = ast.dump(valor)
        if "health" in texto.lower():
            faltan.append(f"{servicio}: DOMAIN_ROUTERS incluye health")
        if "metrics" in texto.lower() and servicio != "knowledge-service":
            # knowledge mounts inteligencia (not metrics) in DOMAIN_ROUTERS.
            pass
        if "metrics" in texto.lower():
            faltan.append(f"{servicio}: DOMAIN_ROUTERS incluye metrics")
    assert not faltan, faltan


def test_el_proceso_incluye_los_nueve_domain_routers() -> None:
    texto = (RAIZ / "services/docuboto/docuboto/main.py").read_text(encoding="utf-8")
    for nombre in (
        "AUTH_ROUTERS",
        "TENANT_ROUTERS",
        "DOCUMENT_ROUTERS",
        "ARCHIVE_ROUTERS",
        "STORAGE_ROUTERS",
        "WORKFLOW_ROUTERS",
        "NOTIFICATION_ROUTERS",
        "SIGNATURE_ROUTERS",
        "KNOWLEDGE_ROUTERS",
    ):
        assert nombre in texto, f"docuboto.main no incluye {nombre}"
    assert "include_router(_router)" in texto
    assert "health_router" in texto
    assert "metrics_router" in texto


def test_un_solo_health_en_el_proceso() -> None:
    salud = (RAIZ / "services/docuboto/docuboto/routers/health.py").read_text(encoding="utf-8")
    assert salud.count('"/health"') == 1
    assert '"/health/ready"' in salud
    assert 'liveness(_SERVICE)' in salud or "liveness(" in salud


def test_los_batch_no_colisionan() -> None:
    documentos = ruta_src("document-service", "routers/batch.py").read_text(encoding="utf-8")
    archivo = ruta_src("archive-service", "routers/batch.py").read_text(encoding="utf-8")
    assert 'prefix="/api/v1/batch"' in documentos
    assert 'prefix="/api/v1/batch"' in archivo
    assert '"/documents"' in documentos
    assert '"/expedientes"' in archivo


def test_el_lifespan_no_arranca_intelligence() -> None:
    texto = (RAIZ / "services/docuboto/docuboto/core/lifespan.py").read_text(encoding="utf-8")
    assert "intelligence_consumer" not in texto
    arbol = ast.parse(texto)
    llamados = [
        nodo.func.attr if isinstance(nodo.func, ast.Attribute) else getattr(nodo.func, "id", "")
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.Call)
    ]
    assert "spawn_supervised" in llamados
    # The three Redis consumers that belong in this process.
    assert "workflow_consumer" in texto
    assert "stream_worker" in texto
    assert "signature_consumer" in texto
    assert "registrar_apertura_de_flujo" in texto
    assert "abrir_al_radicar" in texto


def test_el_dockerfile_vive_fuera_de_services() -> None:
    assert (RAIZ / "infra" / "Dockerfile.app").is_file()
    assert not (RAIZ / "services" / "docuboto" / "Dockerfile").exists()


def test_compose_dev_levanta_un_proceso_por_defecto() -> None:
    svcs = servicios(COMPOSE_DEV)
    assert "app" in svcs, "falta el servicio `app`"
    app = svcs["app"]
    assert app.get("profiles") in (None, []), "app no lleva perfil: es el default"
    build = app.get("build") or {}
    assert build.get("dockerfile") == "infra/Dockerfile.app"
    for nombre in _DOMINIO:
        perfiles = svcs[nombre].get("profiles") or []
        assert perfiles == ["split"], f"{nombre} debe quedar en el perfil split: {perfiles}"


def test_los_tokens_internos_tienen_alias_por_modulo() -> None:
    pares = (
        ("document-service", "DOCUMENT_INTERNAL_TOKEN"),
        ("storage-service", "STORAGE_INTERNAL_TOKEN"),
        ("archive-service", "ARCHIVE_INTERNAL_TOKEN"),
        ("notification-service", "NOTIFICATION_INTERNAL_TOKEN"),
        ("tenant-service", "TENANT_INTERNAL_TOKEN"),
    )
    for servicio, env in pares:
        texto = ruta_src(servicio, "core/config.py").read_text(encoding="utf-8")
        assert env in texto, servicio
        assert "INTERNAL_SERVICE_TOKEN" in texto, servicio
        assert "AliasChoices" in texto, servicio


def test_el_trafico_no_pasa_por_el_gateway() -> None:
    svcs = servicios(COMPOSE_DEV)
    assert (svcs["api-gateway"].get("profiles") or []) == ["split"]
    entorno = svcs["frontend"].get("environment") or []
    pares = {}
    for item in entorno:
        if isinstance(item, str) and "=" in item:
            k, _, v = item.partition("=")
            pares[k] = v
    assert pares.get("INTERNAL_API_URL") == "http://app:8000"
    assert pares.get("PUBLIC_API_URL") == "http://localhost:19000"
    mcp = svcs["mcp-server"].get("environment") or []
    mcp_pares = {}
    for item in mcp:
        if isinstance(item, str) and "=" in item:
            k, _, v = item.partition("=")
            mcp_pares[k] = v
    assert mcp_pares.get("GATEWAY_URL") == "http://app:8000"


def test_el_gateway_apunta_al_proceso() -> None:
    entorno = servicios(COMPOSE_DEV)["api-gateway"].get("environment") or []
    if isinstance(entorno, dict):
        pares = entorno
    else:
        pares = {}
        for item in entorno:
            if isinstance(item, str) and "=" in item:
                k, _, v = item.partition("=")
                pares[k] = v
    faltan = [k for k in _GATEWAY_URLS if pares.get(k) != "http://app:8000"]
    assert not faltan, f"el gateway no apunta a app:8000: {faltan} → { {k: pares.get(k) for k in faltan} }"
