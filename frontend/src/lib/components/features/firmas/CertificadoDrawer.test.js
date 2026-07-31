// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * CertificadoDrawer.test.js — Vista de certificado de firma (E06, ADR-016,
 * Fase 3 cierre de desfase API↔UI).
 *
 * El punto central: el comprobante NUNCA debe insinuar acreditación ONAC ni
 * control exclusivo del firmante, y un fallo de red al cargar el
 * certificado nunca se disfraza de "sin datos".
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";
import CertificadoDrawer from "./CertificadoDrawer.svelte";

const FIRMA_ID = "22222222-2222-2222-2222-222222222222";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CertificadoDrawer — honestidad del comprobante (art. 7, no art. 28)", () => {
  it("muestra el aviso de no-acreditación de forma permanente, no como letra pequeña", async () => {
    globalThis.fetch = vi.fn(async () => {
      return new Response(
        JSON.stringify({
          firma_id: FIRMA_ID,
          objeto_tipo: "radicado",
          objeto_id: "r1",
          firmante_id: "u1",
          provider: "nativa",
          sello_tiempo: "2026-07-30T10:00:00Z",
          acreditado: false,
        }),
        { status: 200 },
      );
    });

    render(CertificadoDrawer, { props: { open: true, firmaId: FIRMA_ID } });

    await waitFor(() => expect(screen.getByText("Comprobante de firma")).toBeInTheDocument());
    expect(screen.getByText(/No es firma digital acreditada/)).toBeInTheDocument();
    expect(screen.getByText("No acreditada (ONAC)")).toBeInTheDocument();
  });

  it("un fallo de red al cargar el certificado se muestra como error, no como 'sin datos'", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });

    render(CertificadoDrawer, { props: { open: true, firmaId: FIRMA_ID } });

    await waitFor(() =>
      expect(screen.getByText(/No se pudo conectar al servidor de firmas/)).toBeInTheDocument(),
    );
    expect(screen.queryByText("Comprobante de firma")).not.toBeInTheDocument();
  });

  it("404 se muestra como 'no encontrado', nunca silenciosamente vacío", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "firma_not_found", status: 404 }), { status: 404 }),
    );

    render(CertificadoDrawer, { props: { open: true, firmaId: FIRMA_ID } });

    await waitFor(() =>
      expect(screen.getByText("No se encontró información de esta firma.")).toBeInTheDocument(),
    );
  });
});
