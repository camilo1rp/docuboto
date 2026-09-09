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
  /admin/notificaciones — Historial de envíos + registro de webhooks
  salientes (`notification-service`). Dos facetas INDEPENDIENTES: cada una
  tiene su propio estado de carga/error/vacío — un fallo en una no oculta ni
  bloquea a la otra.

  Historial: `GET /notifications/history?limit=` no pagina con
  `X-Total-Count` (solo `limit`, sin total ni cursor) — la tabla lo declara
  con `disableNav` + un texto explícito ("últimas N"), nunca finge una
  paginación que el backend no ofrece.

  Webhooks: CRUD completo. Registrar una suscripción reenvía CADA evento del
  tenant a una URL externa — el formulario de alta es, en efecto, una
  configuración de exfiltración potencial si se usa mal; por eso exige
  USUA_PERM_ADMIN (espejo del backend) y el borrado no es silencioso.

  RBAC: ambos routers (`notifications.py` history/detail, `webhooks.py` CRUD)
  exigen USUA_PERM_ADMIN; espejo aquí, el backend siempre re-valida.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import DataTable from "$lib/components/ui/DataTable.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** @type {import('./$types').PageData} */
  export let data;

  $: isAdmin = $permissions.includes("USUA_PERM_ADMIN");

  // --- Historial de envíos ---------------------------------------------------
  let history = data.history ?? [];
  let historyError = data.historyError ?? "";
  let historyLoading = false;

  const historyColumns = [
    { key: "sent_at", label: "Fecha" },
    { key: "recipient_email", label: "Destinatario" },
    { key: "subject", label: "Asunto" },
    { key: "status", label: "Estado" },
    { key: "event_type", label: "Evento" },
    { key: "attempts", label: "Intentos" },
  ];

  async function refreshHistory() {
    historyLoading = true;
    historyError = "";
    try {
      const res = await fetch(
        `/admin/notificaciones/api/historial?limit=${data.historyLimit ?? 50}`,
      );
      const body = await res.json().catch(() => []);
      if (res.ok) {
        history = Array.isArray(body) ? body : [];
      } else {
        historyError = mensajeError(body, `Error ${res.status}`);
        notifyError(historyError);
      }
    } catch {
      historyError = "No se pudo conectar al servidor de notificaciones.";
      notifyError(historyError);
    } finally {
      historyLoading = false;
    }
  }

  const STATUS_VARIANT = { sent: "success", failed: "error", pending: "warning" };

  // --- Webhooks salientes ------------------------------------------------------
  let webhooks = data.webhooks ?? [];
  let webhooksError = data.webhooksError ?? "";
  let webhooksLoading = false;

  const webhookColumns = [
    { key: "url", label: "URL de destino" },
    { key: "event_types", label: "Eventos" },
    { key: "active", label: "Estado" },
    { key: "created_at", label: "Creado" },
  ];

  $: webhookRows = webhooks.map((w) => ({
    ...w,
    event_types: w.event_types?.length ? w.event_types.join(", ") : "Todos",
    created_at: w.created_at ? formatearFechaHora(w.created_at) : "—",
  }));

  let modalCreate = false;
  let fUrl = "";
  let fEventTypes = "";
  let fSecret = "";
  let fUrlError = "";
  let creating = false;

  function openCreate() {
    fUrl = "";
    fEventTypes = "";
    fSecret = "";
    fUrlError = "";
    modalCreate = true;
  }

  async function createWebhook() {
    fUrlError = "";
    if (!fUrl.trim()) {
      fUrlError = "La URL de destino es obligatoria.";
      return;
    }
    try {
      // Validación temprana en cliente (el backend re-valida con HttpUrl de
      // Pydantic de todos modos): evita un viaje redondo por una URL mal
      // tecleada.
      new URL(fUrl.trim());
    } catch {
      fUrlError = "La URL no es válida (debe incluir esquema, p. ej. https://).";
      return;
    }

    creating = true;
    try {
      const res = await fetch("/admin/notificaciones/api/webhooks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: fUrl.trim(),
          event_types: fEventTypes
            .split(",")
            .map((t) => t.trim())
            .filter(Boolean),
          secret: fSecret.trim() || undefined,
        }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(mensajeError(resData, "No se pudo crear el webhook."));
        return;
      }
      webhooks = [...webhooks, resData];
      notifySuccess("Webhook registrado.");
      modalCreate = false;
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      creating = false;
    }
  }

  let deletingWebhook = null;
  let modalDelete = false;
  let deleting = false;

  function confirmDelete(webhook) {
    deletingWebhook = webhook;
    modalDelete = true;
  }

  async function deleteWebhook() {
    if (!deletingWebhook) return;
    deleting = true;
    try {
      const res = await fetch(`/admin/notificaciones/api/webhooks/${deletingWebhook.id}`, {
        method: "DELETE",
      });
      if (res.status === 204 || res.ok) {
        webhooks = webhooks.filter((w) => w.id !== deletingWebhook.id);
        notifySuccess("Webhook eliminado.");
        modalDelete = false;
        deletingWebhook = null;
      } else {
        const err = await res.json().catch(() => ({}));
        notifyError(mensajeError(err, "No se pudo eliminar el webhook."));
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      deleting = false;
    }
  }

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Notificaciones" }]);
  });
</script>

<svelte:head>
  <title>Notificaciones — OrpycaMCP</title>
</svelte:head>

<div class="notificaciones-page">
  <header class="notificaciones-page__header">
    <h1 class="notificaciones-page__title">Notificaciones</h1>
    <p class="notificaciones-page__subtitle">
      Historial de envíos del tenant y suscripciones de webhooks salientes.
    </p>
  </header>

  {#if !isAdmin}
    <EmptyState
      icon="fas fa-lock"
      title="Sin permiso de administración"
      description="Tu usuario no tiene el permiso de administración necesario para esta pantalla."
    />
  {:else}
    <section class="notificaciones-section">
      <div class="notificaciones-section__header">
        <h2>Historial de envíos</h2>
        <Button
          variant="ghost"
          size="sm"
          icon="fas fa-rotate"
          loading={historyLoading}
          on:click={refreshHistory}
        >
          Actualizar
        </Button>
      </div>
      <p class="notificaciones-section__note">
        Se muestran los últimos {data.historyLimit ?? 50} envíos del tenant. El backend no expone un total
        ni paginación real para este listado — esta lista NO es "todas las notificaciones", es una ventana
        reciente.
      </p>

      {#if historyError}
        <div class="notificaciones-page__error" role="alert">
          <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
          {historyError}
        </div>
      {:else if history.length === 0 && !historyLoading}
        <EmptyState
          icon="fas fa-envelope"
          title="Sin envíos recientes"
          description="No hay notificaciones registradas todavía."
        />
      {:else}
        <DataTable
          columns={historyColumns}
          rows={history}
          totalCount={history.length}
          pageSize={Math.max(history.length, 1)}
          disableNav
          totalDesconocido
          loading={historyLoading}
          caption="Historial de envíos"
        >
          <svelte:fragment slot="row" let:row>
            <td>{row.sent_at}</td>
            <td>{row.recipient_email}</td>
            <td>{row.subject}</td>
            <td>
              <StatusChip status={STATUS_VARIANT[row.status] ?? "neutral"} label={row.status} dot />
            </td>
            <td>{row.event_type ?? "—"}</td>
            <td>{row.attempts}</td>
          </svelte:fragment>
        </DataTable>
      {/if}
    </section>

    <section class="notificaciones-section">
      <div class="notificaciones-section__header">
        <h2>Webhooks salientes</h2>
        <Button icon="fas fa-plus" on:click={openCreate}>Nuevo webhook</Button>
      </div>
      <p class="notificaciones-section__note">
        Cada webhook activo reenvía los eventos del tenant (o solo los tipos indicados) a la URL
        configurada. Revísalos periódicamente: son un canal de salida de datos hacia un tercero.
      </p>

      {#if webhooksError}
        <div class="notificaciones-page__error" role="alert">
          <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
          {webhooksError}
        </div>
      {:else if webhooks.length === 0 && !webhooksLoading}
        <EmptyState
          icon="fas fa-satellite-dish"
          title="Sin webhooks configurados"
          description="Crea el primero con «Nuevo webhook»."
        />
      {:else}
        <DataTable
          columns={webhookColumns}
          rows={webhookRows}
          totalCount={webhookRows.length}
          pageSize={Math.max(webhookRows.length, 1)}
          disableNav
          loading={webhooksLoading}
          caption="Webhooks salientes"
        >
          <svelte:fragment slot="row" let:row>
            <td class="notificaciones-page__url">{row.url}</td>
            <td>{row.event_types}</td>
            <td>
              <StatusChip
                status={row.active ? "success" : "neutral"}
                label={row.active ? "Activo" : "Inactivo"}
                dot
              />
            </td>
            <td>{row.created_at}</td>
            <td>
              <Button
                variant="ghost"
                size="sm"
                icon="fas fa-trash"
                on:click={() => confirmDelete(row)}
              >
                Eliminar
              </Button>
            </td>
          </svelte:fragment>
        </DataTable>
      {/if}
    </section>
  {/if}
</div>

<Modal open={modalCreate} title="Nuevo webhook" on:close={() => (modalCreate = false)}>
  <form class="webhook-form" on:submit|preventDefault={createWebhook}>
    <FormField
      id="webhook-url"
      label="URL de destino"
      bind:value={fUrl}
      placeholder="https://ejemplo.org/webhooks/orpycamcp"
      required
      error={fUrlError}
    />
    <FormField
      id="webhook-events"
      label="Tipos de evento (opcional)"
      bind:value={fEventTypes}
      placeholder="radicado.creado, expediente.cerrado"
      hint="Separados por comas. Vacío = todos los eventos del tenant."
    />
    <FormField
      id="webhook-secret"
      label="Secreto de firma (opcional)"
      bind:value={fSecret}
      type="password"
      hint="Se usa para firmar el payload (HMAC) en cada envío al destino."
    />
  </form>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalCreate = false)}>Cancelar</Button>
    <Button loading={creating} on:click={createWebhook}>Registrar webhook</Button>
  </svelte:fragment>
</Modal>

<Modal open={modalDelete} title="Eliminar webhook" size="sm" on:close={() => (modalDelete = false)}>
  {#if deletingWebhook}
    <p>
      Vas a eliminar la suscripción a <strong>{deletingWebhook.url}</strong>. A partir de ahora ese
      destino dejará de recibir los eventos del tenant. Esta acción no se puede deshacer; si vuelves
      a necesitar el envío a esa URL, deberás crear una suscripción nueva (con un secreto nuevo).
    </p>
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalDelete = false)}>Cancelar</Button>
    <Button variant="danger" loading={deleting} on:click={deleteWebhook}>Eliminar</Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .notificaciones-page {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-5);

    &__header {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
    }

    &__title {
      font-size: var(--op-font-xl);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0;
    }

    &__subtitle {
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__error {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-3);
      border-radius: var(--op-radius);
      background: var(--op-error-bg);
      color: var(--op-error-fg);
    }

    &__url {
      max-width: 320px;
      overflow-wrap: anywhere;
    }
  }

  .notificaciones-section {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);

    &__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--op-space-3);

      h2 {
        font-size: var(--op-font-lg);
        font-weight: var(--op-weight-bold);
        color: var(--op-text);
        margin: 0;
      }
    }

    &__note {
      margin: 0;
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
    }
  }

  .webhook-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
  }
</style>
