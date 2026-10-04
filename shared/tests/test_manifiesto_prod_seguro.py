# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Lo comprobable del checklist de seguridad de producción (AREA-13-11).

`docs/es/deployment.md` lleva un checklist manual. Parte de él ya se cumplía en
`infra/docker-compose.prod.yml` pero sin guardián, así que cualquier edición
podía deshacerlo sin que nada fallara. Aquí queda ejecutable lo que SE PUEDE
verificar en el repo; TLS, firewall y auditoría de PostgreSQL/Keycloak son del
host y se comprueban con `scripts/preflight-prod.sh`.

1. Ningún servicio publica `ports:` (el tráfico entra por el proxy inverso del
   host; la lista de admisión está vacía a propósito).
2. Redis exige contraseña (`--requirepass ${REDIS_PASSWORD:?}`).
3. Toda credencial es obligatoria (`${VAR:?}`) y ninguna tiene valor por
   defecto; las rotaciones vacías por diseño están declaradas.
4. Las imágenes de terceros llevan tag fijo o digest, nunca `latest` ni tag
   implícito.
"""

from __future__ import annotations

import re

from tests._compose import COMPOSE_PROD, servicios, servicios_propios

_CRED = re.compile(r"(PASSWORD|SECRET|KEY|TOKEN)")
_REF = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(:\?|:-|-|\?)?")

#: Referencias con valor por defecto que SÍ son legítimas, con su motivo.
DEFECTO_DECLARADO = {
    # Secreto ANTERIOR durante una rotación: vacío cuando no hay rotación en curso.
    # El vigente (MCP_PROPOSAL_SECRET) es `:?`.
    "MCP_PROPOSAL_SECRET_PREVIOUS",
    # Llavero versionado de webhooks: `{}` + versión 1 sin rotación; la clave
    # activa (WEBHOOK_SECRET_KEK) es `:?`.
    "WEBHOOK_SECRET_KEKS",
    "WEBHOOK_SECRET_KEK_ACTIVE_VERSION",
}


def _texto_sin_comentarios() -> str:
    return "\n".join(
        l for l in COMPOSE_PROD.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#")
    )


def test_se_leyeron_los_servicios() -> None:
    assert len(servicios(COMPOSE_PROD)) >= 15


def test_ningun_servicio_publica_puertos() -> None:
    con_puertos = {n: s["ports"] for n, s in servicios(COMPOSE_PROD).items() if s.get("ports")}
    assert not con_puertos, f"producción no publica puertos (entra por el proxy del host): {con_puertos}"


def test_redis_exige_contrasena() -> None:
    cmd = servicios(COMPOSE_PROD)["redis"].get("command")
    cmd = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    assert "--requirepass ${REDIS_PASSWORD:?" in cmd, f"Redis sin requirepass obligatorio: {cmd}"


def test_toda_credencial_es_obligatoria_y_sin_defecto() -> None:
    texto = _texto_sin_comentarios()
    ops: dict[str, set[str | None]] = {}
    for nombre, op in _REF.findall(texto):
        if _CRED.search(nombre):
            ops.setdefault(nombre, set()).add(op or None)
    assert len(ops) >= 20, f"solo {len(ops)} credenciales detectadas: guardián ciego"
    con_defecto = sorted(n for n, o in ops.items() if o & {":-", "-"} and n not in DEFECTO_DECLARADO)
    sin_obligatoria = sorted(
        n for n, o in ops.items() if ":?" not in o and n not in DEFECTO_DECLARADO
    )
    assert not con_defecto, f"credenciales con valor por defecto: {con_defecto}"
    assert not sin_obligatoria, (
        f"credenciales que nunca se exigen con `${{VAR:?}}` (arrancaría con vacío): {sin_obligatoria}"
    )


def test_los_defectos_declarados_siguen_existiendo() -> None:
    """Anti-caducidad: si una rotación desaparece, se quita de la lista."""
    texto = _texto_sin_comentarios()
    huerfanas = sorted(n for n in DEFECTO_DECLARADO if f"${{{n}:-" not in texto)
    assert not huerfanas, f"ya no tienen valor por defecto: {huerfanas}"


def test_imagenes_de_terceros_con_tag_fijo() -> None:
    propios = {*servicios_propios(), "frontend"}
    mal: list[str] = []
    terceros = 0
    for nombre, s in servicios(COMPOSE_PROD).items():
        if nombre in propios or nombre.startswith("migrate-"):
            continue
        imagen = str(s.get("image", ""))
        terceros += 1
        nombre_tag = imagen.split("@", 1)[0].rsplit("/", 1)[-1]
        if ":" not in nombre_tag and "@sha256:" not in imagen:
            mal.append(f"{nombre}: tag implícito (latest): {imagen}")
        elif nombre_tag.endswith(":latest"):
            mal.append(f"{nombre}: latest: {imagen}")
    assert terceros >= 4, "no se encontraron imágenes de terceros: guardián ciego"
    assert not mal, "\n  ".join(mal)
