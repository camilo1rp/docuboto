// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * anulaciones-page.test.js — Render de /anulaciones (+page.svelte, Fase 0
 * cierre desfase API↔UI + revisión UX Fase 0: A1-A5, M7-M9, M16-M17).
 *
 * Cubre:
 *  - Sin PERM_PANU_CODI: EmptyState de "sin permiso", ninguna tabla/buscador,
 *    y el mensaje ya NO expone el código de permiso interno (M16).
 *  - Con PERM_PANU_CODI: buscador + banner de degradación siempre visible.
 *  - A4: el estado vacío de "sin resultados" no afirma que no hay pendientes.
 *  - A5b: tras buscar, cada fila resuelve su propio estado de anulación en
 *    paralelo (columna "Anulación") y el botón "Ver anulación" SOLO aparece
 *    donde hay algo que ver — no en las filas sin solicitud (404).
 *  - Flujo de búsqueda → ver anulación → decidir: el motivo es OBLIGATORIO,
 *    no se llama al proxy de decisión si el textarea está vacío.
 *  - A1: aprobar usa la variante de alerta (rojo) y rechazar la conservadora
 *    — el color ya no contradice la gravedad del acto.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";

const { default: AnulacionesPage } = await import("./+page.svelte");

/**
 * Monta la pantalla con el `data` del `load`.
 *
 * El componente NO da un valor por defecto a `data` a propósito: en la
 * aplicación siempre lo entrega SvelteKit, y un default aquí convertiría un
 * fallo de cableado en una pantalla que se pinta vacía sin quejarse. Los tests
 * lo pasan explícitamente, que además les deja declarar el estado de la cola.
 */
function renderPagina({ items = [], total = null, colaError = null } = {}) {
  return render(AnulacionesPage, {
    props: {
      data: { cola: { items, total: total ?? items.length }, colaError },
    },
  });
}

const { initSession, clearSession } = await import("$lib/stores/auth.js");

function sessionWith({ permissions = [] } = {}) {
  return {
    sub: "keycloak-sub-1",
    // Identidad INTERNA del tenant: es la que el backend escribe en
    // `solicitado_por`, y con la que se compara. No es el `sub`.
    user_id: "u-1",
    email: "aprobador@orpyca.com",
    name: "Aprobadora",
    preferred_username: "aprobadora",
    roles: [],
    tenant_slug: "icetex",
    permissions,
  };
}

describe("/anulaciones — sin PERM_PANU_CODI", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: [] }));
  });

  it("muestra un EmptyState explicando la falta de permiso, sin buscador ni tabla", () => {
    renderPagina();
    expect(screen.getByText(/sin permiso de aprobación de anulaciones/i)).toBeTruthy();
    expect(screen.queryByLabelText(/número de radicado o texto del asunto/i)).toBeNull();
  });

  it("M16: no expone el código de permiso interno al usuario final", () => {
    renderPagina();
    expect(screen.queryByText(/PERM_PANU_CODI/)).toBeNull();
    expect(
      screen.getByText(/no tiene asignado el permiso de aprobación de anulaciones/i),
    ).toBeTruthy();
  });
});

describe("/anulaciones — con PERM_PANU_CODI", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["PERM_PANU_CODI"] }));
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("muestra la COLA de pendientes y conserva el buscador", async () => {
    renderPagina({
      items: [
        {
          id: "sol-1",
          radicado_id: "rad-1",
          estado: "solicitada",
          causal: "error_digitacion",
          motivo: "duplicado",
          solicitado_por: "u-9",
          solicitado_at: "2026-08-01T10:00:00Z",
          tracking_number: "2026-DEMO-S-000012",
          subject: "Respuesta a solicitud",
          doc_type: "S",
          radicado_status: "radicado",
        },
      ],
      total: 1,
    });

    // La cola dice de QUÉ habla: sin número y asunto habría que abrir la ficha.
    expect(screen.getByText("2026-DEMO-S-000012")).toBeTruthy();
    expect(screen.getByText("Respuesta a solicitud")).toBeTruthy();
    // Y el buscador NO se retira: sigue siendo la vía directa cuando alguien
    // llega con un número de radicado en la mano.
    expect(screen.getByLabelText(/número de radicado o texto del asunto/i)).toBeTruthy();
  });

  it("«Resolver» consulta el id del RADICADO, no el de la solicitud", async () => {
    // Trampa real de esta pantalla: una fila de la cola trae DOS identificadores
    // —`id` (la solicitud) y `radicado_id`— y el detalle se consulta por el del
    // radicado. Pasar la fila tal cual usaría el equivocado sin que nada fallara
    // a la vista: el proxy devolvería 404 y la pantalla diría «no tiene ninguna
    // anulación registrada» sobre una solicitud que sí existe.
    const pedidas = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (u) => {
        pedidas.push(String(u));
        return { ok: true, status: 200, json: async () => ({ estado: "solicitada" }) };
      }),
    );

    renderPagina({
      items: [
        {
          id: "SOLICITUD-999",
          radicado_id: "RADICADO-111",
          estado: "solicitada",
          causal: "error_digitacion",
          motivo: null,
          solicitado_por: "u-9",
          solicitado_at: "2026-08-01T10:00:00Z",
          tracking_number: "2026-DEMO-S-000012",
          subject: "Respuesta",
          doc_type: "S",
          radicado_status: "radicado",
        },
      ],
      total: 1,
    });

    await fireEvent.click(screen.getByRole("button", { name: /resolver/i }));

    await waitFor(() => expect(pedidas.length).toBeGreaterThan(0));
    expect(pedidas.some((u) => u.includes("RADICADO-111"))).toBe(true);
    expect(pedidas.some((u) => u.includes("SOLICITUD-999"))).toBe(false);
  });

  it("la cola vacía NO se confunde con no haber podido consultarla", async () => {
    renderPagina({ items: [], total: 0 });
    expect(screen.getByText(/no hay anulaciones pendientes/i)).toBeTruthy();
  });

  it("un fallo al consultar la cola lo dice, y aclara que no significa que no haya", async () => {
    renderPagina({ colaError: { detail: "El servidor no respondió.", status: 502 } });
    expect(screen.getByText(/no se pudo consultar la cola/i)).toBeTruthy();
    expect(screen.getByText(/no significa que no haya solicitudes pendientes/i)).toBeTruthy();
  });

  it("sin permiso para la cola lo dice sin fingir que está vacía", async () => {
    renderPagina({ colaError: { detail: "forbidden", status: 403 } });
    expect(screen.getByText(/sin permiso para ver la cola/i)).toBeTruthy();
    expect(screen.queryByText(/no hay anulaciones pendientes/i)).toBeNull();
  });

  it("buscar con menos de 2 caracteres no llama al proxy y muestra el error de validación", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;
    renderPagina();

    await fireEvent.input(screen.getByLabelText(/número de radicado o texto del asunto/i), {
      target: { value: "a" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));

    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText(/escribe al menos 2 caracteres/i)).toBeTruthy();
  });

  it("A4: sin resultados, el estado vacío NO afirma que no hay anulaciones pendientes", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ results: [], total: 0 }),
    }));
    globalThis.fetch = fetchMock;
    renderPagina();

    await fireEvent.input(screen.getByLabelText(/número de radicado o texto del asunto/i), {
      target: { value: "sin coincidencias" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));

    await waitFor(() =>
      expect(screen.getByText(/ningún radicado coincide con la búsqueda/i)).toBeTruthy(),
    );
    // La afirmación falsa que motivó A4 no debe aparecer en ninguna forma.
    expect(screen.queryByText(/no hay nada por resolver/i)).toBeNull();
    expect(screen.getByText(/esto no significa que no haya anulaciones pendientes/i)).toBeTruthy();
  });

  // C3 (crítico, revisión UX): regresión de "un fallo de red se lee como
  // 'no hay nada que resolver'". Antes, un 503 del proxy caía en el mismo
  // `catch` que "cero resultados" y pintaba el EmptyState de "Ningún
  // radicado coincide" — indistinguible de una búsqueda legítima sin
  // coincidencias. Un aprobador que busca un radicado del que le avisaron
  // por teléfono leería eso como "la solicitud no existe" y cerraría el
  // caso: justo lo que el banner de esta pantalla dice combatir.
  it("C3: un fallo de red al buscar NO se confunde con 'sin resultados' — estado de error persistente con reintento", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 503,
      json: async () => ({ error: "connection_error", detail: "Gateway no disponible." }),
    }));
    globalThis.fetch = fetchMock;
    renderPagina();

    await fireEvent.input(screen.getByLabelText(/número de radicado o texto del asunto/i), {
      target: { value: "2026-ICETEX-E-000999" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));

    // El estado de error tiene su PROPIO título — nunca el de "sin resultados".
    await waitFor(() =>
      expect(screen.getByText(/no se pudo consultar el servidor de búsqueda/i)).toBeTruthy(),
    );
    expect(screen.queryByText(/ningún radicado coincide con la búsqueda/i)).toBeNull();
    expect(screen.getByRole("button", { name: /reintentar/i })).toBeTruthy();
  });

  it("C3: tras un fallo, Reintentar vuelve a llamar al proxy con la misma búsqueda", async () => {
    let call = 0;
    const fetchMock = vi.fn(async () => {
      call += 1;
      if (call === 1) {
        return { ok: false, status: 503, json: async () => ({ error: "connection_error" }) };
      }
      return { ok: true, json: async () => ({ results: [], total: 0 }) };
    });
    globalThis.fetch = fetchMock;
    renderPagina();

    await fireEvent.input(screen.getByLabelText(/número de radicado o texto del asunto/i), {
      target: { value: "2026-ICETEX-E-000999" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    // Título del EmptyState de error (sin punto final) — único, a diferencia
    // del mensaje de `searchError`, que además aparece bajo el campo y (sin
    // `err.detail`) coincide textualmente con la descripción del EmptyState.
    await waitFor(() =>
      expect(screen.getByText("No se pudo consultar el servidor de búsqueda")).toBeTruthy(),
    );

    await fireEvent.click(screen.getByRole("button", { name: /reintentar/i }));

    // La segunda llamada SÍ resuelve — y ahora el estado vacío legítimo
    // (distinto del de error) es el que se muestra.
    await waitFor(() =>
      expect(screen.getByText(/ningún radicado coincide con la búsqueda/i)).toBeTruthy(),
    );
    expect(screen.queryByText("No se pudo consultar el servidor de búsqueda")).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("A3/menor: el resumen de la tabla dice el TOTAL real (searchTotal), no solo lo cargado, y el paginador no ofrece 'Siguiente' inalcanzable", async () => {
    const manyResults = Array.from({ length: 20 }, (_, i) => ({
      id: `22222222-2222-2222-2222-${String(i).padStart(12, "0")}`,
      tracking_number: `2026-ICETEX-E-0001${i}`,
      doc_type: "E",
      subject: `Radicado ${i}`,
    }));
    const fetchMock = vi.fn(async (input) => {
      const u = String(input);
      if (u.startsWith("/anulaciones/api/buscar")) {
        return { ok: true, json: async () => ({ results: manyResults, total: 87 }) };
      }
      // Columna "Anulación" por fila: ninguna tiene solicitud (404 normal).
      return { ok: false, status: 404, json: async () => ({ error: "not_found" }) };
    });
    globalThis.fetch = fetchMock;
    renderPagina();

    await fireEvent.input(screen.getByLabelText(/número de radicado o texto del asunto/i), {
      target: { value: "contrato" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));

    // Menor (revisión UX): antes la tabla decía "1–20 de 20" (contradiciendo
    // el párrafo "primeros 20 de 87") — ahora el total real se dice UNA sola
    // vez, en el resumen de la propia tabla.
    await waitFor(() => expect(screen.getByText(/1.20 de 87/)).toBeTruthy());
    expect(screen.getByText(/hay más resultados de los que se muestran aquí/i)).toBeTruthy();
    // Sin paginación real conectada (el backend siempre trae página 1, size
    // 20): no debe ofrecerse un botón "Página siguiente" que no hace nada.
    expect(screen.queryByRole("button", { name: /página siguiente/i })).toBeNull();
  });

  it("A5b: resuelve el estado por fila en paralelo — 'Ver anulación' solo aparece donde hay solicitud", async () => {
    const ROW_CON_SOLICITUD = "11111111-1111-1111-1111-111111111111";
    const ROW_SIN_SOLICITUD = "44444444-4444-4444-4444-444444444444";
    const fetchMock = vi.fn(async (input) => {
      const u = String(input);
      if (u.startsWith("/anulaciones/api/buscar")) {
        return {
          ok: true,
          json: async () => ({
            results: [
              {
                id: ROW_CON_SOLICITUD,
                tracking_number: "2026-ICETEX-E-000123",
                doc_type: "E",
                subject: "Contrato de arrendamiento",
              },
              {
                id: ROW_SIN_SOLICITUD,
                tracking_number: "2026-ICETEX-E-000456",
                doc_type: "S",
                subject: "Oficio de respuesta",
              },
            ],
            total: 2,
          }),
        };
      }
      if (u.startsWith(`/anulaciones/api/${ROW_CON_SOLICITUD}`)) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            id: "a-1",
            radicado_id: ROW_CON_SOLICITUD,
            estado: "solicitada",
            causal: "ERROR_RADICACION",
            motivo: "Se radicó por error",
            solicitado_por: "u-2",
            solicitado_at: "2026-07-01T10:00:00Z",
            decidido_por: null,
            decidido_at: null,
            observacion: null,
          }),
        };
      }
      if (u.startsWith(`/anulaciones/api/${ROW_SIN_SOLICITUD}`)) {
        return { ok: false, status: 404, json: async () => ({ error: "not_found" }) };
      }
      // El refresco de la cola se dispara tras decidir; no es el objeto de
      // estos tests, pero sin esta rama la guarda lo convierte en un fallo.
      if (u.startsWith("/anulaciones/api/cola")) {
        return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) };
      }
      throw new Error(`fetch no esperado: ${u}`);
    });
    globalThis.fetch = fetchMock;

    renderPagina();

    await fireEvent.input(screen.getByLabelText(/número de radicado o texto del asunto/i), {
      target: { value: "contrato" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));

    await waitFor(() => expect(screen.getByText("Pendiente de decisión")).toBeTruthy());
    await waitFor(() => expect(screen.getByText("Sin solicitud")).toBeTruthy());

    // Solo la fila CON solicitud ofrece "Ver anulación".
    expect(
      screen.getByRole("button", { name: /ver anulación del radicado 2026-icetex-e-000123/i }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /ver anulación del radicado 2026-icetex-e-000456/i }),
    ).toBeNull();
  });

  // Menor (revisión UX): si la consulta de estado de UNA fila falla, el chip
  // decía "No se pudo consultar" pero antes no había ningún botón — ni
  // reintento ni acceso al detalle — para esa fila.
  it("menor: una fila cuyo estado falló ofrece 'Reintentar' (no queda sin botón ni acceso)", async () => {
    const ROW_ID = "55555555-5555-5555-5555-555555555555";
    let rowCalls = 0;
    const fetchMock = vi.fn(async (input) => {
      const u = String(input);
      if (u.startsWith("/anulaciones/api/buscar")) {
        return {
          ok: true,
          json: async () => ({
            results: [
              {
                id: ROW_ID,
                tracking_number: "2026-ICETEX-E-000789",
                doc_type: "E",
                subject: "Radicado con backend inestable",
              },
            ],
            total: 1,
          }),
        };
      }
      if (u.startsWith(`/anulaciones/api/${ROW_ID}`)) {
        rowCalls += 1;
        if (rowCalls === 1) {
          return { ok: false, status: 500, json: async () => ({ error: "internal_error" }) };
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            id: "a-9",
            radicado_id: ROW_ID,
            estado: "solicitada",
            causal: "ERROR_RADICACION",
            motivo: "m",
            solicitado_por: "u-2",
            solicitado_at: "2026-07-01T10:00:00Z",
            decidido_por: null,
            decidido_at: null,
            observacion: null,
          }),
        };
      }
      // El refresco de la cola se dispara tras decidir; no es el objeto de
      // estos tests, pero sin esta rama la guarda lo convierte en un fallo.
      if (u.startsWith("/anulaciones/api/cola")) {
        return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) };
      }
      throw new Error(`fetch no esperado: ${u}`);
    });
    globalThis.fetch = fetchMock;

    renderPagina();

    await fireEvent.input(screen.getByLabelText(/número de radicado o texto del asunto/i), {
      target: { value: "inestable" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));

    await waitFor(() => expect(screen.getByText("No se pudo consultar")).toBeTruthy());
    const retryBtn = screen.getByRole("button", {
      name: /reintentar consultar la anulación del radicado 2026-icetex-e-000789/i,
    });
    expect(retryBtn).toBeTruthy();

    await fireEvent.click(retryBtn);

    await waitFor(() => expect(screen.getByText("Pendiente de decisión")).toBeTruthy());
    expect(
      screen.getByRole("button", { name: /ver anulación del radicado 2026-icetex-e-000789/i }),
    ).toBeTruthy();
  });

  it("busca, ve el estado de una anulación pendiente y exige motivo antes de aprobar", async () => {
    const fetchMock = vi.fn(async (input) => {
      const u = String(input);
      if (u.startsWith("/anulaciones/api/buscar")) {
        return {
          ok: true,
          json: async () => ({
            results: [
              {
                id: "11111111-1111-1111-1111-111111111111",
                tracking_number: "2026-ICETEX-E-000123",
                doc_type: "E",
                subject: "Contrato de arrendamiento",
              },
            ],
            total: 1,
          }),
        };
      }
      if (u.startsWith("/anulaciones/api/11111111-1111-1111-1111-111111111111")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            id: "a-1",
            radicado_id: "11111111-1111-1111-1111-111111111111",
            estado: "solicitada",
            causal: "ERROR_RADICACION",
            motivo: "Se radicó por error",
            solicitado_por: "u-2",
            solicitado_at: "2026-07-01T10:00:00Z",
            decidido_por: null,
            decidido_at: null,
            observacion: null,
          }),
        };
      }
      // El refresco de la cola se dispara tras decidir; no es el objeto de
      // estos tests, pero sin esta rama la guarda lo convierte en un fallo.
      if (u.startsWith("/anulaciones/api/cola")) {
        return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) };
      }
      throw new Error(`fetch no esperado: ${u}`);
    });
    globalThis.fetch = fetchMock;

    renderPagina();

    await fireEvent.input(screen.getByLabelText(/número de radicado o texto del asunto/i), {
      target: { value: "contrato" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));

    await waitFor(() => expect(screen.getByText("2026-ICETEX-E-000123")).toBeTruthy());

    // A5b: "Ver anulación" solo aparece una vez resuelto el estado de la fila.
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /ver anulación del radicado 2026-icetex-e-000123/i }),
      ).toBeTruthy(),
    );
    await fireEvent.click(
      screen.getByRole("button", { name: /ver anulación del radicado 2026-icetex-e-000123/i }),
    );

    await waitFor(() => expect(screen.getByText("ERROR_RADICACION")).toBeTruthy());

    // A1: aprobar es el acto irreversible → variante de alerta (danger), no
    // la "tranquilizadora" (primary). Rechazar es el conservador (secondary).
    const aprobarDrawerBtn = screen.getByRole("button", { name: "Aprobar" });
    const rechazarDrawerBtn = screen.getByRole("button", { name: "Rechazar" });
    expect(aprobarDrawerBtn.className).toContain("op-btn--danger");
    expect(rechazarDrawerBtn.className).toContain("op-btn--secondary");

    // Abre el modal de aprobación desde el footer del drawer.
    await fireEvent.click(aprobarDrawerBtn);

    const confirmBtn = screen.getByRole("button", { name: "Confirmar aprobación" });
    expect(confirmBtn.className).toContain("op-btn--danger");
    const callsBeforeConfirm = fetchMock.mock.calls.length;

    // Sin motivo: no debe llamar al proxy de decisión.
    await fireEvent.click(confirmBtn);
    expect(screen.getByText(/el motivo es obligatorio/i)).toBeTruthy();
    expect(fetchMock.mock.calls.length).toBe(callsBeforeConfirm);
  });

  it("A2: tras aprobar con éxito, el foco vuelve al título de la página (no se pierde en <body>)", async () => {
    const RAD_ID = "11111111-1111-1111-1111-111111111111";
    const fetchMock = vi.fn(async (input, init) => {
      const u = String(input);
      if (u.startsWith("/anulaciones/api/buscar")) {
        return {
          ok: true,
          json: async () => ({
            results: [
              {
                id: RAD_ID,
                tracking_number: "2026-ICETEX-E-000123",
                doc_type: "E",
                subject: "Contrato de arrendamiento",
              },
            ],
            total: 1,
          }),
        };
      }
      if (u.startsWith(`/anulaciones/api/${RAD_ID}/aprobar`) && init?.method === "POST") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ id: "a-1", estado: "aprobada" }),
        };
      }
      if (u.startsWith(`/anulaciones/api/${RAD_ID}`)) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            id: "a-1",
            radicado_id: RAD_ID,
            estado: "solicitada",
            causal: "ERROR_RADICACION",
            motivo: "Se radicó por error",
            solicitado_por: "u-2",
            solicitado_at: "2026-07-01T10:00:00Z",
            decidido_por: null,
            decidido_at: null,
            observacion: null,
          }),
        };
      }
      // El refresco de la cola se dispara tras decidir; no es el objeto de
      // estos tests, pero sin esta rama la guarda lo convierte en un fallo.
      if (u.startsWith("/anulaciones/api/cola")) {
        return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) };
      }
      throw new Error(`fetch no esperado: ${u}`);
    });
    globalThis.fetch = fetchMock;

    renderPagina();

    await fireEvent.input(screen.getByLabelText(/número de radicado o texto del asunto/i), {
      target: { value: "contrato" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /ver anulación del radicado 2026-icetex-e-000123/i }),
      ).toBeTruthy(),
    );
    await fireEvent.click(
      screen.getByRole("button", { name: /ver anulación del radicado 2026-icetex-e-000123/i }),
    );
    await waitFor(() => expect(screen.getByText("ERROR_RADICACION")).toBeTruthy());

    await fireEvent.click(screen.getByRole("button", { name: "Aprobar" }));
    await fireEvent.input(screen.getByLabelText(/motivo de la decisión/i), {
      target: { value: "Radicado duplicado por error de digitación." },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Confirmar aprobación" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Resolver anulaciones" })).toBe(
        document.activeElement,
      ),
    );
  });
});

describe("/anulaciones — quien la pidió no la aprueba", () => {
  /**
   * Segregación de funciones. La pantalla de solicitud ya promete «aprobación
   * posterior por un supervisor», pero en la revisión por navegador el mismo
   * usuario solicitó y aprobó seguido, y el radicado quedó `anulado` sin que
   * nadie más lo mirara. El backend ahora responde 409
   * `aprobacion_del_propio_solicitante`; aquí se comprueba la otra mitad: que
   * el límite se DECLARE antes, en vez de descubrirse fallando.
   */
  const originalFetch = globalThis.fetch;

  /** Búsqueda con una solicitud pendiente cuyo solicitante se controla. */
  function backendCon(solicitadoPor) {
    globalThis.fetch = vi.fn(async (url) => {
      const u = String(url);
      if (u.includes("/anulaciones/api/buscar")) {
        return {
          ok: true,
          json: async () => ({
            results: [
              {
                id: "rad-1",
                tracking_number: "2026-DEMO-S-000012",
                doc_type: "S",
                subject: "Respuesta a solicitud",
                anulacion: { estado: "solicitada" },
              },
            ],
            total: 1,
          }),
        };
      }
      return {
        ok: true,
        json: async () => ({
          id: "anu-1",
          radicado_id: "rad-1",
          estado: "solicitada",
          causal: "ANU-ERR",
          motivo: "Error de radicación",
          solicitado_por: solicitadoPor,
          solicitado_at: "2026-08-19T20:03:59Z",
          decidido_por: null,
          decidido_at: null,
          observacion: null,
        }),
      };
    });
  }

  /** Abre el detalle de la anulación encontrada. */
  async function abrirDetalle() {
    renderPagina();
    await fireEvent.input(screen.getByLabelText(/número de radicado o texto del asunto/i), {
      target: { value: "2026-DEMO-S-000012" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    const ver = await screen.findByRole("button", { name: /ver anulación/i });
    await fireEvent.click(ver);
  }

  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["PERM_PANU_CODI"] }));
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("si la pedí yo, no se ofrece Aprobar y se explica quién debe hacerlo", async () => {
    backendCon("u-1"); // el mismo `sub` de la sesión
    await abrirDetalle();

    await waitFor(() => expect(screen.getByText(/debe aprobarla otra persona/i)).toBeTruthy());
    expect(screen.queryByRole("button", { name: /^aprobar$/i })).toBeNull();
  });

  it("y se dice la vía que SÍ queda abierta: rechazarla es desistir", async () => {
    // Un «no puede» sin alternativa deja el trámite parado sin decir cómo seguir.
    backendCon("u-1");
    await abrirDetalle();

    await waitFor(() => expect(screen.getByText(/puede rechazarla usted mismo/i)).toBeTruthy());
    expect(screen.getByRole("button", { name: /rechazar/i })).toBeTruthy();
  });

  it("si la pidió OTRA persona, Aprobar sigue estando", async () => {
    // Control positivo: la corrección no puede consistir en retirar el acto.
    backendCon("otro-usuario");
    await abrirDetalle();

    await waitFor(() => expect(screen.getByRole("button", { name: /^aprobar$/i })).toBeTruthy());
    expect(screen.queryByText(/debe aprobarla otra persona/i)).toBeNull();
  });

  it("sin identidad en sesión, «Aprobar» NO se oculta", async () => {
    // El backend rechaza la aprobación propia con 409 y su motivo. Ocultar el
    // botón cuando no se sabe quién es uno convertiría un caso raro —sesión
    // sin `user_id`— en un bloqueo total de una acción legítima: nadie podría
    // resolver ninguna anulación.
    clearSession();
    const sesion = sessionWith({ permissions: ["PERM_PANU_CODI"] });
    delete sesion.user_id;
    initSession(sesion);
    backendCon("u-1");
    await abrirDetalle();

    await waitFor(() => expect(screen.getByRole("button", { name: /^aprobar$/i })).toBeTruthy());
    expect(screen.queryByText(/debe aprobarla otra persona/i)).toBeNull();
  });
});
