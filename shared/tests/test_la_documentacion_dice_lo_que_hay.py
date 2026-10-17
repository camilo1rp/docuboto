# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La documentación no afirma lo que el código ya contradice (T16, AREA-14-*).

Cada afirmación falsa que esta tanda corrigió tenía la misma forma: se escribió cuando
era cierta y el código siguió. Un comentario o un ADR que dice «no implementado» sobre
algo que ya existe no es neutro: dirige trabajo (se reabre lo cerrado), o peor, hace que
alguien copie una receta que el proyecto ya descartó por insegura.

Cada prueba ata la frase al hecho del código que la hace falsa: la frase solo se
prohíbe MIENTRAS el hecho exista. Si el hecho desaparece (se retira el endpoint, se borra
el middleware), la prueba deja de exigir nada y no obliga a conservar texto muerto.

Control de vacuidad: cada comprobación afirma antes que el hecho existe y que el
fichero que vigila se leyó; un guardián que no encuentra su objeto aprobaría en vacío.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[2]
_DOCS = _RAIZ / "docs"
_SERVICIOS = _RAIZ / "services"

pytestmark = pytest.mark.skipif(
    not (_SERVICIOS.is_dir() and _DOCS.is_dir()),
    reason="el árbol del repositorio no está montado en esta imagen",
)


def _leer(rel: str) -> str:
    p = _RAIZ / rel
    assert p.is_file(), f"no existe {rel}: el guardián vigilaría el vacío"
    return p.read_text(encoding="utf-8")


def _plano(texto: str) -> str:
    """Una frase partida en dos renglones sigue siendo la misma frase."""
    return re.sub(r"\s+", " ", texto)


def _contiene(rel: str, frase: str) -> bool:
    return frase.lower() in _plano(_leer(rel)).lower()


# --------------------------------------------------------------------------- AREA-14-01


def test_el_roadmap_no_dice_que_la_disposicion_final_no_se_ejecuta() -> None:
    router = _leer("services/archive-service/app/routers/eliminacion.py")
    assert "/lotes/{lote_id}/ejecutar" in router, "el hecho que desmiente la frase ya no existe"
    for rel in ("docs/es/roadmap.md", "docs/en/roadmap.md"):
        for falsa in ("no destruye nunca", "never destroys", "La disposición final no se ejecuta"):
            # `\b` final: «no se ejecutaba» (el relato histórico) no es la frase falsa.
            hay = re.search(re.escape(falsa) + r"\b", _plano(_leer(rel)), re.I)
            assert hay is None, f"{rel} afirma «{falsa}» con /ejecutar implementado"


# --------------------------------------------------------------------------- AREA-14-02


def test_el_roadmap_no_dice_que_las_escrituras_de_catalogos_y_metadatos_estan_abiertas() -> None:
    routers = (
        "services/document-service/app/routers/metadata.py",
        "services/document-service/app/routers/metadata_elements.py",
        "services/tenant-service/app/routers/catalogos.py",
        "services/tenant-service/app/routers/config.py",
    )
    for r in routers:
        # Toda escritura declara su gate: cada @router.post/patch/put/delete va
        # seguido de require_permission antes del `async def`.
        src = _leer(r)
        for m in re.finditer(r"@router\.(post|patch|put|delete)\((.*?)\)\s*\nasync def", src, re.S):
            assert "require_permission" in m.group(2), f"{r}: escritura sin gate"
    for rel in ("docs/es/roadmap.md", "docs/en/roadmap.md"):
        assert not _contiene(rel, "no declaran ningún `require_permission`")


# --------------------------------------------------------------------------- AREA-02-01


def test_el_roadmap_no_deja_abierto_el_limitador_por_ip_si_el_middleware_existe() -> None:
    assert "class RateLimitPorUsuarioMiddleware" in _leer(
        "services/api-gateway/app/middleware/rate_limit.py"
    )
    assert "RateLimitPorUsuarioMiddleware" in _leer("services/api-gateway/app/main.py")
    roadmap = _leer("docs/es/roadmap.md")
    assert "Pendiente abierto — el limitador cuenta por IP" not in roadmap
    # Si queda el bloque histórico, su casilla está tachada.
    m = re.search(r"- \[(.)\] (?:~~)?`GET /api/v1/assistant/alcance` responde \*\*429\*\*", roadmap)
    assert m is None or m.group(1) == "x"


# --------------------------------------------------------------------------- AREA-14-03


def test_el_numero_de_adrs_citado_a_mano_coincide_con_el_arbol() -> None:
    reales = {
        re.match(r"(ADR-\d+)", p.name).group(1)  # type: ignore[union-attr]
        for p in (_DOCS / "es" / "adr").glob("ADR-*.md")
    }
    assert len(reales) > 20, "el censo de ADRs salió vacío"
    for rel in ("docs/es/roadmap.md", "docs/en/roadmap.md"):
        for m in re.finditer(r"(\d+) ADRs", _leer(rel)):
            assert int(m.group(1)) == len(reales), (
                f"{rel} cita {m.group(1)} ADRs y el árbol tiene {len(reales)}: "
                "enlaza al índice en vez de escribir el número"
            )


def test_la_cabecera_del_roadmap_tiene_fecha_igual_en_los_dos_idiomas() -> None:
    es = re.search(r"Actualizado al (\d{4}-\d{2}-\d{2})", _leer("docs/es/roadmap.md"))
    en = re.search(r"Updated as of (\d{4}-\d{2}-\d{2})", _leer("docs/en/roadmap.md"))
    assert es and en
    assert es.group(1) == en.group(1)


# --------------------------------------------------------------------------- AREA-14-04 (roadmap) / OMI-07
#
# El changelog EN NO se vigila por paridad: `docs/es/changelog.md` declara que la versión
# inglesa es una selección deliberada y no una traducción (decisión de proyecto, a confirmar).


def _casillas_abiertas(rel: str) -> int:
    return len(re.findall(r"^\s*- \[ \]", _leer(rel), re.M))


def test_los_pendientes_abiertos_del_roadmap_son_los_mismos_en_es_y_en() -> None:
    es = _casillas_abiertas("docs/es/roadmap.md")
    en = _casillas_abiertas("docs/en/roadmap.md")
    assert es > 0, "el censo de casillas salió vacío"
    assert es == en, f"el roadmap ES tiene {es} pendientes abiertos y el EN {en}"


def _tandas_de_seguridad(rel: str, patron: str) -> set[str]:
    return set(re.findall(patron, _leer(rel)))


def test_security_audit_en_tiene_las_mismas_tandas_y_secciones_que_es() -> None:
    es = _tandas_de_seguridad("docs/es/security-audit.md", r"\(tanda (T\d+)")
    en = _tandas_de_seguridad("docs/en/security-audit.md", r"\(batch (T\d+)")
    # T7 se titula distinto en cada idioma pero existe en ambos.
    es |= set(re.findall(r"\(octubre de 2026, (T\d+)\)", _leer("docs/es/security-audit.md")))
    en |= set(re.findall(r"\(October 2026, (T\d+)\)", _leer("docs/en/security-audit.md")))
    assert len(es) >= 12
    assert es == en, f"tandas solo en ES: {sorted(es - en)}; solo en EN: {sorted(en - es)}"
    es_txt, en_txt = _leer("docs/es/security-audit.md"), _leer("docs/en/security-audit.md")
    assert "segunda pasada" in es_txt and "Second-pass audit" in en_txt
    assert "ventanilla ciudadana" in es_txt and "citizen window" in en_txt


# --------------------------------------------------------------------------- AREA-14-06 / 14-07

_RECETAS_PROHIBIDAS = (
    re.compile(r"session\.execute\(text\("),
    re.compile(r"migraciones? Alembic|Alembic migrations?\b(?! \()", re.I),
    re.compile(r"modelos? SQLAlchemy|SQLAlchemy models?", re.I),
)
#: Una mención es receta solo si no la acompaña una negación: «sin ORM ni Alembic»,
#: «no hay Alembic», «originally written as Alembic, superseded» son la corrección.
_NEGACION = re.compile(
    r"\b(sin|no|nunca|prohib\w*|without|never|superad\w*|superseded|originally|originalmente|"
    r"retirar|ya no|instead of|en lugar de|no ORM)\b",
    re.I,
)
_EXENTOS = ("adr/ADR-003", "changelog.md", "adrs.md", "plan-pruebas.md")


def _docs_md() -> list[Path]:
    return sorted(p for p in _DOCS.rglob("*.md") if not any(e in p.as_posix() for e in _EXENTOS))


def test_la_documentacion_no_receta_lo_prohibido() -> None:
    docs = _docs_md()
    assert len(docs) > 30
    ofensas: list[str] = []
    for p in docs:
        for n, linea in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for pat in _RECETAS_PROHIBIDAS:
                if pat.search(linea) and not _NEGACION.search(linea):
                    ofensas.append(f"{p.relative_to(_RAIZ)}:{n}: {linea.strip()[:90]}")
            # `SET search_path` con interpolación y sin comillas dobles: inyección.
            if re.search(r"SET search_path", linea, re.I) and "{" in linea and '"{' not in linea:
                if not re.search(r"tenant_\{slug\}", linea):  # prosa con el patrón de nombre
                    ofensas.append(f"{p.relative_to(_RAIZ)}:{n}: search_path sin validar/entrecomillar")
    assert not ofensas, "\n".join(ofensas)


def test_el_control_de_recetas_caza_la_linea_que_se_retiro() -> None:
    """Control positivo: las líneas que esta tanda sacó de multi-tenancy.md habrían caído."""
    assert any(
        p.search('await session.execute(text(f"SET search_path TO tenant_{x}, public"))')
        for p in _RECETAS_PROHIBIDAS
    )
    assert any(p.search("Ejecuta las migraciones Alembic en ese schema") for p in _RECETAS_PROHIBIDAS)


def test_los_slugs_de_los_ejemplos_casan_con_el_validador_de_tenant_service() -> None:
    # El validador es único (AREA-02-05): el alta de tenant-service lo importa.
    from orpycamcp_common.tenant import SLUG_RE as slug_re

    assert "slug_valido" in _leer("services/tenant-service/app/schemas/tenant.py")
    vistos = 0
    for p in _DOCS.rglob("*multi-tenancy.md"):
        txt = p.read_text(encoding="utf-8")
        for s in re.findall(r'"slug":\s*"([^"]*)"', txt) + re.findall(r"init-tenant\.sh (\S+)", txt):
            vistos += 1
            assert slug_re.match(s), f"{p.relative_to(_RAIZ)}: el slug de ejemplo «{s}» no lo admite el servicio"
    assert vistos >= 2


def test_ningun_documento_manda_a_dar_de_alta_un_tenant_por_el_gateway() -> None:
    proxy = _leer("services/api-gateway/app/routers/proxy.py")
    assert '("/api/v1/tenants' not in proxy, "el gateway enruta /tenants: el guardián ya no aplica"
    ofensas = [
        f"{p.relative_to(_RAIZ)}:{n}"
        for p in _DOCS.rglob("*.md")
        for n, linea in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "curl" in linea and "19080/api/v1/tenants" in linea
    ]
    assert not ofensas, ofensas


# --------------------------------------------------------------------------- AREA-14-08..11


def test_adr_021_no_dice_que_el_dlq_de_notification_esta_pendiente() -> None:
    assert "/replay" in _leer("services/notification-service/app/routers/admin_deadletter.py")
    adr = "docs/es/adr/ADR-021-reclamacion-deadletter-eventos.md"
    assert not _contiene(adr, "notification-service (Incremento 2) queda pendiente")
    assert not _contiene(adr, "notification queda acoplado al Inc. 2")


def test_adr_006_no_dice_que_no_hay_barrera_dura() -> None:
    assert "def is_embeddable" in _leer("services/knowledge-service/app/core/clearance.py")
    assert not _contiene("docs/es/adr/ADR-006-proveedor-ia-configurable.md", "No hay barrera dura que lo impida")


def test_adr_018_no_promete_entregas_registradas_ni_deja_el_ssrf_como_evolutivo() -> None:
    assert "def validate_webhook_target" in _leer(
        "services/notification-service/app/core/webhook_security.py"
    )
    adr = "docs/es/adr/ADR-018-interoperabilidad-webhooks.md"
    assert not _contiene(adr, "el endurecimiento fino queda como evolutivo")
    # La afirmación «registrando entregas» solo sería cierta con una tabla de entregas.
    migraciones = " ".join(
        p.read_text(encoding="utf-8")
        for p in (_SERVICIOS / "notification-service" / "migrations").rglob("*.sql")
    )
    if "webhook_entregas" not in migraciones:
        assert not _contiene(adr, "y registrando entregas;")


def test_ningun_comentario_de_tsa_dice_stub_con_el_cliente_remoto_implementado() -> None:
    assert "httpx.post(" in _leer("services/signature-service/app/core/tsa.py")
    for rel in (
        "services/signature-service/app/core/tsa.py",
        "services/signature-service/app/core/config.py",
        "services/signature-service/app/schemas/firma.py",
    ):
        for n, linea in enumerate(_leer(rel).splitlines(), 1):
            if "stub" in linea.lower() and re.search(r"tsa|remot", linea, re.I):
                assert re.search(r"fue un stub|antes|ya no|Mientras", linea), f"{rel}:{n}: {linea.strip()}"
    for rel in ("docs/es/roadmap.md", "docs/es/adr/ADR-016-firma-electronica.md"):
        assert not _contiene(rel, "cliente de TSA remota (hoy stub)")
        assert not _contiene(rel, "stub delgado que falla explícito")


# --------------------------------------------------------------------------- AREA-14-12 / 14-14 / 14-15


def test_ni_el_catalogo_ni_el_repositorio_dicen_que_la_cofirma_entrega_no_tiene_escritor() -> None:
    assert "roles.add(\"entrega\")" in _leer("services/archive-service/app/services/transferencia_service.py")
    repo = _leer("services/archive-service/app/repositories/transferencia_repository.py")
    assert "hoy, a lo sumo" not in _plano(repo)
    # El COMMENT vigente es el de la ÚLTIMA migración que lo escribe (028 corrigió el de la 027).
    escriben = sorted(
        p
        for p in (_SERVICIOS / "archive-service" / "migrations" / "tenant").glob("*.sql")
        if "COMMENT ON COLUMN transferencia_acta_firma_personal.rol" in p.read_text(encoding="utf-8")
    )
    assert len(escriben) >= 2, "el censo de migraciones que comentan `rol` salió corto"
    vigente = escriben[-1].read_text(encoding="utf-8").split(
        "COMMENT ON COLUMN transferencia_acta_firma_personal.rol", 1
    )[1]
    assert "sin escritor" not in vigente


def test_los_comentarios_de_deuda_ya_cerrada_se_corrigieron() -> None:
    # B5 (ADR-032): la validación del destino de la regla pasó a resolverse por código.
    assert "_resolver_dependencia" in _leer("services/workflow-service/app/services/rules_service.py")
    dist = _plano(_leer("services/workflow-service/app/services/distribution.py"))
    assert "no se valida al crear/editar la regla" not in dist
    assert "def purge_for_disposition" in _leer("services/storage-service/app/services/storage_service.py")
    assert "es un roadmap item" not in _plano(_leer("services/storage-service/app/schemas/preservacion.py"))
    # El hecho que sostiene la frase vive en el árbol versionado y se exige SIEMPRE.
    assert (_SERVICIOS / "notification-service" / "app" / "jobs" / "retention.py").is_file()
    # El CLAUDE.md del servicio es infraestructura local (gitignored, symlink a
    # ~/Documents/claudeCode): no existe en CI ni, colgante, en un contenedor que no
    # monta ese directorio. Solo cuando es legible se le exige la frase; `is_file()` sigue
    # el symlink, así que un enlace colgante cuenta como ausente (nada que vigilar).
    claude_md = _SERVICIOS / "notification-service" / "CLAUDE.md"
    if claude_md.is_file():
        assert "app/jobs/retention.py" in claude_md.read_text(encoding="utf-8")


def test_oai_de_document_no_promete_un_fast_follow_que_ya_se_cerro() -> None:
    assert "level" in _leer("services/archive-service/app/services/oai_service.py")
    src = _leer("services/document-service/app/services/oai_service.py")
    assert "fast-follow" not in src and "monolvel" not in src


# --------------------------------------------------------------------------- AREA-14-13


def test_no_quedan_todo_de_funciones_que_ya_existen_en_bandeja_y_radicar() -> None:
    bandeja = _leer("frontend/src/routes/(app)/bandeja/+page.svelte")
    radicar = _leer("frontend/src/routes/(app)/radicar/+page.svelte")
    assert "TODO T-08b" not in bandeja
    assert "cuando exista un endpoint de asignación automática de flujo" not in radicar
    # El hecho que desmiente el TODO: los proxies de esas acciones existen.
    for accion in ("anulacion", "devolver", "respuesta", "vobo"):
        assert (_RAIZ / "frontend/src/routes/(app)/bandeja/api/[id]" / accion / "+server.js").is_file()


# --------------------------------------------------------------------------- AREA-14-19


def test_la_documentacion_de_registro_y_orquestacion_dice_lo_que_hay() -> None:
    hay_github = (_RAIZ / ".github").exists()
    registro = _leer("infra/DOCKER_REGISTRY.md")
    if not hay_github:
        for n, linea in enumerate(registro.splitlines(), 1):
            if "GitHub Actions" in linea or "ghcr.io" in linea:
                assert re.search(r"\b(no|ni|sin)\b", linea, re.I), f"DOCKER_REGISTRY.md:{n}: {linea}"
    hay_chart = any((_RAIZ / "infra").rglob("Chart.yaml")) or any((_RAIZ / "infra").rglob("kustomization.yaml")) or (_RAIZ / "Chart.yaml").exists()
    if not hay_chart:
        for rel in ("docs/es/deployment.md", "docs/en/deployment.md"):
            assert not _contiene(rel, "Kubernetes (recomendado)")
            assert not _contiene(rel, "Kubernetes (recommended)")


# --------------------------------------------------------------------------- AREA-11-05 (cifras del catálogo MCP)


def _catalogo_mcp() -> tuple[int, int, int, int]:
    """(tools, escrituras, irreversibles, con valor tecleado) leídos del AST de CATALOG."""
    import ast

    arbol = ast.parse(_leer("services/mcp-server/app/tools/catalog.py"))
    elts = next(
        n.value.elts  # type: ignore[union-attr]
        for n in arbol.body
        if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "CATALOG"
    )

    def _con(e: ast.expr, kw: str, valor: object = True) -> bool:
        return isinstance(e, ast.Call) and any(
            k.arg == kw and (valor is None or getattr(k.value, "value", None) is valor) for k in e.keywords
        )

    return (
        len(elts),
        sum(_con(e, "write") for e in elts),
        sum(_con(e, "destructive") for e in elts),
        sum(_con(e, "confirmar_con", None) for e in elts),
    )


def test_las_cifras_del_catalogo_mcp_en_api_md_son_las_del_codigo() -> None:
    tools, escrituras, irreversibles, tecleadas = _catalogo_mcp()
    assert tools > 150 and 0 < escrituras < tools and irreversibles > 0 and tecleadas > 0
    for rel in ("docs/es/api.md", "docs/en/api.md"):
        texto = _leer(rel)
        citas = re.findall(r"\*\*(\d+) tools\*\*", texto)
        assert citas, f"{rel}: no cita el total de tools (el guardián vigilaría el vacío)"
        assert set(citas) == {str(tools)}, f"{rel} cita {sorted(set(citas))} tools y el catálogo tiene {tools}"
        m = re.search(r"\*\*(\d+) tools, (\d+) (?:of them writes|de escritura)\*\*", texto)
        assert m, f"{rel}: falta la frase «N tools, M de escritura»"
        assert (int(m.group(1)), int(m.group(2))) == (tools, escrituras), rel
        assert f"**{irreversibles} " in texto and f"**{tecleadas} " in texto, (
            f"{rel}: irreversibles ({irreversibles}) o con valor tecleado ({tecleadas}) desactualizados"
        )
