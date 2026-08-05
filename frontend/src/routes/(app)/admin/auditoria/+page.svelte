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
  /admin/auditoria — Consulta del registro de auditoría inmutable y
  verificación de la cadena de hash (E08, ADR-008).

  Esta pantalla la usa un auditor externo: la integridad de la cadena tiene
  que ser inequívoca. Tres estados, NUNCA dos:
    - "checking"  → verificación en curso (spinner, ningún veredicto todavía)
    - "ok"        → la cadena se recalculó y es íntegra (verde)
    - "broken"    → la cadena se recalculó y está COMPROMETIDA — la fila
                     rota se señala (rojo, hallazgo grave, texto explícito)
    - "error"     → la verificación NO PUDO EJECUTARSE (red/5xx). Se muestra
                     como fallo de la comprobación, nunca como "íntegra" —
                     mentir aquí ("todo correcto" cuando en realidad no se
                     pudo comprobar nada) es exactamente el error que esta
                     pantalla tiene prohibido cometer.

  RBAC: `GET /audit` y `GET /audit/verify` exigen USUA_PERM_ADMIN a nivel de
  router (auth-service); espejo aquí, el backend siempre re-valida.

  HALLAZGO DE DOMINIO (ver +page.server.js): el listado NO filtra por
  clearance del llamante sobre `object_ref` — se muestra tal cual llega del
  backend, sin inventar un filtro de cliente que daría una falsa sensación
  de seguridad.

  Paginación: `GET /audit` SÍ pagina con `X-Total-Count` real — los filtros y
  el cambio de página se resuelven contra el servidor (`admin/auditoria/api`),
  no localmente sobre la página visible.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError } from "$lib/stores/notifications.js";

  import DataTable from "$lib/components/ui/DataTable.svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  $: isAdmin = $permissions.includes("USUA_PERM_ADMIN");

  // --- Verificación de la cadena de hash ------------------------------------
  /** @type {'checking'|'ok'|'broken'|'error'} */
  let verifyState = data.verifyError ? "error" : data.verifyResult?.ok ? "ok" : "broken";
  let verifyDetail = data.verifyError ?? "";
  let brokenId = data.verifyResult?.broken_id ?? null;
  let lastVerifiedAt = data.verifyError ? null : new Date();

  async function reverify() {
    verifyState = "checking";
    verifyDetail = "";
    try {
      const res = await fetch("/admin/auditoria/api/verify");
      const body = await res.json().catch(() => null);
      if (res.ok && body) {
        verifyState = body.ok ? "ok" : "broken";
        brokenId = body.broken_id ?? null;
        lastVerifiedAt = new Date();
      } else {
        verifyState = "error";
        verifyDetail = body?.detail ?? `Error ${res.status}`;
      }
    } catch {
      verifyState = "error";
      verifyDetail = "No se pudo conectar al servidor para verificar la cadena.";
    }
  }

  // --- Listado filtrado y paginado (servidor) -------------------------------
  let entries = data.entries ?? [];
  let totalCount = data.totalCount ?? 0;
  const pageSize = data.pageSize ?? 20;
  let page = 1;
  let listLoading = false;
  let listError = data.loadError ?? "";

  let fAction = "";
  let fObjectType = "";
  let fObjectRef = "";
  let fActor = "";

  async function fetchEntries(targetPage = page) {
    listLoading = true;
    listError = "";
    const params = new URLSearchParams();
    if (fAction.trim()) params.set("action", fAction.trim());
    if (fObjectType.trim()) params.set("object_type", fObjectType.trim());
    if (fObjectRef.trim()) params.set("object_ref", fObjectRef.trim());
    if (fActor.trim()) params.set("actor", fActor.trim());
    params.set("page", String(targetPage));
    params.set("size", String(pageSize));

    try {
      const res = await fetch(`/admin/auditoria/api?${params}`);
      const body = await res.json().catch(() => []);
      if (res.ok) {
        entries = Array.isArray(body) ? body : [];
        totalCount = Number(res.headers.get("X-Total-Count") ?? entries.length);
        page = targetPage;
      } else {
        listError = body?.detail ?? `Error ${res.status}`;
        notifyError(listError);
      }
    } catch {
      listError = "No se pudo conectar al servidor de auditoría.";
      notifyError(listError);
    } finally {
      listLoading = false;
    }
  }

  function applyFilters() {
    fetchEntries(1);
  }

  function clearFilters() {
    fAction = "";
    fObjectType = "";
    fObjectRef = "";
    fActor = "";
    fetchEntries(1);
  }

  function handlePageChange(event) {
    fetchEntries(event.detail.page);
  }

  // --- Detalle de una entrada (payload/hash completos) ----------------------
  let detailDrawerOpen = false;
  let detailEntry = null;

  function openDetail(entry) {
    detailEntry = entry;
    detailDrawerOpen = true;
  }

  const columns = [
    { key: "ts", label: "Fecha" },
    { key: "service", label: "Servicio" },
    { key: "action", label: "Acción" },
    { key: "object_type", label: "Tipo de objeto" },
    { key: "object_ref", label: "Referencia" },
    { key: "actor", label: "Actor" },
  ];

  $: tableRows = entries.map((e) => ({ ...e, ts: new Date(e.ts).toLocaleString("es-CO") }));

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Auditoría" }]);
  });
</script>

<svelte:head>
  <title>Auditoría — OrpycaMCP</title>
</svelte:head>

<div class="auditoria-page">
  <header class="auditoria-page__header">
    <h1 class="auditoria-page__title">Auditoría</h1>
    <p class="auditoria-page__subtitle">
      Registro inmutable de acciones del tenant (<code>audit_log</code>), encadenado por hash.
    </p>
  </header>

  {#if !isAdmin}
    <EmptyState
      icon="fas fa-lock"
      title="Sin permiso de administración"
      description="Tu usuario no tiene el permiso de administración necesario para consultar la auditoría."
    />
  {:else}
    <!-- Panel de integridad: el hallazgo más grave que puede reportar esta
         pantalla es que la cadena esté rota — se comunica sin ambigüedad,
         separado de cualquier otro estado de carga/error de la tabla. -->
    <section
      class="integrity-panel integrity-panel--{verifyState}"
      aria-live="polite"
      aria-atomic="true"
    >
      <div class="integrity-panel__icon" aria-hidden="true">
        {#if verifyState === "checking"}
          <Loader size="sm" />
        {:else if verifyState === "ok"}
          <i class="fas fa-shield-halved"></i>
        {:else if verifyState === "broken"}
          <i class="fas fa-triangle-exclamation"></i>
        {:else}
          <i class="fas fa-circle-question"></i>
        {/if}
      </div>
      <div class="integrity-panel__body">
        {#if verifyState === "checking"}
          <p class="integrity-panel__title">Verificando la cadena de hash…</p>
          <p class="integrity-panel__desc">Recalculando sobre el registro completo del tenant.</p>
        {:else if verifyState === "ok"}
          <p class="integrity-panel__title">Cadena de auditoría íntegra</p>
          <p class="integrity-panel__desc">
            La cadena de hash se recalculó y coincide extremo a extremo con lo almacenado.
            {#if lastVerifiedAt}Última verificación: {lastVerifiedAt.toLocaleString("es-CO")}.{/if}
          </p>
        {:else if verifyState === "broken"}
          <p class="integrity-panel__title">
            CADENA DE AUDITORÍA COMPROMETIDA — posible manipulación del registro legal
          </p>
          <p class="integrity-panel__desc">
            El recálculo de la cadena de hash NO coincide{brokenId !== null
              ? ` a partir de la entrada con id ${brokenId}`
              : ""}. Esto indica que uno o más registros de auditoría fueron alterados o eliminados
            fuera del único punto de escritura permitido. Trátese como un incidente: no continúe
            asumiendo que el resto del registro es confiable sin investigar.
          </p>
        {:else}
          <p class="integrity-panel__title">No se pudo verificar la cadena</p>
          <p class="integrity-panel__desc">
            La comprobación no se ejecutó ({verifyDetail || "fallo de conexión"}) — esto NO
            significa que la cadena esté íntegra, significa que todavía no se sabe. Reintente la
            verificación antes de dar por buena la integridad del registro.
          </p>
        {/if}
      </div>
      <div class="integrity-panel__action">
        <Button
          variant="secondary"
          size="sm"
          icon="fas fa-rotate"
          disabled={verifyState === "checking"}
          on:click={reverify}
        >
          Reverificar cadena
        </Button>
      </div>
    </section>

    <!-- Filtros del listado (contra el servidor, no locales) -->
    <form class="auditoria-filters" on:submit|preventDefault={applyFilters}>
      <FormField
        id="audit-action"
        label="Acción"
        bind:value={fAction}
        placeholder="p. ej. radicado.creado"
      />
      <FormField
        id="audit-object-type"
        label="Tipo de objeto"
        bind:value={fObjectType}
        placeholder="p. ej. radicado"
      />
      <FormField
        id="audit-object-ref"
        label="Referencia"
        bind:value={fObjectRef}
        placeholder="ID del objeto"
      />
      <FormField id="audit-actor" label="Actor" bind:value={fActor} placeholder="ID de usuario" />
      <div class="auditoria-filters__actions">
        <Button type="submit" icon="fas fa-filter" loading={listLoading}>Filtrar</Button>
        <Button type="button" variant="ghost" on:click={clearFilters}>Limpiar</Button>
      </div>
    </form>

    {#if listError}
      <div class="auditoria-page__error" role="alert">
        <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
        {listError}
      </div>
    {/if}

    {#if entries.length === 0 && !listLoading && !listError}
      <EmptyState
        icon="fas fa-clipboard-list"
        title="Sin entradas de auditoría"
        description="No hay registros que coincidan con los filtros seleccionados."
      />
    {:else}
      <DataTable
        {columns}
        rows={tableRows}
        {totalCount}
        {page}
        {pageSize}
        loading={listLoading}
        caption="Registro de auditoría"
        on:pageChange={handlePageChange}
      >
        <svelte:fragment slot="row" let:row>
          <td>{row.ts}</td>
          <td>{row.service}</td>
          <td>{row.action}</td>
          <td>{row.object_type}</td>
          <td>{row.object_ref ?? "—"}</td>
          <td>{row.actor ?? "—"}</td>
          <td>
            <Button variant="ghost" size="sm" icon="fas fa-eye" on:click={() => openDetail(row)}>
              Detalle
            </Button>
          </td>
        </svelte:fragment>
      </DataTable>
    {/if}
  {/if}
</div>

<Drawer
  open={detailDrawerOpen}
  title={detailEntry ? `Entrada #${detailEntry.id}` : "Detalle"}
  on:close={() => (detailDrawerOpen = false)}
>
  {#if detailEntry}
    <dl class="detail-list">
      <dt>Fecha</dt>
      <dd>{new Date(detailEntry.ts).toLocaleString("es-CO")}</dd>
      <dt>Servicio</dt>
      <dd>{detailEntry.service}</dd>
      <dt>Canal</dt>
      <dd>{detailEntry.canal}</dd>
      <dt>Acción</dt>
      <dd>{detailEntry.action}</dd>
      <dt>Tipo de objeto</dt>
      <dd>{detailEntry.object_type}</dd>
      <dt>Referencia</dt>
      <dd>{detailEntry.object_ref ?? "—"}</dd>
      <dt>Actor</dt>
      <dd>{detailEntry.actor ?? "—"}</dd>
      <dt>Hash</dt>
      <dd class="detail-list__mono">{detailEntry.hash}</dd>
      <dt>Payload</dt>
      <dd>
        <pre class="detail-list__payload">{JSON.stringify(detailEntry.payload, null, 2)}</pre>
      </dd>
    </dl>
  {/if}
</Drawer>

<style lang="scss">
  .auditoria-page {
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

      code {
        background: var(--op-bg);
        padding: 0 4px;
        border-radius: var(--op-radius-sm);
      }
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
  }

  .integrity-panel {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-3);
    padding: var(--op-space-4);
    border-radius: var(--op-radius);
    border: 1px solid var(--op-border);

    &__icon {
      font-size: 1.5rem;
      flex-shrink: 0;
      margin-top: 2px;
    }

    &__body {
      flex: 1;
      min-width: 0;
    }

    &__title {
      font-weight: var(--op-weight-bold);
      margin: 0 0 var(--op-space-1);
    }

    &__desc {
      margin: 0;
      color: var(--op-text-secondary);
    }

    &__action {
      flex-shrink: 0;
    }

    &--checking {
      background: var(--op-bg);
      border-color: var(--op-border);

      .integrity-panel__icon {
        color: var(--op-text-secondary);
      }
    }

    &--ok {
      background: var(--op-success-bg);
      border-color: var(--op-success);

      .integrity-panel__icon {
        color: var(--op-success);
      }
      .integrity-panel__title {
        color: var(--op-success-fg);
      }
    }

    &--broken {
      background: var(--op-error-bg);
      border-color: var(--op-error);

      .integrity-panel__icon {
        color: var(--op-error);
      }
      .integrity-panel__title {
        color: var(--op-error-fg);
      }
    }

    &--error {
      background: var(--op-warning-bg);
      border-color: var(--op-warning);

      .integrity-panel__icon {
        color: var(--op-warning);
      }
      .integrity-panel__title {
        color: var(--op-warning-fg);
      }
    }
  }

  .auditoria-filters {
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
    margin: 0;

    dt {
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
    }

    dd {
      margin: 0;
      color: var(--op-text);
      word-break: break-word;
    }

    &__mono {
      font-family: monospace;
      font-size: var(--op-font-xs);
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
  }
</style>
