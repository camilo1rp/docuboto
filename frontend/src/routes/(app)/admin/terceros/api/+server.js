// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/terceros/api — proxy SSR del directorio de terceros (RF-RAD-05).
 *
 * Un solo módulo con `accion` en la query en vez de cuatro rutas anidadas: las
 * cuatro llamadas de esta pantalla van al mismo recurso y comparten cabeceras,
 * y repartirlas en `[id]/radicados/+server.js`, `[id]/fusionar/+server.js`… era
 * cuatro veces el mismo preámbulo de token y tenant, que es donde se cuelan las
 * diferencias.
 *
 *   GET  ?accion=buscar&q=…          → lista del directorio
 *   GET  ?accion=duplicados          → grupos sospechosos
 *   GET  ?accion=radicados&id=…      → dónde aparece una ficha
 *   GET  ?accion=por_conciliar&page=&size=  → cola de fichas provisionales (C17)
 *   POST ?accion=fusionar&id=…       → consolidar (IRREVERSIBLE)
 *   POST ?accion=conciliar&id=…      → vincular / confirmar / descartar una provisional
 */

import { json } from "@sveltejs/kit";

import { API_V1 } from "$lib/utils/config.server.js";

const MEDIOS = [
  "presencial_documento",
  "respuesta_canal_registrado",
  "llamada_telefono_registrado",
  "otro",
];

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Cabeceras de la sesión, o `null` si no hay token.
 * @param {App.Locals} locals
 */
function contexto(locals) {
  const token = locals.accessToken ?? "";
  if (!token) return null;
  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  const userId = locals.user?.sub ?? "";
  if (userId) headers["X-User-Id"] = userId;
  return headers;
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url, fetch }) {
  const headers = contexto(locals);
  if (!headers) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const accion = url.searchParams.get("accion") ?? "buscar";
  let ruta;
  if (accion === "duplicados") {
    ruta = "/terceros/duplicados?limit=50";
  } else if (accion === "por_conciliar") {
    // Acotado aquí además de en el backend (size 1-100): un valor basura no viaja.
    const page = Math.max(1, Number.parseInt(url.searchParams.get("page") ?? "1", 10) || 1);
    const size = Math.min(
      100,
      Math.max(1, Number.parseInt(url.searchParams.get("size") ?? "20", 10) || 20),
    );
    ruta = `/terceros/por-conciliar?page=${page}&size=${size}`;
  } else if (accion === "radicados") {
    const id = url.searchParams.get("id") ?? "";
    if (!UUID_RE.test(id)) {
      return json(
        { error: "validation_error", detail: "Identificador inválido." },
        { status: 400 },
      );
    }
    ruta = `/terceros/${id}/radicados?limit=50`;
  } else {
    const q = (url.searchParams.get("q") ?? "").slice(0, 120);
    ruta = `/terceros?limit=100${q ? `&q=${encodeURIComponent(q)}` : ""}`;
  }

  try {
    const res = await fetch(`${API_V1}${ruta}`, { headers });
    const total = res.headers.get("X-Total-Count");
    return json(await res.json().catch(() => null), {
      status: res.status,
      ...(total ? { headers: { "X-Total-Count": total } } : {}),
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo consultar el directorio." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, url, request, fetch }) {
  const headers = contexto(locals);
  if (!headers) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const accion = url.searchParams.get("accion") ?? "";
  if (accion !== "fusionar" && accion !== "conciliar") {
    return json({ error: "accion_desconocida", detail: "Acción no admitida." }, { status: 400 });
  }

  const id = url.searchParams.get("id") ?? "";
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  if (accion === "conciliar") {
    const acto = body?.accion;
    const destino = body?.destino_id ?? null;
    const motivo = typeof body?.motivo === "string" ? body.motivo : null;
    const medio = body?.medio_verificacion ?? null;
    if (
      !UUID_RE.test(id) ||
      !["vincular", "confirmar", "descartar"].includes(acto) ||
      (destino !== null && !UUID_RE.test(String(destino))) ||
      (medio !== null && !MEDIOS.includes(medio))
    ) {
      return json(
        { error: "validation_error", detail: "Solicitud de conciliación inválida." },
        { status: 400 },
      );
    }
    try {
      const res = await fetch(`${API_V1}/terceros/${encodeURIComponent(id)}/conciliar`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        // Allowlist campo a campo: nada más del cuerpo del cliente llega al gateway.
        // `medio_verificacion` solo viaja si el cliente lo manda (vincular/confirmar):
        // el backend rechaza con 422 que llegue en «descartar».
        body: JSON.stringify({
          accion: acto,
          destino_id: destino,
          motivo,
          ...(medio !== null ? { medio_verificacion: medio } : {}),
        }),
      });
      return json(await res.json().catch(() => null), { status: res.status });
    } catch {
      return json(
        { error: "connection_error", detail: "No se pudo completar la conciliación." },
        { status: 503 },
      );
    }
  }
  // Allowlist: la superviviente va en la ruta y la absorbida en el cuerpo, y
  // nada más viaja. Reenviar el body íntegro dejaría que un cliente colara
  // campos que el backend no espera hoy pero podría aceptar mañana.
  const absorbe = body?.absorbe_id ?? "";
  if (!UUID_RE.test(id) || !UUID_RE.test(absorbe)) {
    return json(
      { error: "validation_error", detail: "Identificadores inválidos." },
      { status: 400 },
    );
  }

  try {
    const res = await fetch(`${API_V1}/terceros/${id}/fusionar`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ absorbe_id: absorbe }),
    });
    return json(await res.json().catch(() => null), { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo completar la fusión." },
      { status: 503 },
    );
  }
}
