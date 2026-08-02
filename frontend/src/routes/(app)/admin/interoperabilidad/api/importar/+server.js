// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/interoperabilidad/api/importar — Proxy SSR de importación interoperable
 * de radicados (E11, RF-INT-05) (D-05, Fase 8).
 *
 * POST /api/v1/import — cuerpo CRUDO (`Content-Type: application/zip`, sin
 * multipart, espejo exacto del router). El backend valida esquema + fixity
 * SHA-256 de CADA binario ANTES de ingerir, todo-o-nada:
 *   - 200 → resumen {import_uuid, importados, omitidos_existentes,
 *     omitidos_clearance, relaciones_reconstruidas, antecedentes_no_resueltos[]}
 *   - 422 → {error:"paquete_invalido", detail: "<qué archivo/checksum falló y por qué>"}
 *     Ese `detail` es la única explicación de fixity: se reenvía TAL CUAL,
 *     nunca se colapsa en un mensaje genérico "no se pudo importar" (Fase 8,
 *     punto crítico #1 — la UI debe mostrar CUÁL ítem falló y POR QUÉ).
 *
 * El cuerpo se retransmite en streaming (no se materializa el ZIP completo
 * en memoria del proceso SvelteKit) — el guard de tamaño (512 MiB) lo aplica
 * el backend.
 */

import { API_V1 } from "$lib/utils/config.server.js";
import { json } from "@sveltejs/kit";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  const contentType = request.headers.get("content-type") ?? "";
  if (
    !contentType.includes("application/zip") &&
    !contentType.includes("application/octet-stream")
  ) {
    return json(
      {
        error: "validation_error",
        detail: "El cuerpo debe ser un ZIP (Content-Type: application/zip).",
      },
      { status: 400 },
    );
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/zip",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  let res;
  try {
    // Node/undici exige `duplex: 'half'` para enviar un ReadableStream como
    // cuerpo de una petición saliente — sin materializar el ZIP en memoria.
    // `duplex` no está en el tipo `RequestInit` de lib.dom.d.ts todavía
    // (soporte de runtime real en Node ≥18, ver undici); se castea el init
    // a `any` únicamente para esta llamada.
    /** @type {any} */
    const init = {
      method: "POST",
      headers,
      body: request.body,
      duplex: "half",
    };
    res = await fetch(`${API_V1}/import`, init);
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de importación." },
      { status: 503 },
    );
  }

  const data = await res.json().catch(() => ({
    error: "upstream_error",
    detail: `HTTP ${res.status}`,
    status: res.status,
  }));
  return json(data, { status: res.status });
}
