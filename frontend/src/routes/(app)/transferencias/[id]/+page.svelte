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
  /transferencias/[id] — Detalle de una transferencia documental, con su
  acta de entrega en apariencia de CERTIFICADO (Fase 4, cierre de desfase
  API↔UI, E12/E17/F4).

  Backend real verificado en `services/archive-service/app/routers/
  transferencias.py`: GET /transferencias/{id}, GET .../acta,
  GET .../acta/verificacion, POST .../acta/firmar-personal,
  POST .../acta/firmar (reintento del sello institucional).

  El acta tiene TRES roles de firma distintos — elaborador, remitente
  ('entrega'), receptor ('recibe') — y el rol se DERIVA en el backend de la
  identidad congelada (`creada_por`/`enviada_por`/`decidida_por` de la
  transferencia), NUNCA lo elige el cliente. Esta pantalla espeja esa misma
  derivación (comparando la identidad interna, `$userId`, contra esas tres columnas) para
  mostrar a CADA persona solo lo que a ELLA le corresponde firmar — nunca un
  botón "Firmar" genérico — pero el backend re-valida siempre y es la única
  fuente de verdad (si el espejo se equivoca, el backend responde 403).

  Estado de carga/error POR FACETA (transferencia / acta / verificación):
  un fallo de red en una NUNCA se presenta como "sin firmar" o "sin acta".

  RBAC: lectura con USUA_PERM_EXPEDIENTE; firmar-personal con
  USUA_PERM_EXPEDIENTE crud>=3 (misma faceta que "gestionar transferencias"
  en el listado). D-05: toda escritura va a `/transferencias/api/*`.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import { permissionLevels, userId } from "$lib/stores/auth.js";
  import { etiquetaUbicacion } from "$lib/utils/ubicacion.js";
  import { notifySuccess, notifyError } from "$lib/stores/notifications.js";

  import Card from "$lib/components/ui/Card.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** @type {import('./$types').PageData} */
  export let data;

  // A6 (revisión UX): "Reintentar sellado" (POST .../acta/firmar) y "Firmar
  // como <rol>" (POST .../acta/firmar-personal) exigen
  // `require_permission("USUA_PERM_EXPEDIENTE", min_crud=3)` en el backend
  // (transferencias.py líneas 186/203) — la docstring de este archivo ya lo
  // documentaba, pero el gate seguía usando solo presencia del permiso.
  $: canManageWrite = ($permissionLevels["USUA_PERM_EXPEDIENTE"] ?? 0) >= 3;
  // Identidad INTERNA (`auth_users.id`), no el `sub` de Keycloak: las tres
  // columnas de autoría de la transferencia guardan la primera. Comparando
  // contra `sub` esto daba siempre `[]`, así que el botón «Firmar como …» no
  // aparecía nunca — para nadie, ni siquiera para quien había hecho el acto.
  $: userSub = $userId ?? "";

  onMount(() => {
    setBreadcrumbs([
      { label: "Panel principal", href: "/dashboard" },
      { label: "Transferencias", href: "/transferencias" },
      { label: "Detalle" },
    ]);
  });

  // ---------------------------------------------------------------------------
  // Facetas (estado de carga/error INDEPENDIENTE cada una — nunca un global)
  // ---------------------------------------------------------------------------
  $: transferencia = data.transferencia?.data ?? null;

  // Identidad legible. El acta de entrega es un documento con valor probatorio:
  // mostraba tres UUID donde deben ir el código del expediente y los de las
  // ubicaciones, que son los que están impresos en la carátula y en las
  // signaturas. Si el catálogo no llegó, se cae al identificador — que es lo
  // que había antes, no una regresión.
  $: expedienteEtiqueta = data.expedienteCodigo
    ? data.expedienteNombre
      ? `${data.expedienteCodigo} — ${data.expedienteNombre}`
      : data.expedienteCodigo
    : (transferencia?.expediente_id ?? "—");

  /** @param {string|null|undefined} id */
  function ubicacionEtiqueta(id) {
    if (!id) return "—";
    const u = (data.ubicaciones ?? []).find((x) => x.id === id);
    return u ? etiquetaUbicacion(u) : id;
  }
  $: transferenciaError = data.transferencia?.error ?? null;

  $: acta = data.acta?.data ?? null;
  $: actaError = data.acta?.error ?? null;
  // 404 = "aún no hay acta" (no se recibió, o fue rechazada) — respuesta
  // LEGÍTIMA del ciclo de vida, no un fallo de red. Solo se trata como error
  // real (banner rojo) cuando el status NO es 404.
  $: actaFalloReal = actaError && actaError.status !== 404;

  $: verificacion = data.verificacion?.data ?? null;
  $: verificacionError = data.verificacion?.error ?? null;
  $: verificacionNoDisponible = verificacionError?.status === 404;
  $: verificacionFalloReal = verificacionError && verificacionError.status !== 404;

  // ---------------------------------------------------------------------------
  // Derivación del rol del usuario ACTUAL — espejo del backend
  // (`TransferenciaService.firmar_personal`), NUNCA una segunda fuente de
  // verdad: el backend siempre re-valida contra `enviada_por`/`decidida_por`/
  // `creada_por` en el momento de firmar.
  // ---------------------------------------------------------------------------
  /** @returns {string[]} */
  /**
   * Roles que esta persona desempeñó en ESTA transferencia.
   *
   * Recibe sus dos entradas por parámetro y no las lee del ámbito. La razón es
   * de robustez, no un fallo observado: `$: misRoles = derivedRoles()` deduce
   * sus dependencias LEYENDO el statement, donde solo aparece el nombre de la
   * función, así que lo que la función consultara por dentro no cuenta y el
   * valor se congela tras el primer cálculo. Aquí no llegó a notarse porque
   * `data` viene completo del `load` SSR —el defecto real que dejaba el botón
   * invisible era otro: se comparaba el `sub` de Keycloak contra la identidad
   * interna del tenant—, pero basta con que estos datos pasen a llegar más
   * tarde (un `fetch` en cliente, una recarga parcial) para que reaparezca.
   *
   * Con los argumentos a la vista, Svelte ve `transferencia` y `usuario`. Es el
   * mismo patrón que sí se manifestó en `/busqueda` y en `/bandeja`.
   *
   * @param {{enviada_por?: string, decidida_por?: string, creada_por?: string}|null} transf
   * @param {string} usuario — identidad INTERNA del tenant (`auth_users.id`)
   * @returns {string[]}
   */
  function derivedRoles(transf, usuario) {
    if (!transf || !usuario) return [];
    /** @type {string[]} */
    const roles = [];
    if (usuario === transf.enviada_por) roles.push("entrega");
    if (usuario === transf.decidida_por) roles.push("recibe");
    if (usuario === transf.creada_por) roles.push("elabora");
    return roles;
  }
  $: misRoles = derivedRoles(transferencia, userSub);

  const ROL_LABELS = {
    elabora: "Elaborador(a)",
    entrega: "Remitente (entrega)",
    recibe: "Receptor(a) (recibe)",
  };

  /** @param {string} rol */
  function firmaPersonalDe(rol) {
    if (!verificacion) return null;
    return verificacion.firmas_personales?.find((f) => f.rol === rol) ?? null;
  }

  // Los tres roles del FUID a mostrar SIEMPRE en el certificado, en orden.
  const ROLES_CERTIFICADO = ["elabora", "entrega", "recibe"];

  // ---------------------------------------------------------------------------
  // Refrescar solo las facetas de acta/verificación (tras firmar/reintentar) —
  // sin recargar toda la página (SSR), vía los proxies del cliente.
  // ---------------------------------------------------------------------------
  let actaLocal = null;
  let actaLocalError = null;
  let verificacionLocal = null;
  let verificacionLocalError = null;
  $: actaEfectiva = actaLocal ?? acta;
  $: actaEfectivaFalloReal = actaLocal ? !!actaLocalError : actaFalloReal;
  $: verificacionEfectiva = verificacionLocal ?? verificacion;
  $: verificacionEfectivaNoDisponible = verificacionLocal ? false : verificacionNoDisponible;
  $: verificacionEfectivaFalloReal = verificacionLocal
    ? !!verificacionLocalError
    : verificacionFalloReal;

  async function refrescarActaYVerificacion() {
    try {
      const [actaRes, verifRes] = await Promise.all([
        fetch(`/transferencias/api/${transferencia.id}/acta`),
        fetch(`/transferencias/api/${transferencia.id}/acta/verificacion`),
      ]);
      if (actaRes.ok) {
        actaLocal = await actaRes.json();
        actaLocalError = null;
      } else {
        actaLocalError = await actaRes.json().catch(() => ({ status: actaRes.status }));
        if (actaRes.status === 404) {
          actaLocal = null; // 404 legítimo, no error
        }
      }
      if (verifRes.ok) {
        verificacionLocal = await verifRes.json();
        verificacionLocalError = null;
      } else {
        verificacionLocalError = await verifRes.json().catch(() => ({ status: verifRes.status }));
        if (verifRes.status === 404) {
          verificacionLocal = null;
        }
      }
    } catch {
      // Fallo de red al refrescar — las llamadas específicas (verificarSello,
      // verificarFirmas, reintentar) ya notifican al usuario; este catch solo
      // evita que una excepción no controlada rompa el flujo.
    }
  }

  // ---------------------------------------------------------------------------
  // Verificar sello (recompute on-demand costoso, botón explícito)
  // ---------------------------------------------------------------------------
  let verificandoSello = false;

  async function verificarSello() {
    verificandoSello = true;
    try {
      const res = await fetch(`/transferencias/api/${transferencia.id}/acta?verify=true`);
      const result = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(mensajeError(result, "No se pudo verificar el sello del acta."));
        return;
      }
      actaLocal = result;
      notifySuccess(
        result?.firma?.valida === true
          ? "El sello institucional del acta es válido."
          : result?.firma?.valida === false
            ? "El sello institucional del acta NO es válido."
            : "No se pudo determinar la validez del sello (canal de verificación no disponible).",
      );
    } catch {
      notifyError("No se pudo conectar con el servidor para verificar el sello.");
    } finally {
      verificandoSello = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Verificar firmas personales (cripto + revocación, on-demand)
  // ---------------------------------------------------------------------------
  let verificandoFirmas = false;

  async function verificarFirmas() {
    verificandoFirmas = true;
    try {
      await refrescarActaYVerificacion();
      if (verificacionLocalError && verificacionLocalError.status !== 404) {
        notifyError(mensajeError(verificacionLocalError, "No se pudo verificar el acta."));
      } else {
        notifySuccess("Verificación de firmas actualizada.");
      }
    } finally {
      verificandoFirmas = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Reintento del sello institucional (H-H) — sin TOTP, min_crud=3
  // ---------------------------------------------------------------------------
  let reintentandoSello = false;

  async function reintentarSelloInstitucional() {
    reintentandoSello = true;
    try {
      const res = await fetch(`/transferencias/api/${transferencia.id}/acta/firmar`, {
        method: "POST",
      });
      const result = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(mensajeError(result, "No se pudo reintentar el sellado del acta."));
        return;
      }
      notifySuccess("Reintento de sellado del acta enviado.");
      await refrescarActaYVerificacion();
    } catch {
      notifyError("No se pudo conectar con el servidor para reintentar el sello.");
    } finally {
      reintentandoSello = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Modal: firma personal (TOTP) de UN rol
  // ---------------------------------------------------------------------------
  let showFirmarModal = false;
  /** @type {string|null} */
  let firmarRolObjetivo = null;
  let firmarTotp = "";
  let firmarLoading = false;

  /** @param {string} rol */
  function abrirFirmarModal(rol) {
    firmarRolObjetivo = rol;
    firmarTotp = "";
    showFirmarModal = true;
  }

  async function confirmarFirmarPersonal() {
    if (!firmarTotp.trim()) {
      notifyError("Ingrese el código de verificación (TOTP) para firmar.");
      return;
    }
    firmarLoading = true;
    try {
      const res = await fetch(`/transferencias/api/${transferencia.id}/acta/firmar-personal`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          totp_code: firmarTotp.trim(),
          // `rol` solo desempata el auto-traslado (misma persona en dos
          // roles) — se envía SIEMPRE que el objetivo esté claro, el
          // backend lo ignora si es redundante y lo rechaza si intentara
          // conceder un rol que la identidad no otorga.
          rol: firmarRolObjetivo,
        }),
      });
      const result = await res.json().catch(() => ({}));
      if (!res.ok) throw result;
      notifySuccess(
        `Firma personal (${ROL_LABELS[firmarRolObjetivo] ?? firmarRolObjetivo}) registrada correctamente.`,
      );
      showFirmarModal = false;
      await refrescarActaYVerificacion();
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo registrar la firma personal."));
    } finally {
      firmarLoading = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Helpers de formato
  // ---------------------------------------------------------------------------
  /** @param {string|null|undefined} iso */
  function fmtDatetime(iso) {
    if (!iso) return "—";
    return formatearFechaHora(iso);
  }

  const ESTADO_MAP = {
    preparada: { label: "Preparada", status: "warning" },
    enviada: { label: "Enviada", status: "info" },
    recibida: { label: "Recibida", status: "success" },
    rechazada: { label: "Rechazada", status: "danger" },
  };

  const TIPO_MAP = { primaria: "Primaria", secundaria: "Secundaria" };

  const COMPLETITUD_MAP = {
    sellada: { label: "Solo sello institucional", status: "info" },
    firma_personal_pendiente: { label: "Sin firmas personales", status: "warning" },
    firmada_receptor: { label: "Firmada por el receptor", status: "info" },
    firmada_remitente: { label: "Firmada por el remitente", status: "info" },
    firmada_completa: { label: "Firma personal completa", status: "success" },
  };
</script>

<svelte:head>
  <title>Transferencia — OrpycaMCP</title>
</svelte:head>

<div class="trd-workspace">
  <!-- =====================================================================
       Faceta 1 — Cabecera de la transferencia
       ===================================================================== -->
  {#if transferenciaError}
    <Card>
      <EmptyState
        icon="fas fa-exclamation-triangle"
        title="No se pudo cargar la transferencia"
        description={mensajeError(transferenciaError, "Intenta recargar la página.")}
      />
    </Card>
  {:else if !transferencia}
    <Loader label="Cargando transferencia…" />
  {:else}
    {@const estadoInfo = ESTADO_MAP[transferencia.estado] ?? {
      label: transferencia.estado,
      status: "neutral",
    }}
    <div class="trd-header">
      <div>
        <h1 class="trd-header__title">
          <i class="fas fa-exchange-alt" aria-hidden="true"></i>
          Transferencia {TIPO_MAP[transferencia.tipo] ?? transferencia.tipo}
        </h1>
        <p class="trd-header__subtitle">
          Expediente <code>{expedienteEtiqueta}</code>
        </p>
      </div>
      <StatusChip status={estadoInfo.status} label={estadoInfo.label} dot />
    </div>

    <Card>
      <h2 slot="header" class="op-card-title">Datos de la transferencia</h2>
      <dl class="trd-meta-list">
        <div class="trd-meta-list__item">
          <dt>Creada</dt>
          <dd>{fmtDatetime(transferencia.created_at)}</dd>
        </div>
        <div class="trd-meta-list__item">
          <dt>Enviada</dt>
          <dd>{fmtDatetime(transferencia.enviada_at)}</dd>
        </div>
        <div class="trd-meta-list__item">
          <dt>Recibida</dt>
          <dd>{fmtDatetime(transferencia.recibida_at)}</dd>
        </div>
        {#if transferencia.motivo_rechazo}
          <div class="trd-meta-list__item">
            <dt>Motivo de rechazo</dt>
            <dd>{transferencia.motivo_rechazo}</dd>
          </div>
        {/if}
        {#if transferencia.observacion}
          <div class="trd-meta-list__item trd-meta-list__item--wide">
            <dt>Observación</dt>
            <dd>{transferencia.observacion}</dd>
          </div>
        {/if}
      </dl>
    </Card>

    <!-- =====================================================================
         Faceta 2 — Acta de entrega, con apariencia de CERTIFICADO
         ===================================================================== -->
    <Card>
      <div slot="header" class="trd-acta-header">
        <h2 class="op-card-title">Acta de entrega (certificado FUID)</h2>
        {#if actaEfectiva}
          <div class="trd-acta-header__actions">
            <Button
              variant="secondary"
              size="sm"
              on:click={verificarSello}
              disabled={verificandoSello}
            >
              {#if verificandoSello}<i class="fas fa-spinner fa-spin" aria-hidden="true"></i>{/if}
              Verificar sello
            </Button>
            <Button
              variant="secondary"
              size="sm"
              on:click={verificarFirmas}
              disabled={verificandoFirmas}
            >
              {#if verificandoFirmas}<i class="fas fa-spinner fa-spin" aria-hidden="true"></i>{/if}
              Verificar firmas
            </Button>
          </div>
        {/if}
      </div>

      {#if actaEfectivaFalloReal}
        <EmptyState
          icon="fas fa-exclamation-triangle"
          title="No se pudo consultar el acta"
          description={actaLocal
            ? mensajeError(actaLocalError, "Intenta de nuevo.")
            : mensajeError(actaError, "Intenta de nuevo.")}
        />
      {:else if !actaEfectiva}
        <!-- 404 neutro (RF-SEG-08): puede ser que la transferencia aún no se
             recibió, O que el llamante no tiene visibilidad del expediente.
             NUNCA se afirma "sin firmar": simplemente no hay acta que mostrar. -->
        <EmptyState
          icon="fas fa-file-signature"
          title="Sin acta de entrega disponible"
          description="Esta transferencia aún no tiene acta congelada (se genera al recibirla), o usted no tiene visibilidad sobre este expediente."
        />
      {:else}
        <div class="trd-certificate">
          <header class="trd-certificate__seal">
            <i class="fas fa-stamp" aria-hidden="true"></i>
            <div>
              <p class="trd-certificate__seal-title">
                Acta de entrega — Formato Único de Inventario Documental
              </p>
              <p class="trd-certificate__seal-sub">
                Congelada el {fmtDatetime(actaEfectiva.congelada_at)} · versión de índice
                {actaEfectiva.indice_version ?? "—"}
              </p>
            </div>
          </header>

          <div class="trd-certificate__body">
            <div class="trd-certificate__field">
              <span class="trd-certificate__label">Qué se transfiere</span>
              <span>Expediente <code>{expedienteEtiqueta}</code></span>
            </div>
            <div class="trd-certificate__field">
              <!-- Decía «Entre dependencias» y pinta las UBICACIONES físicas
                   (depósito, estante, entrepaño), que no son dependencias: la
                   unidad administrativa y la oficina productora son campos
                   propios de la cabecera del FUID y hoy viajan vacíos. Se
                   nombra lo que de verdad se muestra. -->
              <span class="trd-certificate__label">Entre ubicaciones</span>
              <span>
                {ubicacionEtiqueta(transferencia.ubicacion_origen_id)} → {ubicacionEtiqueta(
                  transferencia.ubicacion_destino_id,
                ) ?? "—"}
              </span>
            </div>
            <div class="trd-certificate__field">
              <span class="trd-certificate__label">Cuándo</span>
              <span>{fmtDatetime(actaEfectiva.recibido_at)}</span>
            </div>
            <div class="trd-certificate__field">
              <span class="trd-certificate__label">Huella SHA-256 del acta</span>
              <span class="trd-certificate__hash">{actaEfectiva.acta_fuid_sha256}</span>
            </div>
          </div>

          <!-- Sello institucional -->
          <div class="trd-certificate__seal-status">
            <span class="trd-certificate__label">Sello institucional (XAdES)</span>
            {#if actaEfectiva.firma?.presente}
              <StatusChip
                status={actaEfectiva.firma.valida === true
                  ? "success"
                  : actaEfectiva.firma.valida === false
                    ? "error"
                    : "info"}
                label={actaEfectiva.firma.valida === true
                  ? `Sellada y verificada (${actaEfectiva.firma.xades_level ?? "XAdES"})`
                  : actaEfectiva.firma.valida === false
                    ? "Sellada — la verificación falló"
                    : `Sellada (${actaEfectiva.firma.xades_level ?? "XAdES"}) — sin verificar todavía`}
                dot
              />
            {:else}
              <StatusChip status="warning" label="Pendiente de sellado institucional" dot />
              {#if canManageWrite}
                <Button
                  variant="secondary"
                  size="sm"
                  on:click={reintentarSelloInstitucional}
                  disabled={reintentandoSello}
                >
                  {#if reintentandoSello}<i class="fas fa-spinner fa-spin" aria-hidden="true"
                    ></i>{/if}
                  Reintentar sellado
                </Button>
              {/if}
            {/if}
          </div>

          <!-- Roles de firma del FUID -->
          <div class="trd-certificate__roles">
            <span class="trd-certificate__label">Responsables y estado de firma</span>
            {#if verificacionEfectivaFalloReal}
              <EmptyState
                icon="fas fa-exclamation-circle"
                title="No se pudo consultar el estado de las firmas"
                description={verificacionLocal
                  ? mensajeError(verificacionLocalError, "Intenta de nuevo.")
                  : mensajeError(verificacionError, "Intenta de nuevo.")}
              />
            {:else}
              <ul class="trd-roles-list">
                {#each ROLES_CERTIFICADO as rol (rol)}
                  {@const firma = firmaPersonalDe(rol)}
                  {@const esMiRol = misRoles.includes(rol)}
                  <!-- La variable se llamaba `nombre` y llevaba el
                       identificador interno: los tres responsables del acta
                       —elaborador, remitente y receptor— se pintaban como
                       `50b64803-568d-49f7-9299-cb140279d947`. Un acta de
                       entrega es el documento que dice QUIÉN entregó y quién
                       recibió; contestarlo con un identificador es no
                       contestarlo. El acta congela el identificador a
                       propósito (es probatoria, no se toca) y el nombre se
                       resuelve al leer, en el backend. El identificador sigue
                       a mano en el `title`: es la clave con la que se cruza
                       el acta con el registro de firmas. -->
                  {@const identificador =
                    rol === "elabora"
                      ? actaEfectiva.elaborado_por
                      : rol === "entrega"
                        ? actaEfectiva.entregado_por
                        : actaEfectiva.recibido_por}
                  {@const nombre =
                    rol === "elabora"
                      ? actaEfectiva.elaborado_por_nombre
                      : rol === "entrega"
                        ? actaEfectiva.entregado_por_nombre
                        : actaEfectiva.recibido_por_nombre}
                  <li class="trd-roles-list__item" class:trd-roles-list__item--mine={esMiRol}>
                    <div class="trd-roles-list__who">
                      <strong>{ROL_LABELS[rol]}</strong>
                      <span class="trd-roles-list__identity" title={identificador ?? undefined}
                        >{nombre ?? identificador ?? "—"}</span
                      >
                      {#if esMiRol}
                        <span class="trd-roles-list__you-badge">Usted</span>
                      {/if}
                    </div>
                    <div class="trd-roles-list__status">
                      {#if firma}
                        <StatusChip
                          status={firma.valida === true
                            ? "success"
                            : firma.valida === false
                              ? "error"
                              : "info"}
                          label={firma.valida === true
                            ? "Firmado personalmente"
                            : firma.valida === false
                              ? "Firmado — no válido"
                              : "Firmado — sin verificar"}
                          dot
                        />
                      {:else}
                        <StatusChip status="neutral" label="Sin firma personal" dot />
                      {/if}
                      {#if !firma && esMiRol && canManageWrite && transferencia.estado === "recibida"}
                        <Button variant="primary" size="sm" on:click={() => abrirFirmarModal(rol)}>
                          <i class="fas fa-signature" aria-hidden="true"></i>
                          Firmar como {ROL_LABELS[rol]}
                        </Button>
                      {/if}
                    </div>
                  </li>
                {/each}
              </ul>
            {/if}

            {#if verificacionEfectiva}
              {@const completitudInfo = COMPLETITUD_MAP[verificacionEfectiva.completitud] ?? {
                label: verificacionEfectiva.completitud,
                status: "neutral",
              }}
              <div class="trd-certificate__completitud">
                <StatusChip status={completitudInfo.status} label={completitudInfo.label} dot />
                {#if verificacionEfectiva.auto_traslado}
                  <span class="trd-certificate__note">
                    <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
                    Auto-traslado: la misma persona envió y recibió — sin control dual (separación de
                    funciones).
                  </span>
                {/if}
              </div>
            {:else if verificacionEfectivaNoDisponible}
              <p class="trd-certificate__note">
                Sin información de verificación de firmas todavía.
              </p>
            {/if}
          </div>
        </div>
      {/if}
    </Card>
  {/if}
</div>

<!-- =========================================================================
     Modal: firmar personalmente (TOTP)
     ========================================================================= -->
<Modal
  open={showFirmarModal}
  title={`Firmar acta — ${firmarRolObjetivo ? ROL_LABELS[firmarRolObjetivo] : ""}`}
  size="sm"
  on:close={() => (showFirmarModal = false)}
>
  <p>
    Va a firmar personalmente el acta de entrega con su credencial PKI, en su rol de
    <strong>{firmarRolObjetivo ? ROL_LABELS[firmarRolObjetivo] : ""}</strong>. Esta acción consume
    su código de verificación (TOTP) y queda registrada de forma permanente.
  </p>
  <div class="trd-form">
    <FormField
      id="trd-firmar-totp"
      label="Código de verificación (TOTP) *"
      type="text"
      required
      placeholder="Código de su aplicación de autenticación"
      bind:value={firmarTotp}
    />
  </div>
  <div slot="footer" class="trd-modal-footer">
    <Button variant="secondary" on:click={() => (showFirmarModal = false)}>Cancelar</Button>
    <Button variant="primary" on:click={confirmarFirmarPersonal} disabled={firmarLoading}>
      {#if firmarLoading}
        <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
      {/if}
      Firmar acta
    </Button>
  </div>
</Modal>

<style lang="scss">
  .trd-workspace {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
    padding: var(--op-space-5);
  }

  .trd-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: var(--op-space-3);

    &__title {
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      display: flex;
      align-items: center;
      gap: var(--op-space-2);

      i {
        color: var(--op-primary);
      }
    }

    &__subtitle {
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
      margin-top: var(--op-space-1);

      code {
        font-family: var(--op-font-family-mono);
      }
    }
  }

  .trd-meta-list {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: var(--op-space-3);

    &__item {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);

      &--wide {
        grid-column: 1 / -1;
      }

      dt {
        font-size: var(--op-font-xs);
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--op-text-secondary);
      }

      dd {
        color: var(--op-text);
      }
    }
  }

  .trd-acta-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: var(--op-space-2);
    width: 100%;

    &__actions {
      display: flex;
      gap: var(--op-space-2);
      flex-wrap: wrap;
    }
  }

  // Apariencia de certificado (Sistema de Diseño Orpyca: 60/30/10, sin abusar del verde)
  .trd-certificate {
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    overflow: hidden;

    &__seal {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
      background: var(--op-primary-light);
      padding: var(--op-space-4);

      i {
        font-size: 1.75rem;
        color: var(--op-primary-dark);
      }
    }

    &__seal-title {
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
    }

    &__seal-sub {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
    }

    &__body {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: var(--op-space-3);
      padding: var(--op-space-4);
      border-bottom: 1px solid var(--op-border);
    }

    &__field {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
      font-size: var(--op-font-sm);
      color: var(--op-text);
    }

    &__label {
      font-size: var(--op-font-xs);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--op-text-secondary);
      font-weight: var(--op-weight-bold);
    }

    &__hash {
      font-family: var(--op-font-family-mono);
      font-size: var(--op-font-xs);
      word-break: break-all;
    }

    &__seal-status {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
      flex-wrap: wrap;
      padding: var(--op-space-4);
      border-bottom: 1px solid var(--op-border);
    }

    &__roles {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-3);
      padding: var(--op-space-4);
    }

    &__completitud {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
      flex-wrap: wrap;
    }

    &__note {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);

      i {
        color: var(--op-warning);
        margin-right: var(--op-space-1);
      }
    }
  }

  .trd-roles-list {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);

    &__item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: var(--op-space-2);
      padding: var(--op-space-3);
      border-radius: var(--op-radius-sm);
      background: var(--op-bg);

      &--mine {
        border: 1px solid var(--op-primary);
      }
    }

    &__who {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      flex-wrap: wrap;
    }

    &__identity {
      font-size: var(--op-font-xs);
      font-family: var(--op-font-family-mono);
      color: var(--op-text-secondary);
    }

    &__you-badge {
      background: var(--op-accent);
      color: var(--op-text);
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-bold);
      padding: 2px var(--op-space-2);
      border-radius: var(--op-radius-full);
    }

    &__status {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      flex-wrap: wrap;
    }
  }

  .trd-form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
    padding: var(--op-space-2) 0;
  }

  .trd-modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: var(--op-space-2);
  }
</style>
