<!--
  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
  Contacto: aurigadl@gmail.com

  Software libre: este archivo se distribuye bajo la Licencia Pública General
  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
-->

<!--
  SignAssistant.svelte — Asistente de firma del turno activo de una cadena
  (E06 Inc.2, RF-FIR-13, Fase 3 cierre de desfase API↔UI).

  Cubre el flujo completo: Confirmar → Verificación (OTP, solo si el backend
  la exige) → Firmar → Comprobante.

  Por qué existe: `POST /requests/{id}/sign` puede responder 409
  `2fa_requerido` (objetos RESERVADA/CLASIFICADA, o `firma_2fa_required`
  activo) — antes de este componente ese reto era INALCANZABLE desde la UI
  (no había ningún formulario para `POST .../challenge`). Este componente
  intenta firmar primero SIN segundo factor (camino rápido, objetos
  PUBLICA); solo si el backend lo exige pasa al paso de verificación.

  Dos verdades del dominio que este componente respeta (frontend/CLAUDE.md):
    - El acto criptográfico ocurre en SERVIDOR — este componente nunca
      calcula ni maneja material de firma, solo reenvía el código OTP que el
      usuario introduce.
    - La firma NO está acreditada (Ley 527/1999 art. 7, no art. 28) y no hay
      control exclusivo del firmante (custodia servidor) — el paso final
      ("Comprobante") lo dice explícitamente, nunca se calla.

  Props:
    open          boolean  — controla la visibilidad (Modal interno)
    solicitudId   string   — UUID de la solicitud cuyo turno activo se firma
    titulo        string   — título descriptivo del documento (solo UI)

  Eventos:
    on:close   — el usuario cerró el asistente (en cualquier paso)
    on:signed  — firma completada; detail = SolicitudResponse actualizada
                 (incluye el firma_id del turno recién firmado en items[])
-->
<script>
  import { createEventDispatcher, onDestroy } from "svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import Stepper from "$lib/components/ui/Stepper.svelte";
  import Button from "$lib/components/ui/Button.svelte";

  export let open = false;
  export let solicitudId = "";
  export let titulo = "";

  const dispatch = createEventDispatcher();

  const STEPS = ["Confirmar", "Verificación", "Comprobante"];

  /** @type {0|1|2} */
  let step = 0;

  // ---------------------------------------------------------------------------
  // Paso 0: confirmar y primer intento de firma (camino rápido sin 2FA)
  // ---------------------------------------------------------------------------
  let signing = false;
  /** @type {string} */
  let confirmError = "";

  // ---------------------------------------------------------------------------
  // Paso 1: verificación OTP (solo si el backend la exigió)
  // ---------------------------------------------------------------------------
  let requestingChallenge = false;
  /** @type {import('$lib/types/api.js').SignatureChallengeResponse|null} */
  let challenge = null;
  let otpValue = "";
  let otpError = "";
  let intentosRestantes = null;
  let remainingSeconds = 0;
  /** @type {ReturnType<typeof setInterval>|null} */
  let countdownTimer = null;

  // ---------------------------------------------------------------------------
  // Paso 2: comprobante
  // ---------------------------------------------------------------------------
  /** @type {import('$lib/types/api.js').SignatureRequestResponse|null} */
  let resultado = null;

  function stopCountdown() {
    if (countdownTimer) {
      clearInterval(countdownTimer);
      countdownTimer = null;
    }
  }

  function startCountdown(/** @type {string} */ expiraAtIso) {
    stopCountdown();
    const tick = () => {
      const remainingMs = new Date(expiraAtIso).getTime() - Date.now();
      remainingSeconds = Math.max(0, Math.round(remainingMs / 1000));
      if (remainingSeconds <= 0) stopCountdown();
    };
    tick();
    countdownTimer = setInterval(tick, 1000);
  }

  onDestroy(stopCountdown);

  function formatRemaining(/** @type {number} */ s) {
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${String(r).padStart(2, "0")}`;
  }

  /**
   * Traduce un error del gateway al paso/mensaje correcto. Nunca da por
   * firmado ni oculta un fallo de red bajo un mensaje de "sin firmas".
   *
   * @param {number} status_
   * @param {{error?: string; detail?: string; intentos_restantes?: number}} result
   */
  function classifyError(status_, result) {
    const code = result?.error ?? "";

    if (status_ === 409 && code === "2fa_requerido") {
      return { kind: "need_2fa" };
    }
    if (status_ === 401 && code === "otp_incorrecto") {
      return {
        kind: "otp_error",
        message: "El código introducido no es correcto.",
        intentosRestantes: result?.intentos_restantes ?? null,
      };
    }
    if (status_ === 409 && code === "challenge_expirado") {
      return {
        kind: "otp_error",
        message: "El código expiró. Solicite uno nuevo.",
        expired: true,
      };
    }
    if (status_ === 409 && code === "challenge_bloqueado") {
      return {
        kind: "otp_error",
        message: "Se agotaron los intentos para este código. Solicite uno nuevo.",
        expired: true,
      };
    }
    if (
      status_ === 409 &&
      [
        "challenge_invalido",
        "challenge_ajeno",
        "challenge_acto_distinto",
        "challenge_documento_distinto",
        "challenge_consumido",
      ].includes(code)
    ) {
      return {
        kind: "otp_error",
        message: "El código ya no es válido para esta firma. Solicite uno nuevo.",
        expired: true,
      };
    }
    if (status_ === 403 && (code === "no_es_su_turno" || code === "no_es_firmante")) {
      return {
        kind: "fatal",
        message:
          "Este documento ya no está en tu turno. Cierra el asistente y actualiza la bandeja.",
      };
    }
    if (status_ === 409 && code === "solicitud_no_pendiente") {
      return {
        kind: "fatal",
        message: "Esta solicitud ya no está pendiente. Cierra el asistente y actualiza la bandeja.",
      };
    }
    if (status_ === 422 && code === "firma_xades_requerida") {
      return {
        kind: "fatal",
        message:
          "Este acto requiere un certificado de firma personal activo que tu usuario no tiene. Contacta al administrador para completar el trámite de emisión.",
      };
    }
    return {
      kind: "fatal",
      message: result?.detail ?? `No se pudo firmar (error ${status_}).`,
    };
  }

  /**
   * Intenta firmar. `body` opcional lleva challenge_id/otp cuando ya se
   * resolvió el segundo factor.
   *
   * @param {{challenge_id?: string; otp?: string}} [body]
   */
  async function attemptSign(body) {
    try {
      const res = await fetch(`/firmas/api/${encodeURIComponent(solicitudId)}/sign`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body ?? {}),
      });
      const result = await res.json().catch(() => ({}));

      if (res.ok) {
        resultado = result;
        step = 2;
        dispatch("signed", result);
        return;
      }

      const outcome = classifyError(res.status, result);
      if (outcome.kind === "need_2fa") {
        await solicitarChallenge();
        return;
      }
      if (outcome.kind === "otp_error") {
        otpError = outcome.message;
        intentosRestantes = outcome.intentosRestantes ?? null;
        if (outcome.expired) {
          challenge = null;
          stopCountdown();
        }
        step = 1;
        return;
      }
      // fatal — no se puede continuar el asistente
      confirmError = outcome.message;
      step = 0;
    } catch {
      // Fallo de red: NUNCA se interpreta como éxito ni como "sin firmas".
      const message =
        "No se pudo conectar al servidor de firmas. Verifica tu conexión e inténtalo de nuevo.";
      if (step === 1) {
        otpError = message;
      } else {
        confirmError = message;
      }
    }
  }

  async function iniciarFirma() {
    confirmError = "";
    signing = true;
    await attemptSign();
    signing = false;
  }

  async function solicitarChallenge() {
    requestingChallenge = true;
    otpError = "";
    otpValue = "";
    try {
      const res = await fetch(`/firmas/api/${encodeURIComponent(solicitudId)}/challenge`, {
        method: "POST",
      });
      const result = await res.json().catch(() => ({}));

      if (res.ok) {
        challenge = result;
        intentosRestantes = null;
        startCountdown(result.expira_at);
        step = 1;
      } else if (res.status === 429) {
        otpError =
          "Ya se envió un código recientemente. Espera un momento antes de solicitar otro.";
        step = 1;
      } else {
        confirmError =
          result?.detail ?? "No se pudo emitir el código de verificación. Inténtalo de nuevo.";
        step = 0;
      }
    } catch {
      confirmError = "No se pudo conectar al servidor de firmas para emitir el código.";
      step = 0;
    } finally {
      requestingChallenge = false;
    }
  }

  async function confirmarOtp() {
    otpError = "";
    if (!otpValue.trim()) {
      otpError = "Introduce el código recibido por correo.";
      return;
    }
    if (!challenge) {
      otpError = "El código ya no es válido. Solicita uno nuevo.";
      return;
    }
    signing = true;
    await attemptSign({ challenge_id: challenge.challenge_id, otp: otpValue.trim() });
    signing = false;
  }

  function handleClose() {
    dispatch("close");
  }
</script>

<Modal {open} title="Firmar documento" size="sm" on:close={handleClose} closeOnBackdrop={!signing}>
  <Stepper steps={STEPS} current={step} canGoBack={false} />

  <div class="sign-assistant__body">
    {#if titulo}
      <p class="sign-assistant__doc">
        <i class="fas fa-file-signature" aria-hidden="true"></i>
        {titulo}
      </p>
    {/if}

    {#if step === 0}
      <p class="sign-assistant__info">
        <i class="fas fa-exclamation-circle" aria-hidden="true"></i>
        Vas a firmar electrónicamente este documento. La firma tiene efectos jurídicos y no se puede deshacer.
      </p>
      <p class="sign-assistant__legal">
        Firma bajo Ley 527/1999 art. 7 (reconocimiento general), sin acreditación ONAC. La clave de
        firma se custodia en el servidor institucional.
      </p>
      {#if confirmError}
        <p class="sign-assistant__error" role="alert">{confirmError}</p>
      {/if}
    {:else if step === 1}
      <p class="sign-assistant__info">
        <i class="fas fa-shield-alt" aria-hidden="true"></i>
        Este documento requiere un segundo factor de verificación antes de firmarse.
      </p>

      {#if requestingChallenge}
        <p class="sign-assistant__status" aria-live="polite">Enviando código por correo…</p>
      {:else if challenge}
        <p class="sign-assistant__status" aria-live="polite">
          Enviamos un código a <strong>{challenge.canal}</strong>.
          {#if remainingSeconds > 0}
            Vence en <strong>{formatRemaining(remainingSeconds)}</strong>.
          {:else}
            <span class="sign-assistant__expired">El código expiró.</span>
          {/if}
        </p>

        <label class="sign-assistant__otp-label" for="sign-assistant-otp">
          Código de verificación
        </label>
        <input
          id="sign-assistant-otp"
          class="sign-assistant__otp-input"
          type="text"
          inputmode="numeric"
          autocomplete="one-time-code"
          bind:value={otpValue}
          disabled={remainingSeconds <= 0 || signing}
          aria-describedby={otpError ? "sign-assistant-otp-error" : undefined}
          aria-invalid={!!otpError}
        />

        {#if otpError}
          <p id="sign-assistant-otp-error" class="sign-assistant__error" role="alert">
            {otpError}
            {#if intentosRestantes !== null}
              ({intentosRestantes}
              {intentosRestantes === 1 ? "intento restante" : "intentos restantes"})
            {/if}
          </p>
        {/if}

        {#if remainingSeconds <= 0}
          <Button
            variant="ghost"
            size="sm"
            on:click={solicitarChallenge}
            loading={requestingChallenge}
          >
            Reenviar código
          </Button>
        {/if}
      {:else}
        <p class="sign-assistant__error" role="alert">
          {otpError || "No se pudo emitir el código."}
        </p>
        <Button
          variant="ghost"
          size="sm"
          on:click={solicitarChallenge}
          loading={requestingChallenge}
        >
          Solicitar código
        </Button>
      {/if}
    {:else if step === 2 && resultado}
      <p class="sign-assistant__success" role="status">
        <i class="fas fa-check-circle" aria-hidden="true"></i>
        Documento firmado correctamente.
      </p>
      <p class="sign-assistant__legal">
        Comprobante generado por el sistema — firma bajo Ley 527/1999 art. 7, sin acreditación ONAC,
        custodia de la clave en servidor institucional. No implica control exclusivo del firmante en
        el sentido del art. 28.
      </p>
    {/if}
  </div>

  <svelte:fragment slot="footer">
    {#if step === 0}
      <Button variant="ghost" on:click={handleClose} disabled={signing}>Cancelar</Button>
      <Button variant="primary" icon="fas fa-signature" loading={signing} on:click={iniciarFirma}>
        Firmar
      </Button>
    {:else if step === 1}
      <Button variant="ghost" on:click={handleClose} disabled={signing}>Cancelar</Button>
      <Button
        variant="primary"
        icon="fas fa-check"
        loading={signing}
        disabled={!challenge || remainingSeconds <= 0}
        on:click={confirmarOtp}
      >
        Confirmar código
      </Button>
    {:else}
      <Button variant="primary" on:click={handleClose}>Cerrar</Button>
    {/if}
  </svelte:fragment>
</Modal>

<style lang="scss">
  .sign-assistant {
    &__body {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-3);
      margin-top: var(--op-space-4);
    }

    &__doc {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
      margin: 0;
    }

    &__info {
      display: flex;
      align-items: flex-start;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-warning-fg);
      background: var(--op-warning-bg);
      border: 1px solid var(--op-warning);
      border-radius: var(--op-radius-sm);
      padding: var(--op-space-3);
      margin: 0;
    }

    &__legal {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__status {
      font-size: var(--op-font-sm);
      color: var(--op-text);
      margin: 0;
    }

    &__expired {
      color: var(--op-error);
      font-weight: var(--op-weight-medium);
    }

    &__otp-label {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
    }

    &__otp-input {
      font-family: var(--op-font-family);
      font-size: var(--op-font-lg);
      letter-spacing: 0.3em;
      text-align: center;
      height: 44px;
      padding: 0 var(--op-space-3);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      color: var(--op-text);
      background: var(--op-surface);

      &:focus {
        outline: none;
        border-color: var(--op-primary);
        box-shadow: 0 0 0 3px var(--op-focus-ring);
      }

      &:disabled {
        background: var(--op-bg);
        color: var(--op-text-secondary);
      }
    }

    &__error {
      font-size: var(--op-font-sm);
      color: var(--op-error);
      margin: 0;
    }

    &__success {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-md);
      font-weight: var(--op-weight-medium);
      color: var(--op-success);
      margin: 0;
    }
  }
</style>
