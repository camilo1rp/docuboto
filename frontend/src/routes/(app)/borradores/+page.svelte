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
  /borradores — Gestor de borradores de Salida/Interno (T-12c).

  Criterio de hecho: crear un borrador desde una plantilla, aprobarlo y radicarlo
  genera el radicado real — su tracking_number se muestra en pantalla.

  Acciones del flujo:
    borrador → [editar] → [aprobar] → aprobado → [radicar] → radicado (DocumentResponse)

  RBAC (espejo del backend, D-11):
    - Ver lista, crear, editar, aprobar, radicar → PERM_RADI
    - Acciones de escritura deshabilitadas si el usuario no tiene PERM_RADI.
    El backend re-valida en cada llamada; la UI solo filtra la visibilidad.

  D-05 — access_token NUNCA en cliente:
    Todas las llamadas van a /borradores/api[/id/accion] que leen locals.accessToken
    en el servidor. El token nunca llega al JS del cliente.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import Drawer from "$lib/components/ui/Drawer.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import RichEditor from "$lib/components/editor/RichEditor.svelte";
  import RagSuggestButton from "$lib/components/features/rag/RagSuggestButton.svelte";
  import { appendGeneratedHtml } from "$lib/utils/ragInsert.js";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** @type {import('./$types').PageData} */
  export let data;

  // ---------------------------------------------------------------------------
  // Estado de la lista
  // ---------------------------------------------------------------------------

  /** @type {import('$lib/types/api.js').BorradorResponse[]} */
  let borradores = data.borradores ?? [];
  let plantillas = data.plantillas ?? [];
  let loading = false;
  let estadoFilter = data.initialEstado ?? "";

  const ESTADOS = [
    { value: "", label: "Todos" },
    { value: "borrador", label: "Borrador" },
    { value: "aprobado", label: "Aprobado" },
    { value: "radicado", label: "Radicado" },
  ];

  // ---------------------------------------------------------------------------
  // Drawer de creación / edición
  // ---------------------------------------------------------------------------
  let drawerOpen = false;
  let drawerMode = /** @type {'create'|'edit'} */ ("create");
  /** @type {import('$lib/types/api.js').BorradorResponse|null} */
  let editingBorrador = null;
  let actionLoading = false;

  // Campos del formulario
  let fDocType = "S";
  let fSubject = "";
  let fDestDept = "";
  let fCuerpo = "";
  let fPlantillaId = "";
  let fSubjectError = "";
  /** Radicado de Entrada que se responde: fuente del contexto de combinación. */
  let fRadicadoPadreId = "";
  /** Campos del vocabulario que quedaron sin valor al combinar (RF-PRO-01). */
  let combinacionFaltantes = [];
  /**
   * Campos que no tenían dato cuando se combinó la plantilla, tal y como los
   * guarda el borrador (E23, RF-PRO-01).
   *
   * La lista se calculaba al combinar, se enseñaba una vez en el cajón de
   * creación y se perdía. Como el cuerpo se guarda YA combinado —los huecos son
   * cadenas vacías, no marcadores—, nada podía volver a deducirla: al reabrir el
   * borrador no había aviso ninguno. El aviso existía justo en el instante en
   * que el hueco todavía no importaba, y desaparecía antes de importar.
   */
  let faltantesGuardados = [];
  let combinacionError = "";

  /**
   * Carga en el editor el cuerpo de la plantilla con los campos YA COMBINADOS
   * (E23, RF-PRO-02). Solo activo en modo creación; en edición el cambio de
   * plantilla NO pisa el cuerpo existente del borrador (ALTO #1 — pérdida de
   * datos).
   *
   * La combinación la hace el SERVIDOR, no esta función. Antes se sembraba el
   * editor con `found.cuerpo_html` —la plantilla literal, con sus `*NOM_R*`
   * dentro— y se enviaba tal cual al crear: como el `cuerpo` llegaba lleno, la
   * rama que combina en el backend no se ejecutaba nunca. El marcador acababa
   * impreso en el oficio que recibe el ciudadano.
   *
   * Si la combinación falla, se cae a la plantilla cruda y se avisa: es
   * preferible poder redactar a quedarse con el editor vacío, pero el
   * funcionario tiene que saber que los campos NO están resueltos.
   */
  async function handlePlantillaChange() {
    if (drawerMode !== "create" || !fPlantillaId) return;
    const found = plantillas.find((p) => p.id === fPlantillaId);
    combinacionFaltantes = [];
    combinacionError = "";
    try {
      const res = await fetch(`/borradores/api/plantillas/${fPlantillaId}/combinar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ radicado_padre_id: fRadicadoPadreId || null }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(mensajeError(data, "No se pudo combinar la plantilla."));
      fCuerpo = data.cuerpo_html ?? data.cuerpo ?? "";
      combinacionFaltantes = Array.isArray(data.faltantes) ? data.faltantes : [];
    } catch (err) {
      fCuerpo = found?.cuerpo_html ?? found?.cuerpo ?? "";
      combinacionError =
        "No se pudieron combinar los campos: el texto se cargó SIN resolver. " +
        "Revise los marcadores antes de radicar.";
    }
  }

  // ---------------------------------------------------------------------------
  // Modal de confirmación de radicación
  // ---------------------------------------------------------------------------
  let modalRadicar = false;
  /** @type {string|null} — tracking_number tras radicación exitosa */
  let trackingNumberResult = null;
  let modalRadicadoOpen = false;

  // ---------------------------------------------------------------------------
  // Breadcrumbs
  // ---------------------------------------------------------------------------
  onMount(() => {
    setBreadcrumbs([{ label: "Borradores" }]);
  });

  // ---------------------------------------------------------------------------
  // Filtrar lista
  // ---------------------------------------------------------------------------
  async function applyFilter(estado) {
    estadoFilter = estado;
    loading = true;
    try {
      const params = new URLSearchParams();
      if (estado) params.set("estado", estado);
      const res = await fetch(`/borradores/api?${params}`);
      if (res.ok) {
        borradores = await res.json();
      } else {
        const err = await res.json().catch(() => ({}));
        notifyError(mensajeError(err, "Error al cargar borradores."));
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      loading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Abrir drawer para crear
  // ---------------------------------------------------------------------------
  function openCreate() {
    drawerMode = "create";
    editingBorrador = null;
    fDocType = "S";
    fSubject = "";
    fDestDept = "";
    fCuerpo = "";
    fPlantillaId = "";
    fSubjectError = "";
    faltantesGuardados = [];
    drawerOpen = true;
  }

  // ---------------------------------------------------------------------------
  // Abrir drawer para editar
  // ---------------------------------------------------------------------------
  function openEdit(borrador) {
    drawerMode = "edit";
    editingBorrador = borrador;
    fDocType = borrador.doc_type ?? "S";
    fSubject = borrador.subject ?? "";
    fDestDept = borrador.dest_dept ?? "";
    // BAJO #6: sembrar el editor desde el cuerpo YA saneado (cuerpo_html).
    fCuerpo = borrador.cuerpo_html ?? borrador.cuerpo ?? "";
    fPlantillaId = borrador.plantilla_id ?? "";
    fSubjectError = "";
    faltantesGuardados = Array.isArray(borrador.faltantes) ? borrador.faltantes : [];
    drawerOpen = true;
    cargarCircuito(borrador.id);
  }

  function closeDrawer() {
    drawerOpen = false;
  }

  // ---------------------------------------------------------------------------
  // Guardar (crear o editar)
  // ---------------------------------------------------------------------------
  async function guardar() {
    fSubjectError = "";
    if (!fSubject.trim()) {
      fSubjectError = "El asunto es obligatorio.";
      return;
    }

    actionLoading = true;
    try {
      let res;
      if (drawerMode === "create") {
        res = await fetch("/borradores/api", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            doc_type: fDocType,
            subject: fSubject.trim(),
            dest_dept: fDestDept.trim() || null,
            cuerpo: fCuerpo || null,
            plantilla_id: fPlantillaId || null,
            radicado_padre_id: fRadicadoPadreId || null,
          }),
        });
      } else {
        res = await fetch(`/borradores/api/${editingBorrador.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            subject: fSubject.trim(),
            dest_dept: fDestDept.trim() || null,
            cuerpo: fCuerpo || null,
          }),
        });
      }

      const data = await res.json();
      if (!res.ok) {
        notifyError(mensajeError(data, "Error al guardar el borrador."));
        return;
      }

      if (drawerMode === "create") {
        borradores = [data, ...borradores];
        notifySuccess("Borrador creado.");
      } else {
        borradores = borradores.map((b) => (b.id === data.id ? data : b));
        editingBorrador = data;
        notifySuccess("Borrador actualizado.");
      }
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Aprobar
  // ---------------------------------------------------------------------------
  async function aprobar() {
    if (!editingBorrador) return;
    actionLoading = true;
    try {
      const res = await fetch(`/borradores/api/${editingBorrador.id}/aprobar`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) {
        notifyError(mensajeError(data, "No se pudo aprobar el borrador."));
        return;
      }
      borradores = borradores.map((b) => (b.id === data.id ? data : b));
      editingBorrador = data;
      notifySuccess("Borrador aprobado.");
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Circuito de aprobación y firma (E23 Inc.4, RF-PRO-10)
  //
  // El proxy `/borradores/api/[id]/circuito` existía y NINGUNA pantalla lo
  // llamaba: el backend sabía componer cadenas de visto bueno y firma, y desde
  // el navegador no había forma de crear ninguna. `/firmas` era una bandeja que
  // firmaba lo que otro creaba, y no había otro.
  //
  // Lo que el circuito modela es el trámite real: varias personas dan su visto
  // bueno sobre el mismo documento y UNA sola firma —la que produce el
  // documento definitivo—. Si el texto cambia, las aprobaciones anteriores no
  // se arrastran.
  // ---------------------------------------------------------------------------
  let modalCircuito = false;
  /** @type {any[]} Usuarios elegibles, cargados al abrir el modal. */
  let firmantesDisponibles = [];
  /** @type {{firmante_id: string, rol: string}[]} Turnos EN ORDEN. */
  let turnos = [];
  let circuitoError = "";
  let circuitoLoading = false;
  /** @type {any|null} Circuito ya abierto sobre este borrador, si lo hay. */
  let circuitoActual = null;

  async function abrirModalCircuito() {
    if (!editingBorrador) return;
    modalCircuito = true;
    circuitoError = "";
    turnos = [];
    try {
      const res = await fetch("/borradores/api/firmantes");
      const data = await res.json().catch(() => []);
      if (!res.ok) throw new Error(mensajeError(data, "No se pudo cargar el listado."));
      firmantesDisponibles = Array.isArray(data) ? data : (data?.items ?? []);
    } catch (err) {
      firmantesDisponibles = [];
      circuitoError = err instanceof Error ? err.message : String(err);
    }
  }

  /** Estado del circuito ya abierto, para enseñarlo en el cajón. */
  async function cargarCircuito(borradorId) {
    circuitoActual = null;
    try {
      const res = await fetch(`/borradores/api/${borradorId}/circuito`);
      if (!res.ok) return;
      const data = await res.json().catch(() => null);
      const lista = Array.isArray(data) ? data : (data?.items ?? []);
      // Solo interesa el circuito vivo: uno cerrado o invalidado no gobierna
      // nada, y enseñarlo como si mandara confundiría sobre qué falta.
      circuitoActual = lista.find((c) => c.estado === "pendiente") ?? null;
    } catch {
      circuitoActual = null;
    }
  }

  function agregarTurno() {
    turnos = [...turnos, { firmante_id: "", rol: "visto_bueno" }];
  }

  function quitarTurno(idx) {
    turnos = turnos.filter((_, i) => i !== idx);
  }

  $: turnosCompletos = turnos.length > 0 && turnos.every((t) => t.firmante_id);
  $: hayFirma = turnos.some((t) => t.rol === "firma");
  // Un mismo usuario dos veces en la cadena firmaría su propio visto bueno.
  $: hayRepetidos =
    new Set(turnos.map((t) => t.firmante_id).filter(Boolean)).size !==
    turnos.filter((t) => t.firmante_id).length;

  async function crearCircuito() {
    if (!editingBorrador) return;
    circuitoLoading = true;
    circuitoError = "";
    try {
      const res = await fetch(`/borradores/api/${editingBorrador.id}/circuito`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ titulo: editingBorrador.subject, turnos }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(mensajeError(data, "No se pudo abrir el circuito."));
      modalCircuito = false;
      notifySuccess("Circuito abierto. Cada turno lo verá en su bandeja de firmas.");
      await cargarCircuito(editingBorrador.id);
    } catch (err) {
      circuitoError = err instanceof Error ? err.message : String(err);
    } finally {
      circuitoLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Radicar (con confirmación)
  // ---------------------------------------------------------------------------
  function pedirConfirmacionRadicar() {
    modalRadicar = true;
  }

  async function confirmarRadicar() {
    if (!editingBorrador) return;
    modalRadicar = false;
    actionLoading = true;
    try {
      const res = await fetch(`/borradores/api/${editingBorrador.id}/radicar`, {
        method: "POST",
      });
      const data = await res.json();
      if (!res.ok) {
        notifyError(mensajeError(data, "No se pudo radicar el borrador."));
        return;
      }
      // data es DocumentResponse con tracking_number
      trackingNumberResult = data.tracking_number ?? null;
      // MEDIO #4: persistir tracking_number y radicado_id en la fila de la lista
      // para que sigan visibles tras cerrar el modal.
      // Cast explícito para que `estado` mantenga la unión literal, no `string`.
      /** @type {import('$lib/types/api.js').BorradorResponse} */
      const updatedBorrador = {
        ...editingBorrador,
        estado: /** @type {'borrador'|'aprobado'|'radicado'} */ ("radicado"),
        radicado_id: data.id ?? editingBorrador.radicado_id,
        _tracking_number: data.tracking_number ?? null,
      };
      borradores = borradores.map((b) => (b.id === editingBorrador.id ? updatedBorrador : b));
      editingBorrador = updatedBorrador;
      modalRadicadoOpen = true;
      drawerOpen = false;
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Helpers de presentación
  // ---------------------------------------------------------------------------
  const ESTADO_CHIP = {
    borrador: "neutral",
    aprobado: "warning",
    radicado: "success",
  };

  const ESTADO_LABEL = {
    borrador: "Borrador",
    aprobado: "Aprobado",
    radicado: "Radicado",
  };

  const DOCTYPE_LABEL = { S: "Salida", I: "Interno" };

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

  // ¿El borrador activo se puede editar? Solo si estado === 'borrador'
  $: canEdit = editingBorrador?.estado === "borrador";
  $: canAprobar = editingBorrador?.estado === "borrador";
  $: canRadicar = editingBorrador?.estado === "aprobado";

  // ── Previsualización (E23, RF-PRO-05) ──────────────────────────────────────
  let previsualizando = false;
  let previsualizacionFaltantes = [];
  let previsualizacionError = "";

  /**
   * Abre el documento tal como saldrá, sin persistir nada.
   *
   * El PDF se abre en una pestaña por `blob:`; los campos que no resolvieron
   * llegan en `X-Faltantes` y se enseñan aparte. Sin esa lista, la persona
   * vería un hueco en el documento sin saber que es un hueco — y el oficio
   * saldría así al ciudadano.
   */
  async function previsualizar() {
    if (!editingBorrador) return;
    previsualizando = true;
    previsualizacionError = "";
    previsualizacionFaltantes = [];
    try {
      const res = await fetch(`/borradores/api/${editingBorrador.id}/previsualizar`, {
        method: "POST",
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(mensajeError(data, "No se pudo generar la previsualización."));
      }
      const crudo = res.headers.get("X-Faltantes") ?? "";
      previsualizacionFaltantes = crudo ? crudo.split(",").filter(Boolean) : [];
      const url = URL.createObjectURL(await res.blob());
      window.open(url, "_blank", "noopener");
      // Se revoca con retraso: revocarlo en el acto deja la pestaña recién
      // abierta sin nada que mostrar.
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      previsualizacionError = err instanceof Error ? err.message : String(err);
    } finally {
      previsualizando = false;
    }
  }
  $: hasPerm = $permissions.includes("PERM_RADI");

  // Opciones de plantillas para el select
  $: plantillaOptions = [
    { value: "", label: "Sin plantilla" },
    ...plantillas.map((p) => ({ value: p.id, label: p.nombre })),
  ];
</script>

<svelte:head>
  <title>Borradores — OrpycaMCP</title>
</svelte:head>

<!-- =========================================================================
     Página principal
     ========================================================================= -->
<div class="borradores-page">
  <!-- Cabecera -->
  <div class="borradores-page__header">
    <div class="borradores-page__title-row">
      <h1 class="borradores-page__title">Borradores</h1>
      {#if hasPerm}
        <Button icon="fas fa-plus" on:click={openCreate}>Nuevo borrador</Button>
      {/if}
    </div>

    <!-- Filtros de estado -->
    <div class="borradores-page__filters" role="group" aria-label="Filtrar por estado">
      {#each ESTADOS as e (e.value)}
        <button
          type="button"
          class="borradores-page__filter-btn"
          class:borradores-page__filter-btn--active={estadoFilter === e.value}
          aria-pressed={estadoFilter === e.value}
          on:click={() => applyFilter(e.value)}
        >
          {e.label}
        </button>
      {/each}
    </div>
  </div>

  <!-- Error de carga -->
  {#if data.loadError}
    <div class="borradores-page__error" role="alert">
      <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
      {data.loadError}
    </div>
  {/if}

  <!-- Estado de carga -->
  {#if loading}
    <div class="borradores-page__loader">
      <Loader />
    </div>
  {:else if borradores.length === 0}
    <EmptyState
      icon="fa-file-alt"
      title="No hay borradores"
      description={estadoFilter
        ? `No hay borradores en estado "${ESTADO_LABEL[estadoFilter] ?? estadoFilter}".`
        : "Crea el primer borrador con el botón «Nuevo borrador»."}
    />
  {:else}
    <!-- Tabla de borradores -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <div class="borradores-table-wrap" role="region" aria-label="Lista de borradores" tabindex="0">
      <table class="borradores-table" aria-label="Borradores">
        <thead>
          <tr>
            <th scope="col">Asunto</th>
            <th scope="col">Tipo</th>
            <th scope="col">Destino</th>
            <th scope="col">Estado</th>
            <th scope="col">N.° Radicado</th>
            <th scope="col">Creado</th>
            <th scope="col"><span class="sr-only">Acciones</span></th>
          </tr>
        </thead>
        <tbody>
          {#each borradores as borrador (borrador.id)}
            <tr class="borradores-table__row">
              <td class="borradores-table__subject">{borrador.subject}</td>
              <td>{DOCTYPE_LABEL[borrador.doc_type] ?? borrador.doc_type}</td>
              <td>{borrador.dest_dept ?? "—"}</td>
              <td>
                <StatusChip
                  status={ESTADO_CHIP[borrador.estado] ?? "neutral"}
                  label={ESTADO_LABEL[borrador.estado] ?? borrador.estado}
                  dot
                />
              </td>
              <td class="borradores-table__tracking">
                {#if borrador._tracking_number}
                  <span class="borradores-table__tracking-num">{borrador._tracking_number}</span>
                {:else}
                  <span class="borradores-table__tracking-none">—</span>
                {/if}
              </td>
              <td class="borradores-table__date">{formatDate(borrador.created_at)}</td>
              <td class="borradores-table__actions">
                <Button
                  variant="ghost"
                  size="sm"
                  icon="fas fa-pencil-alt"
                  aria-label="Editar borrador {borrador.subject}"
                  on:click={() => openEdit(borrador)}
                >
                  Abrir
                </Button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<!-- =========================================================================
     Drawer: crear / editar borrador
     ========================================================================= -->
<Drawer
  open={drawerOpen}
  title={drawerMode === "create" ? "Nuevo borrador" : "Editar borrador"}
  width="600px"
  on:close={closeDrawer}
>
  <form
    class="borrador-form"
    on:submit|preventDefault={guardar}
    aria-label={drawerMode === "create" ? "Formulario de nuevo borrador" : "Formulario de edición"}
    novalidate
  >
    <!-- Estado visible (solo en edición) -->
    {#if editingBorrador}
      <div class="borrador-form__estado">
        <span class="borrador-form__estado-label">Estado:</span>
        <StatusChip
          status={ESTADO_CHIP[editingBorrador.estado] ?? "neutral"}
          label={ESTADO_LABEL[editingBorrador.estado] ?? editingBorrador.estado}
          dot
        />
      </div>
    {/if}

    <!-- Tipo de documento -->
    <FormField
      id="borrador-doctype"
      label="Tipo de documento"
      type="select"
      bind:value={fDocType}
      required
      disabled={!hasPerm || (editingBorrador !== null && !canEdit)}
      options={[
        { value: "S", label: "Salida" },
        { value: "I", label: "Interno" },
      ]}
    />

    <!-- Asunto -->
    <FormField
      id="borrador-subject"
      label="Asunto"
      type="text"
      bind:value={fSubject}
      placeholder="Descripción breve del documento"
      required
      disabled={!hasPerm || (editingBorrador !== null && !canEdit)}
      error={fSubjectError}
    />

    <!-- Destino -->
    <FormField
      id="borrador-destdept"
      label="Dependencia destino"
      type="text"
      bind:value={fDestDept}
      placeholder="Nombre o código de la dependencia receptora"
      disabled={!hasPerm || (editingBorrador !== null && !canEdit)}
    />

    <!-- Selector de plantilla (solo en creación) -->
    {#if drawerMode === "create" && plantillas.length > 0}
      <FormField
        id="borrador-plantilla"
        label="Plantilla de contenido"
        type="select"
        bind:value={fPlantillaId}
        disabled={!hasPerm}
        options={plantillaOptions}
        hint="Al elegir una plantilla, sus campos se combinan en el servidor y el resultado se carga en el editor."
        on:change={handlePlantillaChange}
      />

      {#if combinacionError}
        <p class="borrador-form__aviso borrador-form__aviso--error" role="alert">
          {combinacionError}
        </p>
      {:else if combinacionFaltantes.length > 0}
        <!-- Un campo vacío en silencio es el defecto que esta lista evita: el
             oficio saldría con un hueco donde debía ir un dato, y nadie lo
             sabría hasta que llegara al ciudadano. -->
        <p class="borrador-form__aviso" role="status">
          Sin datos para {combinacionFaltantes.length === 1 ? "el campo" : "los campos"}:
          <strong>{combinacionFaltantes.join(", ")}</strong>. Complételos en el texto antes de
          radicar.
        </p>
      {/if}
    {/if}

    <!-- F6: ayuda de redacción con RAG — opcional, degrada sin romper crear/editar
         el borrador si el servicio de conocimiento no responde. -->
    <RagSuggestButton
      endpoint="/borradores/api/rag/suggest"
      query={fSubject}
      disabled={!hasPerm || (editingBorrador !== null && !canEdit)}
      on:insert={(e) => (fCuerpo = appendGeneratedHtml(fCuerpo, e.detail.html))}
    />

    <!-- Aviso persistente de campos sin dato (E23, RF-PRO-01).
         Se enseña SIEMPRE que el borrador los tenga guardados, no solo en el
         momento de elegir la plantilla: quien abre el borrador al día siguiente
         —o quien no es quien lo creó— vería un espacio en blanco en mitad del
         oficio sin nada que le diga que ahí faltaba un dato.
         Es un hecho sobre la combinación, no sobre el texto de ahora, y por eso
         no desaparece al editar: borrarlo a la primera pulsación quitaría el
         aviso justo cuando alguien empieza a tocar el documento sin haber
         rellenado necesariamente los huecos. -->
    {#if drawerMode === "edit" && faltantesGuardados.length > 0}
      <p class="borrador-form__aviso" role="status">
        Al combinar la plantilla no había datos para
        {faltantesGuardados.length === 1 ? "el campo" : "los campos"}:
        <strong>{faltantesGuardados.join(", ")}</strong>. Compruebe que el texto está completo antes
        de radicar.
      </p>
    {/if}

    <!-- Editor de cuerpo -->
    <RichEditor
      id="borrador-cuerpo"
      label="Contenido del documento"
      protegerMarcadores
      bind:value={fCuerpo}
      placeholder="Redacte el cuerpo del documento aquí…"
      disabled={!hasPerm || (editingBorrador !== null && !canEdit)}
    />

    <!-- Botón guardar -->
    {#if hasPerm && (editingBorrador === null || canEdit)}
      <div class="borrador-form__save-row">
        <Button type="submit" icon="fas fa-save" loading={actionLoading}>
          {drawerMode === "create" ? "Crear borrador" : "Guardar cambios"}
        </Button>
      </div>
    {/if}
  </form>

  <!-- Acciones de flujo (solo en edición) -->
  {#if editingBorrador && hasPerm}
    <div class="borrador-form__flow-actions">
      <hr class="borrador-form__divider" aria-hidden="true" />
      <p class="borrador-form__flow-title">Acciones de flujo</p>

      <!-- Previsualizar: ver ANTES de gastar el número (E23, RF-PRO-05) -->
      {#if editingBorrador}
        <Button
          variant="secondary"
          icon="fas fa-file-pdf"
          loading={previsualizando}
          on:click={previsualizar}
        >
          Previsualizar documento
        </Button>
        {#if previsualizacionFaltantes.length > 0}
          <p class="borrador-form__aviso" role="status">
            El documento sale sin {previsualizacionFaltantes.length === 1
              ? "el campo"
              : "los campos"}:
            <strong>{previsualizacionFaltantes.join(", ")}</strong>.
          </p>
        {/if}
        {#if previsualizacionError}
          <p class="borrador-form__aviso borrador-form__aviso--error" role="alert">
            {previsualizacionError}
          </p>
        {/if}
      {/if}

      <!-- Aprobar -->
      {#if canAprobar}
        <Button
          variant="secondary"
          icon="fas fa-check-circle"
          loading={actionLoading}
          on:click={aprobar}
        >
          Aprobar borrador
        </Button>
      {/if}

      <!-- Circuito de aprobación y firma (E23 Inc.4, RF-PRO-10).
           Va antes de Radicar porque ese es el orden del trámite: varios dan su
           visto bueno, uno firma, y solo entonces sale el documento. -->
      {#if canRadicar}
        {#if circuitoActual}
          <div class="borrador-circuito" role="status">
            <p class="borrador-circuito__titulo">
              Circuito abierto — turno {(circuitoActual.turno_actual ?? 0) + 1} de
              {circuitoActual.turnos?.length ?? 0}
            </p>
            <ol class="borrador-circuito__turnos">
              {#each circuitoActual.turnos ?? [] as turno, idx (turno.firmante_id + idx)}
                <li
                  class="borrador-circuito__turno"
                  class:borrador-circuito__turno--hecho={turno.firmado_at}
                  class:borrador-circuito__turno--actual={idx === circuitoActual.turno_actual}
                >
                  <span class="borrador-circuito__rol">
                    {turno.rol === "firma" ? "Firma" : "Visto bueno"}
                  </span>
                  <span class="borrador-circuito__quien">
                    {turno.firmante_nombre ?? turno.firmante_id}
                  </span>
                  {#if turno.firmado_at}
                    <i class="fas fa-check" aria-label="cumplido"></i>
                  {/if}
                </li>
              {/each}
            </ol>
            <p class="borrador-circuito__nota">
              Si el texto del documento cambia, las aprobaciones recogidas dejan de valer y hay que
              abrir el circuito de nuevo.
            </p>
          </div>
        {:else}
          <Button variant="secondary" icon="fas fa-user-check" on:click={abrirModalCircuito}>
            Abrir circuito de firmas
          </Button>
        {/if}
      {/if}

      <!-- Radicar (solo si aprobado) -->
      {#if canRadicar}
        <Button
          icon="fas fa-paper-plane"
          loading={actionLoading}
          on:click={pedirConfirmacionRadicar}
        >
          Radicar
        </Button>
      {/if}

      <!-- Radicado — estado final, tracking_number visible (MEDIO #4) -->
      {#if editingBorrador.estado === "radicado"}
        <div class="borrador-form__flow-done" role="status">
          <i class="fas fa-check-double" aria-hidden="true"></i>
          <span>Este borrador ya fue radicado.</span>
          {#if editingBorrador._tracking_number}
            <span>
              N.° <strong>{editingBorrador._tracking_number}</strong>
            </span>
          {:else if editingBorrador.radicado_id}
            <span class="sr-only">ID de radicado: {editingBorrador.radicado_id}</span>
          {/if}
        </div>
      {/if}
    </div>
  {/if}
</Drawer>

<!-- =========================================================================
     Modal: confirmación de radicación
     ========================================================================= -->
<Modal
  open={modalRadicar}
  title="Confirmar radicación"
  size="sm"
  on:close={() => (modalRadicar = false)}
>
  <p class="modal-confirm__text">
    ¿Confirma la radicación de este borrador? Esta acción generará un radicado real con número de
    control inmutable y no puede deshacerse.
  </p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalRadicar = false)}>Cancelar</Button>
    <Button icon="fas fa-paper-plane" loading={actionLoading} on:click={confirmarRadicar}>
      Sí, radicar
    </Button>
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: resultado de radicación exitosa
     ========================================================================= -->
<Modal
  open={modalRadicadoOpen}
  title="Radicado generado"
  size="sm"
  on:close={() => (modalRadicadoOpen = false)}
>
  <div class="modal-success">
    <i class="fas fa-check-circle modal-success__icon" aria-hidden="true"></i>
    <p class="modal-success__text">El borrador fue radicado exitosamente.</p>
    {#if trackingNumberResult}
      <p class="modal-success__tracking" aria-live="polite">
        Número de radicado:
        <strong>{trackingNumberResult}</strong>
      </p>
    {/if}
  </div>
  <svelte:fragment slot="footer">
    <Button on:click={() => (modalRadicadoOpen = false)}>Cerrar</Button>
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: componer el circuito de aprobación y firma (E23 Inc.4, RF-PRO-10)
     ========================================================================= -->
<Modal
  open={modalCircuito}
  title="Circuito de firmas"
  size="md"
  on:close={() => (modalCircuito = false)}
>
  <p class="circuito-modal__intro">
    Los turnos se cumplen <strong>en orden</strong>. Quien da visto bueno aprueba el documento;
    quien firma produce el documento definitivo, y va el último.
  </p>

  {#if firmantesDisponibles.length === 0 && !circuitoError}
    <p class="circuito-modal__vacio">Cargando personas…</p>
  {/if}

  {#each turnos as turno, idx (idx)}
    <div class="circuito-modal__turno">
      <span class="circuito-modal__orden" aria-hidden="true">{idx + 1}</span>

      <label class="circuito-modal__campo">
        <span class="circuito-modal__label">Persona</span>
        <select bind:value={turno.firmante_id} class="circuito-modal__select">
          <option value="">Elija una persona…</option>
          {#each firmantesDisponibles as u (u.id ?? u.user_id)}
            <option value={u.id ?? u.user_id}>{u.nombre ?? u.name ?? u.username}</option>
          {/each}
        </select>
      </label>

      <label class="circuito-modal__campo">
        <span class="circuito-modal__label">Actúa como</span>
        <select bind:value={turno.rol} class="circuito-modal__select">
          <option value="visto_bueno">Visto bueno</option>
          <option value="firma">Firma</option>
        </select>
      </label>

      <button
        type="button"
        class="circuito-modal__quitar"
        aria-label={`Quitar el turno ${idx + 1}`}
        on:click={() => quitarTurno(idx)}
      >
        <i class="fas fa-times" aria-hidden="true"></i>
      </button>
    </div>
  {/each}

  <button type="button" class="circuito-modal__agregar" on:click={agregarTurno}>
    <i class="fas fa-plus" aria-hidden="true"></i> Añadir turno
  </button>

  <!-- Las tres condiciones se dicen ANTES de pulsar, no como error después: el
       backend las rechaza igual, pero enterarse por un 400 obliga a rehacer el
       formulario entero para averiguar qué faltaba. -->
  {#if turnos.length > 0 && !hayFirma}
    <p class="borrador-form__aviso" role="status">
      Falta el turno de firma: sin él no hay documento definitivo y el borrador no podría salir.
    </p>
  {/if}
  {#if hayRepetidos}
    <p class="borrador-form__aviso borrador-form__aviso--error" role="alert">
      Una misma persona aparece dos veces: acabaría dando el visto bueno a su propia firma.
    </p>
  {/if}
  {#if circuitoError}
    <p class="borrador-form__aviso borrador-form__aviso--error" role="alert">{circuitoError}</p>
  {/if}

  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalCircuito = false)}>Cancelar</Button>
    <Button
      icon="fas fa-user-check"
      loading={circuitoLoading}
      disabled={!turnosCompletos || !hayFirma || hayRepetidos}
      on:click={crearCircuito}
    >
      Abrir circuito
    </Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  @use "$styles/_tokens" as t;

  .borradores-page {
    padding: var(--op-space-5);
    max-width: 1100px;

    &__header {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-3);
      margin-bottom: var(--op-space-5);
    }

    &__title-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: var(--op-space-3);
    }

    &__title {
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0;
    }

    &__filters {
      display: flex;
      flex-wrap: wrap;
      gap: var(--op-space-2);
    }

    &__filter-btn {
      padding: var(--op-space-1) var(--op-space-3);
      font-size: var(--op-font-sm);
      font-family: var(--op-font-family);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-full);
      background: var(--op-surface);
      color: var(--op-text-secondary);
      cursor: pointer;
      transition:
        background var(--op-transition),
        color var(--op-transition),
        border-color var(--op-transition);

      &:hover {
        background: var(--op-primary-light);
        color: var(--op-primary-dark);
        border-color: var(--op-primary-light);
      }

      &--active {
        background: var(--op-primary-dark);
        color: var(--op-surface);
        border-color: var(--op-primary-dark);

        &:hover {
          background: var(--op-brand-700);
          border-color: var(--op-brand-700);
        }
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
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

    &__loader {
      display: flex;
      justify-content: center;
      padding: var(--op-space-7);
    }
  }

  // ---------------------------------------------------------------------------
  // Tabla de borradores
  // ---------------------------------------------------------------------------
  .borradores-table-wrap {
    overflow-x: auto;
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: -2px;
    }
  }

  .borradores-table {
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
      &.borradores-table__date,
      .borradores-table__tracking-none {
        color: var(--op-text);
      }
    }

    &__subject {
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
      max-width: 280px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    &__date {
      color: var(--op-text-secondary);
      white-space: nowrap;
    }

    &__tracking {
      white-space: nowrap;
    }

    &__tracking-num {
      font-weight: var(--op-weight-medium);
      color: var(--op-primary-dark);
      font-size: var(--op-font-xs);
      background: var(--op-primary-light);
      padding: 2px var(--op-space-2);
      border-radius: var(--op-radius-sm);
    }

    &__tracking-none {
      color: var(--op-text-secondary);
    }

    &__actions {
      width: 80px;
    }
  }

  // ---------------------------------------------------------------------------
  // Formulario del drawer
  // ---------------------------------------------------------------------------
  .borrador-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__estado {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-2) var(--op-space-3);
      background: var(--op-bg);
      border-radius: var(--op-radius);
      font-size: var(--op-font-sm);
    }

    &__estado-label {
      color: var(--op-text-secondary);
    }

    &__save-row {
      display: flex;
      justify-content: flex-end;
    }
  }

  // Acciones de flujo debajo del formulario
  .borrador-form__flow-actions {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
    padding-top: var(--op-space-2);
  }

  .borrador-form__divider {
    border: none;
    border-top: 1px solid var(--op-border);
    margin: 0;
  }

  .borrador-form__flow-title {
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    color: var(--op-text-secondary);
    margin: 0;
  }

  .borrador-form__flow-done {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--op-space-2);
    font-size: var(--op-font-sm);
    color: var(--op-success-fg);
    background: var(--op-success-bg);
    padding: var(--op-space-2) var(--op-space-3);
    border-radius: var(--op-radius);
    margin: 0;

    strong {
      font-weight: var(--op-weight-bold);
    }
  }

  // ---------------------------------------------------------------------------
  // Modales
  // ---------------------------------------------------------------------------
  .modal-confirm__text {
    font-size: var(--op-font-sm);
    color: var(--op-text);
    line-height: var(--op-line-height);
    margin: 0;
  }

  .modal-success {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--op-space-3);
    text-align: center;
    padding: var(--op-space-3) 0;

    &__icon {
      font-size: var(--op-font-2xl);
      color: var(--op-success);
    }

    &__text {
      font-size: var(--op-font-sm);
      color: var(--op-text);
      margin: 0;
    }

    &__tracking {
      font-size: var(--op-font-md);
      color: var(--op-text);
      background: var(--op-primary-light);
      padding: var(--op-space-2) var(--op-space-4);
      border-radius: var(--op-radius);
      margin: 0;

      strong {
        color: var(--op-primary-dark);
        font-weight: var(--op-weight-bold);
      }
    }
  }

  // ── Circuito de aprobación y firma (E23 Inc.4, RF-PRO-10) ──────────────────
  .borrador-circuito {
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-3);
    background: var(--op-surface);

    &__titulo {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-bold);
      margin-bottom: var(--op-space-2);
    }

    &__turnos {
      list-style: none;
      margin: 0 0 var(--op-space-2);
      padding: 0;
      counter-reset: turno;
    }

    &__turno {
      display: flex;
      align-items: baseline;
      gap: var(--op-space-2);
      padding: var(--op-space-1) 0;
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);

      &--hecho {
        color: var(--op-success-fg);
      }

      &--actual {
        color: var(--op-text);
        font-weight: var(--op-weight-medium);
      }
    }

    &__rol {
      flex: 0 0 auto;
      font-size: var(--op-font-xs);
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }

    &__quien {
      // Sin `min-width: 0` el nombre se trocea por palabras dentro del flex.
      flex: 1 1 auto;
      min-width: 0;
    }

    &__nota {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      line-height: var(--op-line-height);
    }
  }

  .circuito-modal {
    &__intro,
    &__vacio {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      line-height: var(--op-line-height);
      margin-bottom: var(--op-space-3);
    }

    &__turno {
      display: flex;
      align-items: flex-end;
      gap: var(--op-space-2);
      margin-bottom: var(--op-space-2);
    }

    &__orden {
      flex: 0 0 auto;
      width: 1.75rem;
      height: 1.75rem;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: var(--op-radius-full);
      background: var(--op-primary-light);
      color: var(--op-primary-dark);
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-bold);
    }

    &__campo {
      flex: 1 1 0;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
    }

    &__label {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    &__select {
      width: 100%;
      padding: var(--op-space-2);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      background: var(--op-bg);
      color: var(--op-text);
      font-family: var(--op-font-family);
      font-size: var(--op-font-sm);
    }

    &__quitar {
      flex: 0 0 auto;
      background: none;
      border: none;
      cursor: pointer;
      color: var(--op-text-secondary);
      padding: var(--op-space-2);

      &:hover,
      &:focus-visible {
        color: var(--op-error-fg);
      }
    }

    &__agregar {
      display: inline-flex;
      align-items: center;
      gap: var(--op-space-2);
      background: none;
      border: 1px dashed var(--op-border);
      border-radius: var(--op-radius);
      padding: var(--op-space-2) var(--op-space-3);
      cursor: pointer;
      color: var(--op-primary);
      font-family: var(--op-font-family);
      font-size: var(--op-font-sm);
      margin-bottom: var(--op-space-3);
    }
  }
</style>
