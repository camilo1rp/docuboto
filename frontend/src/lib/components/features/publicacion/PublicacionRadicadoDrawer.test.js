// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * PublicacionRadicadoDrawer.test.js — acto de publicación de oficio
 * (Ley 1712/2014 art. 12).
 *
 * Lo que se fija aquí no es cosmética:
 * 1. Un radicado reservado NO recibe formulario de publicación — el backend lo
 *    rechaza con un 404 neutro, y dejar que el usuario redacte un fundamento
 *    para recibir después "no existe" es una pantalla que engaña.
 * 2. El acto ofrecido es siempre el CONTRARIO del estado real, y ese estado se
 *    lee del servidor: nunca se deduce.
 * 3. El fundamento es obligatorio en ambos sentidos.
 * 4. Un fallo de red no se muestra como "no publicado".
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/svelte";
import { get } from "svelte/store";
import { toasts } from "$lib/stores/notifications.js";
import PublicacionRadicadoDrawer from "./PublicacionRadicadoDrawer.svelte";

const RADICADO = { id: "11111111-1111-1111-1111-111111111111", tracking_number: "2026-ICETEX-E-1" };

function mockEstado(estado) {
  return vi.fn(async () => new Response(JSON.stringify(estado), { status: 200 }));
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PublicacionRadicadoDrawer", () => {
  it("un radicado reservado no recibe formulario: se explica el motivo", async () => {
    globalThis.fetch = mockEstado({
      tracking_number: RADICADO.tracking_number,
      nivel_seguridad: 2,
      status: "registered",
      publicable: false,
      publicado_at: null,
    });

    render(PublicacionRadicadoDrawer, { props: { open: true, radicado: RADICADO } });

    await waitFor(() => expect(screen.getByText(/clasificado o reservado/i)).toBeInTheDocument());
    expect(screen.queryByLabelText(/Fundamento de la publicación/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Publicar$/ })).not.toBeInTheDocument();
  });

  it("un radicado anulado tampoco admite el acto", async () => {
    globalThis.fetch = mockEstado({
      tracking_number: RADICADO.tracking_number,
      nivel_seguridad: 1,
      status: "anulado",
      publicable: false,
      publicado_at: null,
    });

    render(PublicacionRadicadoDrawer, { props: { open: true, radicado: RADICADO } });

    await waitFor(() => expect(screen.getByText(/anulado/i)).toBeInTheDocument());
    expect(screen.queryByLabelText(/Fundamento/i)).not.toBeInTheDocument();
  });

  it("no publicado: ofrece publicar y advierte de la cosecha OAI-PMH/CMIS", async () => {
    globalThis.fetch = mockEstado({
      tracking_number: RADICADO.tracking_number,
      nivel_seguridad: 1,
      status: "registered",
      publicable: false,
      publicado_at: null,
    });

    render(PublicacionRadicadoDrawer, { props: { open: true, radicado: RADICADO } });

    await waitFor(() => expect(screen.getByText("No publicado")).toBeInTheDocument());
    expect(screen.getByText(/cosechable por OAI-PMH y CMIS/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Publicar/ })).toBeInTheDocument();
  });

  it("ya publicado: el acto ofrecido es el contrario — retirar", async () => {
    globalThis.fetch = mockEstado({
      tracking_number: RADICADO.tracking_number,
      nivel_seguridad: 1,
      status: "registered",
      publicable: true,
      publicado_at: "2026-08-12T10:30:00Z",
    });

    render(PublicacionRadicadoDrawer, { props: { open: true, radicado: RADICADO } });

    await waitFor(() => expect(screen.getByText("Publicado")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Retirar publicación/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Publicar$/ })).not.toBeInTheDocument();
  });

  it("sin fundamento el botón está deshabilitado, en los dos sentidos del acto", async () => {
    globalThis.fetch = mockEstado({
      tracking_number: RADICADO.tracking_number,
      nivel_seguridad: 1,
      status: "registered",
      publicable: true,
      publicado_at: "2026-08-12T10:30:00Z",
    });

    render(PublicacionRadicadoDrawer, { props: { open: true, radicado: RADICADO } });

    const boton = await screen.findByRole("button", { name: /Retirar publicación/ });
    expect(boton).toBeDisabled();

    const campo = screen.getByLabelText(/Fundamento del retiro/i);
    await fireEvent.input(campo, { target: { value: "Corrección del acto publicado" } });
    await waitFor(() => expect(boton).not.toBeDisabled());
  });

  it("un fallo de red no se disfraza de 'no publicado'", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });

    render(PublicacionRadicadoDrawer, { props: { open: true, radicado: RADICADO } });

    await waitFor(() =>
      expect(screen.getByText(/No se pudo conectar al servidor/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText("No publicado")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Publicar/ })).not.toBeInTheDocument();
  });

  it("envía el sentido del acto y el fundamento, y avisa del cambio", async () => {
    const llamadas = [];
    globalThis.fetch = vi.fn(async (url, opts) => {
      llamadas.push({ url, opts });
      if (!opts || opts.method !== "POST") {
        return new Response(
          JSON.stringify({
            tracking_number: RADICADO.tracking_number,
            nivel_seguridad: 1,
            status: "registered",
            publicable: false,
            publicado_at: null,
          }),
          { status: 200 },
        );
      }
      return new Response(
        JSON.stringify({
          tracking_number: RADICADO.tracking_number,
          publicable: true,
          publicado_at: "2026-08-12T11:00:00Z",
        }),
        { status: 200 },
      );
    });

    const { component } = render(PublicacionRadicadoDrawer, {
      props: { open: true, radicado: RADICADO },
    });
    const cambios = [];
    component.$on("changed", (e) => cambios.push(e.detail));

    const campo = await screen.findByLabelText(/Fundamento de la publicación/i);
    await fireEvent.input(campo, { target: { value: "Numeral 4.2 del esquema" } });
    await fireEvent.click(screen.getByRole("button", { name: /Publicar/ }));

    await waitFor(() => expect(cambios.length).toBe(1));
    const post = llamadas.find((c) => c.opts?.method === "POST");
    expect(JSON.parse(post.opts.body)).toEqual({
      publicar: true,
      fundamento: "Numeral 4.2 del esquema",
    });
    expect(cambios[0].publicable).toBe(true);
  });
});

describe("409 tipo_documental_no_publicable (C-4)", () => {
  it("se explica en español que el TIPO del radicado no es publicable, sin disfrazarlo de otro fallo", async () => {
    globalThis.fetch = vi.fn(async (url, opts) => {
      if (opts?.method === "POST") {
        return new Response(
          JSON.stringify({ error: "tipo_documental_no_publicable", status: 409 }),
          { status: 409 },
        );
      }
      return new Response(
        JSON.stringify({
          tracking_number: RADICADO.tracking_number,
          nivel_seguridad: 1,
          status: "registered",
          publicable: false,
          publicado_at: null,
        }),
        { status: 200 },
      );
    });
    const { component } = render(PublicacionRadicadoDrawer, {
      props: { open: true, radicado: RADICADO },
    });
    const cambios = [];
    component.$on("changed", (e) => cambios.push(e.detail));
    const campo = await screen.findByLabelText(/Fundamento de la publicación/i);
    await fireEvent.input(campo, { target: { value: "Numeral 4.2" } });
    await fireEvent.click(screen.getByRole("button", { name: /Publicar/ }));
    await waitFor(() =>
      expect(
        get(toasts).some(
          (n) => /tipo documental/i.test(n.message) && /publicable/i.test(n.message),
        ),
      ).toBe(true),
    );
    expect(cambios).toHaveLength(0);
  });
});
