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

1. Solo `edge` (nginx en contenedor, ADR-033) publica `ports:`, y solo 80 y 443:
   el tráfico entra por el proxy en contenedor; la lista de admisión es exacta.
2. Redis exige contraseña (`--requirepass ${REDIS_PASSWORD:?}`).
3. Toda credencial es obligatoria (`${VAR:?}`) y ninguna tiene valor por
   defecto; las rotaciones vacías por diseño están declaradas.
4. Las imágenes de terceros llevan tag Y digest (`@sha256:`), nunca `latest`
   ni tag implícito; los scripts de `infra/` tampoco admiten `:latest`.
5. `TRUSTED_PROXIES` por defecto no incluye el gateway de la red Docker, la
   plantilla de nginx cubre los tres hosts y `edge` los resuelve por alias.
6. `infra/.env.example` cubre todas las variables del manifiesto.
"""

from __future__ import annotations

import ipaddress
import re

from tests._compose import COMPOSE_PROD, RAIZ, cargar, servicios, servicios_propios

ENV_EJEMPLO = RAIZ / "infra" / ".env.example"
PLANTILLA_NGINX = RAIZ / "infra" / "nginx" / "orpyca.conf.template"

#: Único servicio que publica puertos, y exactamente cuáles (ADR-033).
PUERTOS_ADMITIDOS = {"edge": {"80:80", "443:443"}}

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
    """Lista de admisión EXACTA: solo `edge`, solo 80 y 443, sin IP de enlace."""
    con_puertos = {
        n: {str(p) for p in s["ports"]} for n, s in servicios(COMPOSE_PROD).items() if s.get("ports")
    }
    assert con_puertos == PUERTOS_ADMITIDOS, (
        f"producción solo publica {PUERTOS_ADMITIDOS} (entra por `edge`): {con_puertos}"
    )


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


def test_imagenes_de_terceros_con_tag_y_digest() -> None:
    """Tag legible Y digest: el tag solo se puede mover en el registro, el digest no."""
    propios = {*servicios_propios(), "frontend"}
    mal: list[str] = []
    terceros = 0
    for nombre, s in servicios(COMPOSE_PROD).items():
        if nombre in propios or nombre.startswith("migrate-"):
            continue
        imagen = str(s.get("image", ""))
        terceros += 1
        nombre_tag = imagen.split("@", 1)[0].rsplit("/", 1)[-1]
        if ":" not in nombre_tag:
            mal.append(f"{nombre}: tag implícito (latest): {imagen}")
        elif nombre_tag.endswith(":latest"):
            mal.append(f"{nombre}: latest: {imagen}")
        elif not re.search(r"@sha256:[0-9a-f]{64}$", imagen):
            mal.append(f"{nombre}: sin digest @sha256 (un tag se puede mover): {imagen}")
    assert terceros >= 5, "no se encontraron imágenes de terceros: guardián ciego"
    assert not mal, "\n  ".join(mal)


def test_los_scripts_de_infra_no_usan_latest() -> None:
    """Los defectos de imagen de backup/restore tampoco flotan (`amazon/aws-cli`)."""
    scripts = sorted((RAIZ / "infra").glob("*.sh"))
    assert scripts, "no se encontraron scripts en infra/: guardián ciego"
    mal: list[str] = []
    for f in scripts:
        for n, linea in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if linea.lstrip().startswith("#"):
                continue
            if ":latest" in linea:
                mal.append(f"{f.name}:{n}: {linea.strip()}")
            if "S3_CLI_IMAGEN:-" in linea and "@sha256:" not in linea:
                mal.append(f"{f.name}:{n}: imagen sin digest: {linea.strip()}")
    assert not mal, "\n  ".join(mal)


def _redes_de_confianza_por_defecto() -> list[ipaddress.IPv4Network]:
    entorno = servicios(COMPOSE_PROD)["api-gateway"]["environment"]
    valor = str(entorno["TRUSTED_PROXIES"])
    m = re.fullmatch(r"\$\{TRUSTED_PROXIES:-([^}]*)\}", valor)
    assert m and m.group(1), f"TRUSTED_PROXIES sin valor por defecto legible: {valor}"
    return [ipaddress.IPv4Network(c.strip()) for c in m.group(1).split(",") if c.strip()]


def test_trusted_proxies_excluye_el_gateway_de_la_red() -> None:
    """El gateway de la red (docker-proxy / anfitrión) no puede ser un salto de confianza.

    Si lo fuera, un cliente que llegara con esa IP haría que el gateway de la API
    saltara ese par y se quedara con la entrada de `X-Forwarded-For` forjada por
    el cliente, que acaba sellada en `audit_log`.
    """
    red = cargar(COMPOSE_PROD)["networks"]["orpycamcp-net"]["ipam"]["config"][0]
    gateway = ipaddress.IPv4Address(red["gateway"])
    subred = ipaddress.IPv4Network(red["subnet"])
    ip_range = ipaddress.IPv4Network(red["ip_range"])
    assert gateway in subred and gateway not in ip_range, "el gateway debe estar fuera de ip_range"
    confianza = _redes_de_confianza_por_defecto()
    dentro = [str(n) for n in confianza if gateway in n]
    assert not dentro, f"TRUSTED_PROXIES por defecto incluye al gateway {gateway}: {dentro}"
    fuera = [str(n) for n in confianza if not n.subnet_of(ip_range)]
    assert not fuera, f"TRUSTED_PROXIES por defecto confía fuera de ip_range {ip_range}: {fuera}"


def test_el_gateway_arranca_en_produccion_con_la_puerta_de_la_red_declarada() -> None:
    """ARC-B4: el manifiesto activa la comprobación y declara la IP correcta de la red."""
    entorno = servicios(COMPOSE_PROD)["api-gateway"]["environment"]
    assert entorno["APP_ENV"] == "production"
    gateway = cargar(COMPOSE_PROD)["networks"]["orpycamcp-net"]["ipam"]["config"][0]["gateway"]
    assert entorno["GATEWAY_RED_DOCKER_GATEWAY"] == gateway


def test_plantilla_nginx_cubre_los_tres_hosts() -> None:
    texto = PLANTILLA_NGINX.read_text(encoding="utf-8")
    sin_comentarios = "\n".join(l for l in texto.splitlines() if not l.lstrip().startswith("#"))
    for host in ("FRONTEND_HOSTNAME", "API_PUBLIC_HOSTNAME", "KEYCLOAK_HOSTNAME"):
        assert f"server_name ${{{host}}};" in sin_comentarios, f"sin `server` TLS para {host}"
    assert "proxy_pass http://127.0.0.1" not in sin_comentarios, (
        "proxy_pass a loopback: dentro del contenedor `edge` no hay nadie ahí"
    )


def _sin_comentarios_nginx() -> str:
    texto = PLANTILLA_NGINX.read_text(encoding="utf-8")
    return "\n".join(l for l in texto.splitlines() if not l.lstrip().startswith("#"))


def _servidor(host: str) -> str:
    """Cuerpo del bloque `server { ... }` cuyo `server_name` es `${host}` (sin anidar `}`)."""
    texto = _sin_comentarios_nginx()
    for m in re.finditer(r"^server \{\n(.*?)^\}$", texto, re.S | re.M):
        if f"server_name ${{{host}}};" in m.group(1) and "listen 443" in m.group(1):
            return m.group(1)
    raise AssertionError(f"sin server TLS para {host}")


def _locations(cuerpo: str) -> list[tuple[str, str, str]]:
    """[(modificador, ruta, cuerpo)] de los `location` del bloque."""
    return [
        (m.group(1) or "", m.group(2), m.group(3))
        for m in re.finditer(r"location (= )?(\S+) \{\n(.*?)\n    \}", cuerpo, re.S)
    ]


def _destino_nginx(cuerpo: str, ruta: str) -> str:
    """Qué `location` atiende `ruta` (prefijo más largo; `=` exacto gana) o `;`-rechazo."""
    if re.search(r'if \(\$uri ~ ";"\) \{\s*return 404;', cuerpo) and ";" in ruta:
        return "404-por-punto-y-coma"
    mejor = ("", "")
    for mod, prefijo, cuerpo_loc in _locations(cuerpo):
        if mod and ruta == prefijo:
            return f"{prefijo}:{'proxy' if 'proxy_pass' in cuerpo_loc else 'otro'}"
        if not mod and ruta.startswith(prefijo) and len(prefijo) > len(mejor[0]):
            mejor = (prefijo, cuerpo_loc)
    return f"{mejor[0]}:{'proxy' if 'proxy_pass' in mejor[1] else '404' if 'return 404' in mejor[1] else 'otro'}"


def test_keycloak_publica_por_admision_no_por_denegacion() -> None:
    """SEG-M1: solo el realm de la aplicación y los recursos; todo lo demás, 404.

    La denegación anterior (`/admin/`, `/realms/master/`) se esquivaba con
    `/realms/master;x/...` y con `/realms/master` sin barra final (200 en vivo).
    """
    cuerpo = _servidor("KEYCLOAK_HOSTNAME")
    rutas = {mod + ruta for mod, ruta, _ in _locations(cuerpo)}
    assert rutas == {"/realms/orpycamcp/", "/resources/", "/"}, f"lista de admisión inesperada: {rutas}"
    assert "deny all" not in cuerpo, "la lista de admisión no se apoya en denegaciones"
    for ruta in ("/realms/orpycamcp/protocol/openid-connect/auth", "/resources/abc/login/keycloak/css/a.css"):
        assert _destino_nginx(cuerpo, ruta).endswith(":proxy"), ruta
    for ruta in (
        "/realms/master",
        "/realms/master/",
        "/realms/master;x/protocol/openid-connect/token",
        "/realms/master/protocol/openid-connect/token",
        "/realms/orpycamcp",  # sin barra: no se admite (Keycloak no la necesita)
        "/realms/orpycamcp-otro/",
        "/realms/",
        "/admin/",
        "/admin/master/console/",
        "/metrics",
        "/health",
        "/",
    ):
        destino = _destino_nginx(cuerpo, ruta)
        assert destino in ("/:404", "404-por-punto-y-coma"), f"{ruta} llega a Keycloak ({destino})"
    assert _destino_nginx(cuerpo, "/realms/orpycamcp/;x") == "404-por-punto-y-coma"


def test_la_sonda_de_salud_solo_admite_el_propio_contenedor() -> None:
    """SEG-B1: la IP del gateway de la red Docker es la de cualquier cliente de internet."""
    cuerpo = _servidor("API_PUBLIC_HOSTNAME")
    [(_, _, sonda)] = [l for l in _locations(cuerpo) if l[1] == "/health/ready"]
    permitidas = re.findall(r"allow (\S+);", sonda)
    assert permitidas == ["127.0.0.1"], permitidas
    assert "deny  all;" in sonda or "deny all;" in sonda
    assert "172.20.0.1" not in _sin_comentarios_nginx()


def test_el_host_de_la_api_no_publica_metrics() -> None:
    """C18 (ADR-041): `/metrics` es de Prometheus por la red interna; desde internet, 404.

    Control positivo: `/api/v1/...` sigue al gateway (el bloque no se vació) y la sonda
    interna conserva su `location`. Mutación que debe caer: quitar los dos `location`
    de `/metrics` (entonces `/metrics` cae en `/` y se proxea al gateway).
    """
    cuerpo = _servidor("API_PUBLIC_HOSTNAME")
    assert _destino_nginx(cuerpo, "/api/v1/documents") == "/:proxy"
    assert _destino_nginx(cuerpo, "/health/ready") == "/health/ready:proxy"
    assert _destino_nginx(cuerpo, "/metrics") == "/metrics:otro"
    assert _destino_nginx(cuerpo, "/metrics/x") == "/metrics/:404"
    [(_, _, exacto)] = [l for l in _locations(cuerpo) if l[0] == "= " and l[1] == "/metrics"]
    assert "return 404" in exacto and "proxy_pass" not in exacto


def test_nginx_escucha_tambien_en_ipv6() -> None:
    """Sin `listen [::]` el anfitrión con IPv6 rechaza las conexiones de los clientes IPv6."""
    texto = _sin_comentarios_nginx()
    assert "listen [::]:80;" in texto
    assert texto.count("listen [::]:443 ssl;") == texto.count("listen 443 ssl;") == 3


def test_edge_alias_keycloak() -> None:
    """Los servicios llaman a https://KEYCLOAK_HOSTNAME: el alias lo resuelve a `edge`."""
    aliases = servicios(COMPOSE_PROD)["edge"]["networks"]["orpycamcp-net"]["aliases"]
    for host in ("KEYCLOAK_HOSTNAME", "FRONTEND_HOSTNAME", "API_PUBLIC_HOSTNAME"):
        assert f"${{{host}}}" in aliases, f"`edge` sin alias {host}: {aliases}"


def test_env_example_cubre_el_manifiesto() -> None:
    texto = _texto_sin_comentarios()
    refs: dict[str, set[str | None]] = {}
    for nombre, op in _REF.findall(texto):
        refs.setdefault(nombre, set()).add(op or None)
    assert len(refs) >= 40, f"solo {len(refs)} variables detectadas: guardián ciego"
    activas: set[str] = set()
    comentadas: set[str] = set()
    for linea in ENV_EJEMPLO.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(#\s*)?([A-Z][A-Z0-9_]*)=", linea)
        if m:
            (comentadas if m.group(1) else activas).add(m.group(2))
    ausentes = sorted(n for n in refs if n not in activas | comentadas)
    assert not ausentes, f"variables del manifiesto ausentes de infra/.env.example: {ausentes}"
    obligatorias = sorted(n for n, o in refs.items() if ":?" in o and n not in activas)
    assert not obligatorias, f"obligatorias (`:?`) que .env.example no trae ACTIVAS: {obligatorias}"
