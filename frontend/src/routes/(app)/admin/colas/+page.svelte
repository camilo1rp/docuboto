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
  /admin/colas — Colas de errores (dead-letter, ADR-021 Fase 5): tres
  servicios independientes (workflow, notification, signature) exponen la
  misma forma de API bajo `admin_deadletter.py`. Selector de servicio +
  filtro de estado (pending/resolved/discarded) + listado con detalle,
  reintento y descarte.

  Gate REAL: `PERM_DLQ_ADMIN` — DISTINTO de `USUA_PERM_ADMIN` (verificado
  leyendo los 3 routers de backend). El catálogo de `admin/+page.svelte`
  todavía declara esta tarjeta con `permission: "USUA_PERM_ADMIN"` — repórtese
  al equipo que la coordina para corregirlo (fuera de alcance de este agente).

  Descartar es IRREVERSIBLE: el evento pasa a `discarded` y el mensaje
  original nunca se reprocesará — el modal de confirmación dice
  explícitamente qué se pierde y exige un motivo trazable (auditado). El
  reintento (`replay`) reprocesa el evento con efectos reales (puede
  reenviar un correo o re-ejecutar una distribución) y también pide
  confirmación, aunque no es irreversible en el mismo sentido.

  Truncamiento: si `X-Truncated: true`, puede haber dead-letters pendientes
  más antiguos que el backend no incluyó en el conjunto candidato — se
  muestra como aviso persistente, no solo en un comentario de código.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import DataTable from "$lib/components/ui/DataTable.svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** @type {import('./$types').PageData} */
  export let data;

  $: isAdmin = $permissions.includes("PERM_DLQ_ADMIN");

  const SERVICES = [
    { value: "workflow", label: "Flujos (workflow)" },
    { value: "notification", label: "Notificaciones" },
    { value: "signature", label: "Firma" },
  ];
  const STATUSES = [
    { value: "pending", label: "Pendientes" },
    { value: "resolved", label: "Resueltos" },
    { value: "discarded", label: "Descartados" },
  ];

  let selectedService = data.defaultService ?? "workflow";
  let selectedStatus = "pending";
  let originStreamFilter = "";

  let items = data.items ?? [];
  let totalCount = data.totalCount ?? 0;
  let truncated = data.truncated ?? false;
  const pageSize = data.pageSize ?? 20;
  let page = 1;
  let listLoading = false;
  let listError = data.loadError ?? "";

  async function fetchList(targetPage = 1) {
    listLoading = true;
    listError = "";
    const params = new URLSearchParams();
    params.set("status", selectedStatus);
    if (originStreamFilter.trim()) params.set("origin_stream", originStreamFilter.trim());
    params.set("page", String(targetPage));
    params.set("size", String(pageSize));

    try {
      const res = await fetch(`/admin/colas/api/${selectedService}?${params}`);
      const body = await res.json().catch(() => []);
      if (res.ok) {
        items = Array.isArray(body) ? body : [];
        totalCount = Number(res.headers.get("X-Total-Count") ?? items.length);
        truncated = res.headers.get("X-Truncated") === "true";
        page = targetPage;
      } else {
        listError = mensajeError(body, `Error ${res.status}`);
        notifyError(listError);
      }
    } catch {
      listError = "No se pudo conectar al servidor de colas de errores.";
      notifyError(listError);
    } finally {
      listLoading = false;
    }
  }

  function changeService(service) {
    selectedService = service;
    fetchList(1);
  }

  function applyFilters() {
    fetchList(1);
  }

  function handlePageChange(event) {
    fetchList(event.detail.page);
  }

  const STATUS_VARIANT = { pending: "warning", resolved: "success", discarded: "neutral" };

  const columns = [
    { key: "event_id", label: "Evento" },
    { key: "event_type", label: "Tipo" },
    { key: "origin_stream", label: "Origen" },
    { key: "status", label: "Estado" },
    { key: "dead_lettered_at", label: "Fecha de fallo" },
  ];

  $: tableRows = items.map((i) => ({
    ...i,
    dead_lettered_at: i.dead_lettered_at ? formatearFechaHora(i.dead_lettered_at) : "—",
  }));

  // --- Detalle ---------------------------------------------------------------
  let detailDrawerOpen = false;
  let detailLoading = false;
  let detailError = "";
  let detail = null;

  async function openDetail(row) {
    detailDrawerOpen = true;
    detailLoading = true;
    detailError = "";
    detail = null;
    try {
      const res = await fetch(
        `/admin/colas/api/${selectedService}/${encodeURIComponent(row.event_id)}`,
      );
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        detail = body;
      } else {
        detailError =
          res.status === 404
            ? "No se encontró el evento, o tu clearance no permite verlo."
            : mensajeError(body, `Error ${res.status}`);
      }
    } catch {
      detailError = "No se pudo conectar al servidor de colas de errores.";
    } finally {
      detailLoading = false;
    }
  }

  // --- Reintento (replay) ------------------------------------------------------
  let modalReplay = false;
  let replayTarget = null;
  let replaying = false;

  function confirmReplay(row) {
    replayTarget = row;
    modalReplay = true;
  }

  async function doReplay() {
    if (!replayTarget) return;
    replaying = true;
    try {
      const res = await fetch(
        `/admin/colas/api/${selectedService}/${encodeURIComponent(replayTarget.event_id)}/replay`,
        { method: "POST" },
      );
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        notifySuccess("Evento reprocesado correctamente.");
        modalReplay = false;
        detailDrawerOpen = false;
        fetchList(page);
      } else {
        notifyError(mensajeError(body, `El reprocesamiento falló (error ${res.status}).`));
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      replaying = false;
    }
  }

  // --- Descarte (irreversible) -----------------------------------------------
  let modalDiscard = false;
  let discardTarget = null;
  let discardReason = "";
  let discardReasonError = "";
  let discarding = false;

  function confirmDiscard(row) {
    discardTarget = row;
    discardReason = "";
    discardReasonError = "";
    modalDiscard = true;
  }

  async function doDiscard() {
    discardReasonError = "";
    if (discardReason.trim().length < 10) {
      discardReasonError = "Escribe un motivo de al menos 10 caracteres.";
      return;
    }
    if (!discardTarget) return;
    discarding = true;
    try {
      const res = await fetch(
        `/admin/colas/api/${selectedService}/${encodeURIComponent(discardTarget.event_id)}/discard`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: discardReason.trim() }),
        },
      );
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        notifySuccess("Evento descartado.");
        modalDiscard = false;
        detailDrawerOpen = false;
        fetchList(page);
      } else {
        notifyError(mensajeError(body, `El descarte falló (error ${res.status}).`));
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      discarding = false;
    }
  }

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Colas de errores" }]);
  });
</script>

<svelte:head>
  <title>Colas de errores — OrpycaMCP</title>
</svelte:head>

<div class="colas-page">
  <header class="colas-page__header">
    <h1 class="colas-page__title">Colas de errores</h1>
    <p class="colas-page__subtitle">
      Eventos que fallaron su procesamiento automático (dead-letter, ADR-021) en workflow,
      notificaciones o firma. Reintentar reprocesa el evento; descartar lo cierra permanentemente.
    </p>
  </header>

  {#if !isAdmin}
    <EmptyState
      icon="fas fa-lock"
      title="Sin permiso de administración de colas"
      description="Tu usuario no tiene el permiso PERM_DLQ_ADMIN necesario para esta pantalla."
    />
  {:else}
    <div class="colas-tabs" role="tablist" aria-label="Servicio">
      {#each SERVICES as svc (svc.value)}
        <button
          type="button"
          role="tab"
          aria-selected={selectedService === svc.value}
          class="colas-tabs__tab"
          class:colas-tabs__tab--active={selectedService === svc.value}
          on:click={() => changeService(svc.value)}
        >
          {svc.label}
        </button>
      {/each}
    </div>

    <form class="colas-filters" on:submit|preventDefault={applyFilters}>
      <FormField
        id="colas-status"
        label="Estado"
        type="select"
        bind:value={selectedStatus}
        options={STATUSES}
      />
      <FormField
        id="colas-origin"
        label="Stream de origen (opcional)"
        bind:value={originStreamFilter}
        placeholder="p. ej. orpycamcp.workflow.events"
      />
      <div class="colas-filters__actions">
        <Button type="submit" icon="fas fa-filter" loading={listLoading}>Filtrar</Button>
      </div>
    </form>

    {#if truncated}
      <div class="colas-page__warning" role="alert">
        <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
        El conjunto de eventos candidatos alcanzó el límite de la consulta: puede haber eventos pendientes
        más antiguos que NO aparecen en este listado ni en el total mostrado.
      </div>
    {/if}

    {#if listError}
      <div class="colas-page__error" role="alert">
        <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
        {listError}
      </div>
    {:else if items.length === 0 && !listLoading}
      <EmptyState
        icon="fas fa-inbox"
        title="Sin eventos en este estado"
        description={`No hay dead-letters con estado "${selectedStatus}" para este servicio.`}
      />
    {:else}
      <DataTable
        {columns}
        rows={tableRows}
        {totalCount}
        {page}
        {pageSize}
        loading={listLoading}
        caption="Cola de errores"
        on:pageChange={handlePageChange}
      >
        <svelte:fragment slot="row" let:row>
          <td class="colas-page__mono">{row.event_id}</td>
          <td>{row.event_type ?? "—"}</td>
          <td>{row.origin_stream ?? "—"}</td>
          <td>
            <StatusChip status={STATUS_VARIANT[row.status] ?? "neutral"} label={row.status} dot />
          </td>
          <td>{row.dead_lettered_at}</td>
          <td class="colas-page__row-actions">
            <Button variant="ghost" size="sm" icon="fas fa-eye" on:click={() => openDetail(row)}>
              Detalle
            </Button>
            {#if row.status === "pending"}
              <Button
                variant="secondary"
                size="sm"
                icon="fas fa-rotate"
                on:click={() => confirmReplay(row)}
              >
                Reintentar
              </Button>
              <Button
                variant="danger"
                size="sm"
                icon="fas fa-ban"
                on:click={() => confirmDiscard(row)}
              >
                Descartar
              </Button>
            {/if}
          </td>
        </svelte:fragment>
      </DataTable>
    {/if}
  {/if}
</div>

<Drawer
  open={detailDrawerOpen}
  title={detail ? `Evento ${detail.event_id}` : "Detalle del evento"}
  on:close={() => (detailDrawerOpen = false)}
>
  {#if detailLoading}
    <p>Cargando detalle…</p>
  {:else if detailError}
    <div class="colas-page__error" role="alert">
      <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
      {detailError}
    </div>
  {:else if detail}
    <dl class="detail-list">
      <dt>Estado</dt>
      <dd>
        <StatusChip status={STATUS_VARIANT[detail.status] ?? "neutral"} label={detail.status} dot />
      </dd>
      <dt>Tipo de evento</dt>
      <dd>{detail.event_type ?? "—"}</dd>
      <dt>Correlación</dt>
      <dd>{detail.correlation_id ?? "—"}</dd>
      <dt>Stream de origen</dt>
      <dd>{detail.origin_stream ?? "—"}</dd>
      <dt>Fecha de fallo</dt>
      <dd>
        {detail.dead_lettered_at ? formatearFechaHora(detail.dead_lettered_at) : "—"}
      </dd>
      <dt>Fecha de resolución</dt>
      <dd>{detail.resolved_at ? formatearFechaHora(detail.resolved_at) : "—"}</dd>
      <dt>Motivo del fallo</dt>
      <dd>
        <pre class="detail-list__payload">{JSON.stringify(detail.failure, null, 2)}</pre>
      </dd>
      <dt>Sobre del evento</dt>
      <dd>
        <pre class="detail-list__payload">{JSON.stringify(detail.payload, null, 2)}</pre>
      </dd>
    </dl>
    {#if detail.status === "pending"}
      <div class="detail-list__actions">
        <Button variant="secondary" icon="fas fa-rotate" on:click={() => confirmReplay(detail)}>
          Reintentar
        </Button>
        <Button variant="danger" icon="fas fa-ban" on:click={() => confirmDiscard(detail)}>
          Descartar
        </Button>
      </div>
    {/if}
  {/if}
</Drawer>

<Modal
  open={modalReplay}
  title="Reintentar evento"
  size="sm"
  on:close={() => (modalReplay = false)}
>
  {#if replayTarget}
    <p>
      Vas a reprocesar el evento <strong>{replayTarget.event_id}</strong>. El sistema re-ejecutará
      el efecto original (puede incluir un envío de correo o una distribución real). Si el reproceso
      vuelve a fallar, el evento seguirá pendiente sin cambios adicionales.
    </p>
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalReplay = false)}>Cancelar</Button>
    <Button loading={replaying} on:click={doReplay}>Reintentar ahora</Button>
  </svelte:fragment>
</Modal>

<Modal
  open={modalDiscard}
  title="Descartar evento"
  size="sm"
  on:close={() => (modalDiscard = false)}
>
  {#if discardTarget}
    <p class="colas-page__discard-warning">
      Vas a descartar permanentemente el evento <strong>{discardTarget.event_id}</strong>. Esta
      acción NO se puede deshacer: el evento pasará a "descartado" y el mensaje original
      <strong>nunca se reprocesará</strong>. Si el evento representaba, por ejemplo, una
      distribución de radicado o el envío de una notificación, ese efecto no ocurrirá jamás.
    </p>
    <FormField
      id="discard-reason"
      label="Motivo del descarte"
      type="textarea"
      bind:value={discardReason}
      required
      error={discardReasonError}
      hint="Mínimo 10 caracteres. Queda registrado en la auditoría inmutable junto con este evento."
    />
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalDiscard = false)}>Cancelar</Button>
    <Button variant="danger" loading={discarding} on:click={doDiscard}
      >Descartar definitivamente</Button
    >
  </svelte:fragment>
</Modal>

<style lang="scss">
  .colas-page {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

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

    &__warning {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-3);
      border-radius: var(--op-radius);
      background: var(--op-warning-bg);
      color: var(--op-warning-fg);
    }

    &__mono {
      font-family: monospace;
      font-size: var(--op-font-xs);
      word-break: break-all;
    }

    &__row-actions {
      display: flex;
      flex-wrap: wrap;
      gap: var(--op-space-1);
    }

    &__discard-warning {
      margin: 0 0 var(--op-space-3);
      color: var(--op-error-fg);
    }
  }

  .colas-tabs {
    display: flex;
    gap: var(--op-space-2);
    border-bottom: 1px solid var(--op-border);

    &__tab {
      background: none;
      border: none;
      border-bottom: 2px solid transparent;
      padding: var(--op-space-2) var(--op-space-3);
      font: inherit;
      color: var(--op-text-secondary);
      cursor: pointer;
      min-height: 36px;

      @media (pointer: coarse) {
        min-height: 44px;
      }

      &:hover {
        color: var(--op-primary-dark);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }

      &--active {
        color: var(--op-primary-dark);
        border-bottom-color: var(--op-primary);
        font-weight: var(--op-weight-medium);
      }
    }
  }

  .colas-filters {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    gap: var(--op-space-3);
    padding: var(--op-space-4);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    background: var(--op-surface);

    &__actions {
      display: flex;
      gap: var(--op-space-2);
      padding-bottom: 2px;
    }
  }

  .detail-list {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: var(--op-space-2) var(--op-space-3);
    margin: 0 0 var(--op-space-4);

    dt {
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
    }

    dd {
      margin: 0;
      color: var(--op-text);
      word-break: break-word;
    }

    &__payload {
      background: var(--op-bg);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-sm);
      padding: var(--op-space-2);
      font-size: var(--op-font-xs);
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }

    &__actions {
      display: flex;
      gap: var(--op-space-2);
    }
  }
</style>
