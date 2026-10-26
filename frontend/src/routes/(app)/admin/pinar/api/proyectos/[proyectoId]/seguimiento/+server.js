// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Seguimiento de un proyecto — **append-only**: se registra el avance, nunca se
 * corrige. Un histórico que se puede reescribir no sirve para rendir cuentas.
 *
 * Solo con el plan `aprobado` o `en_ejecucion` (409 `plan_no_rastreable` en
 * otro caso): un plan en borrador todavía no compromete a nada, y uno cerrado ya
 * no admite movimiento.
 */
import { lectura, mutacion, UUID_RE } from "$lib/server/pinarProxy.server.js";

/** Historial del proyecto (`GET /pinar/proyectos/{id}/seguimiento`), de solo lectura. */
export const GET = lectura({
  path: (p) => (UUID_RE.test(p.proyectoId ?? "") ? `/proyectos/${p.proyectoId}/seguimiento` : null),
});

export const POST = mutacion({
  method: "POST",
  path: (p) => (UUID_RE.test(p.proyectoId ?? "") ? `/proyectos/${p.proyectoId}/seguimiento` : null),
  campos: ["fecha", "avance_pct", "observacion"],
  validar: (c) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(c.fecha ?? ""))) return "Indica la fecha del corte.";
    const avance = Number(c.avance_pct);
    if (!Number.isInteger(avance) || avance < 0 || avance > 100) {
      return "El avance va de 0 a 100.";
    }
    return null;
  },
});
