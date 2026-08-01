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
  admin/pinar — Listado de planes PINAR (RF-ADM-08, Ac. AGN 003/2015, Fase 7
  cierre de desfase API↔UI).

  Reutiliza CatalogCrudPanel (Fase 5) solo para su parte de listado + creación:
  no hay edición ni borrado en este MVP (el plan solo se modifica a través de
  su ciclo de vida — ver admin/pinar/[id] — o creando una versión nueva). El
  slot row-actions enlaza a la vista de detalle en vez de editar/eliminar.

  "Reformular" un plan = crear una versión nueva (version = max+1); el
  histórico de versiones anteriores se conserva y se lista igual que el resto.
-->
<script>
  import { onMount } from "svelte";
  import { goto } from "$app/navigation";
  import CatalogCrudPanel from "$lib/components/features/admin/CatalogCrudPanel.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError } from "$lib/stores/notifications.js";

  /** @type {import('./$types').PageData} */
  export let data;

  $: planes = data.planes ?? [];
  $: loadError = data.loadError;
  $: estadoFiltro = data.estadoFiltro ?? "";

  $: canManage = $permissions.includes("USUA_PERM_ADMIN");

  const ESTADO_LABELS = {
    borrador: "Borrador",
    aprobado: "Aprobado",
    en_ejecucion: "En ejecución",
    cerrado: "Cerrado",
  };
  // "cerrado" es un estado normal del ciclo de vida (terminal, no un error) —
  // StatusChip mapea closed→rojo por defecto, así que se pide "neutral".
  const ESTADO_CHIP_MAP = {
    borrador: "neutral",
    aprobado: "info",
    en_ejecucion: "active",
    cerrado: "neutral",
  };

  const ESTADO_OPTIONS = [
    { value: "", label: "Todos los estados" },
    { value: "borrador", label: "Borrador" },
    { value: "aprobado", label: "Aprobado" },
    { value: "en_ejecucion", label: "En ejecución" },
    { value: "cerrado", label: "Cerrado" },
  ];

  function onFiltroChange() {
    goto(estadoFiltro ? `/admin/pinar?estado=${estadoFiltro}` : "/admin/pinar", {
      keepFocus: true,
    });
  }

  /** @param {string|null|undefined} iso */
  function formatDate(iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleDateString("es-CO", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  }

  // ---------------------------------------------------------------------------
  // Crear plan (versión nueva, siempre en 'borrador')
  // ---------------------------------------------------------------------------
  let createOpen = false;
  let actionLoading = false;
  let fVision = "";
  let fVigenciaDesde = "";
  let fVigenciaHasta = "";

  function resetForm() {
    fVision = "";
    fVigenciaDesde = "";
    fVigenciaHasta = "";
  }

  async function crear() {
    actionLoading = true;
    try {
      const res = await fetch("/admin/pinar/api/planes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vision: fVision,
          vigencia_desde: fVigenciaDesde || null,
          vigencia_hasta: fVigenciaHasta || null,
        }),
      });
      const result = await res.json();
      if (!res.ok) throw result;
      createOpen = false;
      resetForm();
      // Navega directo al workspace del plan recién creado: el ciclo de vida
      // (aprobar/ejecutar/cerrar) se gestiona ahí, no en este listado.
      await goto(`/admin/pinar/${result.id}`);
    } catch (err) {
      notifyError(err?.detail ?? "No se pudo crear el plan PINAR.");
    } finally {
      actionLoading = false;
    }
  }

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "PINAR" }]);
  });
</script>

<svelte:head>
  <title>PINAR — Administración — OrpycaMCP</title>
</svelte:head>

<CatalogCrudPanel
  title="PINAR — Plan Institucional de Archivos"
  ariaLabel="Lista de planes PINAR"
  icon="fas fa-map"
  items={planes}
  rowKey={(p) => p.id}
  loadError={loadError ? (loadError ?? "No se pudo cargar el listado de planes PINAR.") : null}
  canCreate={canManage}
  createLabel="Nuevo plan"
  emptyIcon="fas fa-map"
  emptyTitle="Sin planes PINAR"
  emptyDescription={canManage
    ? 'Crea el primero con "Nuevo plan".'
    : "Aún no se ha creado ningún plan institucional de archivos."}
  bind:createOpen
>
  <svelte:fragment slot="toolbar-extra">
    <FormField
      id="pinar-filtro-estado"
      name="estado"
      label="Estado"
      type="select"
      options={ESTADO_OPTIONS}
      bind:value={estadoFiltro}
      on:change={onFiltroChange}
    />
  </svelte:fragment>

  <svelte:fragment slot="thead">
    <th scope="col">Versión</th>
    <th scope="col">Estado</th>
    <th scope="col">Vigencia</th>
    <th scope="col">Acto administrativo</th>
    <th scope="col">Creado</th>
  </svelte:fragment>

  <svelte:fragment slot="row" let:item>
    <td>v{item.version}</td>
    <td>
      <StatusChip status={ESTADO_CHIP_MAP[item.estado] ?? "neutral"} label={ESTADO_LABELS[item.estado] ?? item.estado} />
    </td>
    <td>
      {#if item.vigencia_desde || item.vigencia_hasta}
        {formatDate(item.vigencia_desde)} – {formatDate(item.vigencia_hasta)}
      {:else}
        —
      {/if}
    </td>
    <td>{item.acto_administrativo ?? "—"}</td>
    <td>{formatDate(item.created_at)}</td>
  </svelte:fragment>

  <svelte:fragment slot="row-actions" let:item>
    <Button variant="ghost" size="sm" icon="fas fa-eye" href="/admin/pinar/{item.id}">Ver</Button>
  </svelte:fragment>

  <svelte:fragment slot="create-form">
    <FormField
      id="pinar-vision"
      label="Visión"
      type="textarea"
      hint="Visión estratégica del plan (opcional; puede completarse después, mientras esté en borrador)."
      bind:value={fVision}
    />
    <FormField id="pinar-vigencia-desde" label="Vigencia desde" type="date" bind:value={fVigenciaDesde} />
    <FormField id="pinar-vigencia-hasta" label="Vigencia hasta" type="date" bind:value={fVigenciaHasta} />
  </svelte:fragment>
  <svelte:fragment slot="create-footer">
    <Button variant="ghost" on:click={() => (createOpen = false)} disabled={actionLoading}>Cancelar</Button>
    <Button icon="fas fa-save" loading={actionLoading} on:click={crear}>Crear</Button>
  </svelte:fragment>
</CatalogCrudPanel>
