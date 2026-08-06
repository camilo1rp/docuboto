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
  /admin/grupos — Administración de grupos: membresías, permisos y clearance
  (E08, Fase 5 cierre de desfase API↔UI).

  QUÉ NO EXISTE en el backend (rbac.py / clearance.py): actualizar ni borrar
  un grupo (solo alta + lectura de la lista). Tampoco existe NINGÚN endpoint
  de LECTURA de membresías, permisos asignados ni clearance de un grupo YA
  existente — solo altas/bajas "ciegas" (POST/DELETE members, PUT
  permissions, PUT clearance). CatalogCrudPanel encaja sin extensión: como
  esta página nunca fija `editTarget`/`deleteTarget` ni abre `editOpen`/
  `deleteOpen`, esos modales simplemente no se activan (no hace falta un
  prop `canEdit`/`canDelete` — el propio patrón de "no invocar lo que no
  existe" ya lo cubre).

  El drawer de detalle NO puede mostrar "el estado actual" del grupo (no hay
  de dónde leerlo) — se advierte explícitamente arriba de cada sección y las
  acciones se aplican de inmediato, sin confirmación de "antes/después".

  RIESGO DE DOMINIO: igual que admin/usuarios, esta pantalla toca permisos y
  clearance — superficie de escalada de privilegios. El backend SIEMPRE
  re-valida (USUA_PERM_ADMIN); la UI es un espejo, no una segunda fuente de
  verdad.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import { NIVEL_OPTIONS, getNivelChip } from "$lib/utils/nivelSeguridad.js";

  import CatalogCrudPanel from "$lib/components/features/admin/CatalogCrudPanel.svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  let groups = data.groups ?? [];
  const permissionCatalog = data.permissions ?? [];
  const securityLevels = data.securityLevels ?? [];

  $: isAdmin = $permissions.includes("USUA_PERM_ADMIN");

  // ---------------------------------------------------------------------------
  // Crear grupo
  // ---------------------------------------------------------------------------
  let createOpen = false;
  let actionLoading = false;
  let fName = "";
  let fDescription = "";
  let fNameError = "";

  function openCreate() {
    fName = "";
    fDescription = "";
    fNameError = "";
    createOpen = true;
  }

  async function crearGrupo() {
    fNameError = "";
    if (!fName.trim()) {
      fNameError = "El nombre del grupo es obligatorio.";
      return;
    }
    actionLoading = true;
    try {
      const res = await fetch("/admin/grupos/api", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: fName.trim(), description: fDescription.trim() || null }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(resData.detail ?? "No se pudo crear el grupo.");
        return;
      }
      groups = [...groups, resData];
      createOpen = false;
      notifySuccess("Grupo creado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Drawer de detalle: miembros + permisos + clearance (todo ciego, sin GET)
  // ---------------------------------------------------------------------------
  let drawerOpen = false;
  /** @type {Record<string, any>|null} */
  let selectedGroup = null;

  // Miembros — universo de usuarios pickable, cargado una sola vez por drawer.
  let pickableUsers = [];
  let pickableLoading = false;
  let pickableError = "";
  let fMemberToAdd = "";
  let memberActionLoading = false;
  // Estado SOLO de sesión: usuarios que este drawer agregó/confirmó, para dar
  // algo de feedback visual — NO es la membresía real del grupo (el backend
  // no la expone). Se reinicia cada vez que se abre el drawer.
  /** @type {Array<{id: string, username: string}>} */
  let sessionMembers = [];

  async function openDetail(group) {
    selectedGroup = group;
    drawerOpen = true;
    fMemberToAdd = "";
    sessionMembers = [];
    pickableError = "";

    if (pickableUsers.length === 0) {
      pickableLoading = true;
      try {
        const res = await fetch("/admin/grupos/api/users-pickable");
        if (res.ok) {
          pickableUsers = await res.json();
        } else {
          pickableError = "No se pudo cargar el listado de usuarios.";
        }
      } catch {
        pickableError = "No se pudo conectar al servidor.";
      } finally {
        pickableLoading = false;
      }
    }
  }

  function closeDrawer() {
    drawerOpen = false;
    selectedGroup = null;
  }

  $: availableToAdd = pickableUsers.filter((u) => !sessionMembers.some((m) => m.id === u.id));

  async function agregarMiembro() {
    if (!selectedGroup || !fMemberToAdd) return;
    const user = pickableUsers.find((u) => u.id === fMemberToAdd);
    if (!user) return;
    memberActionLoading = true;
    try {
      const res = await fetch(`/admin/grupos/api/${selectedGroup.id}/members/${user.id}`, {
        method: "POST",
      });
      if (res.status === 201 || res.ok) {
        sessionMembers = [...sessionMembers, user];
        fMemberToAdd = "";
        notifySuccess(`${user.username} agregado al grupo.`);
      } else {
        const err = await res.json().catch(() => ({}));
        notifyError(err.detail ?? "No se pudo agregar al miembro.");
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      memberActionLoading = false;
    }
  }

  async function quitarMiembro(user) {
    if (!selectedGroup) return;
    memberActionLoading = true;
    try {
      const res = await fetch(`/admin/grupos/api/${selectedGroup.id}/members/${user.id}`, {
        method: "DELETE",
      });
      if (res.status === 204 || res.ok) {
        sessionMembers = sessionMembers.filter((m) => m.id !== user.id);
        notifySuccess(`${user.username} quitado del grupo.`);
      } else {
        const err = await res.json().catch(() => ({}));
        notifyError(err.detail ?? "No se pudo quitar al miembro.");
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      memberActionLoading = false;
    }
  }

  // Permisos — un select de crud (0..5) por permiso del catálogo, guardado
  // fila a fila. `crudDraft` guarda solo lo que el admin está por enviar,
  // no un valor "actual" (que el backend no expone).
  /** @type {Record<string, string>} */
  let crudDraft = {};
  /** @type {Record<string, boolean>} */
  let permissionLoading = {};

  const CRUD_OPTIONS = [
    { value: "0", label: "0 — Ninguno" },
    { value: "1", label: "1" },
    { value: "2", label: "2" },
    { value: "3", label: "3" },
    { value: "4", label: "4" },
    { value: "5", label: "5 — Nivel 2" },
  ];

  async function guardarPermiso(permission) {
    if (!selectedGroup) return;
    const crud = Number(crudDraft[permission.id] ?? "0");
    permissionLoading = { ...permissionLoading, [permission.id]: true };
    try {
      const res = await fetch(
        `/admin/grupos/api/${selectedGroup.id}/permissions/${permission.id}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ crud }),
        },
      );
      const resData = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(resData.detail ?? "No se pudo actualizar el permiso.");
        return;
      }
      notifySuccess(`Permiso "${permission.nombre}" fijado en crud=${crud}.`);
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      permissionLoading = { ...permissionLoading, [permission.id]: false };
    }
  }

  // Clearance del grupo
  let fClearanceLevel = "";
  let clearanceLoading = false;

  $: clearanceChipPreview = fClearanceLevel ? getNivelChip(Number(fClearanceLevel)) : null;

  async function guardarClearance() {
    if (!selectedGroup || !fClearanceLevel) return;
    clearanceLoading = true;
    try {
      const res = await fetch(`/admin/grupos/api/${selectedGroup.id}/clearance`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ max_level: Number(fClearanceLevel) }),
      });
      if (res.status === 204 || res.ok) {
        notifySuccess("Clearance del grupo actualizada.");
      } else {
        const err = await res.json().catch(() => ({}));
        notifyError(err.detail ?? "No se pudo actualizar la clearance.");
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      clearanceLoading = false;
    }
  }

  $: memberOptions = availableToAdd.map((u) => ({ value: u.id, label: u.username }));
  $: levelOptions =
    securityLevels.length > 0
      ? securityLevels.map((l) => ({ value: String(l.code), label: `${l.code} — ${l.nombre}` }))
      : NIVEL_OPTIONS;

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Grupos" }]);
  });
</script>

<svelte:head>
  <title>Grupos — OrpycaMCP</title>
</svelte:head>

<div class="grupos-page">
  <CatalogCrudPanel
    title="Grupos"
    ariaLabel="Lista de grupos"
    items={groups}
    rowKey={(g) => g.id}
    loadError={data.loadError}
    canCreate={isAdmin}
    createLabel="Nuevo grupo"
    emptyIcon="fas fa-people-group"
    emptyTitle="Sin grupos"
    emptyDescription="Crea el primero con «Nuevo grupo»."
    bind:createOpen
    on:create={openCreate}
  >
    <svelte:fragment slot="thead">
      <th scope="col">Nombre</th>
      <th scope="col">Descripción</th>
    </svelte:fragment>

    <svelte:fragment slot="row" let:item>
      <td class="grupos-page__nombre">{item.name}</td>
      <td>{item.description ?? "—"}</td>
    </svelte:fragment>

    <svelte:fragment slot="row-actions" let:item>
      <Button variant="ghost" size="sm" icon="fas fa-sliders" on:click={() => openDetail(item)}>
        Ver detalle
      </Button>
    </svelte:fragment>

    <svelte:fragment slot="create-form">
      <form class="grupos-form" on:submit|preventDefault={crearGrupo} novalidate>
        <FormField
          id="grupo-nombre"
          label="Nombre"
          required
          bind:value={fName}
          error={fNameError}
        />
        <FormField
          id="grupo-descripcion"
          label="Descripción (opcional)"
          type="textarea"
          rows={2}
          bind:value={fDescription}
        />
      </form>
    </svelte:fragment>
    <svelte:fragment slot="create-footer">
      <Button variant="ghost" on:click={() => (createOpen = false)}>Cancelar</Button>
      <Button icon="fas fa-save" loading={actionLoading} on:click={crearGrupo}>Crear grupo</Button>
    </svelte:fragment>
  </CatalogCrudPanel>
</div>

<!-- =========================================================================
     Drawer: detalle del grupo (miembros + permisos + clearance)
     ========================================================================= -->
<Drawer
  open={drawerOpen}
  title={selectedGroup ? `Grupo: ${selectedGroup.name}` : "Grupo"}
  on:close={closeDrawer}
>
  <div class="grupos-drawer__gap-notice" role="note">
    <i class="fas fa-circle-info" aria-hidden="true"></i>
    El backend no expone lectura de miembros, permisos ni clearance ya asignados a este grupo — solo altas/bajas.
    Las acciones de abajo se aplican de inmediato; lo que ves en "agregados en esta sesión" es solo lo
    que hiciste aquí, no el estado real guardado en el servidor.
  </div>

  <!-- Miembros -->
  <section class="grupos-drawer__section" aria-labelledby="miembros-heading">
    <h3 id="miembros-heading" class="grupos-drawer__heading">Miembros</h3>

    {#if pickableLoading}
      <Loader type="skeleton" lines={2} label="Cargando usuarios…" />
    {:else if pickableError}
      <p class="grupos-drawer__error-inline" role="alert">{pickableError}</p>
    {:else}
      <form
        class="grupos-form grupos-form--inline"
        on:submit|preventDefault={agregarMiembro}
        novalidate
      >
        <FormField
          id="grupo-miembro"
          label="Agregar usuario"
          type="select"
          bind:value={fMemberToAdd}
          options={memberOptions}
          placeholder="Selecciona un usuario…"
          hint={availableToAdd.length === 0 ? "No hay más usuarios para agregar." : ""}
        />
        <Button
          type="submit"
          size="sm"
          icon="fas fa-plus"
          loading={memberActionLoading}
          disabled={!fMemberToAdd}
        >
          Agregar
        </Button>
      </form>

      {#if sessionMembers.length === 0}
        <p class="grupos-drawer__hint">Ningún miembro agregado en esta sesión todavía.</p>
      {:else}
        <ul class="grupos-drawer__list">
          {#each sessionMembers as member (member.id)}
            <li class="grupos-drawer__list-item">
              <span>{member.username}</span>
              <Button
                size="sm"
                variant="ghost"
                icon="fas fa-trash"
                loading={memberActionLoading}
                aria-label="Quitar a {member.username} del grupo"
                on:click={() => quitarMiembro(member)}
              >
                Quitar
              </Button>
            </li>
          {/each}
        </ul>
      {/if}
    {/if}
  </section>

  <!-- Permisos -->
  <section class="grupos-drawer__section" aria-labelledby="permisos-heading">
    <h3 id="permisos-heading" class="grupos-drawer__heading">Permisos por grupo</h3>

    {#if permissionCatalog.length === 0}
      <p class="grupos-drawer__hint">No se pudo cargar el catálogo de permisos.</p>
    {:else}
      <ul class="grupos-drawer__perm-list">
        {#each permissionCatalog as permission (permission.id)}
          <li class="grupos-drawer__perm-item">
            <div class="grupos-drawer__perm-info">
              <code>{permission.nombre}</code>
              {#if permission.descripcion}
                <span class="grupos-drawer__muted">{permission.descripcion}</span>
              {/if}
            </div>
            <div class="grupos-drawer__perm-controls">
              <FormField
                id={`grupo-crud-${permission.id}`}
                label="Nivel"
                type="select"
                options={CRUD_OPTIONS}
                value={crudDraft[permission.id] ?? "0"}
                on:change={(e) =>
                  (crudDraft = {
                    ...crudDraft,
                    [permission.id]: /** @type {HTMLSelectElement} */ (e.target).value,
                  })}
              />
              <Button
                size="sm"
                icon="fas fa-save"
                loading={!!permissionLoading[permission.id]}
                on:click={() => guardarPermiso(permission)}
              >
                Guardar
              </Button>
            </div>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <!-- Clearance -->
  <section class="grupos-drawer__section" aria-labelledby="clearance-heading">
    <h3 id="clearance-heading" class="grupos-drawer__heading">Clearance del grupo</h3>
    <form
      class="grupos-form grupos-form--inline"
      on:submit|preventDefault={guardarClearance}
      novalidate
    >
      <FormField
        id="grupo-clearance"
        label="Nivel máximo"
        type="select"
        bind:value={fClearanceLevel}
        options={levelOptions}
        placeholder="Selecciona un nivel…"
      />
      {#if clearanceChipPreview}
        <StatusChip status={clearanceChipPreview.status} label={clearanceChipPreview.label} dot />
      {/if}
      <Button
        type="submit"
        size="sm"
        icon="fas fa-save"
        loading={clearanceLoading}
        disabled={!fClearanceLevel}
      >
        Guardar
      </Button>
    </form>
  </section>
</Drawer>

<style lang="scss">
  .grupos-page {
    padding: var(--op-space-5);
    max-width: 1100px;
  }

  .grupos-page__nombre {
    font-weight: var(--op-weight-medium);
    color: var(--op-text);
  }

  .grupos-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &--inline {
      flex-direction: row;
      align-items: flex-end;
      flex-wrap: wrap;
      gap: var(--op-space-3);

      :global(.op-field) {
        flex: 1 1 220px;
      }
    }
  }

  .grupos-drawer {
    &__gap-notice {
      display: flex;
      align-items: flex-start;
      gap: var(--op-space-2);
      padding: var(--op-space-3) var(--op-space-4);
      background: var(--op-primary-light);
      color: var(--op-text);
      border-radius: var(--op-radius);
      font-size: var(--op-font-xs);
      line-height: var(--op-line-height);
      margin-bottom: var(--op-space-5);
    }

    &__section {
      margin-bottom: var(--op-space-5);
    }

    &__heading {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin: 0 0 var(--op-space-3) 0;
    }

    &__hint,
    &__muted {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__error-inline {
      padding: var(--op-space-2) var(--op-space-3);
      background: var(--op-error-bg);
      color: var(--op-error-fg);
      border-radius: var(--op-radius);
      font-size: var(--op-font-sm);
    }

    &__list,
    &__perm-list {
      list-style: none;
      margin: var(--op-space-3) 0 0 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: var(--op-space-2);
    }

    &__list-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--op-space-2) 0;
      border-bottom: 1px solid var(--op-border);
      font-size: var(--op-font-sm);
    }

    &__perm-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--op-space-3);
      padding: var(--op-space-2) 0;
      border-bottom: 1px solid var(--op-border);
      flex-wrap: wrap;
    }

    &__perm-info {
      display: flex;
      flex-direction: column;
      gap: 2px;
      font-size: var(--op-font-sm);
    }

    &__perm-controls {
      display: flex;
      align-items: flex-end;
      gap: var(--op-space-2);

      :global(.op-field) {
        width: 140px;
      }
    }
  }
</style>
