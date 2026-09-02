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
  /admin/plantillas — CRUD de plantillas de contenido reutilizables (T-12c).

  RBAC (espejo del backend, D-11):
    - Crear / borrar plantillas → USUA_PERM_ADMIN
      (`services/document-service/app/routers/borradores.py`, POST/DELETE
      `/api/v1/plantillas`).
    - Ver la lista → en realidad PERM_RADI a nivel de backend (mismo router,
      GET `/api/v1/plantillas`) — NO USUA_PERM_ADMIN como afirmaba este
      comentario (revisión UX 2026-08-02, hallazgo residual del punto 2,
      NO corregido: un USUA_PERM_ADMIN sin PERM_RADI ve la tarjeta en /admin
      pero la carga SSR de la lista falla con 403/loadError; un PERM_RADI sin
      USUA_PERM_ADMIN nunca ve la tarjeta, así que tampoco llega aquí. Fuera
      de alcance del lote 1 — requiere decidir el gate correcto en el
      backend, no solo reflejarlo).

  D-05 — access_token NUNCA en cliente:
    Todas las escrituras van a /admin/plantillas/api[/id] que leen locals.accessToken.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import RichEditor from "$lib/components/editor/RichEditor.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** @type {import('./$types').PageData} */
  export let data;

  /** @type {import('$lib/types/api.js').PlantillaResponse[]} */
  let plantillas = data.plantillas ?? [];
  let actionLoading = false;

  // ---------------------------------------------------------------------------
  // Modal de creación
  // ---------------------------------------------------------------------------
  let modalCreate = false;
  let fNombre = "";
  let fTipoDocumental = "";
  let fCuerpo = "";
  let fActivo = true;
  let fNombreError = "";
  let fCuerpoError = "";

  // ── Vocabulario de marcadores (E23, RF-PRO-01) ────────────────────────────
  // El vocabulario es CERRADO: lo que no está en esta lista no se resuelve al
  // combinar, se imprime literal en el oficio. Publicarlo aquí es lo que
  // convierte «adivinar el nombre del campo» en «elegirlo».
  /** @type {any[]} */
  const campos = data.campos ?? [];

  /** Marcadores agrupados, en el orden en que los declara el backend. */
  const camposPorGrupo = campos.reduce((acc, c) => {
    (acc[c.grupo ?? "Otros"] ??= []).push(c);
    return acc;
  }, /** @type {Record<string, any[]>} */ ({}));

  let camposAbiertos = false;

  /**
   * Inserta el marcador al final del cuerpo.
   *
   * No se intenta insertar en la posición del cursor: RichEditor no expone ese
   * punto de entrada y fingirlo —insertar «donde estaba» leyendo el DOM— es la
   * clase de atajo que coloca el texto en el sitio equivocado sin avisar.
   * Añadir al final es predecible, y el funcionario lo mueve.
   */
  function insertarCampo(marcador) {
    fCuerpo = `${fCuerpo ?? ""}${marcador}`;
  }

  // ── Alcance por dependencia (E23 Inc.5, RF-PRO-06) ────────────────────────
  /** @type {any[]} */
  let dependencias = data.dependencias ?? [];
  let fEsGlobal = true;
  /** @type {number[]} */
  let fDependencias = [];
  let alcanceLoading = false;

  /** Plantilla cuyo alcance se está editando, o `null`. */
  let editandoAlcance = null;

  function abrirAlcance(plantilla) {
    editandoAlcance = plantilla;
    fEsGlobal = plantilla.es_global !== false;
    fDependencias = Array.isArray(plantilla.dependencias) ? [...plantilla.dependencias] : [];
  }

  function alternarDependencia(codigo) {
    fDependencias = fDependencias.includes(codigo)
      ? fDependencias.filter((c) => c !== codigo)
      : [...fDependencias, codigo];
  }

  async function guardarAlcance() {
    if (!editandoAlcance) return;
    alcanceLoading = true;
    try {
      const res = await fetch(`/admin/plantillas/api/${editandoAlcance.id}/alcance`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ es_global: fEsGlobal, dependencias: fDependencias }),
      });
      const dataRes = await res.json();
      if (!res.ok) {
        notifyError(mensajeError(dataRes, "Error al guardar el alcance."));
        return;
      }
      plantillas = plantillas.map((p) => (p.id === dataRes.id ? { ...p, ...dataRes } : p));
      editandoAlcance = null;
      notifySuccess("Alcance actualizado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      alcanceLoading = false;
    }
  }

  /** Texto del alcance de una plantilla, para la tabla. */
  function textoAlcance(plantilla) {
    if (plantilla.es_global !== false) return "Todas las dependencias";
    const n = plantilla.dependencias?.length ?? 0;
    // El cero se nombra: «0 dependencias» se leería como un dato que falta, y
    // es un estado deliberado —plantilla retirada sin borrar su historial—.
    if (n === 0) return "Ninguna (retirada de circulación)";
    return n === 1 ? "1 dependencia" : `${n} dependencias`;
  }

  function openCreate() {
    fNombre = "";
    fTipoDocumental = "";
    fCuerpo = "";
    fActivo = true;
    fNombreError = "";
    fCuerpoError = "";
    modalCreate = true;
  }

  async function crearPlantilla() {
    fNombreError = "";
    fCuerpoError = "";

    if (!fNombre.trim()) {
      fNombreError = "El nombre es obligatorio.";
      return;
    }
    if (!fCuerpo.trim()) {
      fCuerpoError = "El contenido no puede estar vacío.";
      return;
    }

    actionLoading = true;
    try {
      const res = await fetch("/admin/plantillas/api", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          nombre: fNombre.trim(),
          tipo_documental: fTipoDocumental.trim() || null,
          cuerpo: fCuerpo,
          activo: fActivo,
        }),
      });

      const dataRes = await res.json();
      if (!res.ok) {
        notifyError(mensajeError(dataRes, "Error al crear la plantilla."));
        return;
      }

      plantillas = [dataRes, ...plantillas];
      modalCreate = false;
      notifySuccess("Plantilla creada.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Confirmación de borrado
  // ---------------------------------------------------------------------------
  /** @type {import('$lib/types/api.js').PlantillaResponse|null} */
  let deletingPlantilla = null;
  let modalDelete = false;

  function confirmDelete(plantilla) {
    deletingPlantilla = plantilla;
    modalDelete = true;
  }

  async function deletePlantilla() {
    if (!deletingPlantilla) return;
    actionLoading = true;
    try {
      const res = await fetch(`/admin/plantillas/api/${deletingPlantilla.id}`, {
        method: "DELETE",
      });

      if (res.status === 204 || res.ok) {
        plantillas = plantillas.filter((p) => p.id !== deletingPlantilla.id);
        notifySuccess("Plantilla eliminada.");
        modalDelete = false;
        deletingPlantilla = null;
      } else {
        const err = await res.json().catch(() => ({}));
        notifyError(mensajeError(err, "No se pudo eliminar la plantilla."));
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------
  function formatDate(iso) {
    if (!iso) return "—";
    try {
      return new Intl.DateTimeFormat("es-CO", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
      }).format(new Date(iso));
    } catch {
      return iso.slice(0, 10);
    }
  }

  $: isAdmin = $permissions.includes("USUA_PERM_ADMIN");

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Plantillas" }]);
  });
</script>

<svelte:head>
  <title>Plantillas de contenido — OrpycaMCP</title>
</svelte:head>

<div class="plantillas-page">
  <!-- Cabecera -->
  <div class="plantillas-page__header">
    <h1 class="plantillas-page__title">Plantillas de contenido</h1>
    {#if isAdmin}
      <Button icon="fas fa-plus" on:click={openCreate}>Nueva plantilla</Button>
    {/if}
  </div>

  <!-- Error de carga -->
  {#if data.loadError}
    <div class="plantillas-page__error" role="alert">
      <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
      {data.loadError}
    </div>
  {/if}

  <!-- Lista vacía -->
  {#if plantillas.length === 0 && !data.loadError}
    <EmptyState
      icon="fa-copy"
      title="Sin plantillas"
      description="Crea la primera plantilla de contenido con el botón «Nueva plantilla»."
    />
  {:else if plantillas.length > 0}
    <!-- Tabla -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <div class="plantillas-table-wrap" role="region" aria-label="Lista de plantillas" tabindex="0">
      <table class="plantillas-table" aria-label="Plantillas de contenido">
        <thead>
          <tr>
            <th scope="col">Nombre</th>
            <th scope="col">Tipo documental</th>
            <th scope="col">Estado</th>
            <th scope="col">Alcance</th>
            <th scope="col">Creada</th>
            {#if isAdmin}
              <th scope="col"><span class="sr-only">Acciones</span></th>
            {/if}
          </tr>
        </thead>
        <tbody>
          {#each plantillas as plantilla (plantilla.id)}
            <tr class="plantillas-table__row">
              <td class="plantillas-table__nombre">{plantilla.nombre}</td>
              <td>{plantilla.tipo_documental ?? "—"}</td>
              <td>
                <StatusChip
                  status={plantilla.activo ? "success" : "neutral"}
                  label={plantilla.activo ? "Activa" : "Inactiva"}
                  dot
                />
              </td>
              <td class="plantillas-table__alcance">{textoAlcance(plantilla)}</td>
              <td class="plantillas-table__date">{formatDate(plantilla.created_at)}</td>
              {#if isAdmin}
                <td class="plantillas-table__actions">
                  <Button
                    variant="ghost"
                    size="sm"
                    icon="fas fa-sitemap"
                    aria-label="Cambiar alcance de {plantilla.nombre}"
                    on:click={() => abrirAlcance(plantilla)}
                  >
                    Alcance
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon="fas fa-trash"
                    aria-label="Eliminar plantilla {plantilla.nombre}"
                    on:click={() => confirmDelete(plantilla)}
                  >
                    Eliminar
                  </Button>
                </td>
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<!-- =========================================================================
     Modal: crear plantilla
     ========================================================================= -->
<Modal open={modalCreate} title="Nueva plantilla" size="lg" on:close={() => (modalCreate = false)}>
  <form
    class="plantilla-form"
    on:submit|preventDefault={crearPlantilla}
    aria-label="Formulario de nueva plantilla"
    novalidate
  >
    <FormField
      id="plantilla-nombre"
      label="Nombre"
      type="text"
      bind:value={fNombre}
      placeholder="Identificador de la plantilla"
      required
      error={fNombreError}
    />

    <FormField
      id="plantilla-tipo"
      label="Tipo documental"
      type="text"
      bind:value={fTipoDocumental}
      placeholder="Ej. Circular, Oficio, Resolución (opcional)"
    />

    <div class="plantilla-form__check-row">
      <label class="plantilla-form__check-label" for="plantilla-activo">
        <input
          id="plantilla-activo"
          type="checkbox"
          class="plantilla-form__check"
          bind:checked={fActivo}
        />
        Activa (visible en el selector de borradores)
      </label>
    </div>

    <div class="plantilla-form__editor">
      <!--
        MEDIO #3: El id del mensaje de error se pasa a RichEditor vía ariaDescribedby
        para que el contenteditable de TipTap quede asociado al mensaje cuando hay error.
      -->
      <RichEditor
        id="plantilla-cuerpo"
        label="Contenido de la plantilla"
        protegerMarcadores
        bind:value={fCuerpo}
        placeholder="Escriba el contenido reutilizable de la plantilla…"
        ariaDescribedby={fCuerpoError ? "plantilla-cuerpo-error" : undefined}
      />
      <p
        id="plantilla-cuerpo-error"
        class="plantilla-form__editor-error"
        role="alert"
        aria-live="polite"
        aria-atomic="true"
      >
        {fCuerpoError || ""}
      </p>

      {#if campos.length > 0}
        <div class="plantilla-campos">
          <button
            type="button"
            class="plantilla-campos__toggle"
            aria-expanded={camposAbiertos}
            aria-controls="plantilla-campos-lista"
            on:click={() => (camposAbiertos = !camposAbiertos)}
          >
            <i class="fas fa-{camposAbiertos ? 'chevron-down' : 'chevron-right'}" aria-hidden="true"
            ></i>
            Campos que se pueden insertar ({campos.length})
          </button>

          {#if camposAbiertos}
            <div id="plantilla-campos-lista" class="plantilla-campos__lista">
              <p class="plantilla-campos__nota">
                Solo estos campos se reemplazan al crear el borrador. Cualquier otro texto entre
                asteriscos se imprime tal cual en el documento.
              </p>
              {#each Object.entries(camposPorGrupo) as [grupo, delGrupo] (grupo)}
                <div class="plantilla-campos__grupo">
                  <h4 class="plantilla-campos__grupo-titulo">{grupo}</h4>
                  <ul class="plantilla-campos__items">
                    {#each delGrupo as campo (campo.marcador)}
                      <li>
                        <button
                          type="button"
                          class="plantilla-campos__item"
                          title={campo.descripcion}
                          on:click={() => insertarCampo(campo.marcador)}
                        >
                          <code>{campo.marcador}</code>
                          <span class="plantilla-campos__desc">{campo.descripcion}</span>
                          {#if campo.diferido}
                            <!-- Se resuelve al radicar, no al combinar: si no se
                                 dice, el borrador parece incompleto y se
                                 «arregla» escribiendo el número a mano. -->
                            <span class="plantilla-campos__tag">al radicar</span>
                          {/if}
                        </button>
                      </li>
                    {/each}
                  </ul>
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/if}
    </div>
  </form>

  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalCreate = false)}>Cancelar</Button>
    <Button icon="fas fa-save" loading={actionLoading} on:click={crearPlantilla}>
      Crear plantilla
    </Button>
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: confirmar borrado
     ========================================================================= -->
<Modal
  open={editandoAlcance !== null}
  title="Alcance de la plantilla"
  size="md"
  on:close={() => (editandoAlcance = null)}
>
  {#if editandoAlcance}
    <div class="plantilla-alcance">
      <p class="plantilla-alcance__intro">
        Quién ve <strong>{editandoAlcance.nombre}</strong> al redactar una respuesta. No es una restricción
        de seguridad —una plantilla no contiene datos de nadie— sino de uso: con muchas plantillas en
        la lista, la que hace falta no se encuentra.
      </p>

      <div class="plantilla-form__check-row">
        <label class="plantilla-form__check-label" for="plantilla-global">
          <input
            id="plantilla-global"
            type="checkbox"
            class="plantilla-form__check"
            bind:checked={fEsGlobal}
          />
          Disponible para todas las dependencias
        </label>
      </div>

      {#if !fEsGlobal}
        <fieldset class="plantilla-alcance__deps">
          <legend class="plantilla-alcance__legend">Dependencias que la usan</legend>
          {#if dependencias.length === 0}
            <p class="plantilla-alcance__vacio" role="status">
              No se pudo cargar el organigrama, así que no se puede restringir ahora mismo. Vuelva a
              intentarlo o deje la plantilla disponible para todas.
            </p>
          {:else}
            {#each dependencias as dep (dep.codigo)}
              <label class="plantilla-alcance__dep" for={`dep-${dep.codigo}`}>
                <input
                  id={`dep-${dep.codigo}`}
                  type="checkbox"
                  class="plantilla-form__check"
                  checked={fDependencias.includes(dep.codigo)}
                  on:change={() => alternarDependencia(dep.codigo)}
                />
                {dep.nombre} <span class="plantilla-alcance__cod">({dep.codigo})</span>
              </label>
            {/each}
          {/if}
          {#if fDependencias.length === 0 && dependencias.length > 0}
            <p class="plantilla-alcance__aviso" role="status">
              Sin ninguna dependencia marcada, la plantilla deja de ofrecerse a todo el mundo. Es
              válido para retirarla de circulación sin borrar los documentos ya producidos con ella.
            </p>
          {/if}
        </fieldset>
      {/if}
    </div>
  {/if}

  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (editandoAlcance = null)}>Cancelar</Button>
    <Button icon="fas fa-save" loading={alcanceLoading} on:click={guardarAlcance}>
      Guardar alcance
    </Button>
  </svelte:fragment>
</Modal>

<Modal
  open={modalDelete}
  title="Eliminar plantilla"
  size="sm"
  on:close={() => (modalDelete = false)}
>
  <p class="modal-confirm__text">
    ¿Está seguro de que desea eliminar la plantilla
    <strong>{deletingPlantilla?.nombre}</strong>? Esta acción no puede deshacerse.
  </p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalDelete = false)}>Cancelar</Button>
    <Button variant="danger" icon="fas fa-trash" loading={actionLoading} on:click={deletePlantilla}>
      Eliminar
    </Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  @use "$styles/_tokens" as t;

  .plantillas-page {
    padding: var(--op-space-5);
    max-width: 1000px;

    &__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: var(--op-space-3);
      margin-bottom: var(--op-space-5);
    }

    &__title {
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
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
      margin-bottom: var(--op-space-4);
    }
  }

  // ---------------------------------------------------------------------------
  // Tabla
  // ---------------------------------------------------------------------------
  .plantillas-table-wrap {
    overflow-x: auto;
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: -2px;
    }
  }

  .plantillas-table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--op-font-sm);

    th,
    td {
      padding: var(--op-space-3) var(--op-space-4);
      text-align: left;
      border-bottom: 1px solid var(--op-border);
    }

    th {
      background: var(--op-bg);
      font-weight: var(--op-weight-medium);
      color: var(--op-text-secondary);
      font-size: var(--op-font-xs);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    tbody tr:last-child td {
      border-bottom: none;
    }

    &__row:hover td {
      background: var(--op-primary-light);

      // Contraste AA (revisión UX lote 2): `--op-text-secondary` sobre
      // `--op-primary-light` mide ~4.0:1, bajo AA — se sube a `--op-text`
      // (que sí cumple) solo mientras el puntero está sobre la fila.
      &.plantillas-table__date {
        color: var(--op-text);
      }
    }

    &__nombre {
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
    }

    &__date {
      color: var(--op-text-secondary);
      white-space: nowrap;
    }

    &__actions {
      width: 100px;
    }
  }

  // ---------------------------------------------------------------------------
  // Formulario de plantilla
  // ---------------------------------------------------------------------------
  .plantilla-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__check-row {
      display: flex;
      align-items: center;
    }

    &__check-label {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-text);
      cursor: pointer;
      user-select: none;
    }

    &__check {
      width: 16px;
      height: 16px;
      accent-color: var(--op-primary);
      cursor: pointer;
    }

    &__editor {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
    }

    &__editor-error {
      font-size: var(--op-font-xs);
      color: var(--op-error);
      margin: 0;
    }
  }

  // ---------------------------------------------------------------------------
  // Modal de confirmación
  // ---------------------------------------------------------------------------
  .modal-confirm__text {
    font-size: var(--op-font-sm);
    color: var(--op-text);
    line-height: var(--op-line-height);
    margin: 0;
  }
  // ---------------------------------------------------------------------------
  // Alcance por dependencia (E23 Inc.5, RF-PRO-06)
  // ---------------------------------------------------------------------------
  .plantilla-alcance {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__intro {
      font-size: var(--op-font-sm);
      line-height: var(--op-line-height);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__deps {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-2);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      padding: var(--op-space-3);
      // La lista puede ser larga: se desplaza dentro de su caja en vez de
      // estirar el modal hasta que el botón de guardar queda fuera de pantalla.
      max-height: 18rem;
      overflow-y: auto;
    }

    &__legend {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      padding: 0 var(--op-space-2);
    }

    &__dep {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-text);
      cursor: pointer;
    }

    &__cod {
      color: var(--op-text-secondary);
      font-family: var(--op-font-family-mono);
      font-size: var(--op-font-xs);
    }

    &__aviso,
    &__vacio {
      font-size: var(--op-font-sm);
      line-height: var(--op-line-height);
      background: var(--op-warning-bg);
      color: var(--op-warning-fg);
      padding: var(--op-space-2) var(--op-space-3);
      border-radius: var(--op-radius);
      margin: 0;
    }
  }

  .plantillas-table__alcance {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    white-space: nowrap;
  }

  // ── Vocabulario de marcadores (E23, RF-PRO-01) ─────────────────────────────
  .plantilla-campos {
    margin-top: var(--op-space-3);
    border-top: 1px solid var(--op-border);
    padding-top: var(--op-space-2);

    &__toggle {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      background: none;
      border: none;
      padding: var(--op-space-1) 0;
      cursor: pointer;
      color: var(--op-primary);
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
    }

    &__lista {
      margin-top: var(--op-space-2);
      max-height: 16rem;
      overflow-y: auto;
    }

    &__nota {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      line-height: var(--op-line-height);
      margin-bottom: var(--op-space-3);
    }

    &__grupo-titulo {
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-bold);
      color: var(--op-text-secondary);
      text-transform: uppercase;
      margin-bottom: var(--op-space-1);
    }

    &__items {
      list-style: none;
      margin: 0 0 var(--op-space-3);
      padding: 0;
    }

    &__item {
      // Prosa y etiqueta en una fila: el texto envuelve dentro de su columna en
      // vez de trocearse por palabras, que es lo que hace un flex sin `min-width`.
      display: flex;
      align-items: baseline;
      gap: var(--op-space-2);
      width: 100%;
      text-align: left;
      background: none;
      border: none;
      border-radius: var(--op-radius);
      padding: var(--op-space-1) var(--op-space-2);
      cursor: pointer;
      color: var(--op-text);

      &:hover,
      &:focus-visible {
        background: var(--op-primary-light);
      }

      code {
        font-family: var(--op-font-family-mono);
        font-size: var(--op-font-xs);
        white-space: nowrap;
        color: var(--op-primary);
      }
    }

    &__desc {
      flex: 1 1 auto;
      min-width: 0;
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      line-height: var(--op-line-height);
    }

    &__tag {
      flex: 0 0 auto;
      font-size: var(--op-font-xs);
      background: var(--op-warning-bg);
      color: var(--op-warning-fg);
      border-radius: var(--op-radius);
      padding: 0 var(--op-space-1);
    }
  }
</style>
