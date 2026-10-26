// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/trd/api/disposicion/[expedienteId]/decidir — Proxy SSR (D-05).
 *
 * `POST /api/v1/trd/disposicion-vencida/{id}/decidir` (RF-TRD-06/08): registra
 * la decisión de un usuario autorizado sobre un vencimiento de disposición.
 *
 * **No reenvía el cuerpo tal cual.** `fase` y `decision` se validan contra
 * listas CERRADAS —lo que no está admitido no pasa— y la fecha del vencimiento
 * y el código de disposición NO se aceptan en absoluto: los resuelve el backend
 * desde el snapshot congelado. Si viajaran por aquí, confirmar una eliminación
 * sería cuestión de mandar la fecha adecuada.
 */
import { json } from "@sveltejs/kit";

import { API_V1 } from "$lib/utils/config.server.js";
import { headersFor } from "$lib/server/trdCircuitoProxy.server.js";

const FASES = new Set(["gestion", "central"]);
const DECISIONES = new Set(["confirmada", "aplazada"]);
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** @type {import('./$types').RequestHandler} */
export async function POST({ params, request, locals, fetch }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { expedienteId } = params;
  if (!UUID_RE.test(expedienteId ?? "")) {
    return json(
      { error: "validation_error", detail: "Identificador de expediente inválido." },
      { status: 400 },
    );
  }

  const body = await request.json().catch(() => ({}));
  const { fase, decision, motivo, revisar_de_nuevo_el } = body ?? {};

  if (!FASES.has(fase)) {
    return json({ error: "validation_error", detail: "Fase inválida." }, { status: 400 });
  }
  if (!DECISIONES.has(decision)) {
    return json({ error: "validation_error", detail: "Decisión inválida." }, { status: 400 });
  }

  /** @type {Record<string, unknown>} */
  const payload = { fase, decision };

  if (decision === "aplazada") {
    // Se exige aquí ADEMÁS de en el backend y en el CHECK de la migración: no
    // por desconfianza del backend, sino porque el mensaje que lee la persona
    // tiene que llegar antes que un 422 genérico, y porque un aplazamiento sin
    // motivo ni fecha de regreso es un olvido con mejor nombre.
    if (typeof motivo !== "string" || motivo.trim().length < 10) {
      return json(
        {
          error: "validation_error",
          detail:
            "Aplazar exige un motivo de al menos 10 caracteres: es lo que leerá quien retome el expediente.",
        },
        { status: 400 },
      );
    }
    if (typeof revisar_de_nuevo_el !== "string" || !DATE_RE.test(revisar_de_nuevo_el)) {
      return json(
        {
          error: "validation_error",
          detail: "Aplazar exige la fecha en que se vuelve a revisar (AAAA-MM-DD).",
        },
        { status: 400 },
      );
    }
    payload.motivo = motivo.trim();
    payload.revisar_de_nuevo_el = revisar_de_nuevo_el;
  } else if (typeof motivo === "string" && motivo.trim()) {
    // Al confirmar el motivo es opcional (suele remitir al acta del Comité).
    payload.motivo = motivo.trim();
  }

  const res = await fetch(
    `${API_V1}/trd/disposicion-vencida/${encodeURIComponent(expedienteId)}/decidir`,
    {
      method: "POST",
      headers: headersFor(locals, true),
      body: JSON.stringify(payload),
    },
  );
  const data = await res.json().catch(() => ({}));
  return json(data, { status: res.status });
}
