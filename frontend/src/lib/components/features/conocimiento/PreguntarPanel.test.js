// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * El panel solo aparece si la ruta de ese nivel se puede nombrar. Al preguntar
 * manda el radicado como alcance y muestra la cita con enlace al anexo y a la
 * página. Si responde el índice local, lo dice.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import PreguntarPanel from "./PreguntarPanel.svelte";

const RAD = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const ANEXO = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const FILE = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function transmision(levels) {
  return {
    transmits: true,
    provider: { id: "example-provider", name: "Example Provider" },
    levels,
  };
}

const publica = {
  level: 1,
  hosting: "installation",
  model_aliases: ["triage-small"],
  subprocessors: [],
};

function fetchDe(mapa) {
  return vi.fn(async (url, init) => {
    const clave = String(url);
    const handler = mapa[clave];
    if (!handler) return json({ error: "no" }, 404);
    return handler(init);
  });
}

describe("PreguntarPanel", () => {
  it("no se ofrece si el nivel del radicado no se transmite", async () => {
    globalThis.fetch = fetchDe({
      "/bandeja/api/preguntar/transmision": () => json(transmision([publica])),
    });
    render(PreguntarPanel, { props: { radicadoId: RAD, nivel: 2, anexos: [] } });
    await expect(
      screen.findByRole("heading", { name: "Preguntar" }, { timeout: 300 }),
    ).rejects.toThrow();
  });

  it("no se ofrece si no se puede leer la ruta", async () => {
    globalThis.fetch = fetchDe({
      "/bandeja/api/preguntar/transmision": () => json({ error: "ruta_no_disponible" }, 503),
    });
    render(PreguntarPanel, { props: { radicadoId: RAD, nivel: 1, anexos: [] } });
    await expect(
      screen.findByRole("heading", { name: "Preguntar" }, { timeout: 300 }),
    ).rejects.toThrow();
  });

  it("avisa la ruta, pregunta con alcance al radicado y enlaza el anexo", async () => {
    const llamadas = [];
    globalThis.fetch = vi.fn(async (url, init) => {
      llamadas.push({ url: String(url), init });
      if (String(url).endsWith("/transmision")) return json(transmision([publica]));
      return json({
        answer: "El plazo es de quince días.",
        origin: "ppi",
        status: "answered",
        advisory: "Cada afirmación tiene cita.",
        citations: [
          {
            cited_text: "quince días",
            source_ref: "2026-E-1",
            page: 3,
            attachment_ref: ANEXO,
            record_id: RAD,
          },
        ],
      });
    });
    render(PreguntarPanel, {
      props: {
        radicadoId: RAD,
        nivel: 1,
        anexos: [{ id: ANEXO, file_id: FILE, filename: "contrato.pdf" }],
      },
    });
    const nota = await screen.findByRole("note");
    expect(nota.textContent).toMatch(/Example Provider \(example-provider\)/);
    expect(nota.textContent).toMatch(/ruta de la instalación/);
    expect(nota.textContent).toMatch(/triage-small/);

    const campo = screen.getByLabelText("Pregunta sobre este radicado");
    await fireEvent.input(campo, { target: { value: "¿cuál es el plazo?" } });
    await fireEvent.click(screen.getByRole("button", { name: "Preguntar" }));

    await screen.findByText("quince días");
    const enlace = screen.getByRole("link", { name: "contrato.pdf, página 3" });
    expect(enlace.getAttribute("href")).toBe(`/bandeja/api/${RAD}/anexos/${FILE}#page=3`);
    const cuerpo = JSON.parse(llamadas[1].init.body);
    expect(cuerpo).toEqual({ query: "¿cuál es el plazo?", record_ref: RAD });
  });

  it("dice cuándo responde el índice local", async () => {
    globalThis.fetch = vi.fn(async (url) => {
      if (String(url).endsWith("/transmision")) return json(transmision([publica]));
      return json({
        answer: null,
        origin: "local_index",
        status: "unavailable",
        advisory: "Respuesta no disponible, inténtelo de nuevo",
        citations: [
          { cited_text: "Plazo de respuesta", source_ref: "2026-E-7", record_id: RAD, page: null },
        ],
      });
    });
    render(PreguntarPanel, { props: { radicadoId: RAD, nivel: 1, anexos: [] } });
    await screen.findByRole("note");
    await fireEvent.input(screen.getByLabelText("Pregunta sobre este radicado"), {
      target: { value: "plazo" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Preguntar" }));
    expect(await screen.findByText("Respuesta del índice local.")).toBeInTheDocument();
    expect(screen.getByText("Respuesta no disponible, inténtelo de nuevo")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "2026-E-7" }).getAttribute("href")).toBe(
      `/bandeja?radicado=${RAD}`,
    );
  });
});
