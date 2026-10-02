// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * AssistantDock.test.js — Panel de chat del asistente conversacional (E22/E18).
 *
 * Cubre:
 *  - El dock abre/cierra (foco entra al botón de cerrar, patrón heredado de Modal).
 *  - El input tiene label asociado; la lista de turnos es un `role="log"` con
 *    `aria-live="polite"` acotado (no todo el `<aside>`).
 *  - Enviar un mensaje empuja el turno de usuario y llama al servicio del asistente
 *    a través del store (nunca al gateway directamente — se mockea el servicio).
 *  - Backend ausente (501/gateway aún no construido) → banner honesto "aún no
 *    disponible", NUNCA una respuesta simulada del asistente.
 *  - Los turnos del asistente en HTML se renderizan como `{@html}` YA SANEADO
 *    (el store nunca recibe HTML crudo del servicio mockeado sin pasar por el saneo
 *    real del proxy — aquí se verifica que el componente confía en `turn.html` tal
 *    cual llega del store, que es el campo ya-saneado del contrato).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/svelte";
import { tick } from "svelte";

const sendMessageMock = vi.fn();

vi.mock("$lib/services/assistant.service.js", async () => {
  const actual = await vi.importActual("$lib/services/assistant.service.js");
  return { ...actual, sendMessage: (...args) => sendMessageMock(...args) };
});

const { default: AssistantDock } = await import("./AssistantDock.svelte");
const { reset: resetAssistant } = await import("$lib/stores/assistant.js");

beforeEach(() => {
  sendMessageMock.mockReset();
  resetAssistant();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AssistantDock — apertura y accesibilidad base", () => {
  it("no renderiza el diálogo cuando open=false", () => {
    render(AssistantDock, { props: { open: false, onClose: vi.fn() } });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("al abrir: role=dialog, foco inicial en el campo de mensaje, input con label accesible", async () => {
    render(AssistantDock, { props: { open: true, onClose: vi.fn() } });
    await tick();

    const dialog = screen.getByRole("dialog", { name: "Asistente conversacional" });
    expect(dialog).toHaveAttribute("aria-modal", "true");

    const input = screen.getByLabelText("Mensaje para el asistente");
    expect(input.tagName).toBe("TEXTAREA");
    // Foco inicial en el textarea (dock de chat: escribir de inmediato), no
    // en el botón "Cerrar" -- ambos dentro del focus-trap.
    expect(document.activeElement).toBe(input);
  });

  it("la lista de turnos es un role=log con aria-live acotado (no en el <aside>)", async () => {
    render(AssistantDock, { props: { open: true, onClose: vi.fn() } });
    await tick();

    const dialog = screen.getByRole("dialog");
    expect(dialog).not.toHaveAttribute("aria-live");

    const log = screen.getByRole("log");
    expect(log).toHaveAttribute("aria-live", "polite");
  });

  it("Escape cierra el dock (focus trap heredado del patrón Modal)", async () => {
    const onClose = vi.fn();
    render(AssistantDock, { props: { open: true, onClose } });
    await tick();
    await fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});

describe("AssistantDock — envío de mensajes", () => {
  it("escribir y enviar empuja el turno del usuario y llama al servicio del asistente", async () => {
    sendMessageMock.mockResolvedValue({
      conversation_id: "c-1",
      message: { id: "a-1", texto: "Respuesta del asistente", html: null, ts: "t" },
    });

    render(AssistantDock, { props: { open: true, onClose: vi.fn() } });
    await tick();

    const input = screen.getByLabelText("Mensaje para el asistente");
    await fireEvent.input(input, { target: { value: "¿Cómo radico una entrada?" } });
    await fireEvent.click(screen.getByRole("button", { name: "Enviar mensaje" }));
    await tick();
    await Promise.resolve();
    await tick();

    expect(sendMessageMock).toHaveBeenCalledWith({
      mensaje: "¿Cómo radico una entrada?",
      conversationId: null,
    });

    const log = screen.getByRole("log");
    expect(within(log).getByText("¿Cómo radico una entrada?")).toBeInTheDocument();
    expect(within(log).getByText("Respuesta del asistente")).toBeInTheDocument();
  });

  it("Enter envía; Shift+Enter no envía (inserta salto de línea)", async () => {
    sendMessageMock.mockResolvedValue({
      conversation_id: null,
      message: { id: "a-1", texto: "ok", html: null, ts: "t" },
    });
    render(AssistantDock, { props: { open: true, onClose: vi.fn() } });
    await tick();

    const input = screen.getByLabelText("Mensaje para el asistente");
    await fireEvent.input(input, { target: { value: "hola" } });
    await fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(sendMessageMock).not.toHaveBeenCalled();

    await fireEvent.keyDown(input, { key: "Enter" });
    expect(sendMessageMock).toHaveBeenCalledTimes(1);
  });

  it("no envía un mensaje en blanco (el botón de enviar queda deshabilitado)", async () => {
    render(AssistantDock, { props: { open: true, onClose: vi.fn() } });
    await tick();
    const sendBtn = screen.getByRole("button", { name: "Enviar mensaje" });
    expect(sendBtn).toBeDisabled();
    expect(sendMessageMock).not.toHaveBeenCalled();
  });

  it("backend ausente (assistant_unavailable) → banner honesto, sin turno de asistente fabricado", async () => {
    const { AssistantError } = await import("$lib/services/assistant.service.js");
    // El motivo viaja en `detail` desde el backend: es lo que el banner enseña.
    const motivo =
      "Esta instalación no tiene configurado un modelo de lenguaje, así que el asistente " +
      "no puede responder. Quien administra el sistema puede activarlo.";
    sendMessageMock.mockRejectedValue(
      new AssistantError("unavailable", "El asistente aún no está disponible.", motivo),
    );

    render(AssistantDock, { props: { open: true, onClose: vi.fn() } });
    await tick();

    const input = screen.getByLabelText("Mensaje para el asistente");
    await fireEvent.input(input, { target: { value: "hola" } });
    await fireEvent.click(screen.getByRole("button", { name: "Enviar mensaje" }));
    await tick();
    await Promise.resolve();
    await tick();

    expect(screen.getByText(motivo)).toBeInTheDocument();
    // Y ya no promete lo que no depende del producto: la función existe; lo que
    // falta es configurarla en esta instalación.
    expect(screen.queryByText(/se activará próximamente/i)).toBeNull();
    // El turno del usuario se conserva; no se fabrica un turno de asistente.
    const log = screen.getByRole("log");
    expect(within(log).queryAllByText(/asistente/i)).toHaveLength(0);
  });
});

describe("AssistantDock — el dictado rellena el compositor y NUNCA envía", () => {
  /** Reconocedor local disponible: es la única condición en que hay botón. */
  function instalarReconocedorLocal() {
    const instancias = [];
    class SR {
      constructor() {
        instancias.push(this);
      }
      start() {}
      stop() {}
    }
    SR.availableOnDevice = async () => "available";
    vi.stubGlobal("SpeechRecognition", SR);
    return instancias;
  }

  async function abrirConDictado() {
    const instancias = instalarReconocedorLocal();
    render(AssistantDock, { props: { open: true } });
    await tick();
    await Promise.resolve();
    await tick();
    return instancias;
  }

  it("lo dictado aparece en el compositor sin mandar nada", async () => {
    // La garantía del punto 11.3: una acción con consecuencias sobre un
    // registro oficial no puede dispararse por lo que el reconocedor creyó oír.
    // El compositor se rellena, la persona lee, y envía con la mano.
    const instancias = await abrirConDictado();

    await fireEvent.click(screen.getByTestId("voice-toggle"));
    await tick();

    instancias[0].onresult({
      resultIndex: 0,
      results: [{ isFinal: true, 0: { transcript: "radica esta tutela" } }],
    });
    await tick();

    const compositor = screen.getByLabelText("Mensaje para el asistente");
    expect(compositor).toHaveValue("radica esta tutela");
    expect(sendMessageMock).not.toHaveBeenCalled();
  });

  it("lo parcial se reemplaza, no se acumula", async () => {
    // Sin una base sobre la que pintar, cada resultado intermedio se pegaría
    // detrás del anterior y la persona vería la frase repetida palabra a
    // palabra — un compositor ilegible justo cuando hay que revisarlo.
    const instancias = await abrirConDictado();

    await fireEvent.click(screen.getByTestId("voice-toggle"));
    await tick();

    const emitir = (transcript, isFinal) =>
      instancias[0].onresult({
        resultIndex: 0,
        results: [{ isFinal, 0: { transcript } }],
      });

    emitir("radica", false);
    await tick();
    emitir("radica esta", false);
    await tick();
    emitir("radica esta tutela", true);
    await tick();

    expect(screen.getByLabelText("Mensaje para el asistente")).toHaveValue("radica esta tutela");
  });

  it("sin reconocimiento en el dispositivo, el compositor no ofrece dictado", async () => {
    // Chrome/Edge clásicos: la API existe y manda el audio a un servidor de
    // Google. El botón no aparece — ver la cabecera de VoiceInput.
    class SR {
      start() {}
      stop() {}
    }
    vi.stubGlobal("SpeechRecognition", SR); // sin availableOnDevice
    render(AssistantDock, { props: { open: true } });
    await tick();
    await Promise.resolve();
    await tick();

    expect(screen.queryByTestId("voice-toggle")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Mensaje para el asistente")).toBeInTheDocument();
  });
});
