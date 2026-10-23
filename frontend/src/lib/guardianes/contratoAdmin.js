// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * contratoAdmin.js — Reglas del contrato `ADMIN_AREA_PERMISSIONS` ↔ gates del backend.
 *
 * Función PURA: recibe los datos ya leídos (mapa, rutas del backend, endpoints que
 * consume cada área) y devuelve la lista de HALLAZGOS. Vacía = contrato cumplido.
 * Separada del test para poder MUTAR sus entradas (quitar un permiso del mapa,
 * añadir un área sin entrada, vaciar el parser) y comprobar que el guardián cae.
 *
 * Excepciones: clave `"<área> <MÉTODO> <ruta>"`, o `"* <MÉTODO> <ruta>"` para cubrir
 * la misma ruta en cualquier área (selectores compartidos). Siempre con motivo.
 *
 * Reglas:
 *   R1 carpetas ↔ mapa: toda carpeta de `admin/` está en el mapa y toda clave tiene
 *      carpeta; el permiso de cada tarjeta del índice es uno de los del mapa.
 *   R2 entrada satisfacible: cada lectura del `load` de un área exige un único
 *      permiso del área con `min_crud ≤ 1`; si no, el área deja entrar a quien solo
 *      verá 403 (o a nadie).
 *   R3 sin permiso muerto: cada permiso de la lista de un área gatea al menos un
 *      endpoint que el área consume.
 *   R4 resto de endpoints (proxies/acciones): todo gate ∈ lista del área.
 *   R5 fail-closed: ruta no encontrada, permiso decidido dentro del código o ruta
 *      sin gate ⇒ hallazgo. Solo una excepción CON MOTIVO lo apaga, y una excepción
 *      que ya no apaga nada también es hallazgo (no se acumulan indultos).
 */
import { buscarRuta } from "./gatesFastapi.js";

/** @typedef {{ area: string, tipo: "entrada" | "secundario", metodo: string, ruta: string, archivo: string, linea: number }} EndpointArea */

/**
 * @param {{
 *   mapa: Record<string, string[]>,
 *   defecto: string,
 *   carpetas: string[],
 *   tarjetas: { area: string, permiso: string }[],
 *   rutas: import("./gatesFastapi.js").RutaBackend[],
 *   endpoints: EndpointArea[],
 *   excepciones: Record<string, string>,
 *   niveles?: Record<string, Record<string, number>>,
 * }} datos
 * @returns {{ regla: string, clave: string, detalle: string }[]}
 */
export function evaluarContrato({
  mapa,
  defecto,
  carpetas,
  tarjetas,
  rutas,
  endpoints,
  excepciones,
  niveles = {},
}) {
  /** @type {{ regla: string, clave: string, detalle: string }[]} */
  const hallazgos = [];
  const usadas = new Set();
  /** Registra un hallazgo de endpoint salvo que lo cubra una excepción con motivo. */
  const reportar = (
    /** @type {string} */ regla,
    /** @type {EndpointArea} */ e,
    /** @type {string} */ detalle,
  ) => {
    const clave = `${e.area} ${e.metodo} ${e.ruta}`;
    // Clave exacta, o `* MÉTODO /ruta` para un selector compartido por varias áreas.
    for (const k of [clave, `* ${e.metodo} ${e.ruta}`]) {
      if ((excepciones[k] ?? "").trim() !== "") {
        usadas.add(k);
        return;
      }
    }
    hallazgos.push({ regla, clave, detalle: `${detalle} (${e.archivo}:${e.linea})` });
  };

  // R1
  for (const c of carpetas)
    if (!(c in mapa))
      hallazgos.push({
        regla: "R1",
        clave: c,
        detalle: `carpeta admin/${c} sin entrada en ADMIN_AREA_PERMISSIONS (hoy exigiría ${defecto} por defecto)`,
      });
  for (const k of Object.keys(mapa))
    if (!carpetas.includes(k))
      hallazgos.push({ regla: "R1", clave: k, detalle: `el mapa declara «${k}» sin carpeta` });
  for (const t of tarjetas)
    if (!(mapa[t.area] ?? [defecto]).includes(t.permiso))
      hallazgos.push({
        regla: "R1",
        clave: t.area,
        detalle: `la tarjeta del índice exige ${t.permiso}, que no está en el mapa (${(mapa[t.area] ?? [defecto]).join("|")})`,
      });

  /** @type {Record<string, Set<string>>} */
  const gateadosPorArea = {};
  for (const e of endpoints) {
    const lista = mapa[e.area] ?? [defecto];
    const ruta = buscarRuta(rutas, e.metodo, e.ruta);
    if (!ruta) {
      reportar("R5", e, `no se encontró ${e.metodo} ${e.ruta} en ningún router del backend`);
      continue;
    }
    if (
      ruta.routerDesconocido ||
      ruta.dinamico ||
      ruta.gates.some((g) => !g.permiso || g.minCrud === null)
    ) {
      reportar(
        "R5",
        e,
        `el gate de ${e.metodo} ${e.ruta} no es estático (${ruta.archivo}:${ruta.linea}): se decide dentro del código`,
      );
      continue;
    }
    if (ruta.gates.length === 0) {
      reportar(
        "R5",
        e,
        `${e.metodo} ${e.ruta} no tiene require_permission (${ruta.archivo}:${ruta.linea})` +
          (ruta.especiales.length ? ` salvo ${ruta.especiales.join(",")}` : ""),
      );
      continue;
    }
    const set = (gateadosPorArea[e.area] ??= new Set());
    for (const g of ruta.gates) for (const p of g.anyOf) set.add(p);

    if (e.tipo === "entrada") {
      // Una entrada entra con el nivel mínimo que el área fija para ese permiso (1 por defecto):
      // `preservacion` abre con USUA_PERM_ADMIN solo desde el nivel 3, el de los sellos de archivo.
      const ok = lista.some((p) =>
        ruta.gates.every(
          (g) => g.anyOf.includes(p) && (g.minCrud ?? 99) <= (niveles[e.area]?.[p] ?? 1),
        ),
      );
      if (!ok)
        reportar(
          "R2",
          e,
          `la entrada ${e.metodo} ${e.ruta} exige ${ruta.gates.map((g) => `${g.anyOf.join("|")}>=${g.minCrud}`).join(" + ")} (${ruta.archivo}:${ruta.linea}); el área admite ${lista.join("|")}`,
        );
    } else {
      // Any-of: el gate es «ajeno» solo si NINGUNO de sus brazos está en el área.
      const ajenos = ruta.gates.filter((g) => !g.anyOf.some((p) => lista.includes(p)));
      if (ajenos.length)
        reportar(
          "R4",
          e,
          `${e.metodo} ${e.ruta} exige ${ajenos.map((g) => `${g.anyOf.join("|")}>=${g.minCrud}`).join(" + ")} (${ruta.archivo}:${ruta.linea}), fuera de ${lista.join("|")}`,
        );
    }
  }

  // R3
  for (const area of Object.keys(mapa))
    for (const p of mapa[area])
      if (!gateadosPorArea[area]?.has(p))
        hallazgos.push({
          regla: "R3",
          clave: `${area} ${p}`,
          detalle: `el permiso ${p} del área «${area}» no gatea ningún endpoint que el área consuma`,
        });

  for (const [k, motivo] of Object.entries(excepciones)) {
    if (motivo.trim() === "")
      hallazgos.push({ regla: "R5", clave: k, detalle: "excepción sin motivo" });
    else if (!usadas.has(k))
      hallazgos.push({
        regla: "R5",
        clave: k,
        detalle: "excepción obsoleta: ya no apaga ningún hallazgo; bórrala",
      });
  }
  return hallazgos;
}

/**
 * Control positivo: sin él, «0 desajustes» puede significar «0 rutas leídas».
 * @param {import("./gatesFastapi.js").RutaBackend[]} rutas
 * @returns {string[]} problemas (vacío = el parser lee lo que debe)
 */
export function controlPositivo(rutas) {
  const problemas = [];
  if (rutas.length < 300) problemas.push(`solo ${rutas.length} rutas leídas (mínimo 300)`);
  const permisos = (/** @type {string} */ m, /** @type {string} */ r) =>
    rutas
      .filter((x) => x.metodo === m && x.ruta === r)
      .map((x) => x.gates.map((g) => g.permiso).join("+") || "(sin gate)");
  const casos = [
    ["POST", "/api/v1/trd", "USUA_PERM_TRD"], // gate en la firma / decorador
    ["GET", "/api/v1/audit", "USUA_PERM_ADMIN"], // gate a nivel de router
    ["GET", "/api/v1/tipos-documentales", "(sin gate)"], // lectura abierta
  ];
  for (const [m, r, esperado] of casos) {
    const vistos = permisos(m, r);
    if (!vistos.includes(esperado))
      problemas.push(`${m} ${r}: se esperaba ${esperado}, se leyó [${vistos.join(", ")}]`);
  }
  return problemas;
}
