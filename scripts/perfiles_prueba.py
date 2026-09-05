#!/usr/bin/env python3
# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Segundo factor y credencial de firma de los perfiles del entorno de pruebas.

## Por qué existe

`seed_demo.py` reparte permisos, nivel CRUD y clearance. Lo que **no** reparte es
lo que cuelga de la identidad de cada persona: su TOTP y su credencial de firma
PKI. Hasta ahora eso quedaba como lo dejara quien hubiera trasteado a mano.

Y esa diferencia fortuita fue lo único que destapó el peor defecto del recorrido
de septiembre de 2026: `jefe` era el único usuario con certificado **activo**, y
resultó que tener credencial de firma **impedía firmar cadenas** —el backend
exigía `totp_code` por la vía personal y la pantalla mandaba siempre OTP por
correo—. Nueve tests con mocks cubrían las dos ramas por separado; el defecto
vivía entre ellas.

Tras un `down -v` los cinco usuarios habrían quedado idénticos y esa rama habría
vuelto a ser invisible. **Un entorno de pruebas reproducible tiene que DISEÑAR
esas diferencias, no heredarlas.**

## El reparto, y por qué cada fila existe

Ninguna es decorativa: cada una ejercita un camino que ninguna otra toca.

| usuario      | TOTP | credencial  | camino exclusivo                                   |
|--------------|------|-------------|----------------------------------------------------|
| `operador`   | sí   | ninguna     | firma **nativa** (HMAC) con OTP-correo             |
| `jefe`       | sí   | **activa**  | firma **personal XAdES** y el gate `totp_requerido`|
| `secretaria` | no   | ninguna     | OTP-correo sin TOTP disponible                     |
| `archivista` | sí   | **revocada**| credencial revocada, que NO es lo mismo que ausente|
| `consulta`   | no   | ninguna     | control negativo: no firma nada                    |

`archivista` con la credencial **revocada** cubre un tercer estado que el código
distingue explícitamente de «sin certificado» (ver
`_resolve_personal_signing_status`, que separa *sin cert* de *canal caído*) y que
hasta ahora no ejercitaba ninguna prueba ni ningún recorrido.

El clearance de `secretaria` lo baja `seed_demo.py` a **2**: con solo los niveles
1 y 3 el no-read-up únicamente discrimina en un salto, y el peldaño intermedio es
donde se esconden los errores de comparación (`>` frente a `>=`).

## Los secretos se IMPRIMEN

Un TOTP que solo conoce el servidor no sirve para probar: quien recorre el
sistema necesita poder generar el código. El guion imprime el secreto base32 y el
`otpauth://` de cada usuario para cargarlo en una app autenticadora, y además
imprime el código vigente en ese instante.

Esto es aceptable **solo** porque es un entorno de desarrollo con credenciales ya
documentadas en el CLAUDE.md. Por eso el slug del tenant no es parametrizable,
igual que en `seed_demo.py`: apuntar esto a la entidad de un cliente repartiría
segundos factores conocidos.

## Uso

    # el stack tiene que estar levantado y el tenant sembrado
    python scripts/perfiles_prueba.py --gateway http://localhost:19080

Es idempotente: un TOTP ya activo o una credencial ya emitida se respetan y se
informan, no se rehacen. Volver a ejecutarlo tras un recorrido no rompe nada.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import os
import struct
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

RAIZ = Path(__file__).resolve().parents[1]

TENANT = "demo"
CLAVE = "orpycamcp_dev"

GATEWAY = "http://localhost:19080"
#: `internal/signing-key/activate` NO lo enruta el gateway (lleva
#: `X-Internal-Token`, no un bearer de persona): se llama al auth-service directo.
AUTH_DIRECTO = "http://localhost:19001"


class Fallo(RuntimeError):
    """Un fallo con un mensaje que dice qué hacer, no solo qué pasó."""


def paso(txt: str) -> None:
    print(f"\n▶ {txt}")


def ok(txt: str) -> None:
    print(f"   ✓ {txt}")


def aviso(txt: str) -> None:
    print(f"   · {txt}")


# ---------------------------------------------------------------------------
# TOTP (RFC 6238) sin dependencias
# ---------------------------------------------------------------------------
# No se añade `pyotp` al repositorio por seis líneas: una dependencia nueva se
# arrastra a todos los lockfiles y hay que justificarla. El algoritmo es
# HMAC-SHA1 sobre el contador de 30 segundos, y calcularlo aquí tiene además la
# ventaja de que si el servidor cambiara de parámetros (dígitos, ventana), este
# guion fallaría en vez de pasar en verde con un código que nadie comprueba.
def codigo_totp(secreto_b32: str, *, en: float | None = None, digitos: int = 6) -> str:
    """El código TOTP vigente para ese secreto."""
    clave = base64.b32decode(secreto_b32.upper() + "=" * (-len(secreto_b32) % 8))
    contador = int((en if en is not None else time.time()) // 30)
    mac = hmac.new(clave, struct.pack(">Q", contador), hashlib.sha1).digest()
    desplazamiento = mac[-1] & 0x0F
    valor = struct.unpack(">I", mac[desplazamiento : desplazamiento + 4])[0] & 0x7FFFFFFF
    return str(valor % (10**digitos)).zfill(digitos)


def secreto_de_uri(otpauth_uri: str) -> str:
    """Extrae el secreto base32 de un `otpauth://totp/...?secret=...`."""
    partes = urllib.parse.urlparse(otpauth_uri)
    params = urllib.parse.parse_qs(partes.query)
    secreto = (params.get("secret") or [""])[0]
    if not secreto:
        raise Fallo(
            f"El `otpauth_uri` que devolvió el enrolamiento no trae `secret`: {otpauth_uri!r}. "
            "Sin el secreto no se puede activar el TOTP ni dárselo a quien vaya a probar."
        )
    return secreto


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------
class Sesion:
    """El cliente habla como UNA persona concreta: su token, su tenant.

    Un solo token compartido no serviría: enrolar el TOTP y la credencial son
    actos sobre la identidad propia (`/auth/me/...`), así que cada perfil tiene
    que autenticarse de verdad — que además es lo que provoca su
    aprovisionamiento en `auth_users` si aún no existiera.
    """

    def __init__(self, cliente: httpx.AsyncClient, usuario: str) -> None:
        self._c = cliente
        self.usuario = usuario
        self._token = ""
        self.user_id = ""

    @property
    def cabeceras(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "X-Tenant-Slug": TENANT}

    async def entrar(self) -> None:
        r = await self._c.post(
            f"{GATEWAY}/api/v1/auth/token",
            json={"username": self.usuario, "password": CLAVE, "tenant_slug": TENANT},
        )
        if r.status_code != 200:
            raise Fallo(
                f"No se pudo autenticar como «{self.usuario}» ({r.status_code}). "
                "Ejecuta antes `python scripts/seed_demo.py`, que es quien crea los "
                "usuarios en Keycloak y los aprovisiona en el tenant."
            )
        self._token = r.json()["access_token"]
        me = await self.pedir("GET", "/api/v1/auth/me")
        # `user_id`, no `sub`: el de Keycloak y el de `auth_users` son distintos
        # y confundirlos ya rompió tres pantallas en este proyecto.
        self.user_id = str((me.json() or {}).get("user_id") or "")
        if not self.user_id:
            raise Fallo(
                f"`/auth/me` no devolvió `user_id` para «{self.usuario}»: sin él no se "
                "puede activar su certificado (la ruta interna lo exige en el body)."
            )

    async def pedir(self, metodo: str, ruta: str, **kw: Any) -> httpx.Response:
        """Una petición, reintentando si el gateway pide calma (429)."""
        for intento in range(5):
            r = await self._c.request(
                metodo, f"{GATEWAY}{ruta}", headers=self.cabeceras, **kw
            )
            if r.status_code != 429:
                return r
            await asyncio.sleep(1.5 * (intento + 1))
        return r


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------
async def asegurar_totp(s: Sesion) -> dict[str, str] | None:
    """Deja el TOTP de este usuario ACTIVO y devuelve su secreto.

    Idempotente: si ya está activo se respeta —re-enrolar sobre un TOTP activo
    exige step-up con un código vigente, y aquí no lo tenemos— y se devuelve
    `None` en el secreto, diciéndolo. Rehacerlo en silencio invalidaría el que
    alguien ya tuviera cargado en su autenticador.
    """
    estado = await s.pedir("GET", "/api/v1/auth/me/totp")
    if estado.status_code == 200 and (estado.json() or {}).get("estado") == "activo":
        return {"estado": "activo", "secreto": "", "uri": ""}

    # Cuerpo vacío, pero cuerpo: `TotpEnrollRequest` lleva sus dos campos de
    # step-up opcionales y FastAPI exige el objeto igualmente. Sin él son 422 con
    # un «Field required» sobre `body` que no dice cuál falta.
    r = await s.pedir("POST", "/api/v1/auth/me/totp/enroll", json={})
    if r.status_code not in (200, 201):
        raise Fallo(
            f"No se pudo enrolar el TOTP de «{s.usuario}» ({r.status_code}): {r.text[:200]}"
        )
    cuerpo = r.json()
    uri = cuerpo.get("otpauth_uri") or ""
    secreto = secreto_de_uri(uri)

    # El código se calcula para el instante actual. Si la activación cayera
    # justo en el cambio de ventana de 30 s el servidor lo rechazaría, así que
    # se reintenta con el código de la ventana siguiente en vez de dar por roto
    # el aprovisionamiento por un problema de reloj.
    for intento in range(3):
        r = await s.pedir(
            "POST",
            "/api/v1/auth/me/totp/activate",
            json={"code": codigo_totp(secreto)},
        )
        if r.status_code in (200, 201):
            return {"estado": "activo", "secreto": secreto, "uri": uri}
        if r.status_code != 401 or intento == 2:
            break
        await asyncio.sleep(31)  # esperar a la ventana siguiente
    raise Fallo(
        f"No se pudo activar el TOTP de «{s.usuario}» ({r.status_code}): {r.text[:200]}. "
        "Si es `codigo_invalido` repetido, comprueba que el reloj del contenedor de "
        "auth-service y el de esta máquina no difieran más de una ventana de 30 s."
    )


# ---------------------------------------------------------------------------
# Credencial de firma PKI
# ---------------------------------------------------------------------------
async def pedir_csr(s: Sesion, csr_dir: Path) -> Path:
    """Enrola la credencial y deja el CSR en disco para que lo firme el HOST.

    La emisión está partida en dos a propósito, y la frontera no es una molestia
    del entorno: es la que declara `gen-user-cert.sh`, *«NUNCA `$CA_DIR` se monta
    en ningún contenedor»*. La clave de la sub-CA de usuarios vive en el host y
    ahí se queda; lo que cruza es un CSR, que es público.

    Así queda el reparto:

      · aquí (contenedor)  enrolar → CSR
      · host (openssl)     firmar el CSR con la sub-CA
      · aquí (contenedor)  activar el certificado y, si toca, revocarlo

    El par RSA lo genera el **servidor** y la privada nunca sale de auth-service:
    esto solo pide el CSR.
    """
    # Igual que el enrolamiento de TOTP: el cuerpo va vacío pero tiene que ir.
    r = await s.pedir("POST", "/api/v1/auth/me/signing-key/enroll", json={})
    if r.status_code not in (200, 201):
        raise Fallo(
            f"No se pudo enrolar la credencial de «{s.usuario}» ({r.status_code}): "
            f"{r.text[:200]}. Recuerda que exige `PERM_FIRMA`."
        )
    csr_pem = r.json().get("csr_pem") or ""
    if not csr_pem:
        raise Fallo(f"El enrolamiento de «{s.usuario}» no devolvió `csr_pem`.")
    csr_dir.mkdir(parents=True, exist_ok=True)
    destino = csr_dir / f"{s.usuario}.csr"
    destino.write_text(csr_pem)
    # El `user_id` viaja al lado: la ruta interna de activación lo exige en el
    # body, y la fase que activa no vuelve a autenticarse como esta persona.
    (csr_dir / f"{s.usuario}.user_id").write_text(s.user_id)
    return destino


async def activar_certificado(usuario: str, user_id: str, cert_pem: str, *, token_interno: str) -> None:
    """Carga el certificado emitido offline sobre la credencial pendiente.

    Va al auth-service **directo**, no por el gateway: lleva `X-Internal-Token`
    y no el bearer de una persona, y el gateway no enruta esa ruta a propósito.
    """
    async with httpx.AsyncClient(timeout=30.0) as directo:
        r = await directo.post(
            f"{AUTH_DIRECTO}/internal/signing-key/activate",
            headers={"X-Internal-Token": token_interno, "X-Tenant-Slug": TENANT},
            json={"user_id": user_id, "cert_pem": cert_pem},
        )
    if r.status_code not in (200, 201):
        raise Fallo(
            f"No se pudo activar el certificado de «{usuario}» ({r.status_code}): "
            f"{r.text[:300]}"
        )


async def revocar_credencial(s: Sesion, secreto_totp: str) -> None:
    """Revoca la credencial propia (exige step-up TOTP).

    Es el estado que `archivista` aporta al entorno: una credencial **revocada**
    no es lo mismo que no tener ninguna, y el código lo distingue.
    """
    r = await s.pedir(
        "DELETE",
        "/api/v1/auth/me/signing-key",
        json={"step_up_totp_code": codigo_totp(secreto_totp)},
    )
    if r.status_code not in (200, 204):
        raise Fallo(
            f"No se pudo revocar la credencial de «{s.usuario}» ({r.status_code}): {r.text[:200]}"
        )


# ---------------------------------------------------------------------------
# El reparto
# ---------------------------------------------------------------------------
#: (usuario, quiere_totp, credencial) — `credencial` ∈ ninguna | activa | revocada.
#: Ver el docstring del módulo para por qué cada fila existe.
PERFILES: tuple[tuple[str, bool, str], ...] = (
    ("operador", True, "ninguna"),
    ("jefe", True, "activa"),
    ("secretaria", False, "ninguna"),
    ("archivista", True, "revocada"),
    ("consulta", False, "ninguna"),
)


#: El de `POST /internal/signing-key/activate`. El nombre es el que usa
#: `docker-compose.yml`, y el valor por defecto es el que ÉL declara para
#: desarrollo: si se escribiera otro aquí, el guion fallaría con un 401 en la
#: última fase y parecería un problema del certificado.
VAR_TOKEN = "SIGNING_KEY_INTERNAL_TOKEN"
TOKEN_DEV = "orpycamcp_signing_key_internal_dev"


def _token_interno() -> str:
    """El `X-Internal-Token` del despliegue: entorno, `.env`, o el default de dev."""
    valor = os.getenv(VAR_TOKEN) or ""
    if valor:
        return valor
    env = RAIZ / ".env"
    if env.exists():
        for linea in env.read_text().splitlines():
            if linea.startswith(f"{VAR_TOKEN}="):
                return linea.split("=", 1)[1].strip().strip("'\"")
    # El mismo default que `docker-compose.yml` aplica cuando la variable no
    # está: sin esto el guion exigiría configurar a mano algo que la pila ya
    # resuelve sola en desarrollo.
    return TOKEN_DEV


async def fase_enrolar(csr_dir: Path) -> list[dict[str, str]]:
    """Fase 1 (contenedor): TOTP de todos, y CSR de quien lleva credencial.

    Escribe en `csr_dir` lo que el host necesita para firmar, y devuelve el
    resumen con los secretos TOTP. El estado deliberado de cada perfil no se
    decide aquí: sale de `PERFILES`.
    """
    resumen: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=60.0) as cliente:
        for usuario, quiere_totp, credencial in PERFILES:
            paso(
                f"Perfil «{usuario}» — TOTP: {'sí' if quiere_totp else 'no'}, "
                f"credencial: {credencial}"
            )
            s = Sesion(cliente, usuario)
            await s.entrar()

            secreto = ""
            if quiere_totp:
                r = await asegurar_totp(s)
                secreto = (r or {}).get("secreto", "")
                if secreto:
                    ok(f"TOTP activado — secreto {secreto}")
                else:
                    aviso("TOTP ya estaba activo; se respeta el secreto existente")
            else:
                aviso("sin segundo factor a propósito (firmará por OTP-correo)")

            pide_csr = False
            if credencial in ("activa", "revocada"):
                estado = await s.pedir("GET", "/api/v1/auth/me/signing-key")
                ya = (estado.json() or {}).get("estado") if estado.status_code == 200 else None
                if ya == "activo" and credencial == "activa":
                    aviso("credencial ya activa; se respeta")
                else:
                    await pedir_csr(s, csr_dir)
                    ok("CSR emitido — lo firmará el host (la clave de la sub-CA no entra aquí)")
                    pide_csr = True

            resumen.append(
                {
                    "usuario": usuario,
                    "totp": secreto or ("activo (secreto previo)" if quiere_totp else "—"),
                    "credencial": credencial,
                    "pendiente": "sí" if pide_csr else "",
                }
            )
    return resumen


async def fase_activar(csr_dir: Path, secretos: dict[str, str]) -> None:
    """Fase 2 (contenedor): carga los certificados que firmó el host y revoca.

    La revocación va aquí y no en la fase 1 porque exige step-up TOTP **sobre
    una credencial ya activa**: revocar lo que aún no existe no es un estado, es
    un error. `archivista` termina con la credencial revocada, que es su aporte
    al entorno — un tercer estado distinto de «sin certificado».
    """
    token_interno = _token_interno()
    async with httpx.AsyncClient(timeout=60.0) as cliente:
        for usuario, _quiere_totp, credencial in PERFILES:
            crt = csr_dir / f"{usuario}.crt"
            if not crt.exists():
                continue
            uid_f = csr_dir / f"{usuario}.user_id"
            if not uid_f.exists():
                raise Fallo(
                    f"Está el certificado de «{usuario}» pero no su `user_id`. "
                    "La fase de enrolamiento no terminó: vuelve a ejecutarla."
                )
            paso(f"Activando la credencial de «{usuario}»")
            await activar_certificado(
                usuario, uid_f.read_text().strip(), crt.read_text(), token_interno=token_interno
            )
            ok("certificado activado")

            if credencial == "revocada":
                secreto = secretos.get(usuario, "")
                if not secreto:
                    raise Fallo(
                        f"«{usuario}» tiene que acabar con la credencial REVOCADA y eso "
                        "exige step-up TOTP, pero no tengo su secreto. Si su TOTP ya "
                        "estaba activo de antes, resetea el entorno para repartirlo de cero."
                    )
                s = Sesion(cliente, usuario)
                await s.entrar()
                await revocar_credencial(s, secreto)
                ok("credencial REVOCADA (estado deliberado de este perfil)")


def tarjeta(resumen: list[dict[str, str]]) -> None:
    """Lo que hace falta para recorrer el sistema, en una pantalla."""
    print("\n" + "═" * 78)
    print("  PERFILES DEL ENTORNO DE PRUEBAS — contraseña de todos: " + CLAVE)
    print("═" * 78)
    print(f"  {'usuario':<12} {'TOTP (secreto base32)':<36} {'credencial':<12} código ahora")
    print("  " + "─" * 74)
    for p in resumen:
        secreto = p["totp"]
        codigo = ""
        if secreto and secreto not in ("—",) and " " not in secreto:
            try:
                codigo = codigo_totp(secreto)
            except Exception:  # noqa: BLE001 — un secreto ilegible no debe tumbar la tarjeta
                codigo = "?"
        print(f"  {p['usuario']:<12} {secreto:<36} {p['credencial']:<12} {codigo}")
    print("═" * 78)
    print("  Los códigos TOTP caducan cada 30 s: carga el secreto en una app")
    print("  autenticadora, o vuelve a ejecutar este guion para ver el vigente.")
    print("  El OTP por correo de quien no tiene TOTP llega a MailHog:")
    print("      http://localhost:8025")
    print("═" * 78 + "\n")


def main() -> int:
    global GATEWAY, AUTH_DIRECTO
    p = argparse.ArgumentParser(
        description=(
            "Reparte TOTP y credencial de firma entre los perfiles del entorno de pruebas. "
            "Corre en DOS fases porque la clave de la sub-CA de usuarios no entra en ningún "
            "contenedor: `--enrolar` deja los CSR, el host los firma, `--activar` los carga."
        )
    )
    p.add_argument("--gateway", default=GATEWAY)
    p.add_argument("--auth", default=AUTH_DIRECTO, help="auth-service directo (ruta interna)")
    p.add_argument(
        "--csr-dir",
        default=str(RAIZ / "secrets" / "perfiles-csr"),
        help="Dónde se dejan los CSR y se recogen los .crt que firma el host",
    )
    p.add_argument("--enrolar", action="store_true", help="Fase 1: TOTP + CSR")
    p.add_argument("--activar", action="store_true", help="Fase 2: cargar certificados y revocar")
    p.add_argument(
        "--secretos",
        default="",
        help="Fase 2: `usuario=secreto` separados por coma (los imprime la fase 1)",
    )
    args = p.parse_args()
    GATEWAY = args.gateway.rstrip("/")
    AUTH_DIRECTO = args.auth.rstrip("/")
    csr_dir = Path(args.csr_dir)

    if not args.enrolar and not args.activar:
        print(
            "Indica una fase: --enrolar o --activar.\n"
            "Normalmente no se invocan a mano: `scripts/reset-entorno.sh` las encadena "
            "con la firma del host en medio.",
            file=sys.stderr,
        )
        return 2

    try:
        if args.enrolar:
            resumen = asyncio.run(fase_enrolar(csr_dir))
            tarjeta(resumen)
            # Los secretos, en una línea que el shell del host puede capturar y
            # devolver a la fase 2. No se escriben a disco: el TOTP de una
            # persona no tiene por qué quedar en un fichero cuando basta con
            # pasarlo entre dos pasos del mismo comando.
            pares = ",".join(
                f"{r['usuario']}={r['totp']}"
                for r in resumen
                if r["totp"] and " " not in r["totp"] and r["totp"] != "—"
            )
            print(f"SECRETOS_TOTP={pares}")
        if args.activar:
            secretos = dict(
                par.split("=", 1) for par in args.secretos.split(",") if "=" in par
            )
            asyncio.run(fase_activar(csr_dir, secretos))
    except Fallo as e:
        print(f"\n✗ {e}\n", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
