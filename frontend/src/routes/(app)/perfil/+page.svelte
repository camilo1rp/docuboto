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
  /perfil — Mi perfil (Fase 3, cierre de desfase API↔UI).

  Antes de esta pantalla, el segundo factor TOTP (E06) y la credencial de
  firma personal PKI (E17/F4) estaban implementados en el backend pero eran
  INALCANZABLES desde la interfaz. Cuatro secciones, EN ESTE ORDEN
  (el orden importa — ver nota de prerrequisito):

    1. Identidad y permisos efectivos — solo lectura, de `data.user`
       (ya resuelto por `(app)/+layout.server.js` desde `/auth/me`).
    2. Segundo factor (TOTP) — PRERREQUISITO de la sección 3.
    3. Credencial de firma (PKI) — oculta sus acciones si el TOTP no está
       'activo' (el backend exige `step_up_totp_code` para revocar SIEMPRE,
       y para re-enrolar sobre una credencial 'activo'); sin 2FO, cualquier
       acción de escritura fallaría contra el backend con `no_activo`. La
       pantalla NO deja que el usuario choque contra ese error: explica el
       prerrequisito y enlaza a la sección 2.
    4. Dependencia activa — `DependencySwitcher.svelte`, misma fuente que
       usa el conmutador del topbar (`AppLayout.svelte`).

  TRES VERDADES DEL DOMINIO que este archivo respeta (no renegociar sin
  volver a leer los routers/servicios de auth-service):

    - La clave privada de firma NUNCA se genera ni pasa por el navegador.
      Se genera y custodia CIFRADA en el servidor; el enrolamiento solo
      devuelve el CSR (información pública) — cero criptografía aquí.
    - Custodia en servidor ⇒ SIN control exclusivo del firmante en sentido
      legal estricto, y la firma resultante NO está acreditada (Ley
      527/1999 art. 7 — equivalencia funcional; NO art. 28/Decreto 333/2014
      ONAC). El texto de esta pantalla es preciso al respecto: ni alarmista
      ni vende de más. Mismo lenguaje que `docs/es/normativa.md` y el resto
      del proyecto ("no acreditado").
    - Re-enrolar sobre una credencial 'activo' la REVOCA de inmediato
      (`SigningKeyService.enroll`, docstring): la credencial nueva queda
      'pendiente_emision', INÚTIL hasta una reemisión que es un TRÁMITE
      FUERA DE LÍNEA (el CSR se entrega a un administrador de la PKI
      institucional). Por eso el re-enroll exige escribir
      `REENROLL_CONFIRM_PHRASE` a mano, no solo un clic.

  D-05: la ejecución va a `/perfil/api/*` (nunca al gateway desde el
  cliente); `+page.server.js` ya trae los estados iniciales por SSR.
-->
<script>
  import { onMount, tick } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import { can, sessionUser } from "$lib/stores/auth.js";
  import { notifySuccess, notifyError } from "$lib/stores/notifications.js";
  import { isReenrollConfirmed, REENROLL_CONFIRM_PHRASE } from "$lib/utils/signingKeyConfirm.js";

  import Stepper from "$lib/components/ui/Stepper.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import Card from "$lib/components/ui/Card.svelte";
  import DependencySwitcher from "$lib/components/features/perfil/DependencySwitcher.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";

  // Tipado manual (no `import('./$types').PageData`): `data.user` viene del
  // `load` de `(app)/+layout.server.js` (un ancestro), no del propio
  // `+page.server.js` — mismo patrón que `radicar/+page.svelte` para evitar
  // que svelte-check infiera `{}` al no cruzar el límite del layout en JS puro.
  /** @type {{ user: any, totpStatus: any, totpError: string|null, signingKeyStatus: any, signingKeyError: string|null }} */
  export let data;

  $: user = data.user ?? {};
  $: permissions = user.permissions ?? [];
  // `can(perm, 1)` y no `permissions.includes(...)`: el nivel CRUD es lo que
  // separa "tiene el permiso en modo lectura" de "puede ejecutar la acción", y
  // el helper es fail-closed (`?? 0`) si `permission_levels` no llega. El gate
  // real de `enroll` es `require_permission("PERM_FIRMA")`, cuyo mínimo es 1 —
  // exigir más aquí sería el mismo defecto al revés: negar de más, en silencio.
  $: puedeFirmar = $sessionUser ? can("PERM_FIRMA", 1) : false;
  $: roles = user.roles ?? [];

  // ---------------------------------------------------------------------------
  // Sección 2 — TOTP
  // ---------------------------------------------------------------------------
  /** @type {{estado: string, activated_at?: string|null, locked_until?: string|null, recovery_codes_restantes?: number}} */
  let totp = data.totpStatus ?? { estado: "no_configurado" };
  // Códigos de recuperación (E06, migración 015). Solo tienen valor en el
  // instante en que el backend los devuelve: en la base queda su hash, así que
  // no hay forma de volver a consultarlos, solo de regenerarlos.
  /** @type {string[]} */
  let recoveryCodes = [];
  let recoveryRegenerating = false;
  let recoveryStepUp = "";
  let recoveryStepUpError = "";
  $: recoveryRestantes = totp?.recovery_codes_restantes ?? 0;
  // Umbral de aviso: con 2 o menos, un par de reemplazos de teléfono dejan al
  // usuario sin salida. Se avisa antes de que llegue a cero, no cuando ya no
  // hay margen.
  $: recoveryEscasos = totp?.estado === "activo" && recoveryRestantes <= 2;

  async function regenerateRecoveryCodes() {
    recoveryStepUpError = "";
    const codigo = recoveryStepUp.trim();
    if (!codigo) {
      recoveryStepUpError = "Ingrese su código de 6 dígitos o uno de recuperación.";
      return;
    }
    recoveryRegenerating = true;
    try {
      // Se distingue por FORMA, no por una casilla que el usuario tenga que
      // marcar: 6 dígitos es un TOTP, cualquier otra cosa es un código de
      // recuperación. Quien perdió el teléfono no debería tener que explicarle
      // a la interfaz qué tipo de código está pegando.
      const esTotp = /^\d{6}$/.test(codigo);
      const res = await fetch("/perfil/api/totp/recovery-codes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          esTotp ? { step_up_totp_code: codigo } : { step_up_recovery_code: codigo },
        ),
      });
      const resData = await res.json().catch(() => ({}));
      if (!res.ok) {
        recoveryStepUpError = mensajeError(resData, "No se pudieron regenerar los códigos.");
        return;
      }
      recoveryCodes = resData.recovery_codes ?? [];
      totp = { ...totp, recovery_codes_restantes: recoveryCodes.length };
      recoveryStepUp = "";
      notifySuccess("Códigos de recuperación regenerados. Los anteriores ya no sirven.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      recoveryRegenerating = false;
    }
  }
  $: totpLoadError = data.totpError;

  const TOTP_STEP_LABELS = ["Generar secreto", "Verificar código", "Activo"];
  $: totpStepIndex = totp.estado === "activo" ? 2 : totp.estado === "pendiente_activacion" ? 1 : 0;
  $: totpActive = totp.estado === "activo";

  // Revelado UNA SOLA VEZ al enrolar — nunca se vuelve a pedir al backend.
  let pendingOtpauthUri = "";
  $: pendingSecret = (() => {
    if (!pendingOtpauthUri) return "";
    try {
      const url = new URL(pendingOtpauthUri.replace(/^otpauth:/, "https:"));
      const raw = url.searchParams.get("secret") ?? "";
      return raw.replace(/(.{4})/g, "$1 ").trim();
    } catch {
      return "";
    }
  })();

  let totpEnrolling = false;
  let totpStepUpForReenroll = ""; // step-up para re-enrolar sobre 'activo'
  let totpActivateCode = "";
  let totpActivating = false;
  let totpRevokeCode = "";
  let totpRevoking = false;
  let totpRevokeOpen = false;

  // Errores de validación asociados al campo (revisión UX lote 2, punto 4):
  // antes solo salían como toast, sin `aria-invalid`/`aria-describedby` — quien
  // usa lector de pantalla no sabía qué campo falló. `FormField` ya soporta
  // `error`; aquí solo faltaba conectarlo.
  let totpReenrollError = "";
  let totpActivateError = "";
  let totpRevokeError = "";

  async function enrollTotp() {
    totpReenrollError = "";
    if (totpActive && !/^\d{6}$/.test(totpStepUpForReenroll)) {
      totpReenrollError =
        "Ingrese el código actual de 6 dígitos para reemplazar el segundo factor.";
      return;
    }
    totpEnrolling = true;
    try {
      const res = await fetch("/perfil/api/totp/enroll", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(totpActive ? { step_up_totp_code: totpStepUpForReenroll } : {}),
      });
      const resData = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = mensajeError(resData, "No se pudo generar el segundo factor.");
        if (totpActive) {
          totpReenrollError = detail;
        } else {
          notifyError(detail);
        }
        return;
      }
      totp = { estado: "pendiente_activacion" };
      pendingOtpauthUri = resData.otpauth_uri ?? "";
      totpStepUpForReenroll = "";
      notifySuccess(
        "Nuevo secreto generado. Regístrelo en su app autenticadora y verifique el código.",
      );
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      totpEnrolling = false;
    }
  }

  async function activateTotp() {
    totpActivateError = "";
    if (!/^\d{6}$/.test(totpActivateCode)) {
      totpActivateError = "El código debe tener 6 dígitos.";
      return;
    }
    totpActivating = true;
    try {
      const res = await fetch("/perfil/api/totp/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: totpActivateCode }),
      });
      const resData = await res.json().catch(() => ({}));
      if (!res.ok) {
        totpActivateError = mensajeError(resData, "Código incorrecto o vencido.");
        return;
      }
      totp = {
        estado: "activo",
        activated_at: resData.activated_at,
        recovery_codes_restantes: (resData.recovery_codes ?? []).length,
      };
      pendingOtpauthUri = "";
      totpActivateCode = "";
      // Los códigos llegan aquí y en ningún otro sitio: el backend solo guarda
      // su hash, así que si el usuario cierra esta pantalla sin apuntarlos, la
      // única salida es regenerarlos. Por eso se muestran de forma insistente
      // y no como un aviso secundario.
      recoveryCodes = resData.recovery_codes ?? [];
      notifySuccess("Segundo factor activado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      totpActivating = false;
    }
  }

  async function revokeTotp() {
    totpRevokeError = "";
    if (totpActive && !/^\d{6}$/.test(totpRevokeCode)) {
      totpRevokeError = "Ingrese el código actual de 6 dígitos para revocar.";
      return;
    }
    totpRevoking = true;
    try {
      const res = await fetch("/perfil/api/totp", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(totpActive ? { step_up_totp_code: totpRevokeCode } : {}),
      });
      const resData = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = mensajeError(resData, "No se pudo revocar el segundo factor.");
        if (totpActive) {
          totpRevokeError = detail;
        } else {
          notifyError(detail);
        }
        return;
      }
      totp = { estado: "no_configurado" };
      pendingOtpauthUri = "";
      totpRevokeCode = "";
      totpRevokeOpen = false;
      notifySuccess("Segundo factor revocado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      totpRevoking = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Sección 3 — Credencial de firma (PKI)
  // ---------------------------------------------------------------------------
  /** @type {{estado: string, cert_pem?: string|null, serial?: string|null, subject_dn?: string|null, not_before?: string|null, not_after?: string|null, activated_at?: string|null}} */
  let signingKey = data.signingKeyStatus ?? { estado: "no_configurado" };
  $: signingKeyLoadError = data.signingKeyError;

  const SIGNING_STEP_LABELS = ["Solicitar credencial (CSR)", "Emisión fuera de línea", "Activa"];
  $: signingStepIndex =
    signingKey.estado === "activo" ? 2 : signingKey.estado === "pendiente_emision" ? 1 : 0;
  $: signingActive = signingKey.estado === "activo";
  $: signingHasCredential =
    signingKey.estado === "activo" || signingKey.estado === "pendiente_emision";

  // Revelado UNA SOLA VEZ al enrolar (no hay endpoint que lo devuelva de nuevo).
  let csrPem = "";

  let signingEnrolling = false;
  let signingReenrollConfirm = "";
  let signingReenrollStepUp = "";
  let signingRevokeOpen = false;
  let signingRevokeStepUp = "";
  let signingRevokeMotivo = "";
  let signingRevoking = false;

  // Errores de validación asociados al campo (revisión UX lote 2, punto 4).
  let signingReenrollStepUpError = "";
  let signingReenrollConfirmError = "";
  let signingRevokeStepUpError = "";

  $: reenrollReady =
    !signingActive ||
    (isReenrollConfirmed(signingReenrollConfirm) && /^\d{6}$/.test(signingReenrollStepUp));

  async function enrollSigningKey() {
    if (!totpActive) {
      notifyError("Active primero el segundo factor (TOTP).");
      return;
    }
    signingReenrollStepUpError = "";
    signingReenrollConfirmError = "";
    if (signingActive && !reenrollReady) {
      if (!isReenrollConfirmed(signingReenrollConfirm)) {
        signingReenrollConfirmError = `Escriba exactamente "${REENROLL_CONFIRM_PHRASE}" para confirmar.`;
      }
      if (!/^\d{6}$/.test(signingReenrollStepUp)) {
        signingReenrollStepUpError = "Ingrese el código actual de 6 dígitos (2FA).";
      }
      return;
    }
    signingEnrolling = true;
    try {
      const res = await fetch("/perfil/api/signing-key/enroll", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(signingActive ? { step_up_totp_code: signingReenrollStepUp } : {}),
      });
      const resData = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = mensajeError(resData, "No se pudo generar la solicitud de credencial.");
        if (signingActive) {
          signingReenrollStepUpError = detail;
        } else {
          notifyError(detail);
        }
        return;
      }
      signingKey = { estado: "pendiente_emision" };
      csrPem = resData.csr_pem ?? "";
      signingReenrollConfirm = "";
      signingReenrollStepUp = "";
      notifySuccess(
        "Solicitud de credencial (CSR) generada. Entréguela al administrador de la PKI para su emisión.",
      );
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      signingEnrolling = false;
    }
  }

  async function revokeSigningKey() {
    signingRevokeStepUpError = "";
    if (!/^\d{6}$/.test(signingRevokeStepUp)) {
      signingRevokeStepUpError = "Ingrese el código actual de 6 dígitos para revocar.";
      return;
    }
    signingRevoking = true;
    try {
      const res = await fetch("/perfil/api/signing-key", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          step_up_totp_code: signingRevokeStepUp,
          ...(signingRevokeMotivo.trim() ? { motivo: signingRevokeMotivo.trim() } : {}),
        }),
      });
      const resData = await res.json().catch(() => ({}));
      if (!res.ok) {
        signingRevokeStepUpError = mensajeError(
          resData,
          "No se pudo revocar la credencial de firma.",
        );
        return;
      }
      signingKey = { estado: "revocado" };
      csrPem = "";
      signingRevokeStepUp = "";
      signingRevokeMotivo = "";
      signingRevokeOpen = false;
      notifySuccess("Credencial de firma revocada.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      signingRevoking = false;
    }
  }

  function copyText(text) {
    if (!text || typeof navigator === "undefined" || !navigator.clipboard) return;
    navigator.clipboard.writeText(text).then(
      () => notifySuccess("Copiado al portapapeles."),
      () => notifyError("No se pudo copiar."),
    );
  }

  /** @type {HTMLElement|null} */
  let totpSectionEl = null;
  async function scrollToTotp() {
    await tick();
    totpSectionEl?.scrollIntoView({ behavior: "smooth", block: "start" });
    totpSectionEl?.focus();
  }

  function formatDate(iso) {
    if (!iso) return "—";
    try {
      return formatearFechaHora(iso);
    } catch {
      return iso;
    }
  }

  onMount(() => {
    setBreadcrumbs([{ label: "Mi perfil" }]);
  });
</script>

<svelte:head>
  <title>Mi perfil — OrpycaMCP</title>
</svelte:head>

<div class="perfil-page">
  <header class="perfil-page__header">
    <h1 class="perfil-page__title">Mi perfil</h1>
    <p class="perfil-page__subtitle">
      Identidad, segundo factor, credencial de firma electrónica y dependencia activa.
    </p>
  </header>

  <!-- ════════════════════════════════════════════════════════════════ -->
  <!-- 1. Identidad y permisos efectivos (solo lectura)                  -->
  <!-- ════════════════════════════════════════════════════════════════ -->
  <Card title="Identidad">
    <dl class="perfil-identity">
      <div class="perfil-identity__row">
        <dt>Usuario</dt>
        <dd>{user.name ?? user.preferred_username ?? "—"}</dd>
      </div>
      <div class="perfil-identity__row">
        <dt>Correo</dt>
        <dd>{user.email ?? "—"}</dd>
      </div>
      <div class="perfil-identity__row">
        <dt>Tenant</dt>
        <dd>{user.tenant_slug ?? "—"}</dd>
      </div>
      {#if roles.length > 0}
        <div class="perfil-identity__row">
          <dt>Roles</dt>
          <dd class="perfil-chips">
            {#each roles as r (r)}<span class="perfil-chip">{r}</span>{/each}
          </dd>
        </div>
      {/if}
      <div class="perfil-identity__row">
        <dt>Permisos efectivos</dt>
        <dd class="perfil-chips">
          {#if permissions.length === 0}
            <span class="perfil-identity__muted">Sin permisos asignados.</span>
          {:else}
            {#each permissions as p (p)}<span class="perfil-chip perfil-chip--permission">{p}</span
              >{/each}
          {/if}
        </dd>
      </div>
    </dl>
  </Card>

  <!-- ════════════════════════════════════════════════════════════════ -->
  <!-- 2. Segundo factor (TOTP) — prerrequisito de la sección 3          -->
  <!-- ════════════════════════════════════════════════════════════════ -->
  <section
    id="perfil-totp"
    class="perfil-section"
    aria-labelledby="perfil-totp-title"
    bind:this={totpSectionEl}
    tabindex="-1"
  >
    <h2 id="perfil-totp-title" class="perfil-section__title">
      <i class="fas fa-shield-halved" aria-hidden="true"></i> Segundo factor (TOTP)
    </h2>
    <p class="perfil-section__desc">
      Código de un solo uso (app autenticadora) que protege las acciones más sensibles: revocar o
      renovar su credencial de firma electrónica siempre lo exige.
    </p>

    {#if totpLoadError}
      <div class="perfil-page__error" role="alert">{totpLoadError}</div>
    {/if}

    <Stepper steps={TOTP_STEP_LABELS} current={totpStepIndex} canGoBack={false} />

    {#if totp.estado === "no_configurado"}
      <p class="perfil-section__status">No tiene un segundo factor configurado.</p>
      <Button variant="primary" loading={totpEnrolling} on:click={enrollTotp}>
        Generar secreto
      </Button>
    {:else if totp.estado === "pendiente_activacion"}
      {#if pendingOtpauthUri}
        <div class="perfil-totp-secret">
          <p>
            Registre esta clave en su app autenticadora (Google Authenticator, Authy, etc.) — solo
            se muestra <strong>una vez</strong>. Si la pierde antes de activar, genere una nueva con
            el botón de abajo.
          </p>
          <div class="perfil-totp-secret__value">
            <code>{pendingSecret}</code>
            <Button
              variant="ghost"
              size="sm"
              icon="fas fa-copy"
              on:click={() => copyText(pendingSecret)}
            >
              Copiar
            </Button>
          </div>
        </div>
      {:else}
        <p class="perfil-section__status">
          Ya generó un secreto en una sesión anterior; si no lo registró, genere uno nuevo.
        </p>
        <Button variant="secondary" loading={totpEnrolling} on:click={enrollTotp}>
          Generar un nuevo secreto
        </Button>
      {/if}

      <form class="perfil-inline-form" on:submit|preventDefault={activateTotp}>
        <FormField
          id="totp-activate-code"
          label="Código de 6 dígitos de su app autenticadora"
          bind:value={totpActivateCode}
          placeholder="000000"
          inputmode="numeric"
          autocomplete="one-time-code"
          error={totpActivateError}
        />
        <Button type="submit" variant="primary" loading={totpActivating}>Activar</Button>
      </form>
    {:else if totp.estado === "activo"}
      <p class="perfil-section__status perfil-section__status--ok">
        <i class="fas fa-circle-check" aria-hidden="true"></i> Activo
        {#if totp.activated_at}desde {formatDate(totp.activated_at)}{/if}
      </p>

      <!-- Códigos de recuperación (E06, migración 015) -->
      <div class="perfil-recovery">
        <h3 class="perfil-recovery__title">
          <i class="fas fa-key" aria-hidden="true"></i> Códigos de recuperación
        </h3>
        <p class="perfil-recovery__desc">
          Sirven para desactivar el segundo factor o pasarlo a un teléfono nuevo <strong
            >si pierde el actual</strong
          >. Cada uno se usa una sola vez.
          <strong>No sirven para firmar</strong>: firmar sigue exigiendo el código de su
          dispositivo.
        </p>

        {#if recoveryCodes.length > 0}
          <div class="perfil-recovery__codes" role="group" aria-label="Códigos de recuperación">
            <p class="perfil-recovery__once">
              <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
              Guárdelos ahora en un lugar seguro. <strong>No se volverán a mostrar</strong> — si los pierde,
              la única salida es generar un juego nuevo.
            </p>
            <ol class="perfil-recovery__list">
              {#each recoveryCodes as codigo (codigo)}
                <li><code>{codigo}</code></li>
              {/each}
            </ol>
            <Button
              variant="ghost"
              size="sm"
              icon="fas fa-copy"
              on:click={() => copyText(recoveryCodes.join("\n"))}
            >
              Copiar todos
            </Button>
          </div>
        {:else}
          <p class="perfil-section__status" class:perfil-recovery__low={recoveryEscasos}>
            {#if recoveryEscasos}
              <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
            {/if}
            Le quedan <strong>{recoveryRestantes}</strong>
            {recoveryRestantes === 1 ? "código sin usar" : "códigos sin usar"}.
            {#if recoveryEscasos}
              Genere un juego nuevo antes de quedarse sin ninguno.
            {/if}
          </p>
        {/if}

        <details class="perfil-recovery__regen">
          <summary>Generar un juego nuevo</summary>
          <p class="perfil-danger__warning">
            Los códigos anteriores dejan de servir en el momento en que se generan los nuevos.
          </p>
          <form class="perfil-inline-form" on:submit|preventDefault={regenerateRecoveryCodes}>
            <FormField
              id="totp-recovery-stepup"
              label="Código de 6 dígitos, o uno de recuperación que aún tenga"
              bind:value={recoveryStepUp}
              placeholder="000000 · ABCDE-FGHJK-MNPQR-STUVW"
              autocomplete="one-time-code"
              error={recoveryStepUpError}
            />
            <Button type="submit" variant="secondary" loading={recoveryRegenerating}>
              Generar códigos
            </Button>
          </form>
        </details>
      </div>

      <details class="perfil-danger">
        <summary>Reemplazar el segundo factor</summary>
        <p class="perfil-danger__warning">
          Genera un secreto nuevo e invalida el actual. Requiere el código vigente.
        </p>
        <form class="perfil-inline-form" on:submit|preventDefault={enrollTotp}>
          <FormField
            id="totp-reenroll-code"
            label="Código actual de 6 dígitos"
            bind:value={totpStepUpForReenroll}
            placeholder="000000"
            inputmode="numeric"
            autocomplete="one-time-code"
            error={totpReenrollError}
          />
          <Button type="submit" variant="secondary" loading={totpEnrolling}>Reemplazar</Button>
        </form>
      </details>

      <details class="perfil-danger" bind:open={totpRevokeOpen}>
        <summary>Revocar el segundo factor</summary>
        <p class="perfil-danger__warning">
          <span>
            <strong>Sin 2FA no podrá revocar ni renovar su credencial de firma</strong> hasta configurar
            uno nuevo — el backend exige siempre este código para esas acciones.
          </span>
        </p>
        <form class="perfil-inline-form" on:submit|preventDefault={revokeTotp}>
          <FormField
            id="totp-revoke-code"
            label="Código actual de 6 dígitos"
            bind:value={totpRevokeCode}
            placeholder="000000"
            inputmode="numeric"
            autocomplete="one-time-code"
            error={totpRevokeError}
          />
          <Button type="submit" variant="danger" loading={totpRevoking}>Revocar</Button>
        </form>
      </details>
    {/if}
  </section>

  <!-- ════════════════════════════════════════════════════════════════ -->
  <!-- 3. Credencial de firma (PKI)                                      -->
  <!-- ════════════════════════════════════════════════════════════════ -->
  <section class="perfil-section" aria-labelledby="perfil-firma-title">
    <h2 id="perfil-firma-title" class="perfil-section__title">
      <i class="fas fa-signature" aria-hidden="true"></i> Credencial de firma electrónica
    </h2>

    <div class="perfil-legal-note">
      <p>
        Esta credencial permite firmar electrónicamente (XAdES) con una clave <strong
          >generada y custodiada en el servidor</strong
        >, cifrada en reposo — nunca sale de auth-service, y esta pantalla nunca la ve ni la
        transporta.
      </p>
      <p>
        Por tratarse de custodia en servidor, <strong>no hay control exclusivo del firmante</strong>
        en el sentido estricto de la ley, y la firma resultante <strong>no está acreditada</strong>
        ante una entidad de certificación (Decreto 333/2014, Cap. 48). Tiene la equivalencia funcional
        de una firma electrónica (Ley 527/1999, art. 7) — no el nivel de una firma digital con certificado
        acreditado (art. 28).
      </p>
    </div>

    {#if signingKeyLoadError}
      <div class="perfil-page__error" role="alert">{signingKeyLoadError}</div>
    {/if}

    {#if !puedeFirmar}
      <!--
        Espejo del gate real: `POST /auth/me/signing-key/enroll` exige
        `PERM_FIRMA` (`require_permission("PERM_FIRMA")`, min_crud por defecto
        = 1). Sin este bloque la pantalla ofrecía enrolar a cualquier usuario y
        el backend respondía 403 al pulsar — se le dice ANTES, y con el motivo,
        en vez de dejarle descubrirlo. El backend sigue revalidando: esto es la
        capa de honestidad de la interfaz, no la de autorización.
      -->
      <div class="perfil-prereq">
        <i class="fas fa-ban" aria-hidden="true"></i>
        <div>
          <strong>Su cuenta no tiene autorización para firmar.</strong> La credencial de firma solo se
          emite a usuarios con el permiso de firma; solicítelo al administrador de su entidad.
        </div>
      </div>
      {#if signingHasCredential}
        <p class="perfil-section__status">
          Estado actual: <strong>{signingKey?.estado}</strong>. Puede consultarlo, pero no emitir ni
          renovar la credencial sin el permiso de firma.
        </p>
      {/if}
    {:else if !totpActive}
      <div class="perfil-prereq">
        <i class="fas fa-lock" aria-hidden="true"></i>
        <div>
          <strong>Requiere segundo factor activo.</strong> Revocar o renovar esta credencial exige
          siempre un código TOTP vigente; configúrelo primero en la sección de arriba.
          <Button variant="ghost" size="sm" on:click={scrollToTotp}>Ir a Segundo factor</Button>
        </div>
      </div>
      {#if signingHasCredential}
        <p class="perfil-section__status">
          Estado actual: <strong
            >{signingKey.estado === "activo" ? "activa" : "pendiente de emisión"}</strong
          >
          — sin 2FA activo no puede revocarla ni renovarla desde aquí.
        </p>
      {/if}
    {:else}
      <Stepper steps={SIGNING_STEP_LABELS} current={signingStepIndex} canGoBack={false} />

      {#if signingKey.estado === "no_configurado" || signingKey.estado === "revocado"}
        <p class="perfil-section__status">
          {signingKey.estado === "revocado"
            ? "Su credencial anterior fue revocada."
            : "No tiene una credencial de firma."}
        </p>
        <Button variant="primary" loading={signingEnrolling} on:click={enrollSigningKey}>
          Solicitar credencial
        </Button>
      {:else if signingKey.estado === "pendiente_emision"}
        <p class="perfil-section__status">
          Solicitud generada. Un administrador de la PKI institucional debe emitir el certificado
          <strong>fuera de línea</strong> a partir del CSR — esta credencial no sirve para firmar hasta
          entonces.
        </p>
        {#if csrPem}
          <div class="perfil-csr">
            <label for="perfil-csr-text"
              >Solicitud de certificado (CSR) — entréguela al administrador</label
            >
            <textarea id="perfil-csr-text" class="perfil-csr__text" readonly rows="8"
              >{csrPem}</textarea
            >
            <div class="perfil-csr__actions">
              <Button
                variant="ghost"
                size="sm"
                icon="fas fa-copy"
                on:click={() => copyText(csrPem)}
              >
                Copiar CSR
              </Button>
            </div>
          </div>
        {:else}
          <p class="perfil-section__status">
            El CSR ya generado solo se mostró una vez. Si lo perdió, puede solicitar uno nuevo (no
            requiere 2FA porque aún no tiene una credencial activa firmando nada).
          </p>
          <Button variant="secondary" loading={signingEnrolling} on:click={enrollSigningKey}>
            Solicitar un CSR nuevo
          </Button>
        {/if}
      {:else if signingKey.estado === "activo"}
        <p class="perfil-section__status perfil-section__status--ok">
          <i class="fas fa-circle-check" aria-hidden="true"></i> Activa
          {#if signingKey.activated_at}desde {formatDate(signingKey.activated_at)}{/if}
        </p>
        <dl class="perfil-identity">
          {#if signingKey.subject_dn}
            <div class="perfil-identity__row">
              <dt>Titular</dt>
              <dd>{signingKey.subject_dn}</dd>
            </div>
          {/if}
          {#if signingKey.serial}
            <div class="perfil-identity__row">
              <dt>Serial</dt>
              <dd>{signingKey.serial}</dd>
            </div>
          {/if}
          <div class="perfil-identity__row">
            <dt>Vigencia</dt>
            <dd>{formatDate(signingKey.not_before)} — {formatDate(signingKey.not_after)}</dd>
          </div>
        </dl>

        <details class="perfil-danger">
          <summary>Reemplazar la credencial (re-enrolar)</summary>
          <p class="perfil-danger__warning">
            <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
            <strong>
              Al reemplazarla, su credencial activa se revoca DE INMEDIATO. Se queda sin poder
              firmar hasta completar de nuevo el trámite de emisión FUERA DE LÍNEA con el CSR nuevo.
              Esto no es reversible desde aquí.
            </strong>
          </p>
          <form
            class="perfil-inline-form perfil-inline-form--stacked"
            on:submit|preventDefault={enrollSigningKey}
          >
            <FormField
              id="signing-reenroll-stepup"
              label="Código actual de 6 dígitos (2FA)"
              bind:value={signingReenrollStepUp}
              placeholder="000000"
              inputmode="numeric"
              autocomplete="one-time-code"
              error={signingReenrollStepUpError}
            />
            <FormField
              id="signing-reenroll-confirm"
              label={`Escriba "${REENROLL_CONFIRM_PHRASE}" para confirmar que entiende que quedará sin firma`}
              bind:value={signingReenrollConfirm}
              placeholder={REENROLL_CONFIRM_PHRASE}
              error={signingReenrollConfirmError}
            />
            <Button
              type="submit"
              variant="danger"
              disabled={!reenrollReady}
              loading={signingEnrolling}
            >
              Reemplazar credencial
            </Button>
          </form>
        </details>

        <details class="perfil-danger" bind:open={signingRevokeOpen}>
          <summary>Revocar la credencial</summary>
          <p class="perfil-danger__warning">
            Quedará sin poder firmar hasta enrolar una credencial nueva (trámite fuera de línea).
          </p>
          <form
            class="perfil-inline-form perfil-inline-form--stacked"
            on:submit|preventDefault={revokeSigningKey}
          >
            <FormField
              id="signing-revoke-stepup"
              label="Código actual de 6 dígitos (2FA)"
              bind:value={signingRevokeStepUp}
              placeholder="000000"
              inputmode="numeric"
              autocomplete="one-time-code"
              error={signingRevokeStepUpError}
            />
            <FormField
              id="signing-revoke-motivo"
              label="Motivo (opcional)"
              type="textarea"
              bind:value={signingRevokeMotivo}
              hint="Queda registrado en el asiento de auditoría."
            />
            <Button type="submit" variant="danger" loading={signingRevoking}
              >Revocar credencial</Button
            >
          </form>
        </details>
      {/if}
    {/if}
  </section>

  <!-- ════════════════════════════════════════════════════════════════ -->
  <!-- 4. Dependencia activa                                             -->
  <!-- ════════════════════════════════════════════════════════════════ -->
  {#if user.context}
    <section class="perfil-section" aria-labelledby="perfil-depe-title">
      <h2 id="perfil-depe-title" class="perfil-section__title">
        <i class="fas fa-building" aria-hidden="true"></i> Dependencia activa
      </h2>
      <p class="perfil-section__desc">
        La dependencia activa determina desde qué unidad organizacional radica y tramita. Cambiar de
        dependencia no reemite su sesión, pero sí actualiza inmediatamente la navegación.
      </p>
      <DependencySwitcher
        activeDepeId={user.context.active_depe_id}
        availableDepts={user.context.available_depts ?? []}
        dependencias={user.dependencias ?? []}
      />
    </section>
  {/if}
</div>

<style lang="scss">
  @use "$styles/_tokens" as t;

  .perfil-page {
    padding: var(--op-space-5);
    max-width: 820px;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__header {
      margin-bottom: var(--op-space-1);
    }

    &__title {
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0 0 var(--op-space-1);
    }

    &__subtitle {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__error {
      background: var(--op-surface);
      border: 1px solid var(--op-error);
      color: var(--op-error);
      border-radius: var(--op-radius);
      padding: var(--op-space-3);
      font-size: var(--op-font-sm);
      margin-bottom: var(--op-space-3);
    }
  }

  .perfil-identity {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);
    margin: 0;

    &__row {
      display: grid;
      grid-template-columns: 160px 1fr;
      gap: var(--op-space-3);
      align-items: start;

      dt {
        font-weight: var(--op-weight-medium);
        color: var(--op-text-secondary);
        font-size: var(--op-font-sm);
      }

      dd {
        margin: 0;
        color: var(--op-text);
        font-size: var(--op-font-sm);
        word-break: break-word;
      }
    }

    &__muted {
      color: var(--op-text-secondary);
      font-size: var(--op-font-xs);
    }
  }

  .perfil-chips {
    display: flex;
    flex-wrap: wrap;
    gap: var(--op-space-1);
  }

  .perfil-chip {
    display: inline-block;
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-medium);
    background: var(--op-primary-light);
    color: var(--op-primary-dark);
    border-radius: var(--op-radius-sm);
    padding: 2px var(--op-space-2);

    &--permission {
      background: var(--op-bg);
      color: var(--op-text-secondary);
      border: 1px solid var(--op-border);
    }
  }

  .perfil-section {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-4);
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }

    &__title {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-base);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0;
    }

    &__desc {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__status {
      font-size: var(--op-font-sm);
      color: var(--op-text);
      margin: 0;

      &--ok {
        color: var(--op-success);
        font-weight: var(--op-weight-medium);
        display: flex;
        align-items: center;
        gap: var(--op-space-2);
      }
    }
  }

  .perfil-legal-note {
    background: var(--op-bg);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-3) var(--op-space-4);
    font-size: var(--op-font-sm);
    color: var(--op-text);

    p {
      margin: 0 0 var(--op-space-2);
      &:last-child {
        margin-bottom: 0;
      }
    }
  }

  .perfil-prereq {
    display: flex;
    gap: var(--op-space-3);
    align-items: flex-start;
    background: var(--op-primary-light);
    border: 1px solid var(--op-primary);
    border-radius: var(--op-radius);
    padding: var(--op-space-3) var(--op-space-4);
    font-size: var(--op-font-sm);
    color: var(--op-text);

    i {
      color: var(--op-primary-dark);
      margin-top: 2px;
    }
  }

  .perfil-inline-form {
    display: flex;
    align-items: flex-end;
    gap: var(--op-space-3);
    flex-wrap: wrap;

    &--stacked {
      flex-direction: column;
      align-items: stretch;
    }

    :global(.op-field) {
      flex: 1 1 220px;
    }
  }

  .perfil-totp-secret {
    background: var(--op-bg);
    border: 1px dashed var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-3);

    p {
      margin: 0 0 var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-text);
    }

    &__value {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
      flex-wrap: wrap;

      code {
        font-size: var(--op-font-sm);
        letter-spacing: 0.05em;
        background: var(--op-surface);
        border: 1px solid var(--op-border);
        border-radius: var(--op-radius-sm);
        padding: var(--op-space-2) var(--op-space-3);
      }
    }
  }

  .perfil-csr {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);

    label {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
    }

    &__text {
      width: 100%;
      font-family: monospace;
      font-size: var(--op-font-xs);
      color: var(--op-text);
      background: var(--op-bg);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      padding: var(--op-space-3);
      resize: vertical;
    }

    &__actions {
      display: flex;
      gap: var(--op-space-2);
    }
  }

  .perfil-recovery {
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-4);
    margin: var(--op-space-4) 0;

    &__title {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      margin: 0 0 var(--op-space-2);
      font-family: var(--op-font-display);
      font-size: var(--op-font-md);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
    }

    &__desc {
      margin: 0 0 var(--op-space-3);
      font-size: var(--op-font-sm);
      line-height: var(--op-line-height);
      color: var(--op-text-secondary);
    }

    &__codes {
      background: var(--op-bg);
      border: 1px dashed var(--op-border);
      border-radius: var(--op-radius);
      padding: var(--op-space-3);
      margin-bottom: var(--op-space-3);
    }

    &__once {
      display: flex;
      gap: var(--op-space-2);
      align-items: flex-start;
      margin: 0 0 var(--op-space-3);
      font-size: var(--op-font-sm);
      line-height: var(--op-line-height);
      color: var(--op-warning-fg);

      i {
        margin-top: 2px;
        flex-shrink: 0;
      }
    }

    &__list {
      // Dos columnas en cuanto hay sitio: diez códigos en una sola tira
      // obligan a desplazarse justo cuando hay que copiarlos.
      display: grid;
      grid-template-columns: 1fr;
      gap: var(--op-space-1) var(--op-space-4);
      margin: 0 0 var(--op-space-3);
      padding-left: var(--op-space-5);

      @media (min-width: 480px) {
        grid-template-columns: repeat(2, 1fr);
      }

      code {
        font-family: var(--op-font-mono, monospace);
        font-size: var(--op-font-sm);
        letter-spacing: 0.04em;
      }
    }

    &__low {
      color: var(--op-warning-fg);

      i {
        margin-right: var(--op-space-1);
      }
    }

    &__regen {
      summary {
        cursor: pointer;
        font-size: var(--op-font-sm);
        font-weight: var(--op-weight-medium);
        color: var(--op-text);
        min-height: 44px;
        display: flex;
        align-items: center;

        &:focus-visible {
          outline: 2px solid var(--op-primary);
          outline-offset: 2px;
        }
      }
    }
  }

  .perfil-danger {
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-3);

    summary {
      cursor: pointer;
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
      min-height: 44px;
      display: flex;
      align-items: center;

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }

    &__warning {
      display: flex;
      gap: var(--op-space-2);
      align-items: flex-start;
      color: var(--op-error);
      font-size: var(--op-font-sm);
      margin: var(--op-space-2) 0 var(--op-space-3);

      i {
        margin-top: 2px;
        flex-shrink: 0;
      }
    }
  }
</style>
