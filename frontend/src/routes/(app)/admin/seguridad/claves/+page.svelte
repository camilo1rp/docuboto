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
  /admin/seguridad/claves — Revocación administrativa de la credencial de
  firma personal PKI de OTRO usuario (E17/F4, Fase 3 cierre de desfase
  API↔UI).

  Backend real verificado: `POST /api/v1/auth/admin/users/{user_id}/signing-key/revoke`
  (`admin_router` de `services/auth-service/app/routers/signing_key.py`,
  gateado `USUA_PERM_ADMIN` a nivel de router) — SIN step-up TOTP propio (el
  permiso administrativo ES el gate; a diferencia de la auto-revocación en
  `/perfil`, que SIEMPRE exige el 2FA del titular).

  "Revocación a ciegas": NO existe un endpoint administrativo que liste el
  estado de firma por usuario (solo `GET /auth/me/signing-key`, propio del
  titular) — el administrador elige un usuario del catálogo y revoca; el
  backend responde 404 `no_signing_key` si no tenía ninguna credencial. La
  pantalla lo explica en vez de aparentar que "sabe" quién tiene clave.

  RBAC: requiere USUA_PERM_ADMIN (mismo gate que el resto de `/admin/*`).
  D-05: la ejecución va a `admin/seguridad/claves/api` (nunca al gateway
  desde el cliente); la lista de usuarios la trae `+page.server.js` (SSR).
-->
<script>
  import { onMount, tick } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyInfo, notifyError } from "$lib/stores/notifications.js";
  import {
    isAdminRevokeConfirmed,
    ADMIN_REVOKE_CONFIRM_PHRASE,
  } from "$lib/utils/signingKeyConfirm.js";

  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Button from "$lib/components/ui/Button.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  $: users = data.users ?? [];
  $: isAdmin = $permissions.includes("USUA_PERM_ADMIN");

  let query = "";
  $: filteredUsers = query.trim()
    ? users.filter((u) =>
        `${u.username} ${u.email ?? ""}`.toLowerCase().includes(query.trim().toLowerCase()),
      )
    : users;

  /** @type {string} */
  let selectedUserId = "";
  let motivo = "";
  let confirmText = "";

  let submitting = false;
  let resultMessage = "";
  /** @type {HTMLElement|null} */
  let resultEl = null;

  $: selectionReady = !!selectedUserId;
  $: confirmReady = isAdminRevokeConfirmed(confirmText);
  $: canSubmit = selectionReady && confirmReady && !submitting;

  $: missingRequirements = [
    !selectionReady ? "seleccione un usuario" : null,
    !confirmReady ? `escriba "${ADMIN_REVOKE_CONFIRM_PHRASE}" para confirmar` : null,
  ].filter(Boolean);

  async function submitRevoke() {
    resultMessage = "";
    if (!canSubmit) return;
    submitting = true;
    try {
      const res = await fetch("/admin/seguridad/claves/api", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: selectedUserId,
          motivo: motivo.trim() || undefined,
          confirm: confirmText.trim(),
        }),
      });
      const resData = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (res.status === 404) {
          notifyInfo(
            resData.detail ?? "Ese usuario no tiene una credencial de firma para revocar.",
          );
        } else {
          notifyError(resData.detail ?? "No se pudo revocar la credencial.");
        }
        return;
      }
      const targetLabel = users.find((u) => u.id === selectedUserId)?.username ?? selectedUserId;
      resultMessage = `Se revocó la credencial de firma de "${targetLabel}".`;
      selectedUserId = "";
      motivo = "";
      confirmText = "";
      query = "";
      await tick();
      resultEl?.focus();
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      submitting = false;
    }
  }

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Claves de firma" }]);
  });
</script>

<svelte:head>
  <title>Claves de firma — OrpycaMCP</title>
</svelte:head>

<div class="claves-page">
  <header class="claves-page__header">
    <h1 class="claves-page__title">Claves de firma</h1>
    <p class="claves-page__subtitle">
      Revoca la credencial de firma electrónica personal (PKI) de otro usuario, por ejemplo tras la
      salida de un funcionario o la sospecha de un compromiso de sesión.
    </p>
  </header>

  {#if !isAdmin}
    <EmptyState
      icon="fas fa-lock"
      title="Sin permiso de administración"
      description="Tu usuario no tiene el permiso de administración necesario para revocar credenciales de firma de otros usuarios."
    />
  {:else}
    <div class="claves-page__warning">
      <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
      <div>
        <strong>Acción irreversible y sin previo aviso al usuario.</strong> Tras revocar, el usuario
        no podrá firmar hasta enrolar (y re-emitir, un trámite fuera de línea) una credencial nueva.
        Esta pantalla no puede saber de antemano si el usuario elegido tiene una credencial activa:
        el sistema no ofrece un listado administrativo de ese estado — solo cada usuario ve el suyo
        propio en <a href="/perfil">Mi perfil</a>.
      </div>
    </div>

    {#if data.loadError}
      <div class="claves-page__error" role="alert">
        <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
        {data.loadError}
      </div>
    {/if}

    <section class="claves-section" aria-labelledby="claves-usuario-title">
      <h2 id="claves-usuario-title" class="claves-section__title">1. Usuario</h2>

      <FormField
        id="claves-query"
        label="Buscar por usuario o correo"
        bind:value={query}
        placeholder="Escriba para filtrar…"
        hint={`${filteredUsers.length} de ${users.length} usuario(s)`}
      />

      <fieldset class="claves-userlist">
        <legend class="sr-only">Usuario objetivo</legend>
        {#if filteredUsers.length === 0}
          <p class="claves-userlist__empty">No hay usuarios que coincidan con la búsqueda.</p>
        {:else}
          <ul class="claves-userlist__list" role="list">
            {#each filteredUsers as u (u.id)}
              <li>
                <label class="claves-userlist__option">
                  <input
                    type="radio"
                    name="claves-target"
                    value={u.id}
                    bind:group={selectedUserId}
                  />
                  <span class="claves-userlist__name">{u.username}</span>
                  {#if u.email}<span class="claves-userlist__email">{u.email}</span>{/if}
                  {#if !u.is_active}
                    <span class="claves-userlist__inactive">inactivo</span>
                  {/if}
                </label>
              </li>
            {/each}
          </ul>
        {/if}
      </fieldset>
    </section>

    <section class="claves-section" aria-labelledby="claves-motivo-title">
      <h2 id="claves-motivo-title" class="claves-section__title">2. Motivo (opcional)</h2>
      <FormField
        id="claves-motivo"
        label="Motivo de la revocación"
        type="textarea"
        bind:value={motivo}
        placeholder="Ej.: salida del funcionario, sospecha de sesión comprometida…"
        hint="Queda registrado en el asiento de auditoría (audit_log)."
      />
    </section>

    <section class="claves-section" aria-labelledby="claves-confirm-title">
      <h2 id="claves-confirm-title" class="claves-section__title">3. Confirmar</h2>
      <FormField
        id="claves-confirm"
        label={`Escriba "${ADMIN_REVOKE_CONFIRM_PHRASE}" para habilitar la revocación`}
        bind:value={confirmText}
        placeholder={ADMIN_REVOKE_CONFIRM_PHRASE}
        hint="Confirmación tecleada — un clic no basta para esta acción."
      />

      <div class="claves-page__actions">
        <Button variant="danger" disabled={!canSubmit} loading={submitting} on:click={submitRevoke}>
          Revocar credencial
        </Button>
        {#if missingRequirements.length > 0}
          <p class="claves-page__missing">Falta: {missingRequirements.join("; ")}.</p>
        {/if}
      </div>

      {#if resultMessage}
        <p
          class="claves-page__result"
          role="status"
          aria-live="polite"
          tabindex="-1"
          bind:this={resultEl}
        >
          <i class="fas fa-circle-check" aria-hidden="true"></i>
          {resultMessage}
        </p>
      {/if}
    </section>
  {/if}
</div>

<style lang="scss">
  @use "$styles/_tokens" as t;

  .claves-page {
    padding: var(--op-space-5);
    max-width: 760px;

    &__header {
      margin-bottom: var(--op-space-4);
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

    &__warning {
      display: flex;
      gap: var(--op-space-3);
      align-items: flex-start;
      background: var(--op-primary-light);
      border: 1px solid var(--op-warning);
      border-radius: var(--op-radius);
      padding: var(--op-space-3) var(--op-space-4);
      margin-bottom: var(--op-space-4);
      color: var(--op-text);
      font-size: var(--op-font-sm);

      i {
        color: var(--op-warning);
        margin-top: 2px;
        flex-shrink: 0;
      }

      a {
        color: var(--op-primary-dark);
        text-decoration: underline;
      }
    }

    &__error {
      display: flex;
      gap: var(--op-space-2);
      align-items: center;
      background: var(--op-surface);
      border: 1px solid var(--op-error);
      color: var(--op-error);
      border-radius: var(--op-radius);
      padding: var(--op-space-3);
      margin-bottom: var(--op-space-4);
      font-size: var(--op-font-sm);
    }

    &__actions {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
      flex-wrap: wrap;
      margin-top: var(--op-space-3);
    }

    &__missing {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__result {
      display: flex;
      gap: var(--op-space-2);
      align-items: center;
      color: var(--op-success);
      font-weight: var(--op-weight-medium);
      margin-top: var(--op-space-3);

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }
  }

  .claves-section {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    padding: var(--op-space-4);
    margin-bottom: var(--op-space-4);

    &__title {
      font-size: var(--op-font-base);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0 0 var(--op-space-3);
    }
  }

  .claves-userlist {
    border: none;
    margin: var(--op-space-3) 0 0;
    padding: 0;
    max-height: 320px;
    overflow-y: auto;
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);

    &__empty {
      padding: var(--op-space-3);
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
      margin: 0;
    }

    &__list {
      list-style: none;
      margin: 0;
      padding: 0;
    }

    &__option {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-2) var(--op-space-3);
      min-height: 44px;
      cursor: pointer;
      border-bottom: 1px solid var(--op-border);

      &:last-child {
        border-bottom: none;
      }

      &:hover {
        background: var(--op-bg);
      }

      @media (pointer: coarse) {
        min-height: 48px;
      }
    }

    &__name {
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
    }

    &__email {
      color: var(--op-text-secondary);
      font-size: var(--op-font-xs);
    }

    &__inactive {
      margin-left: auto;
      font-size: var(--op-font-xs);
      color: var(--op-error);
      border: 1px solid var(--op-error);
      border-radius: var(--op-radius-sm);
      padding: 1px var(--op-space-2);
    }
  }
</style>
