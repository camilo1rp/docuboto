// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * radicar-page.test.js — Render de /radicar (+page.svelte, T-09).
 *
 * Cubre la migración del campo dinámico de metadatos del tipo documental
 * (E03) al componente compartido SchemaField (fuente única con el Form
 * Builder de expediente, `$lib/utils/schemaField.js`): antes del incremento
 * un `enum`/`boolean` de la plantilla degradaba a texto libre (→ 422 del
 * backend); ahora renderiza select y el `metadata` del submit va tipado.
 *
 * `$app/navigation` se mockea porque el componente se monta aislado (sin el
 * router real de SvelteKit) — mismo patrón que expedientes-page.test.js. El
 * flujo se conduce con docType="E" para no montar RichEditor (TipTap,
 * dependencia opcional solo usada en Salida/Interno).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, within, waitFor } from "@testing-library/svelte";
import { plantillaMetadatos } from "$lib/testing/plantillaMetadatos.js";

const gotoMock = vi.fn();
vi.mock("$app/navigation", () => ({ goto: gotoMock }));

const { default: RadicarPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function sessionWith({ permissions = [] } = {}) {
  return {
    sub: "u-1",
    email: "op@orpyca.com",
    name: "Operador",
    preferred_username: "operador",
    roles: [],
    tenant_slug: "icetex",
    permissions,
  };
}

/** @param {Partial<{dependencias: any[], trdSeries: any[], tiposDocumentales: any[]}>} overrides */
function baseData(overrides = {}) {
  return {
    user: sessionWith({ permissions: ["PERM_RADI_ENTRADA"] }),
    dependencias: [],
    trdSeries: [],
    // `nombre`, NO `name`: es lo que devuelve de verdad
    // `archive-service/routers/tipos_documentales.py`. Este fixture decía
    // `name` —la misma creencia equivocada que el componente— y por eso ningún
    // test podía cazar que el desplegable pintara «undefined». Un fixture
    // inventado a la vez que el código bajo prueba no prueba nada: confirma la
    // suposición. (Las series TRD sí usan `name`; son routers distintos.)
    tiposDocumentales: [{ code: "RES", nombre: "Resolución" }],
    ...overrides,
  };
}

/** Instala un fetch mock que responde /radicar/api/templates con `templates`. */
function mockTemplatesFetch(templates) {
  return vi.fn(async (url) => {
    if (String(url).startsWith("/radicar/api/templates")) {
      return { ok: true, json: async () => templates.map((t) => plantillaMetadatos(t)) };
    }
    return { ok: true, json: async () => [] };
  });
}

/** Elige el formulario vacío y abre el paso Tipo. */
async function elegirFormulario() {
  await fireEvent.click(screen.getByRole("button", { name: /Llenar el formulario/i }));
}

/** Selecciona docType="Entrada", llena Asunto/Remitente y avanza a Metadatos. */
async function goToMetadataStep() {
  await elegirFormulario();
  await fireEvent.click(screen.getByRole("radio", { name: /Entrada/i }));
  await fireEvent.click(screen.getByRole("button", { name: "Siguiente" }));
  await fireEvent.input(screen.getByLabelText("Asunto", { exact: false }), {
    target: { value: "Radicación de prueba" },
  });
  await fireEvent.input(screen.getByLabelText("Nombre del remitente", { exact: false }), {
    target: { value: "Juan Pérez" },
  });
}

/** Elige el tipo documental "Resolución" y espera a que cargue la plantilla. */
async function selectDocClass() {
  await fireEvent.change(screen.getByLabelText("Tipo documental", { exact: false }), {
    target: { value: "RES" },
  });
}

describe("/radicar — campo dinámico de metadatos vía SchemaField (E03/T-12)", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["PERM_RADI_ENTRADA"] }));
    gotoMock.mockClear();
  });
  afterEach(() => {
    clearSession();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("un campo `enum` de la plantilla se renderiza como <select> (antes degradaba a texto libre)", async () => {
    globalThis.fetch = mockTemplatesFetch([
      {
        id: "tpl-1",
        activo: true,
        json_schema: {
          properties: { prioridad: { enum: ["alta", "media", "baja"], title: "Prioridad" } },
          required: [],
        },
      },
    ]);

    render(RadicarPage, { props: { data: baseData() } });
    await goToMetadataStep();
    await selectDocClass();

    const prioridadSelect = await screen.findByLabelText("Prioridad", { exact: false });
    expect(prioridadSelect.tagName).toBe("SELECT");
    expect(prioridadSelect).toHaveAttribute("name", "meta_prioridad");
    expect(within(prioridadSelect).getByRole("option", { name: "alta" })).toBeInTheDocument();
  });

  it("un campo `boolean` de la plantilla se renderiza como <select> Sí/No (antes degradaba a texto libre)", async () => {
    globalThis.fetch = mockTemplatesFetch([
      {
        id: "tpl-2",
        activo: true,
        json_schema: {
          properties: { urgente: { type: "boolean", title: "Urgente" } },
          required: [],
        },
      },
    ]);

    render(RadicarPage, { props: { data: baseData() } });
    await goToMetadataStep();
    await selectDocClass();

    const urgenteSelect = await screen.findByLabelText("Urgente", { exact: false });
    expect(urgenteSelect.tagName).toBe("SELECT");
    expect(urgenteSelect).toHaveAttribute("name", "meta_urgente");
    expect(within(urgenteSelect).getByRole("option", { name: "Sí" })).toBeInTheDocument();
    expect(within(urgenteSelect).getByRole("option", { name: "No" })).toBeInTheDocument();
  });

  it("regresión: string/number/format:date siguen renderizando el MISMO control que antes de la migración", async () => {
    globalThis.fetch = mockTemplatesFetch([
      {
        id: "tpl-3",
        activo: true,
        json_schema: {
          properties: {
            motivo: { type: "string", title: "Motivo" },
            cantidad: { type: "integer", title: "Cantidad" },
            fecha_limite: { type: "string", format: "date", title: "Fecha límite" },
          },
          required: ["motivo"],
        },
      },
    ]);

    render(RadicarPage, { props: { data: baseData() } });
    await goToMetadataStep();
    await selectDocClass();

    const motivoInput = await screen.findByLabelText("Motivo", { exact: false });
    expect(motivoInput.tagName).toBe("INPUT");
    expect(motivoInput).toHaveAttribute("type", "text");

    const cantidadInput = screen.getByLabelText("Cantidad", { exact: false });
    expect(cantidadInput).toHaveAttribute("type", "number");

    const fechaInput = screen.getByLabelText("Fecha límite", { exact: false });
    expect(fechaInput).toHaveAttribute("type", "date");
  });

  it("una propiedad no soportada (object) NO se renderiza y muestra el aviso de campos no editables", async () => {
    globalThis.fetch = mockTemplatesFetch([
      {
        id: "tpl-4",
        activo: true,
        json_schema: {
          properties: {
            motivo: { type: "string", title: "Motivo" },
            anexos_extra: { type: "object", title: "Anexos extra", properties: {} },
          },
          required: [],
        },
      },
    ]);

    render(RadicarPage, { props: { data: baseData() } });
    await goToMetadataStep();
    await selectDocClass();

    await screen.findByLabelText("Motivo", { exact: false });
    expect(screen.queryByLabelText("Anexos extra", { exact: false })).not.toBeInTheDocument();
    expect(
      screen.getByText("1 campo de la plantilla no se puede editar en este formulario."),
    ).toBeInTheDocument();
  });

  it("el submit envía el metadata TIPADO: boolean como bool, number como number, string tal cual, y omite lo vacío/no soportado", async () => {
    const TEMPLATE = {
      id: "tpl-5",
      activo: true,
      json_schema: {
        properties: {
          motivo: { type: "string", title: "Motivo" },
          cantidad: { type: "integer", title: "Cantidad" },
          urgente: { type: "boolean", title: "Urgente" },
          prioridad: { enum: ["alta", "media", "baja"], title: "Prioridad" },
          fecha_limite: { type: "string", format: "date", title: "Fecha límite" },
          detalle: { type: "object", title: "Detalle", properties: {} },
        },
        required: ["motivo"],
      },
    };

    /** @type {any} */
    let capturedBody = null;
    globalThis.fetch = vi.fn(async (url, opts) => {
      const href = String(url);
      if (href.startsWith("/radicar/api/templates")) {
        return { ok: true, json: async () => [plantillaMetadatos(TEMPLATE)] };
      }
      if (href === "/radicar/api/documents") {
        capturedBody = JSON.parse(opts.body);
        return { ok: true, json: async () => ({ tracking_number: "2026-ICETEX-E-000001" }) };
      }
      return { ok: true, json: async () => [] };
    });

    render(RadicarPage, { props: { data: baseData() } });
    await goToMetadataStep();
    await selectDocClass();

    await fireEvent.input(await screen.findByLabelText("Motivo", { exact: false }), {
      target: { value: "Solicitud de prueba" },
    });
    await fireEvent.input(screen.getByLabelText("Cantidad", { exact: false }), {
      target: { value: "5" },
    });
    await fireEvent.change(screen.getByLabelText("Urgente", { exact: false }), {
      target: { value: "true" },
    });
    await fireEvent.change(screen.getByLabelText("Prioridad", { exact: false }), {
      target: { value: "alta" },
    });
    // fecha_limite se deja vacío deliberadamente (debe omitirse del metadata).

    // Pasos 2 (Anexos), 3 (TRD) y 4 (Flujo/Destino): sin campos obligatorios.
    await fireEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    await fireEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    await fireEvent.click(screen.getByRole("button", { name: "Siguiente" }));

    await fireEvent.click(screen.getByRole("button", { name: "Radicar" }));
    await fireEvent.click(screen.getByRole("button", { name: "Confirmar radicación" }));

    expect(capturedBody).not.toBeNull();
    expect(capturedBody.metadata).toEqual({
      motivo: "Solicitud de prueba",
      cantidad: 5,
      urgente: true,
      prioridad: "alta",
    });
    expect(typeof capturedBody.metadata.cantidad).toBe("number");
    expect(typeof capturedBody.metadata.urgente).toBe("boolean");
    expect(capturedBody.metadata.fecha_limite).toBeUndefined();
    expect(capturedBody.metadata.detalle).toBeUndefined();
  });
});

describe("/radicar — un remitente del directorio sin envío decidido se puede radicar", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["PERM_RADI_ENTRADA"] }));
  });
  afterEach(() => {
    clearSession();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("«Sin decidir» viaja como canal null, no como cadena vacía (el backend da 422 con «»)", async () => {
    // Hallado en la pila el 2026-10-02: toda Entrada con un remitente elegido del
    // directorio fallaba con 422 si no se decidía antes el envío.
    /** @type {any} */
    let capturedBody = null;
    globalThis.fetch = vi.fn(async (url, opts) => {
      const href = String(url);
      if (href.startsWith("/radicar/api/terceros?q=")) {
        return {
          ok: true,
          json: async () => [
            {
              id: "t-1",
              nombre: "María Fernanda Ruiz",
              email: "mf@ejemplo.test",
              direccion: "Calle 10",
            },
          ],
        };
      }
      if (href === "/radicar/api/documents") {
        capturedBody = JSON.parse(opts.body);
        return { ok: true, json: async () => ({ tracking_number: "2026-ICETEX-E-000002" }) };
      }
      return { ok: true, json: async () => [] };
    });

    render(RadicarPage, { props: { data: baseData() } });
    await goToMetadataStep();
    await fireEvent.input(screen.getByLabelText("Buscar remitente", { exact: false }), {
      target: { value: "maria fernanda" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    await fireEvent.click(await screen.findByRole("button", { name: "Añadir" }));

    for (let i = 0; i < 3; i++) {
      await fireEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    }
    await fireEvent.click(screen.getByRole("button", { name: "Radicar" }));
    await fireEvent.click(screen.getByRole("button", { name: "Confirmar radicación" }));

    expect(capturedBody).not.toBeNull();
    expect(capturedBody.actores).toHaveLength(1);
    expect(capturedBody.actores[0]).toMatchObject({
      tercero_id: "t-1",
      rol: "remitente",
      canal: null,
      principal: true,
    });
  });
});

describe("/radicar — el desplegable de tipo documental dice QUÉ tipo es", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["PERM_RADI_ENTRADA"] }));
  });
  afterEach(() => clearSession());

  it("muestra el nombre del tipo, no «undefined»", async () => {
    // Encontrado navegando: el único tipo documental del tenant aparecía como
    // «undefined» en el paso 2. El componente leía `t.name` y el backend manda
    // `t.nombre`. En la pantalla más usada del sistema, eso deja a quien radica
    // eligiendo a ciegas entre opciones sin etiqueta.
    render(RadicarPage, {
      props: {
        data: baseData({
          tiposDocumentales: [
            { code: "DEM-PET", nombre: "Petición ciudadana" },
            { code: "RES", nombre: "Resolución" },
          ],
        }),
      },
    });
    await goToMetadataStep();

    expect(screen.getByRole("option", { name: "Petición ciudadana" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Resolución" })).toBeTruthy();
    // La aserción que discrimina: ninguna opción se llama «undefined».
    expect(screen.queryByRole("option", { name: /undefined/i })).toBeNull();
  });
});

describe("/radicar — dos versiones de la misma serie TRD son distinguibles", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["PERM_RADI_ENTRADA"] }));
  });
  afterEach(() => clearSession());

  const DOS_VERSIONES = [
    { id: "v2", code: "DEM-100", name: "Derechos de Petición", version: 2, estado: "borrador" },
    { id: "v1", code: "DEM-100", name: "Derechos de Petición", version: 1, estado: "convalidada" },
  ];

  async function irAClasificacion() {
    await goToMetadataStep();
    for (const _ of [1, 2]) {
      await fireEvent.click(screen.getByRole("button", { name: /siguiente/i }));
    }
  }

  it("la etiqueta lleva versión y estado, y no hay dos opciones iguales", async () => {
    // El circuito de convalidación abre una v2 en borrador mientras la v1
    // convalidada sigue rigiendo. El desplegable pintaba ambas como
    // «DEM-100 — Derechos de Petición»: quien clasifica elegía a ciegas entre
    // la vigente y una que todavía no rige.
    render(RadicarPage, { props: { data: baseData({ trdSeries: DOS_VERSIONES }) } });
    await irAClasificacion();

    const select = screen.getByLabelText("Serie TRD", { exact: false });
    const etiquetas = Array.from(select.options)
      .map((o) => o.textContent.trim())
      .filter((t) => t.includes("DEM-100"));

    expect(etiquetas).toHaveLength(2);
    expect(new Set(etiquetas).size).toBe(2); // la aserción que discrimina
    expect(etiquetas.some((t) => /v1.*vigente/.test(t))).toBe(true);
    expect(etiquetas.some((t) => /v2.*borrador/.test(t))).toBe(true);
  });

  it("la vigente aparece antes que el borrador", async () => {
    // El fixture las entrega al revés a propósito: sin ordenación, este test
    // pasaría por casualidad.
    render(RadicarPage, { props: { data: baseData({ trdSeries: DOS_VERSIONES }) } });
    await irAClasificacion();

    const select = screen.getByLabelText("Serie TRD", { exact: false });
    const conSerie = Array.from(select.options)
      .map((o) => o.textContent.trim())
      .filter((t) => t.includes("DEM-100"));
    expect(conSerie[0]).toMatch(/vigente/);
  });

  it("una serie sin estado no pierde su opción", async () => {
    // Fail-safe: si el backend dejara de mandar `estado`, la serie debe seguir
    // siendo clasificable — sin sufijo, pero presente. Esconderla dejaría la
    // radicación sin nada que clasificar.
    render(RadicarPage, {
      props: { data: baseData({ trdSeries: [{ id: "x", code: "DEM-200", name: "Contratos" }] }) },
    });
    await irAClasificacion();

    const select = screen.getByLabelText("Serie TRD", { exact: false });
    expect(Array.from(select.options).some((o) => /DEM-200 — Contratos/.test(o.textContent))).toBe(
      true,
    );
  });
});

describe("/radicar — el primer paso es Documento", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["PERM_RADI_ENTRADA"] }));
  });
  afterEach(() => {
    clearSession();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("llenar el formulario abre Tipo vacío, sin radios marcados", async () => {
    render(RadicarPage, { props: { data: baseData() } });
    expect(screen.getByRole("heading", { name: /documento o el formulario/i })).toBeInTheDocument();
    await elegirFormulario();
    expect(screen.getByRole("radiogroup", { name: /Tipo de radicado/i })).toBeInTheDocument();
    for (const radio of screen.getAllByRole("radio")) {
      expect(radio).not.toBeChecked();
    }
  });

  it("subir documento muestra la zona de carga y el archivo elegido", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      json: async () => ({ detail: "servicio no disponible" }),
    }));
    render(RadicarPage, { props: { data: baseData() } });
    await fireEvent.click(screen.getByRole("button", { name: /Subir documento/i }));
    const input = screen.getByLabelText("Seleccionar archivo para radicar");
    const file = new File(["hola"], "oficio.pdf", { type: "application/pdf" });
    await fireEvent.change(input, { target: { files: [file] } });
    expect(screen.getByTestId("entry-file")).toHaveTextContent("oficio.pdf");
  });

  it("Anexos, TRD y Flujo siguen en el stepper después de Tipo", async () => {
    render(RadicarPage, { props: { data: baseData() } });
    await elegirFormulario();
    expect(screen.getByText("Anexos")).toBeInTheDocument();
    expect(screen.getByText("Clasificación TRD")).toBeInTheDocument();
    expect(screen.getByText("Flujo / Destino")).toBeInTheDocument();
  });
});

describe("/radicar — ingesta indexada prefilla Tipo y Metadatos", () => {
  const originalFetch = globalThis.fetch;
  const FILE_ID = "11111111-1111-4111-8111-111111111111";

  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["PERM_RADI_ENTRADA"] }));
  });
  afterEach(() => {
    clearSession();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("al indexar extrae, marca IA y deja el archivo en anexos", async () => {
    globalThis.fetch = vi.fn(async (url, opts) => {
      const href = String(url);
      if (href === "/radicar/api/storage") {
        return { ok: true, json: async () => ({ id: FILE_ID }) };
      }
      if (href === "/radicar/api/ingesta" && opts?.method === "POST") {
        return {
          ok: true,
          json: async () => ({ staging_ref: "staging-1", file_ref: "staging-1.file" }),
        };
      }
      if (href === "/radicar/api/ingesta/staging-1.file") {
        return {
          ok: true,
          json: async () => ({
            external_ref: "staging-1.file",
            ingest_state: "indexed",
            error_code: null,
          }),
        };
      }
      if (href === "/radicar/api/ingesta/extract") {
        const body = JSON.parse(opts.body);
        const keys = body.fields.map((f) => f.key);
        const values = keys.includes("direction")
          ? {
              direction: "E",
              subject: "Petición ciudadana",
              sender_name: "Ana Ruiz",
              details: "El oficio pide copia del acto administrativo.",
            }
          : {};
        return { ok: true, json: async () => ({ external_ref: body.external_ref, values }) };
      }
      if (href.startsWith("/radicar/api/templates")) {
        return { ok: true, json: async () => [] };
      }
      return { ok: true, json: async () => ({}) };
    });

    render(RadicarPage, { props: { data: baseData() } });
    await fireEvent.click(screen.getByRole("button", { name: /Subir documento/i }));
    const input = screen.getByLabelText("Seleccionar archivo para radicar");
    await fireEvent.change(input, {
      target: { files: [new File(["hola"], "peticion.pdf", { type: "application/pdf" })] },
    });

    await waitFor(() => {
      expect(screen.getByRole("radio", { name: /Entrada/i })).toBeChecked();
    });
    expect(screen.getByText("IA")).toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    expect(screen.getByLabelText("Asunto", { exact: false })).toHaveValue("Petición ciudadana");
    expect(screen.getByLabelText("Nombre del remitente", { exact: false })).toHaveValue("Ana Ruiz");
    expect(screen.getByLabelText("Observaciones", { exact: false })).toHaveValue(
      "El oficio pide copia del acto administrativo.",
    );
    expect(screen.getAllByText("IA").length).toBeGreaterThan(0);

    expect(screen.getByTestId("ai-chip-subject")).toBeInTheDocument();
    expect(screen.getByTestId("ai-chip-sender_name")).toBeInTheDocument();
    expect(screen.getByTestId("ai-chip-observations")).toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    expect(screen.getByText("peticion.pdf")).toBeInTheDocument();
    expect(screen.getByLabelText("Subido correctamente")).toBeInTheDocument();
  });

  it("si extract falla con 503, reintenta y rellena el formulario", async () => {
    let extractCalls = 0;
    globalThis.fetch = vi.fn(async (url, opts) => {
      const href = String(url);
      if (href === "/radicar/api/storage") {
        return { ok: true, json: async () => ({ id: FILE_ID }) };
      }
      if (href === "/radicar/api/ingesta" && opts?.method === "POST") {
        return {
          ok: true,
          json: async () => ({ staging_ref: "staging-1", file_ref: "staging-1.file" }),
        };
      }
      if (href === "/radicar/api/ingesta/staging-1.file") {
        return {
          ok: true,
          json: async () => ({
            external_ref: "staging-1.file",
            ingest_state: "indexed",
            error_code: null,
          }),
        };
      }
      if (href === "/radicar/api/ingesta/extract") {
        extractCalls += 1;
        if (extractCalls === 1) {
          // Un parpadeo de infraestructura: falla rápido y el reintento lo salva.
          return {
            ok: false,
            status: 503,
            json: async () => ({
              error: "upstream_unavailable",
              detail: "No se pudo contactar el servicio",
              status: 503,
            }),
          };
        }
        const body = JSON.parse(opts.body);
        const keys = body.fields.map((f) => f.key);
        const values = keys.includes("direction")
          ? { direction: "E", subject: "Petición ciudadana", sender_name: "Ana Ruiz" }
          : {};
        return { ok: true, json: async () => ({ external_ref: body.external_ref, values }) };
      }
      if (href.startsWith("/radicar/api/templates")) {
        return { ok: true, json: async () => [] };
      }
      return { ok: true, json: async () => ({}) };
    });

    render(RadicarPage, { props: { data: baseData() } });
    await fireEvent.click(screen.getByRole("button", { name: /Subir documento/i }));
    const input = screen.getByLabelText("Seleccionar archivo para radicar");
    await fireEvent.change(input, {
      target: { files: [new File(["hola"], "peticion.pdf", { type: "application/pdf" })] },
    });

    await waitFor(
      () => {
        expect(screen.getByRole("radio", { name: /Entrada/i })).toBeChecked();
      },
      { timeout: 4000 },
    );
    expect(extractCalls).toBeGreaterThanOrEqual(2);
  }, 10_000);

  it("si extract no responde, abre Tipo vacío y deja reintentar", async () => {
    let extractCalls = 0;
    globalThis.fetch = vi.fn(async (url, opts) => {
      const href = String(url);
      if (href === "/radicar/api/storage") {
        return { ok: true, json: async () => ({ id: FILE_ID }) };
      }
      if (href === "/radicar/api/ingesta" && opts?.method === "POST") {
        return {
          ok: true,
          json: async () => ({ staging_ref: "staging-1", file_ref: "staging-1.file" }),
        };
      }
      if (href === "/radicar/api/ingesta/staging-1.file") {
        return {
          ok: true,
          json: async () => ({
            external_ref: "staging-1.file",
            ingest_state: "indexed",
            error_code: null,
          }),
        };
      }
      if (href === "/radicar/api/ingesta/extract") {
        extractCalls += 1;
        return {
          ok: false,
          status: 504,
          json: async () => ({
            error: "extract_timeout",
            detail: "La extracción tardó demasiado. Vuelve a extraer o llena el formulario.",
            status: 504,
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    });

    render(RadicarPage, { props: { data: baseData() } });
    await fireEvent.click(screen.getByRole("button", { name: /Subir documento/i }));
    const input = screen.getByLabelText("Seleccionar archivo para radicar");
    await fireEvent.change(input, {
      target: { files: [new File(["hola"], "oficio.pdf", { type: "application/pdf" })] },
    });

    await waitFor(
      () => {
        expect(screen.getByTestId("extract-error")).toBeInTheDocument();
      },
      { timeout: 8000 },
    );
    expect(screen.getByText(/tardó demasiado/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Volver a extraer/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Entrada/i })).not.toBeChecked();
    // Un plazo agotado NO se reintenta solo: se espera una decisión humana.
    expect(extractCalls).toBe(1);
  }, 10_000);
});
