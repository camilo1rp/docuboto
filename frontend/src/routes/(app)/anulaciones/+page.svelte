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
  /anulaciones — Cola de trabajo del aprobador de anulaciones (Fase 0, cierre
  de desfase API↔UI).

  `/bandeja` ya permite SOLICITAR la anulación de un radicado (PERM_SOL_ANULAR),
  pero nadie podía resolverla: el backend expone aprobar/rechazar
  (PERM_PANU_CODI) sin ninguna pantalla. El aprobador es un rol DISTINTO del
  solicitante, así que esta es su cola propia.

  DEGRADACIÓN DECLARADA (ver +page.server.js para el detalle de la
  investigación): document-service NO expone un listado de anulaciones
  pendientes — ni un filtro de estado en `GET /documents`, ni un campo que
  delate una anulación en trámite. Esta pantalla NO inventa ese endpoint.
  En su lugar, localiza radicados por texto (reutilizando la búsqueda E09,
  `/anulaciones/api/buscar`) y consulta el estado real de cada uno por
  radicado (`/anulaciones/api/[id]`, GET /documents/{id}/anulacion). El
  banner de la cabecera lo explica siempre visible — no se disimula.

  RBAC (espejo del backend, D-05):
    - Decidir (aprobar/rechazar) → PERM_PANU_CODI. Sin este permiso, la
      pantalla completa se sustituye por un EmptyState: es la cola del
      aprobador, no una vista de consulta general.
    - El backend re-valida en cada operación; esta UI solo filtra visibilidad.

  D-05 — access_token NUNCA en cliente:
    Todas las llamadas van a /anulaciones/api/* que leen locals.accessToken
    en el servidor. El token nunca llega al JS del cliente.
-->
<script>
  import { onMount, tick } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import { permissions, userId } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import DataTable from "$lib/components/ui/DataTable.svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import { ANULACION_ESTADO_CHIP, ANULACION_ESTADO_LABEL } from "$lib/utils/anulacion.js";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** @type {import('./$types').PageData} */
  export let data;

  // ---------------------------------------------------------------------------
  // Cola de solicitudes pendientes — lo que esta pantalla NO podía mostrar
  // ---------------------------------------------------------------------------
  // Hasta que existió `GET /api/v1/anulaciones`, esta pantalla solo sabía
  // consultar radicado por radicado y lo declaraba en un banner permanente: el
  // aprobador únicamente se enteraba de que tenía trabajo si alguien se lo
  // decía por otro canal. La cola se carga en SSR y se refresca tras cada
  // decisión, porque una fila ya resuelta que sigue en pantalla hasta recargar
  // a mano es lo que vuelve inútil una cola de trabajo.
  let cola = data.cola?.items ?? [];
  let colaTotal = data.cola?.total ?? 0;
  $: colaError = data.colaError ?? null;
  let colaCargando = false;

  async function recargarCola() {
    colaCargando = true;
    try {
      const res = await fetch("/anulaciones/api/cola");
      const cuerpo = await res.json().catch(() => null);
      if (res.ok && cuerpo) {
        cola = cuerpo.items ?? [];
        colaTotal = cuerpo.total ?? cola.length;
      }
    } finally {
      colaCargando = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Permisos (espejo del RBAC del backend)
  // ---------------------------------------------------------------------------
  // `sub` del token frente a quien figura como solicitante. Si por lo que sea
  // no hay identidad en sesión, NO se asume que somos otro: se prefiere ocultar
  // el botón y que el acto lo haga alguien con sesión válida.
  // `solicitado_por` es la identidad INTERNA del tenant, no el `sub` de
  // Keycloak: comparar contra `sub` daba siempre falso y este aviso no se
  // mostraba nunca (el backend sí bloqueaba, con un 409 que la persona se
  // encontraba de golpe tras rellenar el motivo).
  // Sin identidad en sesión NO se oculta «Aprobar»: el backend rechaza la
  // aprobación propia con 409 y su motivo, así que ocultarlo «por si acaso»
  // convertiría un caso raro (sesión sin `user_id`) en un bloqueo total de una
  // acción legítima. El aviso se muestra cuando SE SABE que es de uno mismo.
  $: esMiPropiaSolicitud =
    anulacion?.estado === "solicitada" && Boolean($userId) && anulacion?.solicitado_por === $userId;

  $: canDecidir = $permissions.includes("PERM_PANU_CODI");

  const DOC_TYPE_LABELS = /** @type {Record<string,string>} */ ({
    E: "Entrada",
    S: "Salida",
    I: "Interno",
  });

  // Motivo de la decisión: acto con efectos jurídicos (Ley 594/2000 art. 28);
  // el proxy rechaza (422) lo que exceda este límite (M9, revisión UX Fase 0).
  const MOTIVO_MAX_LENGTH = 2000;

  // ---------------------------------------------------------------------------
  // Buscador de radicados (sustituto de la cola real, ver banner + cabecera)
  // ---------------------------------------------------------------------------
  let qInput = "";
  let searchResults = [];
  let searchTotal = 0;
  let searchLoading = false;
  let searched = false;
  let searchError = "";
  // C3 (revisión UX): un fallo de RED/gateway (503, etc.) caía en el mismo
  // `catch` que "sin resultados" — `searchResults = []` con `searched = true`
  // pintaba el EmptyState "Ningún radicado coincide con la búsqueda". Un
  // aprobador que busca un radicado del que le avisaron por teléfono leería
  // eso como "la solicitud no existe" y cerraría el caso, exactamente lo que
  // el banner de esta pantalla dice combatir. `searchFailed` distingue el
  // caso "consulté y no hay nada" del caso "no pude consultar".
  let searchFailed = false;

  /**
   * Estado de la anulación de cada fila visible, por `row.id` (A5b, revisión
   * UX Fase 0). El buscador solo devuelve el radicado; para saber si HAY algo
   * que resolver hay que consultar `/anulaciones/api/{id}` una vez por fila
   * (mismo endpoint que "Ver anulación" usaba una por una). Se dispara en
   * paralelo tras cada búsqueda — nunca bloquea el render de la tabla: cada
   * fila arranca en `loading: true` y se va resolviendo por su cuenta.
   * @type {Record<string, {loading: boolean, estado: string|null, notFound: boolean, error: boolean}>}
   */
  let rowAnulacionStatus = {};

  /** @param {any} row */
  async function fetchRowStatus(row) {
    try {
      const res = await fetch(`/anulaciones/api/${encodeURIComponent(row.id)}`);
      // 404 es el caso NORMAL "este radicado no tiene solicitud de
      // anulación" (mismo criterio no-read-up del backend) — no un error.
      if (res.status === 404) return { estado: null, notFound: true, error: false };
      if (!res.ok) return { estado: null, notFound: false, error: true };
      const data = await res.json();
      return { estado: data?.estado ?? null, notFound: false, error: false };
    } catch {
      return { estado: null, notFound: false, error: true };
    }
  }

  /** @param {any[]} rows Filas de la búsqueda actual (SearchHit[]) */
  async function cargarEstadosAnulacion(rows) {
    /** @type {typeof rowAnulacionStatus} */
    const next = {};
    for (const row of rows) {
      next[row.id] = { loading: true, estado: null, notFound: false, error: false };
    }
    rowAnulacionStatus = next;

    // Cada tarea resuelve su propia entrada del mapa en cuanto termina (no
    // espera a las demás): la columna "Anulación" se va rellenando fila por
    // fila. Ninguna tarea propaga el rechazo — un radicado que falle no debe
    // tumbar el estado de los demás — así Promise.allSettled solo sirve para
    // saber cuándo terminó el lote completo.
    const tasks = rows.map((row) =>
      fetchRowStatus(row).then((value) => {
        rowAnulacionStatus = { ...rowAnulacionStatus, [row.id]: { loading: false, ...value } };
      }),
    );

    await Promise.allSettled(tasks);
  }

  /**
   * Menor (revisión UX): si la consulta de estado de UNA fila falla, el chip
   * ya lo decía ("No se pudo consultar") pero el botón "Ver anulación" solo
   * aparecía con `estado` truthy — la fila quedaba sin forma de reintentar
   * ni de llegar al detalle. Reintenta solo esa fila (no toda la búsqueda).
   * @param {any} row
   */
  async function retryRowStatus(row) {
    rowAnulacionStatus = {
      ...rowAnulacionStatus,
      [row.id]: { loading: true, estado: null, notFound: false, error: false },
    };
    const value = await fetchRowStatus(row);
    rowAnulacionStatus = { ...rowAnulacionStatus, [row.id]: { loading: false, ...value } };
  }

  async function buscar() {
    const q = qInput.trim();
    searchError = "";
    if (q.length < 2) {
      searchError = "Escribe al menos 2 caracteres.";
      return;
    }
    searchLoading = true;
    searched = true;
    searchFailed = false;
    try {
      const res = await fetch(`/anulaciones/api/buscar?q=${encodeURIComponent(q)}`);
      const result = await res.json();
      if (!res.ok) throw result;
      searchResults = result.results ?? [];
      searchTotal = result.total ?? searchResults.length;
      if (searchResults.length > 0) {
        // No se espera esta promesa: la tabla ya se renderiza con las filas,
        // la columna "Anulación" se rellena por su cuenta.
        cargarEstadosAnulacion(searchResults);
      }
    } catch (/** @type {any} */ err) {
      searchResults = [];
      searchTotal = 0;
      // C3: la búsqueda no pudo resolverse — esto NO es "sin resultados".
      searchFailed = true;
      searchError = mensajeError(err, "No se pudo consultar el servidor de búsqueda.");
      notifyError(mensajeError(err, "No se pudo buscar radicados."));
    } finally {
      searchLoading = false;
    }
  }

  const TABLE_COLUMNS = [
    { key: "tracking_number", label: "Radicado" },
    { key: "doc_type", label: "Tipo" },
    { key: "subject", label: "Asunto" },
    { key: "_anulacion", label: "Anulación", width: "200px" },
    { key: "_actions", label: "Acciones", width: "160px" },
  ];

  // ---------------------------------------------------------------------------
  // Drawer: estado de anulación de un radicado
  // ---------------------------------------------------------------------------
  let drawerOpen = false;
  let detailLoading = false;
  /** @type {any|null} */
  let selectedRow = null;
  /** @type {import('$lib/types/api.js').AnulacionResponse|null} */
  let anulacion = null;
  /** @type {HTMLElement | undefined} Título de la página: destino de foco estable. */
  let tituloEl;

  /** @param {any} row Fila del resultado de búsqueda (SearchHit) */
  async function verAnulacion(row) {
    selectedRow = row;
    anulacion = null;
    drawerOpen = true;
    detailLoading = true;
    try {
      const res = await fetch(`/anulaciones/api/${encodeURIComponent(row.id)}`);
      if (res.status === 404) {
        drawerOpen = false;
        notifyError(`El radicado ${row.tracking_number} no tiene ninguna anulación registrada.`);
        return;
      }
      const result = await res.json();
      if (!res.ok) throw result;
      anulacion = result;
    } catch (/** @type {any} */ err) {
      drawerOpen = false;
      notifyError(mensajeError(err, "No se pudo consultar el estado de la anulación."));
    } finally {
      detailLoading = false;
    }
  }

  /**
   * Abre el detalle desde una fila de la COLA.
   *
   * No se pasa la fila tal cual: `verAnulacion` espera `id` = identificador del
   * RADICADO, y en una fila de cola `id` es el de la SOLICITUD de anulación.
   * Pasarla directa usaría el identificador equivocado sin que nada fallara a
   * la vista — el proxy devolvería 404 y la pantalla diría «no tiene ninguna
   * anulación registrada» sobre una solicitud que sí existe.
   *
   * @param {any} pendiente fila de `GET /api/v1/anulaciones`
   */
  function abrirDesdeCola(pendiente) {
    return verAnulacion({
      id: pendiente.radicado_id,
      tracking_number: pendiente.tracking_number,
      subject: pendiente.subject,
    });
  }

  function closeDrawer() {
    drawerOpen = false;
    selectedRow = null;
    anulacion = null;
  }

  // ---------------------------------------------------------------------------
  // Modal de decisión: motivo OBLIGATORIO (acto con efectos jurídicos, D-05)
  // ---------------------------------------------------------------------------
  let modalDecision = false;
  /** @type {"aprobar"|"rechazar"} */
  let decisionAccion = "aprobar";
  let fMotivo = "";
  let fMotivoError = "";
  let decisionLoading = false;

  // M9 (revisión UX Fase 0): el proxy ahora rechaza (422) un motivo por
  // encima de MOTIVO_MAX_LENGTH en vez de recortarlo en silencio; en el
  // cliente se recorta a la vez que se teclea para que nunca llegue a
  // dispararse ese 422 en el uso normal, y el contador (hint de FormField,
  // arriba) avisa antes de llegar al límite.
  $: if (fMotivo.length > MOTIVO_MAX_LENGTH) {
    fMotivo = fMotivo.slice(0, MOTIVO_MAX_LENGTH);
  }

  function abrirDecision(/** @type {"aprobar"|"rechazar"} */ accion) {
    decisionAccion = accion;
    fMotivo = "";
    fMotivoError = "";
    // Cerrar drawer ANTES de abrir modal para no apilar focus traps (A11y)
    drawerOpen = false;
    modalDecision = true;
  }

  function cerrarDecision() {
    modalDecision = false;
    tick().then(() => tituloEl?.focus());
  }

  // M8 (revisión UX Fase 0): el borde/`aria-invalid` rojo no debe sobrevivir
  // a la corrección — se limpia en cuanto el aprobador vuelve a escribir.
  function onMotivoInput() {
    if (fMotivoError) fMotivoError = "";
  }

  async function confirmarDecision() {
    fMotivoError = "";
    if (!fMotivo.trim()) {
      fMotivoError = "El motivo es obligatorio.";
      // M7: el foco se quedaba en "Confirmar" tras el error; llevarlo al
      // campo que falló para no obligar a un lector de pantalla a buscarlo.
      tick().then(() => {
        document.getElementById("decision-motivo")?.focus();
      });
      return;
    }
    if (!selectedRow) return;

    decisionLoading = true;
    try {
      const res = await fetch(
        `/anulaciones/api/${encodeURIComponent(selectedRow.id)}/${decisionAccion}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ motivo: fMotivo.trim() }),
        },
      );
      const result = await res.json();
      if (!res.ok) throw result;

      notifySuccess(
        decisionAccion === "aprobar"
          ? `Anulación aprobada. El radicado ${selectedRow.tracking_number} quedó anulado.`
          : `Anulación de ${selectedRow.tracking_number} rechazada.`,
      );

      // La fila ya tenía su estado resuelto (columna "Anulación", A5b):
      // se actualiza in-place con la decisión, sin esperar a que el
      // aprobador vuelva a abrir "Ver anulación" para enterarse.
      if (result?.estado) {
        rowAnulacionStatus = {
          ...rowAnulacionStatus,
          [selectedRow.id]: {
            loading: false,
            estado: result.estado,
            notFound: false,
            error: false,
          },
        };
      }

      // A2: pasar por `cerrarDecision()` (no solo `modalDecision = false`)
      // para que el foco vuelva al título de la página. El `previousFocus`
      // del Modal apuntaba a un botón del Drawer ya desmontado, así que sin
      // esto el foco caía en <body> justo tras un acto irreversible.
      cerrarDecision();
      selectedRow = null;
      anulacion = null;

      // La cola se refresca tras decidir: la solicitud recién resuelta ya no
      // está pendiente y dejarla en pantalla hasta recargar a mano es lo que
      // vuelve inútil una cola de trabajo. Es un refresco, no una faceta
      // crítica — si falla, la fila sigue ahí y el siguiente «Actualizar» la
      // quita; no se convierte un acto que SÍ ocurrió en un error a la vista.
      recargarCola();
    } catch (/** @type {any} */ err) {
      notifyError(
        mensajeError(
          err,
          `No se pudo ${decisionAccion === "aprobar" ? "aprobar" : "rechazar"} la anulación.`,
        ),
      );
    } finally {
      decisionLoading = false;
    }
  }

  function formatDateTime(/** @type {string} */ iso) {
    if (!iso) return "—";
    return formatearFechaHora(iso);
  }

  onMount(() => {
    setBreadcrumbs([{ label: "Resolver anulaciones" }]);
  });
</script>

<svelte:head>
  <title>Resolver anulaciones — OrpycaMCP</title>
</svelte:head>

<div class="anulaciones-page">
  <header class="anulaciones-page__header">
    <h1 class="anulaciones-page__title" bind:this={tituloEl} tabindex="-1">Resolver anulaciones</h1>
  </header>

  {#if !canDecidir}
    <EmptyState
      icon="fas fa-lock"
      title="Sin permiso de aprobación de anulaciones"
      description="Esta pantalla resuelve solicitudes de anulación de radicados. Tu usuario no tiene asignado el permiso de aprobación de anulaciones. Solicítalo a un administrador de la entidad."
    />
  {:else}
    <!-- Cola de trabajo: lo pendiente de decidir, lo más antiguo primero.
         Sustituye al banner que declaraba que el backend no exponía listado. -->
    <section class="anulaciones-cola" aria-labelledby="cola-titulo">
      <div class="anulaciones-cola__head">
        <h2 class="anulaciones-cola__title" id="cola-titulo">
          Pendientes de decidir
          {#if !colaError}<span class="anulaciones-cola__count">{colaTotal}</span>{/if}
        </h2>
        <Button
          variant="ghost"
          size="sm"
          icon="fas fa-rotate"
          loading={colaCargando}
          on:click={recargarCola}
        >
          Actualizar
        </Button>
      </div>

      {#if colaError}
        <!-- «No se pudo consultar» NO es «no hay nada»: son estados distintos y
             confundirlos afirma que la entidad no tiene anulaciones pendientes. -->
        <EmptyState
          icon={colaError.status === 403 ? "fas fa-lock" : "fas fa-triangle-exclamation"}
          title={colaError.status === 403
            ? "Sin permiso para ver la cola"
            : "No se pudo consultar la cola"}
          description={colaError.status === 403
            ? "Tu usuario no tiene el permiso de aprobación de anulaciones."
            : `${colaError.detail} Esto no significa que no haya solicitudes pendientes.`}
        />
      {:else if cola.length === 0}
        <EmptyState
          icon="fas fa-check"
          title="No hay anulaciones pendientes"
          description="Cuando alguien solicite anular un radicado, aparecerá aquí."
        />
      {:else}
        <ul class="anulaciones-cola__list" role="list">
          {#each cola as p (p.id)}
            <li class="anulaciones-cola__item">
              <div class="anulaciones-cola__main">
                <code class="anulaciones-cola__tracking">{p.tracking_number}</code>
                <span class="anulaciones-cola__subject">{p.subject ?? "—"}</span>
              </div>
              <div class="anulaciones-cola__meta">
                <StatusChip status="warning" label="Pendiente" dot />
                <span class="anulaciones-cola__causal">{p.causal}</span>
                <time datetime={p.solicitado_at}>{formatDateTime(p.solicitado_at)}</time>
              </div>
              <Button
                variant="secondary"
                size="sm"
                icon="fas fa-gavel"
                on:click={() => abrirDesdeCola(p)}
              >
                Resolver
              </Button>
            </li>
          {/each}
        </ul>
      {/if}
    </section>

    <!-- Buscador -->
    <form class="anulaciones-search" on:submit|preventDefault={buscar} aria-label="Buscar radicado">
      <FormField
        id="anulaciones-q"
        label="Número de radicado o texto del asunto"
        type="text"
        bind:value={qInput}
        placeholder="Ej. 2026-ICETEX-E-000123 o «contrato de arrendamiento»"
        error={searchError}
      />
      <Button type="submit" icon="fas fa-search" loading={searchLoading}>Buscar</Button>
    </form>

    <!-- Resultados -->
    {#if searchLoading || searchResults.length > 0}
      <div class="anulaciones-table-wrap">
        <DataTable
          columns={TABLE_COLUMNS}
          rows={searchResults}
          totalCount={searchTotal}
          disableNav
          loading={searchLoading}
          caption="Radicados encontrados"
        >
          <svelte:fragment slot="row" let:row>
            <td class="op-datatable__td anulaciones-table__tracking">{row.tracking_number}</td>
            <td class="op-datatable__td">{DOC_TYPE_LABELS[row.doc_type] ?? row.doc_type}</td>
            <td class="op-datatable__td anulaciones-table__subject">{row.subject}</td>
            <td class="op-datatable__td">
              {#if rowAnulacionStatus[row.id]?.loading}
                <Loader type="spinner" size="sm" label="Consultando estado de la anulación…" />
              {:else if rowAnulacionStatus[row.id]?.notFound}
                <StatusChip status="neutral" label="Sin solicitud" dot />
              {:else if rowAnulacionStatus[row.id]?.error}
                <StatusChip status="error" label="No se pudo consultar" dot />
              {:else if rowAnulacionStatus[row.id]?.estado}
                <StatusChip
                  status={ANULACION_ESTADO_CHIP[rowAnulacionStatus[row.id].estado] ?? "neutral"}
                  label={ANULACION_ESTADO_LABEL[rowAnulacionStatus[row.id].estado] ??
                    rowAnulacionStatus[row.id].estado}
                  dot
                />
              {:else}
                <StatusChip status="neutral" label="Sin solicitud" dot />
              {/if}
            </td>
            <td class="op-datatable__td anulaciones-table__actions">
              {#if rowAnulacionStatus[row.id]?.estado}
                <Button
                  variant="ghost"
                  size="sm"
                  icon="fas fa-ban"
                  on:click={() => verAnulacion(row)}
                  aria-label="Ver anulación del radicado {row.tracking_number}"
                >
                  Ver anulación
                </Button>
              {:else if rowAnulacionStatus[row.id]?.error}
                <!-- Menor (revisión UX): antes esta fila quedaba SIN botón —
                  ni reintento ni acceso al detalle — cuando la consulta de su
                  estado fallaba. -->
                <Button
                  variant="ghost"
                  size="sm"
                  icon="fas fa-rotate-right"
                  on:click={() => retryRowStatus(row)}
                  aria-label="Reintentar consultar la anulación del radicado {row.tracking_number}"
                >
                  Reintentar
                </Button>
              {/if}
            </td>
          </svelte:fragment>
        </DataTable>
      </div>
      {#if searchTotal > searchResults.length}
        <!-- Menor (revisión UX): el total ya lo dice la propia tabla ("1–20
          de {searchTotal}", vía `totalCount={searchTotal}` arriba) — antes
          esta pantalla lo repetía con OTRO número ("1–20 de 20" de la tabla
          vs "primeros 20 de {searchTotal}" de este párrafo), una
          contradicción. Este aviso ya no repite la cifra: solo orienta. -->
        <p class="anulaciones-table__truncated">
          Hay más resultados de los que se muestran aquí; refina la búsqueda para encontrar el
          radicado que buscas.
        </p>
      {/if}
    {:else if searchFailed && !searchLoading}
      <!-- C3: un fallo de red/gateway NUNCA se presenta como "no hay nada
        que resolver" — estado persistente y distinto del EmptyState de "sin
        resultados", con reintento explícito. -->
      <EmptyState
        icon="fas fa-triangle-exclamation"
        title="No se pudo consultar el servidor de búsqueda"
        description={searchError ||
          "Hubo un problema al buscar radicados. Esto no significa que no existan — vuelve a intentarlo."}
      >
        <Button variant="primary" on:click={buscar}>Reintentar</Button>
      </EmptyState>
    {:else if searched && !searchLoading}
      <EmptyState
        icon="fas fa-search"
        title="Ningún radicado coincide con la búsqueda"
        description="Esto no significa que no haya anulaciones pendientes: esta pantalla solo puede consultar radicado por radicado. Prueba con el número completo de radicado."
      />
    {:else}
      <EmptyState
        icon="fas fa-search"
        title="Busca un radicado para empezar"
        description="Escribe un número de radicado o parte del asunto y presiona «Buscar»."
      />
    {/if}
  {/if}
</div>

<!-- =========================================================================
     Drawer: estado de la anulación
     ========================================================================= -->
<Drawer
  open={drawerOpen}
  title={selectedRow ? `Anulación — ${selectedRow.tracking_number}` : "Anulación"}
  width="440px"
  on:close={closeDrawer}
>
  {#if detailLoading}
    <Loader type="skeleton" lines={6} label="Cargando estado de la anulación…" />
  {:else if anulacion}
    <dl class="anulacion-detail">
      <div class="anulacion-detail__row">
        <dt>Estado</dt>
        <dd>
          <StatusChip
            status={ANULACION_ESTADO_CHIP[anulacion.estado] ?? "neutral"}
            label={ANULACION_ESTADO_LABEL[anulacion.estado] ?? anulacion.estado}
            dot
          />
        </dd>
      </div>
      <div class="anulacion-detail__row">
        <dt>Causal</dt>
        <dd>{anulacion.causal}</dd>
      </div>
      {#if anulacion.motivo}
        <div class="anulacion-detail__row">
          <dt>Motivo de la solicitud</dt>
          <dd>{anulacion.motivo}</dd>
        </div>
      {/if}
      <div class="anulacion-detail__row">
        <dt>Solicitada</dt>
        <dd>
          <time datetime={anulacion.solicitado_at}>{formatDateTime(anulacion.solicitado_at)}</time>
        </dd>
      </div>
      {#if anulacion.decidido_at}
        <div class="anulacion-detail__row">
          <dt>Decidida</dt>
          <dd>
            <time datetime={anulacion.decidido_at}>{formatDateTime(anulacion.decidido_at)}</time>
          </dd>
        </div>
      {/if}
      {#if anulacion.observacion}
        <div class="anulacion-detail__row">
          <dt>Observación de la decisión</dt>
          <dd>{anulacion.observacion}</dd>
        </div>
      {/if}
    </dl>

    <!-- Ver el radicado antes de decidir sobre él.
         El detalle enseñaba la causal, el motivo alegado y las fechas: todo lo
         que dijo QUIEN PIDIÓ la anulación, y nada del documento sobre el que
         se decide. Anular no se deshace, así que resolver viendo solo la
         versión del solicitante es la peor forma de decidir barata.
         El deep-link ya existía y solo lo usaba /busqueda; `id` de la fila es
         el UUID del RADICADO (no el de la solicitud — ver `abrirDesdeCola`). -->
    {#if selectedRow?.id}
      <a
        class="anulacion-detail__ver"
        href="/bandeja?radicado={encodeURIComponent(selectedRow.id)}"
      >
        <i class="fas fa-up-right-from-square" aria-hidden="true"></i>
        Ver el radicado {selectedRow.tracking_number ?? ""}
      </a>
    {/if}
  {/if}

  <svelte:fragment slot="footer">
    {#if anulacion?.estado === "solicitada"}
      <!--
        A1 (revisión UX Fase 0): el color estaba invertido — aprobar es el
        acto DESTRUCTIVO e irreversible (el radicado pasa a `anulado`);
        rechazar es el conservador (el radicado no cambia). Aprobar lleva
        ahora la variante de alerta, no el botón que "tranquiliza".
      -->
      <Button
        variant="secondary"
        size="sm"
        disabled={decisionLoading}
        on:click={() => abrirDecision("rechazar")}
      >
        <i class="fas fa-times-circle" aria-hidden="true"></i>
        Rechazar
      </Button>
      <!--
        Quien pidió la anulación no la aprueba: la decide otra persona (el
        backend responde 409 `aprobacion_del_propio_solicitante`). El límite se
        DECLARA aquí en vez de dejar que se descubra fallando, y se nombra la
        vía que sí queda abierta —rechazarla, que es desistir— porque un «no
        puede» sin alternativa deja el trámite parado sin decir cómo seguir.
      -->
      {#if esMiPropiaSolicitud}
        <p class="anulacion-dual" role="note">
          <i class="fas fa-user-shield" aria-hidden="true"></i>
          <span>
            Usted solicitó esta anulación, así que <strong>debe aprobarla otra persona</strong> con permiso
            para resolver anulaciones. Si desea desistir, puede rechazarla usted mismo.
          </span>
        </p>
      {:else}
        <Button
          variant="danger"
          size="sm"
          disabled={decisionLoading}
          on:click={() => abrirDecision("aprobar")}
        >
          <i class="fas fa-ban" aria-hidden="true"></i>
          Aprobar
        </Button>
      {/if}
    {:else}
      <Button variant="ghost" size="sm" on:click={closeDrawer}>Cerrar</Button>
    {/if}
  </svelte:fragment>
</Drawer>

<!-- =========================================================================
     Modal: decisión (motivo obligatorio) — acto con efectos jurídicos y
     traza de auditoría (Ley 594/2000 art. 28); no admite motivo vacío.
     ========================================================================= -->
<Modal
  open={modalDecision}
  title={decisionAccion === "aprobar" ? "Aprobar anulación" : "Rechazar anulación"}
  size="sm"
  on:close={cerrarDecision}
>
  <div class="decision-form">
    <p class="decision-form__info">
      <i class="fas fa-exclamation-circle" aria-hidden="true"></i>
      {#if decisionAccion === "aprobar"}
        Al aprobar, el radicado <strong>{selectedRow?.tracking_number}</strong> pasará a
        <strong>anulado</strong>. Esta acción no se puede deshacer.
      {:else}
        Al rechazar, la solicitud de anulación de
        <strong>{selectedRow?.tracking_number}</strong> queda cerrada; el radicado no cambia de estado.
      {/if}
    </p>
    <FormField
      id="decision-motivo"
      label="Motivo de la decisión"
      type="textarea"
      bind:value={fMotivo}
      required
      error={fMotivoError}
      hint={!fMotivoError ? `${fMotivo.length}/${MOTIVO_MAX_LENGTH} caracteres` : ""}
      placeholder="Explique brevemente el motivo de la decisión…"
      rows={4}
      on:input={onMotivoInput}
    />
  </div>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={cerrarDecision}>Cancelar</Button>
    <!--
      A1: mismo criterio que en el Drawer — aprobar es el acto irreversible
      (radicado → anulado) y lleva la variante de alerta; rechazar es el
      conservador.
    -->
    <Button
      variant={decisionAccion === "aprobar" ? "danger" : "secondary"}
      icon={decisionAccion === "aprobar" ? "fas fa-ban" : "fas fa-times-circle"}
      loading={decisionLoading}
      on:click={confirmarDecision}
    >
      {decisionAccion === "aprobar" ? "Confirmar aprobación" : "Confirmar rechazo"}
    </Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  @use "$styles/_tokens" as t;

  .anulaciones-page {
    padding: var(--op-space-5);
    max-width: 1000px;

    &__header {
      margin-bottom: var(--op-space-4);
    }

    &__title {
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0;
    }
  }

  .anulaciones-cola {
    margin-bottom: var(--op-space-5);

    &__head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--op-space-3);
      margin-bottom: var(--op-space-3);
    }

    &__title {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      margin: 0;
      font-size: var(--op-font-lg);
      color: var(--op-text);
    }

    &__count {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 1.75rem;
      padding: 0 var(--op-space-2);
      border-radius: 999px;
      background: var(--op-primary-light);
      color: var(--op-primary-dark);
      font-size: var(--op-font-sm);
      font-weight: 600;
    }

    &__list {
      list-style: none;
      margin: 0;
      padding: 0;
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      overflow: hidden;
    }

    &__item {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
      padding: var(--op-space-3) var(--op-space-4);
      background: var(--op-surface, #fff);

      & + & {
        border-top: 1px solid var(--op-border);
      }
    }

    &__main {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
      flex: 1 1 auto;
    }

    &__tracking {
      /* Sin deshacer nada: el estilo base de `code` (custom-bulma) ya es plano
         y monoespaciado. Aquí solo se ajusta el tamaño y el tono, que son
         decisiones de ESTA lista. */
      align-self: flex-start;
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
    }

    &__subject {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--op-text);
    }

    &__meta {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      flex: 0 0 auto;
    }

    /* En pantalla estrecha la fila se apila: los metadatos y el botón no caben
       al lado del asunto sin recortarlo hasta hacerlo inútil. */
    @media (max-width: 768px) {
      &__item {
        flex-wrap: wrap;
      }

      &__meta {
        width: 100%;
      }
    }
  }

  .anulaciones-search {
    display: flex;
    // M17 (revisión UX Fase 0): con `flex-end` el botón "Buscar" se
    // descolgaba cuando `searchError` hacía crecer el <p> de error dentro
    // del campo. `flex-start` + un margen que compensa la altura del label
    // mantiene el botón alineado con el CONTROL (el input), no con el
    // fondo del campo completo.
    align-items: flex-start;
    gap: var(--op-space-3);
    margin-bottom: var(--op-space-4);
    flex-wrap: wrap;

    :global(.op-field) {
      flex: 1;
      min-width: 240px;
    }

    :global(.op-btn) {
      margin-top: 22px;
    }
  }

  .anulaciones-table-wrap {
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    overflow: hidden;
  }

  .anulaciones-table {
    &__tracking {
      font-family: var(--op-font-family-mono);
      white-space: nowrap;
    }

    &__subject {
      max-width: 320px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    &__actions {
      width: 160px;
    }

    &__truncated {
      margin: var(--op-space-2) 0 0;
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }
  }

  .anulacion-detail {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-1);
    margin: 0;

    // Enlace al radicado sobre el que se decide. `&__ver` produce una clase
    // propia (no un descendiente), y hace falta: el enlace va FUERA del <dl>.
    &__ver {
      display: inline-flex;
      align-items: center;
      gap: var(--op-space-2);
      margin-top: var(--op-space-3);
      font-size: var(--op-font-sm);
      color: var(--op-primary);
      text-decoration: none;

      &:hover,
      &:focus-visible {
        text-decoration: underline;
      }
    }

    &__row {
      display: flex;
      flex-direction: column;
      gap: 2px;
      padding: var(--op-space-2) 0;
      border-bottom: 1px solid var(--op-border);
      font-size: var(--op-font-sm);

      &:last-child {
        border-bottom: none;
      }

      dt {
        color: var(--op-text-secondary);
        font-weight: var(--op-weight-medium);
        font-size: var(--op-font-xs);
        text-transform: uppercase;
        letter-spacing: 0.03em;
      }

      dd {
        color: var(--op-text);
        margin: 0;
      }
    }
  }

  .decision-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__info {
      display: flex;
      align-items: flex-start;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-warning-fg);
      background: var(--op-warning-bg);
      border: 1px solid var(--op-warning);
      border-radius: var(--op-radius-sm);
      padding: var(--op-space-3);
      margin: 0;
    }
  }

  .anulacion-dual {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    margin: 0;
    padding: var(--op-space-3);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    background: var(--op-bg);
    font-size: var(--op-font-sm);
    color: var(--op-text);

    i {
      color: var(--op-text-secondary);
      margin-top: 2px;
      flex-shrink: 0;
    }
  }
</style>
