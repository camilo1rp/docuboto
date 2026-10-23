// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/security-level — Proxy SSR para reclasificar el nivel de seguridad
 * de un radicado (D-05, RF-SEG-08, Ley 1712).
 *
 * POST { nivel_seguridad, motivo, [causal_reserva], [fundamento_juridico], [plazo_reserva_meses] }
 *   → PATCH /api/v1/documents/{id}/security-level
 *   → { id, tracking_number, nivel_seguridad }
 *
 * Allowlist explícita: solo los cinco campos del contrato (anti mass-assignment).
 * Desde el nivel 3 (clasificada), BAJAR el nivel responde 409
 * `desclasificacion_requiere_aprobacion` (ADR-039): el 409 pasa tal cual y la
 * pantalla ofrece la solicitud (`../desclasificacion`).
 * No-read-up: el 404 del backend es opaco — no distingue "no existe" de "clearance
 * insuficiente para leer", preservando la política del document-service (RF-SEG-08).
 * X-User-Id: trazabilidad del actor (D-05).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  // Allowlist: solo los cinco campos del contrato (anti mass-assignment).
  const { nivel_seguridad, motivo, causal_reserva, fundamento_juridico, plazo_reserva_meses } =
    body ?? {};

  // Validación server-side — nivel_seguridad
  const nivelInt = Number(nivel_seguridad);
  if (!nivel_seguridad || ![1, 2, 3].includes(nivelInt)) {
    return json(
      {
        error: "validation_error",
        detail: "El nivel de seguridad debe ser 1 (Pública), 2 (Reservada) o 3 (Clasificada).",
      },
      { status: 422 },
    );
  }

  // Validación server-side — motivo (acto de reserva motivado, Ley 1712)
  if (!motivo || typeof motivo !== "string" || !motivo.trim()) {
    return json(
      {
        error: "validation_error",
        detail: "El motivo de la reclasificación es obligatorio.",
      },
      { status: 422 },
    );
  }
  if (motivo.trim().length > 500) {
    return json(
      {
        error: "validation_error",
        detail: "El motivo no puede superar los 500 caracteres.",
      },
      { status: 422 },
    );
  }

  // Construir payload con los campos del contrato; normalizar opcionales vacíos → omitidos.
  /** @type {Record<string, unknown>} */
  const payload = {
    nivel_seguridad: nivelInt,
    motivo: motivo.trim(),
  };

  // La causal es del catálogo CERRADO (Ley 1712 arts. 18-19, ADR-039): se envía su
  // código tal cual y es el backend quien lo valida contra el catálogo (422).
  if (typeof causal_reserva === "string" && causal_reserva.trim()) {
    payload.causal_reserva = causal_reserva.trim().slice(0, 100);
  }
  if (typeof fundamento_juridico === "string" && fundamento_juridico.trim()) {
    payload.fundamento_juridico = fundamento_juridico.trim().slice(0, 300);
  }
  if (
    plazo_reserva_meses !== undefined &&
    plazo_reserva_meses !== null &&
    plazo_reserva_meses !== ""
  ) {
    // Art. 19: 1..180 meses (15 años, Ley 1712 art. 22). El art. 18 no lleva plazo y
    // el backend rechaza uno con 422; aquí solo se acota la forma.
    const plazo = Number(plazo_reserva_meses);
    if (!Number.isInteger(plazo) || plazo < 1 || plazo > 180) {
      return json(
        {
          error: "validation_error",
          detail: "El plazo de reserva debe ser un número entero de 1 a 180 meses.",
        },
        { status: 422 },
      );
    }
    payload.plazo_reserva_meses = plazo;
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/documents/${encodeURIComponent(params.id)}/security-level`, {
      method: "PATCH",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));

    // No-read-up: el 404 es opaco — el cliente NO puede distinguir "el radicado no existe"
    // de "tu clearance es insuficiente para leerlo" (preserva RF-SEG-08 del backend).
    if (res.status === 404) {
      return json(
        {
          error: "not_found",
          detail: "No fue posible reclasificar el radicado o no tiene acceso a él.",
        },
        { status: 404 },
      );
    }

    // 403 — no-write-up: clearance insuficiente para subir al nivel solicitado.
    if (res.status === 403) {
      return json(
        {
          error: "clearance_insuficiente",
          detail: mensajeError(
            data,
            "No tiene el nivel de acceso necesario para asignar ese nivel de seguridad.",
          ),
        },
        { status: 403 },
      );
    }

    return json(data, { status: res.status });
  } catch {
    return json(
      {
        error: "connection_error",
        detail: "No se pudo conectar al servidor de documentos.",
      },
      { status: 503 },
    );
  }
}
