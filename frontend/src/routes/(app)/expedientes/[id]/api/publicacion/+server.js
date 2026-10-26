// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/publicacion — Proxy SSR del acto de publicación de
 * oficio del expediente (Ley 1712/2014 art. 12, D-05).
 *
 * Endpoint real: `POST /api/v1/expedientes/{id}/publicacion`
 * (`PERM_RECLASIFICAR`, `min_crud=3`) → `{ code, publicable, publicado_at }`.
 *
 * Por qué existe: la migración 042 puso `publicable` en `FALSE` por defecto, así
 * que sin esta ruta el esquema de publicación de expedientes quedaría
 * inejecutable salvo por SQL — el mismo defecto que ya se pagó una vez con los
 * radicados. El estado NO se lee aquí: viene en `ExpedienteResponse`, que la
 * página ya carga.
 *
 * Allowlist campo a campo: `publicar` (bool) y `fundamento` (str, 1..500). El
 * fundamento es obligatorio en AMBOS sentidos — retirar una publicación también
 * hay que poder explicarlo.
 *
 * No-read-up (RF-SEG-08): el backend responde el MISMO 404 neutro para «no
 * existe», «supera tu clearance» y «no es de nivel público». Este proxy preserva
 * esa opacidad en vez de inventar un mensaje que distinga los casos.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const FUNDAMENTO_MAX = 500;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { publicar, fundamento } = body ?? {};

  if (typeof publicar !== "boolean") {
    return json(
      {
        error: "validation_error",
        detail: "Debe indicarse si el acto publica o retira la publicación.",
      },
      { status: 422 },
    );
  }

  const texto = typeof fundamento === "string" ? fundamento.trim() : "";
  if (!texto) {
    return json(
      { error: "validation_error", detail: "El fundamento del acto es obligatorio." },
      { status: 422 },
    );
  }
  if (texto.length > FUNDAMENTO_MAX) {
    return json(
      {
        error: "validation_error",
        detail: `El fundamento no puede superar ${FUNDAMENTO_MAX} caracteres.`,
      },
      { status: 422 },
    );
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  const userId = locals.user?.sub ?? "";
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/expedientes/${encodeURIComponent(params.id)}/publicacion`, {
      method: "POST",
      headers,
      body: JSON.stringify({ publicar, fundamento: texto }),
    });
    if (res.status === 404) {
      return json(
        {
          error: "not_found",
          detail:
            "No fue posible encontrar el expediente, no tiene acceso a él, " +
            "o su nivel de acceso no admite el acto de publicación.",
        },
        { status: 404 },
      );
    }
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de archivo." },
      { status: 503 },
    );
  }
}
