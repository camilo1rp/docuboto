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
  Workspace de expediente (T-10, RF-UI-05).

  Una vista con 5 pestañas internas:
    0 — Resumen       → metadatos + radicados vinculados
    1 — Índice (E15)  → IndiceResponse (JSON y descarga XML)
    2 — Anexos (E07)  → por radicado, cargados bajo demanda desde document-service
    3 — Flujo/Hist.   → eventos del expediente (hoja de ruta E02 §9)
    4 — Físico (E17)  → unidades de conservación + signatura

  Accesibilidad:
    - Patrón ARIA tablist/tab/tabpanel (WCAG 2.1 §4.1.2)
    - Navegación por teclado: flechas ← → entre tabs, Tab hacia el panel
    - Labels y roles ARIA en todos los elementos interactivos

  Acciones guiadas (cierre/transferencia):
    - Solo visibles si el usuario tiene el permiso y el estado lo permite
    - Siempre presentan un Modal de confirmación antes de ejecutar
    - El backend re-valida; la UI no es la única línea de defensa (ADR-013)
-->
<script>
  import { codigoError, mensajeError } from "$lib/utils/apiError.js";
  import { formatearFecha, formatearFechaHora } from "$lib/utils/fecha.js";
  import { nombreSerieTrd, vigenciaSerieTrd } from "$lib/utils/trd.js";
  import { invalidateAll } from "$app/navigation";
  import { tick } from "svelte";

  import Card from "$lib/components/ui/Card.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import { ACCESO_ACCION_LABELS } from "./acceso-labels.js";
  import Button from "$lib/components/ui/Button.svelte";
  import CadenaFirmaModal from "$lib/components/features/firmas/CadenaFirmaModal.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import VincularRadicadosDrawer from "$lib/components/features/expedientes/VincularRadicadosDrawer.svelte";
  import EditarMetadatosModal from "$lib/components/features/expedientes/EditarMetadatosModal.svelte";
  import DisposicionExpedienteDrawer from "$lib/components/features/disposicion/DisposicionExpedienteDrawer.svelte";
  import PublicacionExpedienteDrawer from "$lib/components/features/publicacion/PublicacionExpedienteDrawer.svelte";

  import { permissions, permissionLevels } from "$lib/stores/auth.js";
  import { notifySuccess, notifyError } from "$lib/stores/notifications.js";
  // Las acciones (cerrar/transferir, descargar XML/anexos) usan los endpoints locales
  // /expedientes/[id]/api/* que leen la cookie httpOnly en el servidor (D-05).
  // El token nunca alcanza el JS del cliente.

  /** @type {import('./$types').PageData} */
  export let data;

  // ---------------------------------------------------------------------------
  // Datos del servidor
  // ---------------------------------------------------------------------------
  $: expediente = data.expediente;

  // Nombre legible de la serie TRD, del detalle que el `load` ya resuelve.
  // `null` si no se pudo resolver: la ficha cae entonces al UUID, que es feo
  // pero cierto, en vez de dejar el dato en blanco.
  $: serieTrdNombre = nombreSerieTrd(data.trdSerieDetail);
  // Bajo qué VERSIÓN está el expediente y si esa versión todavía rige. No
  // bloquea nada ni esconde nada: el archivista sigue clasificando, foliando y
  // cerrando igual — pero sabiendo que el instrumento que ve fue reemplazado.
  $: serieTrdVigencia = vigenciaSerieTrd(data.trdSerieDetail);
  $: facetas = data.facetas;

  // ---------------------------------------------------------------------------
  // Autorización (espejo del RBAC; backend re-valida siempre)
  // ---------------------------------------------------------------------------
  // Wrapper local (no el `can()` del store, que usa `get()` internamente y
  // no dispararía las declaraciones `$:` de abajo): referencia $permissions
  // / $permissionLevels directamente para que Svelte las trate como
  // dependencia reactiva, igual que antes de A6.
  /** @param {string} p @param {number} [minCrud] */
  function can(p, minCrud) {
    if (minCrud === undefined) return $permissions.includes(p);
    return ($permissionLevels[p] ?? 0) >= minCrud;
  }

  // D-07: 'CERRAR_EXPEDIENTE' y 'TRANSFERIR_EXPEDIENTE' no existen en el catálogo
  // RBAC (migración 004). El único permiso de expediente disponible es
  // USUA_PERM_EXPEDIENTE ("Gestión de expedientes"). Ambas acciones requieren
  // ese permiso; el backend re-valida el estado y los permisos granulares en
  // cada endpoint.
  //
  // A6 (revisión UX, CERRADO): close/transfer/unlink_radicado exigen
  // `require_permission("USUA_PERM_EXPEDIENTE", min_crud=3)` en el backend
  // (verificado en archive-service/app/routers/expedientes.py líneas
  // 223/260/511) — antes la UI solo miraba presencia del permiso (cualquier
  // nivel, incluido "leer"), ofreciendo botones que el backend rechazaba con
  // 403. link_radicado / update_expediente / export / firmar_indice quedan
  // en min_crud=1 (default de `require_permission`) — no se tocan.
  $: canClose = can("USUA_PERM_EXPEDIENTE", 3) && expediente?.status === "open";
  $: canTransfer = can("USUA_PERM_EXPEDIENTE", 3) && expediente?.status === "closed";
  // Vincular (POST /radicados, min_crud=1) exige solo el expediente en
  // estado 'open' — cerrado/transferido responde 409 expediente_not_open.
  $: canLink = can("USUA_PERM_EXPEDIENTE") && expediente?.status === "open";
  // Acto de publicación de oficio (Ley 1712/2014 art. 12). Mismo permiso y nivel
  // que en el gemelo del radicado: publicar y clasificar son las dos caras de la
  // competencia sobre el régimen de acceso, y el backend exige
  // `PERM_RECLASIFICAR` con `min_crud=3`. No se condiciona por `status`: un
  // expediente cerrado o transferido sigue siendo difundible.
  $: canPublicar = can("PERM_RECLASIFICAR", 3);
  let showPublicacionDrawer = false;
  // Excluir (DELETE /radicados/{id}, min_crud=3) — ver nota A6 arriba.
  $: canUnlink = can("USUA_PERM_EXPEDIENTE", 3) && expediente?.status === "open";
  // Solo para el mensaje informativo "no admite vincular ni excluir" — no
  // gatea ninguna acción de escritura por sí solo.
  $: canLinkUnlink = canLink || canUnlink;
  $: canEditMetadata = can("USUA_PERM_EXPEDIENTE");
  $: canExport = can("USUA_PERM_EXPEDIENTE");
  // Disposición final (RF-MET-08/E04, Fase 4 cierre desfase API↔UI): de solo
  // lectura (se materializa al cerrar), visible con el mismo permiso que el
  // resto de la gestión del expediente.
  $: canVerDisposicion = can("USUA_PERM_EXPEDIENTE");
  // POST /indice/firmar exige USUA_PERM_EXPEDIENTE Y PERM_FIRMA, ambos en
  // min_crud=1 (default; routers/indice.py líneas 194-195) — no se tocan.
  $: canFirmarIndice = can("USUA_PERM_EXPEDIENTE") && can("PERM_FIRMA");

  // Cadena de firmantes SOBRE EL EXPEDIENTE (E06, RF-FIR-04), que no es lo
  // mismo que firmar su índice electrónico: aquello es el sello institucional
  // del instrumento, esto son varias personas comprometiéndose en orden sobre
  // el expediente —típicamente alrededor de su cierre—. El backend lo admitía y
  // lo sembraba el propio entorno de pruebas; lo que faltaba era la puerta.
  // Mismo par de permisos que firmar el índice, porque el router de cadenas
  // exige PERM_FIRMA y el objeto es un expediente.
  $: canAbrirCadena = can("USUA_PERM_EXPEDIENTE") && can("PERM_FIRMA");
  let showCadenaModal = false;

  // ---------------------------------------------------------------------------
  /** @param {any} err @param {string} fallback */
  function friendlyActionError(err, fallback) {
    if (codigoError(err) === "forbidden") {
      return "No tiene el nivel de permiso necesario para esta acción; consulte con el administrador de su entidad.";
    }
    return mensajeError(err, fallback);
  }

  // ---------------------------------------------------------------------------
  // Descarga de blob (export ZIP, anexo, XML del índice) — helper compartido
  // ---------------------------------------------------------------------------
  /**
   * Menor (revisión UX): un `<a>` que nunca se inserta en el DOM, con
   * `revokeObjectURL` disparado de forma síncrona justo después de `click()`,
   * puede abortar la descarga en Firefox (el navegador procesa el click de
   * forma asíncrona y la URL del blob ya pudo quedar revocada). Se inserta el
   * ancla en el DOM y se revoca en un `setTimeout(0)`, dando margen a que el
   * navegador tome el blob antes de liberarlo.
   * @param {Blob} blob
   * @param {string} filename
   */
  function triggerBlobDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  // ---------------------------------------------------------------------------
  // Pestañas (tablist ARIA)
  // ---------------------------------------------------------------------------
  const TABS = [
    { id: "resumen", label: "Contenido", icon: "fa-folder-open" },
    { id: "indice", label: "Índice electrónico", icon: "fa-list-ol" },
    { id: "foliado", label: "Foliado", icon: "fa-hashtag" },
    { id: "anexos", label: "Anexos", icon: "fa-paperclip" },
    { id: "flujo", label: "Flujo / Historial", icon: "fa-route" },
    { id: "preservacion", label: "Preservación", icon: "fa-box-archive" },
    { id: "acceso", label: "Acceso", icon: "fa-user-shield" },
    { id: "fisico", label: "Ubicación física", icon: "fa-archive" },
  ];
  let activeTab = "resumen";

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
  // Modales de acción guiada
  // ---------------------------------------------------------------------------
  let showCloseModal = false;
  let showTransferModal = false;
  let actionLoading = false;

  // Drawer de disposición final (solo lectura) — ver DisposicionExpedienteDrawer.svelte
  let showDisposicionDrawer = false;

  // I2 (revisión UX): `invalidateAll()` corría ANTES de cerrar el Modal —
  // al cerrarse, `Modal.svelte` intentaba reenfocar el botón "Cerrar
  // expediente"/"Transferir expediente", pero ese botón ya había salido del
  // DOM (canClose/canTransfer cambian con el nuevo status) y `focus()` sobre
  // un nodo desconectado no hace nada: el foco caía al <body>, justo tras un
  // acto irreversible con valor legal. Se cierra el Modal ANTES de invalidar
  // y se mueve el foco explícitamente a un ancla estable (patrón A7, igual
  // que `handleExcluir`).
  async function handleClose() {
    actionLoading = true;
    try {
      const res = await fetch(`/expedientes/${expediente.id}/api/close`, { method: "POST" });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess(`Expediente ${expediente.code} cerrado correctamente.`);
      showCloseModal = false;
      await invalidateAll();
      await tick();
      document.getElementById("exp-workspace-title")?.focus();
    } catch (err) {
      notifyError(friendlyActionError(err, "No se pudo cerrar el expediente."));
      showCloseModal = false;
    } finally {
      actionLoading = false;
    }
  }

  async function handleTransfer() {
    actionLoading = true;
    try {
      const res = await fetch(`/expedientes/${expediente.id}/api/transfer`, { method: "POST" });
      const result = await res.json();
      if (!res.ok) throw result;
      notifySuccess(`Expediente ${expediente.code} transferido correctamente.`);
      showTransferModal = false;
      await invalidateAll();
      await tick();
      document.getElementById("exp-workspace-title")?.focus();
    } catch (err) {
      notifyError(friendlyActionError(err, "No se pudo transferir el expediente."));
      showTransferModal = false;
    } finally {
      actionLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Vincular / excluir radicados (Fase 2 — capacidad central del módulo)
  // ---------------------------------------------------------------------------
  let showVincularDrawer = false;
  $: linkedIds = new Set((expediente?.radicados ?? []).map((r) => r.radicado_id));

  let showExcluirModal = false;
  /** @type {{radicado_id: string, tracking_number: string} | null} */
  let excluirTarget = null;
  let excluirCausal = "";
  let excluirCausalError = "";
  let excluirLoading = false;

  function openExcluirModal(rad) {
    excluirTarget = rad;
    excluirCausal = "";
    excluirCausalError = "";
    showExcluirModal = true;
  }

  function closeExcluirModal() {
    showExcluirModal = false;
    excluirTarget = null;
    excluirCausal = "";
    excluirCausalError = "";
  }

  // M2 (revisión UX): antes el botón "Confirmar exclusión" simplemente
  // quedaba deshabilitado sin motivo sin causal — un lector de pantalla
  // anunciaba un botón inerte sin explicar por qué. Ahora se valida al
  // intentar confirmar, con un error asociado (aria-describedby vía
  // FormField) que SÍ se anuncia, y el foco vuelve al campo.
  function validateExcluirCausal() {
    if (!excluirCausal.trim()) {
      excluirCausalError = "El motivo de la exclusión es obligatorio.";
      return false;
    }
    if (excluirCausal.trim().length > 500) {
      excluirCausalError = "El motivo no puede superar 500 caracteres.";
      return false;
    }
    excluirCausalError = "";
    return true;
  }

  async function handleExcluir() {
    if (!excluirTarget) return;
    if (!validateExcluirCausal()) {
      document.getElementById("excluir-causal")?.focus();
      return;
    }
    excluirLoading = true;
    try {
      const res = await fetch(
        `/expedientes/${expediente.id}/api/radicados/${excluirTarget.radicado_id}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ causal: excluirCausal.trim() }),
        },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw data;
      notifySuccess(`Radicado ${excluirTarget.tracking_number} excluido del expediente.`);
      closeExcluirModal();
      resetIndiceInspectionState();
      await invalidateAll();
      // A7 (revisión UX): tras un acto irreversible, el foco NO debe caer al
      // <body> porque el botón "Excluir" de esa fila desapareció del DOM. Se
      // enfoca un ancla estable: el encabezado de la tarjeta de radicados.
      await tick();
      document.getElementById("radicados-vinculados-heading")?.focus();
    } catch (err) {
      notifyError(friendlyActionError(err, "No se pudo excluir el radicado."));
    } finally {
      excluirLoading = false;
    }
  }

  // A5 (revisión UX): vincular/excluir regeneran el índice (nueva huella) —
  // si el usuario ya había verificado o abierto el historial de versiones
  // ANTES del cambio, esos paneles debían quedar en "idle" para no seguir
  // afirmando un estado que el cambio de índice ya volvió obsoleto.
  function resetIndiceInspectionState() {
    indiceVerifyState = { status: "idle", data: null, errorMsg: null };
    indiceVersionsState = { status: "idle", items: [], total: 0, errorMsg: null };
  }

  async function handleVinculado() {
    resetIndiceInspectionState();
    await invalidateAll();
  }

  // ---------------------------------------------------------------------------
  // Editar metadatos (name/description/trd_serie_id — ver contrato verificado
  // en EditarMetadatosModal.svelte: `metadata` NO es un campo de este PATCH)
  // ---------------------------------------------------------------------------
  let showMetadataModal = false;

  async function handleMetadataSaved() {
    await invalidateAll();
  }

  // ---------------------------------------------------------------------------
  // Exportar ZIP (índice + manifiesto + anexos)
  // ---------------------------------------------------------------------------
  let exporting = false;

  async function handleExport() {
    exporting = true;
    try {
      const res = await fetch(`/expedientes/${expediente.id}/api/export`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw err;
      }
      const blob = await res.blob();
      triggerBlobDownload(blob, `expediente_${expediente.code}.zip`);
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo exportar el expediente."));
    } finally {
      exporting = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Índice electrónico — versiones / verificación / firma (valor legal, E06)
  // ---------------------------------------------------------------------------
  let indiceVersionsState = { status: "idle", items: [], total: 0, errorMsg: null };
  let indiceVerifyState = { status: "idle", data: null, errorMsg: null };
  let showFirmarModal = false;
  let firmarLoading = false;

  async function loadIndiceVersions() {
    if (indiceVersionsState.status === "loading") return;
    indiceVersionsState = { status: "loading", items: [], total: 0, errorMsg: null };
    try {
      const res = await fetch(`/expedientes/${expediente.id}/api/indice/versions`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw data;
      const items = data.items ?? [];
      indiceVersionsState = {
        status: "loaded",
        items,
        // A2 (revisión UX): el badge/pie deben reflejar el TOTAL real
        // (`X-Total-Count`), no `items.length` (esta vista pide una sola
        // página de 20) — si no, un expediente con 30 versiones muestra "20"
        // y las 10 restantes son inalcanzables sin decirlo.
        total: Number(data.total ?? items.length),
        errorMsg: null,
      };
    } catch (err) {
      indiceVersionsState = {
        status: "error",
        items: [],
        total: 0,
        errorMsg: mensajeError(err, "No se pudieron cargar las versiones del índice."),
      };
    }
  }

  async function loadIndiceVerify() {
    if (indiceVerifyState.status === "loading") return;
    indiceVerifyState = { status: "loading", data: null, errorMsg: null };
    try {
      const res = await fetch(`/expedientes/${expediente.id}/api/indice/verify`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw data;
      indiceVerifyState = { status: "loaded", data, errorMsg: null };
    } catch (err) {
      indiceVerifyState = {
        status: "error",
        data: null,
        errorMsg: mensajeError(err, "No se pudo verificar el índice."),
      };
    }
  }

  async function handleFirmarIndice() {
    firmarLoading = true;
    try {
      const res = await fetch(`/expedientes/${expediente.id}/api/indice/firmar`, {
        method: "POST",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw data;
      notifySuccess("Índice electrónico firmado.");
      showFirmarModal = false;
      // A5 (revisión UX): firmar SÍ cambia el índice (pasa a "firmado") pero
      // antes esto no limpiaba `indiceVerifyState`/`indiceVersionsState` — si
      // el usuario había verificado ANTES de firmar, el panel de
      // verificación seguía diciendo "Firma: ausente, no válida" junto al
      // chip que ya decía "Firmado". Sobre un artefacto con valor legal eso
      // es la peor forma de mostrar "vacío"/desactualizado como si fuera la
      // verdad vigente: se resetea a idle para forzar una relectura explícita.
      resetIndiceInspectionState();
      await invalidateAll();
      // I2 (revisión UX): firmar retira el botón "Firmar índice" del DOM
      // (`!indiceFirmado` pasa a false) — Modal.svelte reenfocaría un nodo
      // desconectado. Se mueve el foco al encabezado de la tarjeta del
      // índice, ancla estable (mismo patrón que cerrar/transferir).
      await tick();
      document.getElementById("indice-electronico-heading")?.focus();
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo firmar el índice."));
    } finally {
      firmarLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Foliado — carga perezosa al activar la pestaña
  // ---------------------------------------------------------------------------
  let foliadoState = { status: "idle", data: null, errorMsg: null };

  async function loadFoliado() {
    if (foliadoState.status !== "idle") return;
    foliadoState = { status: "loading", data: null, errorMsg: null };
    try {
      const res = await fetch(`/expedientes/${expediente.id}/api/foliado`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw data;
      foliadoState = { status: "loaded", data, errorMsg: null };
    } catch (err) {
      foliadoState = {
        status: "error",
        data: null,
        errorMsg: mensajeError(err, "No se pudo cargar la foliación."),
      };
    }
  }

  // ---------------------------------------------------------------------------
  // Preservación (AIP + eventos PREMIS) — carga perezosa, en paralelo
  // ---------------------------------------------------------------------------
  // Crítico 1 (revisión UX): antes `errorMsg` solo se llenaba cuando las DOS
  // peticiones (AIP y eventos) fallaban a la vez. Si una sola fallaba (p. ej.
  // un 503 puntual del gateway), la otra rama entregaba `null`/`[]` y la
  // vista mostraba una afirmación categórica de "vacío" ("Sin AIP
  // empaquetado", "Sin eventos de preservación") — un fallo de red se leía
  // como "este expediente no tiene preservación". En preservación/auditoría
  // de accesos eso puede llevar a concluir erróneamente que falta un AIP que
  // SÍ existe, o que nadie ha leído un expediente reservado. Cada faceta
  // (AIP, eventos) ahora lleva su PROPIO {status, errorMsg}; la plantilla
  // elige "no se pudo cargar" vs. "está vacío" según la promesa de ESA
  // faceta, nunca derivado de un `catch` compartido.
  let preservacionState = {
    status: "idle", // idle|loading|loaded — solo gatilla loadPreservacion() una vez
    aip: { status: "idle", data: null, errorMsg: null },
    eventos: { status: "idle", items: [], errorMsg: null },
  };

  async function loadPreservacion() {
    if (preservacionState.status !== "idle") return;
    preservacionState = {
      status: "loading",
      aip: { status: "loading", data: null, errorMsg: null },
      eventos: { status: "loading", items: [], errorMsg: null },
    };
    // AREA-12-04: primero el estado de INSTANCIA. Con la preservación apagada
    // el 404 neutro del AIP («sin AIP, o sin visibilidad») hacía pasar por
    // normal que nada se empaqueta jamás. Solo `enabled === false` lo apaga: si
    // la consulta falla o no responde con ese campo, se sigue como antes (el
    // AIP decide) — un fallo del estado no inventa «desactivada».
    const estado = await fetch("/expedientes/api/preservacion-estado")
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
    const desactivada = estado?.enabled === false;

    const [aipRes, eventosRes] = await Promise.allSettled([
      desactivada
        ? Promise.reject({ desactivada: true })
        : fetch(`/expedientes/${expediente.id}/api/aip`).then(async (r) => {
            const d = await r.json().catch(() => ({}));
            // C1 (revisión UX): el proxy retransmite el status real del backend
            // (`res.status`) — se conserva aquí para distinguir el 404 NEUTRO
            // (sin AIP / sin visibilidad, ver archive_service.py get_aip) de un
            // fallo real (5xx, red). Antes CUALQUIER !ok pasaba por `error`, así
            // que el caso normal (todavía no hay AIP) se presentaba como fallo
            // del sistema y la rama honesta "empty" quedaba inalcanzable.
            if (!r.ok) throw { ...d, status: r.status };
            return d;
          }),
      fetch(`/expedientes/${expediente.id}/api/preservacion-eventos`).then(async (r) => {
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw d;
        return d;
      }),
    ]);
    preservacionState = {
      status: "loaded",
      aip:
        aipRes.status === "fulfilled"
          ? { status: "loaded", data: aipRes.value, errorMsg: null }
          : aipRes.reason?.desactivada
            ? { status: "desactivada", data: null, errorMsg: null }
            : aipRes.reason?.status === 404
              ? { status: "empty", data: null, errorMsg: null }
              : {
                  status: "error",
                  data: null,
                  errorMsg: mensajeError(
                    aipRes.reason,
                    "No se pudo cargar el paquete de preservación (AIP).",
                  ),
                },
      eventos:
        eventosRes.status === "fulfilled"
          ? {
              status: eventosRes.value?.length ? "loaded" : "empty",
              items: eventosRes.value ?? [],
              errorMsg: null,
            }
          : {
              status: "error",
              items: [],
              errorMsg: mensajeError(
                eventosRes.reason,
                "No se pudieron cargar los eventos de preservación.",
              ),
            },
    };
  }

  // ---------------------------------------------------------------------------
  // Acceso (historial de lecturas + roles autorizados) — carga perezosa
  // ---------------------------------------------------------------------------
  // Misma corrección del Crítico 1: historial y roles llevan estado propio.
  let accesoState = {
    status: "idle",
    historial: { status: "idle", items: [], total: 0, errorMsg: null },
    roles: { status: "idle", items: [], errorMsg: null },
  };

  async function loadAcceso() {
    if (accesoState.status !== "idle") return;
    accesoState = {
      status: "loading",
      historial: { status: "loading", items: [], total: 0, errorMsg: null },
      roles: { status: "loading", items: [], errorMsg: null },
    };
    const [historialRes, rolesRes] = await Promise.allSettled([
      fetch(`/expedientes/${expediente.id}/api/historial-acceso`).then(async (r) => {
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw d;
        return d;
      }),
      fetch(`/expedientes/${expediente.id}/api/roles-autorizados`).then(async (r) => {
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw d;
        return d;
      }),
    ]);
    accesoState = {
      status: "loaded",
      historial:
        historialRes.status === "fulfilled"
          ? {
              status: (historialRes.value.items ?? []).length ? "loaded" : "empty",
              items: historialRes.value.items ?? [],
              // A2: total real (`X-Total-Count`), no el tamaño de esta única
              // página pedida — ver comentario homólogo en loadIndiceVersions.
              total: Number(historialRes.value.total ?? (historialRes.value.items ?? []).length),
              errorMsg: null,
            }
          : {
              status: "error",
              items: [],
              total: 0,
              errorMsg: mensajeError(
                historialRes.reason,
                "No se pudo cargar el historial de acceso.",
              ),
            },
      roles:
        rolesRes.status === "fulfilled"
          ? {
              status: (rolesRes.value ?? []).length ? "loaded" : "empty",
              items: rolesRes.value ?? [],
              errorMsg: null,
            }
          : {
              status: "error",
              items: [],
              errorMsg: mensajeError(
                rolesRes.reason,
                "No se pudo cargar la matriz de grupos autorizados.",
              ),
            },
    };
  }

  // Carga perezosa al activar cada pestaña por primera vez.
  $: if (activeTab === "foliado") loadFoliado();
  $: if (activeTab === "preservacion") loadPreservacion();
  $: if (activeTab === "acceso") loadAcceso();

  // ---------------------------------------------------------------------------
  // Migración de formato contra la obsolescencia (E10, RF-PRE-04)
  // ---------------------------------------------------------------------------
  // Produce una COPIA DE PRESERVACIÓN en PDF/A y **deja el original intacto**
  // (Decreto 2609/2012 art. 32): la norma manda conservar el original y su
  // procedencia, no reemplazarlo. Por eso el aviso de éxito dice que se creó
  // una copia, no que el anexo «se convirtió» — que es lo que la gente
  // entendería si no se lo dijéramos.
  let migrandoFileId = null;

  async function migrarAPdfa(anexo) {
    migrandoFileId = anexo.file_id;
    try {
      const res = await fetch(`/expedientes/${expediente.id}/api/migrar-formato`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_id: anexo.file_id, perfil: "2b" }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        // El 503 del convertidor ausente se muestra tal cual: es un problema
        // de instalación, no de este documento, y colapsarlo mandaría a
        // depurar el PDF equivocado.
        notifyError(mensajeError(body, `Error ${res.status}`));
        return;
      }
      if (body.estado === "migrado") {
        notifySuccess(
          `Copia de preservación creada en ${body.formato_destino}. ` +
            "El original se conserva intacto.",
        );
      } else if (body.estado === "no_verificado") {
        notifyError(
          "Se convirtió, pero no hay validador PDF/A para comprobarlo: la copia " +
            "NO se guardó como copia de preservación.",
        );
      } else {
        notifyError(`No se pudo migrar: ${body.motivo ?? "conversión fallida"}`);
      }
      // Los eventos PREMIS del expediente cambian con la migración.
      preservacionState = { ...preservacionState, status: "idle" };
      if (activeTab === "preservacion") loadPreservacion();
    } catch {
      notifyError("No se pudo conectar con el servidor de preservación.");
    } finally {
      migrandoFileId = null;
    }
  }

  // ---------------------------------------------------------------------------
  // Descarga de anexo (mediada por document-service — E07, RF-SEG-08, D-02)
  // ---------------------------------------------------------------------------
  /**
   * Descarga el anexo vía el endpoint local, que retransmite el streaming
   * mediado por document-service (GET /api/v1/documents/{document_id}/anexos/{file_id}/download).
   * Necesita el `documentId` del RADICADO dueño del anexo (no el expediente):
   * document-service aplica el no-read-up contra el nivel_seguridad del radicado.
   * El endpoint local /expedientes/[id]/api/download/[documentId]/[fileId] lee
   * la cookie httpOnly en el servidor.
   * @param {string} documentId - radicado_id dueño del anexo
   * @param {string} fileId
   * @param {string} filename
   */
  async function handleDownloadAnexo(documentId, fileId, filename) {
    try {
      const res = await fetch(`/expedientes/${expediente.id}/api/download/${documentId}/${fileId}`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw err;
      }
      const blob = await res.blob();
      triggerBlobDownload(blob, filename);
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo descargar el anexo."));
    }
  }

  // ---------------------------------------------------------------------------
  // Descarga XML del índice
  // ---------------------------------------------------------------------------
  let xmlDownloading = false;

  async function handleDownloadXml() {
    xmlDownloading = true;
    try {
      // El endpoint local /expedientes/[id]/api/indice-xml lee la cookie httpOnly.
      const res = await fetch(`/expedientes/${expediente.id}/api/indice-xml`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw err;
      }
      const blob = await res.blob();
      triggerBlobDownload(blob, `indice_${expediente.code}.xml`);
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo obtener el XML del índice."));
    } finally {
      xmlDownloading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Helpers de formato
  // ---------------------------------------------------------------------------
  /** @param {string|null|undefined} iso */
  function fmtDate(iso) {
    return formatearFecha(iso);
  }

  /** @param {string|null|undefined} iso */
  function fmtDatetime(iso) {
    if (!iso) return "—";
    return formatearFechaHora(iso);
  }

  /** @param {number|null|undefined} bytes */
  function fmtBytes(bytes) {
    if (!bytes) return "—";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  const STATUS_LABELS = {
    open: "Abierto",
    closed: "Cerrado",
    transferred: "Transferido",
  };
  $: statusLabel = STATUS_LABELS[expediente?.status] ?? expediente?.status;
  // M6 (revisión UX): "closed" es el curso normal del ciclo de vida de un
  // expediente (se cerró como corresponde), NO un fallo — mapearlo al rojo
  // de "error" (mapeo por defecto de StatusChip) lo hace leerse como algo
  // que salió mal. Se resuelve localmente (sin tocar StatusChip.svelte, que
  // es compartido con otras pantallas fuera de esta zona) pidiendo
  // explícitamente una variante neutra/informativa — mismo patrón ya usado
  // en /expedientes (listado).
  const STATUS_CHIP_MAP = { open: "active", closed: "neutral", transferred: "info" };
  $: statusChipVariant = STATUS_CHIP_MAP[expediente?.status] ?? "neutral";

  // M5 (revisión UX): valores crudos del backend (enums en inglés, p. ej.
  // "success"/"completo") no deben mostrarse literalmente en una interfaz en
  // español — se traducen con un diccionario y se cae a una versión
  // "humanizada" del valor crudo (guiones bajos → espacios, capitalizado)
  // para no ocultar un valor nuevo que el diccionario todavía no conozca.
  /** @param {string|null|undefined} value */
  function humanize(value) {
    if (!value) return "—";
    return String(value)
      .replace(/_/g, " ")
      .replace(/^./, (c) => c.toUpperCase());
  }
  const AIP_ESTADO_LABELS = {
    completo: "Completo",
    pendiente: "Pendiente",
    en_proceso: "En proceso",
    error: "Error",
  };
  /** @param {string} estado */
  function aipEstadoLabel(estado) {
    return AIP_ESTADO_LABELS[estado] ?? humanize(estado);
  }
  const PREMIS_RESULTADO_LABELS = {
    success: "Correcto",
    error: "Con error",
    warning: "Con advertencia",
  };
  /** @param {string} resultado */
  function premisResultadoLabel(resultado) {
    return PREMIS_RESULTADO_LABELS[resultado] ?? humanize(resultado);
  }
  /** @param {string} action */
  function accesoAccionLabel(action) {
    return ACCESO_ACCION_LABELS[action] ?? humanize(action);
  }

  // ---------------------------------------------------------------------------
  // Anexos por radicado (E07) — carga perezosa, panel Anexos
  // ---------------------------------------------------------------------------
  // `expediente.radicados[]` (archive-service) NO trae los anexos embebidos —
  // solo tracking_number/added_at/radicado_id. Los anexos viven en
  // document-service; se piden UNO A UNO por radicado, al expandirlo, vía el
  // proxy local /expedientes/[id]/api/anexos/[documentId] (D-05). Perezoso en
  // vez de eager: un expediente puede tener decenas de radicados y no todos
  // se van a inspeccionar — pedir los anexos de todos al abrir la vista sería
  // N llamadas normalmente descartadas.
  //
  // Estado por radicado_id: { status: 'idle'|'loading'|'loaded'|'empty'|'error',
  //                            items: Anexo[], errorMsg: string|null }
  /** @type {Record<string, {status: string, items: any[], errorMsg: string|null}>} */
  let anexosState = {};
  /** @type {Set<string>} */
  let expandedRadicados = new Set();

  /** @param {string} documentId - radicado_id */
  async function loadAnexos(documentId) {
    const current = anexosState[documentId];
    if (current && current.status !== "error") return; // ya cargado o cargando
    anexosState = {
      ...anexosState,
      [documentId]: { status: "loading", items: [], errorMsg: null },
    };
    try {
      const res = await fetch(`/expedientes/${expediente.id}/api/anexos/${documentId}`);
      if (res.status === 404) {
        anexosState = {
          ...anexosState,
          [documentId]: {
            status: "error",
            items: [],
            errorMsg: "Los anexos de este radicado no están disponibles.",
          },
        };
        return;
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        anexosState = {
          ...anexosState,
          [documentId]: {
            status: "error",
            items: [],
            errorMsg: mensajeError(err, "No se pudieron cargar los anexos."),
          },
        };
        return;
      }
      const items = await res.json();
      anexosState = {
        ...anexosState,
        [documentId]: { status: items.length ? "loaded" : "empty", items, errorMsg: null },
      };
    } catch {
      anexosState = {
        ...anexosState,
        [documentId]: {
          status: "error",
          items: [],
          errorMsg: "No se pudo conectar para cargar los anexos.",
        },
      };
    }
  }

  /** @param {string} documentId - radicado_id */
  function toggleRadicadoAnexos(documentId) {
    const next = new Set(expandedRadicados);
    if (next.has(documentId)) {
      next.delete(documentId);
    } else {
      next.add(documentId);
      loadAnexos(documentId);
    }
    expandedRadicados = next;
  }
</script>

<svelte:head>
  <title>Expediente {expediente?.code ?? ""} — OrpycaMCP</title>
</svelte:head>

<!-- =========================================================================
     Workspace
     ========================================================================= -->
<div class="exp-workspace">
  <!-- Cabecera -->
  <div class="exp-workspace__header">
    <div class="exp-workspace__header-info">
      <nav aria-label="Ubicación" class="exp-workspace__breadcrumb">
        <a href="/bandeja">Bandeja</a>
        <span aria-hidden="true"> / </span>
        <a href="/expedientes">Expedientes</a>
        <span aria-hidden="true"> / </span>
        <span aria-current="page">{expediente?.code}</span>
      </nav>

      <h1 id="exp-workspace-title" class="exp-workspace__title" tabindex="-1">
        {expediente?.name}
        <StatusChip status={statusChipVariant} label={statusLabel} dot />
      </h1>

      {#if expediente?.description}
        <p class="exp-workspace__desc">{expediente.description}</p>
      {/if}
    </div>

    <div class="exp-workspace__actions" role="group" aria-label="Acciones del expediente">
      {#if canEditMetadata}
        <Button variant="ghost" on:click={() => (showMetadataModal = true)}>
          <i class="fas fa-pen" aria-hidden="true"></i> Editar metadatos
        </Button>
      {/if}
      {#if canExport}
        <Button
          variant="secondary"
          on:click={handleExport}
          disabled={exporting}
          loading={exporting}
        >
          <i class="fas fa-file-zipper" aria-hidden="true"></i> Exportar ZIP
        </Button>
      {/if}
      {#if canVerDisposicion}
        <Button variant="ghost" on:click={() => (showDisposicionDrawer = true)}>
          <i class="fas fa-box-archive" aria-hidden="true"></i> Disposición final
        </Button>
      {/if}
      {#if canPublicar}
        <Button variant="ghost" on:click={() => (showPublicacionDrawer = true)}>
          <i class="fas fa-bullhorn" aria-hidden="true"></i> Publicación
        </Button>
      {/if}
      {#if canClose}
        <Button variant="secondary" on:click={() => (showCloseModal = true)}>
          <i class="fas fa-lock" aria-hidden="true"></i> Cerrar expediente
        </Button>
      {/if}
      {#if canAbrirCadena}
        <Button variant="ghost" on:click={() => (showCadenaModal = true)}>
          <i class="fas fa-users" aria-hidden="true"></i> Cadena de firmas
        </Button>
      {/if}
      {#if canTransfer}
        <Button variant="secondary" on:click={() => (showTransferModal = true)}>
          <i class="fas fa-exchange-alt" aria-hidden="true"></i> Transferir expediente
        </Button>
      {/if}
    </div>
  </div>

  <!-- Tablist -->
  <div class="exp-tabs" role="tablist" aria-label="Facetas del expediente">
    {#each TABS as tab (tab.id)}
      <button
        id="tab-{tab.id}"
        class="exp-tabs__tab"
        class:exp-tabs__tab--active={activeTab === tab.id}
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
  <div class="exp-workspace__panels">
    <!-- -----------------------------------------------------------------------
         Panel 0 — Resumen
         ----------------------------------------------------------------------- -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="panel-resumen"
      role="tabpanel"
      aria-labelledby="tab-resumen"
      hidden={activeTab !== "resumen"}
      tabindex="0"
      class="exp-panel"
    >
      <div class="exp-panel__grid">
        <!-- Metadatos principales -->
        <Card>
          <h2 slot="header" class="op-card-title">Metadatos del expediente</h2>
          <dl class="exp-meta-list">
            <div class="exp-meta-list__item">
              <dt>Código</dt>
              <dd>{expediente?.code ?? "—"}</dd>
            </div>
            <div class="exp-meta-list__item">
              <dt>Estado</dt>
              <dd>
                <StatusChip status={statusChipVariant} label={statusLabel} dot />
              </dd>
            </div>
            <div class="exp-meta-list__item">
              <dt>Fecha apertura</dt>
              <dd>{fmtDate(expediente?.opened_at)}</dd>
            </div>
            {#if expediente?.closed_at}
              <div class="exp-meta-list__item">
                <dt>Fecha cierre</dt>
                <dd>{fmtDate(expediente.closed_at)}</dd>
              </div>
            {/if}
            <div class="exp-meta-list__item">
              <dt>Actualizado</dt>
              <dd>{fmtDate(expediente?.updated_at)}</dd>
            </div>
            {#if expediente?.trd_serie_id}
              <!--
                La serie se nombra, no se identifica por su UUID. El servidor ya
                resuelve la serie completa (faceta 6 del `load`, con su propia
                llamada a `/trd/{id}`) y su comentario dice para qué: «no solo un
                UUID crudo». Ese dato llegaba únicamente al drawer de Disposición
                final, y la ficha —lo primero que se ve al abrir el expediente—
                seguía pintando `83a44878-d169-…`, que quien clasifica no puede
                buscar, ni citar, ni reconocer. El dato estaba cargado y el sitio
                más visible no lo leía.

                Si la resolución falla (serie inactiva, servicio caído) se
                muestra el UUID: es feo, pero es el identificador real y permite
                pedir ayuda con él. Callarlo sería peor.
              -->
              <div class="exp-meta-list__item">
                <dt>Serie TRD</dt>
                {#if serieTrdNombre}
                  <!--
                    Con la VERSIÓN a la vista y, cuando el instrumento ya no
                    rige, dicho con todas las letras. `EXP-2026-0001` apuntaba a
                    la v1 derogada mientras la ficha mostraba «DEM-100 —
                    Derechos de Petición» a secas: quien clasificaba creía estar
                    bajo el instrumento vigente.

                    No se bloquea el trabajo — la decisión es del archivista, y
                    el cierre además re-resuelve la serie por su código y salta
                    a la versión vigente. Lo que cambia es que ahora lo sabe
                    ANTES, no al leer el asiento después.
                  -->
                  <dd>
                    {serieTrdVigencia.etiqueta ?? serieTrdNombre}
                    {#if serieTrdVigencia.estadoTexto && !serieTrdVigencia.rige}
                      <!--
                        El `title` va en el <span> y no en <StatusChip>: el
                        componente solo declara `status`, `label` y `dot` y no
                        reenvía `$$restProps`, así que un atributo de más se
                        perdería sin decir nada.
                      -->
                      <span
                        title="Esta versión del instrumento ya no rige. Al cerrar, el expediente se re-resolverá a la versión vigente de {data
                          .trdSerieDetail?.code ?? 'la serie'}."
                      >
                        <StatusChip status="warning" label={serieTrdVigencia.estadoTexto} />
                      </span>
                    {/if}
                  </dd>
                {:else}
                  <dd class="exp-meta-list__code" title="No se pudo resolver el nombre de la serie">
                    {expediente.trd_serie_id}
                  </dd>
                {/if}
              </div>
            {/if}
            {#if expediente?.disposition}
              <div class="exp-meta-list__item">
                <dt>Disposición</dt>
                <dd>
                  {expediente.disposition.disposition_label ??
                    expediente.disposition.disposition_code ??
                    "—"}
                  {#if expediente.disposition.fin_archivo_central}
                    <span class="exp-meta-list__sub">
                      hasta {fmtDate(expediente.disposition.fin_archivo_central)}
                    </span>
                  {/if}
                </dd>
              </div>
            {/if}
          </dl>

          {#if expediente?.metadata && Object.keys(expediente.metadata).length}
            <!-- M12 (revisión UX): estos campos vienen del json_schema de la
                 plantilla de la serie TRD asignada al crear el expediente —
                 el PATCH de este expediente (`ExpedienteUpdate`, `extra="forbid"`)
                 NO acepta el campo `metadata`, así que no se ofrecen en el modal
                 de edición. Antes esto no se explicaba: se veían como
                 "Metadatos adicionales" sin más, y el usuario no tenía forma de
                 saber por qué no podía cambiarlos ahí. -->
            <h3 class="exp-section-subtitle">
              Metadatos de la serie TRD (solo lectura — se definen al crear el expediente)
            </h3>
            <dl class="exp-meta-list">
              {#each Object.entries(expediente.metadata) as [k, v] (k)}
                <div class="exp-meta-list__item">
                  <dt>{k}</dt>
                  <dd>{String(v)}</dd>
                </div>
              {/each}
            </dl>
          {/if}
        </Card>

        <!-- Radicados vinculados -->
        <Card>
          <!-- A7: ancla de foco estable tras excluir un radicado — el botón
               "Excluir" de la fila desaparece del DOM al recargar, así que el
               foco no puede volver ahí; se enfoca este encabezado en su lugar. -->
          <h2 id="radicados-vinculados-heading" slot="header" class="op-card-title" tabindex="-1">
            Radicados vinculados
            <span class="exp-count-badge">{expediente?.radicados?.length ?? 0}</span>
            {#if canLink}
              <Button
                variant="primary"
                size="sm"
                icon="fas fa-link"
                on:click={() => (showVincularDrawer = true)}
              >
                Vincular radicados
              </Button>
            {/if}
          </h2>
          {#if !canLinkUnlink && expediente?.status !== "open" && can("USUA_PERM_EXPEDIENTE")}
            <p class="exp-panel__info">
              <i class="fas fa-info-circle" aria-hidden="true"></i>
              Un expediente {statusLabel?.toLowerCase()} no admite vincular ni excluir radicados.
            </p>
          {/if}
          {#if !expediente?.radicados?.length}
            <EmptyState
              icon="fas fa-inbox"
              title="Sin radicados"
              description="Este expediente no tiene radicados vinculados todavía."
            >
              {#if canLink}
                <Button variant="primary" size="sm" on:click={() => (showVincularDrawer = true)}>
                  Vincular el primer radicado
                </Button>
              {/if}
            </EmptyState>
          {:else}
            <div class="exp-radicado-list" role="list" aria-label="Radicados del expediente">
              {#each expediente.radicados as rad (rad.tracking_number)}
                <div class="exp-radicado-list__item" role="listitem">
                  <a
                    href="/bandeja?q={rad.tracking_number}"
                    class="exp-radicado-list__number"
                    aria-label="Ver radicado {rad.tracking_number}"
                  >
                    {rad.tracking_number}
                  </a>
                  <span class="exp-radicado-list__date">{fmtDate(rad.added_at)}</span>
                  {#if canUnlink}
                    <button
                      type="button"
                      class="exp-action-link exp-action-link--danger"
                      aria-label="Excluir {rad.tracking_number} del expediente"
                      on:click={() => openExcluirModal(rad)}
                    >
                      <i class="fas fa-link-slash" aria-hidden="true"></i> Excluir
                    </button>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
        </Card>
      </div>
    </div>

    <!-- -----------------------------------------------------------------------
         Panel 1 — Índice electrónico E15
         ----------------------------------------------------------------------- -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="panel-indice"
      role="tabpanel"
      aria-labelledby="tab-indice"
      hidden={activeTab !== "indice"}
      tabindex="0"
      class="exp-panel"
    >
      {#if facetas.indice.error}
        <Card>
          <EmptyState
            icon="fas fa-exclamation-circle"
            title="No se pudo cargar el índice"
            description={facetas.indice.error.detail}
          />
        </Card>
      {:else if !facetas.indice.data}
        <!-- C2 (revisión UX): el 404 de GET /expedientes/{id}/indice es NEUTRO
             (no-read-up, RF-SEG-08, igual que get_expediente) — cubre tanto
             "no se ha generado todavía" como "usted no tiene visibilidad
             sobre este expediente". No se puede afirmar categóricamente que
             el índice "no existe": el backend deliberadamente no distingue. -->
        <EmptyState
          icon="fas fa-list-ol"
          title="Índice no disponible"
          description="El índice electrónico no ha sido generado para este expediente, o usted no tiene visibilidad sobre él."
        />
      {:else}
        <!--
          Anotado a propósito: `IndiceElectronico` (en `$lib/types/api.js`)
          hace que `svelte-check` valide los campos que lee la tabla de abajo.
          Sin el tipo, leer uno que no existe —`fecha_archivado` en vez de
          `fecha_incorporacion`— no falla en ningún sitio: la columna sale
          vacía y el «—» la disfraza de dato no declarado.
        -->
        {@const indice = /** @type {import('$lib/types/api.js').IndiceElectronico} */ (
          facetas.indice.data
        )}
        {@const indiceFirmado = indice.estado === "firmado"}
        <!-- "Apariencia de certificado" (Sistema de Diseño v1.0): el índice y su
             firma tienen valor legal (E06/E15) — se presentan con un marco propio
             en vez de mezclarse con el resto de tarjetas de la vista. -->
        <Card>
          <h2 id="indice-electronico-heading" slot="header" class="op-card-title" tabindex="-1">
            <i class="fas fa-file-shield" aria-hidden="true"></i>
            Índice electrónico
            <span class="exp-indice-meta">
              <!--
                `num_documentos` cuenta SOLO los vigentes; `items` trae también
                los excluidos, porque el índice tiene que dejar el hueco
                trazable a la vista (E15 v3, RT-15). Mostrar solo el primero
                junto a una tabla con más filas se lee como una contradicción de
                la propia pantalla, así que el excluido se nombra.
              -->
              v{indice.version} · {indice.num_documentos} vigente(s){#if (indice.items?.length ?? 0) > indice.num_documentos}
                · {(indice.items?.length ?? 0) - indice.num_documentos} excluido(s){/if}
              <StatusChip
                status={indiceFirmado ? "success" : "warning"}
                label={indiceFirmado ? "Firmado" : "Pendiente de firma"}
                dot
              />
            </span>
          </h2>

          <div class="exp-indice-toolbar">
            <Button
              variant="secondary"
              size="sm"
              on:click={handleDownloadXml}
              disabled={xmlDownloading}
            >
              {#if xmlDownloading}
                <i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Descargando…
              {:else}
                <i class="fas fa-file-code" aria-hidden="true"></i> Descargar XML
              {/if}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              on:click={loadIndiceVerify}
              disabled={indiceVerifyState.status === "loading"}
            >
              <i class="fas fa-shield-halved" aria-hidden="true"></i> Verificar integridad
            </Button>
            <Button
              variant="ghost"
              size="sm"
              on:click={loadIndiceVersions}
              disabled={indiceVersionsState.status === "loading"}
            >
              <i class="fas fa-clock-rotate-left" aria-hidden="true"></i> Ver versiones
            </Button>
            {#if canFirmarIndice && !indiceFirmado}
              <Button variant="primary" size="sm" on:click={() => (showFirmarModal = true)}>
                <i class="fas fa-signature" aria-hidden="true"></i> Firmar índice
              </Button>
            {/if}
            <dl class="exp-indice-summary">
              <div>
                <dt>SHA-256</dt>
                <dd class="exp-indice-hash">{indice.xml_sha256}</dd>
              </div>
              <div>
                <dt>Algoritmo</dt>
                <dd>{indice.algoritmo}</dd>
              </div>
              <div>
                <dt>Generado</dt>
                <dd>{fmtDatetime(indice.generado_ts)}</dd>
              </div>
            </dl>
          </div>

          <!-- Resultado de verificación — nunca comunicado solo por color -->
          {#if indiceVerifyState.status === "loading"}
            <Loader size="sm" label="Verificando índice…" />
          {:else if indiceVerifyState.status === "error"}
            <p class="exp-indice-verify-error" role="alert">
              <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
              {indiceVerifyState.errorMsg}
            </p>
          {:else if indiceVerifyState.status === "loaded"}
            {@const v = indiceVerifyState.data}
            <div
              class="exp-indice-verify"
              class:exp-indice-verify--ok={v.valido}
              class:exp-indice-verify--fail={!v.valido}
              role="status"
            >
              <i class="fas {v.valido ? 'fa-circle-check' : 'fa-circle-xmark'}" aria-hidden="true"
              ></i>
              <div class="exp-indice-verify__body">
                <strong>
                  {v.valido ? "Índice íntegro y válido" : "El índice presenta inconsistencias"}
                </strong>
                <ul class="exp-indice-verify__list">
                  <li>Huella del XML: {v.huella_indice_ok ? "coincide" : "NO coincide"}</li>
                  <li>
                    Firma: {v.firma?.presente ? "presente" : "ausente"},
                    {v.firma?.valida ? "válida" : "no válida"}
                    {#if v.firma?.motivo}(motivo: {v.firma.motivo}){/if}
                  </li>
                  <li>
                    Documentos con huella verificada: {v.documentos?.filter((d) => d.coincide)
                      .length ?? 0} / {v.documentos?.length ?? 0}
                  </li>
                </ul>
                <span class="exp-indice-verify__ts"
                  >Verificado: {fmtDatetime(v.fecha_verificacion)}</span
                >
              </div>
            </div>
          {/if}

          <!-- Historial de versiones -->
          {#if indiceVersionsState.status === "loading"}
            <Loader size="sm" label="Cargando versiones…" />
          {:else if indiceVersionsState.status === "error"}
            <p class="exp-indice-verify-error" role="alert">
              <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
              {indiceVersionsState.errorMsg}
            </p>
          {:else if indiceVersionsState.status === "loaded"}
            {#if indiceVersionsState.total > indiceVersionsState.items.length}
              <p class="exp-panel__info">
                <i class="fas fa-info-circle" aria-hidden="true"></i>
                Mostrando las {indiceVersionsState.items.length} versiones más recientes de {indiceVersionsState.total}
                en total.
              </p>
            {/if}
            <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
            <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
            <div
              class="exp-table-wrap"
              role="region"
              aria-label="Versiones del índice"
              tabindex="0"
            >
              <table class="exp-table" aria-label="Historial de versiones del índice">
                <thead>
                  <tr>
                    <th scope="col">Versión</th>
                    <th scope="col">Estado</th>
                    <th scope="col">Documentos</th>
                    <th scope="col">Generado</th>
                    <th scope="col">Por</th>
                  </tr>
                </thead>
                <tbody>
                  {#each indiceVersionsState.items as v (v.version)}
                    <tr>
                      <td>{v.version}</td>
                      <td>
                        <StatusChip
                          status={v.estado === "firmado" ? "success" : "warning"}
                          label={v.estado === "firmado" ? "Firmado" : "Pendiente de firma"}
                        />
                      </td>
                      <td>{v.num_documentos}</td>
                      <td>{fmtDatetime(v.generado_ts)}</td>
                      <td>{v.generado_por ?? "—"}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {/if}

          {#if !indice.items?.length}
            <EmptyState
              icon="fas fa-list-ol"
              title="Sin documentos en el índice"
              description="El índice existe pero no contiene documentos."
            />
          {:else}
            <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
            <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
            <div
              class="exp-table-wrap"
              role="region"
              aria-label="Documentos del índice"
              tabindex="0"
            >
              <table class="exp-table" aria-label="Ítems del índice electrónico">
                <thead>
                  <tr>
                    <th scope="col">#</th>
                    <th scope="col">Documento</th>
                    <th scope="col">Tipología</th>
                    <th scope="col">Archivado</th>
                    <th scope="col">Huella (SHA-256)</th>
                  </tr>
                </thead>
                <tbody>
                  {#each indice.items as item (item.document_id)}
                    <tr>
                      <td>{item.orden}</td>
                      <td>
                        {#if item.titulo}
                          {item.titulo}
                        {:else}
                          <span class="exp-uuid">{item.document_id}</span>
                        {/if}
                        <!--
                          Un documento excluido sigue en el índice a propósito
                          —es el hueco trazable: consta que estuvo y que se
                          retiró— pero listado igual que uno vigente deja de
                          serlo, porque quien lee la tabla no puede distinguir
                          lo que compone el expediente de lo que se apartó de
                          él. El backend proyecta `estado` desde v3 justo para
                          esto y la tabla no lo usaba.
                        -->
                        {#if item.estado === "excluido"}
                          <StatusChip status="warning" label="Excluido" />
                        {/if}
                      </td>
                      <td>{item.tipologia ?? "—"}</td>
                      <!--
                        `fecha_incorporacion` es el nombre del contrato
                        (`IndiceItemResponse`), alineado con
                        <fechaIncorporacion> del XML probatorio. Aquí se leía
                        `fecha_archivado`, que no existe en la respuesta: la
                        columna salía vacía en TODAS las filas y el «—» lo hacía
                        pasar por un dato no declarado. Es el mismo cruce de
                        esquemas parecidos que ya se corrigió en la foliación.
                      -->
                      <td>{item.fecha_incorporacion ? fmtDate(item.fecha_incorporacion) : "—"}</td>
                      <td>
                        {#if item.valor_huella}
                          <code class="exp-hash" title={item.valor_huella}
                            >{item.valor_huella.slice(0, 12)}…</code
                          >
                        {:else}
                          —
                        {/if}
                      </td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {/if}
        </Card>
      {/if}
    </div>

    <!-- -----------------------------------------------------------------------
         Panel — Foliado (E02)
         ----------------------------------------------------------------------- -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="panel-foliado"
      role="tabpanel"
      aria-labelledby="tab-foliado"
      hidden={activeTab !== "foliado"}
      tabindex="0"
      class="exp-panel"
    >
      {#if foliadoState.status === "loading"}
        <Loader label="Cargando foliación…" />
      {:else if foliadoState.status === "error"}
        <Card>
          <EmptyState
            icon="fas fa-exclamation-circle"
            title="No se pudo cargar la foliación"
            description={foliadoState.errorMsg}
          />
        </Card>
      {:else if foliadoState.status === "loaded"}
        {@const foliado = foliadoState.data}
        <Card>
          <h2 slot="header" class="op-card-title">
            Foliación
            <span class="exp-count-badge">{foliado.total_folios} folio(s)</span>
          </h2>
          {#if !foliado.items?.length}
            <EmptyState
              icon="fas fa-hashtag"
              title="Sin foliación"
              description="Este expediente no tiene documentos foliados todavía."
            />
          {:else}
            <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
            <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
            <div
              class="exp-table-wrap"
              role="region"
              aria-label="Foliación del expediente"
              tabindex="0"
            >
              <!--
                Esta tabla leía el contrato EQUIVOCADO. `GET /expedientes/{id}/foliado`
                responde `ExpedienteIndice` —`folio`, `radicado_id`,
                `tracking_number`, `added_at`—, y aquí se leían los campos de
                `IndiceItemResponse`, que es el ÍNDICE ELECTRÓNICO (E15, RT-15) y
                tiene su propia pestaña: `orden`, `titulo`, `document_id`,
                `tipologia`, `valor_huella`. Dos esquemas parecidos, contratos
                distintos, y ninguno de los campos leídos existía en la respuesta.
                En pantalla: «undefined» en Orden y Documento —la foliación, que
                es el orden que da fe del expediente— y guiones en tres columnas
                que este endpoint no puede llenar nunca. La clave del `{#each}`
                era `item.document_id`, es decir `undefined` REPETIDO en todas las
                filas, que es además una clave duplicada para Svelte.

                Tipología y huella no se piden aquí: viven en el índice
                electrónico. Una columna que solo puede mostrar «—» promete un
                dato que este endpoint no tiene.
              -->
              <table class="exp-table" aria-label="Foliación ordenada del expediente">
                <thead>
                  <tr>
                    <th scope="col">Folio</th>
                    <th scope="col">Radicado</th>
                    <th scope="col">Incorporado</th>
                  </tr>
                </thead>
                <tbody>
                  {#each foliado.items as item (item.radicado_id)}
                    <tr>
                      <td>{item.folio}</td>
                      <td>
                        {#if item.tracking_number}
                          <span class="exp-meta-list__code">{item.tracking_number}</span>
                        {:else}
                          <span class="exp-uuid">{item.radicado_id}</span>
                        {/if}
                      </td>
                      <td>{item.added_at ? fmtDatetime(item.added_at) : "—"}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {/if}
        </Card>
      {/if}
    </div>

    <!-- -----------------------------------------------------------------------
         Panel 2 — Anexos E07
         ----------------------------------------------------------------------- -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="panel-anexos"
      role="tabpanel"
      aria-labelledby="tab-anexos"
      hidden={activeTab !== "anexos"}
      tabindex="0"
      class="exp-panel"
    >
      <Card>
        <h2 slot="header" class="op-card-title">
          Anexos
          <span
            class="exp-count-badge"
            aria-label="{expediente?.radicados?.length ?? 0} radicado(s)"
            >{expediente?.radicados?.length ?? 0}</span
          >
        </h2>

        {#if !expediente?.radicados?.length}
          <EmptyState
            icon="fas fa-paperclip"
            title="Sin radicados"
            description="Este expediente no tiene radicados vinculados de los que listar anexos."
          />
        {:else}
          <p class="exp-panel__info">
            <i class="fas fa-info-circle" aria-hidden="true"></i>
            Expanda un radicado para consultar sus anexos. Se cargan al momento de expandirlo y quedan
            sujetos al control de acceso del radicado dueño del anexo.
          </p>

          <ul class="exp-anexos-accordion" aria-label="Radicados del expediente">
            {#each expediente.radicados as rad (rad.radicado_id)}
              {@const state = anexosState[rad.radicado_id]}
              {@const expanded = expandedRadicados.has(rad.radicado_id)}
              <li class="exp-anexos-accordion__item">
                <button
                  type="button"
                  class="exp-anexos-accordion__trigger"
                  aria-expanded={expanded}
                  aria-controls="anexos-panel-{rad.radicado_id}"
                  on:click={() => toggleRadicadoAnexos(rad.radicado_id)}
                >
                  <i
                    class="fas {expanded ? 'fa-chevron-down' : 'fa-chevron-right'}"
                    aria-hidden="true"
                  ></i>
                  <span class="exp-anexos-accordion__radicado">{rad.tracking_number}</span>
                  <span class="exp-anexos-accordion__date">{fmtDate(rad.added_at)}</span>
                  {#if state?.status === "loaded"}
                    <span class="exp-count-badge">{state.items.length}</span>
                  {/if}
                </button>

                {#if expanded}
                  <div id="anexos-panel-{rad.radicado_id}" class="exp-anexos-accordion__panel">
                    {#if state?.status === "loading"}
                      <Loader size="sm" label="Cargando anexos…" />
                    {:else if state?.status === "error"}
                      <p class="exp-anexos-error" role="status" aria-live="polite">
                        <i class="fas fa-exclamation-circle" aria-hidden="true"></i>
                        {state.errorMsg}
                      </p>
                    {:else if state?.status === "empty"}
                      <p class="exp-anexos-empty" role="status" aria-live="polite">
                        Este radicado no tiene anexos.
                      </p>
                    {:else if state?.status === "loaded"}
                      <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
                      <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
                      <div
                        class="exp-table-wrap"
                        role="region"
                        aria-label="Anexos de {rad.tracking_number}"
                        tabindex="0"
                      >
                        <table class="exp-table" aria-label="Anexos de {rad.tracking_number}">
                          <thead>
                            <tr>
                              <th scope="col">Archivo</th>
                              <th scope="col">Tipo</th>
                              <th scope="col">Tamaño</th>
                              <th scope="col">Principal</th>
                              <th scope="col">Acción</th>
                            </tr>
                          </thead>
                          <tbody>
                            {#each state.items as anexo (anexo.file_id)}
                              <tr>
                                <td class="exp-filename" title={anexo.filename}>{anexo.filename}</td
                                >
                                <td>{anexo.mime_type ?? "—"}</td>
                                <td>{fmtBytes(anexo.file_size)}</td>
                                <td>
                                  {#if anexo.es_principal}
                                    <i
                                      class="fas fa-check-circle exp-icon--success"
                                      aria-label="Principal"
                                    ></i>
                                  {:else}
                                    <span aria-label="No es el documento principal">—</span>
                                  {/if}
                                </td>
                                <td>
                                  <!-- Descarga mediada por document-service -->
                                  <button
                                    type="button"
                                    class="exp-action-link"
                                    aria-label="Descargar {anexo.filename}"
                                    on:click={() =>
                                      handleDownloadAnexo(
                                        rad.radicado_id,
                                        anexo.file_id,
                                        anexo.filename,
                                      )}
                                  >
                                    <i class="fas fa-download" aria-hidden="true"></i> Descargar
                                  </button>
                                  <!-- Migración de formato (E10, RF-PRE-04). Solo
                                       se ofrece sobre PDF: es una conversión A
                                       PDF/A, y ofrecerla sobre un .docx sería
                                       ofrecer un botón que el convertidor
                                       rechaza. El original NO se toca. -->
                                  {#if (anexo.mime_type ?? "").includes("pdf")}
                                    <button
                                      type="button"
                                      class="exp-action-link"
                                      aria-label="Migrar {anexo.filename} a PDF/A"
                                      disabled={migrandoFileId === anexo.file_id}
                                      on:click={() => migrarAPdfa(anexo)}
                                    >
                                      <i class="fas fa-file-shield" aria-hidden="true"></i>
                                      {migrandoFileId === anexo.file_id
                                        ? "Migrando…"
                                        : "Migrar a PDF/A"}
                                    </button>
                                  {/if}
                                </td>
                              </tr>
                            {/each}
                          </tbody>
                        </table>
                      </div>
                    {/if}
                  </div>
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      </Card>
    </div>

    <!-- -----------------------------------------------------------------------
         Panel 3 — Flujo / Historial (eventos del expediente E02 §9)
         ----------------------------------------------------------------------- -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="panel-flujo"
      role="tabpanel"
      aria-labelledby="tab-flujo"
      hidden={activeTab !== "flujo"}
      tabindex="0"
      class="exp-panel"
    >
      {#if facetas.eventos.error}
        <Card>
          <EmptyState
            icon="fas fa-exclamation-circle"
            title="No se pudo cargar el historial"
            description={facetas.eventos.error.detail}
          />
        </Card>
      {:else if !facetas.eventos.data?.length}
        <EmptyState
          icon="fas fa-route"
          title="Sin eventos registrados"
          description="Este expediente aún no tiene eventos en su hoja de ruta."
        />
      {:else}
        <Card>
          <h2 slot="header" class="op-card-title">
            Hoja de ruta del expediente
            <span class="exp-count-badge">{facetas.eventos.data.length}</span>
          </h2>

          <p class="exp-panel__info">
            <i class="fas fa-info-circle" aria-hidden="true"></i>
            Hoja de ruta del ciclo de vida del expediente. El historial de flujo por radicado se accede
            desde la bandeja de cada radicado.
          </p>

          <ol class="exp-timeline" aria-label="Eventos del expediente">
            {#each facetas.eventos.data as evento (evento.id ?? evento.ts)}
              <li class="exp-timeline__item">
                <div class="exp-timeline__marker" aria-hidden="true"></div>
                <div class="exp-timeline__content">
                  <div class="exp-timeline__header">
                    <strong class="exp-timeline__accion">{evento.accion}</strong>
                    <time class="exp-timeline__ts" datetime={evento.ts}>
                      {fmtDatetime(evento.ts)}
                    </time>
                  </div>
                  {#if evento.comentario}
                    <p class="exp-timeline__comment">{evento.comentario}</p>
                  {/if}
                  {#if evento.actor}
                    <span class="exp-timeline__actor">
                      <i class="fas fa-user" aria-hidden="true"></i>
                      {evento.actor_nombre ?? evento.actor}
                    </span>
                  {/if}
                  {#if evento.detalles && Object.keys(evento.detalles).length}
                    <details class="exp-timeline__details">
                      <summary>Ver detalles</summary>
                      <pre class="exp-timeline__json">{JSON.stringify(
                          evento.detalles,
                          null,
                          2,
                        )}</pre>
                    </details>
                  {/if}
                </div>
              </li>
            {/each}
          </ol>
        </Card>
      {/if}
    </div>

    <!-- -----------------------------------------------------------------------
         Panel — Preservación (E10: AIP + eventos PREMIS)
         ----------------------------------------------------------------------- -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="panel-preservacion"
      role="tabpanel"
      aria-labelledby="tab-preservacion"
      hidden={activeTab !== "preservacion"}
      tabindex="0"
      class="exp-panel"
    >
      {#if preservacionState.status === "loading"}
        <Loader label="Cargando información de preservación…" />
      {:else}
        <div class="exp-panel__grid">
          <Card>
            <h2 slot="header" class="op-card-title">Paquete de preservación (AIP)</h2>
            {#if preservacionState.aip.status === "desactivada"}
              <!-- AREA-12-04: estado de INSTANCIA (`PRESERVATION_ENABLED`), no del
                   expediente — decirlo no abre oráculo no-read-up. -->
              <EmptyState
                icon="fas fa-power-off"
                title="Preservación desactivada"
                description="La preservación está desactivada en esta instalación (PRESERVATION_ENABLED): no se empaquetan AIP ni se sellan artefactos. Pida a quien administra la instalación que la active."
              />
            {:else if preservacionState.aip.status === "error"}
              <EmptyState
                icon="fas fa-exclamation-circle"
                title="No se pudo cargar el AIP"
                description={preservacionState.aip.errorMsg}
              />
            {:else if preservacionState.aip.status === "empty" || preservacionState.aip.status === "idle"}
              <!-- C1 (revisión UX): el 404 de GET /expedientes/{id}/aip es NEUTRO
                   (archive_service.py get_aip) — cubre "sin AIP empaquetado
                   todavía" Y "sin visibilidad sobre este expediente" con la
                   misma forma, a propósito (no-read-up, RF-SEG-08). No se
                   afirma categóricamente que el AIP no existe. -->
              <EmptyState
                icon="fas fa-box-archive"
                title="Sin AIP empaquetado"
                description="Este expediente no tiene un paquete de preservación generado, o usted no tiene visibilidad sobre él."
              />
            {:else}
              {@const aip = preservacionState.aip.data}
              <dl class="exp-meta-list">
                <div class="exp-meta-list__item">
                  <dt>Estado</dt>
                  <dd>
                    <StatusChip
                      status={aip.estado === "completo" ? "success" : "warning"}
                      label={aipEstadoLabel(aip.estado)}
                    />
                  </dd>
                </div>
                <div class="exp-meta-list__item">
                  <dt>Documentos</dt>
                  <dd>{aip.num_documentos}</dd>
                </div>
                <div class="exp-meta-list__item">
                  <dt>Generado</dt>
                  <dd>{fmtDatetime(aip.created_at)}</dd>
                </div>
                <div class="exp-meta-list__item">
                  <dt>SHA-256 (AIP)</dt>
                  <dd class="exp-indice-hash">{aip.aip_sha256}</dd>
                </div>
                {#if aip.indice_sha256}
                  <div class="exp-meta-list__item">
                    <dt>SHA-256 (índice)</dt>
                    <dd class="exp-indice-hash">{aip.indice_sha256}</dd>
                  </div>
                {/if}
              </dl>
            {/if}
          </Card>

          <Card>
            <h2 slot="header" class="op-card-title">
              Eventos PREMIS
              <!-- I1 (revisión UX): el badge NO puede afirmar "0" cuando la
                   carga falló — se leería como "no hubo actividad de
                   preservación" en vez de "no se pudo consultar". -->
              <span class="exp-count-badge">
                {preservacionState.eventos.status === "error"
                  ? "—"
                  : preservacionState.eventos.items.length}
              </span>
            </h2>
            {#if preservacionState.eventos.status === "error"}
              <EmptyState
                icon="fas fa-exclamation-circle"
                title="No se pudieron cargar los eventos"
                description={preservacionState.eventos.errorMsg}
              />
            {:else if preservacionState.eventos.status === "empty" || preservacionState.eventos.status === "idle"}
              <EmptyState
                icon="fas fa-timeline"
                title="Sin eventos de preservación"
                description="Este expediente no tiene actividad de preservación registrada todavía."
              />
            {:else}
              <ol class="exp-timeline" aria-label="Eventos PREMIS de preservación">
                {#each preservacionState.eventos.items as evento (evento.id)}
                  <li class="exp-timeline__item">
                    <div class="exp-timeline__marker" aria-hidden="true"></div>
                    <div class="exp-timeline__content">
                      <div class="exp-timeline__header">
                        <strong class="exp-timeline__accion">{evento.tipo}</strong>
                        <StatusChip
                          status={evento.resultado === "success" ? "success" : "error"}
                          label={premisResultadoLabel(evento.resultado)}
                        />
                        <time class="exp-timeline__ts" datetime={evento.ts}>
                          {fmtDatetime(evento.ts)}
                        </time>
                      </div>
                      {#if evento.agente}
                        <span class="exp-timeline__actor">
                          <i class="fas fa-robot" aria-hidden="true"></i>
                          {evento.agente}
                        </span>
                      {/if}
                    </div>
                  </li>
                {/each}
              </ol>
            {/if}
          </Card>
        </div>
      {/if}
    </div>

    <!-- -----------------------------------------------------------------------
         Panel — Acceso (E15 perfil v4 R3-b/R4-b: historial + roles autorizados)
         ----------------------------------------------------------------------- -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="panel-acceso"
      role="tabpanel"
      aria-labelledby="tab-acceso"
      hidden={activeTab !== "acceso"}
      tabindex="0"
      class="exp-panel"
    >
      {#if accesoState.status === "loading"}
        <Loader label="Cargando información de acceso…" />
      {:else}
        <div class="exp-panel__grid">
          <Card>
            <h2 slot="header" class="op-card-title">
              Grupos autorizados
              <!-- I1 (revisión UX): ver comentario homólogo en Eventos PREMIS. -->
              <span class="exp-count-badge">
                {accesoState.roles.status === "error" ? "—" : accesoState.roles.items.length}
              </span>
            </h2>
            <p class="exp-panel__info">
              <i class="fas fa-info-circle" aria-hidden="true"></i>
              Vista actual de los grupos con acceso — no forma parte del índice firmado, porque la política
              de acceso puede cambiar con el tiempo y el índice firmado no se actualiza retroactivamente.
            </p>
            {#if accesoState.roles.status === "error"}
              <EmptyState
                icon="fas fa-exclamation-circle"
                title="No se pudo cargar la matriz de acceso"
                description={accesoState.roles.errorMsg}
              />
            {:else if accesoState.roles.status === "empty" || accesoState.roles.status === "idle"}
              <EmptyState
                icon="fas fa-user-shield"
                title="Sin grupos autorizados"
                description="No hay grupos con acceso configurado para el nivel de este expediente."
              />
            {:else}
              <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
              <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
              <div
                class="exp-table-wrap"
                role="region"
                aria-label="Grupos autorizados"
                tabindex="0"
              >
                <table class="exp-table" aria-label="Grupos autorizados a leer el expediente">
                  <thead>
                    <tr>
                      <th scope="col">Grupo</th>
                      <th scope="col">Clearance máx.</th>
                      <th scope="col">CRUD</th>
                    </tr>
                  </thead>
                  <tbody>
                    {#each accesoState.roles.items as rol (rol.grupo)}
                      <tr>
                        <td>{rol.grupo}</td>
                        <td>{rol.clearanceMax}</td>
                        <td>{rol.crud}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            {/if}
          </Card>

          <Card>
            <h2 slot="header" class="op-card-title">
              Historial de acceso
              <!-- I1 (revisión UX): CRÍTICO en este panel específicamente — un
                   "0" falso sobre un fallo de carga se lee como "nadie ha
                   consultado este expediente reservado", una afirmación falsa
                   sobre un dato sensible en un panel de auditoría de lecturas. -->
              <span class="exp-count-badge">
                {accesoState.historial.status === "error" ? "—" : accesoState.historial.total}
              </span>
            </h2>
            {#if accesoState.historial.status === "error"}
              <EmptyState
                icon="fas fa-exclamation-circle"
                title="No se pudo cargar el historial"
                description={accesoState.historial.errorMsg}
              />
            {:else if accesoState.historial.status === "empty" || accesoState.historial.status === "idle"}
              <EmptyState
                icon="fas fa-clock-rotate-left"
                title="Sin lecturas registradas"
                description="Todavía no hay lecturas ni descargas auditadas de este expediente, su índice o sus documentos."
              />
            {:else}
              {#if accesoState.historial.total > accesoState.historial.items.length}
                <p class="exp-panel__info">
                  <i class="fas fa-info-circle" aria-hidden="true"></i>
                  Mostrando las {accesoState.historial.items.length} lecturas más recientes de {accesoState
                    .historial.total}
                  en total.
                </p>
              {/if}
              <ul class="exp-radicado-list" aria-label="Historial de acceso">
                {#each accesoState.historial.items as entry (entry.ts + entry.action + (entry.actor ?? "") + (entry.object_ref ?? ""))}
                  <li class="exp-radicado-list__item">
                    <!-- El nombre lo resuelve el backend; si no se puede
                         resolver (asiento del sistema, usuario dado de baja) se
                         cae al identificador, que dice poco pero no miente. -->
                    <span class="exp-uuid">{entry.actor_nombre ?? entry.actor ?? "sistema"}</span>
                    <span>{accesoAccionLabel(entry.action)}</span>
                    <!-- SOBRE QUÉ. Sin esto, «Descarga de un anexo» no dice de
                         qué documento — y esa es exactamente la pregunta que
                         esta pestaña existe para responder. Se muestra el
                         número de radicado y NO el `payload`, que lleva el
                         nivel de clasificación del documento. -->
                    {#if entry.object_type === "radicado"}
                      <span class="exp-radicado-list__ref">{entry.object_ref}</span>
                    {/if}
                    <span class="exp-radicado-list__date">{fmtDatetime(entry.ts)}</span>
                  </li>
                {/each}
              </ul>
            {/if}
          </Card>
        </div>
      {/if}
    </div>

    <!-- -----------------------------------------------------------------------
         Panel 4 — Ubicación física E17
         ----------------------------------------------------------------------- -->
    <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      id="panel-fisico"
      role="tabpanel"
      aria-labelledby="tab-fisico"
      hidden={activeTab !== "fisico"}
      tabindex="0"
      class="exp-panel"
    >
      {#if facetas.ubicacion.error}
        <Card>
          <EmptyState
            icon="fas fa-exclamation-circle"
            title="No se pudo cargar la ubicación"
            description={facetas.ubicacion.error.detail}
          />
        </Card>
      {:else if !facetas.ubicacion.data?.length}
        <EmptyState
          icon="fas fa-archive"
          title="Sin ubicación física registrada"
          description="Este expediente no ha sido ubicado en ninguna unidad de conservación."
        />
      {:else}
        <Card>
          <h2 slot="header" class="op-card-title">Unidades de conservación</h2>
          <!-- Técnica WCAG SCR29: región con overflow horizontal alcanzable con teclado (WCAG 2.1.1). -->
          <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
          <div class="exp-table-wrap" role="region" aria-label="Ubicaciones físicas" tabindex="0">
            <table class="exp-table" aria-label="Unidades de conservación del expediente">
              <thead>
                <tr>
                  <th scope="col">Signatura</th>
                  <!-- La signatura topográfica dice en qué entrepaño mirar; el
                       TIPO y el CÓDIGO dicen qué hay que sacar de ahí — «la caja
                       CAJ-001». Aquí había una columna «Unidad (ID)» con el
                       identificador interno, que no le sirve a nadie que vaya al
                       depósito. El backend ya traía las dos por el JOIN y el
                       docstring de `UnidadDeExpediente` ya narraba esta misma
                       corrección hecha en `/archivo-fisico`: se arregló una
                       pantalla y se dejó la otra, sobre la misma respuesta. -->
                  <th scope="col">Tipo unidad</th>
                  <th scope="col">Código unidad</th>
                  <th scope="col">Folio inicio</th>
                  <th scope="col">Folio fin</th>
                </tr>
              </thead>
              <tbody>
                {#each facetas.ubicacion.data as unidad (unidad.unidad_id)}
                  <tr>
                    <td>{unidad.signatura ?? "—"}</td>
                    <td>{unidad.tipo ?? "—"}</td>
                    <td><code>{unidad.codigo ?? "—"}</code></td>
                    <td>{unidad.folio_inicio ?? "—"}</td>
                    <td>{unidad.folio_fin ?? "—"}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        </Card>
      {/if}
    </div>
  </div>
</div>

<!-- =========================================================================
     Modal: Confirmar cierre
     ========================================================================= -->
{#if expediente}
  <CadenaFirmaModal
    open={showCadenaModal}
    objetoTipo="expediente"
    objetoId={expediente.id}
    titulo={`Cadena de firmas — ${expediente.code ?? expediente.id}`}
    on:close={() => (showCadenaModal = false)}
    on:created={() => {
      showCadenaModal = false;
      notifySuccess(
        "Cadena de firmas abierta. Cada firmante la verá en su bandeja cuando le toque.",
      );
    }}
  />
{/if}

<Modal
  open={showCloseModal}
  title="Cerrar expediente"
  size="sm"
  on:close={() => (showCloseModal = false)}
>
  <p>
    ¿Confirma el cierre del expediente <strong>{expediente?.code}</strong>? Su estado pasará a
    <strong>Cerrado</strong> y se materializará la disposición TRD.
  </p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (showCloseModal = false)} disabled={actionLoading}
      >Cancelar</Button
    >
    <Button
      variant="primary"
      on:click={handleClose}
      disabled={actionLoading}
      loading={actionLoading}>Confirmar cierre</Button
    >
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: Confirmar transferencia
     ========================================================================= -->
<Modal
  open={showTransferModal}
  title="Transferir expediente"
  size="sm"
  on:close={() => (showTransferModal = false)}
>
  <!-- Este botón es el ATAJO: cambia la custodia sin acta de entrega, sin
       FUID y sin cotejo de integridad, y consume el estado `cerrado` que el
       circuito formal necesita — después de pulsarlo, abrir una transferencia
       con acta para este expediente es imposible para siempre. No se retira
       (una transferencia puramente lógica es legítima y quitar capacidad no
       es decisión de esta pantalla), pero sí se dice lo que hace y lo que
       cierra, y se ofrece la otra puerta. -->
  <p>
    ¿Confirma la transferencia del expediente <strong>{expediente?.code}</strong>? Su estado pasará
    a <strong>Transferido</strong> y dejará de admitir documentos.
  </p>
  <p class="exp-modal__warning" role="status">
    <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
    <!-- Toda la prosa dentro de UN `<span>`: en un contenedor flex cada tramo
         de texto suelto se vuelve un elemento propio y la frase se parte en
         columnas. Ya pasó cinco veces con avisos de actos irreversibles. -->
    <span>
      Esta vía <strong>no produce acta de entrega ni FUID</strong> y no coteja la integridad del
      acervo. Además es de un solo sentido: después ya no se podrá abrir para este expediente una
      transferencia con acta, porque esa exige que el expediente esté cerrado.
      <a href="/transferencias">Preparar una transferencia con acta</a> en su lugar.
    </span>
  </p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (showTransferModal = false)} disabled={actionLoading}
      >Cancelar</Button
    >
    <Button
      variant="primary"
      on:click={handleTransfer}
      disabled={actionLoading}
      loading={actionLoading}>Confirmar transferencia</Button
    >
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: Confirmar exclusión de radicado (soft-delete, E15 perfil v3)
     ========================================================================= -->
<Modal
  open={showExcluirModal}
  title="Excluir radicado del expediente"
  size="sm"
  on:close={closeExcluirModal}
>
  <p>
    ¿Confirma excluir el radicado <strong>{excluirTarget?.tracking_number}</strong> de este expediente?
    El vínculo quedará marcado como excluido (hueco trazable en el índice), no se borra físicamente.
  </p>
  <FormField
    id="excluir-causal"
    label="Motivo de la exclusión"
    type="textarea"
    required
    bind:value={excluirCausal}
    rows={3}
    hint="Obligatorio, máximo 500 caracteres."
    error={excluirCausalError}
  />
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={closeExcluirModal} disabled={excluirLoading}>Cancelar</Button>
    <Button
      variant="danger"
      on:click={handleExcluir}
      disabled={excluirLoading}
      loading={excluirLoading}
    >
      Confirmar exclusión
    </Button>
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: Confirmar firma del índice electrónico (acto con valor legal, E06)
     ========================================================================= -->
<Modal
  open={showFirmarModal}
  title="Firmar índice electrónico"
  size="sm"
  on:close={() => (showFirmarModal = false)}
>
  <p>
    ¿Confirma la firma XAdES-B del índice electrónico vigente del expediente
    <strong>{expediente?.code}</strong>? Una vez firmado, esta versión del índice es inmutable.
  </p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (showFirmarModal = false)} disabled={firmarLoading}>
      Cancelar
    </Button>
    <Button
      variant="primary"
      on:click={handleFirmarIndice}
      disabled={firmarLoading}
      loading={firmarLoading}
    >
      <i class="fas fa-signature" aria-hidden="true"></i>
      Confirmar firma
    </Button>
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Drawer: Vincular radicados (capacidad central del módulo)
     ========================================================================= -->
<VincularRadicadosDrawer
  open={showVincularDrawer}
  expedienteId={expediente?.id ?? ""}
  {linkedIds}
  on:close={() => (showVincularDrawer = false)}
  on:linked={handleVinculado}
/>

<!-- =========================================================================
     Modal: Editar metadatos del expediente
     ========================================================================= -->
<EditarMetadatosModal
  open={showMetadataModal}
  {expediente}
  trdSeries={data.trdSeries ?? []}
  on:close={() => (showMetadataModal = false)}
  on:saved={handleMetadataSaved}
/>

<!-- =========================================================================
     Drawer: Disposición final (solo lectura) — RF-MET-08/E04
     ========================================================================= -->
<DisposicionExpedienteDrawer
  open={showDisposicionDrawer}
  {expediente}
  trdSerieDetail={data.trdSerieDetail ?? null}
  on:close={() => (showDisposicionDrawer = false)}
/>

<!-- Acto de publicación de oficio (Ley 1712/2014 art. 12) -->
<PublicacionExpedienteDrawer
  open={showPublicacionDrawer}
  {expediente}
  on:close={() => (showPublicacionDrawer = false)}
  on:changed={(e) => {
    // Reflejo optimista sobre el objeto ya cargado: el backend es la fuente de
    // verdad y devolvió el estado nuevo, así que no hace falta recargar la
    // página entera para que el drawer y la ficha dejen de discrepar.
    if (expediente) {
      expediente.publicable = e.detail.publicable;
      expediente.publicado_at = e.detail.publicado_at;
    }
  }}
/>

<style lang="scss">
  // =========================================================================
  // Workspace
  // =========================================================================

  .exp-workspace {
    display: flex;
    flex-direction: column;
    min-height: 100%;
  }

  .exp-workspace__header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--op-space-4);
    padding: var(--op-space-5) var(--op-space-5) var(--op-space-4);
    background: var(--op-surface);
    border-bottom: 1px solid var(--op-border);
    flex-wrap: wrap;
  }

  .exp-workspace__breadcrumb {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    margin-bottom: var(--op-space-2);

    a {
      // A3 (revisión UX): `--op-primary` como color de TEXTO no alcanza AA
      // (4.28:1 sobre blanco, 4.15:1 sobre `--op-bg`, el fondo real detrás de
      // este breadcrumb). `--op-primary-dark` sí: 6.37:1.
      color: var(--op-primary-dark);
      text-decoration: none;

      &:hover {
        text-decoration: underline;
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
        border-radius: 2px;
      }
    }

    span[aria-hidden] {
      margin: 0 var(--op-space-1);
    }
  }

  .exp-workspace__title {
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0 0 var(--op-space-1);
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    flex-wrap: wrap;
  }

  .exp-workspace__desc {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    margin: 0;
  }

  .exp-workspace__actions {
    display: flex;
    gap: var(--op-space-2);
    flex-shrink: 0;
    flex-wrap: wrap;
    align-items: flex-start;
    padding-top: var(--op-space-5);
  }

  // =========================================================================
  // Tablist
  // =========================================================================

  .exp-tabs {
    display: flex;
    background: var(--op-surface);
    border-bottom: 2px solid var(--op-border);
    overflow-x: auto;
    // `position: relative` NO es cosmético: sin bloque contenedor propio, un
    // `position: absolute` de dentro (el `sr-only` de las cabeceras) se resuelve
    // contra el DOCUMENTO y este `overflow` no lo alcanza — desplazaba la página
    // entera hacia una franja vacía. Ver el hallazgo de los 390 px.
    position: relative;
    scrollbar-width: none;
    flex-shrink: 0;

    &::-webkit-scrollbar {
      display: none;
    }
  }

  .exp-tabs__tab {
    display: inline-flex;
    align-items: center;
    gap: var(--op-space-2);
    padding: var(--op-space-3) var(--op-space-4);
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    margin-bottom: -2px;
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-medium);
    color: var(--op-text-secondary);
    cursor: pointer;
    white-space: nowrap;
    transition:
      color 0.15s,
      border-color 0.15s;

    i {
      font-size: var(--op-font-xs);
    }

    &:hover {
      color: var(--op-text);
      background: var(--op-bg);
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: -2px;
    }

    &--active {
      color: var(--op-primary-dark);
      border-bottom-color: var(--op-primary);
      font-weight: var(--op-weight-bold);
    }
  }

  // =========================================================================
  // Panels
  // =========================================================================

  .exp-workspace__panels {
    flex: 1;
    padding: var(--op-space-5);
    background: var(--op-bg);
  }

  .exp-panel {
    outline: none;

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 4px;
      border-radius: var(--op-radius-sm);
    }

    &__grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: var(--op-space-4);

      @media (max-width: 900px) {
        grid-template-columns: 1fr;
      }
    }

    &__info {
      font-size: var(--op-font-sm);
      color: var(--op-info-fg);
      background: var(--op-info-bg);
      border-left: 3px solid var(--op-info);
      padding: var(--op-space-2) var(--op-space-3);
      border-radius: 0 var(--op-radius-sm) var(--op-radius-sm) 0;
      margin-bottom: var(--op-space-3);

      i {
        margin-right: var(--op-space-1);
      }
    }
  }

  // =========================================================================
  // Metadatos
  // =========================================================================

  .exp-meta-list {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--op-space-2) var(--op-space-4);
    margin: 0;

    @media (max-width: 640px) {
      grid-template-columns: 1fr;
    }

    &__item {
      display: flex;
      flex-direction: column;
      gap: 2px;

      dt {
        font-size: var(--op-font-xs);
        color: var(--op-text-secondary);
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }

      dd {
        font-size: var(--op-font-sm);
        color: var(--op-text);
        font-weight: var(--op-weight-medium);
        margin: 0;
        display: flex;
        align-items: center;
        gap: var(--op-space-1);
        flex-wrap: wrap;
      }
    }

    &__code {
      font-family: var(--op-font-family-mono);
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      word-break: break-all;
    }

    &__sub {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }
  }

  .exp-section-subtitle {
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-bold);
    color: var(--op-text-secondary);
    margin: var(--op-space-4) 0 var(--op-space-2);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  // =========================================================================
  // Radicados
  // =========================================================================

  .exp-count-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: var(--op-primary-light);
    color: var(--op-primary-dark);
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-bold);
    border-radius: var(--op-radius-full);
    min-width: 20px;
    height: 20px;
    padding: 0 var(--op-space-1);
    margin-left: var(--op-space-2);
  }

  .exp-radicado-list {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);

    &__item {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
      padding: var(--op-space-2) var(--op-space-3);
      background: var(--op-bg);
      border-radius: var(--op-radius-sm);
      border: 1px solid var(--op-border);
    }

    &__number {
      font-family: var(--op-font-family-mono);
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-bold);
      // A3: ver comentario en .exp-workspace__breadcrumb — mismo problema de
      // contraste, mismo fix (`--op-primary-dark`, 6.37:1 sobre `--op-bg`).
      color: var(--op-primary-dark);
      text-decoration: none;

      &:hover {
        text-decoration: underline;
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
        border-radius: 2px;
      }
    }

    &__ref {
      font-family: var(--op-font-family-mono);
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    &__date {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin-left: auto;
    }
  }

  // =========================================================================
  // Índice
  // =========================================================================

  .exp-indice-meta {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-regular);
    color: var(--op-text-secondary);
    margin-left: auto;
    flex-wrap: wrap;
  }

  .exp-indice-toolbar {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-4);
    margin-bottom: var(--op-space-4);
    flex-wrap: wrap;
  }

  .exp-indice-summary {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-1);
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);

    div {
      display: flex;
      gap: var(--op-space-2);
    }

    dt {
      font-weight: var(--op-weight-medium);
    }
  }

  .exp-indice-hash {
    font-family: var(--op-font-family-mono);
    font-size: var(--op-font-xs);
    word-break: break-all;
  }

  // =========================================================================
  // Tablas
  // =========================================================================

  .exp-table-wrap {
    overflow-x: auto;
    // `position: relative` NO es cosmético: sin bloque contenedor propio, un
    // `position: absolute` de dentro (el `sr-only` de las cabeceras) se resuelve
    // contra el DOCUMENTO y este `overflow` no lo alcanza — desplazaba la página
    // entera hacia una franja vacía. Ver el hallazgo de los 390 px.
    position: relative;
    border-radius: var(--op-radius-sm);
    border: 1px solid var(--op-border);

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: -2px;
    }
  }

  .exp-table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--op-font-sm);

    th {
      background: var(--op-bg);
      color: var(--op-text-secondary);
      font-weight: var(--op-weight-medium);
      font-size: var(--op-font-xs);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      padding: var(--op-space-2) var(--op-space-3);
      text-align: left;
      border-bottom: 1px solid var(--op-border);
    }

    td {
      padding: var(--op-space-2) var(--op-space-3);
      border-bottom: 1px solid var(--op-border);
      color: var(--op-text);
      vertical-align: middle;
    }

    tr:last-child td {
      border-bottom: none;
    }
    tr:hover td {
      background: var(--op-bg);
    }
  }

  .exp-hash {
    font-family: var(--op-font-family-mono);
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
    cursor: help;
  }

  .exp-uuid {
    font-family: var(--op-font-family-mono);
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
  }

  .exp-filename {
    max-width: 200px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .exp-link,
  .exp-action-link {
    background: none;
    border: none;
    padding: 0;
    font: inherit;
    // A3: ver .exp-workspace__breadcrumb — mismo fix de contraste AA.
    color: var(--op-primary-dark);
    text-decoration: none;
    font-size: var(--op-font-xs);
    cursor: pointer;

    &:hover {
      text-decoration: underline;
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
      border-radius: 2px;
    }

    &--danger {
      color: var(--op-error);
      margin-left: var(--op-space-2);
    }
  }

  .exp-icon--success {
    color: var(--op-success);
  }

  // =========================================================================
  // Índice — verificación de integridad ("apariencia de certificado")
  // =========================================================================

  .exp-indice-verify-error {
    color: var(--op-error-fg);
    font-size: var(--op-font-sm);
    margin: 0 0 var(--op-space-4);

    i {
      margin-right: var(--op-space-1);
    }
  }

  .exp-indice-verify {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-3);
    padding: var(--op-space-4);
    border-radius: var(--op-radius);
    border: 1px solid var(--op-border);
    margin-bottom: var(--op-space-4);

    i {
      font-size: var(--op-font-lg);
      margin-top: 2px;
    }

    &--ok {
      // Menor (revisión UX): el fallback `var(--op-primary-light)` estaba
      // muerto — `--op-success-bg` SÍ está definido en _tokens.scss.
      background: var(--op-success-bg);
      border-color: var(--op-success);

      i {
        color: var(--op-success);
      }
    }

    &--fail {
      background: var(--op-error-bg);
      border-color: var(--op-error);

      i {
        color: var(--op-error);
      }
    }

    &__body {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
      font-size: var(--op-font-sm);
      color: var(--op-text);
    }

    &__list {
      margin: var(--op-space-1) 0 0;
      padding-left: var(--op-space-4);
      font-size: var(--op-font-xs);
      // A4 (revisión UX): esta lista dice si la huella coincide y si la firma
      // es válida — justo el detalle con valor legal — a tamaño xs.
      // `--op-text-secondary` sobre `--op-success-bg`/`--op-error-bg` (los
      // fondos reales de `.exp-indice-verify--ok`/`--fail`) mide 3.97:1/4.10:1,
      // por debajo de AA. `--op-text` da 12.56:1 sobre esos mismos fondos.
      color: var(--op-text);
    }

    &__ts {
      font-size: var(--op-font-xs);
      // I3 (revisión UX): igual que `&__list` arriba (comentario A4) —
      // `--op-text-secondary` sobre `--op-success-bg`/`--op-error-bg` mide
      // 3.97:1/4.10:1, bajo el 4.5:1 de AA (medido en tamaño xs). El
      // comentario A4 ya lo había corregido para `&__list` pero no bajó a
      // `&__ts`. `--op-text` da 12.56:1 sobre esos mismos fondos.
      color: var(--op-text);
      margin-top: var(--op-space-1);
    }
  }

  // =========================================================================
  // Anexos — accordion por radicado (carga perezosa)
  // =========================================================================

  .exp-anexos-accordion {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);

    &__item {
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-sm);
      overflow: hidden;
    }

    &__trigger {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      width: 100%;
      padding: var(--op-space-2) var(--op-space-3);
      background: var(--op-bg);
      border: none;
      cursor: pointer;
      font-size: var(--op-font-sm);
      color: var(--op-text);
      text-align: left;

      &:hover {
        background: var(--op-primary-light);

        // I4 (revisión UX): `--op-text-secondary` sobre `--op-primary-light`
        // (el mismo fondo que `--op-success-bg`) mide 3.97:1, bajo el 4.5:1
        // de AA, justo mientras el puntero está encima. Se sube a
        // `--op-text` (12.56:1) SOLO en hover — el resto del componente ya
        // usa `--op-text-secondary` sobre `--op-bg`, que sí cumple.
        .exp-anexos-accordion__date,
        i.fa-chevron-right,
        i.fa-chevron-down {
          color: var(--op-text);
        }
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: -2px;
      }

      i.fa-chevron-right,
      i.fa-chevron-down {
        color: var(--op-text-secondary);
        font-size: var(--op-font-xs);
        width: 12px;
      }
    }

    &__radicado {
      font-family: var(--op-font-family-mono);
      font-weight: var(--op-weight-bold);
    }

    &__date {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin-left: auto;
    }

    &__panel {
      padding: var(--op-space-3);
      border-top: 1px solid var(--op-border);
      background: var(--op-surface);
    }
  }

  .exp-anexos-error {
    font-size: var(--op-font-sm);
    color: var(--op-error-fg);
    margin: 0;

    i {
      margin-right: var(--op-space-1);
    }
  }

  .exp-anexos-empty {
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    margin: 0;
  }

  // =========================================================================
  // Timeline (eventos)
  // =========================================================================

  .exp-timeline {
    list-style: none;
    padding: 0;
    margin: 0;
    position: relative;

    &::before {
      content: "";
      position: absolute;
      left: 9px;
      top: 16px;
      bottom: 0;
      width: 2px;
      background: var(--op-border);
    }

    &__item {
      display: flex;
      gap: var(--op-space-3);
      padding-bottom: var(--op-space-4);
      position: relative;
    }

    &__marker {
      width: 20px;
      height: 20px;
      border-radius: 50%;
      background: var(--op-primary);
      border: 2px solid var(--op-surface);
      box-shadow: 0 0 0 2px var(--op-primary-light);
      flex-shrink: 0;
      margin-top: 2px;
      z-index: 1;
    }

    &__content {
      flex: 1;
      padding-bottom: var(--op-space-2);
      border-bottom: 1px solid var(--op-border);
    }

    &__header {
      display: flex;
      align-items: baseline;
      gap: var(--op-space-3);
      flex-wrap: wrap;
      margin-bottom: var(--op-space-1);
    }

    &__accion {
      font-size: var(--op-font-sm);
      color: var(--op-text);
    }

    &__ts {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin-left: auto;
    }

    &__comment {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin: var(--op-space-1) 0;
    }

    &__actor {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      display: flex;
      align-items: center;
      gap: var(--op-space-1);
    }

    &__details {
      margin-top: var(--op-space-2);

      summary {
        font-size: var(--op-font-xs);
        // A3: ver .exp-workspace__breadcrumb — mismo fix de contraste AA.
        color: var(--op-primary-dark);
        cursor: pointer;

        &:focus-visible {
          outline: 2px solid var(--op-primary);
          outline-offset: 2px;
          border-radius: 2px;
        }
      }
    }

    &__json {
      font-size: var(--op-font-xs);
      background: var(--op-bg);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-sm);
      padding: var(--op-space-2);
      overflow-x: auto;
      // `position: relative` NO es cosmético: sin bloque contenedor propio, un
      // `position: absolute` de dentro (el `sr-only` de las cabeceras) se resuelve
      // contra el DOCUMENTO y este `overflow` no lo alcanza — desplazaba la página
      // entera hacia una franja vacía. Ver el hallazgo de los 390 px.
      position: relative;
      margin-top: var(--op-space-1);
      white-space: pre-wrap;
      word-break: break-word;
    }
  }

  // =========================================================================
  // Card title helper (scoped :global para el slot del Card)
  // =========================================================================

  :global(.op-card-title) {
    font-size: var(--op-font-sm);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--op-space-2);
  }

  // A7 / I2: anclas de foco programático (tabindex="-1") — visibles solo
  // cuando realmente reciben el foco (nunca en el tabbing normal del
  // teclado). #exp-workspace-title y #indice-electronico-heading replican el
  // patrón de #radicados-vinculados-heading para cerrar/transferir/firmar
  // (I2, revisión UX): sin esto, al retirarse del DOM el botón que originó
  // la acción, `Modal.svelte` reenfoca un nodo desconectado y el foco cae al
  // <body>, dejando al usuario de teclado/lector de pantalla desorientado
  // justo tras un acto irreversible con valor legal.
  :global(#radicados-vinculados-heading:focus-visible),
  :global(#exp-workspace-title:focus-visible),
  :global(#indice-electronico-heading:focus-visible) {
    outline: 2px solid var(--op-primary);
    outline-offset: 4px;
    border-radius: var(--op-radius-sm);
  }
  .exp-modal__warning {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    margin: var(--op-space-3) 0 0;
    padding: var(--op-space-3) var(--op-space-4);
    border-radius: var(--op-radius);
    background: var(--op-warning-bg);
    color: var(--op-warning-fg);
    font-size: var(--op-font-sm);
    line-height: var(--op-line-height);

    i {
      flex-shrink: 0;
      margin-top: 2px;
    }

    a {
      color: inherit;
      text-decoration: underline;

      &:focus-visible {
        outline: 2px solid currentColor;
        outline-offset: 2px;
        border-radius: var(--op-radius-sm);
      }
    }
  }
</style>
