// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin-gate-contrato.test.js — Guardián estático: `ADMIN_AREA_PERMISSIONS` ↔ gates reales.
 *
 * `adminGate.js` declara a mano qué permiso abre cada área de /admin, y el comentario
 * de su cabecera jura que es «el que su router backend exige DE VERDAD». Era una
 * afirmación sin comprobar: dos copias (el mapa y los `require_permission` de 87
 * routers) que nadie cruzaba. Este guardián las cruza leyendo la FUENTE de ambos
 * lados — no una fixture — así que no puede envejecer.
 *
 * Lee `services/*\/app/routers/*.py` (ver `$lib/guardianes/gatesFastapi.js`) y los
 * `+page.server.js` / `+server.js` de cada área (ver `frontAdminEndpoints.js`); las
 * reglas viven en `contratoAdmin.js`. Patrón de `pinar/pinar-paridad.test.js`.
 *
 * Si falla: el hallazgo dice qué área y qué ruta. O se corrige el mapa/la pantalla, o
 * el gate (backend), o —solo si es una decisión consciente— se declara en
 * `EXCEPCIONES` CON MOTIVO. No se borra el guardián ni se baja el mínimo de rutas.
 *
 * `process.getBuiltinModule` y no `node:fs`: la condición «browser» de Vitest rompe el
 * import (ver `error-de-carga-leido.test.js`).
 */
import { describe, it, expect } from "vitest";
import {
  ADMIN_AREA_MIN_LEVEL,
  ADMIN_AREA_PERMISSIONS,
  ADMIN_DEFAULT_PERMISSION,
} from "$lib/utils/adminGate.js";
import { extraerRutasFastapi } from "$lib/guardianes/gatesFastapi.js";
import { extraerEndpointsFront } from "$lib/guardianes/frontAdminEndpoints.js";
import { evaluarContrato, controlPositivo } from "$lib/guardianes/contratoAdmin.js";

const { readFileSync, readdirSync, existsSync } = process.getBuiltinModule("fs");
const { dirname, join, resolve } = process.getBuiltinModule("path");
const { fileURLToPath } = process.getBuiltinModule("url");

const AQUI = dirname(fileURLToPath(import.meta.url));
const SERVICIOS = resolve(AQUI, "../../../../../services");

/**
 * Hallazgos aceptados. Clave `"<área> <MÉTODO> <ruta>"` → motivo. Una excepción que
 * deja de apagar un hallazgo también falla: no se acumulan indultos.
 * Son DESAJUSTES CONOCIDOS entre el mapa/pantalla y el backend; el guardián los hace
 * visibles, no los corrige (cada uno se decide aparte: mapa del front o gate del back).
 */
const EXCEPCIONES = /** @type {Record<string, string>} */ ({
  // ── A. Lecturas SIN require_permission que el admin consume (selectores y catálogos) ──
  // Hoy cualquier usuario autenticado del tenant las lee (el tenant lo fija el JWT; la
  // escritura sí está gateada en cada área). Si alguna debe cerrarse, es decisión del
  // backend (fastapi-developer + tenant-security-auditor), no de este guardián.
  "* GET /dependencias":
    "Selector de organigrama: tenant-service/dependencias.py lo deja abierto (sin require_permission) porque lo usan radicación y flujos; la escritura exige USUA_PERM_ADMIN>=3.",
  "dependencias GET /dependencias/tree":
    "Árbol del organigrama: lectura abierta en tenant-service/dependencias.py, igual que su lista; la escritura exige USUA_PERM_ADMIN>=3.",
  "catalogos GET /catalogos":
    "Índice de catálogos: tenant-service/catalogos.py lo lee cualquier autenticado (alimenta selectores de radicación); la escritura exige USUA_PERM_ADMIN>=3.",
  "catalogos GET /catalogos/{}":
    "Ítems de un catálogo: lectura abierta en tenant-service/catalogos.py por el mismo motivo que su índice; la escritura exige USUA_PERM_ADMIN>=3.",
  "* GET /trd":
    "Lista de series TRD: archive-service/trd.py no gatea GET /trd (los POST/PATCH sí exigen USUA_PERM_TRD); la consume el selector de clasificación de radicación.",
  "* GET /tipos-documentales":
    "Tipos documentales: archive-service/tipos_documentales.py los lee cualquier autenticado (selector de radicación); su escritura sí está gateada.",
  "tvd GET /tvd":
    "Lista de TVD: archive-service/tvd.py no gatea la lectura (la escritura exige USUA_PERM_TRD); pendiente de decisión si debe cerrarse como la TRD de escritura.",
  "* GET /metadata/templates":
    "Plantillas de metadatos de documento: document-service/metadata.py lectura abierta (las usa el formulario de radicación); la escritura sí está gateada.",
  "* GET /metadata/elements":
    "Elementos de metadatos: document-service/metadata_elements.py lectura abierta (los usa el formulario de radicación); la escritura sí está gateada.",
  "* GET /expediente-metadata/templates":
    "Plantillas de metadatos de expediente: archive-service/metadata.py lectura abierta (las usa el formulario de expediente); la escritura sí está gateada.",
  "grupos GET /auth/security-levels":
    "Niveles de seguridad: auth-service/clearance.py los sirve a todo autenticado (catálogo estático del selector); asignar nivel a un grupo sí exige USUA_PERM_ADMIN.",
  "parametros GET /config/params":
    "Parámetros del tenant: tenant-service/config.py lectura abierta (la UI de radicación consulta límites y plazos); PUT/PATCH sí exigen USUA_PERM_ADMIN.",
  "parametros GET /config/asistente":
    "Bandera del asistente por entidad (ADR-037): tenant-service/config.py lectura abierta a todo el tenant (el diseño la clasifica como catálogo; la UI de cada persona ya la recibe por /auth/me); el PUT exige USUA_PERM_ADMIN>=3.",
  "parametros GET /config/params/{}":
    "Un parámetro del tenant: misma lectura abierta que la lista, tenant-service/config.py.",
  "parametros GET /config/holidays":
    "Festivos del tenant: tenant-service/config.py lectura abierta (los usa el cálculo de plazos); alta/baja sí exigen USUA_PERM_ADMIN.",
  "parametros GET /business-days/calculate":
    "Cálculo de días hábiles: tenant-service/config.py es una función pura abierta (la usa la radicación); no expone datos del admin.",

  // ── B. Gate de otro permiso que el del área, mitigado en la UI ──
  // (B6: correo/email/cuentas, users/pickable y plantillas[/campos] pasaron a gate any-of
  // {PERM_RADI, USUA_PERM_ADMIN} en el backend y el guardián lo lee; ya no son desajustes.)
  "preservacion POST /expedientes/{}/indice/firmar":
    "Firmar el índice exige PERM_FIRMA (archive-service/indice.py), no USUA_PERM_EXPEDIENTE (el permiso del área). No es un hallazgo abierto: la UI de preservación ya oculta el botón a quien no tiene AMBOS permisos (admin/preservacion/+page.svelte:89-93); el backend re-valida PERM_FIRMA.",

  "trd GET /trd/cuadro":
    "Cuadro de clasificación: archive-service/trd.py no gatea la lectura, igual que GET /trd (la escritura de la TRD sí exige USUA_PERM_TRD); lo consumen los selectores de clasificación. Cerrarlo es decisión del backend.",

  // ── C. Permiso decidido DENTRO del código (no verificable estáticamente) ──
  "plantillas GET /plantillas":
    "Gate any-of {PERM_RADI, USUA_PERM_ADMIN} en el decorador (document-service/borradores.py) y, con ?todas=true, check_permission(USUA_PERM_ADMIN) en el cuerpo: el guardián marca la ruta como dinámica por esa segunda comprobación. El administrador del área satisface las dos, así que ya no hay desajuste; solo queda el motivo de no ser estático.",
  "interoperabilidad GET /batch/documents/{}/status":
    "document-service/batch.py decide por check_permission(PERM_RADI, min_crud=3) en el cuerpo si el llamante ve lotes ajenos; el gate de entrada no es estático. Verificar a mano al tocar batch.py.",
  "interoperabilidad POST /batch/documents/{}/reprocesar":
    "document-service/batch.py decide en el cuerpo (check_permission) quién reprocesa un lote ajeno; gate no estático. Verificar a mano al tocar batch.py.",

  // ── D. Gate de alcance en el CUERPO (no es un hallazgo abierto) ──
  "flujos GET /workflows/pending":
    "Gate de alcance en el cuerpo, alcance y clearance en el WHERE (auditado 2026-10-11): workflow-service/workflow.py list_pending no lleva require_permission en el decorador, pero por defecto solo devuelve los pasos del propio llamante (_own_scope_condition, NULL fail-closed); ampliar a otra dependencia o usuario (dept_code/dept/assigned_to) exige PERM_TRAMITAR o USUA_PERM_ADMIN (_require_scope_widening_permission), y el clearance va en el WHERE compartido con el COUNT. Verificar a mano al tocar list_pending.",
});

function walk(dir, pred) {
  /** @type {string[]} */
  const out = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const r = join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name === "node_modules" || e.name === "tests") continue;
      out.push(...walk(r, pred));
    } else if (pred(r)) out.push(r);
  }
  return out;
}

function rutasBackend() {
  const out = [];
  for (const svc of readdirSync(SERVICIOS, { withFileTypes: true })) {
    const dir = join(SERVICIOS, svc.name, "app", "routers");
    if (!svc.isDirectory() || !existsSync(dir)) continue;
    for (const f of readdirSync(dir).filter((n) => n.endsWith(".py"))) {
      out.push(
        ...extraerRutasFastapi(readFileSync(join(dir, f), "utf8"), `${svc.name}/app/routers/${f}`),
      );
    }
  }
  return out;
}

function carpetas() {
  return readdirSync(AQUI, { withFileTypes: true })
    .filter((e) => e.isDirectory() && !e.name.startsWith("."))
    .map((e) => e.name);
}

function endpointsFront() {
  const out = [];
  for (const area of carpetas()) {
    const archivos = walk(join(AQUI, area), (f) =>
      /\/(\+page\.server\.js|\+server\.js|\+layout\.server\.js)$/.test(f),
    );
    for (const f of archivos) {
      const esLoad = f.endsWith("+page.server.js");
      const rel = f.slice(AQUI.length + 1);
      for (const e of extraerEndpointsFront(readFileSync(f, "utf8"), rel))
        out.push({ ...e, area, tipo: esLoad ? "entrada" : "secundario" });
    }
  }
  return out;
}

function tarjetasIndice() {
  const src = readFileSync(join(AQUI, "+page.svelte"), "utf8");
  return [
    ...src.matchAll(
      /href:\s*"\/admin\/([a-z]+)[^"]*",(?:\s|\/\/[^\n]*\n)*permission:\s*"([A-Z_]+)"/g,
    ),
  ].map((m) => ({ area: m[1], permiso: m[2] }));
}

const rutas = rutasBackend();
const endpoints = endpointsFront();
const datos = () => ({
  mapa: /** @type {Record<string, string[]>} */ (ADMIN_AREA_PERMISSIONS),
  defecto: ADMIN_DEFAULT_PERMISSION,
  carpetas: carpetas(),
  tarjetas: tarjetasIndice(),
  rutas,
  endpoints,
  excepciones: EXCEPCIONES,
  niveles: ADMIN_AREA_MIN_LEVEL,
});
const texto = (h) => h.map((x) => `[${x.regla}] ${x.clave}: ${x.detalle}`).join("\n");

describe("contrato ADMIN_AREA_PERMISSIONS ↔ gates del backend", () => {
  it("control positivo: el parser lee las rutas y acierta tres casos conocidos", () => {
    expect(controlPositivo(rutas)).toEqual([]);
  });

  it("el front consume endpoints en todas las áreas (el lector del front no está ciego)", () => {
    expect(endpoints.length).toBeGreaterThanOrEqual(60);
    for (const area of carpetas())
      expect(
        endpoints.some((e) => e.area === area),
        area,
      ).toBe(true);
  });

  it("no hay hallazgos sin excepción con motivo", () => {
    const h = evaluarContrato(datos());
    expect(texto(h), `\n${texto(h)}\n`).toBe("");
  });

  it("toda excepción lleva un motivo escrito", () => {
    for (const [k, m] of Object.entries(EXCEPCIONES))
      expect(m.trim().length, k).toBeGreaterThan(20);
  });

  describe("mutaciones: el guardián cae", () => {
    it("quitar un permiso del mapa (trd → USUA_PERM_ADMIN)", () => {
      const d = datos();
      d.mapa = { ...d.mapa, trd: ["USUA_PERM_ADMIN"] };
      expect(evaluarContrato(d).length).toBeGreaterThan(0);
    });

    it("añadir a un área un permiso que no gatea nada", () => {
      const d = datos();
      d.mapa = { ...d.mapa, preservacion: [...d.mapa.preservacion, "PERM_INEXISTENTE"] };
      expect(evaluarContrato(d).some((h) => h.regla === "R3")).toBe(true);
    });

    it("añadir un área (carpeta) sin entrada en el mapa", () => {
      const d = datos();
      d.carpetas = [...d.carpetas, "nueva-area"];
      expect(evaluarContrato(d).some((h) => h.regla === "R1" && h.clave === "nueva-area")).toBe(
        true,
      );
    });

    it("una clave del mapa sin carpeta", () => {
      const d = datos();
      d.mapa = { ...d.mapa, fantasma: ["USUA_PERM_ADMIN"] };
      expect(evaluarContrato(d).some((h) => h.clave === "fantasma")).toBe(true);
    });

    it("romper el parser (cero rutas) cae el control positivo y el contrato", () => {
      expect(controlPositivo([]).length).toBeGreaterThan(0);
      const d = datos();
      d.rutas = [];
      expect(evaluarContrato(d).length).toBeGreaterThan(0);
    });

    it("parser que ignora los gates de router: cae (audit)", () => {
      const ciego = rutas.map((r) => ({
        ...r,
        gates: r.gates.filter((g) => !g.origen.startsWith("router")),
      }));
      expect(controlPositivo(ciego).length).toBeGreaterThan(0);
    });

    it("una excepción que ya no apaga nada es hallazgo", () => {
      const d = datos();
      d.excepciones = {
        ...EXCEPCIONES,
        "trd GET /nada": "motivo suficientemente largo para pasar",
      };
      expect(evaluarContrato(d).some((h) => h.detalle.includes("obsoleta"))).toBe(true);
    });
  });
});
