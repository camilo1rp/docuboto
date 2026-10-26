<!--
  Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
  Contacto: aurigadl@gmail.com

  Software libre: este archivo se distribuye bajo la Licencia Pública General
  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
-->

<!--
  /admin/usuarios — Administración de usuarios y su URD (E08, Fase 5 cierre
  de desfase API↔UI).

  RIESGO DE DOMINIO: esta pantalla manipula usuarios, permisos y clearance —
  la superficie de más riesgo de escalada de privilegios del sistema. Toda
  consulta va contra el tenant de la sesión (search_path del backend); el
  backend re-valida siempre, pero la UI no ofrece nada que el backend
  rechazaría (USUA_PERM_ADMIN, D-11).

  QUÉ NO EXISTE en el backend (rbac.py): actualizar, desactivar ni borrar un
  usuario. Solo alta + lectura + gestión de su URD. La tabla NO tiene columna
  de acciones de edición/borrado del usuario — no es una omisión de UI, es
  que el endpoint no existe.

  Detalle (Drawer, no Modal — CLAUDE.md: nunca apilar Modal sobre Drawer
  abierto): permisos EFECTIVOS (agregados de todos sus grupos) + su URD
  (dependencia + rol=grupo), con alta/baja de URD.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions, userId } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import CatalogCrudPanel from "$lib/components/features/admin/CatalogCrudPanel.svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** @type {import('./$types').PageData} */
  export let data;

  let users = data.users ?? [];
  const callerIsRoot = data.callerIsRoot === true;
  const groups = data.groups ?? [];
  const dependencias = data.dependencias ?? [];

  $: isAdmin = $permissions.includes("USUA_PERM_ADMIN");

  $: groupNameById = new Map(groups.map((g) => [g.id, g.name]));
  $: dependenciaByCodigo = new Map(dependencias.map((d) => [d.codigo, d]));

  // ---------------------------------------------------------------------------
  // Crear usuario
  // ---------------------------------------------------------------------------
  let createOpen = false;
  let actionLoading = false;
  let fUsername = "";
  let fEmail = "";
  let fIsRoot = false;
  let fUsernameError = "";

  function openCreate() {
    fUsername = "";
    fEmail = "";
    fIsRoot = false;
    fUsernameError = "";
    createOpen = true;
  }

  async function crearUsuario() {
    fUsernameError = "";
    if (!fUsername.trim()) {
      fUsernameError = "El nombre de usuario es obligatorio.";
      return;
    }
    actionLoading = true;
    try {
      const res = await fetch("/admin/usuarios/api", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: fUsername.trim(),
          email: fEmail.trim() || null,
          // `callerIsRoot &&` y no solo `fIsRoot`: ocultar la casilla es UI, no
          // control. Si el estado quedara en `true` por cualquier vía (una
          // sesión que cambia, un reinicio parcial del formulario), esto impide
          // enviar una petición que el backend ya sabe que va a rechazar.
          is_root: callerIsRoot && fIsRoot,
        }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(mensajeError(resData, "No se pudo crear el usuario."));
        return;
      }
      users = [resData, ...users];
      createOpen = false;
      notifySuccess("Usuario creado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Drawer de detalle: permisos efectivos + URD
  // ---------------------------------------------------------------------------
  let drawerOpen = false;
  /** @type {Record<string, any>|null} */
  let selectedUser = null;
  let detailLoading = false;
  let detailError = "";
  /** @type {{is_root: boolean, permissions: Record<string, number>}|null} */
  let effectivePermissions = null;
  /** @type {Array<Record<string, any>>} */
  let urdList = [];
  // Fallo de la carga de URD, SEPARADO del de permisos. Compartir una sola
  // variable hacía que el segundo fallo se perdiera —y con él la única señal
  // de que la lista está vacía porque no se pudo leer, no porque no haya nada.
  let urdError = "";

  async function openDetail(user) {
    selectedUser = user;
    drawerOpen = true;
    detailError = "";
    urdError = "";
    effectivePermissions = null;
    urdList = [];
    detailLoading = true;
    try {
      const [permRes, urdRes] = await Promise.all([
        fetch(`/admin/usuarios/api/${user.id}/permissions`),
        fetch(`/admin/usuarios/api/${user.id}/urd`),
      ]);
      if (permRes.ok) {
        effectivePermissions = await permRes.json();
      } else {
        detailError = "No se pudieron cargar los permisos efectivos.";
      }
      if (urdRes.ok) {
        const raw = await urdRes.json();
        urdList = Array.isArray(raw) ? raw : [];
      } else {
        // Antes era `else if (!detailError)`: si los permisos ya habían
        // fallado, el fallo de la URD se descartaba, `urdList` se quedaba en
        // `[]` y la sección afirmaba «Sin dependencias asignadas» — una
        // AFIRMACIÓN FALSA sobre datos que nunca se leyeron. Un administrador
        // podía concluir que a esa persona no le habían asignado unidad y
        // asignarle otra encima.
        urdError = "No se pudieron cargar las dependencias de este usuario.";
      }
    } catch {
      detailError = "No se pudo conectar al servidor.";
      urdError = "No se pudieron cargar las dependencias de este usuario.";
    } finally {
      detailLoading = false;
    }
  }

  function closeDrawer() {
    drawerOpen = false;
    selectedUser = null;
  }

  // Alta de URD dentro del drawer
  let fDepeCodigo = "";
  let fRolId = "";
  let fEsPrincipal = false;
  let urdActionLoading = false;
  let urdFormError = "";

  async function agregarUrd() {
    urdFormError = "";
    if (!selectedUser) return;
    const depeCodigoNum = Number(fDepeCodigo);
    if (!fDepeCodigo || !Number.isInteger(depeCodigoNum)) {
      urdFormError = "Selecciona una dependencia.";
      return;
    }
    if (!fRolId) {
      urdFormError = "Selecciona un rol (grupo).";
      return;
    }
    urdActionLoading = true;
    try {
      const res = await fetch(`/admin/usuarios/api/${selectedUser.id}/urd`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          depe_id: depeCodigoNum,
          rol_id: fRolId,
          es_principal: fEsPrincipal,
        }),
      });
      const resData = await res.json();
      if (!res.ok) {
        notifyError(mensajeError(resData, "No se pudo asignar la URD."));
        return;
      }
      urdList = [...urdList, resData];
      fDepeCodigo = "";
      fRolId = "";
      fEsPrincipal = false;
      notifySuccess("URD asignada.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      urdActionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Segundo factor: restablecimiento administrativo
  // ---------------------------------------------------------------------------
  // Cierra el encierro de quien pierde el autenticador: re-enrolar y revocar
  // exigen el código actual o uno de recuperación, y los de recuperación NO
  // sirven para firmar. Sin esta acción la única salida era tocar la base de
  // datos a mano.
  //
  // Deja a la persona SIN segundo factor para que lo registre ella misma desde
  // «Mi perfil». Aquí no se enrola por nadie: quien pudiera hacerlo tendría de
  // hecho su firma personal.
  let totpMotivo = "";
  let totpResetLoading = false;
  let totpResetError = "";
  let totpResetAbierto = false;

  // Espejo del 409 `restablecimiento_propio_no_permitido` del backend: hacen
  // falta dos personas. Se compara contra la identidad INTERNA (`auth_users.id`),
  // NUNCA contra el `sub` de Keycloak — son dos UUID distintos para la misma
  // persona y compararlos da siempre falso, en silencio.
  $: esUnoMismo = Boolean($userId) && selectedUser?.id === $userId;

  function alternarRestablecerTotp() {
    totpResetAbierto = !totpResetAbierto;
    totpMotivo = "";
    totpResetError = "";
  }

  async function restablecerTotp() {
    totpResetError = "";
    if (!selectedUser) return;
    const motivo = totpMotivo.trim();
    if (motivo.length < 8) {
      totpResetError = "Explique el motivo del restablecimiento (mínimo 8 caracteres).";
      return;
    }
    totpResetLoading = true;
    try {
      const res = await fetch(`/admin/usuarios/api/${selectedUser.id}/totp`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ motivo }),
      });
      const resData = await res.json().catch(() => ({}));
      if (!res.ok) {
        totpResetError = mensajeError(resData, "No se pudo restablecer el segundo factor.");
        return;
      }
      totpResetAbierto = false;
      totpMotivo = "";
      const codigos = resData?.codigos_recuperacion_eliminados ?? 0;
      notifySuccess(
        "Segundo factor restablecido. La persona debe registrarlo de nuevo desde «Mi perfil»" +
          (codigos ? `; se anularon ${codigos} códigos de recuperación.` : "."),
      );
    } catch {
      totpResetError = "No se pudo conectar al servidor.";
    } finally {
      totpResetLoading = false;
    }
  }

  // Baja de URD — confirmación INLINE (nunca un Modal apilado sobre el Drawer).
  let pendingDeleteUrdId = null;

  function askDeleteUrd(urdId) {
    pendingDeleteUrdId = pendingDeleteUrdId === urdId ? null : urdId;
  }

  async function confirmarBorrarUrd(urd) {
    if (!selectedUser) return;
    urdActionLoading = true;
    try {
      const res = await fetch(`/admin/usuarios/api/${selectedUser.id}/urd/${urd.id}`, {
        method: "DELETE",
      });
      if (res.status === 204 || res.ok) {
        urdList = urdList.filter((u) => u.id !== urd.id);
        notifySuccess("URD eliminada.");
      } else {
        const err = await res.json().catch(() => ({}));
        notifyError(mensajeError(err, "No se pudo eliminar la URD."));
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      urdActionLoading = false;
      pendingDeleteUrdId = null;
    }
  }

  $: dependenciaOptions = dependencias.map((d) => ({
    value: String(d.codigo),
    label: `${d.sigla} — ${d.nombre}`,
  }));
  $: rolOptions = groups.map((g) => ({ value: g.id, label: g.name }));

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Usuarios" }]);
  });
</script>

<svelte:head>
  <title>Usuarios — Docuboto</title>
</svelte:head>

<div class="usuarios-page">
  <CatalogCrudPanel
    title="Usuarios"
    ariaLabel="Lista de usuarios"
    items={users}
    rowKey={(u) => u.id}
    loadError={data.loadError}
    canCreate={isAdmin}
    createLabel="Nuevo usuario"
    emptyIcon="fas fa-users"
    emptyTitle="Sin usuarios"
    emptyDescription="Crea el primero con «Nuevo usuario»."
    bind:createOpen
    on:create={openCreate}
  >
    <svelte:fragment slot="thead">
      <th scope="col">Usuario</th>
      <th scope="col">Correo</th>
      <th scope="col">Estado</th>
      <th scope="col">ROOT</th>
    </svelte:fragment>

    <svelte:fragment slot="row" let:item>
      <td class="usuarios-page__username">{item.username}</td>
      <td>{item.email ?? "—"}</td>
      <td>
        <StatusChip
          status={item.is_active ? "success" : "neutral"}
          label={item.is_active ? "Activo" : "Inactivo"}
          dot
        />
      </td>
      <td>
        {#if item.is_root}
          <StatusChip status="warning" label="ROOT" />
        {:else}
          <span class="usuarios-page__muted">—</span>
        {/if}
      </td>
    </svelte:fragment>

    <svelte:fragment slot="row-actions" let:item>
      <Button variant="ghost" size="sm" icon="fas fa-id-card" on:click={() => openDetail(item)}>
        Ver detalle
      </Button>
    </svelte:fragment>

    <svelte:fragment slot="create-form">
      <form class="usuarios-form" on:submit|preventDefault={crearUsuario} novalidate>
        <FormField
          id="usuario-username"
          label="Nombre de usuario"
          required
          bind:value={fUsername}
          error={fUsernameError}
        />
        <FormField id="usuario-email" label="Correo (opcional)" type="email" bind:value={fEmail} />
        <!-- ADR-024: solo un ROOT puede crear otro ROOT. Para el resto de
             administradores el backend responde 403, así que la casilla ni se
             ofrece — un control que solo puede fallar no es una opción, es un
             callejón sin salida con apariencia de permiso. -->
        {#if callerIsRoot}
          <label class="usuarios-form__check">
            <input type="checkbox" bind:checked={fIsRoot} />
            Otorgar ROOT (bypass total de RBAC — usar con extrema precaución)
          </label>
        {/if}
      </form>
    </svelte:fragment>
    <svelte:fragment slot="create-footer">
      <Button variant="ghost" on:click={() => (createOpen = false)}>Cancelar</Button>
      <Button icon="fas fa-save" loading={actionLoading} on:click={crearUsuario}
        >Crear usuario</Button
      >
    </svelte:fragment>
  </CatalogCrudPanel>
</div>

<!-- =========================================================================
     Drawer: detalle del usuario (permisos efectivos + URD)
     ========================================================================= -->
<Drawer
  open={drawerOpen}
  title={selectedUser ? `Usuario: ${selectedUser.username}` : "Usuario"}
  on:close={closeDrawer}
>
  {#if detailLoading}
    <Loader type="skeleton" lines={4} label="Cargando detalle del usuario…" />
  {:else}
    {#if detailError}
      <div class="usuarios-drawer__error" role="alert">
        <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
        {detailError}
      </div>
    {/if}

    <section class="usuarios-drawer__section" aria-labelledby="permisos-heading">
      <h3 id="permisos-heading" class="usuarios-drawer__heading">Permisos efectivos</h3>
      {#if effectivePermissions}
        {#if effectivePermissions.is_root}
          <p class="usuarios-drawer__hint">
            Usuario ROOT: bypass total de RBAC, no aplica el listado de permisos por-grupo.
          </p>
        {:else if Object.keys(effectivePermissions.permissions ?? {}).length === 0}
          <p class="usuarios-drawer__hint">
            Sin permisos asignados (no pertenece a ningún grupo con crud &gt; 0).
          </p>
        {:else}
          <ul class="usuarios-drawer__perm-list">
            {#each Object.entries(effectivePermissions.permissions) as [nombre, crud] (nombre)}
              <li><code>{nombre}</code> <span class="usuarios-drawer__crud">crud={crud}</span></li>
            {/each}
          </ul>
        {/if}
      {/if}
    </section>

    <section class="usuarios-drawer__section" aria-labelledby="urd-heading">
      <h3 id="urd-heading" class="usuarios-drawer__heading">URD — Usuario-Rol-Dependencia</h3>

      {#if urdError}
        <p class="usuarios-drawer__error" role="alert">{urdError}</p>
      {:else if urdList.length === 0}
        <p class="usuarios-drawer__hint">Sin dependencias asignadas.</p>
      {:else}
        <ul class="usuarios-drawer__urd-list">
          {#each urdList as urd (urd.id)}
            <li class="usuarios-drawer__urd-item">
              <div>
                <strong
                  >{dependenciaByCodigo.get(urd.depe_id)?.nombre ??
                    `Dependencia ${urd.depe_id}`}</strong
                >
                <span class="usuarios-drawer__muted">
                  — {groupNameById.get(urd.rol_id) ?? urd.rol_id}</span
                >
                {#if urd.es_principal}<StatusChip status="info" label="Principal" />{/if}
              </div>
              {#if pendingDeleteUrdId === urd.id}
                <span class="usuarios-drawer__confirm">
                  ¿Confirmar?
                  <Button
                    size="sm"
                    variant="danger"
                    loading={urdActionLoading}
                    on:click={() => confirmarBorrarUrd(urd)}
                  >
                    Sí, eliminar
                  </Button>
                  <Button size="sm" variant="ghost" on:click={() => (pendingDeleteUrdId = null)}
                    >Cancelar</Button
                  >
                </span>
              {:else}
                <Button
                  size="sm"
                  variant="ghost"
                  icon="fas fa-trash"
                  aria-label="Eliminar URD de {dependenciaByCodigo.get(urd.depe_id)?.nombre ??
                    urd.depe_id}"
                  on:click={() => askDeleteUrd(urd.id)}
                >
                  Quitar
                </Button>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}

      <form
        class="usuarios-form usuarios-form--compact"
        on:submit|preventDefault={agregarUrd}
        novalidate
      >
        <FormField
          id="urd-depe"
          label="Dependencia"
          type="select"
          bind:value={fDepeCodigo}
          options={dependenciaOptions}
          hint={dependencias.length === 0 ? "No se pudo cargar el catálogo de dependencias." : ""}
        />
        <FormField
          id="urd-rol"
          label="Rol (grupo)"
          type="select"
          bind:value={fRolId}
          options={rolOptions}
        />
        <label class="usuarios-form__check">
          <input type="checkbox" bind:checked={fEsPrincipal} />
          Dependencia principal
        </label>
        {#if urdFormError}
          <p class="usuarios-drawer__error-inline" role="alert">{urdFormError}</p>
        {/if}
        <Button type="submit" icon="fas fa-plus" loading={urdActionLoading} fullWidth
          >Asignar URD</Button
        >
      </form>
    </section>

    <section class="usuarios-drawer__section" aria-labelledby="totp-heading">
      <h3 id="totp-heading" class="usuarios-drawer__heading">Segundo factor</h3>
      {#if esUnoMismo}
        <p class="usuarios-drawer__hint">
          <span
            >El restablecimiento administrativo repone el segundo factor de otra persona. Para el
            suyo use «Mi perfil»; si también perdió los códigos de recuperación, pida a otro
            administrador que se lo restablezca.</span
          >
        </p>
      {:else}
        <p class="usuarios-drawer__hint">
          <span
            >Retira el segundo factor de esta persona para que pueda volver a registrarlo desde «Mi
            perfil». Se anulan también sus códigos de recuperación. No se le registra ningún factor
            nuevo en su nombre.</span
          >
        </p>
        {#if !totpResetAbierto}
          <Button
            variant="ghost"
            size="sm"
            icon="fas fa-mobile-screen"
            on:click={alternarRestablecerTotp}
          >
            Restablecer segundo factor
          </Button>
        {:else}
          <form
            class="usuarios-form usuarios-form--compact"
            on:submit|preventDefault={restablecerTotp}
          >
            <FormField
              id="totp-motivo"
              label="Motivo del restablecimiento"
              bind:value={totpMotivo}
              placeholder="Ej.: perdió el teléfono; verificado en persona"
              error={totpResetError}
            />
            <div class="usuarios-drawer__confirm">
              <Button
                size="sm"
                variant="danger"
                type="submit"
                loading={totpResetLoading}
                icon="fas fa-mobile-screen"
              >
                Confirmar restablecimiento
              </Button>
              <Button size="sm" variant="ghost" on:click={alternarRestablecerTotp}>Cancelar</Button>
            </div>
          </form>
        {/if}
      {/if}
    </section>
  {/if}
</Drawer>

<style lang="scss">
  .usuarios-page {
    padding: var(--op-space-5);
    max-width: var(--op-content-max);
  }

  .usuarios-page__username {
    font-weight: var(--op-weight-medium);
    color: var(--op-text);
  }

  .usuarios-page__muted,
  .usuarios-drawer__muted {
    color: var(--op-text-secondary);
  }

  .usuarios-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &--compact {
      margin-top: var(--op-space-3);
      padding-top: var(--op-space-3);
      border-top: 1px solid var(--op-border);
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

  .usuarios-drawer {
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

    &__hint {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__error,
    &__error-inline {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-2) var(--op-space-3);
      background: var(--op-error-bg);
      color: var(--op-error-fg);
      border-radius: var(--op-radius);
      font-size: var(--op-font-sm);
      margin: 0 0 var(--op-space-3) 0;
    }

    &__perm-list,
    &__urd-list {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: var(--op-space-2);
    }

    &__perm-list li {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: var(--op-font-sm);
    }

    &__crud {
      color: var(--op-text-secondary);
      font-size: var(--op-font-xs);
    }

    &__urd-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--op-space-3);
      padding: var(--op-space-2) 0;
      border-bottom: 1px solid var(--op-border);
      font-size: var(--op-font-sm);
    }

    &__confirm {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-xs);
      color: var(--op-error);
      white-space: nowrap;
    }
  }
</style>
