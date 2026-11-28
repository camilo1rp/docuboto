# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El proceso unificado monta los routers de dominio y arranca la ingesta PPI.

AST y manifiestos, sin importar FastAPI: CI de `test-unit-shared` no instala
las apps. Un `include_router` de health de cada módulo duplicaría `/health`.
El consumidor de ingesta PPI arranca en el lifespan de la app, y solo con
`PPI_PROVIDER_URL` (ADR-051 §2); knowledge no lo arranca. Sin `worker` ni
`api-gateway` (ADR-047): los bucles pesados corren en la app y los manifiestos
solo declaran la app y la infraestructura que ADR-048/049/050 aún no retiran.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._chain import create_table
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
    assert "include_router(router)" in texto and "def create_app(" in texto
    assert "health_router" in texto
    assert "metrics_router" in texto


def test_ningun_dominio_monta_su_propia_app() -> None:
    """ADR-047: un `FastAPI`, una sonda, un `/metrics`. Los dominios exportan routers."""
    for servicio in _DOMINIO:
        texto = ruta_src(servicio, "main.py").read_text(encoding="utf-8")
        assert "FastAPI(" not in texto, servicio
        assert "lifespan" not in texto, servicio
        assert not ruta_src(servicio, "routers", "health.py").exists(), servicio
        assert not ruta_src(servicio, "routers", "metrics.py").exists(), servicio


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


def test_el_lifespan_arranca_la_ingesta_ppi_solo_con_proveedor() -> None:
    texto = (RAIZ / "services/docuboto/docuboto/core/lifespan.py").read_text(encoding="utf-8")
    assert "intelligence_consumer.run" in texto
    arbol = ast.parse(texto)
    guardas = [
        nodo
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.If) and "ppi_provider_url" in ast.unparse(nodo.test)
    ]
    assert len(guardas) == 1, "la ingesta PPI tiene que arrancar bajo `if ...ppi_provider_url`"
    assert "intelligence_consumer.run" in ast.unparse(guardas[0])
    assert texto.count("intelligence_consumer.run") == 1, "la ingesta PPI arranca sin proveedor"


def test_knowledge_service_no_arranca_la_ingesta() -> None:
    texto = (RAIZ / "services/docuboto/knowledge/main.py").read_text(encoding="utf-8")
    assert "intelligence_consumer" not in texto


def test_el_lifespan_arranca_los_bucles_del_proceso() -> None:
    texto = (RAIZ / "services/docuboto/docuboto/core/lifespan.py").read_text(encoding="utf-8")
    # The consumers of the PostgreSQL bus that belong in this process (ADR-049).
    for marca in (
        "workflow_consumer",
        "stream_worker",
        "signature_consumer",
        "registrar_apertura_de_flujo",
        "abrir_al_radicar",
        "BackgroundTasks",
        "supervised=True",
    ):
        assert marca in texto, marca
    # Lo que hacía el worker (ADR-047): migración de formato, LTA y la cola pesada,
    # cada uno con su plaza de trabajo pesado.
    assert "COLA_WORKER" in texto and "COLA_APP" in texto
    assert "_format_migration_once" in texto
    assert "renovacion_lta.run_once" in texto
    assert texto.count("heavy_slot()") >= 2
    trabajo = (RAIZ / "services/docuboto/docuboto/core/trabajo.py").read_text(encoding="utf-8")
    assert "heavy_slot()" in trabajo


def test_no_queda_proceso_worker() -> None:
    assert not (RAIZ / "services/docuboto/docuboto/worker.py").exists()
    assert not (RAIZ / "services/docuboto/docuboto/core/lifespan_worker.py").exists()
    assert not (RAIZ / "infra" / "Dockerfile.worker").exists()


def test_el_bus_y_el_antirreplay_viven_en_tablas() -> None:
    """ADR-049: el evento nace en la tx del cambio; no hay outbox ni relé ni segundo almacén."""
    assert create_table("eventos")
    assert create_table("evento_entregas")
    assert create_table("evento_ultimo_error")
    assert create_table("altcha_usados")
    app = RAIZ / "services" / "docuboto"
    assert not (app / "document" / "services" / "outbox.py").exists()
    assert not list(app.glob("*/core/re" "dis_client.py"))
    antirrobots = ruta_src("document-service", "services/antirrobots.py").read_text(encoding="utf-8")
    assert "altcha_usados" in antirrobots and "altcha:usado:" not in antirrobots


def test_el_nonce_ppi_vive_en_tabla() -> None:
    texto = ruta_src("knowledge-service", "routers/retorno.py").read_text(encoding="utf-8")
    assert "ppi_nonces" in texto
    assert "orpycamcp:ppi:nonce" not in texto
    assert create_table("ppi_nonces")
    assert create_table("trabajo_fuera")
    admin = ruta_src("document-service", "routers/trabajo.py").read_text(encoding="utf-8")
    assert "reintentar" in admin
    assert "USUA_PERM_ADMIN" in admin


def test_el_dockerfile_vive_fuera_de_services() -> None:
    assert (RAIZ / "infra" / "Dockerfile.app").is_file()
    assert not list((RAIZ / "services").glob("*/Dockerfile"))


def test_la_imagen_lleva_los_xsd() -> None:
    texto = (RAIZ / "infra" / "Dockerfile.app").read_text(encoding="utf-8")
    assert "services/docuboto/schemas/" in texto
    assert "/app/schemas/" in texto


def test_la_imagen_app_lleva_lo_que_llevaba_el_worker() -> None:
    app = (RAIZ / "infra" / "Dockerfile.app").read_text(encoding="utf-8").lower()
    for marca in ("openjdk", "verapdf", "ghostscript", "docuboto.main:app"):
        assert marca in app, marca


def test_postgres_va_en_la_version_actual_y_keycloak_no_esta() -> None:
    for compose in (COMPOSE_DEV, RAIZ / "infra" / "docker-compose.prod.yml"):
        texto = compose.read_text(encoding="utf-8")
        assert "postgres:17-bookworm@sha256:" in texto
        assert "postgres:15-" not in texto
        assert "keycloak" not in texto.lower(), "la identidad la emite la app (ADR-048)"


#: Lo que un manifiesto puede declarar mientras ADR-050 no retire el almacén de
#: objetos. Keycloak salió con ADR-048 y el almacén de eventos con ADR-049.
_PERMITIDOS = {
    COMPOSE_DEV: {"migrate-docuboto", "postgres", "minio", "app", "frontend", "mailpit"},
    RAIZ / "infra" / "docker-compose.prod.yml": {
        "migrate-docuboto", "postgres", "minio", "app", "frontend", "edge",
    },
}


def test_los_manifiestos_solo_declaran_la_app_y_su_infraestructura() -> None:
    for manifiesto, permitidos in _PERMITIDOS.items():
        svcs = servicios(manifiesto)
        sobran = sorted(set(svcs) - permitidos)
        assert not sobran, f"{manifiesto.name} declara {sobran} (ADR-047 §2)"
        perfiles = {n: svc.get("profiles") for n, svc in svcs.items() if svc.get("profiles")}
        # mailpit (buzón de desarrollo) es el único opcional; `split` no vuelve.
        assert set(perfiles) <= {"mailpit"}, f"{manifiesto.name}: perfiles {perfiles}"


def test_compose_dev_levanta_un_proceso_por_defecto() -> None:
    app = servicios(COMPOSE_DEV)["app"]
    build = app.get("build") or {}
    assert build.get("dockerfile") == "infra/Dockerfile.app"


def test_el_frontend_habla_con_la_app() -> None:
    entorno = servicios(COMPOSE_DEV)["frontend"].get("environment") or []
    pares = {}
    for item in entorno:
        if isinstance(item, str) and "=" in item:
            k, _, v = item.partition("=")
            pares[k] = v
    assert pares.get("INTERNAL_API_URL") == "http://app:8000"
    assert pares.get("PUBLIC_API_URL") == "http://localhost:19000"
