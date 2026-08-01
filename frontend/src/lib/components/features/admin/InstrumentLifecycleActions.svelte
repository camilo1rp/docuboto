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
  InstrumentLifecycleActions.svelte — Circuito de convalidación de un
  instrumento archivístico (TRD o TVD), componente COMPARTIDO parametrizado
  por instrumento (ADR-026 §"Qué necesita el frontend": "si esa UI todavía
  no existe en /admin/trd, construirla una vez como componente compartido
  parametrizado por instrumento, no dos veces").

  Máquina de estados (idéntica para TRD y TVD, Ac. AGN 001/2024 que compila
  el 004/2019 — ADR-025/ADR-026):

    borrador --aprobar--> aprobada --convalidar--> convalidada --derogar--> derogada
                  ^_______devolver_______|              |
                                            registrar-rusd (fill-once)
    migrada --convalidar (ratificación)--> convalidada   (solo TRD legacy; TVD nunca la usa)

  La UI NUNCA ofrece una transición que el backend rechazaría (regla dura del
  proyecto): cada botón se gatea por `item.estado` Y por `canWrite`. Los
  actos irreversibles (convalidar, derogar) muestran la advertencia DENTRO
  del Modal de confirmación, ANTES del botón de confirmar — no como tooltip
  ni después de un 409.

  Props:
    item            objeto de la fila (code, version, estado, rusd_radicado,
                     fecha_publicacion, ...) — TrdSerieResponse/TvdSerieResponse.
    basePath        string  — "/admin/trd/api" | "/admin/tvd/api" (proxies SSR,
                     D-05; este componente NUNCA llama al gateway directo).
    canWrite        boolean — permiso real (USUA_PERM_TRD en ambos instrumentos,
                     ADR-026: "se reutiliza USUA_PERM_TRD").
    instrumentLabel string  — "TRD" | "TVD", solo para los textos de la UI.

  Eventos:
    changed  { item }  — la fila cambió de estado; el padre reemplaza el item
                          en su lista (no vuelve a pedir todo el listado).
-->
<script>
  import { createEventDispatcher } from "svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import { notifySuccess, notifyError } from "$lib/stores/notifications.js";

  export let item;
  export let basePath;
  export let canWrite = false;
  export let instrumentLabel = "TRD";

  const dispatch = createEventDispatcher();

  $: estado = item?.estado ?? "borrador";
  $: canAprobar = canWrite && estado === "borrador";
  $: canDevolver = canWrite && estado === "aprobada";
  $: canConvalidar = canWrite && (estado === "aprobada" || estado === "migrada");
  $: canRegistrarRusd = canWrite && estado === "convalidada" && (!item?.rusd_radicado || !item?.fecha_publicacion);
  $: canDerogar = canWrite && (estado === "convalidada" || estado === "migrada");
  // "Nueva versión" solo tiene sentido cuando NO hay ya un borrador/aprobada
  // en trámite para este código (ux_trd_series_en_tramite, ADR-025 037): si
  // el vigente está convalidado/derogado/migrado, abrir una versión nueva es
  // seguro; si está borrador/aprobada, ESA es la versión en trámite.
  $: canNuevaVersion = canWrite && ["convalidada", "migrada", "derogada"].includes(estado);

  let actionLoading = false;
  let openModal = null; // 'aprobar' | 'devolver' | 'convalidar' | 'rusd' | 'derogar' | 'nueva-version' | null

  let fActaComite = "";
  let fFechaAprobacion = "";
  let fMotivo = "";
  let fActoAdministrativo = "";
  let fFechaConvalidacion = "";
  let fInstanciaConvalidante = "";
  let fRusdRadicado = "";
  let fFechaPublicacion = "";

  function resetForms() {
    fActaComite = "";
    fFechaAprobacion = "";
    fMotivo = "";
    fActoAdministrativo = "";
    fFechaConvalidacion = "";
    fInstanciaConvalidante = "";
    fRusdRadicado = "";
    fFechaPublicacion = "";
  }

  function open(modal) {
    resetForms();
    openModal = modal;
  }
  function close() {
    openModal = null;
  }

  function pathFor(action) {
    const code = encodeURIComponent(item.code);
    const version = item.version;
    return `${basePath}/${code}/versiones/${version}/${action}`;
  }

  async function post(action, body) {
    actionLoading = true;
    try {
      const res = await fetch(pathFor(action), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body ?? {}),
      });
      const result = await res.json().catch(() => ({}));
      if (!res.ok) throw result;
      return result;
    } finally {
      actionLoading = false;
    }
  }

  function friendlyError(err, fallback) {
    if (err?.error === "forbidden") {
      return "No tiene el nivel de permiso necesario para esta acción.";
    }
    return err?.detail ?? fallback;
  }

  async function handleAprobar() {
    if (!fActaComite.trim() || !fFechaAprobacion) return;
    try {
      const updated = await post("aprobar", { acta_comite: fActaComite.trim(), fecha_aprobacion_comite: fFechaAprobacion });
      notifySuccess(`Versión ${item.version} de "${item.code}" aprobada por el Comité.`);
      close();
      dispatch("changed", { item: updated });
    } catch (err) {
      notifyError(friendlyError(err, "No se pudo aprobar la versión."));
    }
  }

  async function handleDevolver() {
    if (!fMotivo.trim()) return;
    try {
      const updated = await post("devolver", { motivo: fMotivo.trim() });
      notifySuccess(`Versión ${item.version} de "${item.code}" devuelta a borrador.`);
      close();
      dispatch("changed", { item: updated });
    } catch (err) {
      notifyError(friendlyError(err, "No se pudo devolver la versión."));
    }
  }

  async function handleConvalidar() {
    if (!fActoAdministrativo.trim() || !fFechaConvalidacion || !fInstanciaConvalidante.trim()) return;
    try {
      /** @type {Record<string, unknown>} */
      const body = {
        acto_administrativo: fActoAdministrativo.trim(),
        fecha_convalidacion: fFechaConvalidacion,
        instancia_convalidante: fInstanciaConvalidante.trim(),
      };
      if (fRusdRadicado.trim()) body.rusd_radicado = fRusdRadicado.trim();
      if (fFechaPublicacion) body.fecha_publicacion = fFechaPublicacion;
      const updated = await post("convalidar", body);
      notifySuccess(`Versión ${item.version} de "${item.code}" convalidada. Queda vigente.`);
      close();
      dispatch("changed", { item: updated });
    } catch (err) {
      notifyError(friendlyError(err, "No se pudo convalidar la versión."));
    }
  }

  async function handleRegistrarRusd() {
    if (!fRusdRadicado.trim() && !fFechaPublicacion) return;
    try {
      /** @type {Record<string, unknown>} */
      const body = {};
      if (fRusdRadicado.trim()) body.rusd_radicado = fRusdRadicado.trim();
      if (fFechaPublicacion) body.fecha_publicacion = fFechaPublicacion;
      const updated = await post("registrar-rusd", body);
      notifySuccess(`RUSD registrado para "${item.code}" v${item.version}.`);
      close();
      dispatch("changed", { item: updated });
    } catch (err) {
      notifyError(friendlyError(err, "No se pudo registrar el RUSD."));
    }
  }

  async function handleDerogar() {
    try {
      const updated = await post("derogar", {});
      notifySuccess(`Versión ${item.version} de "${item.code}" derogada.`);
      close();
      dispatch("changed", { item: updated });
    } catch (err) {
      notifyError(friendlyError(err, "No se pudo derogar la versión."));
    }
  }

  async function handleNuevaVersion() {
    actionLoading = true;
    try {
      const res = await fetch(`${basePath}/${encodeURIComponent(item.code)}/versiones`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const result = await res.json().catch(() => ({}));
      if (!res.ok) throw result;
      notifySuccess(`Nueva versión en borrador abierta para "${item.code}".`);
      close();
      dispatch("changed", { item: result, isNewVersion: true });
    } catch (err) {
      notifyError(friendlyError(err, "No se pudo abrir una versión nueva."));
    } finally {
      actionLoading = false;
    }
  }
</script>

{#if canAprobar}
  <Button variant="ghost" size="sm" icon="fas fa-stamp" on:click={() => open("aprobar")}>Aprobar</Button>
{/if}
{#if canDevolver}
  <Button variant="ghost" size="sm" icon="fas fa-rotate-left" on:click={() => open("devolver")}>Devolver</Button>
{/if}
{#if canConvalidar}
  <Button variant="ghost" size="sm" icon="fas fa-gavel" on:click={() => open("convalidar")}>Convalidar</Button>
{/if}
{#if canRegistrarRusd}
  <Button variant="ghost" size="sm" icon="fas fa-book" on:click={() => open("rusd")}>Registrar RUSD</Button>
{/if}
{#if canDerogar}
  <Button variant="ghost" size="sm" icon="fas fa-ban" on:click={() => open("derogar")}>Derogar</Button>
{/if}
{#if canNuevaVersion}
  <Button variant="ghost" size="sm" icon="fas fa-code-branch" on:click={() => open("nueva-version")}>Nueva versión</Button>
{/if}

<!-- Aprobar: reversible (devolver deshace), pero congela el contenido hasta entonces. -->
<Modal open={openModal === "aprobar"} title={`Aprobar versión ${item?.version} — ${instrumentLabel} "${item?.code}"`} size="sm" on:close={close}>
  <p class="ila-warning">
    <i class="fas fa-circle-info" aria-hidden="true"></i>
    Registra la aprobación del Comité Institucional de Gestión y Desempeño (primer acto del
    circuito). Mientras esté <strong>aprobada</strong>, el contenido queda bloqueado para edición
    directa; solo <strong>devolver</strong> o <strong>convalidar</strong> lo desbloquean.
  </p>
  <FormField id="ila-acta-comite" label="Acta del Comité" required bind:value={fActaComite} />
  <FormField id="ila-fecha-aprobacion" label="Fecha de aprobación" type="date" required bind:value={fFechaAprobacion} />
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={close} disabled={actionLoading}>Cancelar</Button>
    <Button
      variant="primary"
      icon="fas fa-stamp"
      loading={actionLoading}
      disabled={actionLoading || !fActaComite.trim() || !fFechaAprobacion}
      on:click={handleAprobar}>Confirmar aprobación</Button
    >
  </svelte:fragment>
</Modal>

<!-- Devolver: aprobada -> borrador, paso ordinario del procedimiento. -->
<Modal open={openModal === "devolver"} title={`Devolver versión ${item?.version} — ${instrumentLabel} "${item?.code}"`} size="sm" on:close={close}>
  <p class="ila-warning">
    <i class="fas fa-circle-info" aria-hidden="true"></i>
    Vuelve la versión a <strong>borrador</strong> para ajustes. La aprobación del Comité queda sin
    efecto: hará falta una aprobación nueva antes de poder convalidar.
  </p>
  <FormField id="ila-motivo" label="Motivo de la devolución" type="textarea" required bind:value={fMotivo} />
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={close} disabled={actionLoading}>Cancelar</Button>
    <Button
      variant="secondary"
      icon="fas fa-rotate-left"
      loading={actionLoading}
      disabled={actionLoading || !fMotivo.trim()}
      on:click={handleDevolver}>Confirmar devolución</Button
    >
  </svelte:fragment>
</Modal>

<!-- Convalidar: IRREVERSIBLE — acto administrativo, queda en auditoría inmutable. -->
<Modal open={openModal === "convalidar"} title={`Convalidar versión ${item?.version} — ${instrumentLabel} "${item?.code}"`} size="sm" on:close={close}>
  <p class="ila-warning ila-warning--danger">
    <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
    Esta acción es <strong>irreversible</strong>: registra el acto administrativo de convalidación,
    queda escrito en la auditoría inmutable y deja esta versión como <strong>vigente</strong>
    (derogando la anterior, si existía). No hay vuelta a "aprobada".
  </p>
  <FormField id="ila-acto-administrativo" label="Acto administrativo" required bind:value={fActoAdministrativo} />
  <FormField id="ila-fecha-convalidacion" label="Fecha de convalidación" type="date" required bind:value={fFechaConvalidacion} />
  <FormField
    id="ila-instancia-convalidante"
    label="Instancia convalidante"
    required
    bind:value={fInstanciaConvalidante}
    hint="Consejo Departamental/Distrital de Archivos (Ac. AGN 001/2024)."
  />
  <FormField id="ila-rusd-radicado" label="Radicado RUSD (opcional, puede llegar después)" bind:value={fRusdRadicado} />
  <FormField id="ila-fecha-publicacion" label="Fecha de publicación (opcional)" type="date" bind:value={fFechaPublicacion} />
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={close} disabled={actionLoading}>Cancelar</Button>
    <Button
      variant="primary"
      icon="fas fa-gavel"
      loading={actionLoading}
      disabled={actionLoading || !fActoAdministrativo.trim() || !fFechaConvalidacion || !fInstanciaConvalidante.trim()}
      on:click={handleConvalidar}>Confirmar convalidación</Button
    >
  </svelte:fragment>
</Modal>

<!-- Registrar RUSD: cada campo solo se puede completar UNA vez. -->
<Modal open={openModal === "rusd"} title={`Registrar RUSD — ${instrumentLabel} "${item?.code}" v${item?.version}`} size="sm" on:close={close}>
  <p class="ila-warning">
    <i class="fas fa-circle-info" aria-hidden="true"></i>
    El Registro Único de Series Documentales y la fecha de publicación solo pueden completarse
    <strong>una vez</strong> cada uno: una vez registrados no podrán corregirse desde aquí.
  </p>
  <FormField id="ila-rusd" label="Radicado RUSD" bind:value={fRusdRadicado} disabled={!!item?.rusd_radicado} />
  <FormField id="ila-fecha-pub" label="Fecha de publicación" type="date" bind:value={fFechaPublicacion} disabled={!!item?.fecha_publicacion} />
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={close} disabled={actionLoading}>Cancelar</Button>
    <Button
      variant="primary"
      icon="fas fa-book"
      loading={actionLoading}
      disabled={actionLoading || (!fRusdRadicado.trim() && !fFechaPublicacion)}
      on:click={handleRegistrarRusd}>Registrar</Button
    >
  </svelte:fragment>
</Modal>

<!-- Derogar: IRREVERSIBLE — baja del instrumento, sin transición de retorno. -->
<Modal open={openModal === "derogar"} title={`Derogar versión ${item?.version} — ${instrumentLabel} "${item?.code}"`} size="sm" on:close={close}>
  <p class="ila-warning ila-warning--danger">
    <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
    Esta acción es <strong>irreversible</strong>: da de baja el instrumento. Los expedientes ya
    cerrados bajo esta versión conservan su cálculo de retención congelado (no hay efecto
    retroactivo), pero ningún expediente nuevo podrá clasificarse aquí.
  </p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={close} disabled={actionLoading}>Cancelar</Button>
    <Button variant="danger" icon="fas fa-ban" loading={actionLoading} on:click={handleDerogar}>Confirmar derogación</Button>
  </svelte:fragment>
</Modal>

<!-- Nueva versión: abre un borrador nuevo (version+1) clonado de la vigente. -->
<Modal open={openModal === "nueva-version"} title={`Nueva versión de "${item?.code}"`} size="sm" on:close={close}>
  <p class="ila-warning">
    <i class="fas fa-circle-info" aria-hidden="true"></i>
    Abre una versión <strong>{(item?.version ?? 0) + 1}</strong> en borrador, con el mismo contenido
    de la versión actual. Edítela en su ficha antes de aprobarla; la versión vigente sigue rigiendo
    hasta que esta se convalide.
  </p>
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={close} disabled={actionLoading}>Cancelar</Button>
    <Button variant="primary" icon="fas fa-code-branch" loading={actionLoading} on:click={handleNuevaVersion}
      >Crear borrador</Button
    >
  </svelte:fragment>
</Modal>

<style lang="scss">
  .ila-warning {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    color: var(--op-text);
    font-size: var(--op-font-sm);
    line-height: 1.5;
    margin-bottom: var(--op-space-4);

    i {
      color: var(--op-info);
      flex-shrink: 0;
      margin-top: 2px;
    }

    &--danger i {
      color: var(--op-warning);
    }
  }
</style>
