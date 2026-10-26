// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * PublicacionExpedienteDrawer.test.js — acto de publicación del expediente
 * (Ley 1712/2014 art. 12).
 *
 * Lo que se fija aquí, y por qué no es cosmética:
 * 1. Un expediente CERRADO sigue siendo publicable — copiar del radicado la
 *    condición de estado habría despublicado en silencio la mayor parte de un
 *    fondo (todo lo cerrado).
 * 2. Sobre un expediente reservado se puede RETIRAR pero no publicar: la
 *    asimetría del backend se refleja, no se aplana bloqueando ambas.
 * 3. El fundamento es obligatorio en los dos sentidos.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/svelte";
import PublicacionExpedienteDrawer from "./PublicacionExpedienteDrawer.svelte";

const BASE = {
  id: "11111111-1111-1111-1111-111111111111",
  code: "EXP-2026-000042",
  publicable: false,
  publicado_at: null,
  nivel_seguridad: 1,
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PublicacionExpedienteDrawer", () => {
  it("no publicado: ofrece publicar y nombra lo que se difunde por OAI-PMH", async () => {
    render(PublicacionExpedienteDrawer, { props: { open: true, expediente: BASE } });

    expect(screen.getByText("No publicado")).toBeInTheDocument();
    expect(screen.getByText(/cosechable por OAI-PMH/i)).toBeInTheDocument();
    // La frontera anti-fuga se dice explícitamente: nunca los radicados hijos.
    expect(screen.getByText(/no se difunden nunca/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Publicar/ })).toBeInTheDocument();
  });

  it("un expediente CERRADO sigue siendo publicable", async () => {
    render(PublicacionExpedienteDrawer, {
      props: { open: true, expediente: { ...BASE, status: "closed" } },
    });

    expect(screen.getByRole("button", { name: /Publicar/ })).toBeInTheDocument();
    expect(screen.queryByText(/clasificado o reservado/i)).not.toBeInTheDocument();
  });

  it("reservado y no publicado: no se ofrece publicar, y se explica", async () => {
    render(PublicacionExpedienteDrawer, {
      props: { open: true, expediente: { ...BASE, nivel_seguridad: 2 } },
    });

    expect(screen.getByText(/clasificado o reservado/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Publicar$/ })).not.toBeInTheDocument();
  });

  it("reservado pero PUBLICADO: sí se puede retirar — es donde más urge", async () => {
    render(PublicacionExpedienteDrawer, {
      props: {
        open: true,
        expediente: {
          ...BASE,
          nivel_seguridad: 2,
          publicable: true,
          publicado_at: "2026-08-01T09:00:00Z",
        },
      },
    });

    expect(screen.getByRole("button", { name: /Retirar publicación/ })).toBeInTheDocument();
    expect(screen.queryByText(/clasificado o reservado/i)).not.toBeInTheDocument();
  });

  it("sin fundamento el botón está deshabilitado", async () => {
    render(PublicacionExpedienteDrawer, { props: { open: true, expediente: BASE } });

    const boton = screen.getByRole("button", { name: /Publicar/ });
    expect(boton).toBeDisabled();

    await fireEvent.input(screen.getByLabelText(/Fundamento de la publicación/i), {
      target: { value: "Numeral 4.3 del esquema" },
    });
    await waitFor(() => expect(boton).not.toBeDisabled());
  });

  it("envía el sentido y el fundamento, y avisa del cambio", async () => {
    const llamadas = [];
    globalThis.fetch = vi.fn(async (url, opts) => {
      llamadas.push({ url, opts });
      return new Response(
        JSON.stringify({ code: BASE.code, publicable: true, publicado_at: "2026-08-14T10:00:00Z" }),
        { status: 200 },
      );
    });

    const { component } = render(PublicacionExpedienteDrawer, {
      props: { open: true, expediente: BASE },
    });
    const cambios = [];
    component.$on("changed", (e) => cambios.push(e.detail));

    await fireEvent.input(screen.getByLabelText(/Fundamento de la publicación/i), {
      target: { value: "  Numeral 4.3 del esquema  " },
    });
    await fireEvent.click(screen.getByRole("button", { name: /Publicar/ }));

    await waitFor(() => expect(cambios.length).toBe(1));
    expect(llamadas[0].url).toBe(`/expedientes/${BASE.id}/api/publicacion`);
    expect(JSON.parse(llamadas[0].opts.body)).toEqual({
      publicar: true,
      fundamento: "Numeral 4.3 del esquema",
    });
    expect(cambios[0].publicable).toBe(true);
  });
});
