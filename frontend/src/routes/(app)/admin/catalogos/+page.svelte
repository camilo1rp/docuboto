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
  /admin/catalogos — Catálogos institucionales de referencia (E14, Fase 5
  cierre de desfase API↔UI) — services/tenant-service/app/routers/catalogos.py.

  Disposición MAESTRO-DETALLE en una sola pantalla: lista de catálogos a la
  izquierda (nombres reales del backend, `GET /catalogos`), items del
  catálogo seleccionado a la derecha (CatalogCrudPanel). En viewport angosto
  degrada a DOS NIVELES navegables (maestro visible XOR detalle visible +
  botón "Volver"), nunca a dos columnas apretadas — CSS puro, sin duplicar
  markup.

  RBAC: ver el hallazgo de dominio en +page.server.js — el backend NO gatea
  por permiso ninguno de los endpoints de catálogos; esta pantalla gatea sus
  botones de escritura con USUA_PERM_ADMIN como medida conservadora del
  cliente (nunca ofrece MENOS de lo que el backend exige — aquí ofrece más
  restricción, lo cual nunca es inseguro).
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import CatalogCrudPanel from "$lib/components/features/admin/CatalogCrudPanel.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** @type {import('./$types').PageData} */
  export let data;

  $: canWrite = $permissions.includes("USUA_PERM_ADMIN");

  // Etiquetas legibles para los nombres kebab-case del backend. Si el
  // backend añade un catálogo nuevo que no está en este mapa, se muestra el
  // nombre crudo — nunca se oculta un catálogo real por falta de etiqueta.
  const LABELS = {
    "tipos-identificacion": "Tipos de identificación",
    "tipos-remitente": "Tipos de remitente",
    "medios-recepcion": "Medios de recepción",
    "tipos-anexo": "Tipos de anexo",
    causales: "Causales",
    "formas-envio": "Formas de envío",
    soportes: "Soportes",
    "mensajes-rapidos": "Mensajes rápidos",
  };
  function labelFor(catalogo) {
    return LABELS[catalogo] ?? catalogo;
  }

  const catalogos = data.catalogos ?? [];

  let selected = null;
  let items = [];
  let itemsLoading = false;
  let itemsError = null;

  async function selectCatalogo(catalogo) {
    selected = catalogo;
    items = [];
    itemsError = null;
    itemsLoading = true;
    try {
      const res = await fetch(`/admin/catalogos/api/${encodeURIComponent(catalogo)}`);
      if (res.ok) {
        const raw = await res.json().catch(() => []);
        items = Array.isArray(raw) ? raw : [];
      } else {
        const body = await res.json().catch(() => ({}));
        itemsError = mensajeError(body, `Error ${res.status}`);
      }
    } catch {
      itemsError = "No se pudo conectar al servidor de catálogos.";
    } finally {
      itemsLoading = false;
    }
  }

  function backToMaster() {
    selected = null;
  }

  // ---------------------------------------------------------------------------
  // Crear / editar / borrar item
  // ---------------------------------------------------------------------------
  let actionLoading = false;
  let createOpen = false;
  let editOpen = false;
  let editTarget = null;
  let deleteOpen = false;
  let deleteTarget = null;

  let fCodigo = "";
  let fNombre = "";
  let fAmbito = "general";
  let fCodigoError = "";
  let fNombreError = "";

  /**
   * Ámbito = a qué acto pertenece una causal (migración tenant 008). Solo el
   * catálogo `causales` lo tiene: pedirlo en cualquier otro devuelve 422
   * `ambito_no_aplica`, así que el campo solo se muestra donde existe.
   *
   * Las etiquetas son las del acto tal como lo ve quien administra, no el valor
   * crudo; el valor crudo es el que viaja al backend.
   */
  const AMBITOS = [
    { value: "general", label: "General (aparece en todos los actos)" },
    { value: "devolucion", label: "Devolución de un radicado" },
    { value: "anulacion", label: "Anulación de un radicado" },
    { value: "reasignacion", label: "Reasignación" },
    { value: "devolucion_postal", label: "Devolución postal (envíos)" },
  ];
  const AMBITO_LABEL = Object.fromEntries(AMBITOS.map((a) => [a.value, a.label]));
  $: usaAmbito = selected === "causales";

  function openCreate() {
    fCodigo = "";
    fNombre = "";
    fAmbito = "general";
    fCodigoError = "";
    fNombreError = "";
    createOpen = true;
  }

  async function crearItem() {
    fCodigoError = "";
    fNombreError = "";
    if (!fCodigo.trim()) {
      fCodigoError = "El código es obligatorio.";
      return;
    }
    if (!fNombre.trim()) {
      fNombreError = "El nombre es obligatorio.";
      return;
    }
    actionLoading = true;
    try {
      const res = await fetch(`/admin/catalogos/api/${encodeURIComponent(selected)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          codigo: fCodigo.trim(),
          nombre: fNombre.trim(),
          activo: true,
          // Solo donde el catálogo lo admite: enviarlo en otro es un 422.
          ...(usaAmbito ? { ambito: fAmbito } : {}),
        }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(mensajeError(resData, "No se pudo crear el item."));
        return;
      }
      items = [...items, resData];
      createOpen = false;
      notifySuccess("Item de catálogo creado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  let eNombre = "";
  let eActivo = true;
  let eAmbito = "general";

  function openEdit(item) {
    editTarget = item;
    eNombre = item.nombre ?? "";
    eActivo = item.activo !== false;
    eAmbito = item.ambito ?? "general";
    editOpen = true;
  }

  async function guardarItem() {
    if (!editTarget) return;
    actionLoading = true;
    try {
      const res = await fetch(
        `/admin/catalogos/api/${encodeURIComponent(selected)}/${editTarget.id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            nombre: eNombre.trim(),
            activo: eActivo,
            ...(usaAmbito ? { ambito: eAmbito } : {}),
          }),
        },
      );
      const resData = await res.json();
      if (!res.ok) {
        notifyError(mensajeError(resData, "No se pudo actualizar el item."));
        return;
      }
      items = items.map((i) => (i.id === resData.id ? resData : i));
      editOpen = false;
      editTarget = null;
      notifySuccess("Item de catálogo actualizado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  async function confirmarBorrarItem(event) {
    const item = event.detail.item;
    actionLoading = true;
    try {
      const res = await fetch(`/admin/catalogos/api/${encodeURIComponent(selected)}/${item.id}`, {
        method: "DELETE",
      });
      if (res.status === 204 || res.ok) {
        items = items.filter((i) => i.id !== item.id);
        notifySuccess("Item de catálogo eliminado.");
        deleteOpen = false;
        deleteTarget = null;
      } else {
        const err = await res.json().catch(() => ({}));
        notifyError(mensajeError(err, "No se pudo eliminar el item."));
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Catálogos" }]);
  });
</script>

<svelte:head>
  <title>Catálogos — OrpycaMCP</title>
</svelte:head>

<div class="cat-page">
  <header class="cat-page__header">
    <h1 class="cat-page__title">Catálogos</h1>
    <p class="cat-page__subtitle">Catálogos institucionales de referencia (E14).</p>
  </header>

  {#if data.loadError}
    <div class="cat-page__error" role="alert">
      <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
      {data.loadError}
    </div>
  {:else}
    <div class="cat-layout" class:cat-layout--detail-active={selected !== null}>
      <!-- Maestro: lista de catálogos -->
      <nav class="cat-master" aria-label="Catálogos disponibles">
        <ul class="cat-master__list" role="list">
          {#each catalogos as catalogo (catalogo)}
            <li>
              <button
                type="button"
                class="cat-master__item"
                class:cat-master__item--active={selected === catalogo}
                aria-current={selected === catalogo ? "true" : undefined}
                on:click={() => selectCatalogo(catalogo)}
              >
                <span>{labelFor(catalogo)}</span>
                <i class="fas fa-chevron-right" aria-hidden="true"></i>
              </button>
            </li>
          {/each}
        </ul>
      </nav>

      <!-- Detalle: items del catálogo seleccionado -->
      <section class="cat-detail" aria-label="Items del catálogo seleccionado">
        {#if selected === null}
          <p class="cat-detail__placeholder">
            Selecciona un catálogo para ver y administrar sus items.
          </p>
        {:else}
          <button type="button" class="cat-detail__back" on:click={backToMaster}>
            <i class="fas fa-arrow-left" aria-hidden="true"></i>
            Volver a catálogos
          </button>

          {#if itemsLoading}
            <Loader type="skeleton" lines={4} label="Cargando {labelFor(selected)}…" />
          {:else}
            <CatalogCrudPanel
              title={labelFor(selected)}
              ariaLabel="Items de {labelFor(selected)}"
              {items}
              rowKey={(i) => i.id}
              loadError={itemsError}
              canCreate={canWrite}
              createLabel="Nuevo item"
              emptyIcon="fas fa-list"
              emptyTitle="Sin items"
              emptyDescription="Crea el primero con «Nuevo item»."
              bind:createOpen
              on:create={openCreate}
              bind:editOpen
              {editTarget}
              bind:deleteOpen
              {deleteTarget}
              deleteConfirmText={(i) =>
                `¿Eliminar el item "${i.nombre}" (${i.codigo})? Esta acción no puede deshacerse.`}
              deleteLoading={actionLoading}
              on:confirmDelete={confirmarBorrarItem}
            >
              <svelte:fragment slot="thead">
                <th scope="col">Código</th>
                <th scope="col">Nombre</th>
                <th scope="col">Estado</th>
              </svelte:fragment>

              <svelte:fragment slot="row" let:item>
                <td><code>{item.codigo}</code></td>
                <td>
                  {item.nombre}
                  {#if usaAmbito}
                    <span class="cat-ambito">{AMBITO_LABEL[item.ambito] ?? item.ambito ?? "—"}</span
                    >
                  {/if}
                </td>
                <td
                  ><StatusChip
                    status={item.activo ? "success" : "neutral"}
                    label={item.activo ? "Activo" : "Inactivo"}
                    dot
                  /></td
                >
              </svelte:fragment>

              <svelte:fragment slot="row-actions" let:item>
                {#if canWrite}
                  <Button
                    variant="ghost"
                    size="sm"
                    icon="fas fa-pen"
                    on:click={() => openEdit(item)}>Editar</Button
                  >
                  <Button
                    variant="ghost"
                    size="sm"
                    icon="fas fa-trash"
                    on:click={() => {
                      deleteTarget = item;
                      deleteOpen = true;
                    }}
                  >
                    Eliminar
                  </Button>
                {/if}
              </svelte:fragment>

              <svelte:fragment slot="create-form">
                <form class="cat-form" on:submit|preventDefault={crearItem} novalidate>
                  <FormField
                    id="item-codigo"
                    label="Código"
                    required
                    bind:value={fCodigo}
                    error={fCodigoError}
                    hint="Máximo 10 caracteres."
                  />
                  <FormField
                    id="item-nombre"
                    label="Nombre"
                    required
                    bind:value={fNombre}
                    error={fNombreError}
                  />
                  {#if usaAmbito}
                    <FormField
                      id="item-ambito"
                      label="Ámbito"
                      type="select"
                      bind:value={fAmbito}
                      options={AMBITOS}
                      hint="Acto en el que se ofrecerá esta causal. «General» la ofrece en todos."
                    />
                  {/if}
                </form>
              </svelte:fragment>
              <svelte:fragment slot="create-footer">
                <Button variant="ghost" on:click={() => (createOpen = false)}>Cancelar</Button>
                <Button icon="fas fa-save" loading={actionLoading} on:click={crearItem}
                  >Crear item</Button
                >
              </svelte:fragment>

              <svelte:fragment slot="edit-form" let:item>
                <form class="cat-form" on:submit|preventDefault={guardarItem} novalidate>
                  <p class="cat-form__hint">Código <code>{item.codigo}</code> — inmutable.</p>
                  <FormField id="item-edit-nombre" label="Nombre" required bind:value={eNombre} />
                  {#if usaAmbito}
                    <FormField
                      id="item-edit-ambito"
                      label="Ámbito"
                      type="select"
                      bind:value={eAmbito}
                      options={AMBITOS}
                      hint="Acto en el que se ofrecerá esta causal. «General» la ofrece en todos."
                    />
                  {/if}
                  <label class="cat-form__check">
                    <input type="checkbox" bind:checked={eActivo} />
                    Activo
                  </label>
                </form>
              </svelte:fragment>
              <svelte:fragment slot="edit-footer">
                <Button variant="ghost" on:click={() => (editOpen = false)}>Cancelar</Button>
                <Button icon="fas fa-save" loading={actionLoading} on:click={guardarItem}
                  >Guardar</Button
                >
              </svelte:fragment>
            </CatalogCrudPanel>
          {/if}
        {/if}
      </section>
    </div>
  {/if}
</div>

<style lang="scss">
  .cat-page {
    padding: var(--op-space-5);
    max-width: 1200px;
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
      padding: var(--op-space-3) var(--op-space-4);
      background: var(--op-error-bg);
      color: var(--op-error-fg);
      border-radius: var(--op-radius);
      font-size: var(--op-font-sm);
    }
  }

  .cat-layout {
    display: grid;
    grid-template-columns: 280px 1fr;
    gap: var(--op-space-5);
    align-items: start;
  }

  .cat-master {
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    background: var(--op-surface);
    overflow: hidden;

    &__list {
      list-style: none;
      margin: 0;
      padding: 0;
    }

    &__item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      width: 100%;
      padding: var(--op-space-3) var(--op-space-4);
      background: none;
      border: none;
      border-bottom: 1px solid var(--op-border);
      font-size: var(--op-font-sm);
      color: var(--op-text);
      text-align: left;
      cursor: pointer;
      min-height: 44px;

      &:hover {
        background: var(--op-bg);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: -2px;
      }

      i {
        color: var(--op-text-secondary);
        font-size: var(--op-font-xs);
      }

      &--active {
        background: var(--op-primary-light);
        color: var(--op-primary-dark);
        font-weight: var(--op-weight-bold);
      }
    }

    li:last-child &__item {
      border-bottom: none;
    }
  }

  .cat-detail {
    min-width: 0;

    &__placeholder {
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
      padding: var(--op-space-5);
      text-align: center;
      border: 1px dashed var(--op-border);
      border-radius: var(--op-radius);
    }

    &__back {
      display: none;
      align-items: center;
      gap: var(--op-space-2);
      background: none;
      border: none;
      color: var(--op-primary-dark);
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
      cursor: pointer;
      padding: var(--op-space-2) 0;
      margin-bottom: var(--op-space-3);
    }
  }

  // Ámbito bajo el nombre en la tabla: la causal y el acto al que pertenece se
  // leen juntos, que es como se decide si está bien clasificada.
  .cat-ambito {
    display: block;
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
  }

  .cat-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__hint {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__check {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-text);
      cursor: pointer;
    }
  }

  // Mobile: dos niveles navegables (maestro XOR detalle), nunca dos
  // columnas apretadas. El botón "Volver" solo existe visualmente aquí.
  @media (max-width: 640px) {
    .cat-layout {
      grid-template-columns: 1fr;

      &--detail-active {
        .cat-master {
          display: none;
        }
      }

      &:not(.cat-layout--detail-active) {
        .cat-detail {
          display: none;
        }
      }
    }

    .cat-detail__back {
      display: inline-flex;
    }
  }
</style>
