// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * bffProxyGuards.test.js — Guarda de autenticación de los proxies BFF (D-05).
 *
 * Cada `(app)/**\/api/+server.js` propaga el access_token al gateway leyéndolo
 * ÚNICAMENTE de `locals.accessToken` (poblado por hooks.server.js desde la
 * cookie httpOnly) — nunca del body/query del cliente. Estos tests llaman
 * directamente a los `RequestHandler` exportados con un `locals` sin token
 * y verifican que devuelven 401 ANTES de intentar contactar al gateway
 * (no se hace `fetch` real; si el handler intentara llamar a la red sin
 * `PUBLIC_API_URL`/gateway real, estos tests fallarían por timeout/red y
 * delatarían una regresión del guard).
 *
 * Se cubre una muestra representativa (no exhaustiva) de proxies de LECTURA
 * (bandeja, plantillas de radicación, firmas, borradores GET, búsqueda,
 * descarga de anexos) y, explícitamente, los proxies de ESCRITURA más
 * sensibles (anular, reclasificar nivel de seguridad, firmar/rechazar firma,
 * transferir expediente/transferencia, radicar) — donde conviven el guard de
 * sesión, el allowlist anti mass-assignment y acciones que exigen
 * confirmación explícita en la UI (D-05). Para los que leen body, se repite
 * el patrón de `borradores/api POST`: un `request.json()` que lanza si se
 * invoca, probando que el guard corre ANTES de tocar el body.
 */
import { describe, it, expect } from "vitest";

import { GET as bandejaGet } from "./(app)/bandeja/api/+server.js";
import { GET as templatesGet } from "./(app)/radicar/api/templates/+server.js";
import { GET as firmasGet } from "./(app)/firmas/api/+server.js";
import { GET as borradoresGet, POST as borradoresPost } from "./(app)/borradores/api/+server.js";
import { GET as busquedaGet } from "./(app)/busqueda/api/+server.js";
import { GET as descargaGet } from "./(app)/expedientes/[id]/api/download/[documentId]/[fileId]/+server.js";
import { GET as metadataTemplatesGet } from "./(app)/expedientes/api/metadata-templates/+server.js";

// Proxies de escritura — anular, reclasificar, firmar/rechazar, transferir, radicar.
import { POST as anulacionPost } from "./(app)/bandeja/api/[id]/anulacion/+server.js";
import { POST as securityLevelPost } from "./(app)/bandeja/api/[id]/security-level/+server.js";
import { POST as firmarSignPost } from "./(app)/firmas/api/[id]/sign/+server.js";
import { POST as firmarRejectPost } from "./(app)/firmas/api/[id]/reject/+server.js";
import { POST as expedienteTransferPost } from "./(app)/expedientes/[id]/api/transfer/+server.js";
import { POST as radicarDocumentsPost } from "./(app)/radicar/api/documents/+server.js";
import { POST as borradorRadicarPost } from "./(app)/borradores/api/[id]/radicar/+server.js";
import { POST as transferenciaEnviarPost } from "./(app)/transferencias/api/[id]/enviar/+server.js";
import { GET as anulacionEstadoGet } from "./(app)/anulaciones/api/[id]/+server.js";
import { POST as anulacionAprobarPost } from "./(app)/anulaciones/api/[id]/aprobar/+server.js";
import { POST as anulacionRechazarPost } from "./(app)/anulaciones/api/[id]/rechazar/+server.js";
import { GET as anulacionBuscarGet } from "./(app)/anulaciones/api/buscar/+server.js";

import { GET as desclasificacionColaGet } from "./(app)/desclasificaciones/api/cola/+server.js";
import { POST as desclasificacionAprobarPost } from "./(app)/desclasificaciones/api/[id]/aprobar/+server.js";
import { POST as desclasificacionRechazarPost } from "./(app)/desclasificaciones/api/[id]/rechazar/+server.js";
import { POST as desclasificacionRetirarPost } from "./(app)/desclasificaciones/api/[id]/retirar/+server.js";
import { POST as desclasificacionSolicitarPost } from "./(app)/bandeja/api/[id]/desclasificacion/+server.js";

const url = (path = "/") => new URL(`http://localhost${path}`);

/** locals sin sesión — simula una cookie ausente/expirada tras el hook. */
const NO_SESSION_LOCALS = {};

describe("Proxies BFF — 401 sin locals.accessToken (D-05)", () => {
  it("bandeja/api GET → 401 unauthenticated", async () => {
    const res = await bandejaGet({ locals: NO_SESSION_LOCALS, url: url("/bandeja/api") });
    expect(res.status).toBe(401);
    const body = await res.json();
    expect(body.error).toBe("unauthenticated");
  });

  it("radicar/api/templates GET → 401 unauthenticated", async () => {
    const res = await templatesGet({
      locals: NO_SESSION_LOCALS,
      url: url("/radicar/api/templates"),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("firmas/api GET → 401 unauthenticated", async () => {
    const res = await firmasGet({ locals: NO_SESSION_LOCALS, url: url("/firmas/api") });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("borradores/api GET → 401 unauthenticated", async () => {
    const res = await borradoresGet({ locals: NO_SESSION_LOCALS, url: url("/borradores/api") });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("borradores/api POST → 401 unauthenticated (el guard corre ANTES de leer el body)", async () => {
    // request.json() nunca debería invocarse: si el guard fallara y siguiera
    // de largo, este `request` fake reventaría al intentar parsear el body.
    const request = {
      json: () => {
        throw new Error("request.json() no debió invocarse sin sesión");
      },
    };
    const res = await borradoresPost({ locals: NO_SESSION_LOCALS, request });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("busqueda/api GET → 401 unauthenticated (antes de validar el query `q`)", async () => {
    // Sin `q` el handler devolvería 400 si llegara a la validación; el 401
    // debe ganar siempre porque el guard es lo primero que se evalúa.
    const res = await busquedaGet({ locals: NO_SESSION_LOCALS, url: url("/busqueda/api") });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("expedientes/[id]/api/download/[documentId]/[fileId] GET → 401 unauthenticated (antes del allowlist UUID)", async () => {
    const res = await descargaGet({
      locals: NO_SESSION_LOCALS,
      params: {
        id: "exp-1",
        documentId: "11111111-1111-1111-1111-111111111111",
        fileId: "22222222-2222-2222-2222-222222222222",
      },
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("un locals.accessToken vacío (string vacía) también es tratado como sin sesión", async () => {
    const res = await bandejaGet({
      locals: { accessToken: "" },
      url: url("/bandeja/api"),
    });
    expect(res.status).toBe(401);
  });

  it("expedientes/api/metadata-templates GET → 401 unauthenticated (antes de validar trd_serie_id)", async () => {
    // Sin `trd_serie_id` el handler devolvería 400 si llegara a la validación
    // del allowlist; el 401 debe ganar siempre porque el guard es lo primero.
    const res = await metadataTemplatesGet({
      locals: NO_SESSION_LOCALS,
      url: url("/expedientes/api/metadata-templates"),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("anulaciones/api/[id] GET → 401 unauthenticated (antes de validar el UUID)", async () => {
    const res = await anulacionEstadoGet({
      locals: NO_SESSION_LOCALS,
      params: { id: "no-es-un-uuid" },
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("anulaciones/api/buscar GET → 401 unauthenticated (antes de validar `q`)", async () => {
    const res = await anulacionBuscarGet({
      locals: NO_SESSION_LOCALS,
      url: url("/anulaciones/api/buscar"),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });
});

/**
 * `request` que revienta si se invoca `.json()` — usado en los proxies de
 * escritura que leen body, para probar que el guard 401 corre ANTES de tocar
 * el body del cliente (mismo patrón que `borradores/api POST` arriba).
 */
function requestThatMustNotBeRead() {
  return {
    json: () => {
      throw new Error("request.json() no debió invocarse sin sesión");
    },
  };
}

describe("Proxies BFF de ESCRITURA — 401 sin locals.accessToken (D-05)", () => {
  // Estos son los proxies de mayor impacto: anular un radicado, reclasificar
  // su nivel de seguridad (RF-SEG-08), firmar/rechazar una solicitud de firma,
  // transferir un expediente/transferencia y radicar — todas acciones que en
  // la UI exigen confirmación explícita antes de disparar el POST/PATCH. Que
  // cualquiera de estos handlers siguiera de largo sin sesión (o leyera el
  // body antes del guard) es el escenario de mayor riesgo.

  it("bandeja/api/[id]/anulacion POST → 401 (guard corre antes de leer el body)", async () => {
    const res = await anulacionPost({
      locals: NO_SESSION_LOCALS,
      params: { id: "rad-1" },
      request: requestThatMustNotBeRead(),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("bandeja/api/[id]/security-level POST → 401 (guard corre antes de leer el body)", async () => {
    const res = await securityLevelPost({
      locals: NO_SESSION_LOCALS,
      params: { id: "rad-1" },
      request: requestThatMustNotBeRead(),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("firmas/api/[id]/sign POST → 401 unauthenticated", async () => {
    const res = await firmarSignPost({
      locals: NO_SESSION_LOCALS,
      params: { id: "sol-1" },
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("firmas/api/[id]/reject POST → 401 (guard corre antes de leer el body)", async () => {
    const res = await firmarRejectPost({
      locals: NO_SESSION_LOCALS,
      params: { id: "sol-1" },
      request: requestThatMustNotBeRead(),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("expedientes/[id]/api/transfer POST → 401 unauthenticated", async () => {
    const res = await expedienteTransferPost({
      locals: NO_SESSION_LOCALS,
      params: { id: "exp-1" },
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("radicar/api/documents POST → 401 (guard corre antes de leer/sanear el body)", async () => {
    const res = await radicarDocumentsPost({
      locals: NO_SESSION_LOCALS,
      request: requestThatMustNotBeRead(),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("borradores/api/[id]/radicar POST → 401 unauthenticated", async () => {
    const res = await borradorRadicarPost({
      locals: NO_SESSION_LOCALS,
      params: { id: "bor-1" },
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("transferencias/api/[id]/enviar POST → 401 unauthenticated", async () => {
    const res = await transferenciaEnviarPost({
      locals: NO_SESSION_LOCALS,
      params: { id: "trf-1" },
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("anulaciones/api/[id]/aprobar POST → 401 (guard corre antes de leer el body)", async () => {
    const res = await anulacionAprobarPost({
      locals: NO_SESSION_LOCALS,
      params: { id: "rad-1" },
      request: requestThatMustNotBeRead(),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("anulaciones/api/[id]/rechazar POST → 401 (guard corre antes de leer el body)", async () => {
    const res = await anulacionRechazarPost({
      locals: NO_SESSION_LOCALS,
      params: { id: "rad-1" },
      request: requestThatMustNotBeRead(),
    });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });
});

describe("Proxies BFF de desclasificación (C-2, ADR-039) — 401 sin sesión", () => {
  const sinBody = {
    json: () => {
      throw new Error("request.json() no debió invocarse sin sesión");
    },
  };
  const params = { id: "11111111-1111-1111-1111-111111111111" };

  it("cola GET → 401", async () => {
    const res = await desclasificacionColaGet({ locals: NO_SESSION_LOCALS, url: url("/x") });
    expect(res.status).toBe(401);
  });

  it.each([
    ["aprobar", desclasificacionAprobarPost],
    ["rechazar", desclasificacionRechazarPost],
    ["retirar", desclasificacionRetirarPost],
    ["solicitar", desclasificacionSolicitarPost],
  ])("%s POST → 401 antes de leer el body", async (_n, handler) => {
    const res = await handler({ locals: NO_SESSION_LOCALS, params, request: sinBody });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });
});
