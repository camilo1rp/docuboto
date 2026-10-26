// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /archivo-fisico/api/ubicaciones — Proxy SSR de ubicaciones de archivo físico (D-05, E17).
 *
 * El token se lee de event.locals (cookie httpOnly). El JS del cliente nunca lo ve.
 *
 * GET  → reenvía a GET /ubicaciones?{parent_id (UUID), activo (bool)}
 * POST → valida (nivel req, codigo req, nombre req) y reenvía a POST /ubicaciones
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  // Saneamiento de parámetros
  const params = new URLSearchParams();
  const parentId = url.searchParams.get("parent_id") ?? "";
  const activo = url.searchParams.get("activo") ?? "";

  const soloRaiz = url.searchParams.get("solo_raiz") ?? "";

  if (parentId && UUID_RE.test(parentId)) params.set("parent_id", parentId);
  if (activo === "true" || activo === "false") params.set("activo", activo);
  // `solo_raiz` NO es equivalente a omitir `parent_id`: eso es "no filtres".
  if (soloRaiz === "true") params.set("solo_raiz", "true");

  const qs = params.toString();

  try {
    const res = await fetch(`${API_V1}/ubicaciones${qs ? `?${qs}` : ""}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servicio de archivo." },
      { status: 503 },
    );
  }
}

export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { nivel, codigo, nombre, parent_id, tipo_archivo, capacidad_ml } = body ?? {};

  const NIVELES_VALIDOS = [
    "sede",
    "edificio",
    "piso",
    "deposito",
    "modulo",
    "estante",
    "entrepano",
    "gaveta",
    "custodia_externa",
  ];

  if (!nivel || typeof nivel !== "string" || !nivel.trim()) {
    return json(
      { error: "validation_error", detail: "El campo 'nivel' es obligatorio." },
      { status: 422 },
    );
  }
  if (!NIVELES_VALIDOS.includes(nivel.trim())) {
    return json(
      {
        error: "validation_error",
        detail: `El campo 'nivel' debe ser uno de: ${NIVELES_VALIDOS.join(", ")}.`,
      },
      { status: 422 },
    );
  }
  if (!codigo || typeof codigo !== "string" || !codigo.trim()) {
    return json(
      { error: "validation_error", detail: "El campo 'codigo' es obligatorio." },
      { status: 422 },
    );
  }
  if (!nombre || typeof nombre !== "string" || !nombre.trim()) {
    return json(
      { error: "validation_error", detail: "El campo 'nombre' es obligatorio." },
      { status: 422 },
    );
  }

  const TIPOS_ARCHIVO = ["gestion", "central", "historico"];

  /** @type {Record<string, unknown>} */
  const payload = {
    nivel: nivel.trim(),
    codigo: codigo.trim(),
    nombre: nombre.trim(),
  };
  if (parent_id && typeof parent_id === "string" && UUID_RE.test(parent_id.trim())) {
    payload.parent_id = parent_id.trim();
  }
  if (tipo_archivo && TIPOS_ARCHIVO.includes(tipo_archivo)) {
    payload.tipo_archivo = tipo_archivo;
  }
  if (
    capacidad_ml !== undefined &&
    capacidad_ml !== null &&
    Number.isFinite(Number(capacidad_ml))
  ) {
    payload.capacidad_ml = Number(capacidad_ml);
  }

  try {
    const res = await fetch(`${API_V1}/ubicaciones`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({ ok: true }));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear la ubicación." },
      { status: 503 },
    );
  }
}
