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
  Módulo de Archivo Físico (T-12, E17).

  5 pestañas internas (patrón tablist ARIA idéntico al workspace de expedientes):
    0 — Ubicaciones      → árbol de ubicaciones de archivo físico
    1 — Unidades         → unidades de conservación (cajas, carpetas…)
    2 — Localizar expte. → localizar un expediente en archivo físico
    3 — Préstamos        → préstamos y devoluciones de unidades
    4 — FUID             → Formato Único de Inventario Documental

  Accesibilidad:
    - Patrón ARIA tablist/tab/tabpanel (WCAG 2.1 §4.1.2)
    - Navegación por teclado: flechas ← → entre tabs
    - Labels y roles ARIA en todos los elementos interactivos
    - aria-busy durante cargas, <caption> y scope en tablas
    - Modales con focus trap (componente Modal)

  Acciones de escritura (crear ubicación, nueva unidad, préstamo, vincular expediente):
    - Solo visibles si el usuario tiene USUA_PERM_EXPEDIENTE
    - El backend re-valida RBAC en cada operación (ADR-013)
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { formatearFecha } from "$lib/utils/fecha.js";
  import { permissions } from "$lib/stores/auth.js";
  import { notifySuccess, notifyError } from "$lib/stores/notifications.js";

  import Modal from "$lib/components/ui/Modal.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Card from "$lib/components/ui/Card.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";
  import { opcionesSerieTrd } from "$lib/utils/trd.js";

  /** @type {import('./$types').PageData} */
  export let data;

  // ---------------------------------------------------------------------------
  // Breadcrumbs
  // ---------------------------------------------------------------------------
  onMount(() => {
    setBreadcrumbs([{ label: "Panel principal", href: "/dashboard" }, { label: "Archivo físico" }]);
  });

  // ---------------------------------------------------------------------------
  // Autorización (espejo del RBAC; backend re-valida siempre)
  // ---------------------------------------------------------------------------
  $: canManage = $permissions.includes("USUA_PERM_EXPEDIENTE");

  // ---------------------------------------------------------------------------
  // Opciones de dominio (FormField options)
  // ---------------------------------------------------------------------------

  /** Niveles de ubicación física según la jerarquía de archivo (E17) */
  const NIVELES_UBICACION_OPTIONS = [
    { value: "sede", label: "Sede" },
    { value: "edificio", label: "Edificio" },
    { value: "piso", label: "Piso" },
    { value: "deposito", label: "Depósito" },
    { value: "modulo", label: "Módulo" },
    { value: "estante", label: "Estante" },
    { value: "entrepano", label: "Entrepaño" },
    { value: "gaveta", label: "Gaveta" },
    { value: "custodia_externa", label: "Custodia externa" },
  ];

  /** Tipos de archivo según Acuerdo AGN */
  const TIPOS_ARCHIVO_OPTIONS = [
    { value: "gestion", label: "Gestión" },
    { value: "central", label: "Central" },
    { value: "historico", label: "Histórico" },
  ];

  /** Tipos de unidad de conservación */
  const TIPOS_UNIDAD_OPTIONS = [
    { value: "caja", label: "Caja" },
    { value: "carpeta", label: "Carpeta" },
    { value: "legajo", label: "Legajo" },
    { value: "libro", label: "Libro" },
    { value: "tomo", label: "Tomo" },
    { value: "az", label: "AZ" },
    { value: "folder", label: "Folder" },
  ];

  // ---------------------------------------------------------------------------
  // Pestañas (tablist ARIA)
  // ---------------------------------------------------------------------------
  const TABS = [
    { id: "ubicaciones", label: "Ubicaciones", icon: "fa-building" },
    { id: "unidades", label: "Unidades", icon: "fa-box" },
    { id: "localizar", label: "Localizar expediente", icon: "fa-map-marker-alt" },
    { id: "prestamos", label: "Préstamos", icon: "fa-hand-holding" },
    { id: "fuid", label: "FUID", icon: "fa-file-alt" },
  ];
  let activeTab = "ubicaciones";

  function handleTabKeydown(e, tabId) {
    const idx = TABS.findIndex((t) => t.id === tabId);
    if (e.key === "ArrowRight") {
      e.preventDefault();
      const next = TABS[(idx + 1) % TABS.length];
      activeTab = next.id;
      document.getElementById(`tab-${next.id}`)?.focus();
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      const prev = TABS[(idx - 1 + TABS.length) % TABS.length];
      activeTab = prev.id;
      document.getElementById(`tab-${prev.id}`)?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      activeTab = TABS[0].id;
      document.getElementById(`tab-${TABS[0].id}`)?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      const last = TABS[TABS.length - 1];
      activeTab = last.id;
      document.getElementById(`tab-${last.id}`)?.focus();
    }
  }

  // ---------------------------------------------------------------------------
  // Tab 0: Ubicaciones
  // ---------------------------------------------------------------------------
  /** @type {any[]} */
  let ubicaciones = data.facetas?.ubicaciones?.data ?? [];

  // Catálogo plano de ubicaciones — con él se poblan los desplegables que
  // antes eran campos donde había que escribir un UUID que ninguna pantalla
  // muestra. Es el árbol entero, no solo el primer nivel: se elige el
  // entrepaño, no la sede.
  /** @type {any[]} */
  let catalogoUbicaciones = data.facetas?.catalogoUbicaciones?.data ?? [];

  /** Etiqueta legible de una ubicación: ruta si la hay, si no código y nombre. */
  function etiquetaUbicacion(u) {
    const nombre = `${u.codigo} — ${u.nombre}`;
    return u.ruta && u.ruta !== u.codigo ? `${u.ruta} · ${u.nombre}` : nombre;
  }

  // Series TRD, para el filtro del FUID. La etiqueta sale de `$lib/utils/trd.js`
  // — el mismo criterio que usan el asistente de radicación y el modal de
  // metadatos, para que una serie no se llame de tres formas distintas.
  /** @type {any[]} */
  const catalogoSeries = data.facetas?.catalogoSeries?.data ?? [];

  async function recargarCatalogoUbicaciones() {
    try {
      const res = await fetch("/archivo-fisico/api/ubicaciones?activo=true");
      const d = await res.json();
      if (res.ok) catalogoUbicaciones = Array.isArray(d) ? d : (d.items ?? []);
    } catch {
      // El catálogo es una comodidad: si falla, los desplegables quedan con lo
      // que ya tenían y la pantalla sigue siendo utilizable.
    }
  }
  let ubicacionesError = data.facetas?.ubicaciones?.error ?? null;
  let ubicacionesLoading = false;

  // Árbol en memoria: mapa id → children cargados
  /** @type {Record<string, any[]>} */
  let ubicacionChildren = {};
  /** @type {Record<string, boolean>} */
  let ubicacionExpanded = {};
  /** @type {Record<string, boolean>} */
  let ubicacionChildrenLoading = {};

  async function loadChildren(parentId) {
    ubicacionChildrenLoading[parentId] = true;
    try {
      const res = await fetch(`/archivo-fisico/api/ubicaciones?parent_id=${parentId}`);
      const data = await res.json();
      if (!res.ok) throw data;
      ubicacionChildren[parentId] = Array.isArray(data) ? data : (data.items ?? []);
      ubicacionExpanded[parentId] = true;
    } catch (err) {
      notifyError(mensajeError(err, "No se pudieron cargar los hijos de la ubicación."));
    } finally {
      ubicacionChildrenLoading[parentId] = false;
    }
  }

  function toggleUbicacion(id) {
    if (ubicacionExpanded[id]) {
      ubicacionExpanded[id] = false;
    } else {
      loadChildren(id);
    }
  }

  // Modal crear ubicación
  let showUbicacionModal = false;

  // --- Corrección de una ubicación ya creada -------------------------------
  // No existía. `tipo_archivo` es opcional al crear y decide qué
  // transferencias admite la ubicación (Ley 594/2000 art. 23): una topografía
  // cargada sin él dejaba el archivo físico sin poder transferir, y el
  // descuido no se podía arreglar desde ninguna pantalla.
  let showCorreccionModal = false;
  let correccionUbicacion = null;
  let correccionForm = { nombre: "", tipo_archivo: "", capacidad_ml: "" };
  let correccionLoading = false;

  function abrirCorreccionUbicacion(ub) {
    correccionUbicacion = ub;
    correccionForm = {
      nombre: ub.nombre ?? "",
      tipo_archivo: ub.tipo_archivo ?? "",
      capacidad_ml: ub.capacidad_ml ?? "",
    };
    showCorreccionModal = true;
  }

  async function handleCorregirUbicacion() {
    if (!correccionUbicacion) return;
    correccionLoading = true;
    try {
      const res = await fetch(
        `/archivo-fisico/api/ubicaciones/${encodeURIComponent(correccionUbicacion.id)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            nombre: correccionForm.nombre.trim() || undefined,
            tipo_archivo: correccionForm.tipo_archivo || undefined,
            capacidad_ml:
              correccionForm.capacidad_ml === "" ? undefined : correccionForm.capacidad_ml,
          }),
        },
      );
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess(`Ubicación ${result.codigo ?? ""} corregida.`);
      showCorreccionModal = false;
      correccionUbicacion = null;
      const reloadRes = await fetch("/archivo-fisico/api/ubicaciones?activo=true&solo_raiz=true");
      const reloadData = await reloadRes.json();
      ubicaciones = Array.isArray(reloadData) ? reloadData : (reloadData.items ?? []);
      // Los hijos ya cargados también quedan obsoletos tras corregir uno.
      ubicacionChildren = {};
      ubicacionExpanded = {};
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo corregir la ubicación."));
    } finally {
      correccionLoading = false;
    }
  }
  let ubicacionForm = { nivel: "", codigo: "", nombre: "", parent_id: "", tipo_archivo: "" };
  let ubicacionFormLoading = false;

  async function handleCrearUbicacion() {
    if (
      !ubicacionForm.nivel.trim() ||
      !ubicacionForm.codigo.trim() ||
      !ubicacionForm.nombre.trim()
    ) {
      notifyError("Los campos Nivel, Código y Nombre son obligatorios.");
      return;
    }
    ubicacionFormLoading = true;
    try {
      const res = await fetch("/archivo-fisico/api/ubicaciones", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          nivel: ubicacionForm.nivel.trim(),
          codigo: ubicacionForm.codigo.trim(),
          nombre: ubicacionForm.nombre.trim(),
          parent_id: ubicacionForm.parent_id.trim() || undefined,
          tipo_archivo: ubicacionForm.tipo_archivo || undefined,
        }),
      });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess("Ubicación creada correctamente.");
      showUbicacionModal = false;
      ubicacionForm = { nivel: "", codigo: "", nombre: "", parent_id: "", tipo_archivo: "" };
      // Recargar raíz
      ubicacionesLoading = true;
      const reloadRes = await fetch("/archivo-fisico/api/ubicaciones?activo=true&solo_raiz=true");
      const reloadData = await reloadRes.json();
      ubicaciones = Array.isArray(reloadData) ? reloadData : (reloadData.items ?? []);
      // Los hijos cargados dejan de ser válidos: la nueva ubicación puede
      // colgar de cualquiera de ellos.
      ubicacionChildren = {};
      ubicacionExpanded = {};
      await recargarCatalogoUbicaciones();
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo crear la ubicación."));
    } finally {
      ubicacionFormLoading = false;
      ubicacionesLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Tab 1: Unidades
  // ---------------------------------------------------------------------------
  let unidadFiltroUbicacionId = "";
  let unidadFiltroTipo = "";
  /** @type {any[]} */
  let unidades = [];
  let unidadesLoading = false;
  let unidadesError = null;

  async function buscarUnidades() {
    unidadesLoading = true;
    unidadesError = null;
    try {
      const params = new URLSearchParams();
      if (unidadFiltroUbicacionId.trim())
        params.set("ubicacion_id", unidadFiltroUbicacionId.trim());
      if (unidadFiltroTipo) params.set("tipo", unidadFiltroTipo);
      const res = await fetch(
        `/archivo-fisico/api/unidades${params.toString() ? `?${params}` : ""}`,
      );
      const data = await res.json();
      if (!res.ok) throw data;
      unidades = Array.isArray(data) ? data : (data.items ?? []);
    } catch (err) {
      unidadesError = mensajeError(err, "No se pudieron cargar las unidades.");
      unidades = [];
    } finally {
      unidadesLoading = false;
    }
  }

  // Modal nueva unidad
  let showNuevaUnidadModal = false;
  let nuevaUnidadForm = { tipo: "", codigo: "", ubicacion_id: "", soporte: "", num_folios: "" };
  let nuevaUnidadLoading = false;

  async function handleCrearUnidad() {
    if (!nuevaUnidadForm.tipo || !nuevaUnidadForm.codigo.trim()) {
      notifyError("Los campos Tipo y Código son obligatorios.");
      return;
    }
    nuevaUnidadLoading = true;
    try {
      const res = await fetch("/archivo-fisico/api/unidades", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tipo: nuevaUnidadForm.tipo,
          codigo: nuevaUnidadForm.codigo.trim(),
          ubicacion_id: nuevaUnidadForm.ubicacion_id.trim() || undefined,
          soporte: nuevaUnidadForm.soporte.trim() || undefined,
          num_folios: nuevaUnidadForm.num_folios ? Number(nuevaUnidadForm.num_folios) : undefined,
        }),
      });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess("Unidad de conservación creada correctamente.");
      showNuevaUnidadModal = false;
      nuevaUnidadForm = { tipo: "", codigo: "", ubicacion_id: "", soporte: "", num_folios: "" };
      // Refrescar lista si hay filtros activos
      if (unidadFiltroUbicacionId || unidadFiltroTipo) {
        await buscarUnidades();
      }
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo crear la unidad."));
    } finally {
      nuevaUnidadLoading = false;
    }
  }

  // Modal vincular expediente a unidad
  let showVincularModal = false;
  /** @type {string|null} */
  let vincularUnidadId = null;
  let vincularForm = { expediente_id: "", folio_inicio: "", folio_fin: "" };
  let vincularLoading = false;

  function openVincularModal(unidadId) {
    vincularUnidadId = unidadId;
    vincularForm = { expediente_id: "", folio_inicio: "", folio_fin: "" };
    showVincularModal = true;
  }

  async function handleVincularExpediente() {
    if (!vincularForm.expediente_id.trim()) {
      notifyError("El ID del expediente es obligatorio.");
      return;
    }
    vincularLoading = true;
    try {
      const res = await fetch(`/archivo-fisico/api/unidades/${vincularUnidadId}/expedientes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expediente_id: vincularForm.expediente_id.trim(),
          folio_inicio: vincularForm.folio_inicio ? Number(vincularForm.folio_inicio) : undefined,
          folio_fin: vincularForm.folio_fin ? Number(vincularForm.folio_fin) : undefined,
        }),
      });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess("Expediente vinculado a la unidad correctamente.");
      showVincularModal = false;
      // Refrescar lista de unidades si hay filtros activos
      if (unidadFiltroUbicacionId || unidadFiltroTipo) {
        await buscarUnidades();
      }
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo vincular el expediente."));
    } finally {
      vincularLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Tab 2: Localizar expediente
  // ---------------------------------------------------------------------------
  let localizarExpedienteId = "";
  /** @type {any[]} */
  let localizarResultados = [];
  let localizarLoading = false;
  let localizarError = null;
  let localizarBuscado = false;

  async function handleLocalizar() {
    if (!localizarExpedienteId.trim()) {
      notifyError("Introduce el ID del expediente.");
      return;
    }
    localizarLoading = true;
    localizarError = null;
    localizarBuscado = true;
    try {
      const res = await fetch(
        `/archivo-fisico/api/expedientes/${encodeURIComponent(localizarExpedienteId.trim())}/unidades`,
      );
      const data = await res.json();
      if (!res.ok) throw data;
      localizarResultados = Array.isArray(data) ? data : (data.items ?? []);
    } catch (err) {
      localizarError = mensajeError(err, "No se pudo localizar el expediente.");
      localizarResultados = [];
    } finally {
      localizarLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Tab 3: Préstamos
  // ---------------------------------------------------------------------------
  let prestamosEstado = "prestado";

  // Datos iniciales del servidor
  /** @type {any[]} */
  let prestamosActivos = data.facetas?.prestamosActivos?.data ?? [];
  /** @type {any[]} */
  let prestamosVencidos = data.facetas?.prestamosVencidos?.data ?? [];
  let prestamosDevueltos = /** @type {any[]} */ ([]);

  $: prestamosVisibles =
    prestamosEstado === "prestado"
      ? prestamosActivos
      : prestamosEstado === "vencido"
        ? prestamosVencidos
        : prestamosDevueltos;

  let prestamosLoading = false;

  async function cargarPrestamos() {
    if (!["prestado", "devuelto", "vencido"].includes(prestamosEstado)) return;
    prestamosLoading = true;
    try {
      const res = await fetch(`/archivo-fisico/api/prestamos?estado=${prestamosEstado}`);
      const data = await res.json();
      if (!res.ok) throw data;
      const lista = Array.isArray(data) ? data : (data.items ?? []);
      if (prestamosEstado === "prestado") prestamosActivos = lista;
      else if (prestamosEstado === "vencido") prestamosVencidos = lista;
      else prestamosDevueltos = lista;
    } catch (err) {
      notifyError(mensajeError(err, "No se pudieron cargar los préstamos."));
    } finally {
      prestamosLoading = false;
    }
  }

  // Modal nuevo préstamo (declarado al final del bloque script para evitar hoisting issues)
  let prestamoForm = { unidad_id: "", prestado_a: "", fecha_devolucion_esperada: "" };
  let prestamoLoading = false;

  // Qué unidades se pueden prestar. Se cargan al abrir el modal: el campo
  // pedía el UUID de la unidad, y en un depósito nadie identifica una caja por
  // su UUID — se identifica por su código y su signatura topográfica.
  /** @type {any[]} */
  let catalogoUnidades = [];
  let catalogoUnidadesLoading = false;

  function etiquetaUnidad(u) {
    const base = `${u.codigo} (${u.tipo})`;
    return u.signatura ? `${base} · ${u.signatura}` : base;
  }

  async function abrirModalPrestamo() {
    showPrestamoModal = true;
    catalogoUnidadesLoading = true;
    try {
      const res = await fetch("/archivo-fisico/api/unidades");
      const d = await res.json();
      if (res.ok) catalogoUnidades = Array.isArray(d) ? d : (d.items ?? []);
    } catch {
      // Sin catálogo el desplegable queda vacío y el aviso lo dice: es
      // preferible a ofrecer un campo libre que nadie puede rellenar.
    } finally {
      catalogoUnidadesLoading = false;
    }
  }

  async function handleRegistrarPrestamo() {
    if (
      !prestamoForm.unidad_id.trim() ||
      !prestamoForm.prestado_a.trim() ||
      !prestamoForm.fecha_devolucion_esperada
    ) {
      notifyError("Los campos Unidad, Prestado a y Fecha de devolución son obligatorios.");
      return;
    }
    prestamoLoading = true;
    try {
      const res = await fetch(
        `/archivo-fisico/api/unidades/${encodeURIComponent(prestamoForm.unidad_id.trim())}/prestamos`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prestado_a: prestamoForm.prestado_a.trim(),
            fecha_devolucion_esperada: prestamoForm.fecha_devolucion_esperada,
          }),
        },
      );
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess("Préstamo registrado correctamente.");
      showPrestamoModal = false;
      prestamoForm = { unidad_id: "", prestado_a: "", fecha_devolucion_esperada: "" };
      await cargarPrestamos();
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo registrar el préstamo."));
    } finally {
      prestamoLoading = false;
    }
  }

  // Devolución de préstamo
  let showDevolverModal = false;
  /** @type {string|null} */
  let devolverPrestamoId = null;
  let devolverPrestamoDesc = "";
  let devolverLoading = false;

  function openDevolverModal(id, desc) {
    devolverPrestamoId = id;
    devolverPrestamoDesc = desc;
    showDevolverModal = true;
  }

  async function handleDevolver() {
    devolverLoading = true;
    try {
      const res = await fetch(`/archivo-fisico/api/prestamos/${devolverPrestamoId}/devolver`, {
        method: "POST",
      });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess("Devolución registrada correctamente.");
      showDevolverModal = false;
      await cargarPrestamos();
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo registrar la devolución."));
    } finally {
      devolverLoading = false;
    }
  }

  // Modal movimientos de unidad
  let showMovimientosModal = false;
  /** @type {any[]} */
  let movimientos = [];
  let movimientosLoading = false;
  let movimientosUnidadDesc = "";

  async function verMovimientos(unidadId, desc) {
    movimientosUnidadDesc = desc;
    movimientos = [];
    showMovimientosModal = true;
    movimientosLoading = true;
    try {
      const res = await fetch(
        `/archivo-fisico/api/unidades/${encodeURIComponent(unidadId)}/movimientos`,
      );
      const data = await res.json();
      if (!res.ok) throw data;
      movimientos = Array.isArray(data) ? data : (data.items ?? []);
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo obtener el historial de movimientos."));
      showMovimientosModal = false;
    } finally {
      movimientosLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Tab 4: FUID
  // ---------------------------------------------------------------------------
  let fuidFiltroUbicacionId = "";
  let fuidFiltroTrdSerieId = "";
  /** @type {any[]} */
  let fuidRows = [];
  let fuidLoading = false;
  let fuidError = null;
  let fuidBuscado = false;
  let fuidXmlLoading = false;

  async function consultarFuid() {
    fuidLoading = true;
    fuidError = null;
    fuidBuscado = true;
    try {
      const params = new URLSearchParams();
      if (fuidFiltroUbicacionId.trim()) params.set("ubicacion_id", fuidFiltroUbicacionId.trim());
      if (fuidFiltroTrdSerieId.trim()) params.set("trd_serie_id", fuidFiltroTrdSerieId.trim());
      const res = await fetch(`/archivo-fisico/api/fuid${params.toString() ? `?${params}` : ""}`);
      const data = await res.json();
      if (!res.ok) throw data;
      fuidRows = Array.isArray(data) ? data : (data.items ?? data.rows ?? []);
    } catch (err) {
      fuidError = mensajeError(err, "No se pudo consultar el FUID.");
      fuidRows = [];
    } finally {
      fuidLoading = false;
    }
  }

  async function handleDescargarFuidXml() {
    fuidXmlLoading = true;
    try {
      const params = new URLSearchParams();
      if (fuidFiltroUbicacionId.trim()) params.set("ubicacion_id", fuidFiltroUbicacionId.trim());
      if (fuidFiltroTrdSerieId.trim()) params.set("trd_serie_id", fuidFiltroTrdSerieId.trim());
      const res = await fetch(
        `/archivo-fisico/api/fuid.xml${params.toString() ? `?${params}` : ""}`,
      );
      if (!res.ok) {
        // NUNCA crear blob de error — parsear JSON y mostrar notificación
        const err = await res.json().catch(() => ({}));
        throw err;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "fuid.xml";
      a.click();
      URL.revokeObjectURL(url);
      notifySuccess("FUID XML descargado correctamente.");
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo descargar el FUID XML."));
    } finally {
      fuidXmlLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Helpers de formato
  // ---------------------------------------------------------------------------
  /** @param {string|null|undefined} iso */
  function fmtDate(iso) {
    return formatearFecha(iso);
  }

  /** Rango de folios del expediente dentro de la unidad, como lo pide el FUID. */
  function rangoFolios(row) {
    if (row?.folio_inicio != null && row?.folio_fin != null) {
      return `${row.folio_inicio}–${row.folio_fin}`;
    }
    return row?.num_folios ?? "—";
  }

  const ESTADO_PRESTAMO_MAP = {
    prestado: { label: "Prestado", status: "warning" },
    devuelto: { label: "Devuelto", status: "success" },
    vencido: { label: "Vencido", status: "danger" },
  };

  // Variable para el modal del préstamo (corregida sintaxis)
  let showPrestamoModal = false;
</script>

<svelte:head>
  <title>Archivo físico — OrpycaMCP</title>
</svelte:head>

<!-- =========================================================================
     Workspace de archivo físico
     ========================================================================= -->
<div class="af-workspace">
  <!-- Cabecera -->
  <div class="af-workspace__header">
    <h1 class="af-workspace__title">
      <i class="fas fa-box-archive" aria-hidden="true"></i>
      Archivo físico
    </h1>
    {#if canManage}
      <div class="af-workspace__header-actions" role="group" aria-label="Acciones de archivo">
        <Button variant="primary" on:click={() => (showUbicacionModal = true)}>
          <i class="fas fa-plus" aria-hidden="true"></i>
          Nueva ubicación
        </Button>
        <Button variant="secondary" on:click={() => (showNuevaUnidadModal = true)}>
          <i class="fas fa-box" aria-hidden="true"></i>
          Nueva unidad
        </Button>
      </div>
    {/if}
  </div>

  <!-- Tablist -->
  <div class="af-tabs" role="tablist" aria-label="Módulos de archivo físico">
    {#each TABS as tab (tab.id)}
      <button
        id="tab-{tab.id}"
        class="af-tabs__tab"
        class:af-tabs__tab--active={activeTab === tab.id}
        role="tab"
        aria-selected={activeTab === tab.id}
        aria-controls="panel-{tab.id}"
        tabindex={activeTab === tab.id ? 0 : -1}
        on:click={() => (activeTab = tab.id)}
        on:keydown={(e) => handleTabKeydown(e, tab.id)}
      >
        <i class="fas {tab.icon}" aria-hidden="true"></i>
        <span>{tab.label}</span>
      </button>
    {/each}
  </div>

  <!-- Panels -->
  <div class="af-workspace__panels">
    <!-- -----------------------------------------------------------------------
         Panel 0 — Ubicaciones
         ----------------------------------------------------------------------- -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="panel-ubicaciones"
      role="tabpanel"
      aria-labelledby="tab-ubicaciones"
      hidden={activeTab !== "ubicaciones"}
      tabindex="0"
      class="af-panel"
    >
      <Card>
        <h2 slot="header" class="op-card-title">Ubicaciones de archivo</h2>

        {#if ubicacionesLoading}
          <Loader label="Cargando ubicaciones…" />
        {:else if ubicacionesError}
          <EmptyState
            icon="fas fa-exclamation-triangle"
            title="Error al cargar ubicaciones"
            description={mensajeError(ubicacionesError, "Intenta recargar la página.")}
          />
        {:else if ubicaciones.length === 0}
          <EmptyState
            icon="fas fa-building"
            title="Sin ubicaciones"
            description="No se han registrado ubicaciones de archivo."
          />
        {:else}
          <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
          <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
          <div class="af-table-wrap" role="region" aria-label="Ubicaciones de archivo" tabindex="0">
            <table class="table is-fullwidth is-hoverable">
              <caption class="is-sr-only">Ubicaciones de archivo físico</caption>
              <thead>
                <tr>
                  <th scope="col">Nivel</th>
                  <th scope="col">Código</th>
                  <th scope="col">Nombre</th>
                  <th scope="col">Tipo</th>
                  <th scope="col">Cap. (ml)</th>
                  <th scope="col"><span class="is-sr-only">Acciones</span></th>
                </tr>
              </thead>
              <tbody>
                {#each ubicaciones as ub (ub.id)}
                  <tr>
                    <td>{ub.nivel ?? "—"}</td>
                    <td><code>{ub.codigo ?? "—"}</code></td>
                    <td>{ub.nombre ?? "—"}</td>
                    <td>{ub.tipo_archivo ?? "—"}</td>
                    <td>{ub.capacidad_ml ?? "—"}</td>
                    <td class="af-actions-cell">
                      <button
                        class="button is-small is-light"
                        aria-label="Ver hijos de {ub.nombre}"
                        aria-expanded={ubicacionExpanded[ub.id] ?? false}
                        aria-busy={ubicacionChildrenLoading[ub.id] ?? false}
                        on:click={() => toggleUbicacion(ub.id)}
                      >
                        {#if ubicacionChildrenLoading[ub.id]}
                          <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
                        {:else if ubicacionExpanded[ub.id]}
                          <i class="fas fa-chevron-up" aria-hidden="true"></i>
                        {:else}
                          <i class="fas fa-chevron-down" aria-hidden="true"></i>
                        {/if}
                        <span class="is-sr-only">Ver hijos</span>
                      </button>
                      <!--
                        Corregir una ubicación faltaba por completo. `tipo_archivo`
                        se deja vacío con toda naturalidad al cargar la topografía
                        —el desplegable ofrece «Sin tipo»— y sin él la ubicación no
                        admite ninguna transferencia (Ley 594/2000 art. 23). Sin
                        esta puerta, ese descuido era permanente.
                      -->
                      <button
                        class="button is-small is-light"
                        aria-label="Corregir la ubicación {ub.nombre}"
                        on:click={() => abrirCorreccionUbicacion(ub)}
                      >
                        <i class="fas fa-pen" aria-hidden="true"></i>
                        <span class="is-sr-only">Corregir</span>
                      </button>
                    </td>
                  </tr>
                  {#if ubicacionExpanded[ub.id] && ubicacionChildren[ub.id]}
                    {#each ubicacionChildren[ub.id] as child (child.id)}
                      <tr class="af-child-row">
                        <td class="af-child-indent">{child.nivel ?? "—"}</td>
                        <td><code>{child.codigo ?? "—"}</code></td>
                        <td>{child.nombre ?? "—"}</td>
                        <td>{child.tipo_archivo ?? "—"}</td>
                        <td>{child.capacidad_ml ?? "—"}</td>
                        <td class="af-actions-cell">
                          <button
                            class="button is-small is-light"
                            aria-label="Corregir la ubicación {child.nombre}"
                            on:click={() => abrirCorreccionUbicacion(child)}
                          >
                            <i class="fas fa-pen" aria-hidden="true"></i>
                            <span class="is-sr-only">Corregir</span>
                          </button>
                        </td>
                      </tr>
                    {/each}
                  {/if}
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </Card>
    </div>

    <!-- -----------------------------------------------------------------------
         Panel 1 — Unidades
         ----------------------------------------------------------------------- -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="panel-unidades"
      role="tabpanel"
      aria-labelledby="tab-unidades"
      hidden={activeTab !== "unidades"}
      tabindex="0"
      class="af-panel"
    >
      <Card>
        <h2 slot="header" class="op-card-title">Unidades de conservación</h2>

        <!-- Filtros de búsqueda -->
        <div class="af-filter-row" role="search" aria-label="Filtros de unidades">
          <div class="af-filter-row__field">
            <label for="unidad-filtro-ubicacion" class="label">Ubicación</label>
            <div class="select is-fullwidth">
              <select id="unidad-filtro-ubicacion" bind:value={unidadFiltroUbicacionId}>
                <option value="">Todas</option>
                {#each catalogoUbicaciones as u (u.id)}
                  <option value={u.id}>{etiquetaUbicacion(u)}</option>
                {/each}
              </select>
            </div>
          </div>
          <div class="af-filter-row__field">
            <label for="unidad-filtro-tipo" class="label">Tipo</label>
            <div class="select">
              <select
                id="unidad-filtro-tipo"
                bind:value={unidadFiltroTipo}
                aria-label="Tipo de unidad"
              >
                <option value="">Todos</option>
                <option value="caja">Caja</option>
                <option value="carpeta">Carpeta</option>
                <option value="legajo">Legajo</option>
                <option value="libro">Libro</option>
                <option value="tomo">Tomo</option>
                <option value="az">AZ</option>
                <option value="folder">Folder</option>
              </select>
            </div>
          </div>
          <Button variant="primary" on:click={buscarUnidades} disabled={unidadesLoading}>
            {#if unidadesLoading}
              <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
            {:else}
              <i class="fas fa-search" aria-hidden="true"></i>
            {/if}
            Buscar
          </Button>
        </div>

        <div aria-live="polite" aria-atomic="false">
          {#if unidadesLoading}
            <Loader label="Buscando unidades…" />
          {:else if unidadesError}
            <EmptyState
              icon="fas fa-exclamation-triangle"
              title="Error al buscar"
              description={unidadesError}
            />
          {:else if unidades.length === 0}
            <EmptyState
              icon="fas fa-box-open"
              title="Sin resultados"
              description="Aplica filtros y pulsa Buscar para ver unidades de conservación."
            />
          {:else}
            <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
            <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
            <div
              class="af-table-wrap"
              aria-busy={unidadesLoading}
              role="region"
              aria-label="Unidades de conservación"
              tabindex="0"
            >
              <table class="table is-fullwidth is-hoverable">
                <caption class="is-sr-only">Unidades de conservación encontradas</caption>
                <thead>
                  <tr>
                    <th scope="col">Tipo</th>
                    <th scope="col">Código</th>
                    <th scope="col">Signatura</th>
                    <th scope="col">Soporte</th>
                    <th scope="col">Folios</th>
                    <th scope="col"><span class="is-sr-only">Acciones</span></th>
                  </tr>
                </thead>
                <tbody>
                  {#each unidades as u (u.id)}
                    <tr>
                      <td><span class="tag is-light">{u.tipo ?? "—"}</span></td>
                      <td><code>{u.codigo ?? "—"}</code></td>
                      <td>{u.signatura ?? "—"}</td>
                      <td>{u.soporte ?? "—"}</td>
                      <td>{u.num_folios ?? "—"}</td>
                      <td class="af-actions-cell">
                        {#if canManage}
                          <button
                            class="button is-small is-light"
                            on:click={() => openVincularModal(u.id)}
                            aria-label="Vincular expediente a {u.codigo}"
                          >
                            <i class="fas fa-link" aria-hidden="true"></i>
                            <span>Vincular expte.</span>
                          </button>
                        {/if}
                        <button
                          class="button is-small is-light"
                          on:click={() => verMovimientos(u.id, u.codigo)}
                          aria-label="Ver movimientos de {u.codigo}"
                        >
                          <i class="fas fa-history" aria-hidden="true"></i>
                          <span>Movimientos</span>
                        </button>
                      </td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {/if}
        </div>
      </Card>
    </div>

    <!-- -----------------------------------------------------------------------
         Panel 2 — Localizar expediente
         ----------------------------------------------------------------------- -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="panel-localizar"
      role="tabpanel"
      aria-labelledby="tab-localizar"
      hidden={activeTab !== "localizar"}
      tabindex="0"
      class="af-panel"
    >
      <Card>
        <h2 slot="header" class="op-card-title">Localizar expediente en archivo</h2>

        <div class="af-filter-row" role="search" aria-label="Localizar expediente">
          <div class="af-filter-row__field">
            <label for="localizar-id" class="label">Número de expediente</label>
            <input
              id="localizar-id"
              class="input"
              type="text"
              placeholder="Ej. EXP-2026-0020"
              bind:value={localizarExpedienteId}
              on:keydown={(e) => e.key === "Enter" && handleLocalizar()}
              aria-label="Número del expediente a localizar"
            />
          </div>
          <Button variant="primary" on:click={handleLocalizar} disabled={localizarLoading}>
            {#if localizarLoading}
              <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
            {:else}
              <i class="fas fa-map-marker-alt" aria-hidden="true"></i>
            {/if}
            Localizar
          </Button>
        </div>

        <div aria-live="polite" aria-atomic="false">
          {#if localizarLoading}
            <Loader label="Localizando expediente…" />
          {:else if localizarError}
            <EmptyState
              icon="fas fa-exclamation-triangle"
              title="Error"
              description={localizarError}
            />
          {:else if localizarBuscado && localizarResultados.length === 0}
            <EmptyState
              icon="fas fa-search"
              title="No encontrado"
              description="El expediente no está vinculado a ninguna unidad de conservación."
            />
          {:else if localizarResultados.length > 0}
            <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
            <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
            <div
              class="af-table-wrap"
              role="region"
              aria-label="Ubicaciones del expediente localizado"
              tabindex="0"
            >
              <table class="table is-fullwidth">
                <caption>Unidades donde se encuentra el expediente</caption>
                <thead>
                  <tr>
                    <th scope="col">Signatura</th>
                    <th scope="col">Tipo unidad</th>
                    <th scope="col">Código unidad</th>
                    <th scope="col">Folio inicio</th>
                    <th scope="col">Folio fin</th>
                  </tr>
                </thead>
                <tbody>
                  {#each localizarResultados as r (r.id ?? r.unidad_id)}
                    <tr>
                      <td>{r.signatura ?? r.unidad?.signatura ?? "—"}</td>
                      <td>{r.tipo ?? r.unidad?.tipo ?? "—"}</td>
                      <td><code>{r.codigo ?? r.unidad?.codigo ?? "—"}</code></td>
                      <td>{r.folio_inicio ?? "—"}</td>
                      <td>{r.folio_fin ?? "—"}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {:else}
            <EmptyState
              icon="fas fa-map-marker-alt"
              title="Busca un expediente"
              description="Introduce el número del expediente (ej. EXP-2026-0020) y pulsa Localizar."
            />
          {/if}
        </div>
      </Card>
    </div>

    <!-- -----------------------------------------------------------------------
         Panel 3 — Préstamos
         ----------------------------------------------------------------------- -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="panel-prestamos"
      role="tabpanel"
      aria-labelledby="tab-prestamos"
      hidden={activeTab !== "prestamos"}
      tabindex="0"
      class="af-panel"
    >
      <Card>
        <div slot="header" class="af-panel-header">
          <h2 class="op-card-title">Préstamos de unidades</h2>
          {#if canManage}
            <Button variant="primary" size="sm" on:click={abrirModalPrestamo}>
              <i class="fas fa-plus" aria-hidden="true"></i>
              Registrar préstamo
            </Button>
          {/if}
        </div>

        <!-- Selector de estado -->
        <div class="af-filter-row">
          <div class="af-filter-row__field">
            <label for="prestamos-estado" class="label">Estado</label>
            <div class="select">
              <select
                id="prestamos-estado"
                bind:value={prestamosEstado}
                on:change={cargarPrestamos}
                aria-label="Filtrar préstamos por estado"
              >
                <option value="prestado">Prestado</option>
                <option value="vencido">Vencido</option>
                <option value="devuelto">Devuelto</option>
              </select>
            </div>
          </div>
        </div>

        {#if prestamosLoading}
          <Loader label="Cargando préstamos…" />
        {:else if prestamosVisibles.length === 0}
          <EmptyState
            icon="fas fa-hand-holding"
            title="Sin préstamos"
            description="No hay préstamos con el estado seleccionado."
          />
        {:else}
          <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
          <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
          <div
            class="af-table-wrap"
            aria-busy={prestamosLoading}
            role="region"
            aria-label="Préstamos"
            tabindex="0"
          >
            <table class="table is-fullwidth is-hoverable">
              <caption class="is-sr-only">Lista de préstamos</caption>
              <thead>
                <tr>
                  <th scope="col">Unidad</th>
                  <th scope="col">Prestado a</th>
                  <th scope="col">Fecha préstamo</th>
                  <th scope="col">Fecha devolución esp.</th>
                  <th scope="col">Estado</th>
                  <th scope="col"><span class="is-sr-only">Acciones</span></th>
                </tr>
              </thead>
              <tbody>
                {#each prestamosVisibles as p (p.id)}
                  {@const estadoInfo = ESTADO_PRESTAMO_MAP[p.estado] ?? {
                    label: p.estado,
                    status: "neutral",
                  }}
                  <tr>
                    <td>
                      <code>{p.unidad_codigo ?? "—"}</code>
                      {#if p.unidad_signatura}
                        <span class="af-fuid-tipo">{p.unidad_signatura}</span>
                      {/if}
                    </td>
                    <td>{p.prestado_a ?? "—"}</td>
                    <td>{fmtDate(p.fecha_prestamo ?? p.created_at)}</td>
                    <td>{fmtDate(p.fecha_devolucion_esperada)}</td>
                    <td>
                      <StatusChip status={estadoInfo.status} label={estadoInfo.label} dot />
                    </td>
                    <td class="af-actions-cell">
                      {#if canManage && p.estado === "prestado"}
                        <button
                          class="button is-small is-warning is-light"
                          on:click={() => openDevolverModal(p.id, p.prestado_a)}
                          aria-label="Registrar devolución del préstamo a {p.prestado_a}"
                        >
                          <i class="fas fa-undo" aria-hidden="true"></i>
                          <span>Devolver</span>
                        </button>
                      {/if}
                      <button
                        class="button is-small is-light"
                        on:click={() =>
                          verMovimientos(
                            p.unidad_id ?? p.unidad?.id,
                            p.unidad_codigo ?? p.unidad?.codigo,
                          )}
                        aria-label="Ver movimientos"
                      >
                        <i class="fas fa-history" aria-hidden="true"></i>
                        <span class="is-sr-only">Movimientos</span>
                      </button>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </Card>
    </div>

    <!-- -----------------------------------------------------------------------
         Panel 4 — FUID
         ----------------------------------------------------------------------- -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="panel-fuid"
      role="tabpanel"
      aria-labelledby="tab-fuid"
      hidden={activeTab !== "fuid"}
      tabindex="0"
      class="af-panel"
    >
      <Card>
        <h2 slot="header" class="op-card-title">FUID — Formato Único de Inventario Documental</h2>

        <!-- Filtros -->
        <div class="af-filter-row" role="search" aria-label="Filtros FUID">
          <div class="af-filter-row__field">
            <label for="fuid-ubicacion-id" class="label">Ubicación</label>
            <div class="select is-fullwidth">
              <select id="fuid-ubicacion-id" bind:value={fuidFiltroUbicacionId}>
                <option value="">Todo el archivo</option>
                {#each catalogoUbicaciones as u (u.id)}
                  <option value={u.id}>{etiquetaUbicacion(u)}</option>
                {/each}
              </select>
            </div>
          </div>
          <div class="af-filter-row__field">
            <label for="fuid-trd-serie-id" class="label">Serie TRD</label>
            <div class="select is-fullwidth">
              <select id="fuid-trd-serie-id" bind:value={fuidFiltroTrdSerieId}>
                <option value="">Todas las series</option>
                {#each opcionesSerieTrd(catalogoSeries) as opt (opt.value)}
                  <option value={opt.value}>{opt.label}</option>
                {/each}
              </select>
            </div>
          </div>
          <div class="af-filter-row__actions">
            <Button variant="primary" on:click={consultarFuid} disabled={fuidLoading}>
              {#if fuidLoading}
                <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
              {:else}
                <i class="fas fa-search" aria-hidden="true"></i>
              {/if}
              Consultar
            </Button>
            <Button variant="secondary" on:click={handleDescargarFuidXml} disabled={fuidXmlLoading}>
              {#if fuidXmlLoading}
                <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
              {:else}
                <i class="fas fa-file-download" aria-hidden="true"></i>
              {/if}
              Descargar XML
            </Button>
          </div>
        </div>

        <div aria-live="polite" aria-atomic="false">
          {#if fuidLoading}
            <Loader label="Consultando FUID…" />
          {:else if fuidError}
            <EmptyState
              icon="fas fa-exclamation-triangle"
              title="Error al consultar FUID"
              description={fuidError}
            />
          {:else if fuidBuscado && fuidRows.length === 0}
            <EmptyState
              icon="fas fa-file-alt"
              title="Sin registros"
              description="No se encontraron registros FUID con los filtros indicados."
            />
          {:else if fuidRows.length > 0}
            <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
            <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
            <div
              class="af-table-wrap"
              aria-busy={fuidLoading}
              role="region"
              aria-label="Registros FUID"
              tabindex="0"
            >
              <table class="table is-fullwidth is-hoverable">
                <caption>Formato Único de Inventario Documental</caption>
                <thead>
                  <tr>
                    <th scope="col">Orden</th>
                    <th scope="col">Código expediente</th>
                    <th scope="col">Asunto</th>
                    <th scope="col">Serie</th>
                    <th scope="col">Fechas extremas</th>
                    <th scope="col">Signatura</th>
                    <th scope="col">Unidad</th>
                    <th scope="col">Soporte</th>
                    <th scope="col">Folios</th>
                    <th scope="col">Frec. consulta</th>
                  </tr>
                </thead>
                <tbody>
                  {#each fuidRows as row (row.expediente_id)}
                    <tr>
                      <td>{row.orden}</td>
                      <td><code>{row.expediente_code}</code></td>
                      <td>{row.asunto}</td>
                      <td>{[row.serie_code, row.serie_name].filter(Boolean).join(" — ") || "—"}</td>
                      <td>{fmtDate(row.fecha_inicial)} – {fmtDate(row.fecha_final)}</td>
                      <td>{row.signatura ?? "—"}</td>
                      <td>
                        <code>{row.unidad_codigo}</code>
                        <span class="af-fuid-tipo">({row.unidad_tipo})</span>
                      </td>
                      <td>{row.soporte ?? "—"}</td>
                      <td>{rangoFolios(row)}</td>
                      <td>{row.frecuencia_consulta}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {:else}
            <EmptyState
              icon="fas fa-file-alt"
              title="Consulta el FUID"
              description="Aplica filtros opcionales y pulsa Consultar para ver el inventario documental."
            />
          {/if}
        </div>
      </Card>
    </div>
  </div>
</div>

<!-- =========================================================================
     Modales
     ========================================================================= -->

<!-- Modal: crear ubicación -->
<!-- Modal: corregir una ubicación existente -->
<Modal
  open={showCorreccionModal}
  title={correccionUbicacion ? `Corregir ${correccionUbicacion.codigo}` : "Corregir ubicación"}
  on:close={() => (showCorreccionModal = false)}
>
  <div class="af-form">
    <p class="help">
      El código y el sitio en el árbol no se cambian: el código es la referencia que ya llevan
      impresas las signaturas de las unidades, y mover el padre arrastraría todo lo que cuelga de
      aquí. Para eso se crea otra ubicación y se trasladan las unidades.
    </p>
    <FormField id="ub-corr-nombre" label="Nombre" type="text" bind:value={correccionForm.nombre} />
    <FormField
      id="ub-corr-tipo"
      label="Tipo de archivo"
      type="select"
      placeholder="— Sin tipo —"
      bind:value={correccionForm.tipo_archivo}
      options={TIPOS_ARCHIVO_OPTIONS}
      hint="Decide qué transferencias admite: una primaria va de «gestión» a «central»; una secundaria, de «central» a «histórico». Sin tipo, la ubicación no puede ser origen ni destino de ninguna."
    />
    <FormField
      id="ub-corr-capacidad"
      label="Capacidad (ml)"
      type="number"
      bind:value={correccionForm.capacidad_ml}
    />
  </div>
  <div slot="footer" class="af-modal-footer">
    <Button variant="secondary" on:click={() => (showCorreccionModal = false)}>Cancelar</Button>
    <Button variant="primary" on:click={handleCorregirUbicacion} disabled={correccionLoading}>
      {#if correccionLoading}
        <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
      {/if}
      Guardar corrección
    </Button>
  </div>
</Modal>

<Modal
  open={showUbicacionModal}
  title="Nueva ubicación de archivo"
  on:close={() => (showUbicacionModal = false)}
>
  <div class="af-form">
    <FormField
      id="ub-nivel"
      label="Nivel *"
      type="select"
      required
      placeholder="— Selecciona nivel —"
      bind:value={ubicacionForm.nivel}
      options={NIVELES_UBICACION_OPTIONS}
    />
    <FormField
      id="ub-codigo"
      label="Código *"
      type="text"
      required
      placeholder="Ej. EST-001"
      bind:value={ubicacionForm.codigo}
    />
    <FormField
      id="ub-nombre"
      label="Nombre *"
      type="text"
      required
      placeholder="Nombre descriptivo"
      bind:value={ubicacionForm.nombre}
    />
    <div class="field">
      <label class="label" for="ub-parent">Ubicación superior</label>
      <div class="select is-fullwidth">
        <select id="ub-parent" bind:value={ubicacionForm.parent_id}>
          <option value="">— Ninguna (primer nivel) —</option>
          {#each catalogoUbicaciones as u (u.id)}
            <option value={u.id}>{etiquetaUbicacion(u)}</option>
          {/each}
        </select>
      </div>
      <p class="help">
        Dónde se encuentra: un depósito cuelga de una sede, un estante de un depósito.
      </p>
    </div>
    <!--
      El tipo de archivo NO es una etiqueta descriptiva: es lo que decide si
      esta ubicación puede ser origen o destino de una transferencia. Una
      primaria va de `gestión` a `central` y una secundaria de `central` a
      `histórico` (Ley 594/2000 art. 23, ciclo vital del documento), así que una
      ubicación sin tipo queda fuera de las dos. Se dejaba vacío con toda
      naturalidad —el desplegable ofrece «Sin tipo» y nada advertía— y el
      resultado se descubría mucho después, al intentar transferir: 422
      `ubicacion_incoherente_con_tipo` sobre un depósito recién creado. Peor
      aún, hoy no hay forma de corregirlo desde el sistema (no existe edición
      de ubicaciones), así que el descuido es permanente.
    -->
    <FormField
      id="ub-tipo"
      label="Tipo de archivo"
      type="select"
      placeholder="— Sin tipo —"
      bind:value={ubicacionForm.tipo_archivo}
      options={TIPOS_ARCHIVO_OPTIONS}
      hint="Decide qué transferencias admite esta ubicación: una primaria va de «gestión» a «central»; una secundaria, de «central» a «histórico». Sin tipo, la ubicación no puede ser origen ni destino de ninguna — y por ahora no se puede cambiar después."
    />
  </div>
  <div slot="footer" class="af-modal-footer">
    <Button variant="secondary" on:click={() => (showUbicacionModal = false)}>Cancelar</Button>
    <Button variant="primary" on:click={handleCrearUbicacion} disabled={ubicacionFormLoading}>
      {#if ubicacionFormLoading}
        <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
      {/if}
      Crear ubicación
    </Button>
  </div>
</Modal>

<!-- Modal: nueva unidad de conservación -->
<Modal
  open={showNuevaUnidadModal}
  title="Nueva unidad de conservación"
  on:close={() => (showNuevaUnidadModal = false)}
>
  <div class="af-form">
    <FormField
      id="nu-tipo"
      label="Tipo *"
      type="select"
      required
      placeholder="— Selecciona tipo —"
      bind:value={nuevaUnidadForm.tipo}
      options={TIPOS_UNIDAD_OPTIONS}
    />
    <FormField
      id="nu-codigo"
      label="Código *"
      type="text"
      required
      placeholder="Ej. CAJ-001"
      bind:value={nuevaUnidadForm.codigo}
    />
    <div class="field">
      <label class="label" for="un-ubicacion">Ubicación</label>
      <div class="select is-fullwidth">
        <select id="un-ubicacion" bind:value={nuevaUnidadForm.ubicacion_id}>
          <option value="">— Sin ubicar todavía —</option>
          {#each catalogoUbicaciones as u (u.id)}
            <option value={u.id}>{etiquetaUbicacion(u)}</option>
          {/each}
        </select>
      </div>
      <p class="help">De ella sale la signatura topográfica de la unidad.</p>
    </div>
    <FormField
      id="nu-soporte"
      label="Soporte"
      type="text"
      placeholder="Ej. papel, digital"
      bind:value={nuevaUnidadForm.soporte}
    />
    <FormField
      id="nu-folios"
      label="N.° de folios"
      type="number"
      placeholder="0"
      bind:value={nuevaUnidadForm.num_folios}
    />
  </div>
  <div slot="footer" class="af-modal-footer">
    <Button variant="secondary" on:click={() => (showNuevaUnidadModal = false)}>Cancelar</Button>
    <Button variant="primary" on:click={handleCrearUnidad} disabled={nuevaUnidadLoading}>
      {#if nuevaUnidadLoading}
        <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
      {/if}
      Crear unidad
    </Button>
  </div>
</Modal>

<!-- Modal: vincular expediente a unidad -->
<Modal
  open={showVincularModal}
  title="Vincular expediente a unidad"
  on:close={() => (showVincularModal = false)}
>
  <div class="af-form">
    <FormField
      id="vinc-expediente"
      label="Número de expediente *"
      type="text"
      required
      placeholder="Ej. EXP-2026-0020"
      hint="El número que aparece en la ficha y en el listado de expedientes."
      bind:value={vincularForm.expediente_id}
    />
    <FormField
      id="vin-folio-ini"
      label="Folio inicio"
      type="number"
      placeholder="Número de folio inicial"
      bind:value={vincularForm.folio_inicio}
    />
    <FormField
      id="vin-folio-fin"
      label="Folio fin"
      type="number"
      placeholder="Número de folio final"
      bind:value={vincularForm.folio_fin}
    />
  </div>
  <div slot="footer" class="af-modal-footer">
    <Button variant="secondary" on:click={() => (showVincularModal = false)}>Cancelar</Button>
    <Button variant="primary" on:click={handleVincularExpediente} disabled={vincularLoading}>
      {#if vincularLoading}
        <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
      {/if}
      Vincular
    </Button>
  </div>
</Modal>

<!-- Modal: registrar préstamo -->
<Modal
  open={showPrestamoModal}
  title="Registrar préstamo"
  on:close={() => (showPrestamoModal = false)}
>
  <div class="af-form">
    <div class="field">
      <label class="label" for="pr-unidad">Unidad a prestar *</label>
      <div class="select is-fullwidth">
        <select id="pr-unidad" bind:value={prestamoForm.unidad_id} required>
          <option value="">— Selecciona la unidad —</option>
          {#each catalogoUnidades as u (u.id)}
            <option value={u.id}>{etiquetaUnidad(u)}</option>
          {/each}
        </select>
      </div>
      {#if catalogoUnidadesLoading}
        <p class="help">Cargando unidades…</p>
      {:else if catalogoUnidades.length === 0}
        <p class="help is-danger">
          No hay unidades de conservación registradas. Crea una antes de prestar.
        </p>
      {/if}
    </div>
    <FormField
      id="pr-prestado-a"
      label="Prestado a *"
      type="text"
      required
      placeholder="Nombre del solicitante"
      bind:value={prestamoForm.prestado_a}
    />
    <FormField
      id="pr-fecha"
      label="Fecha de devolución esperada *"
      type="date"
      required
      bind:value={prestamoForm.fecha_devolucion_esperada}
    />
  </div>
  <div slot="footer" class="af-modal-footer">
    <Button variant="secondary" on:click={() => (showPrestamoModal = false)}>Cancelar</Button>
    <Button variant="primary" on:click={handleRegistrarPrestamo} disabled={prestamoLoading}>
      {#if prestamoLoading}
        <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
      {/if}
      Registrar préstamo
    </Button>
  </div>
</Modal>

<!-- Modal: confirmar devolución -->
<Modal
  open={showDevolverModal}
  title="Confirmar devolución"
  size="sm"
  on:close={() => (showDevolverModal = false)}
>
  <p>
    ¿Confirmas la devolución del préstamo a <strong>{devolverPrestamoDesc}</strong>? Esta acción
    quedará registrada en la auditoría del sistema.
  </p>
  <div slot="footer" class="af-modal-footer">
    <Button variant="secondary" on:click={() => (showDevolverModal = false)}>Cancelar</Button>
    <Button variant="primary" on:click={handleDevolver} disabled={devolverLoading}>
      {#if devolverLoading}
        <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
      {/if}
      Confirmar devolución
    </Button>
  </div>
</Modal>

<!-- Modal: historial de movimientos -->
<Modal
  open={showMovimientosModal}
  title="Movimientos de la unidad {movimientosUnidadDesc}"
  size="lg"
  on:close={() => (showMovimientosModal = false)}
>
  {#if movimientosLoading}
    <Loader label="Cargando movimientos…" />
  {:else if movimientos.length === 0}
    <EmptyState
      icon="fas fa-history"
      title="Sin movimientos"
      description="Esta unidad no tiene movimientos registrados."
    />
  {:else}
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div class="af-table-wrap" role="region" aria-label="Movimientos de la unidad" tabindex="0">
      <table class="table is-fullwidth">
        <caption class="is-sr-only">Historial de movimientos</caption>
        <thead>
          <tr>
            <th scope="col">Fecha</th>
            <th scope="col">Tipo</th>
            <th scope="col">Usuario</th>
            <th scope="col">Detalle</th>
          </tr>
        </thead>
        <tbody>
          {#each movimientos as m (m.id)}
            <tr>
              <td>{fmtDate(m.created_at ?? m.fecha)}</td>
              <td>{m.tipo ?? m.tipo_movimiento ?? "—"}</td>
              <td>{m.usuario ?? m.user_id ?? "—"}</td>
              <td>{m.detalle ?? m.observacion ?? "—"}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
  <div slot="footer" class="af-modal-footer">
    <Button variant="secondary" on:click={() => (showMovimientosModal = false)}>Cerrar</Button>
  </div>
</Modal>

<style lang="scss">
  /* Sufijo tenue: el tipo de unidad y la signatura acompañan al código sin
     competir con él — el código es lo que se busca en el estante. */
  .af-fuid-tipo {
    color: var(--op-text-muted);
    font-size: 0.85em;
    margin-left: 0.35rem;
  }

  .af-workspace {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
    padding: var(--op-space-5);

    &__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: var(--op-space-3);
    }

    &__title {
      font-size: var(--op-font-xl);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      display: flex;
      align-items: center;
      gap: var(--op-space-2);

      i {
        color: var(--op-primary);
      }
    }

    &__header-actions {
      display: flex;
      gap: var(--op-space-2);
      flex-wrap: wrap;
    }

    &__panels {
      background: var(--op-surface);
      border: 1px solid var(--op-border);
      border-radius: 0 0 var(--op-radius) var(--op-radius);
      border-top: none;
    }
  }

  // Tablist
  .af-tabs {
    display: flex;
    border-bottom: 2px solid var(--op-border);
    background: var(--op-bg);
    overflow-x: auto;

    &__tab {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-3) var(--op-space-4);
      border: none;
      background: transparent;
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
      cursor: pointer;
      white-space: nowrap;
      border-bottom: 2px solid transparent;
      margin-bottom: -2px;
      transition:
        color var(--op-transition),
        border-color var(--op-transition);

      &:hover {
        color: var(--op-primary);
      }

      &--active {
        color: var(--op-primary-dark);
        font-weight: var(--op-weight-bold);
        border-bottom-color: var(--op-primary);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: -2px;
      }
    }
  }

  // Paneles
  .af-panel {
    padding: var(--op-space-5);
  }

  .af-panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--op-space-2);
  }

  // Fila de filtros
  .af-filter-row {
    display: flex;
    align-items: flex-end;
    gap: var(--op-space-3);
    flex-wrap: wrap;
    margin-bottom: var(--op-space-4);

    &__field {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
      min-width: 200px;
      flex: 1;
    }

    &__actions {
      display: flex;
      gap: var(--op-space-2);
    }
  }

  // Tabla
  .af-table-wrap {
    overflow-x: auto;

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: -2px;
    }

    .table {
      border-collapse: collapse;
      width: 100%;

      th {
        background: var(--op-bg);
        color: var(--op-text-secondary);
        font-size: var(--op-font-xs);
        font-weight: var(--op-weight-bold);
        text-transform: uppercase;
        letter-spacing: 0.04em;
        padding: var(--op-space-2) var(--op-space-3);
        border-bottom: 2px solid var(--op-border);
      }

      td {
        padding: var(--op-space-2) var(--op-space-3);
        border-bottom: 1px solid var(--op-border);
        color: var(--op-text);
        font-size: var(--op-font-sm);
        vertical-align: middle;
      }

      caption {
        font-size: var(--op-font-sm);
        color: var(--op-text-secondary);
        margin-bottom: var(--op-space-2);
        text-align: left;
      }
    }
  }

  // Acción de fila
  .af-actions-cell {
    display: flex;
    gap: var(--op-space-1);
    justify-content: flex-end;
    align-items: center;
    white-space: nowrap;
  }

  // Fila hija del árbol de ubicaciones
  .af-child-row td:first-child {
    padding-left: var(--op-space-6);
    color: var(--op-text-secondary);
  }

  .af-child-indent {
    position: relative;

    &::before {
      content: "└ ";
      color: var(--op-border);
    }
  }

  // Formulario en modal
  .af-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
    padding: var(--op-space-2) 0;
  }

  .af-modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: var(--op-space-2);
  }

  // Título de card
  :global(.op-card-title) {
    font-size: var(--op-font-md);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0;
  }
</style>
